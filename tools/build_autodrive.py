"""
build_autodrive.py - generate a TWO-LANE, US right-side-driving AutoDrive route network for the converted map and
install it into a savegame's AutoDrive_config.xml.

Approach (self-contained, uses OUR map data - guaranteed aligned):
  1. ROAD MASK  = union of the painted road ground layers (asphalt + gravel + gravelPebblesMoss + concrete weights).
  2. CENTERLINES= skeletonize the mask -> 1px medial lines -> trace into a graph (nodes = junctions/ends, edges =
     polylines between them).
  3. TWO LANES  = for each centerline edge, emit TWO one-way lanes offset +-lane_offset from centre. Each lane is
     offset to the RIGHT of ITS OWN travel direction (US right-hand traffic): forward lane A->B on one side,
     reverse lane B->A on the other. Sequential one-way links along each lane.
  4. JUNCTIONS  = at every graph node, connect each ARRIVING lane end to each DEPARTING lane start nearby, so the AI
     can turn (right-hand offset preserved along the segments).
  5. HEIGHT     = sample Y from the map DEM (roads are graded flush, so DEM ~ road surface).
  6. INSTALL    = write the <waypoints> block into savegame<N>/AutoDrive_config.xml (settings preserved). Load the
     save FRESH afterwards (AutoDrive reads on load, rewrites on save).

Usage: python tools/build_autodrive.py [savegameN]      (default target from config autodrive.savegame or savegame2)
Config block "autodrive" in <map>.convert.json (all optional, sane defaults here).
"""
import os, sys, json, math
import numpy as np
from PIL import Image
import xml.etree.ElementTree as ET
from scipy.ndimage import binary_closing, label as cc_label, convolve, distance_transform_edt
from skimage.morphology import skeletonize

