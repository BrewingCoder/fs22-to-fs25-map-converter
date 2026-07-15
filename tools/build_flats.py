"""
build_flats.py - PHASE 2c: regenerate WW's FULL road network into our FS25 map, WITH collision.
WW's scene has clean named groups: `roads` (8784 shapes: surfaces, onramps, highway, curves), `Bridges` (565),
`tunnels` (7). We take those three subtrees WHOLESALE (hierarchy preserved -> correct placement; everything under
them is road-related so no pruning) = 9356 shapes across 47 unique meshes + 27 materials, all present in the decode
cache. Meshes are FS22 ver=7 (CONFIRMED to render in FS25); we raw-copy the mesh bytes by INTERNAL shapeId (the id
the i3d references) into our wildwest.i3d.shapes. Materials + WW-local road textures copied in. collisionMask on
every shape so nothing is fallen-through. SAFE: terrain/fields don't use .shapes. Run after build_terrain; then
build_road_grade carves the corridors flush. flats_stage1.py caches the 2 min .shapes decode.
"""
import os, sys, json, pickle, struct, shutil, copy
import numpy as np
import xml.etree.ElementTree as ET
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import shapes_codec as sc
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import classify as cls                                            # content-based tagger (roads by material/texture, map-agnostic)

SCENE_TAGS = ("TransformGroup", "Shape", "Light", "AudioSource", "Camera")


def prune_to_shapes(scene, keep):
    """Build a MINIMAL copy of the scene containing only the ancestor chains down to each Shape in `keep` - preserves
    world placement (every ancestor TransformGroup's transform is kept) while excluding non-road siblings. Used when a
    map has no clean road group (West End: road surfaces scattered under Railway1_2/Lights). Returns the top-level
    copied nodes (direct Scene children that lead to a kept shape)."""
    parent = {c: p for p in scene.iter() for c in p}
    copies = {}
    tops = []

    def copy_node(n):
        if id(n) in copies:
            return copies[id(n)]
        c = ET.Element(n.tag, dict(n.attrib))                    # attribs only (the transform); children added selectively
        copies[id(n)] = c
        p = parent.get(n)
        if p is None or p is scene:
            tops.append(c)
        else:
            copy_node(p).append(c)
        return c

    for s in keep:
        copy_node(s)                                             # materializes the shape copy + its full ancestor chain
    return tops

