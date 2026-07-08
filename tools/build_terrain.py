"""
build_terrain.py - GENERATE the FS25 terrain from the FS22 ORIGINAL (read -> understand -> generate. No copying).
  read     : FS22 16-bit heightmap (map_dem.png = WW's landform) + heightScale/unitsPerPixel from the FS22 i3d
  understand: it's the elevation grid; WW's terrain is what we want to preserve (do NOT smooth it)
  generate : write the FS25 DEM through our own writer + stamp heightScale onto the engine-skeleton terrain node
"""
import os, sys, json
import numpy as np
from PIL import Image
import xml.etree.ElementTree as ET

WW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import convert_env
CONV = json.load(open(os.path.join(WW, "tools", os.environ.get("MAP_CONVERT", "wildwest.convert.json")), encoding="utf-8"))
FS22 = convert_env.source_dir(CONV)
OUT = os.path.join(WW, "out", CONV["identity"]["mod"])
I3D = CONV["identity"]["i3d"]


def main():
    # READ WW's DEM (16-bit elevation) + terrain scale from the FS22 i3d
    src_dem = os.path.join(FS22, CONV["source"]["dem"])
    dem = np.array(Image.open(src_dem))
    r = ET.parse(os.path.join(FS22, CONV["source"]["map_i3d"])).getroot()
    t = next(x for x in r.iter("TerrainTransformGroup"))
    height_scale, units = t.get("heightScale"), t.get("unitsPerPixel")

    # GENERATE the FS25 DEM (our writer, 16-bit) onto the skeleton
    dst = os.path.join(OUT, "maps", "data", "map_dem.png")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):                               # resample the FS22 DEM to the engine skeleton's res
        tgt = Image.open(dst).size                        # (handles a different unitsPerPixel, e.g. West End 64x 4097->8193)
        if dem.shape[0] != tgt[0]:
            dem = np.asarray(Image.fromarray(dem).resize(tgt, Image.BILINEAR)); print(f"  resampled DEM -> {tgt}")
    Image.fromarray(dem).save(dst)

    # stamp WW's heightScale. (Terrain-layer displacement is zeroed by the ENGINE for every map - see
    # gen_i3d micro_displacement - so it's not repeated here.)
    i3d_path = os.path.join(OUT, "maps", I3D)
    tree = ET.parse(i3d_path); root = tree.getroot()
    next(x for x in root.iter("TerrainTransformGroup")).set("heightScale", height_scale)
    tree.write(i3d_path, encoding="utf-8", xml_declaration=True)

    print(f"terrain: DEM {dem.shape} {dem.dtype} + heightScale={height_scale} (displacement zeroed by engine)")


if __name__ == "__main__":
    main()
