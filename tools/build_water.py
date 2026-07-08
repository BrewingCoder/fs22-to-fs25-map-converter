"""
build_water.py - PHASE 6: FS25-NATIVE water, PER BASIN. Like trees (user 2026-07-06): water carries ZERO FS22
assets - FS25 has its own seasonal/simulated water. We READ only PLACEMENT (per-basin water level) and GENERATE
native FS25 oceanShader planes:
  - material = FS25 base-game 'waterSim_mat' (oceanShader.xml + $data/maps/textures/shared/water_normal.png,
    planar refraction, depthScale/fog) copied verbatim from $data/maps/mapUS/textures/waterplanes.i3d - 100% $data.
  - ONE flat plane PER connected water body (like the original modeled them), each at its OWN level, because the
    bodies fill to different heights (WW: the field-41 pond sits ~1.5 m above the river/ocean). A single global
    plane can't do that. Each basin is a DEM connected-component below capture_level; its plane extends past any
    MAP EDGE it touches (open-water horizon for the port); the TERRAIN clips each plane to its waterline. Level =
    default_level_m, overridden per body by a config point (nearest component centroid).
The plane meshes are generated here (flat grids, worldspace ocean shader) and serialized as v7 shapes (matching the
map's wildwest.i3d.shapes version) - NOT FS22 meshes. Idempotent (drops WW_water + ww_water_* shapes + mat).
Atomic .shapes write. Config: water_plane block in <map>.convert.json.
"""
import os, sys, json
import numpy as np
from PIL import Image
from scipy import ndimage
import xml.etree.ElementTree as ET
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import shapes_codec as sc

WW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import convert_env
CONV = json.load(open(os.path.join(WW, "tools", os.environ.get("MAP_CONVERT", "wildwest.convert.json")), encoding="utf-8"))
OUT = os.path.join(WW, "out", CONV["identity"]["mod"])
OUT_I3D = os.path.join(OUT, "maps", CONV["identity"]["i3d"])
OUT_SHAPES = os.path.join(OUT, "maps", os.path.splitext(CONV["identity"]["i3d"])[0] + ".i3d.shapes")
FS22 = convert_env.source_dir(CONV)
DEM = os.path.join(FS22, CONV["source"]["dem"])
MAPM = float(CONV.get("cfg", {}).get("map_m", 8192))
HALF = MAPM / 2.0

WP = CONV.get("water_plane", {})
DEFAULT_LEVEL = float(WP.get("default_level_m", WP.get("level_m", 86.5)))
BODIES = WP.get("bodies", [])
CAPTURE = float(WP.get("capture_level_m", max([DEFAULT_LEVEL] + [float(b["level_m"]) for b in BODIES]) + 0.25))
CELL = float(WP.get("cell_size_m", 25.0))
EDGE_EXTENT = float(WP.get("edge_extent_m", 4000.0))
MARGIN = float(WP.get("margin_m", 60.0))
MIN_AREA = float(WP.get("min_area_m2", 500.0))
MIN_DEPTH = float(WP.get("min_depth_m", 1.0))      # a real basin must hold >= this much water below its plane
MAT = WP.get("material", {})
SHADER = MAT.get("shader", "$data/shaders/oceanShader.xml")
NORMALMAP = MAT.get("normalmap", "$data/maps/textures/shared/water_normal.png")
DEPTHSCALE = MAT.get("depthScale", "0.4")
FOGCOLOR = MAT.get("underwaterFogColor", "0.09 0.14 0.11 1.0")
FOGDEPTH = MAT.get("underwaterFogDepth", "2.4 2.5 1.3 1.0")
OPT = 131                                          # N|TAN|UV1


