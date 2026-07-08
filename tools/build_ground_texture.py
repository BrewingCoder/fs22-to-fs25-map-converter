"""
build_ground_texture.py - PHASE 2a: paint WW's ground onto the engine terrain (read -> understand -> generate).
Reads WW's per-layer weight PNGs (8192^2 masks), ORs the 01-04 variants, and repoints the matching FS25 engine
layer's weightMapId at the mask. grass stays the engine's FULL base; the 5 types WW actually painted paint on top
-> WW's grass pastures + dirt fields/roads + asphalt roads + gravel + concrete pads. No copying (read the masks,
generate our own weight files + a dedicated File ref per layer). Mapping = the WW-real subset of GROUND_FS22_TO_FS25.
Idempotent-safe when run on a fresh engine i3d (convert.py start regenerates it clean each build).
"""
import os, sys, json
import numpy as np
from PIL import Image
import xml.etree.ElementTree as ET

WW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import convert_env
CONV = json.load(open(os.path.join(WW, "tools", os.environ.get("MAP_CONVERT", "wildwest.convert.json")), encoding="utf-8"))
# WW's ACTUALLY-painted types (verified >0.05% coverage) -> FS25 engine layer (config-driven, map-specific)
PAINT = {k: v for k, v in CONV.get("ground_layer_map", {}).items() if not k.startswith("_")}
WWD = os.path.join(os.path.dirname(os.path.join(convert_env.source_dir(CONV), CONV["source"]["map_i3d"].replace("/", os.sep))), "data")  # FS22 map-data dir (map-agnostic: dirname(map_i3d), NOT hardcoded "maps")
OUT = os.path.join(WW, "out", CONV["identity"]["mod"])
I3D = os.path.join(OUT, "maps", CONV["identity"]["i3d"])
DATA = os.path.join(OUT, "maps", "data")


def combined_mask(ww_base):
    """OR the 01-04 (and no-variant) WW weight masks into one uint8 0/255 mask."""
    m = None
    for v in ("01", "02", "03", "04", ""):
        p = os.path.join(WWD, f"{ww_base}{v}_weight.png")
        if os.path.exists(p):
            a = np.asarray(Image.open(p).convert("L"))
            m = a if m is None else np.maximum(m, a)
    return m


def main():
    tree = ET.parse(I3D); root = tree.getroot()
    files_el = root.find("Files")
    terrain = next(root.iter("TerrainTransformGroup"))
    layer_by_name = {lay.get("name"): lay for lay in terrain.iter("Layer")}
    next_id = max(int(f.get("fileId")) for f in files_el) + 1

    painted = []
    for ww_base, fs25 in PAINT.items():
        mask = combined_mask(ww_base)
        if mask is None:
            print(f"  skip {ww_base}: no weight files"); continue
        Image.fromarray(mask).save(os.path.join(DATA, f"{fs25}_weight.png"))
        lay = layer_by_name.get(fs25 + "01")
        if lay is None:
            print(f"  WARN no engine layer {fs25}01"); continue
        # dedicated File per painted layer (never touch the shared blank_weight File)
        fid = str(next_id); next_id += 1
        ET.SubElement(files_el, "File", {"fileId": fid, "filename": f"data/{fs25}_weight.png"})
        lay.set("weightMapId", fid)
        painted.append(f"{ww_base}->{fs25}01 ({100*(mask>0).mean():.1f}%)")

    tree.write(I3D, encoding="utf-8", xml_declaration=True)
    print("ground texture painted (grass base kept full):", painted)


if __name__ == "__main__":
    main()
