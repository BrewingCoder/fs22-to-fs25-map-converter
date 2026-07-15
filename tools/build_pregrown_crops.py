"""
build_pregrown_crops.py - bake PRE-GROWN, fertilized crops onto the NPC/contract fields, like proper workshop maps
ship. This is the true fix for the chronic harvest-contract shortfall (decoded live 2026-07-14, see
ww-harvest-contract-shortfall-bug).

WHY (the decoded mechanic):
  * A harvest contract's required delivery is sized from `getMaxCutLiters = actualCropArea * fruit.literPerSqm * 1.60`
    where 1.60 = 1 + 0.45 (full fertilizer, harvestSprayScaleRatio) + 0.15 (full lime, harvestLimeScaleRatio). It is
    ALWAYS best-case - it never reads the field's real spray/lime. The ACTUAL harvest applies the field's REAL
    sprayLevel/limeLevel, so a field that isn't fully fertilized+limed always falls short of the requirement.
  * Every fruitType has `resetsSpray=true`: SOWING a field wipes its sprayLevel back to 0. Our converter shipped the
    fruits gdm with ZERO crops (meadow+grass only), so FS25 sows the NPC fields itself at runtime -> that sow event
    erases the sprayLevel=2 build_field_fertility painted -> harvest reads spray=0 -> ~30% short, every time.
  * Proper maps (Huron County) ship their fields PRE-GROWN with real crops at varied growth states. No runtime sow ->
    the map's fertilizer persists -> harvest hits ~100%. Live-verified on OUR map: field 37 (which the NPC sim left at
    sprayLevel=2 + limeLevel=3) predicts EXACTLY 100.0% of getMaxCutLiters; under-fertilized fields predict 70-72%.

THE FIX: paint each NPC field's cells in densityMap_fruits with a ripe (mostly) crop, and set densityMap_ground to
SOWN(7) so FS25 sees an established field and does NOT re-sow it. build_field_fertility already painted sprayLevel=2 +
limeLevel=3 + plowLevel=1 on these same fields; with no sow event that fertilizer now persists -> harvest = 100%.

KEY ENCODING FACTS (all verified live via the orch_dll /lua/eval bridge):
  * fruits gdm value for a REAL crop is ORDINAL: value = terrainDataPlaneIndex | (growthState << 6). (Only grass/
    meadow use GIANTS' non-ordinal foliage-channel packing - see build_ground_cover. Real crops are plain.)
    Confirmed: canola ripe = 8 | (9<<6) = 584; wheat ripe = 7 | (8<<6) = 519 - matched getDensityAtWorldPos exactly.
  * terrainDataPlaneIndex == the 0-based POSITION of the FoliageType in the map i3d's fruits FoliageMultiLayer
    (meadow=3, grass=6, wheat=7, canola=8, ...). Derived from the i3d here -> fully map-agnostic, nothing hardcoded.
  * The fertilizer harvest bonus comes 100% from the sprayLevel infoLayer, NOT the terrainDetail spray TYPE (field 37,
    fully fertilized, has sprayType=0 everywhere) - so we DON'T touch terrainDetail spray channels.

Runs AFTER `crops` (so build_crops/build_ground_cover have finished writing the fruits gdm and the custom-crop foliage
is registered); it overwrites ONLY the NPC field cells and preserves the meadow/grass everywhere else (the codec round-
trips those values byte-for-byte). Excludes the player's OWNED fields (left PLOWED + empty by build_fields).

Usage: python tools/build_pregrown_crops.py
"""
import os, sys, json, re
import numpy as np
import xml.etree.ElementTree as ET
from PIL import Image, ImageDraw

