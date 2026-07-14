"""
gen_data.py - generate every map-local binary the i3d references, from scratch via tools/binfmt.py. All resolutions
scale from cfg (mapcfg.py). Includes the 100ha owned wheat field: painted ground (sown), fruits (wheat), farmland.
Nothing copied from another map. See docs 40/41/50/51/60.
"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import binfmt

FIELD_HALF = 500          # 100 ha = 1000 x 1000 m field, centred on the origin
WHEAT = (6 << 5) | 7      # typeIdx is the HIGH channels (value>>5), state the LOW. wheat=FoliageType idx6 -> (6<<5)|7=199
GROUND_SOWN = 7           # terrainDetail groundType 7 = sown (tilled dirt under the crop)


def _sq(res, map_m, half_m, inset_m=0):
    """Centred-square pixel slice at resolution `res` for a `map_m`-metre map. inset_m shrinks it (workable area)."""
    ppm = res / map_m; c = res // 2; h = int((half_m - inset_m) * ppm)
    return slice(c - h, c + h)


def build(cfg, data_dir):
    os.makedirs(data_dir, exist_ok=True)
    P = lambda f: os.path.join(data_dir, f)
    M, N = cfg.map_m, cfg.density_res

    binfmt.flat_dem(P("map_dem.png"), cfg.dem_res, 8000, undulation=0)   # flat ~31 m
    binfmt.uniform_weight(P("grass_weight.png"), cfg.weight_res, 255)    # grass01 covers the map
    binfmt.uniform_weight(P("blank_weight.png"), cfg.weight_res, 0)      # shared by the other 85 layers (hidden)

    # densities (density_res^2). For STARTER maps, ground + fruits carry the 100ha field (workable area = 1 m inset);
    # conversions (cfg.starter_field=False) leave them blank so the real map's own densities aren't polluted.
    wa = _sq(N, M, FIELD_HALF, inset_m=1)
    ground = np.zeros((N, N), np.uint16)
    fruits = np.zeros((N, N), np.uint16)
    if cfg.starter_field:
        ground[wa, wa] = GROUND_SOWN
        fruits[wa, wa] = WHEAT
    binfmt.paint_gdm(P("densityMap_ground.gdm"), N, ground, 11, 1, 0)
    binfmt.blank_gdm(P("densityMap_height.gdm"), N, 12, 2, 0, range_splits=(6,))   # 2-range split at 6
    binfmt.blank_gdm(P("densityMap_groundFoliage.gdm"), N, 4, 1, 0)
    binfmt.paint_gdm(P("densityMap_fruits.gdm"), N, fruits, 10, 2, 5, range_splits=(5,))   # split at 5, ntic 5
    binfmt.blank_gdm(P("densityMap_weed.gdm"), N, 4, 1, 0)
    binfmt.blank_gdm(P("densityMap_stones.gdm"), N, 3, 1, 0)

    # infolayers (resolutions per cfg.il_res). farmland: field parcel = 1 (owned), rest = 2 (buyable).
    R = cfg.il_res
    farm = np.full((R["farmland"], R["farmland"]), 2, np.uint8)
    ff = _sq(R["farmland"], M, FIELD_HALF); farm[ff, ff] = 1
    binfmt.paint_grle(P("infoLayer_farmland.grle"), farm)
    binfmt.blank_grle(P("infoLayer_fieldType.grle"), R["fieldType"])
    for nm in ("indoorMask", "navigationCollision", "tipCollision", "tipCollisionGenerated",
               "placementCollision", "placementCollisionGenerated"):
        binfmt.blank_grle(P(f"infoLayer_{nm}.grle"), R[nm])

    # field-work LEVEL maps - blank (value 0 = un-fertilized/limed/plowed on a fresh map). Referenced by our
    # map-local config/fieldGround.xml (gen_configs). ALL are single-plane .grle (image_channels=1, like base
    # mapUS) - the density-map BIT width (spray/lime level = 2 bits = 0/50/100%, plow/stubble/roller = 1) lives
    # in fieldGround.xml's numChannels, NOT the grle image channels. Missing these = spray/fertilize contracts
    # stuck at 0% + no fertilizer harvest bonus (map falls back to mapUS's level maps). fs25-empty-map#1.
    for nm in ("sprayLevel", "limeLevel", "plowLevel", "stubbleShredLevel", "rollerLevel"):
        binfmt.blank_grle(P(f"infoLayer_{nm}.grle"), R[nm])
    # field weed blocking-state layer (blank = weeds not blocked; the weed system only grows them on cultivated
    # ground anyway). Referenced by our map-local weed.xml (gen_configs) instead of borrowing mapUS's.
    binfmt.blank_grle(P("infoLayer_weed.grle"), R["weed"])

    print(f"gen_data: {cfg.title} - DEM {cfg.dem_res}^2, densities {N}^2, 100ha wheat field -> {data_dir}")