def water_bodies():
    """Connected DEM basins below CAPTURE. Returns list of dicts: bbox (world), edges touched, level."""
    hs = float(CONV.get("source", {}).get("height_scale", 255.0))
    a = np.asarray(Image.open(DEM)).astype(np.float32)
    Y = a / (65535.0 if a.dtype != np.uint8 else 255.0) * hs
    N = Y.shape[0]; upp = MAPM / (N - 1)
    lab, n = ndimage.label(Y < CAPTURE)
    out = []
    cents = []
    for ci in range(1, n + 1):
        rows, cols = np.where(lab == ci)
        if len(rows) * upp * upp < MIN_AREA:
            continue
        wx = cols * upp - HALF; wz = rows * upp - HALF
        b = dict(x0=wx.min(), x1=wx.max(), z0=wz.min(), z1=wz.max(),
                 west=cols.min() == 0, east=cols.max() == N - 1,
                 north=rows.min() == 0, south=rows.max() == N - 1,
                 level=DEFAULT_LEVEL, cx=float(wx.mean()), cz=float(wz.mean()),
                 floor=float(Y[rows, cols].min()))
        out.append(b); cents.append((b["cx"], b["cz"]))
    cents = np.array(cents) if cents else np.zeros((0, 2))
    # apply per-body level overrides: nearest component centroid within 200 m of the config point
    for body in BODIES:
        px, pz = float(body["point"][0]), float(body["point"][1])
        if not len(cents):
            continue
        d = np.hypot(cents[:, 0] - px, cents[:, 1] - pz)
        j = int(d.argmin())
        if d[j] <= float(body.get("match_radius_m", 200.0)):
            out[j]["level"] = float(body["level_m"])
    # depth filter: a real water body must actually hold water (plane sits >= MIN_DEPTH above its floor).
    # Drops shallow DEM dips (e.g. a low spot under a road junction) that would render no water anyway.
    return [b for b in out if b["level"] - b["floor"] >= MIN_DEPTH]


def plane_shape(b, name):
    """Flat grid plane over the (edge-extended) body bbox at Y=level; worldspace ocean UVs. Returns shape dict."""
    x0 = (-HALF - EDGE_EXTENT) if b["west"] else b["x0"] - MARGIN
    x1 = (HALF + EDGE_EXTENT) if b["east"] else b["x1"] + MARGIN
    z0 = (-HALF - EDGE_EXTENT) if b["north"] else b["z0"] - MARGIN
    z1 = (HALF + EDGE_EXTENT) if b["south"] else b["z1"] + MARGIN
    nx = max(2, int(np.ceil((x1 - x0) / CELL)) + 1)
    nz = max(2, int(np.ceil((z1 - z0) / CELL)) + 1)
    gx = np.linspace(x0, x1, nx, dtype=np.float32)
    gz = np.linspace(z0, z1, nz, dtype=np.float32)
    xx, zz = np.meshgrid(gx, gz)
    lvl = b["level"]
    pos = np.stack([xx.ravel(), np.full(xx.size, lvl, np.float32), zz.ravel()], 1)
    nrm = np.tile(np.array([0, 1, 0], np.float32), (len(pos), 1))
    tan = np.tile(np.array([1, 0, 0, 1], np.float32), (len(pos), 1))
    uv = np.stack([((xx.ravel() - x0) / (x1 - x0)).astype(np.float32),
                   ((zz.ravel() - z0) / (z1 - z0)).astype(np.float32)], 1)
    idx = np.arange(nx * nz).reshape(nz, nx)
    a = idx[:-1, :-1].ravel(); bb = idx[:-1, 1:].ravel(); c = idx[1:, :-1].ravel(); d = idx[1:, 1:].ravel()
    tris = np.empty((len(a) * 2, 3), np.uint32)
    tris[0::2] = np.stack([a, c, bb], 1)
    tris[1::2] = np.stack([bb, c, d], 1)
    return dict(name=name, pos=pos, nrm=nrm, tan=tan, uv=uv, tris=tris)


def serialize_v7(sh, shape_id):
    """Serialize a flat plane to the v7 entity blob. v7 subset = 5 u32
    (firstVertex, numVertices, firstIndex, numIndices, uvDensity_f32); UV order (u,v) for v7 (>5)."""
    w = sc.BlobWriter()
    nm = sh["name"].encode("ascii")
    w.i32(len(nm)); w.raw(nm); w.align(4)
    w.u32(shape_id)
    P = sh["pos"]; ctr = P.mean(0); rad = float(np.linalg.norm(P - ctr, axis=1).max())
    for val in (float(ctr[0]), float(ctr[1]), float(ctr[2]), rad):
        w.f32(val)
    nv = len(P); nc = sh["tris"].size
    w.u32(nc); w.u32(1); w.u32(nv); w.u32(OPT)
    w.u32(0); w.u32(nv); w.u32(0); w.u32(nc); w.f32(1.0)          # subset (v7)
    is_int = nv > 65536
    for tri in sh["tris"]:
        (w.u32 if is_int else w.u16)(int(tri[0]))
        (w.u32 if is_int else w.u16)(int(tri[1]))
        (w.u32 if is_int else w.u16)(int(tri[2]))
    w.align(4)
    for p in P:
        w.f32(float(p[0])); w.f32(float(p[1])); w.f32(float(p[2]))
    for nn in sh["nrm"]:
        w.f32(float(nn[0])); w.f32(float(nn[1])); w.f32(float(nn[2]))
    for t in sh["tan"]:
        w.f32(float(t[0])); w.f32(float(t[1])); w.f32(float(t[2])); w.f32(float(t[3]))
    for uv in sh["uv"]:
        w.f32(float(uv[0])); w.f32(float(uv[1]))
    w.u32(0)
    return bytes(w.buf)


