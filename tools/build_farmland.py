"""
build_farmland.py - GENERATE the FS25 farmland natively from the FS22 ORIGINAL (read -> understand -> generate).
NO copying. Source of truth = the FS22 original farmlands.xml (its per-<farmland> COMMENTS carry the buyability
meaning) + its parcel grle (the parcel shapes). We regenerate both in FS25 form.

  read     : FS22 farmlands.xml comments (parcel labels) + infoLayer_farmland.grle (parcel-id grid)
  understand: field/forest/bare land/placeable/Main Farm/BGA = buyable; freeway/crap/production/GC = NOT
  generate : FS25 farmlands.xml (showOnFarmlandsScreen="false" on the non-buyable) + FS25 grle (re-encoded)
"""
import re, os, sys, json
import numpy as np
from PIL import Image
sys.path.insert(0, os.path.dirname(__file__)); import ww_fields
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")); import binfmt
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")); import grle_codec

WW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import convert_env
CONV = json.load(open(os.path.join(WW, "tools", os.environ.get("MAP_CONVERT", "wildwest.convert.json")), encoding="utf-8"))
FS22 = convert_env.source_dir(CONV)
MAP_M = CONV.get("cfg", {}).get("map_m", 8192)
# FS22 map-data dir = the folder the source map.i3d lives in (WW: <src>/maps ; mapUS-based maps like West End:
# <src>/maps/mapUS). Derive it from source.map_i3d so the tool stays map-agnostic - don't hardcode "maps".
FS22_MAPS = os.path.dirname(os.path.join(FS22, CONV["source"]["map_i3d"].replace("/", os.sep)))
OUT = os.path.join(WW, "out", CONV["identity"]["mod"])
SHOT = os.path.join(os.path.expanduser("~"), ".fs_convert_cache", "ww_farmland_native.png")   # debug render (buyable=green/red)

# non-buyable parcel-label keywords from config (matched as lowercased substrings of the FS22 farmland comment)
NON_BUYABLE = tuple(s.lower() for s in CONV.get("farmland_classification", {}).get("non_buyable_labels",
              ["Map Freeway", "Main Map Crap", "Production", "GC Tree Nursery", "GC Seed Maker"]))


def is_buyable(comment):
    return not any(k in comment.lower() for k in NON_BUYABLE)


def read_fs22_farmlands():
    """READ the FS22 farmlands.xml via regex (its comments are malformed -> ET can't parse it)."""
    txt = open(os.path.join(FS22_MAPS, "farmlands.xml"), encoding="utf-8", errors="ignore").read()
    price = (re.search(r'pricePerHa="(\d+)"', txt) or [None, "60000"])[1]
    rows = []
    for m in re.finditer(r'<farmland id="(\d+)"([^>]*)/>[ \t]*(?:<!--+<?\s*(.*?)\s*-+->)?', txt):
        attrs, comment = m.group(2), (m.group(3) or "").strip()
        rows.append(dict(
            id=int(m.group(1)),
            npc=(re.search(r'npcName="([^"]*)"', attrs) or [None, None])[1],
            price_scale=(re.search(r'priceScale="([^"]*)"', attrs) or [None, "1"])[1],
            default_farm="defaultFarmProperty" in attrs,
            comment=comment,
        ))
    return price, rows


def field_to_farmland(arr):
    """Map each FS22 field NUMBER -> the farmland id it sits on, by majority-sampling the parcel grid over the
    field polygon. This is the ground truth (immune to the FS22 comment typos: 'field 52' twice, 'field 82' none).

    WHY: FS25 numbers a PDA field by the ID of the farmland under it (verified in-game: FS22 fields 41-44 sit on
    farmlands 50-53 and FS25 badges them 50-53). FS22 instead numbers by the field NODE name. So to make the FS25
    field numbers equal the FS22 ones (distributed AutoDrive/Courseplay configs are keyed to them), we relabel each
    field's farmland id to the field number below."""
    from shapely.geometry import Polygon, Point
    N = arr.shape[0]
    fields = ww_fields.read_fs22_fields(os.path.join(FS22, CONV["source"]["map_i3d"].replace("/", os.sep)))

    def px(w):   # world metre -> grid pixel (grid covers [-MAP_M/2, +MAP_M/2]); verified vs FS22 comment labels
        return min(max(int((w + MAP_M / 2) / MAP_M * N), 0), N - 1)

    f2f = {}
    for f in fields:
        poly = Polygon(f["polygon"])
        minx, minz, maxx, maxz = poly.bounds
        votes = {}
        for wx in np.linspace(minx, maxx, 9):
            for wz in np.linspace(minz, maxz, 9):
                if poly.contains(Point(wx, wz)):
                    v = int(arr[px(wz), px(wx)])
                    if v > 0:
                        votes[v] = votes.get(v, 0) + 1
        if not votes:   # tiny/degenerate polygon -> fall back to centroid
            cx, cz = poly.centroid.coords[0]
            votes = {int(arr[px(cz), px(cx)]): 1}
        f2f[f["num"]] = max(votes, key=votes.get)
    return f2f


