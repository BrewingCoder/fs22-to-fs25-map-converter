"""
build_field_entrances.py - PHASE (F1) BUILD: EXECUTE the entrance plan produced by scan_field_entrances.py.

Two-step design (locked with user 2026-07-12):
  * scan_field_entrances.py  = ANALYSIS. Phase1 (deepest-meadow turn-around sides) -> Phase2 (rule out
    buildings/steep) -> Phase3 (pick a spot in a streetlight gap, road-reachable). Writes field_entrance_plan.json
    and fails LOUD per field. That tool OWNS every decision.
  * build_field_entrances.py = THIS. Reads the plan and just BUILDS each chosen spot. No re-deciding.

For each planned entrance (world boundary point E=(x,z) + outward normal n), we:
  1. Snap to the nearest road cell R (road corridor distance transform), so the track starts on real pavement.
  2. PAINT a dirt "wheel track" (the map's dirt->mudDark ground layer) R -> E -> stub into the field. Same painted
     ground-detail mechanic the FS22 author used for field lanes; ground_cover keeps meadow foliage OFF the dirt layer.
  3. CLEAR the meadow foliage on the track (zero the fruits gdm cells) so the lane shows through the grass.
  4. CLEARANCE: remove TREES within clear_trees_m of E, and STREETLIGHTS within clear_lights_m of R (the plan already
     threaded the pole gap, so a pole is removed only if it's actually on the lane).

Idempotent-ish: re-running re-paints the same tracks (paint is additive; foliage stays cleared; trees/lights already
gone). Runs LATE (after ground_texture, ground_cover, trees, lights). If the plan is missing, runs the scan first.
Map-agnostic core; thresholds config-driven under "field_entrances".
"""
import os, sys, json, subprocess
import numpy as np
from PIL import Image, ImageDraw
import xml.etree.ElementTree as ET
from scipy.ndimage import distance_transform_edt, binary_dilation
from scipy.spatial import cKDTree