WW = os.environ.get("FS_CONVERT_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import convert_env, ww_fields, binfmt, grle_codec, gdm_codec
import gdm_fruits_codec as gf

CONV = json.load(open(os.path.join(WW, "tools", os.environ.get("MAP_CONVERT", "wildwest.convert.json")), encoding="utf-8"))
OUT = os.path.join(WW, "out", CONV["identity"]["mod"])
MAPS = os.path.join(OUT, "maps")
DATA = os.path.join(MAPS, "data")
I3D = os.path.join(MAPS, CONV["identity"]["i3d"])
FS22 = os.path.join(convert_env.source_dir(CONV), CONV["source"]["map_i3d"])
MAP = float(CONV.get("cfg", {}).get("map_m", 8192))
GROUND_SOWN = 7   # terrainDetail groundType = SOWN (same value build_fields/gen_data bake for a worked field)

_PG = CONV.get("pregrown_crops", {})
ENABLED = bool(_PG.get("enabled", True))
RIPE_PCT = int(_PG.get("ripe_pct", 100))         # % of NPC fields pre-grown to RIPE (harvestable at max yield). DEFAULT
#   100 = ALL ripe. WHY: getMaxCutLiters assumes MAX growth, so a harvest contract that lands on a below-ripe field
#   yields less than required -> shortfall (verified: ripe field 29 harvested 103.5% of getMaxCut = matches Huron
#   exactly; a sub-max field falls short). The "growing" fields (ripe_pct<100) are set BELOW minForage = immature, so
#   FS25 never offers a harvest contract on them until they naturally ripen -> no sub-max-growth short contracts ever.
# rotation of combinable field-mission crops (name in the i3d FoliageMultiLayer, maxHarvestingGrowthState). maxHarvest
# is a base-game crop constant. raw plane index is DERIVED from the i3d (map-agnostic). Overridable via config.
DEFAULT_ROTATION = [["wheat", 8], ["barley", 8], ["canola", 9], ["oat", 5],
                    ["maize", 7], ["sunflower", 8], ["soybean", 7], ["sorghum", 5]]
ROTATION = [tuple(x) for x in _PG.get("rotation", DEFAULT_ROTATION)]
EXCLUDE = set(int(x) for x in _PG.get("exclude_fields", []))


def foliage_plane_index():
    """{FoliageType name -> terrainDataPlaneIndex}. The plane index is the GLOBAL 0-based position of the FoliageType
    across ALL FoliageMultiLayers in document order (NOT within one FML): the i3d has several FMLs (decoBush alone,
    then the big crop FML, then weed, then stone), and the engine numbers the terrain data planes continuously across
    them - so decoBush=0, decoFoliage=1, decoBushUS=2, meadow=3, ..., grass=6, wheat=7, canola=8 (live-verified)."""
    root = ET.parse(I3D).getroot()
    names = []
    for fml in root.iter("FoliageMultiLayer"):
        names.extend(t.get("name") for t in fml.findall("FoliageType"))
    if "wheat" not in names or "canola" not in names:
        raise SystemExit("[pregrown_crops] no crop FoliageTypes (wheat/canola) in i3d")
    return {nm: i for i, nm in enumerate(names)}


def owned_field_nums(fields):
    """Field nums whose parcel is defaultFarmProperty (the player's OWNED fields) - replicates build_fields'
    owned-detection so we leave them PLOWED + empty. Returns a set (empty if farmland data unavailable)."""
    fx = os.path.join(MAPS, "farmlands.xml")
    grid = os.path.join(DATA, "infoLayer_farmland.grle")
    if not (os.path.exists(fx) and os.path.exists(grid)):
        return set()
    default_ids = {int(m.group(1)) for m in re.finditer(
        r'<farmland id="(\d+)"[^/]*defaultFarmProperty="true"', open(fx, encoding="utf-8").read())}
    if not default_ids:
        return set()
    from shapely.geometry import Polygon, Point
    farm = np.asarray(grle_codec.decode(open(grid, "rb").read())[0]); FN = farm.shape[0]
    def fpx(w): return min(max(int((w + MAP / 2) / MAP * FN), 0), FN - 1)
    owned = set()
    for f in fields:
        poly = Polygon(f["polygon"]); minx, minz, maxx, maxz = poly.bounds
        votes = {}
        for wx in np.linspace(minx, maxx, 9):
            for wz in np.linspace(minz, maxz, 9):
                if poly.contains(Point(wx, wz)):
                    v = int(farm[fpx(wz), fpx(wx)])
                    if v > 0:
                        votes[v] = votes.get(v, 0) + 1
        if not votes:
            cx, cz = poly.centroid.coords[0]; votes = {int(farm[fpx(cz), fpx(cx)]): 1}
        if max(votes, key=votes.get) in default_ids:
            owned.add(f["num"])
    return owned


def main():
    if not ENABLED:
        print("[pregrown_crops] disabled via config; NPC fields left bare (FS25 will sow them unfertilized)")
        return
    fruits_p = os.path.join(DATA, "densityMap_fruits.gdm")
    ground_p = os.path.join(DATA, "densityMap_ground.gdm")
    if not (os.path.exists(fruits_p) and os.path.exists(ground_p)):
        raise SystemExit("[pregrown_crops] densityMap_fruits/ground missing - run earlier steps first")

    plane = foliage_plane_index()
    missing = [c for c, _ in ROTATION if c not in plane]
    if missing:
        raise SystemExit(f"[pregrown_crops] crops not in i3d FoliageMultiLayer: {missing}")

    fields = ww_fields.read_fs22_fields(FS22)
    owned = owned_field_nums(fields) | EXCLUDE
    npc = sorted((f for f in fields if f["num"] not in owned and len(f.get("polygon") or []) >= 3),
                 key=lambda f: f["num"])

    # decode both maps at density_res; paint per-field; preserve everything outside the fields
    vals = np.asarray(gf.decode_full(fruits_p))                      # fruits (multi-range 11/6), value=typeIdx|(state<<6)
    _g = gdm_codec.decode(open(ground_p, "rb").read())
    ground = np.asarray(_g[0] if isinstance(_g, tuple) else _g).astype(np.uint16)
    N = vals.shape[0]
    assert ground.shape[0] == N, f"fruits/ground res mismatch {vals.shape} vs {ground.shape}"

    def px(w): return min(max(int((w + MAP / 2) / MAP * N), 0), N - 1)

    tally = {}
    nripe = 0
    for i, f in enumerate(npc):
        name, maxh = ROTATION[i % len(ROTATION)]
        raw = plane[name]
        num = f["num"]
        h = (num * 2654435761) & 0xFFFFFFFF                          # deterministic per-field (stable across regens)
        if h % 100 < RIPE_PCT:
            state = maxh; nripe += 1
        else:                                                        # IMMATURE (state 1-2, below every crop's minForage)
            state = 1 + ((h >> 8) % 2)                               # -> no harvest contract until it naturally ripens
        value = raw | (state << 6)                                   # ORDINAL crop encoding (verified live)

        mask = Image.new("L", (N, N), 0)
        ImageDraw.Draw(mask).polygon([(px(x), px(z)) for x, z in f["polygon"]], fill=1)
        m = np.asarray(mask, dtype=bool)
        vals[m] = np.uint16(value)
        ground[m] = np.uint16(GROUND_SOWN)                           # established sown field -> FS25 won't re-sow it
        tally[name] = tally.get(name, 0) + 1

    gf.encode_full(vals, fruits_p, fruits_p)                         # re-encode fruits (meadow/grass round-trip intact)
    binfmt.paint_gdm(ground_p, N, ground, 11, 1, 0)                  # re-encode ground (same params as build_fields)

    crops_str = ", ".join(f"{k}:{v}" for k, v in sorted(tally.items()))
    print(f"[pregrown_crops] {len(npc)} NPC fields pre-grown ({nripe} ripe / {len(npc) - nripe} growing), "
          f"{len(owned)} owned excluded. Ground=SOWN, fruits ordinal @ {N}^2. Crops: {crops_str}. "
          f"Harvest contracts now spawn on fertilized ripe fields -> ~100% of getMaxCutLiters.")


if __name__ == "__main__":
    main()