def build_remap(arr, rows):
    """Permutation old_farmland_id -> new_id: each field's farmland -> the field number (1..82); every non-field
    parcel -> the next ids ABOVE the max field number (so any non-field stays clearly >max-field, never masquerading
    as a low field number). Bijective; ids not owned by a farmland (0, border 255) pass through unchanged."""
    f2f = field_to_farmland(arr)
    field_olds = list(f2f.values())
    if len(set(field_olds)) != len(field_olds):
        dup = sorted({v for v in field_olds if field_olds.count(v) > 1})
        raise SystemExit(f"[farmland] fields share a farmland parcel {dup} - can't remap 1:1; inspect field polygons")
    remap = {old: num for num, old in f2f.items()}
    nxt = max(f2f) + 1
    for r in sorted(rows, key=lambda r: r["id"]):
        if r["id"] not in remap:
            remap[r["id"]] = nxt; nxt += 1
    return remap, f2f


def main():
    price, rows = read_fs22_farmlands()

    # READ the FS22 parcel grid, then REMAP its ids so field-parcel id == FS22 field number (see build_remap).
    arr, _ = grle_codec.decode(open(os.path.join(FS22_MAPS, "data", "infoLayer_farmland.grle"), "rb").read())
    arr = arr.astype(np.uint8)
    remap, f2f = build_remap(arr, rows)
    lut = np.arange(256, dtype=np.uint8)
    for old, new in remap.items():
        lut[old] = new
    arr = lut[arr]                                    # grid now carries the FS25/FS22-parity ids
    for r in rows:
        r["id"] = remap[r["id"]]                      # farmlands.xml ids follow the same permutation
    rows.sort(key=lambda r: r["id"])                  # base-game farmlands.xml is id-sorted

    nb_ids = {r["id"] for r in rows if not is_buyable(r["comment"])}

    # GENERATE FS25 farmlands.xml
    # A valid FS25 farmlands.xml needs: (1) the schema NAMESPACE on <map>; (2) <farmlands> pointing DIRECTLY at the
    # grid file (densityMapFilename) + numChannels="8" (WW has 140 parcels, IDs to 255 = 8-bit; less truncates the
    # town/production IDs); (3) BASE npcName only (gotcha #8: custom FS22 NPC_US_X don't resolve an i3dFilename ->
    # owners never spawn -> zero contracts).
    BASE_NPCS = ("FARMER", "GRANDPA", "FORESTER", "HELPER")
    L = ['<?xml version="1.0" encoding="utf-8" standalone="no" ?>',
         '<map xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
         'xsi:noNamespaceSchemaLocation="../../../shared/xml/schema/farmlands.xsd">',
         f'    <farmlands densityMapFilename="maps/data/infoLayer_farmland.grle" numChannels="8" pricePerHa="{price}">']
    for idx, r in enumerate(rows):
        a = f'id="{r["id"]}" priceScale="{r["price_scale"]}" npcName="{BASE_NPCS[idx % len(BASE_NPCS)]}"'
        if r["default_farm"]: a += ' defaultFarmProperty="true"'
        if r["id"] in nb_ids: a += ' showOnFarmlandsScreen="false"'
        L.append(f'        <farmland {a} />  <!-- {r["comment"]} -->')
    L += ["    </farmlands>", "</map>", ""]
    os.makedirs(os.path.join(OUT, "maps"), exist_ok=True)
    open(os.path.join(OUT, "maps", "farmlands.xml"), "w", encoding="utf-8").write("\n".join(L))

    # GENERATE FS25 grle: write the REMAPPED parcel grid (ids already permuted to field-number parity above)
    os.makedirs(os.path.join(OUT, "maps", "data"), exist_ok=True)
    binfmt.paint_grle(os.path.join(OUT, "maps", "data", "infoLayer_farmland.grle"), arr)

    # RENDER proof: buyable=green, non-buyable=red, none=black
    img = np.zeros((*arr.shape, 3), np.uint8)
    nb_mask = np.isin(arr, list(nb_ids))
    img[(arr > 0) & (~nb_mask)] = (40, 200, 40)
    img[nb_mask] = (220, 40, 40)
    os.makedirs(os.path.dirname(SHOT), exist_ok=True)
    Image.fromarray(img).save(SHOT)

    print(f"{len(rows)} farmlands | {len(nb_ids)} NON-buyable -> {sorted(nb_ids)}")
    print(f"non-buyable labels: {sorted({r['comment'] for r in rows if r['id'] in nb_ids})}")
    print(f"field-# parity: {len(f2f)} field parcels relabelled to their FS22 field numbers "
          f"(1..{max(f2f)}); non-field parcels pushed to {max(f2f)+1}+")
    print(f"rendered -> {SHOT}")


if __name__ == "__main__":
    main()