WW = os.environ.get("FS_CONVERT_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import convert_env
CONV = json.load(open(os.path.join(WW, "tools", os.environ.get("MAP_CONVERT", "wildwest.convert.json")), encoding="utf-8"))
FS22 = convert_env.source_dir(CONV)
FS22_I3D = os.path.join(FS22, CONV["source"]["map_i3d"])
FS22_MAPS = os.path.dirname(FS22_I3D)                             # FS22 map-data dir (map-agnostic; mapUS maps nest under maps/mapUS)
OUT = os.path.join(WW, "out", CONV["identity"]["mod"])
OUT_I3D = os.path.join(OUT, "maps", CONV["identity"]["i3d"])
SHAPES_NAME = os.path.splitext(CONV["identity"]["i3d"])[0] + ".i3d.shapes"   # map-agnostic: <mapname>.i3d.shapes (GIANTS convention)
OUT_SHAPES = os.path.join(OUT, "maps", SHAPES_NAME)
CACHE = os.environ.get("FS_CONVERT_CACHE", os.path.join(os.path.expanduser("~"), ".fs_convert_cache",
        f"flats_ents_{CONV['identity']['mod']}.pkl"))   # PER-MAP (was shared -> WW/West End clobbered each other's mesh cache)
# ROAD collision (GE "ROAD" preset from Smoky): FS25 uses collisionFilterGroup/Mask as HEX strings, NOT the FS22
# single collisionMask - that's why 65535 fell through. Static + Collision, density 1.
ROAD_COLLISION = {"static": "true", "collision": "true", "collisionFilterGroup": "0x601c",
                  "collisionFilterMask": "0xfffffbff", "density": "1"}
# MAP-SPECIFIC values come from the convert.json (config-driven mandate) - defaults = WW's:
_SG = CONV.get("scene_groups", {})
TOP = tuple(_SG.get("top", ["WildWest", "WildWest2"]))                       # top-level scene groups to search
GROUPS = tuple(_SG.get("roads", ["roads"]) + _SG.get("bridges", ["Bridges"]) + _SG.get("tunnels", ["tunnels"]))
MAT_REMAP = {k: v for k, v in CONV.get("material_remaps", {}).items() if not k.startswith("_")}   # unshipped-texture substitutes
DECAL_MATS = set(CONV.get("flats", {}).get("decal_materials", ["Material.003"]))   # WW material NAMES to force-treat as decals
FS22_DATA = os.environ.get("FS22_DATA", r"C:\Program Files (x86)\Steam\steamapps\common\Farming Simulator 22\data")
FS25_DATA = os.environ.get("FS25_DATA", r"C:\Program Files (x86)\Steam\steamapps\common\Farming Simulator 25\data")


def resolve_data(fn):
    """A $data/ texture ref: KEEP it if FS25 base has it; else copy the loose FS22-install file into maps/fs22/
    and return that local ref; None if in neither. WW's road/bridge textures live at FS22 $data paths FS25 DROPPED
    (e.g. maps/mapAlpine/ which FS25 has no folder for; coveredBridge01 gone from mapUS). Mirrors build_buildings."""
    if not fn.startswith("$data/"):
        return fn
    rel = fn[len("$data/"):]; base = os.path.splitext(rel)[0]
    cands = (rel, base + ".dds", base + ".png")
    if any(os.path.exists(os.path.join(FS25_DATA, c.replace("/", os.sep))) for c in cands):
        return fn                                                # FS25 base game has it -> keep the $data ref
    for c in cands:
        srcp = os.path.join(FS22_DATA, c.replace("/", os.sep))
        if os.path.exists(srcp):
            local = "fs22/" + c
            dst = os.path.join(OUT, "maps", local.replace("/", os.sep))
            if not os.path.exists(dst):
                os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copy(srcp, dst)
            return local                                         # pulled loose FS22 texture into maps/fs22/
    return None


def internal_sid(d):
    nl = struct.unpack("<i", d[:4])[0]
    if not (0 < nl < 200):
        return None
    off = (4 + nl + 3) & ~3
    return struct.unpack("<I", d[off:off + 4])[0] if off + 4 <= len(d) else None


def main():
    wr = ET.parse(FS22_I3D).getroot()
    wfiles = {f.get("fileId"): f.get("filename") for f in wr.iter("File")}
    wmats = {m.get("materialId"): m for m in wr.iter("Material")}
    scene = wr.find("Scene")
    # ROAD SHAPE SELECTION - two paths:
    #  (a) NAMED GROUPS (WW): config scene_groups road/bridge/tunnel groups exist -> take those subtrees wholesale
    #      (hierarchy = placement; everything under them is road-related). Unchanged, zero regression.
    #  (b) CONTENT-DETECT (West End & any map whose roads aren't in a clean group): no group matched -> classify every
    #      shape by material/texture and keep the ones tagged "road" (asphalt/gravel/... texture), then rebuild a pruned
    #      tree preserving their placement. This finds roads scattered/mislabeled across the scene.
    # PER-MAP flats shape filter (config `flats.only_prefixes` / `flats.skip_prefixes`, matched on Shape NAME prefix).
    # Lets a map whittle the content-detect road set down to specific families for isolation, WITHOUT touching code or
    # other maps. only_prefixes: None (absent) = keep all; [] = keep NONE (strip everything); [..] = keep only names
    # starting with one of these. skip_prefixes always drops matches. Content-detect (tag-based) only; group-based maps
    # (WW) with no `flats` config are unaffected.
    _FLATS = CONV.get("flats", {})
    _only = _FLATS.get("only_prefixes")
    _skip = tuple(_FLATS.get("skip_prefixes", []))
    def _keep(sh):
        n = sh.get("name") or ""
        if _only is not None and not any(n.startswith(p) for p in _only):
            return False
        return not (_skip and any(n.startswith(p) for p in _skip))

    targets = [ch for top in scene if top.get("name") in TOP for ch in top if ch.get("name") in GROUPS]
    tag_based = not targets
    if tag_based:
        tag, _ = cls.build_tagger(wr)
        road_shapes = [sh for sh in scene.iter("Shape") if tag(sh) == "road" and _keep(sh)]
        emit_nodes = prune_to_shapes(scene, road_shapes)
        sel = f"classify tag=road ({len(road_shapes)} shapes, {len(emit_nodes)} top groups)"
        if _only is not None or _skip:
            sel += f" [filter only={_only} skip={list(_skip)}]"
    else:
        road_shapes = [sh for g in targets for sh in g.iter("Shape")]
        emit_nodes = targets
        sel = f"groups {[g.get('name') for g in targets]}"
    shapeids, used = set(), set()
    for sh in road_shapes:
        if sh.get("shapeId"):
            shapeids.add(int(sh.get("shapeId")))
        for m in (sh.get("materialIds") or "").split(","):
            if m:
                used.add(m)

    # meshes -> our .shapes (raw-copy by internal shapeId; keep ids, no remap)
    if os.path.exists(CACHE):
        ver, seed, ents = pickle.load(open(CACHE, "rb"))
    else:
        print("cache miss - decoding WW .shapes (~2 min)...")
        ver, seed, ents = sc.decode_entities(FS22_I3D + ".shapes")
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        pickle.dump((ver, seed, ents), open(CACHE, "wb"))
    sid2ent = {}
    for et, d in ents:
        if et == 1:
            s = internal_sid(d)
            if s in shapeids:
                sid2ent[s] = (et, d)
    keep_ents = list(sid2ent.values())
    missing = shapeids - set(sid2ent)
    open(OUT_SHAPES, "wb").write(sc.encode_entities(ver, seed, keep_ents))
    print(f"[shapes] {len(keep_ents)}/{len(shapeids)} meshes -> wildwest.i3d.shapes"
          + (f"  MISSING {sorted(missing)}" if missing else ""))

    # our i3d: <Shapes> ref + materials/textures
    tree = ET.parse(OUT_I3D); root = tree.getroot()
    ofiles = root.find("Files"); omats = root.find("Materials"); oscene = root.find("Scene")
    # RESET prior road artifacts (idempotent): road materials (id>2) + their files + WW_roads group + <Shapes>
    road_fids = set()
    for m in list(omats):
        if int(m.get("materialId")) > 2:
            road_fids.update(t.get("fileId") for t in m if t.get("fileId"))
            omats.remove(m)
    for f in list(ofiles):
        if f.get("fileId") in road_fids:
            ofiles.remove(f)
    for g in list(oscene):
        if g.tag == "TransformGroup" and g.get("name") == "WW_roads":
            oscene.remove(g)
    sh0 = root.find("Shapes")
    if sh0 is not None:
        root.remove(sh0)
    root.insert(list(root).index(omats) + 1, ET.Element("Shapes", {"externalShapesFile": SHAPES_NAME}))
    nextf = max(int(f.get("fileId")) for f in ofiles) + 1
    have = {m.get("materialId") for m in omats}; fmap = {}; copied = 0
    decal_mids = set(); line_mids = set(); decal_shader_fid = None   # overlays get decal shader; lines render above wear
    DECAL_KW = ("line", "stripe", "decal", "crack", "mark", "slick")

    def add_file(wfn):                                           # add a File ref, resolving $data via FS25/FS22; None if unresolvable
        nonlocal nextf, copied
        resolved = wfn
        if wfn.startswith("$data/"):
            resolved = resolve_data(wfn)
            if resolved is None:
                return None                                     # $data texture missing from BOTH FS25 and the FS22 install
            if not resolved.startswith("$"):
                copied += 1                                     # a loose FS22 texture pulled into maps/fs22/
        elif wfn and not wfn.startswith("$"):                   # map-local texture (relative to the FS22 map dir)
            src = os.path.normpath(os.path.join(FS22_MAPS, wfn.replace("/", os.sep)))
            if not os.path.exists(src):
                return None                                     # the map references a texture it never shipped
            norm = wfn.replace("\\", "/")
            # a ref may ESCAPE the map dir into a sibling REQUIRED mod (West End roads: ../../../FS22_WestEnd_Vehicles/
            # maps/textures/asphalt_*.dds). Mirror it INTO our map under maps/imported/ (flattened) + rewrite the ref,
            # else it points outside the deployed mod -> white textures.
            local = ("imported/" + "/".join(p for p in norm.split("/") if p not in ("..", "."))
                     if norm.startswith("../") or "/../" in norm else norm)
            dst = os.path.join(OUT, "maps", local.replace("/", os.sep))
            os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copy(src, dst); copied += 1
            resolved = local
        nf = str(nextf); nextf += 1
        ET.SubElement(ofiles, "File", {"fileId": nf, "filename": resolved})
        return nf

    for mid in sorted(used, key=int):
        wmat = wmats.get(mid)
        if wmat is None or mid in have:
            continue
        nm = copy.deepcopy(wmat)
        csid = nm.get("customShaderId")                         # TRANSLATE existing shader ref (bridges/etc. pointed at wrong file)
        if csid:
            if csid not in fmap:
                fmap[csid] = add_file(wfiles.get(csid, ""))
            if fmap[csid]:
                nm.set("customShaderId", fmap[csid])
        remap = MAT_REMAP.get(nm.get("name"))
        if remap:                                               # WW never shipped this texture -> base-game substitute
            for tex in [t for t in nm if t.tag in ("Texture", "Normalmap", "Glossmap")]:
                nm.remove(tex)
            for tag, path in remap.items():
                if path not in fmap:
                    fmap[path] = add_file(path)
                if fmap[path]:
                    ET.SubElement(nm, tag, {"fileId": fmap[path]})
        else:
            for tex in list(nm):
                fid = tex.get("fileId")
                if not fid:
                    continue
                if fid not in fmap:
                    fmap[fid] = add_file(wfiles.get(fid, ""))   # resolves $data via FS25/FS22-install; None -> unresolvable
                if fmap[fid]:
                    tex.set("fileId", fmap[fid])
                else:
                    nm.remove(tex)                              # missing in FS25 AND FS22 install -> drop (was white)
        nml = (nm.get("name") or "").lower()                     # co-planar overlay + no shader -> assign the decal shader
        is_alpha = wmat.get("alphaBlending") == "true" and not wmat.get("customShaderId")   # hash-named lines (e.g. ff7a80c2)
        if not nm.get("customShaderId") and (any(k in nml for k in DECAL_KW) or nm.get("name") in DECAL_MATS or is_alpha):
            if decal_shader_fid is None:
                decal_shader_fid = add_file("$data/shaders/decalShader.xml")
            nm.set("customShaderId", decal_shader_fid); nm.set("alphaBlending", "true")
            decal_mids.add(mid)
            if any(k in nml for k in ("line", "stripe")) or nm.get("name") in DECAL_MATS or is_alpha:
                line_mids.add(mid)                              # lines/markings render ABOVE wear/cracks
        omats.append(nm); have.add(mid)

    # emit the selected road subtrees into WW_roads (hierarchy = placement), collision on every shape. Group-based nodes
    # are originals (deepcopy); content-detect nodes are already fresh pruned copies (append directly).
    roads_root = ET.SubElement(oscene, "TransformGroup", {"name": "WW_roads"})
    for g in emit_nodes:
        roads_root.append(g if tag_based else copy.deepcopy(g))
    # STRIP dangling-shape nodes: content-detect can tag roadside fence/LOD shapes as "road", but their geometry lives
    # in $data/another i3d - NOT this map's .shapes - so their shapeIds are in `missing`. Emitting them leaves dangling
    # externalShapesFile refs that FS25 replaces with empty transform groups and then HANGS the load at 55% (West End:
    # 964 fenceFourPlanks2m + 771 LOD0). Drop every emitted Shape whose id we couldn't extract; keep only real road mesh.
    n_dropped = 0
    if missing:
        rparent = {c: p for p in roads_root.iter() for c in p}
        for sh in list(roads_root.iter("Shape")):
            sid = sh.get("shapeId")
            if sid and int(sid) in missing:
                p = rparent.get(sh)
                if p is not None:
                    p.remove(sh); n_dropped += 1
    # DEDUP coincident road shapes: the FS22 source ships redundant road tiles - the SAME mesh (shapeId) at the SAME
    # world position with the SAME material, stacked 2-3 deep (WW: 251 spots, mostly TRIPLED). Coplanar identical
    # surfaces z-fight (shimmer) and their stacked collision makes vehicles bump/step. They resolve to the same WORLD
    # position via DIFFERENT parent chains (e.g. '073' under group '014' + '001' under a separate identically-placed
    # group), so we must compare WORLD transforms, not local. Keep the first of each coincident set, drop the rest.
    if _FLATS.get("dedup_road_shapes", True):
        def _trs(n):
            t = [float(x) for x in (n.get("translation") or "0 0 0").split()]
            r = [np.radians(float(x)) for x in (n.get("rotation") or "0 0 0").split()]
            s = [float(x) for x in (n.get("scale") or "1 1 1").split()]
            cx, cy, cz = np.cos(r); sx, sy, sz = np.sin(r)
            Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
            Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
            Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
            M = np.eye(4); M[:3, :3] = (Rx @ Ry @ Rz) * np.array(s); M[:3, 3] = t
            return M
        seen = {}
        def _scan(n, M0):
            M = M0 @ _trs(n)
            if n.tag == "Shape":
                w = M[:3, 3]
                key = (n.get("shapeId"), round(float(w[0]), 2), round(float(w[1]), 2), round(float(w[2]), 2), n.get("materialIds"))
                seen.setdefault(key, []).append(n)
            for ch in n:
                if ch.tag in ("TransformGroup", "Shape"):
                    _scan(ch, M)
        _scan(roads_root, np.eye(4))
        dparent = {c: p for p in roads_root.iter() for c in p}
        n_dedup = 0
        for nodes in seen.values():
            for extra in nodes[1:]:                                 # keep nodes[0], remove the coincident twins
                p = dparent.get(extra)
                if p is not None:
                    p.remove(extra); n_dedup += 1
        if n_dedup:
            print(f"[i3d] dedup: removed {n_dedup} coincident-duplicate road shape(s) ({len(seen)} unique surfaces kept)")

    # collision on road surfaces; decal overlays get NONE (asphalt beneath carries it; decal shader handles the depth)
    road_collision = _FLATS.get("collision", True)              # per-map: set flats.collision=false to emit roads as
    n_shapes = n_decals = 0                                     # visual-only (isolation test: is the collision the hang?)
    for sh in roads_root.iter("Shape"):
        d = (sh.get("materialIds") or "").split(",")
        if any(m in decal_mids for m in d):
            sh.set("decalLayer", "2" if any(m in line_mids for m in d) else "1")   # lines above wear; on-top, no collision
            n_decals += 1
            continue
        if road_collision:
            for k, v in ROAD_COLLISION.items():                 # ROAD preset (Static, group/mask hex, density 1)
                sh.set(k, v)
        n_shapes += 1
    nid = max((int(e.get("nodeId")) for e in root.iter() if (e.get("nodeId") or "").isdigit()), default=0) + 1
    for e in roads_root.iter():                                  # fresh unique nodeIds for all copied nodes
        if e.get("nodeId") is not None:
            e.set("nodeId", str(nid)); nid += 1

    tree.write(OUT_I3D, encoding="utf-8", xml_declaration=True)
    print(f"[i3d] +{len(have)-2} materials ({copied} tex) | {len(decal_mids)} overlay mats->decalShader, "
          f"{n_decals} decals decalLayer=1 | {n_shapes} road shapes ROAD-collision(0x601c/0xfffffbff) via {sel}"
          + (f" | dropped {n_dropped} dangling-shape node(s)" if n_dropped else ""))


if __name__ == "__main__":
    main()
