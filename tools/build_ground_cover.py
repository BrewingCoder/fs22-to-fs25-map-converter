"""
build_ground_cover.py - PHASE 2b: pasture ground cover = tall mowable GRASS + meadowUS wildflower drifts.
Per-cell (no squares), across WW's grass-textured pastures, off dirt AND off tilled fields.
Encoding value = runtimeTypeIdx<<5 | state, where runtimeTypeIdx = FML child index + 1 (type 0 = empty).
Proven live 2026-07-06 by dumping g_fruitTypeManager terrainDataPlaneIndex on a running working map (Back
Roads) via the orchestrator; see memory ww-pasture-foliage-typeindex-unsolved.md. On our mapUS-ordered FML:
grass=t6 (value 196 @ harvestReady), meadow=t3 (value 100).
Diag knobs: WW_DIAG_TYPE=<n> [WW_DIAG_STATE=<s>] bakes one raw type everywhere; WW_DIAG_BANDS=1 bakes 16
west->east bands of raw types 0..15 so ONE restart reads the whole write->render table.
"""
import os, sys, json
import numpy as np
from PIL import Image, ImageDraw
import xml.etree.ElementTree as ET
sys.path.insert(0, os.path.dirname(__file__)); import ww_fields
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import gdm_fruits_codec as gf

