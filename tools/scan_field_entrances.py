"""
scan_field_entrances.py - STEP 1 (analysis only, NO map edits): decide the best entrance spot for EVERY field.

Per field, three phases:
  Phase 1 - SIDES: meadow depth on each field side = distance from the PLOWABLE field edge (ww_fields polygon)
            outward to the PROPERTY boundary (farmland parcel, id == field number). The 2 deepest sides are the
            machinery turn-around headlands -> the entrance sides.
  Phase 2 - EXCLUDE: along those sides, drop any stretch with a building (WW_buildings) or steep terrain (slope).
  Phase 3 - PICK: on what's left, choose a spot that threads the streetlight gap (road crossing farthest from any
            pole), is road-reachable, clear of buildings, good slope.

HARD RULE: every field must get >=1 entrance. Fields that can't are reported with the reason (fail-loud) - it does
NOT silently skip them. Writes the chosen plan to out/.../field_entrance_plan.json + an overview PNG; prints a
pass/fail summary. The BUILD step (build_field_entrances) will consume the plan. Map-agnostic; config under
"field_entrances".
"""
import os, sys, json
import numpy as np
from PIL import Image, ImageDraw
import xml.etree.ElementTree as ET
from scipy.ndimage import distance_transform_edt, binary_dilation
from scipy.spatial import cKDTree

