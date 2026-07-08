"""
build_buildings.py - PHASE 4: extract WW's Buildings (the town) into our FS25 map.
FS25 RENAMED all of WW's FS22 $data assets, but FS22 is INSTALLED - so we source the referenced textures from the
FS22 install (WW refs '...X.png', the loose file is '...X.dds'; engine is extension-agnostic) and copy them local,
and raw-copy the building meshes (ver=7) from WW's .shapes exactly like the roads. Buildings WW marked collidable
get the FS25 building collision preset. Merges building meshes into the existing wildwest.i3d.shapes (keeps roads).
Idempotent (drops prior WW_buildings). Run after build_flats. Needs the flats_ents.pkl decode cache.
"""
import os, re, sys, json, pickle, struct, shutil, copy
import xml.etree.ElementTree as ET
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import shapes_codec as sc

WW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import convert_env
CONV = json.load(open(os.path.join(WW, "tools", os.environ.get("MAP_CONVERT", "wildwest.convert.json")), encoding="utf-8"))
FS22_I3D = os.path.join(convert_env.source_dir(CONV), CONV["source"]["map_i3d"])
FS22_DATA = os.environ.get("FS22_DATA", r"C:\Program Files (x86)\Steam\steamapps\common\Farming Simulator 22\data")
FS22_MAPS = os.path.dirname(FS22_I3D)                  # <WW mod>/maps - root for WW's own (local, non-$data) textures
OUT = os.path.join(WW, "out", CONV["identity"]["mod"])
OUT_I3D = os.path.join(OUT, "maps", CONV["identity"]["i3d"])
OUT_SHAPES = os.path.join(OUT, "maps", "wildwest.i3d.shapes")
CACHE = os.environ.get("FS_CONVERT_CACHE", os.path.join(os.path.expanduser("~"), ".fs_convert_cache", "flats_ents.pkl"))
TOPS = tuple(CONV["scene_groups"]["top"])              # top-level scene groups to search (map-specific, from config)
GROUPS = tuple(CONV["scene_groups"]["buildings"])      # building subgroups to extract (Buildings + harbor: pierspot/hotelMedium01)
BLD_COLLISION = {"static": "true", "collision": "true", "collisionFilterGroup": "0x601c",
                 "collisionFilterMask": "0xfffffbff", "density": "1"}   # SAME preset as the roads (build_flats) - the one
#                confirmed solid to vehicles/player in-game. The 0x3e "building" group did NOT stop vehicles (walk-through).


def internal_sid(d):
    nl = struct.unpack("<i", d[:4])[0]
    if not (0 < nl < 200):
        return None
    off = (4 + nl + 3) & ~3
    return struct.unpack("<I", d[off:off + 4])[0] if off + 4 <= len(d) else None


def resolve_fs22(fn):
    """WW $data ref -> real FS22 loose file (WW refs .png, file is .dds). -> (fs22_abspath, local_rel) or None."""
    if not fn.startswith("$data/") or "/" not in fn[6:]:
        return None
    rel = fn[len("$data/"):].replace("/", os.sep)
    base, _ = os.path.splitext(rel)
    for cand in (rel, base + ".dds", base + ".png"):
        p = os.path.join(FS22_DATA, cand)
        if os.path.exists(p):
            return p, os.path.join("fs22", cand)          # mirror under maps/fs22/<path>
    return None


def resolve_local(fn):
    """WW-mod-LOCAL texture ref (relative, non-$data e.g. 'texture/batiment1.dds') -> the file in <WW mod>/maps/.
    These are WW's OWN structure/prop textures (allowed to carry). Extension-agnostic (ref .png, file may be .dds).
    -> (src_abspath, local_rel) mirrored at the SAME relative path under our maps/, or None if WW never shipped it."""
    if not fn or fn.startswith("$"):
        return None
    rel = fn.replace("/", os.sep)
    base, _ = os.path.splitext(rel)
    for cand in (rel, base + ".dds", base + ".png"):
        p = os.path.join(FS22_MAPS, cand)
        if os.path.exists(p):
            return p, cand.replace(os.sep, "/")
    return None