WW = os.environ.get("FS_CONVERT_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import convert_env
import gdm_fruits_codec as gf

CONV = json.load(open(os.path.join(WW, "tools", os.environ.get("MAP_CONVERT", "wildwest.convert.json")), encoding="utf-8"))
OUT = os.path.join(WW, "out", CONV["identity"]["mod"])
I3D = os.path.join(OUT, "maps", CONV["identity"]["i3d"])
DATA = os.path.join(OUT, "maps", "data")
PLAN = os.path.join(OUT, "field_entrance_plan.json")
MAP = float(CONV.get("cfg", {}).get("map_m", 8192))

_FE = CONV.get("field_entrances", {})
TWO_TRACK = bool(_FE.get("two_track", True))             # paint two wheel ruts (FS22 field-10 look) vs one solid band
GAUGE = float(_FE.get("track_gauge_m", 1.9))             # centre-to-centre spacing of the two ruts (tractor track gauge)
RUT_W = float(_FE.get("rut_width_m", 0.9))               # each rut's painted width (bare wheel track)
TRACK_W = float(_FE.get("track_width_m", 4.0))           # solid-lane width (used when two_track=false)
INTO_M = float(_FE.get("into_field_m", 2.0))             # extend the track this far past the field edge (into workable area)
CLEAR_TREES = float(_FE.get("clear_trees_m", _FE.get("clear_radius_m", 20.0)))   # remove TREES within this of the entrance MOUTH
TRACK_CLEAR = float(_FE.get("track_clear_trees_m", 3.5))  # ...OR within this of ANY point on the track line (trees standing in the lane)
ROAD_MOUTH_CLEAR = float(_FE.get("road_mouth_clear_trees_m", 8.0))  # ...OR within this of the ROAD CROSSING (trees crowding the mouth by the road)
CLEAR_LIGHTS = float(_FE.get("clear_lights_m", 6.0))     # remove a STREETLIGHT within this of the road crossing (poles are threaded)
LIGHT_LANE = float(_FE.get("light_lane_clear_m", 2.5))   # ...OR within (corridor half + this) of the track line - guarantees no pole stands IN the lane
# The track ground layer. mudTracks = the base-game FS25 tire-track texture (what WW field 10 uses via pathway->mudTracks
# and what GT's dirt lanes emulate) - a real DUAL wheel-track look, NOT a plain mudDark dirt band. Its engine Layer is
# <track_layer>01 (e.g. mudTracks01), which the FS25 base map ships wired to the blank weight map; we give it a dedicated one.
TRACK_LAYER = _FE.get("track_layer", "mudTracks")
TRACK_LAYER_ENGINE = _FE.get("track_layer_engine", TRACK_LAYER + "01")
ROAD_DILATE = float(_FE.get("road_dilate_m", 5.0))       # road corridor half-width for the nearest-road snap
CORRIDOR_W = (GAUGE + RUT_W) if TWO_TRACK else TRACK_W   # full lane footprint (for tree-line clearance)


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


def _road_field(root, H):
    """(roaddist_m grid, nearest-road iz, ix) on the HxH DEM grid, from all WW_roads subgroups dilated to a corridor."""
    wwroads = next((g for g in root.find("Scene") if g.get("name") == "WW_roads"), None)
    if wwroads is None:
        return None
    pts = []

    def walk(n, M0):
        M = M0 @ _trs(n)
        if n.tag == "Shape":
            pts.append(M[:3, 3])
        for ch in n:
            if ch.tag in ("TransformGroup", "Shape"):
                walk(ch, M)
    walk(wwroads, np.eye(4))
    p = np.array(pts)
    mpp = MAP / (H - 1)
    px = np.clip(((p[:, 0] + MAP / 2) / MAP * (H - 1)).round().astype(int), 0, H - 1)
    pz = np.clip(((p[:, 2] + MAP / 2) / MAP * (H - 1)).round().astype(int), 0, H - 1)
    m = np.zeros((H, H), bool); m[pz, px] = True
    m = binary_dilation(m, iterations=max(1, int(round(ROAD_DILATE / mpp))))
    dist, (iz, ix) = distance_transform_edt(~m, return_indices=True)
    return dist * mpp, iz, ix


def _w2g(x, z, H):
    return (int(np.clip(round((x + MAP / 2) / MAP * (H - 1)), 0, H - 1)),
            int(np.clip(round((z + MAP / 2) / MAP * (H - 1)), 0, H - 1)))


def _densify(poly, step=0.5):
    """Resample a world polyline [(x,z),...] to ~step-m spacing -> (N,2) array."""
    out = []
    for i in range(len(poly) - 1):
        a = np.asarray(poly[i], float); b = np.asarray(poly[i + 1], float)
        d = float(np.hypot(*(b - a))); k = max(1, int(round(d / step)))
        for t in np.linspace(0, 1, k, endpoint=False):
            out.append(a + (b - a) * t)
    out.append(np.asarray(poly[-1], float))
    return np.array(out)


def _rut_lines(dpts):
    """From a densified centre-line, return the two rut polylines offset +-GAUGE/2 along the local normal."""
    tang = np.gradient(dpts, axis=0)
    tn = np.hypot(tang[:, 0], tang[:, 1])[:, None]
    tang = np.divide(tang, tn, out=np.zeros_like(tang), where=tn > 1e-9)
    perp = np.column_stack([-tang[:, 1], tang[:, 0]])
    return dpts + perp * (GAUGE / 2.0), dpts - perp * (GAUGE / 2.0)


def _load_plan():
    if not os.path.exists(PLAN):
        print("[entrances] plan missing - running scan first")
        subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scan_field_entrances.py")], check=True)
    return json.load(open(PLAN, encoding="utf-8"))


def main():
    plan = _load_plan()
    ents = plan.get("entrances", [])
    if not ents:
        print("[entrances] plan has 0 entrances - nothing to build"); return

    root = ET.parse(I3D).getroot()
    H = np.asarray(Image.open(os.path.join(DATA, "map_dem.png"))).shape[0]
    rf = _road_field(root, H)
    if rf is None:
        print("[entrances] no WW_roads group - skipped"); return
    _, riz, rix = rf

    def _cross(x, z):
        """nearest road cell (world xz) for a boundary point."""
        col, rowp = _w2g(x, z, H)
        rz = int(riz[rowp, col]); rx = int(rix[rowp, col])
        return np.array([rx / (H - 1) * MAP - MAP / 2, rz / (H - 1) * MAP - MAP / 2])

    tracks = []             # polylines [(x,z),...] world for painting
    E_xz = []               # entrance boundary points (tree clearance anchors)
    R_xz = []               # road crossings (streetlight clearance anchors)
    for e in ents:
        E = np.array([e["x"], e["z"]])
        n = np.array([e.get("nx", 0.0), e.get("nz", 0.0)])       # outward normal (field -> meadow -> road)
        nn = np.hypot(*n)
        n = n / nn if nn > 1e-6 else np.array([0.0, 0.0])
        R = _cross(E[0], E[1])
        Ein = E - n * INTO_M                                     # stub INTO the field (opposite the outward normal)
        tracks.append([R, E, Ein])
        E_xz.append(E); R_xz.append(R)
    E_xz = np.array(E_xz); R_xz = np.array(R_xz)

    # ---- 1. PAINT two wheel ruts into the mudTracks layer's OWN weight PNG (the tire-track texture) --------------
    # mudTracks01 ships wired to the shared blank weight map; give it a dedicated one (same wiring build_ground_texture
    # uses for mudDark/asphalt/etc.) so the base-game tire-track texture renders where we paint. Rut geometry = two
    # continuous offset lines with a GRASS crown left between them (GT dirt-lane style).
    Wt = Image.open(os.path.join(DATA, "mudDark_weight.png")).size[0]      # match the map's weight-map resolution
    St = Wt / MAP
    wimg = Image.new("L", (Wt, Wt), 0); wd = ImageDraw.Draw(wimg)          # fresh (mudTracks was unpainted)

    def _paintline(img_draw, pts_world, width_m):
        px = [((x + MAP / 2) * St, (z + MAP / 2) * St) for x, z in pts_world]
        img_draw.line(px, fill=255, width=max(1, int(round(width_m * St))), joint="curve")

    rut_polys = []                                                        # densified rut centre-lines (for the foliage clear)
    for tr in tracks:
        if TWO_TRACK:
            dd = _densify(tr); lft, rgt = _rut_lines(dd)
            _paintline(wd, lft, RUT_W); _paintline(wd, rgt, RUT_W)
            rut_polys.append(lft); rut_polys.append(rgt)
        else:
            _paintline(wd, tr, TRACK_W); rut_polys.append(np.array(tr, float))
    wp = os.path.join(DATA, f"{TRACK_LAYER}_weight.png"); wimg.save(wp)

    # wire the engine track layer at this new weight map (dedicated File; never touch the shared blank). mudTracks is
    # a CombinedLayer (mudTracks01;mudTracks02 noise-blended), so point EVERY sublayer (mudTracks01, mudTracks02) at
    # the same weight map - else the noise blend has a half-blank input and the texture renders wrong / not at all.
    files_el = root.find("Files")
    subs = [L for L in root.iter("Layer") if (L.get("name") or "").startswith(TRACK_LAYER)]
    if not subs:
        print(f"[entrances] WARN no engine terrain layer '{TRACK_LAYER}*' - track texture will not render")
    else:
        fid = str(max(int(f.get("fileId")) for f in files_el) + 1)
        ET.SubElement(files_el, "File", {"fileId": fid, "filename": f"data/{TRACK_LAYER}_weight.png"})
        for L in subs:
            L.set("weightMapId", fid)
    paint_desc = (f"two mudTracks ruts {RUT_W:.1f} m @ {GAUGE:.1f} m gauge (grass crown between)" if TWO_TRACK
                  else f"solid {TRACK_W:.1f} m {TRACK_LAYER} band")

    # ---- 2. CLEAR meadow foliage ON THE RUTS ONLY (fruits gdm) - leave the grass crown between + verges outside,
    #         so it reads as a DUAL wheel-track, not a bare swath -------------------------------------------------
    fruits = os.path.join(DATA, "densityMap_fruits.gdm")
    vals = gf.decode_full(fruits); Ng = vals.shape[0]; Sg = Ng / MAP
    mimg = Image.new("L", (Ng, Ng), 0); md = ImageDraw.Draw(mimg)
    gpx_w = max(1, int(round((RUT_W + 0.5) * Sg)))              # clear each rut (+0.5 m) so tall meadow doesn't hide the tracks
    for rp in rut_polys:
        pts = [((x + MAP / 2) * Sg, (z + MAP / 2) * Sg) for x, z in rp]
        md.line(pts, fill=1, width=gpx_w, joint="curve")
    tmask = np.asarray(mimg) > 0
    ncleared = int((tmask & (vals > 0)).sum())
    vals[tmask] = 0
    gf.encode_full(vals, fruits, fruits)

    # ---- 3. CLEARANCE ------------------------------------------------------------------------------------------
    #   TREES: removed if within CLEAR_TREES of the entrance MOUTH (E) OR within (corridor half + TRACK_CLEAR) of ANY
    #          point on the track line - kills trees standing in the lane out across the meadow, not just at the mouth.
    #   STREETLIGHTS: removed only if within CLEAR_LIGHTS of the road crossing (R); the plan pre-threads the pole gap.
    trackpts = np.vstack([_densify(tr) for tr in tracks])       # every ~0.5 m sample along all lanes
    tracktree = cKDTree(trackpts)
    tree_line_r = CORRIDOR_W / 2.0 + TRACK_CLEAR
    light_line_r = CORRIDOR_W / 2.0 + LIGHT_LANE
    removed = {}
    for gname, mode in (("WW_trees", "tree"), ("WW_lights", "light")):
        g = next((x for x in root.find("Scene") if x.get("name") == gname), None)
        if g is None:
            removed[gname] = 0; continue
        drop = []
        for rn in list(g):
            if rn.tag != "ReferenceNode":
                continue
            t = (rn.get("translation") or "0 0 0").split()
            p = np.array([float(t[0]), float(t[2])])
            if mode == "tree":
                hit = (np.min(np.hypot(E_xz[:, 0] - p[0], E_xz[:, 1] - p[1])) <= CLEAR_TREES
                       or np.min(np.hypot(R_xz[:, 0] - p[0], R_xz[:, 1] - p[1])) <= ROAD_MOUTH_CLEAR
                       or tracktree.query(p)[0] <= tree_line_r)
            else:                                                # pole in the lane (near crossing OR anywhere on the track)
                hit = (np.min(np.hypot(R_xz[:, 0] - p[0], R_xz[:, 1] - p[1])) <= CLEAR_LIGHTS
                       or tracktree.query(p)[0] <= light_line_r)
            if hit:
                drop.append(rn)
        for rn in drop:
            g.remove(rn)
        removed[gname] = len(drop)
    ET.ElementTree(root).write(I3D, encoding="utf-8", xml_declaration=True)

    nfields = len({e["field"] for e in ents})
    print(f"[entrances] BUILT {len(ents)} entrances across {nfields} fields (from plan)")
    print(f"  dirt tracks painted -> {TRACK_LAYER}_weight.png ({paint_desc}) | foliage cleared {ncleared} gdm cells")
    print(f"  cleared: {removed.get('WW_trees',0)} trees (mouth r={CLEAR_TREES:.0f} m + lane r={tree_line_r:.1f} m), "
          f"{removed.get('WW_lights',0)} streetlights on-lane (r={CLEAR_LIGHTS:.0f} m)")
    if plan.get("fails"):
        print(f"  NOTE: {len(plan['fails'])} field(s) had NO entrance in the plan: "
              f"{', '.join(str(x[0]) for x in plan['fails'])}")


if __name__ == "__main__":
    main()