WW = os.environ.get("FS_CONVERT_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
CONV = json.load(open(os.path.join(WW, "tools", os.environ.get("MAP_CONVERT", "wildwest.convert.json")), encoding="utf-8"))
OUT = os.path.join(WW, "out", CONV["identity"]["mod"])
I3D = os.path.join(OUT, "maps", CONV["identity"]["i3d"])
DATA = os.path.join(OUT, "maps", "data")
MAP = float(CONV.get("cfg", {}).get("map_m", 8192))

_AD = CONV.get("autodrive", {})
ROAD_LAYERS = _AD.get("road_layers", ["asphalt", "gravelPebblesMoss", "gravel", "concrete"])
THRESH = int(_AD.get("mask_threshold", 40))
WORK = int(_AD.get("work_res", 4096))               # skeleton grid (2 m/px at 16x)
CLOSE_ITER = int(_AD.get("close_iters", 2))         # fill small gaps so the mask is continuous
MIN_CC = int(_AD.get("min_component_px", 40))       # drop skeleton specks smaller than this
RESAMPLE_M = float(_AD.get("waypoint_spacing_m", 5.0))
LANE_OFF = float(_AD.get("lane_offset_m", 2.0))     # fallback half-separation if width unknown
LANE_EDGE_FRAC = float(_AD.get("lane_edge_frac", 0.33))  # each lane sits this fraction of road WIDTH in from its edge
LANE_MIN_OFF = float(_AD.get("lane_min_offset_m", 1.2))  # floor so narrow roads still separate the two lanes
LANE_MAX_OFF = float(_AD.get("lane_max_offset_m", 2.4))  # cap: skeleton dt explodes at plazas/junctions - keep lanes on-road
RIGHT_SIGN = float(_AD.get("right_hand_sign", 1.0)) # flip to -1 if lanes end up on the wrong side
MIN_SPUR = float(_AD.get("min_spur_m", 14.0))       # prune dead-end skeleton spurs shorter than this (road-width artifacts)
MIN_EDGE = float(_AD.get("min_edge_m", 4.0))        # drop centerline segments shorter than this
ORIENT = _AD.get("orientation", "all")              # "ew" (east-west only), "ns" (north-south only), or "all" - baby-step subsets
MODMAP = os.environ.get("FS25_MODS", os.path.join(os.path.expanduser("~"), "Documents", "My Games", "FarmingSimulator2025"))
SAVEDIR = os.path.join(MODMAP, "savegames") if os.path.isdir(os.path.join(MODMAP, "savegames")) else MODMAP


def road_mask():
    m = None
    for lay in ROAD_LAYERS:
        p = os.path.join(DATA, f"{lay}_weight.png")
        if not os.path.exists(p):
            continue
        a = np.asarray(Image.open(p).convert("L")) > THRESH
        m = a if m is None else (m | a)
    if m is None:
        raise SystemExit("no road weight layers found")
    # to working resolution
    if m.shape[0] != WORK:
        im = Image.fromarray(m.astype(np.uint8) * 255).resize((WORK, WORK), Image.NEAREST)
        m = np.asarray(im) > 127
    m = binary_closing(m, iterations=CLOSE_ITER)
    return m


def trace_graph(skel):
    """skeleton bool grid -> (nodes list of (r,c), edges list of pixel-path arrays)."""
    k = np.array([[1, 1, 1], [1, 0, 1], [1, 1, 1]])
    deg = convolve(skel.astype(np.uint8), k, mode="constant")
    deg[~skel] = 0
    node_mask = skel & ((deg == 1) | (deg >= 3))
    nodes = list(map(tuple, np.argwhere(node_mask)))
    node_set = set(nodes)
    H = skel.shape[0]

    def nbrs(r, c):
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0:
                    continue
                rr, cc = r + dr, c + dc
                if 0 <= rr < H and 0 <= cc < H and skel[rr, cc]:
                    yield rr, cc

    edges = []
    used = set()   # undirected pixel steps already walked
    for n in nodes:
        for nb in nbrs(*n):
            if (n, nb) in used:
                continue
            path = [n, nb]; used.add((n, nb)); used.add((nb, n))
            prev, cur = n, nb
            while cur not in node_set:
                nxt = [p for p in nbrs(*cur) if p != prev]
                if not nxt:
                    break
                prev, cur = cur, nxt[0]
                path.append(cur); used.add((path[-2], cur)); used.add((cur, path[-2]))
            edges.append(np.array(path, float))
    # also closed loops with no node: seed from any unused skel pixel of degree 2
    loop_seeds = np.argwhere(skel & (deg == 2))
    for r, c in loop_seeds:
        start = (int(r), int(c))
        moved = [nb for nb in nbrs(*start) if (start, nb) not in used]
        if not moved:
            continue
        nb = moved[0]; path = [start, nb]; used.add((start, nb)); used.add((nb, start))
        prev, cur = start, nb
        while cur != start:
            nxt = [p for p in nbrs(*cur) if p != prev and (cur, p) not in used]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
            path.append(cur); used.add((path[-2], cur)); used.add((cur, path[-2]))
        if len(path) > 3:
            edges.append(np.array(path, float))
    return nodes, edges


def resample(poly_w, step):
    """poly_w (M,2) world -> resampled ~step m, keeping ends."""
    if len(poly_w) < 2:
        return poly_w
    seg = np.linalg.norm(np.diff(poly_w, axis=0), axis=1)
    s = np.concatenate([[0], np.cumsum(seg)]); total = s[-1]
    if total < step:
        return np.array([poly_w[0], poly_w[-1]])
    n = max(1, int(round(total / step)))
    targets = np.linspace(0, total, n + 1)
    out = np.empty((len(targets), 2))
    for i, t in enumerate(targets):
        j = np.searchsorted(s, t) - 1; j = min(max(j, 0), len(seg) - 1)
        f = (t - s[j]) / seg[j] if seg[j] > 1e-6 else 0.0
        out[i] = poly_w[j] + (poly_w[j + 1] - poly_w[j]) * f
    return out


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else _AD.get("savegame", "savegame2")
    cfg_path = os.path.join(SAVEDIR, target, "AutoDrive_config.xml")
    if not os.path.exists(cfg_path):
        raise SystemExit(f"savegame AutoDrive_config not found: {cfg_path}")

    # ---- centerlines
    m = road_mask()
    # distance-to-edge (px) at every road pixel; on the centerline this ~= the road's half-width.
    dt = distance_transform_edt(m)
    skel = skeletonize(m)
    lbl, nlab = cc_label(skel)
    if MIN_CC > 0 and nlab > 1:
        sizes = np.bincount(lbl.ravel())
        keep = np.isin(lbl, np.where(sizes >= MIN_CC)[0]) & skel
        keep[lbl == 0] = False
        skel = keep
    nodes, edges = trace_graph(skel)
    ppm = MAP / WORK                                  # metres per working pixel
    def px2w(rc):                                     # (row,col)->(worldX,worldZ)
        r, c = rc[..., 0], rc[..., 1]
        return np.stack([c * ppm - MAP / 2, r * ppm - MAP / 2], axis=-1)

    def halfwidth_m(pw):                               # world pts (M,2)=(x,z) -> local road half-width in metres
        col = np.clip(((pw[:, 0] + MAP / 2) / ppm).astype(int), 0, WORK - 1)
        row = np.clip(((pw[:, 1] + MAP / 2) / ppm).astype(int), 0, WORK - 1)
        return dt[row, col] * ppm

    # prune skeleton artifacts: dead-end SPURS shorter than MIN_SPUR (stubs off wide paved areas) + tiny fragments
    def edge_len_w(e):
        return float(np.linalg.norm(np.diff(px2w(e), axis=0), axis=1).sum())
    deg = {}
    for e in edges:
        for nd in (tuple(e[0]), tuple(e[-1])):
            deg[nd] = deg.get(nd, 0) + 1
    nprune = 0; kept = []
    for e in edges:
        L = edge_len_w(e)
        spur = deg.get(tuple(e[0]), 0) == 1 or deg.get(tuple(e[-1]), 0) == 1
        if L < MIN_EDGE or (spur and L < MIN_SPUR):
            nprune += 1; continue
        kept.append(e)
    edges = kept
    # keep only nodes still touched by a surviving edge
    live = set()
    for e in edges:
        live.add(tuple(e[0])); live.add(tuple(e[-1]))
    nodes = [n for n in nodes if n in live]

    # ---- DEM height
    dem = np.asarray(Image.open(os.path.join(DATA, "map_dem.png"))).astype(np.float64)
    Hd = dem.shape[0]
    hs = float(next(ET.parse(I3D).getroot().iter("TerrainTransformGroup")).get("heightScale"))
    def ground(x, z):
        gx = np.clip(((x + MAP / 2) / MAP * (Hd - 1)).astype(int), 0, Hd - 1)
        gz = np.clip(((z + MAP / 2) / MAP * (Hd - 1)).astype(int), 0, Hd - 1)
        return dem[gz, gx] / 65535.0 * hs

    # ---- build waypoints (two lanes per edge)
    X, Y, Z = [], [], []
    outc, inc = [], []                                # per-waypoint target lists (0-based, converted to 1-based on emit)
    node_arms = {}                                    # node index -> list of {"arr":wp, "dep":wp}, one per road arm (edge)
    nidx = {n: i for i, n in enumerate(nodes)}

    def add_wp(x, z):
        X.append(float(x)); Z.append(float(z)); Y.append(float(ground(np.array(x), np.array(z))))
        outc.append([]); inc.append([]); return len(X) - 1

    def link(a, b):
        outc[a].append(b); inc[b].append(a)

    n_orient_skip = 0
    for e in edges:
        pw = px2w(e)
        pw = resample(pw, RESAMPLE_M)
        if len(pw) < 2:
            continue
        if ORIENT in ("ew", "ns"):                          # baby-step: keep only edges running predominantly E-W or N-S
            dif = np.diff(pw, axis=0)
            edge_is = "ew" if np.abs(dif[:, 0]).sum() >= np.abs(dif[:, 1]).sum() else "ns"
            if edge_is != ORIENT:
                n_orient_skip += 1; continue
        tang = np.gradient(pw, axis=0)
        tn = np.linalg.norm(tang, axis=1, keepdims=True); tang = np.divide(tang, tn, out=np.zeros_like(tang), where=tn > 1e-9)
        rightn = np.stack([tang[:, 1], -tang[:, 0]], axis=1) * RIGHT_SIGN   # right of travel (flip via RIGHT_SIGN)
        # per-point offset: lane sits LANE_EDGE_FRAC of the WIDTH in from the edge, i.e. bisects between the
        # centre decal and the outer edge. offset-from-centre = halfwidth*(1 - 2*frac). ~0.34*hw at frac=0.33.
        hw = halfwidth_m(pw)
        off = np.clip(hw * (1.0 - 2.0 * LANE_EDGE_FRAC), LANE_MIN_OFF, LANE_MAX_OFF)
        # smooth the offset a little so it doesn't jitter with skeleton/dt noise
        if len(off) >= 3:
            off = np.convolve(off, np.ones(3) / 3.0, mode="same")
            off = np.clip(off, LANE_MIN_OFF, LANE_MAX_OFF)
        off = off[:, None]
        fwd = pw + rightn * off
        rev = pw - rightn * off
        # forward lane: along pw (start=edge node a, end=node b)
        f_wp = [add_wp(*p) for p in fwd]
        for i in range(len(f_wp) - 1):
            link(f_wp[i], f_wp[i + 1])
        # reverse lane: opposite direction (start at b, end at a)
        r_wp = [add_wp(*p) for p in rev[::-1]]
        for i in range(len(r_wp) - 1):
            link(r_wp[i], r_wp[i + 1])
        na, nb = tuple(e[0]), tuple(e[-1])
        if na in nidx:                                     # at node a: reverse lane ARRIVES, forward lane DEPARTS
            node_arms.setdefault(nidx[na], []).append({"arr": r_wp[-1], "dep": f_wp[0]})
        if nb in nidx:                                     # at node b: forward lane ARRIVES, reverse lane DEPARTS
            node_arms.setdefault(nidx[nb], []).append({"arr": f_wp[-1], "dep": r_wp[0]})

    # ---- junction connections: ARM-to-ARM. Each arriving lane of arm i connects to the DEPARTING lane of every
    #      OTHER arm j (i != j) - a clean turn set, NOT a point-cloud web. NEVER connect an arm to itself: that would
    #      join a road's two parallel lanes (a U-turn across the centreline), which the user explicitly forbids.
    #      A dead end (single arm) therefore gets NO junction link at all this pass.
    njc = 0
    for ni, arms in node_arms.items():
        if len(arms) < 2:
            continue
        for i, ai in enumerate(arms):
            for j, aj in enumerate(arms):
                if i != j and aj["dep"] not in outc[ai["arr"]]:
                    link(ai["arr"], aj["dep"]); njc += 1

    N = len(X)
    if N == 0:
        raise SystemExit("no waypoints generated (empty road mask?)")

    # ---- emit AD xml block (1-based ids; ';' separates waypoints, ',' separates targets)
    ids = ",".join(str(i + 1) for i in range(N))
    xs = ",".join(f"{v:.3f}" for v in X)
    ys = ",".join(f"{v:.3f}" for v in Y)
    zs = ",".join(f"{v:.3f}" for v in Z)
    outs = ";".join(",".join(str(t + 1) for t in outc[i]) for i in range(N))
    ins = ";".join(",".join(str(t + 1) for t in inc[i]) for i in range(N))
    wp_block = ("    <waypoints>\n"
                f"        <id>{ids}</id>\n        <x>{xs}</x>\n        <y>{ys}</y>\n        <z>{zs}</z>\n"
                f"        <out>{outs}</out>\n        <incoming>{ins}</incoming>\n    </waypoints>\n")

    raw = open(cfg_path, encoding="utf-8").read()
    # strip any existing waypoints/mapmarker, insert fresh before </AutoDrive>
    import re
    raw = re.sub(r"\s*<waypoints>.*?</waypoints>", "", raw, flags=re.S)
    raw = re.sub(r"\s*<mapmarker>.*?</mapmarker>", "", raw, flags=re.S)
    raw = raw.replace("</AutoDrive>", wp_block + "</AutoDrive>")
    # backup once
    bak = cfg_path + ".bak"
    if not os.path.exists(bak):
        open(bak, "w", encoding="utf-8").write(open(cfg_path, encoding="utf-8").read())
    open(cfg_path, "w", encoding="utf-8").write(raw)

    seg_edges = len(edges)
    print(f"[autodrive] road skeleton: {seg_edges} centerline segments, {len(nodes)} junctions/ends"
          + (f"  | orientation={ORIENT} (skipped {n_orient_skip} non-{ORIENT} edges)" if ORIENT in ("ew", "ns") else ""))
    print(f"[autodrive] generated {N} waypoints (two-lane, right-hand offset {LANE_OFF:.1f} m), {njc} junction links")
    print(f"[autodrive] installed -> {cfg_path}  (backup: {os.path.basename(bak)})")
    print(f"[autodrive] LOAD THE SAVE FRESH. If lanes are on the WRONG side, set autodrive.right_hand_sign=-1 and re-run.")
    # overview for eyeballing
    S = 2048; scl = S / MAP
    ov = Image.new("RGB", (S, S), (16, 16, 18)); import PIL.ImageDraw as ImageDraw; d = ImageDraw.Draw(ov)
    for i in range(N):
        ix = (X[i] + MAP / 2) * scl; iz = (Z[i] + MAP / 2) * scl
        for t in outc[i]:
            jx = (X[t] + MAP / 2) * scl; jz = (Z[t] + MAP / 2) * scl
            d.line([ix, iz, jx, jz], fill=(90, 200, 255), width=1)
    ovp = os.path.join(OUT, "autodrive_overview.png"); ov.save(ovp)
    print(f"[autodrive] overview -> {ovp}")


if __name__ == "__main__":
    main()