def main():
    wr = ET.parse(FS22_I3D).getroot()
    wfiles = {f.get("fileId"): (f.get("filename") or "") for f in wr.iter("File")}
    wmats = {m.get("materialId"): m for m in wr.iter("Material")}
    scene = wr.find("Scene")
    targets = [ch for top in scene if top.get("name") in TOPS
               for ch in top if ch.get("name") in GROUPS]
    # a GROUPS name may also be a TOP-LEVEL group (sibling of TOPS), e.g. 'placeholders' = the base-game FS22
    # production/sell BUILDING models (main dairy/bakery/etc bodies). Those are the authentic FS22 building visuals;
    # our hidden function placeables replace only the base-game FUNCTION, so extracting these = the WW look, no double-up.
    targets += [g for g in scene if g.get("name") in GROUPS and g not in targets]
    shapeids, used = set(), set()
    for g in targets:
        for sh in g.iter("Shape"):
            if sh.get("shapeId"):
                shapeids.add(int(sh.get("shapeId")))
            for m in (sh.get("materialIds") or "").split(","):
                if m:
                    used.add(m)

    # 1. MERGE building meshes into the existing .shapes (keep road meshes)
    ver, seed, ents = pickle.load(open(CACHE, "rb"))
    have_ids = set()
    keep_ents = []
    if os.path.exists(OUT_SHAPES):                        # existing road meshes
        v2, s2, e2 = sc.decode_entities(OUT_SHAPES)
        for et, d in e2:
            keep_ents.append((et, d)); have_ids.add(internal_sid(d) if et == 1 else None)
    for et, d in ents:                                   # add building meshes not already present
        if et == 1:
            s = internal_sid(d)
            if s in shapeids and s not in have_ids:
                keep_ents.append((et, d)); have_ids.add(s)
    open(OUT_SHAPES, "wb").write(sc.encode_entities(ver, seed, keep_ents))
    print(f"[shapes] merged -> {len(keep_ents)} total meshes (added buildings)")

    # 2. our i3d: materials + FS22 textures
    tree = ET.parse(OUT_I3D); root = tree.getroot()
    ofiles = root.find("Files"); omats = root.find("Materials"); oscene = root.find("Scene")
    have = {m.get("materialId") for m in omats}
    nextf = max(int(f.get("fileId")) for f in ofiles) + 1
    fmap = {}; copied = 0; miss = 0

    def add_tex_file(wfn):
        nonlocal nextf, copied, miss
        r = resolve_fs22(wfn)
        loc = None if r else resolve_local(wfn)          # $data (FS22-install) first, else WW-mod-local texture
        if r:
            src, local = r
            dst = os.path.join(OUT, "maps", local)
            if not os.path.exists(dst):
                os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copy(src, dst); copied += 1
            fn_out = local.replace(os.sep, "/")
        elif loc:
            src, local = loc
            dst = os.path.join(OUT, "maps", local.replace("/", os.sep))
            if not os.path.exists(dst):
                os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copy(src, dst); copied += 1
            fn_out = local
        elif wfn.startswith("$"):
            fn_out = wfn                                  # keep $data (shaders/shared that still resolve)
        else:
            miss += 1; return None
        nf = str(nextf); nextf += 1
        ET.SubElement(ofiles, "File", {"fileId": nf, "filename": fn_out})
        return nf

    for mid in sorted(used, key=int):
        wmat = wmats.get(mid)
        if wmat is None or mid in have:
            continue
        nm = copy.deepcopy(wmat)
        csid = nm.get("customShaderId")
        if csid:
            if csid not in fmap:
                fmap[csid] = add_tex_file(wfiles.get(csid, ""))
            if fmap[csid]:
                nm.set("customShaderId", fmap[csid])
        for tex in list(nm):
            fid = tex.get("fileId")
            if not fid:
                continue
            if fid not in fmap:
                fmap[fid] = add_tex_file(wfiles.get(fid, ""))
            if fmap[fid]:
                tex.set("fileId", fmap[fid])
            else:
                nm.remove(tex)                            # texture truly gone (WW broken ref)
        omats.append(nm); have.add(mid)

    # 3. copy Buildings subtree wholesale (preserve hierarchy); FS25 building collision on WW-collidable shapes
    for g in list(oscene):
        if g.tag == "TransformGroup" and g.get("name") == "WW_buildings":
            oscene.remove(g)
    bg = ET.SubElement(oscene, "TransformGroup", {"name": "WW_buildings"})
    for g in targets:
        bg.append(copy.deepcopy(g))
    # SOLID buildings: mirror the road-surface fix (build_flats) - apply the ROAD collision preset directly to the
    # meshes (WW ships proper collision meshes for only ~34 shapes; the rest of the town/harbor was walk-through).
    # User (2026-07-07): ALL harbor shapes need collision (walls, water barriers, buildings) -> apply to EVERY non-LOD
    # shape, renderable AND nonRenderable (barriers/collision meshes are often nonRenderable). LODs skipped (collision
    # is distance-independent, so far-LOD duplicates would just be redundant static geometry).
    n_col = n_shapes = n_lod = 0
    for sh in bg.iter("Shape"):
        n_shapes += 1
        name = sh.get("name") or ""
        is_lod = name.endswith("_LOD") or re.search(r"_LOD[1-9]\d*$", name) is not None
        if is_lod:                                        # far-LOD duplicate of a mesh that already gets collision
            n_lod += 1
            continue
        for k in ("collisionMask", "collisionFilterGroup", "collisionFilterMask"):
            sh.attrib.pop(k, None)                        # clear any WW FS22 collision attrs, then apply the FS25 road preset
        for k, v in BLD_COLLISION.items():
            sh.set(k, v)
        n_col += 1
    nid = max((int(e.get("nodeId")) for e in root.iter() if (e.get("nodeId") or "").isdigit()), default=0) + 1
    for e in bg.iter():
        if e.get("nodeId") is not None:
            e.set("nodeId", str(nid)); nid += 1

    tree.write(OUT_I3D, encoding="utf-8", xml_declaration=True)
    print(f"[i3d] +{len(have)} materials ({copied} FS22 textures copied, {miss} unresolved) | "
          f"+{n_shapes} building shapes ({n_col} SOLID, {n_lod} LOD-skipped) under WW_buildings")


if __name__ == "__main__":
    main()
