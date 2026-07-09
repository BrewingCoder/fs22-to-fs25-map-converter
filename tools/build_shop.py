"""
build_shop.py - PHASE 7: port WW's FUNCTIONAL vehicle shop into our FS25 map.
The shop BUILDING (Concession Claas) already comes via build_buildings; this adds the gameplay config FS25 needs so
the dealership actually works: storeSpawnPlace1/2Start->End (where bought vehicles appear), vehicleShopTrigger (the
enter-shop trigger) and the shopping marker icon - WITH their onCreate UserAttributes
(BaseMission.onCreateStoreSpawnPlace / ShopTrigger.onCreate). FS25 uses the EXACT same node names + callbacks
(verified in $data/maps/mapUS), so it's a direct port. The trigger/icon meshes are merged into wildwest.i3d.shapes;
the icon material is 100% FS25 $data (glowShader + shared marker texture), the trigger is a plain color.
CRITICAL: UserAttributes are keyed by nodeId in a SEPARATE block, so we remap them to the renumbered nodeIds
(a subtree copy alone would orphan every onCreate script). Idempotent (drops prior WW_shop). Atomic .shapes write.
Config: scene_groups.shop {source_top, source_group}. Run after build_buildings (merges into the same .shapes).
"""
import os, sys, json, pickle, struct, copy
import xml.etree.ElementTree as ET
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import shapes_codec as sc

