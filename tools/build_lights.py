"""
build_lights.py - PHASE 5 (REVISED 2026-07-06): WW street/highway/sellpoint lights -> base-game FS25 "Street Light".
The extracted FS22 poles' textures + FS22 Light-node params did NOT translate to FS25 (wrong texture, bad light), so
we STOP extracting and instead place the native FS25 "Street Light" model (the $300 mapEU streetLight01) as an i3d
ReferenceNode at each original fixture's ground position + yaw. Reference (not full placeable) = same model + the
model's own FS25-tuned lights, without 4341 gameplay-placeable registrations (identical light-render cost, far less
overhead). We read each FS22 Light's parent-group WORLD transform for the pole base (x,z) + yaw; Y snaps to our DEM.
Idempotent (drops prior WW_lights). No FS22 textures/meshes/Light nodes are copied anymore.
"""
import os, sys, json, math
import numpy as np
import xml.etree.ElementTree as ET
from PIL import Image

WW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import convert_env
CONV = json.load(open(os.path.join(WW, "tools", os.environ.get("MAP_CONVERT", "wildwest.convert.json")), encoding="utf-8"))
FS22_I3D = os.path.join(convert_env.source_dir(CONV), CONV["source"]["map_i3d"])
OUT = os.path.join(WW, "out", CONV["identity"]["mod"])
OUT_I3D = os.path.join(OUT, "maps", CONV["identity"]["i3d"])
DEM = os.path.join(OUT, "maps", "data", "map_dem.png")
# MAP-SPECIFIC values from convert.json (config-driven) - defaults = WW's:
_SG = CONV.get("scene_groups", {})
STREETLIGHT = CONV.get("assets", {}).get("street_light",
    "$data/placeables/mapEU/brandless/lightsResidential/streetLight01/streetLight01.i3d")   # the $300 "Street Light"
TOP = tuple(_SG.get("top", ["WildWest", "WildWest2"]))
GROUPS = tuple(_SG.get("lights", ["RoadLights", "hwyLights", "SellpointLights"]))
MAP = float(CONV.get("cfg", {}).get("map_m", 8192))
YAW_OFFSET = float(CONV.get("assets", {}).get("street_light_yaw_offset", 90.0))   # rotate the FS25 lamp arm over the road (+90 CCW; flip sign if it aims the wrong way)


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


def main():
    wr = ET.parse(FS22_I3D).getroot()
    scene = wr.find("Scene")
    targets = [g for top in scene if top.get("name") in TOP
               for g in top if g.get("name") in GROUPS]

    # each fixture = a Light's PARENT group world transform: pole base (x,z) + yaw
    fixtures = []

    def walk(node, M0):
        M = M0 @ trs(node)
        for ch in node:
            if ch.tag == "Light":
                yaw = math.degrees(math.atan2(M[0, 2], M[0, 0])) + YAW_OFFSET   # parent-group Y-rotation + CCW aim offset
                yaw = (yaw + 180.0) % 360.0 - 180.0     # normalize to [-180,180): raw +90 produced 270 (out of range) which HUNG the load; -90 is the identical orientation
                fixtures.append((float(M[0, 3]), float(M[2, 3]), yaw))
            elif ch.tag in ("TransformGroup", "Shape"):
                walk(ch, M)
    for g in targets:
        walk(g, np.eye(4))

    seen, fx = set(), []
    for x, z, yaw in fixtures:                                    # dedup coincident lights
        k = (round(x, 1), round(z, 1))
        if k not in seen:
            seen.add(k); fx.append((x, z, yaw))
    P = np.array([(x, z) for x, z, _ in fx]); YAW = [y for _, _, y in fx]

    root = ET.parse(OUT_I3D).getroot()
    dem = np.asarray(Image.open(DEM)).astype(np.float64); H = dem.shape[0]
    hs = float(next(root.iter("TerrainTransformGroup")).get("heightScale"))
    px = np.clip(((P[:, 0] + MAP / 2) / MAP * (H - 1)).round().astype(int), 0, H - 1)
    pz = np.clip(((P[:, 1] + MAP / 2) / MAP * (H - 1)).round().astype(int), 0, H - 1)
    Y = dem[pz, px] / 65535.0 * hs

    files_el = root.find("Files"); oscene = root.find("Scene")
    for g in list(oscene):
        if g.tag == "TransformGroup" and g.get("name") == "WW_lights":
            oscene.remove(g)
    for f in list(files_el):                             # drop our prior streetLight File ref (avoid dup accumulation on rerun)
        if f.get("filename") == STREETLIGHT:
            files_el.remove(f)
    nextf = max(int(f.get("fileId")) for f in files_el) + 1
    nid = max((int(e.get("nodeId")) for e in root.iter() if (e.get("nodeId") or "").isdigit()), default=0) + 1
    fileid = str(nextf)
    ET.SubElement(files_el, "File", {"fileId": fileid, "filename": STREETLIGHT}); nextf += 1
    grp = ET.SubElement(oscene, "TransformGroup", {"name": "WW_lights", "clipDistance": "600", "nodeId": str(nid)}); nid += 1
    for i in range(len(P)):
        ET.SubElement(grp, "ReferenceNode", {"name": "streetLight01", "referenceId": fileid,
            "translation": f"{P[i,0]:.3f} {Y[i]:.3f} {P[i,1]:.3f}", "rotation": f"0 {YAW[i]:.1f} 0",
            "nodeId": str(nid)}); nid += 1

    ET.ElementTree(root).write(OUT_I3D, encoding="utf-8", xml_declaration=True)
    print(f"lights: {len(P)} FS25 'Street Light' references placed (replaced 4341 extracted fixtures) | "
          f"1 i3d File ref, clipDistance 600, no FS22 textures")


if __name__ == "__main__":
    main()
