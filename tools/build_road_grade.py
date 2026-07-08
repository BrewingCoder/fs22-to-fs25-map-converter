"""
build_road_grade.py - PHASE 2c: grade the terrain under the road corridors so the flat road meshes sit flush.
Sloped ground was poking up through the flat asphalt (buried highway/onramps). We compose the WORLD transform of
every road-surface shape under WW_roads (nested up to 9 deep; XYZ Euler - validated: median road_Y==terrain_Y),
rasterize the road footprint, and CARVE the DEM down to each road's surface height wherever the ground sits above
it, with a smooth shoulder so there are no cliffs. Only LOWERS (never raises -> no floating roads over valleys).
Idempotent (carve = terrain - min(above-road)). Run AFTER build_terrain + build_flats. Overwrites OUT's map_dem.png.
"""
import os, sys, json
import numpy as np
import xml.etree.ElementTree as ET
from PIL import Image
from scipy.ndimage import distance_transform_edt

WW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import convert_env
CONV = json.load(open(os.path.join(WW, "tools", os.environ.get("MAP_CONVERT", "wildwest.convert.json")), encoding="utf-8"))
FS22 = convert_env.source_dir(CONV)
ROADS_GRP = tuple(CONV.get("scene_groups", {}).get("roads", ["roads"]))   # the ground-road subgroup(s) to grade under
OUT = os.path.join(WW, "out", CONV["identity"]["mod"])
I3D = os.path.join(OUT, "maps", CONV["identity"]["i3d"])
SRC_DEM = os.path.join(FS22, CONV["source"]["dem"])              # pristine WW DEM (idempotent read)
DEM = os.path.join(OUT, "maps", "data", "map_dem.png")           # our deployed DEM (write target)
MAP = float(CONV.get("cfg", {}).get("map_m", 8192))
CORE_M, SHOULDER_M = 4.0, 11.0                                    # full carve within 4 m, blend to 0 by 11 m
SKIP_M = 5.0                                                     # burial deeper than this = underpass -> don't carve (bimodal: shallow <2m vs deep >6m, nothing between)


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


def collect(node, M0, pts):
    M = M0 @ trs(node)
    if node.tag == "Shape":
        pts.append(M[:3, 3])
    for ch in node:
        if ch.tag in ("TransformGroup", "Shape"):
            collect(ch, M, pts)


def main():
    root = ET.parse(I3D).getroot()
    hs = float(next(root.iter("TerrainTransformGroup")).get("heightScale"))
    dem = np.asarray(Image.open(SRC_DEM)).astype(np.float64)
    H = dem.shape[0]
    mpp = MAP / (H - 1)                                           # metres per pixel
    heightH = dem / 65535.0 * hs

    wwroads = next((g for g in root.find("Scene") if g.get("name") == "WW_roads"), None)
    roads = next((g for g in wwroads if g.get("name") in ROADS_GRP), None) if wwroads is not None else None
    if roads is None:                                               # map has no extracted ground-road group -> nothing to grade
        print("[road_grade] no ground-road group found (config scene_groups.roads unmatched) - skipping")
        return
    pts = []
    collect(roads, trs(wwroads), pts)
    p = np.array(pts)
    px = np.clip(((p[:, 0] + MAP / 2) / MAP * (H - 1)).round().astype(int), 0, H - 1)
    pz = np.clip(((p[:, 2] + MAP / 2) / MAP * (H - 1)).round().astype(int), 0, H - 1)

    # rasterize road surface height onto the grid (avg where multiple shapes land on a cell)
    sumY = np.zeros((H, H)); cnt = np.zeros((H, H))
    np.add.at(sumY, (pz, px), p[:, 1]); np.add.at(cnt, (pz, px), 1)
    road_cell = cnt > 0
    roadY = np.where(road_cell, sumY / np.maximum(cnt, 1), 0.0)

    # nearest road height everywhere + distance (metres) to the corridor centre
    dist, (iz, ix) = distance_transform_edt(~road_cell, return_indices=True)
    nearestY = roadY[iz, ix]
    dist_m = dist * mpp
    w = np.clip((SHOULDER_M - dist_m) / (SHOULDER_M - CORE_M), 0.0, 1.0)   # 1 in core -> 0 past shoulder

    above = np.maximum(0.0, heightH - nearestY)                  # how far ground pokes above the road
    rb = above[road_cell]
    before_buried = (rb > 0.02).mean() * 100
    print(f"  burial @ road cells: p50={np.percentile(rb,50):.2f} p90={np.percentile(rb,90):.2f} "
          f"p99={np.percentile(rb,99):.2f} max={rb.max():.2f}m | "
          f">2m={100*(rb>2).mean():.1f}% >4m={100*(rb>4).mean():.1f}% >6m={100*(rb>6).mean():.1f}%")
    above_c = np.where(above <= SKIP_M, above, 0.0)              # carve shallow hill burials; leave underpasses
    newH = heightH - w * above_c                                 # lower toward road, weighted; never raises
    carved = heightH - newH
    within = w > 0
    after_buried = (np.maximum(0.0, newH - nearestY)[road_cell] > 0.02).mean() * 100

    newdem = np.clip(newH / hs * 65535.0, 0, 65535).round().astype(np.uint16)
    Image.fromarray(newdem).save(DEM)
    print(f"road-grade: {len(p)} road-surface pts | corridor {within.sum()} px ({100*within.mean():.1f}% of map)")
    print(f"  carve depth: max={carved.max():.2f}m mean(where carved)={carved[carved>0.01].mean():.2f}m")
    print(f"  road cells buried >2cm: {before_buried:.0f}% -> {after_buried:.0f}%  (DEM written)")


if __name__ == "__main__":
    main()