WW = os.environ.get("FS_CONVERT_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import convert_env
CONV = json.load(open(os.path.join(WW, "tools", os.environ.get("MAP_CONVERT", "wildwest.convert.json")), encoding="utf-8"))
FS22_I3D = os.path.join(convert_env.source_dir(CONV), CONV["source"]["map_i3d"])
OUT = os.path.join(WW, "out", CONV["identity"]["mod"])
OUT_I3D = os.path.join(OUT, "maps", CONV["identity"]["i3d"])
OUT_SHAPES = os.path.join(OUT, "maps", os.path.splitext(CONV["identity"]["i3d"])[0] + ".i3d.shapes")
CACHE = os.environ.get("FS_CONVERT_CACHE", os.path.join(os.path.expanduser("~"), ".fs_convert_cache", "flats_ents.pkl"))
SHOP = CONV.get("scene_groups", {}).get("shop", {"source_top": "gameplay", "source_group": "vehicleShop"})


def internal_sid(d):
    nl = struct.unpack("<i", d[:4])[0]
    if not (0 < nl < 200):
        return None
    off = (4 + nl + 3) & ~3
    return struct.unpack("<I", d[off:off + 4])[0] if off + 4 <= len(d) else None


def main():
    wr = ET.parse(FS22_I3D).getroot()
    wfiles = {f.get("fileId"): (f.get("filename") or "") for f in wr.iter("File")}
    wmats = {m.get("materialId"): m for m in wr.iter("Material")}
    _wuab = wr.find("UserAttributes")
    wua = {u.get("nodeId"): u for u in (_wuab if _wuab is not None else [])}

    # locate the functional shop group (gameplay/vehicleShop)
    tops = [t for t in wr.find("Scene") if t.get("name") == SHOP["source_top"]]
    grp_src = None
    for t in tops:
        for c in t:
            if c.get("name") == SHOP["source_group"]:
                grp_src = c
    if grp_src is None:
        raise SystemExit(f"shop group {SHOP['source_top']}/{SHOP['source_group']} not found")

    shapeids, used = set(), set()
    for sh in grp_src.iter("Shape"):
        if sh.get("shapeId"):
            shapeids.add(int(sh.get("shapeId")))
        for m in (sh.get("materialIds") or "").split(","):
            if m:
                used.add(m)

    # 1. merge trigger/icon meshes into .shapes (keep everything already there)
    ver, seed, ents = pickle.load(open(CACHE, "rb"))
    have_ids, keep = set(), []
    if os.path.exists(OUT_SHAPES):
        v2, s2, e2 = sc.decode_entities(OUT_SHAPES)
        for et, d in e2:
            keep.append((et, d)); have_ids.add(internal_sid(d) if et == 1 else None)
    added = 0
    for et, d in ents:
        if et == 1:
            s = internal_sid(d)
            if s in shapeids and s not in have_ids:
                keep.append((et, d)); have_ids.add(s); added += 1
    blob = sc.encode_entities(ver, seed, keep)
    tmp = OUT_SHAPES + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(blob)
    os.replace(tmp, OUT_SHAPES)

    # 2. i3d: materials (+ $data files), subtree under gameplay, UserAttributes remap
    tree = ET.parse(OUT_I3D); root = tree.getroot()
    ofiles = root.find("Files"); omats = root.find("Materials"); oscene = root.find("Scene")
    oua = root.find("UserAttributes")
    if oua is None:
        oua = ET.SubElement(root, "UserAttributes")

    # idempotent: drop prior WW_shop group + its materials
    ogameplay = next((g for g in oscene if g.get("name") == SHOP["source_top"]), None)
    if ogameplay is None:
        ogameplay = ET.SubElement(oscene, "TransformGroup", {"name": SHOP["source_top"]})
    for g in list(ogameplay):
        if g.get("name") == "WW_shop":
            # remove its UserAttributes too
            ids = {e.get("nodeId") for e in g.iter() if e.get("nodeId")}
            for u in [u for u in oua if u.get("nodeId") in ids]:
                oua.remove(u)
            ogameplay.remove(g)
    for m in list(omats):
        if (m.get("name") or "").startswith("ww_shop_"):
            omats.remove(m)

    fmap = {}
    nextf = [max((int(f.get("fileId")) for f in ofiles), default=0) + 1]

    def add_file(wfn):
        if not wfn:
            return None
        if wfn in fmap:
            return fmap[wfn]
        nf = str(nextf[0]); nextf[0] += 1
        ET.SubElement(ofiles, "File", {"fileId": nf, "filename": wfn})   # shop files are all FS25 $data (glow/marker)
        fmap[wfn] = nf
        return nf

    matmap = {}
    nextm = max((int(m.get("materialId")) for m in omats), default=0) + 1
    for mid in sorted(used, key=int):
        wmat = wmats.get(mid)
        if wmat is None:
            continue
        nm = copy.deepcopy(wmat)
        nm.set("name", "ww_shop_" + (nm.get("name") or mid))
        nm.set("materialId", str(nextm))
        csid = nm.get("customShaderId")
        if csid:
            nf = add_file(wfiles.get(csid, ""))
            if nf:
                nm.set("customShaderId", nf)
        for sub in list(nm):
            fid = sub.get("fileId")
            if fid:
                nf = add_file(wfiles.get(fid, ""))
                if nf:
                    sub.set("fileId", nf)
                else:
                    nm.remove(sub)
        omats.append(nm); matmap[mid] = str(nextm); nextm += 1

    # copy subtree, renumber nodeIds, remap materialIds, remap UserAttributes
    grp = copy.deepcopy(grp_src)
    grp.set("name", "WW_shop")
    nid = max((int(e.get("nodeId")) for e in root.iter() if (e.get("nodeId") or "").isdigit()), default=0) + 1
    oldnew = {}
    for e in grp.iter():
        old = e.get("nodeId")
        if old is not None:
            oldnew[old] = str(nid); e.set("nodeId", str(nid)); nid += 1
        if e.tag == "Shape" and e.get("materialIds"):
            e.set("materialIds", ",".join(matmap.get(m, m) for m in e.get("materialIds").split(",")))
    ogameplay.append(grp)
    ua_ported = 0
    for old, new in oldnew.items():
        if old in wua:
            u2 = copy.deepcopy(wua[old]); u2.set("nodeId", new); oua.append(u2); ua_ported += 1

    tree.write(OUT_I3D, encoding="utf-8", xml_declaration=True)
    spawns = sum(1 for e in grp.iter() if (e.get("name") or "").startswith("storeSpawnPlace") and e.get("name").endswith("Start"))
    print(f"shop: WW_shop under {SHOP['source_top']} | {spawns} vehicle spawn places + trigger + marker | "
          f"{added} meshes merged | {len(matmap)} mats | {ua_ported} UserAttributes remapped (onCreate scripts)")


if __name__ == "__main__":
    main()
