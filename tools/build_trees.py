"""
build_trees.py - PHASE 3: replace WW's FS22 trees with base-game FS25 trees (seasonal) at WW's positions.
WW's tree texture refs ($data/maps/trees/birch|pine|spruce) are BROKEN in FS25 (renamed to betulaErmanii/
pinusSylvestris/...) + the FS22 meshes lack seasons. So we extract WW's ~75k tree TRUNK positions + type
(deciduous vs conifer) and place base-game FS25 tree ReferenceNodes.
USER RULE (2026-07-06, refined): the original author placed town/roadside trees via SPLINES = single-file lines;
big conifers in those lines stand out badly. Forest STANDS (dense 2D clusters) are where conifers belong. So we
classify each tree's local neighbourhood by LINEARITY (PCA of neighbours within 18 m): a conifer in a single-file
line (or standing alone) -> deciduous; a conifer in a stand -> stays conifer. Deciduous always stays deciduous.
This replaces the old near-road test (no road-subtree dependency -> map-agnostic). Randomize deciduous types.
FS25 trees = $data/maps/trees/<species>/<species>_stage##.i3d, placed via <ReferenceNode referenceId=File>.
Idempotent (drops prior WW_trees). Needs scipy.
"""
import os, sys, json
import numpy as np
import xml.etree.ElementTree as ET
from PIL import Image
from scipy.spatial import cKDTree

