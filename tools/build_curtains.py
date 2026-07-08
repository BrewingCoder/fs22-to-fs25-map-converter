"""
build_curtains.py - PHASE: the MAP CURTAIN (distant backdrop that rings the map so the world doesn't just end).
WW's curtain is the group `horizone` (under gameplay/mapEnds): 2 `distanceHills` (grass_DistanceHills texture, E/W
edges) + 2 `bgMountainUS` (bgMountain texture, N/S edges), baked ver=7 meshes using WW-LOCAL textures (the allowed
FS22 scenery class). FS25 calls this a "backgroundMesh". Same extraction path as build_buildings: raw-copy the meshes
into wildwest.i3d.shapes, copy the materials, resolve their textures (WW-local first, then the FS22 install for any
$data ref), copy the group subtree verbatim (its ancestors gameplay/mapEnds carry no transform, so its own transform
IS its world transform). NO collision - it's a far-off visual backdrop you never touch. Idempotent (drops WW_curtain).
Run after build_buildings (shares wildwest.i3d.shapes + the flats_ents.pkl decode cache).
"""
import os, sys, json, pickle, struct, shutil, copy
import xml.etree.ElementTree as ET
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import shapes_codec as sc

WW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import convert_env
CONV = json.load(open(os.path.join(WW, "tools", os.environ.get("MAP_CONVERT", "wildwest.convert.json")), encoding="utf-8"))
FS22_I3D = os.path.join(convert_env.source_dir(CONV), CONV["source"]["map_i3d"])
FS22_DATA = os.environ.get("FS22_DATA", r"C:\Program Files (x86)\Steam\steamapps\common\Farming Simulator 22\data")
FS22_MAPS = os.path.dirname(FS22_I3D)
OUT = os.path.join(WW, "out", CONV["identity"]["mod"])
OUT_I3D = os.path.join(OUT, "maps", CONV["identity"]["i3d"])
OUT_SHAPES = os.path.join(OUT, "maps", "wildwest.i3d.shapes")
CACHE = os.environ.get("FS_CONVERT_CACHE", os.path.join(os.path.expanduser("~"), ".fs_convert_cache", "flats_ents.pkl"))
GROUPS = tuple(CONV["scene_groups"].get("curtain", ["horizone"]))   # WW's backdrop group(s), map-specific (from config)


def internal_sid(d):
    nl = struct.unpack("<i", d[:4])[0]
    if not (0 < nl < 200):
        return None
    off = (4 + nl + 3) & ~3
    return struct.unpack("<I", d[off:off + 4])[0] if off + 4 <= len(d) else None


def patch_sid(d, newid):
    """Rewrite a mesh entity's internal shapeId (the u32 after its name) to newid, returning patched bytes."""
    nl = struct.unpack("<i", d[:4])[0]
    off = (4 + nl + 3) & ~3
    return d[:off] + struct.pack("<I", newid) + d[off + 4:]


def resolve_fs22(fn):
    """WW $data ref -> real FS22 loose file (WW refs .png, file is .dds). -> (abspath, local_rel) or None."""
    if not fn.startswith("$data/") or "/" not in fn[6:]:
        return None
    rel = fn[len("$data/"):].replace("/", os.sep)
    base, _ = os.path.splitext(rel)
    for cand in (rel, base + ".dds", base + ".png"):
        p = os.path.join(FS22_DATA, cand)
        if os.path.exists(p):
            return p, os.path.join("fs22", cand)
    return None


