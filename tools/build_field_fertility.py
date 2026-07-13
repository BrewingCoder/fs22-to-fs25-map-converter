"""
build_field_fertility.py - paint the field-work LEVEL maps so NPC/AI crop fields spawn in a fully WORKED state:
FERTILIZED (sprayLevel) + LIMED (limeLevel) + PLOWED (plowLevel) - matching proper workshop maps (Huron).

WHY: FS25 harvest contracts size their required delivery from `getMaxCutLiters` = the field's FULLY fertilized +
limed MAX yield (fertilizer +45% `harvestSprayScaleRatio`, lime +15% `harvestLimeScaleRatio`). Proper workshop maps
(verified on Huron County: its infoLayer_sprayLevel.png is 68% painted, values 1/2) ship their fields PRE-fertilized
so a full harvest actually reaches getMaxCutLiters. A from-scratch/converted map generates BLANK level maps (0 spray,
0 lime) -> fields yield only ~2/3 of getMaxCutLiters -> every harvest contract comes up ~30% short, unfixable by the
player (you can't fertilize on a harvest-only contract). This step paints sprayLevel=2 (100%) + limeLevel=2 on every
field's plowable area so harvest contracts are completable. Runs AFTER build_farmland (needs the level grles from
`start` + the field polygons). Map-agnostic; uses ww_fields (the FS22 field polygons) like scan_field_entrances.

Usage: python tools/build_field_fertility.py
"""
import os, sys
import numpy as np
from PIL import Image, ImageDraw

WW = os.environ.get("FS_CONVERT_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json
import convert_env, ww_fields, binfmt, grle_codec

CONV = json.load(open(os.path.join(WW, "tools", os.environ.get("MAP_CONVERT", "wildwest.convert.json")), encoding="utf-8"))
OUT = os.path.join(WW, "out", CONV["identity"]["mod"])
DATA = os.path.join(OUT, "maps", "data")
FS22 = os.path.join(convert_env.source_dir(CONV), CONV["source"]["map_i3d"])
MAP = float(CONV.get("cfg", {}).get("map_m", 8192))

_FF = CONV.get("field_fertility", {})
SPRAY_LEVEL = int(_FF.get("spray_level", 2))    # 2 = 100% fertilized (sprayLevel maxValue=2)
LIME_LEVEL = int(_FF.get("lime_level", 3))      # FULL lime (limeLevel is 2-channel, 0..3)
PLOW_LEVEL = int(_FF.get("plow_level", 1))      # plowed (plowLevel 1-channel, 0/1). Huron ships fields plowed (86%);
                                                # a proper map's fields are WORKED. Unplowed = yield penalty vs getMaxCutLiters.
ENABLED = bool(_FF.get("enabled", True))


def _grle_res(path):
    """size_px from a GRLE header (bytes 5..9)."""
    import struct
    with open(path, "rb") as fh:
        return struct.unpack("<I", fh.read(9)[5:9])[0]


def field_mask(n):
    """Rasterize the FS22 field polygons into an n x n grid (arr[pz][px], same world->pixel convention the farmland
    grle uses - verified by scan_field_entrances reading it back correctly)."""
    fields = ww_fields.read_fs22_fields(FS22)
    img = Image.new("L", (n, n), 0)
    d = ImageDraw.Draw(img)

    def px(w):
        return int(min(max((w + MAP / 2) / MAP * (n - 1), 0), n - 1))
    nfilled = 0
    for f in fields:
        poly = f.get("polygon") or []
        if len(poly) < 3:
            continue
        d.polygon([(px(x), px(z)) for x, z in poly], fill=1)   # (col,row)=(px,pz)
        nfilled += 1
    return (np.asarray(img) > 0), nfilled, len(fields)


def main():
    layers = [("infoLayer_sprayLevel.grle", SPRAY_LEVEL), ("infoLayer_limeLevel.grle", LIME_LEVEL),
              ("infoLayer_plowLevel.grle", PLOW_LEVEL)]
    paths = [os.path.join(DATA, nm) for nm, _ in layers]
    if not all(os.path.exists(p) for p in paths):
        raise SystemExit("[field_fertility] level grles missing - run 'start' (gen_data) first")
    if not ENABLED:
        print("[field_fertility] disabled via config; leaving fields un-worked")
        return
    n = _grle_res(paths[0])
    mask, nfilled, ntot = field_mask(n)
    for path, val in zip(paths, (SPRAY_LEVEL, LIME_LEVEL, PLOW_LEVEL)):
        arr = np.zeros((n, n), np.uint8)
        arr[mask] = val
        binfmt.paint_grle(path, arr)          # single-plane grle (image_channels=1), matches gen_data
    pct = 100.0 * mask.sum() / mask.size
    print(f"[field_fertility] worked {nfilled}/{ntot} fields ({pct:.1f}% of map) -> sprayLevel={SPRAY_LEVEL} "
          f"limeLevel={LIME_LEVEL} plowLevel={PLOW_LEVEL} @ {n}^2. Harvest contracts now reach getMaxCutLiters.")


if __name__ == "__main__":
    main()