def peek_name(data):
    b = sc.Blob(data); nl = b.i32(); return b.take(nl).decode("ascii", "replace")


def is_ours(nm):
    return nm.startswith("ww_water") or nm == "ww_ocean"


def main():
    bodies = water_bodies()
    if not bodies:
        raise SystemExit("no water basins found below capture_level")

    ver, seed, ents = sc.decode_entities(OUT_SHAPES)
    max_sid = 0; kept = []
    for et, d in ents:
        if et == 1:
            if is_ours(peek_name(d)):
                continue                                          # idempotent: drop prior water shapes
            b = sc.Blob(d); nl = b.i32(); b.take(nl); b.align(4); max_sid = max(max_sid, b.u32())
        kept.append((et, d))

    planes = []
    sid = max_sid
    for i, body in enumerate(bodies):
        sid += 1
        sh = plane_shape(body, f"ww_water_{i}")
        kept.append((1, serialize_v7(sh, sid)))
        planes.append((sid, body, len(sh["pos"]), len(sh["tris"])))

    blob = sc.encode_entities(ver, seed, kept)                    # encode first, then atomic replace
    tmp = OUT_SHAPES + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(blob)
    os.replace(tmp, OUT_SHAPES)

    # i3d: Files + one shared Material + Scene group with one Shape per plane
    tree = ET.parse(OUT_I3D); root = tree.getroot()
    files = root.find("Files"); mats = root.find("Materials"); scene = root.find("Scene")

    def file_id(fn):
        for f in files:
            if f.get("filename") == fn:
                return f.get("fileId")
        nid = str(max((int(f.get("fileId")) for f in files), default=0) + 1)
        ET.SubElement(files, "File", {"fileId": nid, "filename": fn}); return nid

    fid_shader = file_id(SHADER); fid_norm = file_id(NORMALMAP)
    for m in list(mats):
        if m.get("name") in ("ww_water_mat", "ww_ocean_mat"):
            mats.remove(m)
    for g in list(scene):
        if g.get("name") == "WW_water":
            scene.remove(g)

    mat_id = str(max((int(m.get("materialId")) for m in mats), default=0) + 1)
    mat = ET.SubElement(mats, "Material", {"name": "ww_water_mat", "materialId": mat_id,
        "diffuseColor": "1 1 1 1", "specularColor": "1 1 1", "customShaderId": fid_shader})
    ET.SubElement(mat, "Refractionmap", {"type": "planar", "coeff": "1", "bumpScale": "0.01", "withSSRData": "true"})
    ET.SubElement(mat, "CustomParameter", {"name": "depthScale", "value": DEPTHSCALE})
    ET.SubElement(mat, "CustomParameter", {"name": "underwaterFogColor", "value": FOGCOLOR})
    ET.SubElement(mat, "CustomParameter", {"name": "underwaterFogDepth", "value": FOGDEPTH})
    ET.SubElement(mat, "Normalmap", {"fileId": fid_norm})

    nid = max((int(e.get("nodeId")) for e in root.iter() if (e.get("nodeId") or "").isdigit()), default=0) + 1
    grp = ET.SubElement(scene, "TransformGroup", {"name": "WW_water", "nodeId": str(nid)}); nid += 1
    for sid_i, body, nv, nt in planes:
        ET.SubElement(grp, "Shape", {"name": f"ww_water", "shapeId": str(sid_i), "nodeId": str(nid),
            "materialIds": mat_id, "clipDistance": "300000", "castsShadows": "false", "receiveShadows": "true"})
        nid += 1

    tree.write(OUT_I3D, encoding="utf-8", xml_declaration=True)
    print(f"water: {len(planes)} FS25-native oceanShader planes (per basin), capture<{CAPTURE:.1f}m | mat={mat_id}")
    for sid_i, body, nv, nt in planes:
        edges = "".join(k[0].upper() for k in ("north", "east", "south", "west") if body[k])
        print(f"   sid={sid_i} Y={body['level']:.2f} bbox X[{body['x0']:.0f},{body['x1']:.0f}] "
              f"Z[{body['z0']:.0f},{body['z1']:.0f}] edges={edges or '-'} {nv}v/{nt}t")


if __name__ == "__main__":
    main()