def resolve_local(fn):
    """WW-mod-LOCAL texture ref (relative, e.g. 'textures/grass_DistanceHills_diffuse.png') -> file under <WW>/maps/."""
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
    targets = [g for g in wr.find("Scene").iter("TransformGroup") if g.get("name") in GROUPS]
    if not targets:
        print(f"[curtain] no group named {GROUPS} in the scene - nothing to do"); return
    shapeids, used = set(), set()
    for g in targets:
        for sh in g.iter("Shape"):
            if sh.get("shapeId"):
                shapeids.add(int(sh.get("shapeId")))
            for m in (sh.get("materialIds") or "").split(","):
                if m:
                    used.add(m)

    # 1. MERGE curtain meshes into the existing .shapes (keep roads + buildings). WW REUSES low shapeIds across its
    # imported sub-i3ds - the curtain's ids (10=distanceHills, 11=bgMountain) already belong to road meshes in OUT.
    # So REMAP the curtain meshes to fresh unique ids and rewrite the curtain Shape nodes to match (step 3).
    ver, seed, ents = pickle.load(open(CACHE, "rb"))
    keep_ents, used_ids = [], set()
    if os.path.exists(OUT_SHAPES):
        v2, s2, e2 = sc.decode_entities(OUT_SHAPES)
        keep_ents = list(e2)
        used_ids = {internal_sid(d) for et, d in e2 if et == 1}
    next_id = max((i for i in used_ids if i is not None), default=1000) + 1000    # gap above every existing mesh id
    remap = {}                                                                    # WW curtain mesh id -> fresh unique id
    for et, d in ents:
        if et == 1:
            s = internal_sid(d)
            if s in shapeids and s not in remap:
                remap[s] = next_id; next_id += 1
                keep_ents.append((et, patch_sid(d, remap[s])))
    open(OUT_SHAPES, "wb").write(sc.encode_entities(ver, seed, keep_ents))
    print(f"[shapes] merged -> {len(keep_ents)} total meshes (+{len(remap)} curtain, remapped ids {remap})")

    # 2. materials + textures (WW-local first, then FS22 install). Like the meshes, WW's low materialIds collide with
    # OUT's (curtain grass_mat=293/bgMountain_mat=73 already hold building materials in OUT) -> REMAP to fresh ids.
    tree = ET.parse(OUT_I3D); root = tree.getroot()
    ofiles = root.find("Files"); omats = root.find("Materials"); oscene = root.find("Scene")
    next_mid = max((int(m.get("materialId")) for m in omats), default=0) + 1
    nextf = max(int(f.get("fileId")) for f in ofiles) + 1
    fmap = {}; mat_remap = {}; copied = miss = 0

    def add_tex_file(wfn):
        nonlocal nextf, copied, miss
        loc = resolve_local(wfn); r = None if loc else resolve_fs22(wfn)   # WW-local scenery texture first, then $data
        if loc:
            src, local = loc
            dst = os.path.join(OUT, "maps", local.replace("/", os.sep)); fn_out = local
        elif r:
            src, local = r
            dst = os.path.join(OUT, "maps", local); fn_out = local.replace(os.sep, "/")
        elif wfn.startswith("$"):
            fn_out = wfn; src = dst = None
        else:
            miss += 1; return None
        if dst and not os.path.exists(dst):
            os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copy(src, dst); copied += 1
        nf = str(nextf); nextf += 1
        ET.SubElement(ofiles, "File", {"fileId": nf, "filename": fn_out})
        return nf

    for mid in sorted(used, key=int):
        wmat = wmats.get(mid)
        if wmat is None:
            continue
        nm = copy.deepcopy(wmat)
        nm.set("materialId", str(next_mid)); mat_remap[mid] = str(next_mid); next_mid += 1   # fresh id, no collision
        csid = nm.get("customShaderId")
        if csid:
            fmap.setdefault(csid, add_tex_file(wfiles.get(csid, "")))
            if fmap[csid]:
                nm.set("customShaderId", fmap[csid])
        for tex in list(nm):
            fid = tex.get("fileId")
            if not fid:
                continue
            fmap.setdefault(fid, add_tex_file(wfiles.get(fid, "")))
            if fmap[fid]:
                tex.set("fileId", fmap[fid])
            else:
                nm.remove(tex)
        omats.append(nm)
    n_mats = len(mat_remap)

    # 3. copy the curtain subtree verbatim (placement) under WW_curtain - NO collision (far-off backdrop)
    for g in list(oscene):
        if g.tag == "TransformGroup" and g.get("name") == "WW_curtain":
            oscene.remove(g)
    cg = ET.SubElement(oscene, "TransformGroup", {"name": "WW_curtain"})
    for g in targets:
        cg.append(copy.deepcopy(g))
    n_shapes = 0
    for sh in cg.iter("Shape"):                                       # point curtain shapes at their remapped mesh + material ids
        n_shapes += 1
        old = sh.get("shapeId")
        if old and int(old) in remap:
            sh.set("shapeId", str(remap[int(old)]))
        mids = sh.get("materialIds")
        if mids:
            sh.set("materialIds", ",".join(mat_remap.get(m, m) for m in mids.split(",")))
    nid = max((int(e.get("nodeId")) for e in root.iter() if (e.get("nodeId") or "").isdigit()), default=0) + 1
    for e in cg.iter():
        if e.get("nodeId") is not None:
            e.set("nodeId", str(nid)); nid += 1

    tree.write(OUT_I3D, encoding="utf-8", xml_declaration=True)
    print(f"[i3d] +{n_mats} materials ({copied} textures copied, {miss} unresolved) | curtain: {n_shapes} shapes, "
          f"groups={[g.get('name') for g in targets]} under WW_curtain (no collision)")


if __name__ == "__main__":
    main()