WW = os.environ.get("FS_CONVERT_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import convert_env, ww_fields, grle_codec

CONV = json.load(open(os.path.join(WW, "tools", os.environ.get("MAP_CONVERT", "wildwest.convert.json")), encoding="utf-8"))
OUT = os.path.join(WW, "out", CONV["identity"]["mod"])
I3D = os.path.join(OUT, "maps", CONV["identity"]["i3d"])
DATA = os.path.join(OUT, "maps", "data")
FS22 = os.path.join(convert_env.source_dir(CONV), CONV["source"]["map_i3d"])
MAP = float(CONV.get("cfg", {}).get("map_m", 8192))

_FE = CONV.get("field_entrances", {})
FLAT_DEG = float(_FE.get("flat_deg", 6.0))
MAX_PER_FIELD = int(_FE.get("max_per_field", 2))
MIN_SEP = float(_FE.get("min_entrance_sep_m", 60.0))
BUILD_CLEAR = float(_FE.get("building_clear_m", 15.0))     # entrance must be at least this from any building shape
ROAD_REACH = float(_FE.get("road_reach_m", 70.0))          # a valid spot must have a road within this (else no access)
MEADOW_MAX = float(_FE.get("meadow_probe_max_m", 100.0))   # cap the outward meadow ray-cast
ROAD_DILATE = float(_FE.get("road_dilate_m", 5.0))
# LOCK: a committed file (OUTSIDE out/, so convert.py's rmtree can't wipe it) that PINS approved entrances. Any field
# present in the lock is used verbatim and NOT re-derived - it stays put across every rebuild, even if terrain/roads/
# lights drift and would otherwise flip an argmax. Freeze the current plan with:  scan_field_entrances.py --lock
LOCK_FILE = _FE.get("lock_file", "field_entrances.lock.json")
LOCK_PATH = LOCK_FILE if os.path.isabs(LOCK_FILE) else os.path.join(WW, LOCK_FILE)


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


def _group_shape_xz(root, name):
    g = next((x for x in root.find("Scene") if x.get("name") == name), None)
    pts = []
    if g is None:
        return np.zeros((0, 2))

    def walk(n, M0):
        M = M0 @ _trs(n)
        if n.tag == "Shape":
            pts.append((M[0, 3], M[2, 3]))
        for ch in n:
            if ch.tag in ("TransformGroup", "Shape"):
                walk(ch, M)
    walk(g, np.eye(4))
    return np.array(pts) if pts else np.zeros((0, 2))


def _ref_xz(root, name):
    g = next((x for x in root.find("Scene") if x.get("name") == name), None)
    if g is None:
        return np.zeros((0, 2))
    out = []
    for rn in g:
        if rn.tag == "ReferenceNode":
            t = (rn.get("translation") or "0 0 0").split()
            out.append((float(t[0]), float(t[2])))
    return np.array(out) if out else np.zeros((0, 2))


def main():
    root = ET.parse(I3D).getroot()
    # parcel raster (property boundaries): arr[gz,gx] == field number
    arr, _ = grle_codec.decode(open(os.path.join(DATA, "infoLayer_farmland.grle"), "rb").read())
    G = arr.shape[0]
    # DEM slope
    dem = np.asarray(Image.open(os.path.join(DATA, "map_dem.png"))).astype(np.float64)
    H = dem.shape[0]; mpp = MAP / (H - 1)
    hs = float(next(root.iter("TerrainTransformGroup")).get("heightScale"))
    elev = dem / 65535.0 * hs
    gz, gx = np.gradient(elev, mpp); slope = np.degrees(np.arctan(np.hypot(gx, gz)))
    # road corridor distance
    rpts = _group_shape_xz(root, "WW_roads")
    rm = np.zeros((H, H), bool)
    rpx = np.clip(((rpts[:, 0] + MAP / 2) / MAP * (H - 1)).round().astype(int), 0, H - 1)
    rpz = np.clip(((rpts[:, 1] + MAP / 2) / MAP * (H - 1)).round().astype(int), 0, H - 1)
    rm[rpz, rpx] = True
    rm = binary_dilation(rm, iterations=max(1, int(round(ROAD_DILATE / mpp))))
    roaddist = distance_transform_edt(~rm) * mpp
    # buildings + lights
    bxz = _group_shape_xz(root, "WW_buildings")
    btree = cKDTree(bxz) if len(bxz) else None
    lxz = _ref_xz(root, "WW_lights")
    ltree = cKDTree(lxz) if len(lxz) else None

    def dempix(x, z):
        return (int(np.clip(round((x + MAP / 2) / MAP * (H - 1)), 0, H - 1)),
                int(np.clip(round((z + MAP / 2) / MAP * (H - 1)), 0, H - 1)))

    def in_parcel(x, z, num):
        px = int((x + MAP / 2) / MAP * (G - 1)); pz = int((z + MAP / 2) / MAP * (G - 1))
        return 0 <= px < G and 0 <= pz < G and arr[pz, px] == num

    def meadow_depth(p, n, num):
        d = 0.0
        while d < MEADOW_MAX:
            d += 2.0
            if not in_parcel(p[0] + n[0] * d, p[1] + n[1] * d, num):
                return d - 2.0
        return MEADOW_MAX

    def slope_at(x, z):
        c, r = dempix(x, z); return slope[r, c]

    def road_at(x, z):
        c, r = dempix(x, z); return roaddist[r, c]

    def bdist(x, z):
        return float(btree.query([x, z])[0]) if btree is not None else 1e9

    def lgap(x, z):
        return float(ltree.query([x, z])[0]) if ltree is not None else 1e9

    # --lock (freeze): derive everything fresh and write the result to the lock file, IGNORING any existing lock.
    # normal run: honor the existing lock - locked fields are pinned and skip re-derivation.
    relock = "--lock" in sys.argv
    locked = {}
    if not relock and os.path.exists(LOCK_PATH):
        for e in json.load(open(LOCK_PATH, encoding="utf-8")).get("entrances", []):
            locked.setdefault(int(e["field"]), []).append(e)

    fields = ww_fields.read_fs22_fields(FS22)
    plan = []; fails = []; n_pinned = 0
    for f in fields:
        num = f["num"]; poly = np.array(f["polygon"])
        if num in locked:                                         # PINNED: use the frozen entrance(s) verbatim
            plan.extend(locked[num]); n_pinned += 1; continue
        if len(poly) < 3:
            fails.append((num, "degenerate polygon (<3 pts)")); continue
        if (arr == num).sum() == 0:
            fails.append((num, "no property parcel (farmland id absent)")); continue
        cen = poly.mean(0)
        # build sides = polygon edges; per side: sample pts + outward normal + meadow depth
        sides = []
        for i in range(len(poly)):
            a = poly[i]; b = poly[(i + 1) % len(poly)]
            L = float(np.hypot(*(b - a)))
            if L < 4:
                continue
            e = (b - a) / L
            n = np.array([-e[1], e[0]])
            mid = (a + b) / 2
            if np.dot(n, mid - cen) < 0:
                n = -n                                            # ensure outward
            k = max(2, int(L / 4))
            spts = np.array([a + (b - a) * t for t in np.linspace(0.1, 0.9, k)])
            depths = np.array([meadow_depth(sp, n, num) for sp in spts])
            depth = float(np.median(depths))
            # meadow DEPTH = how far the property/meadow apron extends off the plowable edge. The user's rule (locked
            # 2026-07-12): entrances go on the 2 sides with the DEEPEST meadow, PERIOD - NOT edge length, NOT area.
            # (An earlier area = depth x length ranking was WRONG: it put field 11 on its long 26 m-meadow sides
            # instead of the short 74/84 m-meadow ends the user actually wants.)
            sides.append(dict(a=a, b=b, n=n, pts=spts, depth=depth, length=L, area=depth * L))
        if not sides:
            fails.append((num, "no usable field sides")); continue
        sides.sort(key=lambda s: -s["depth"])                     # DEEPEST meadow first (the turn-around apron)

        # walk sides deepest-first; on each, find a valid spot (phase 2 + 3); collect up to MAX_PER_FIELD
        chosen = []; reasons = []
        for si, s in enumerate(sides):
            if len(chosen) >= MAX_PER_FIELD:
                break
            sp = s["pts"]
            sl = np.array([slope_at(x, z) for x, z in sp])
            bd = np.array([bdist(x, z) for x, z in sp])
            rdst = np.array([road_at(x, z) for x, z in sp])
            valid = (sl <= FLAT_DEG) & (bd >= BUILD_CLEAR) & (rdst <= ROAD_REACH)
            if not valid.any():
                if si < MAX_PER_FIELD:
                    why = []
                    if (sl <= FLAT_DEG).sum() == 0: why.append("steep")
                    if (bd >= BUILD_CLEAR).sum() == 0: why.append("buildings")
                    if (rdst <= ROAD_REACH).sum() == 0: why.append("no road in reach")
                    reasons.append(f"side#{si}(area{s['area']:.0f}m2,depth{s['depth']:.0f}m,len{s['length']:.0f}m): " + ",".join(why or ["blocked"]))
                continue
            # the two entrances must be on DISTINCT sides (the user's "two sides with the deepest meadow"), so a
            # jagged edge's segments don't stack two entrances on the same-facing side. Opposite sides are fine
            # (dot ~ -1); reject a side whose outward normal points the SAME way (dot > 0.87 ~ within 30deg) as an
            # already-chosen one - it falls through to the next-deepest differently-facing side.
            if any(float(np.dot(s["n"], c["n"])) > 0.87 for c in chosen):
                continue
            vi = np.where(valid)[0]
            # PHASE 3: among valid pts, pick the one whose spot best threads the streetlight gap
            best = max(vi, key=lambda i: (round(lgap(*sp[i]), 1), s["depth"]))
            E = sp[best]
            if all(np.hypot(*(E - c["E"])) >= MIN_SEP for c in chosen):
                chosen.append(dict(E=E, side=si, depth=s["depth"], slope=float(sl[best]),
                                   road=float(rdst[best]), pole_gap=float(lgap(*E)), n=s["n"]))
        if not chosen:
            reason = "; ".join(reasons[:MAX_PER_FIELD]) or "no valid spot on any side (steep/buildings/no road)"
            fails.append((num, reason)); continue
        for c in chosen:
            plan.append(dict(field=num, x=float(c["E"][0]), z=float(c["E"][1]),
                             nx=float(c["n"][0]), nz=float(c["n"][1]), meadow_depth_m=round(c["depth"], 1),
                             slope_deg=round(c["slope"], 1), road_m=round(c["road"], 1),
                             pole_gap_m=round(c["pole_gap"], 1)))

    # ---- report ----
    npass = len({p["field"] for p in plan}); ntot = len(fields)
    json.dump({"entrances": plan, "fails": fails}, open(os.path.join(OUT, "field_entrance_plan.json"), "w"), indent=1)
    if relock:                                                    # freeze the current plan so it can't drift
        json.dump({"entrances": plan, "fails": fails}, open(LOCK_PATH, "w"), indent=1)
        print(f"[LOCK] froze {len({p['field'] for p in plan})} fields -> {os.path.relpath(LOCK_PATH, WW)}")
    print(f"\n===== FIELD ENTRANCE SCAN =====")
    print(f"fields: {ntot} | PASS (>=1 entrance): {npass} | FAIL: {len(fails)} | total entrances planned: {len(plan)}"
          + (f" | PINNED from lock: {n_pinned}" if n_pinned else ""))
    if fails:
        print("\nFAILED fields (field #: why):")
        for num, why in sorted(fails):
            print(f"  field {num:>3}: {why}")
    # per-pass quick stats
    if plan:
        md = np.array([p["meadow_depth_m"] for p in plan]); pg = np.array([p["pole_gap_m"] for p in plan])
        print(f"\nplanned entrances: meadow-depth p50={np.percentile(md,50):.0f}m (min {md.min():.0f}) | "
              f"pole-gap p50={np.percentile(pg,50):.0f}m (min {pg.min():.0f})")
    print(f"\nplan written -> {os.path.relpath(os.path.join(OUT,'field_entrance_plan.json'), WW)}")

    # ---- overview PNG ----
    S = 2048; sc = S / MAP
    def w2i(x, z): return ((x + MAP / 2) * sc, (z + MAP / 2) * sc)
    img = Image.new("RGB", (S, S), (22, 24, 20)); d = ImageDraw.Draw(img)
    # parcels faint
    pc = (arr > 0) & (arr <= 82)
    ys, xs = np.where(pc[::4, ::4])
    for y, x in zip(ys, xs):
        d.point(((x * 4) / (G - 1) * S, (y * 4) / (G - 1) * S), fill=(38, 40, 34))
    for f in fields:                                              # plowable field outline
        pl = [w2i(x, z) for x, z in f["polygon"]]
        if len(pl) > 2: d.line(pl + [pl[0]], fill=(70, 120, 60), width=1)
    fail_nums = {n for n, _ in fails}
    for f in fields:                                              # mark failed fields (red ring at centroid)
        if f["num"] in fail_nums:
            cx, cz = w2i(*np.array(f["polygon"]).mean(0))
            d.ellipse([cx - 6, cz - 6, cx + 6, cz + 6], outline=(255, 60, 60), width=2)
    for p in plan:                                                # entrances
        ix, iz = w2i(p["x"], p["z"]); d.ellipse([ix - 3, iz - 3, ix + 3, iz + 3], fill=(255, 200, 60))
    img.save(os.path.join(OUT, "field_entrance_scan.png"))
    print(f"overview     -> {os.path.relpath(os.path.join(OUT,'field_entrance_scan.png'), WW)}")


if __name__ == "__main__":
    main()