WW = os.environ.get("FS_CONVERT_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import convert_env
CONV = json.load(open(os.path.join(WW, "tools", os.environ.get("MAP_CONVERT", "wildwest.convert.json")), encoding="utf-8"))
FS22_I3D = os.path.join(convert_env.source_dir(CONV), CONV["source"]["map_i3d"])
OUT = os.path.join(WW, "out", CONV["identity"]["mod"])
OUT_I3D = os.path.join(OUT, "maps", CONV["identity"]["i3d"])
DEM = os.path.join(OUT, "maps", "data", "map_dem.png")
FS25_TREES = os.path.join(os.environ.get("FS25_DATA", r"C:\Program Files (x86)\Steam\steamapps\common\Farming Simulator 25\data"), "maps", "trees")
MAP = float(CONV.get("cfg", {}).get("map_m", 8192))
# MAP-SPECIFIC tree palette + scene groups from convert.json (config-driven) - defaults = WW's:
_SG = CONV.get("scene_groups", {})
_TP = CONV.get("tree_palette", {})
TOP = tuple(_SG.get("top", ["WildWest", "WildWest2"]))
TREE_GROUPS = tuple(_SG.get("trees", ["trees"]))
LIN_R = float(_TP.get("linearity_radius_m", 18.0))     # radius (m) for the local-neighbourhood shape test
LIN_TH = float(_TP.get("linearity_threshold", 0.70))   # linearity > this = single-file spline row (stands ~0.36, lines ~0.9)
DECID = _TP.get("deciduous", ["americanElm", "oak", "aspen", "beech", "betulaErmanii", "boxelder", "northernCatalpa", "shagbarkHickory"])
CONIF = _TP.get("conifer", ["pinusSylvestris", "lodgepolePine", "pinusTabuliformis"])
STAGES = _TP.get("stages", ["stage03", "stage04", "stage05"])
SCALE_LO, SCALE_HI = 0.80, 1.25                        # per-tree size jitter (GT inject_forest: kills the clone look)
TILT = 5.0                                             # per-tree lean deg (GT: trees aren't perfectly plumb)
CONIF_KW = tuple(_TP.get("conifer_keywords", ["pine", "spruce", "stonepine", "fir", "cedar"]))   # WW trunk-material name -> conifer


def trs(node):
    t = [float(x) for x in (node.get("translation") or "0 0 0").split()]
    r = [np.radians(float(x)) for x in (node.get("rotation") or "0 0 0").split()]
    s = [float(x) for x in (node.get("scale") or "1 1 1").split()]
    cx, cy, cz = np.cos(r); sx, sy, sz = np.sin(r)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    M = np.eye(4); M[:3, :3] = (Rx @ Ry @ Rz) * np.array(s); M[:3, 3] = t
    return M


def valid_stages(sp):
    d = os.path.join(FS25_TREES, sp)
    if not os.path.isdir(d):
        return []
    return [st for st in STAGES if os.path.exists(os.path.join(d, f"{sp}_{st}.i3d"))]


def main():
    DEC = {sp: valid_stages(sp) for sp in DECID if valid_stages(sp)}
    CON = {sp: valid_stages(sp) for sp in CONIF if valid_stages(sp)}
    print(f"FS25 species available: deciduous {list(DEC)}, conifer {list(CON)}")

    # 1. WW tree trunk positions + type (deciduous/conifer) from the trunk material
    wr = ET.parse(FS22_I3D).getroot()
    mats = {m.get("materialId"): (m.get("name") or "").lower() for m in wr.iter("Material")}
    tgs = [g for top in wr.find("Scene") if top.get("name") in TOP for g in top if g.get("name") in TREE_GROUPS]
    pts, types = [], []

    def walk(n, M0, acc):
        M = M0 @ trs(n)
        if n.tag == "Shape":
            for mid in (n.get("materialIds") or "").split(","):
                nm = mats.get(mid, "")
                if "trunk" in nm:
                    acc[0].append(M[:3, 3])
                    acc[1].append("conif" if any(c in nm for c in CONIF_KW) else "decid")
                    break
        for c in n:
            if c.tag in ("TransformGroup", "Shape"):
                walk(c, M, acc)
    for tg in tgs:                                          # walk ALL matching tree groups (WW: 'trees'; other maps may have several)
        walk(tg, np.eye(4), (pts, types))
    P = np.array(pts); T = np.array(types)
    if len(pts) == 0:                                             # no trees matched (group-based; some maps' tree
        print("trees: 0 found (no matching tree groups for this map) - skipped")   # groups differ) - skip, don't crash
        return

    # 2. classify each tree's neighbourhood: single-file line (spline) vs 2D stand, via PCA linearity
    xz = P[:, [0, 2]]; tk = cKDTree(xz)
    nb = tk.query_ball_point(xz, LIN_R)
    spline = np.zeros(len(P), bool)
    for i, idx in enumerate(nb):
        idx = [j for j in idx if j != i]
        if len(idx) < 2:                               # isolated / lone tree -> treat like a spline (no lone big conifer)
            spline[i] = True; continue
        off = xz[idx] - xz[i]; c = np.cov(off.T)
        w = np.linalg.eigvalsh(c); l2, l1 = max(w[0], 1e-9), max(w[1], 1e-9)
        if (l1 - l2) / (l1 + l2) > LIN_TH:             # neighbours collinear -> single-file spline row
            spline[i] = True

    # 3. snap Y to our terrain DEM (tree base on the ground)
    root = ET.parse(OUT_I3D).getroot()
    dem = np.asarray(Image.open(DEM)).astype(np.float64); H = dem.shape[0]
    hs = float(next(root.iter("TerrainTransformGroup")).get("heightScale"))
    px = np.clip(((P[:, 0] + MAP / 2) / MAP * (H - 1)).round().astype(int), 0, H - 1)
    pz = np.clip(((P[:, 2] + MAP / 2) / MAP * (H - 1)).round().astype(int), 0, H - 1)
    Y = dem[pz, px] / 65535.0 * hs

    # 4. place FS25 tree ReferenceNodes
    files_el = root.find("Files"); oscene = root.find("Scene")
    for g in list(oscene):
        if g.tag == "TransformGroup" and g.get("name") == "WW_trees":
            oscene.remove(g)
    nextf = max(int(f.get("fileId")) for f in files_el) + 1
    nid = max((int(e.get("nodeId")) for e in root.iter() if (e.get("nodeId") or "").isdigit()), default=0) + 1
    grp = ET.SubElement(oscene, "TransformGroup", {"name": "WW_trees", "clipDistance": "2500", "nodeId": str(nid)}); nid += 1
    fmap = {}
    rng = np.random.default_rng(3)
    dec_names, dec_st = list(DEC), DEC
    con_names, con_st = list(CON), CON
    ndec = ncon = flipped = 0
    for i in range(len(P)):
        if T[i] == "decid" or spline[i]:               # deciduous originals + single-file/lone (any type) -> deciduous
            if T[i] == "conif":
                flipped += 1
            sp = dec_names[rng.integers(len(dec_names))]; st = dec_st[sp][rng.integers(len(dec_st[sp]))]; ndec += 1
        else:                                          # conifer in a stand -> conifer
            sp = con_names[rng.integers(len(con_names))]; st = con_st[sp][rng.integers(len(con_st[sp]))]; ncon += 1
        key = (sp, st)
        if key not in fmap:
            ET.SubElement(files_el, "File", {"fileId": str(nextf), "filename": f"$data/maps/trees/{sp}/{sp}_{st}.i3d"})
            fmap[key] = str(nextf); nextf += 1
        yaw = rng.uniform(0, 360); rx = rng.uniform(-TILT, TILT); rz = rng.uniform(-TILT, TILT)
        sc = rng.uniform(SCALE_LO, SCALE_HI)
        ET.SubElement(grp, "ReferenceNode", {"name": f"{sp}_{st}", "referenceId": fmap[key],
            "translation": f"{P[i,0]:.3f} {Y[i]:.3f} {P[i,2]:.3f}", "rotation": f"{rx:.1f} {yaw:.1f} {rz:.1f}",
            "scale": f"{sc:.3f} {sc:.3f} {sc:.3f}", "nodeId": str(nid)})
        nid += 1

    ET.ElementTree(root).write(OUT_I3D, encoding="utf-8", xml_declaration=True)
    print(f"trees: {len(P)} placed | {ndec} deciduous + {ncon} conifer (stands) | "
          f"{flipped} single-file conifers -> deciduous | {len(fmap)} FS25 tree i3d refs")


if __name__ == "__main__":
    main()