WW = os.environ.get("FS_CONVERT_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import convert_env
CONV = json.load(open(os.path.join(WW, "tools", os.environ.get("MAP_CONVERT", "wildwest.convert.json")), encoding="utf-8"))
_GC = CONV.get("ground_cover", {})                       # map-specific foliage values + pasture/exclude weight-layer names
WWD = os.path.join(os.path.dirname(os.path.join(convert_env.source_dir(CONV), CONV["source"]["map_i3d"].replace("/", os.sep))), "data")  # FS22 map-data dir (map-agnostic: dirname(map_i3d), NOT hardcoded "maps")
OUT = os.path.join(WW, "out", CONV["identity"]["mod"])
I3D = os.path.join(OUT, "maps", CONV["identity"]["i3d"])
FRUITS = os.path.join(OUT, "maps", "data", "densityMap_fruits.gdm")
N = 16384                                                # foliage-density grid (0.5 m/px, GPU-OOM cap - fixed, NOT map size)
MAPM = float(CONV.get("cfg", {}).get("map_m", 8192)); HALF = MAPM / 2.0
PASTURE_LAYER = _GC.get("pasture_layer", "grass")        # WW weight layer that = grass pasture
EXCLUDE_LAYERS = _GC.get("exclude_layers", ["dirt"])     # WW weight layers to keep foliage OFF
# groundFoliage states -> the dark clutter: 6 cover foliage(green), 12 maiden fern(green), 13 stinging nettle(green),
#   10 dry branches(brown), 7 ground poplar leaves(brown), 8 ground elm leaves(brown)
GF_STATES = [6, 12, 13, 10, 7, 8]


def ww_mask(base, thresh=110):
    """Finest achievable transition edge: max the 01-04 weight variants at FULL 0-255 (keeps GE's antialiased
    sub-cell brush edge), BILINEAR-upsample to the foliage grid (0.5 m/px), then threshold. -> ~0.5 m edge precision
    (the foliage density-map limit), vs the old 1 m NEAREST blocks. Lower thresh = grass reaches a touch closer."""
    acc = None
    for v in ("01", "02", "03", "04", ""):
        p = os.path.join(WWD, f"{base}{v}_weight.png")
        if os.path.exists(p):
            a = np.asarray(Image.open(p).convert("L")).astype(np.float32)
            acc = a if acc is None else np.maximum(acc, a)
    if acc is None:
        return np.zeros((N, N), bool)
    up = np.asarray(Image.fromarray(acc).resize((N, N), Image.BILINEAR))
    return up >= thresh


def field_mask():
    fields = ww_fields.read_fs22_fields(os.path.join(convert_env.source_dir(CONV), CONV["source"]["map_i3d"]))
    img = Image.new("L", (N, N), 0); d = ImageDraw.Draw(img); S = N / MAPM
    for f in fields:
        d.polygon([((x + HALF) * S, (z + HALF) * S) for x, z in f["polygon"]], fill=1)
    return np.asarray(img) > 0


def main():
    root = ET.parse(I3D).getroot()
    children = None
    for fml in root.iter("FoliageMultiLayer"):
        fts = [t.get("name") for t in fml.findall("FoliageType")]
        if "grass" in fts and "meadow" in fts:
            children = fts; break
    if not children:
        raise SystemExit("no grass/meadow typeIdx")
    # ==== ON-DISK VALUES (GE-VERIFIED 2026-07-06, the FINAL word after the child-index and manager-index laws
    #      both failed): the combined 10-bit fruits value is NOT (childIdx<<5)|stateOrdinal - GIANTS packs
    #      foliage states into the value space with its own scheme. Do not derive; READ the values from GIANTS
    #      Editor's Foliage panel (layer+state -> checked Foliage Channels = the bits of the on-disk value):
    #        grass  @ harvestReady = channels {1,2,7} = 134   (user-verified in GE 10.0.13 on this map)
    #        meadow @ harvestReady = channels {0,1,7} = 131   (GE-painted patch diff-verified in the gdm)
    #      A GE paint of meadow@harvestReady wrote exactly 131 (23,582 cells) and renders the dense tall
    #      grass+wildflower mix in-game/GE. The live game also normalizes any grass-plane write to 134.
    del children  # child order deliberately unused - values come from GE, not a formula

    # FOLIAGE = the GRASS ground-texture, EXACTLY (user insight 2026-07-06: the edge is finer than a coarse dilation
    # can place - the road/grass edge source is WW's 1m weight mask, so a dilation on top is a blunt over-account).
    # WW's grass-weight and road-weight are MUTUALLY EXCLUSIVE + adjacent, so the grass mask already stops at the road
    # at the SAME resolution as the texture -> no dilation, no separate road subtract; foliage and grass align exactly
    # and the edge tufts' own width reaches the pavement. If grass visibly creeps onto the road MESH (mesh wider than
    # the paint), that's a TEXTURE fix (rasterize the mesh footprint into 2a) - never a foliage setback.
    excl = np.zeros((N, N), bool)
    for lyr in EXCLUDE_LAYERS:
        excl |= ww_mask(lyr)
    pastures = ww_mask(PASTURE_LAYER) & (~excl) & (~field_mask())
    rng = np.random.default_rng(7)
    r = rng.integers(0, 256, (N, N), dtype=np.uint8)         # per-cell type roll
    vals = np.zeros((N, N), np.uint16)
    GRASS_HR = int(_GC.get("grass_value", 134))    # GE: grass @ harvestReady (Foliage Channels 1,2,7)
    MEADOW_HR = int(_GC.get("meadow_value", 131))  # GE: meadow @ harvestReady (Foliage Channels 0,1,7) - grass+wildflower mix
    diag = os.environ.get("WW_DIAG_VALUE")
    if diag is not None:
        dv = int(diag)
        vals[pastures] = np.uint16(dv)
        gf.encode_full(vals, FRUITS, FRUITS)
        print(f"DIAG: pastures baked as raw value={dv}")
        return
    # GRASS/FLOWR mix: meadowUS (grass+wildflowers, mowable via MOWER=MEADOW) as the dominant cover with
    # pure-grass drifts - matches what mapUS/Back Roads borders actually are (meadow-dominant).
    GRASS_PCT = int(_GC.get("grass_pct", 64))   # of 256 -> 25% pure grass, 75% meadow
    vals[pastures] = np.where(r[pastures] < GRASS_PCT, np.uint16(GRASS_HR), np.uint16(MEADOW_HR))

    gf.encode_full(vals, FRUITS, FRUITS)
    print(f"ground cover @ pastures {100*pastures.mean():.1f}% (off dirt+fields): meadowUS harvestReady "
          f"(value {MEADOW_HR}, grass+wildflowers) + {100*GRASS_PCT/256:.0f}% grass harvestReady (value {GRASS_HR})")


if __name__ == "__main__":
    main()
