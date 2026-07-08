"""
build_crops.py - PHASE (crops): bring WW's custom FS22 crops into the from-scratch FS25 map, fully wired to
GROW / HARVEST / SELL. The from-scratch map ships only 'meadow' as a farmable fruitType (its terrain FML renders the
base crops but they were never registered), so this ALSO completes the base crop registry.

FS25 crop = (1) a foliage package foliage/<crop>/<crop>.xml (FS25 `foliageType` = embedded <fruitType> economy +
<foliageLayer> growth-state -> geometry) + assets, (2) a <FoliageType> in the terrain fruits-FoliageMultiLayer, (3) a
<fruitType> ref in the map's fruitTypes.xml, (4) a <fillType> (+ categories) so it can be harvested/hauled/sold.

WW ships its OWN crop art under maps/foliage/<crop>/ (FS22 foliageType xml, close to FS25's). We:
  - classify each WW fruitType: BASE (already in the FML) / PORT (WW ships foliage) / NATIVE (FS25 ships foliage, e.g.
    onion) / skip;
  - PORT: copy WW's foliage/<crop>/ to the mod-ROOT /foliage/<crop>/ (loader resolves foliage refs mod-root-relative,
    NOT maps/), and convert <crop>.xml FS22->FS25 (embed the translated <fruitType> economy from WW maps_fruitTypes,
    derive FS25 per-state flags isGrowing/isHarvestReady/isWithered/isCut from the FS22 growth-state indices, fix
    distanceMap paths + foliageLayer attrs);
  - build maps/config/fruitTypes.xml (base $data refs kept as-is + PORT/NATIVE refs) and maps/config/fillTypes.xml
    (FS25 base + custom crop/windrow fills ported from WW + custom crops added to the handling/sell categories);
  - wire the terrain fruits FML (add FoliageType+File per new crop; bump numTypeIndexChannels 5->6 once >32 types) and
    point map.xml at the two config files.

Config-driven (wildwest.convert.json 'crops'); map-agnostic. Idempotent. Needs the i3d + map.xml present (from
`start`). Runs BEFORE build_placeables so the sale points can read these custom crop fills and accept them (custom
fruits sellable). Phase 2 (crop products into production points) is separate.  python tools/build_crops.py
"""
import os, sys, json, re, shutil
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

GT_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")                  # reverse-engineered .gdm codecs live here
# 11ch/6tic .gdm HEADER template (17 bytes) for gf.encode_full - only the header/range-split is read, not pixels.
GT_FRUITS = os.path.join(GT_TOOLS, "fruits_header_11ch6tic.gdm")

WW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import convert_env
CONV = json.load(open(os.path.join(WW, "tools", os.environ.get("MAP_CONVERT", "wildwest.convert.json")), encoding="utf-8"))
SRC = convert_env.source_dir(CONV)
SRC_MAPS = os.path.dirname(os.path.join(SRC, CONV["source"]["map_i3d"].replace("/", os.sep)))   # FS22 map-data dir (map-agnostic: dirname(map_i3d); mapUS maps nest under maps/mapUS)
WW_FRUIT = os.path.join(SRC_MAPS, "maps_fruitTypes.xml")
WW_FILL = os.path.join(SRC_MAPS, "maps_fillTypes.xml")
WW_FOLIAGE = os.path.join(SRC_MAPS, "foliage")
WW_HUD = os.path.join(SRC, "hud", "fillTypes")
FS25_DATA = os.environ.get("FS25_DATA", r"C:\Program Files (x86)\Steam\steamapps\common\Farming Simulator 25\data")
FS25_FOLIAGE = os.path.join(FS25_DATA, "foliage")
FS25_FILL = os.path.join(FS25_DATA, "maps", "maps_fillTypes.xml")
FS25_FRUIT = os.path.join(FS25_DATA, "maps", "maps_fruitTypes.xml")
OUT = os.path.join(WW, "out", CONV["identity"]["mod"])
MAPS = os.path.join(OUT, "maps")
I3D = os.path.join(MAPS, CONV["identity"]["i3d"])
CFG = CONV.get("crops", {})
# categories a raw crop joins so a combine can harvest it + trailers haul it + sell points take it. Windrows are bales.
CROP_CATS = CFG.get("categories", ["COMBINE", "BULK", "AUGERWAGON", "SHOVEL", "TRAINWAGON", "FARMSILO",
                                   "LOADINGVEHICLE", "SELLINGSTATION_FIELDFRUITS"])
WINDROW_CATS = CFG.get("windrow_categories", ["SELLINGSTATION_BALES"])
HARVEST_GROUPS = CFG.get("harvest_groups", {})                     # crop -> which HARVESTER fruitTypeCategories it joins
PLANT_CATS = CFG.get("plant_categories", ["SOWINGMACHINE", "PLANTER", "PLANTER_SMALL"])  # applied to EVERY custom crop
HUD_DIR = os.path.join(MAPS, "hud", "fillTypes")


def fruit_categories_section(custom_crops):
    """FS25 fruitTypeCategories control which HARVESTER/planter each fruit works with (GRAINHEADER=combine header,
    DIRECTCUTTER, SOWINGMACHINE, MOWER, ...). A map's <fruitTypeCategories> REPLACES the base list, so we emit the FULL
    base membership + our custom-crop additions: per-crop HARVEST groups (config crops.harvest_groups) + the PLANTing
    categories applied to EVERY custom crop (so any sower/planter can plant them). Returns the section lines (or [])."""
    assign = {}                                                    # CROP(upper) -> set(category names)
    for grp in HARVEST_GROUPS.values():
        cats = grp.get("categories", [])
        for crop in grp.get("crops", []):
            assign.setdefault(crop.upper(), set()).update(cats)
    for crop in custom_crops:                                      # every ported/native crop is sowable/plantable
        assign.setdefault(crop.upper(), set()).update(PLANT_CATS)
    if not assign:
        return []
    out = ["    <fruitTypeCategories>"]
    for c in ET.parse(FS25_FRUIT).getroot().iter("fruitTypeCategory"):
        name = c.get("name")
        members = (c.text or "").split()
        extra = [crop for crop, cats in sorted(assign.items()) if name in cats and crop not in members]
        out.append(f'        <fruitTypeCategory name="{name}">{" ".join(members + extra)}</fruitTypeCategory>')
    out.append("    </fruitTypeCategories>")
    return out


def ww_fruittypes():
    return {ft.get("name"): ft for ft in ET.parse(WW_FRUIT).getroot().iter("fruitType") if ft.get("name")}


def ww_filltypes():
    return {(ft.get("name") or "").upper(): ft for ft in ET.parse(WW_FILL).getroot().iter("fillType") if ft.get("name")}


def fs25_base_fills():
    return {(ft.get("name") or "").upper() for ft in ET.parse(FS25_FILL).getroot().iter("fillType") if ft.get("name")}


def fs25_base_crops():
    """STABLE set of crop names that FS25 ships by default (+ the non-fruit deco/grass layers) - used to classify which
    WW fruitTypes are CUSTOM, independent of the map i3d's FML (which we mutate, so it can't be the reference)."""
    base = {"grass", "meadow", "decoFoliage", "decoBush", "decoBushUS", "forestPlants", "waterPlants"}
    for ft in ET.parse(os.path.join(FS25_DATA, "maps", "maps_fruitTypes.xml")).getroot().iter("fruitType"):
        fn = ft.get("filename") or ""
        if fn:
            base.add(os.path.basename(fn).replace(".xml", "").lower())
    return base


def upgrade_fruits_gdm():
    """The from-scratch densityMap_fruits.gdm is 10-channel / 5-typeIndex (32 crop slots). >32 fruit types need 6
    typeIndex bits, so re-encode it to 11ch/6tic using GT's fruits map as the header template. PRESERVES painted
    grass/meadow: each cell's value is re-split from the 5-bit (typeIdx | state<<5) layout to the 6-bit
    (typeIdx | state<<6) layout. Idempotent (skips if already 11/6). Returns a status string."""
    if GT_TOOLS not in sys.path:
        sys.path.insert(0, GT_TOOLS)
    import numpy as np
    import gdm_fruits_codec as gf
    dst = os.path.join(MAPS, "data", "densityMap_fruits.gdm")
    h = gf.read_header(dst)
    if h["nch"] == 11 and h["ntic"] == 6:
        return "already 11/6"
    old = gf.decode_full(dst)                                       # value = typeIdx | state<<5 (5-bit typeIdx)
    typeIdx, state = old & 0x1F, old >> 5
    new = (typeIdx | (state << 6)).astype(old.dtype)               # -> 6-bit typeIdx layout
    gf.encode_full(new, GT_FRUITS, dst)                            # GT header = 11ch/6tic (range split at channel 6)
    chk = gf.decode_full(dst)
    assert np.array_equal(chk & 0x3F, typeIdx) and np.array_equal(chk >> 6, state), "fruits gdm upgrade round-trip mismatch"
    return f"10/5 -> 11/6 (preserved {int((typeIdx > 0).sum())} painted cells)"


def fruits_fml(root):
    """The terrain FoliageMultiLayer bound to densityMap_fruits.gdm (the crop layer)."""
    files = {f.get("fileId"): (f.get("filename") or "") for f in root.iter("File")}
    term = root.find(".//TerrainTransformGroup")
    for fml in term.iter("FoliageMultiLayer"):
        if files.get(fml.get("densityMapId"), "").endswith("densityMap_fruits.gdm"):
            return fml, files
    raise SystemExit("no fruits FoliageMultiLayer in the map i3d")


# --------------------------------------------------------------------------- fruitType economy translation

def translate_fruittype(ww):
    """WW FS22 <fruitType> -> FS25 <fruitType> XML text (for embedding in the foliage package)."""
    name = ww.get("name")
    cul = ww.find("cultivation"); har = ww.find("harvest"); gro = ww.find("growth")
    opt = ww.find("options"); win = ww.find("windrow"); mc = ww.find("mapColors")
    L = [f'<fruitType name="{name}" shownOnMap="{ww.get("shownOnMap", "true")}" '
         f'useForFieldMissions="{ww.get("useForFieldJob", "true")}">']
    if mc is not None:
        L.append(f'    <mapColors default="{mc.get("default")}" colorBlind="{mc.get("colorBlind", mc.get("default"))}" />')
    if win is not None:                                              # FS22 windrow name=<fill> -> FS25 fillType=<fill>
        L.append(f'    <windrow fillType="{win.get("name")}" litersPerSqm="{win.get("litersPerSqm", "1")}" />')
    if har is not None:
        h = f'    <harvest litersPerSqm="{har.get("literPerSqm", har.get("litersPerSqm", "0.5"))}"'
        if har.get("cutHeight"):
            h += f' cutHeight="{har.get("cutHeight")}"'
        if har.get("chopperTypeName"):
            h += f' chopperType="{har.get("chopperTypeName")}"'
        L.append(h + " />")
    L.append(f'    <growth resetsSpray="{gro.get("resetsSpray", "true") if gro is not None else "true"}" '
             f'growthRequiresLime="{gro.get("growthRequiresLime", "true") if gro is not None else "true"}" />')
    if opt is not None:
        L.append(f'    <soil lowDensityRequired="{opt.get("lowSoilDensityRequired", "false")}" '
                 f'increasesDensity="{opt.get("increasesSoilDensity", "false")}" '
                 f'consumesLime="{opt.get("consumesLime", "true")}" '
                 f'startSprayLevel="{opt.get("startSprayState", "0")}" />')
    if cul is not None:
        L.append(f'    <seeding directionSnapAngle="{cul.get("directionSnapAngle", "0")}" '
                 f'needsRolling="{cul.get("needsRolling", "false")}" '
                 f'litersPerSqm="{cul.get("seedUsagePerSqm", "0.02")}" isAvailable="true" />')
    L.append('    <cultivation isAllowed="true" />')
    if har is not None and har.get("chopperTypeName"):
        L.append(f'    <mulcher chopperType="{har.get("chopperTypeName")}" />')
    L.append('</fruitType>')
    return "\n    ".join(L)


def state_flags(name, idx):
    """FS25 per-foliageState flags, keyed off the WW state NAME (not the FS22 growth-state INDICES - those don't line up
    with the foliage state-list position for every crop: e.g. alfalfa's list-value 4 is 'harvest ready' but its FS22
    witheredState is also 4, which mis-flagged the harvestable state as withered). WW names states consistently:
    invisible / green {small,middle,big}[ N] / harvest ready / dead / harvested / cut[ N]. idx = 0-based list position."""
    n = " ".join((name or "").lower().split())
    if n == "dead" or "wither" in n:                               # over-ripe/dead - also a valid destruction target
        return ['isWithered="true"', 'isDestructedByWheel="true"', 'isDestructedByDisaster="true"']
    if "harvested" in n or n == "cut":                             # freshly-cut stubble (primary cut state) + destruction target
        return ['isCut="true"', 'isDestructedByWheel="true"', 'isDestructedByDisaster="true"']
    # everything else grows: invisible, green*, harvest ready, and regrowth ("cut 2"/"cut 3" for forage crops like alfalfa)
    f = ['isGrowing="true"']
    if idx <= 1:                                                   # earliest states: weedable + hoeable
        f += ['allowsWeeding="true"', 'allowsHoeing="true"']
    elif idx == 2:
        f.append('allowsWeeding="true"')
    if idx >= 2:                                                   # established plants take wheel/disaster damage
        f += ['isDestructibleByWheel="true"', 'isDestructibleByDisaster="true"']
    if "harvest" in n and "ready" in n:                            # THE harvestable state
        f += ['isHarvestReady="true"', 'groundType="HARVEST_READY_OTHER"']
    return f


def convert_foliage_xml(ww_xml_path, ww_ft):
    """WW FS22 foliageType xml -> FS25 foliageType xml text: embed the translated <fruitType>, drop FS22-only foliageLayer
    attrs + add FS25 ones, fix distanceMap paths (prepend the distanceTexturePath dir), and stamp FS25 per-state flags."""
    src = ET.parse(ww_xml_path).getroot()
    dist_dir = src.get("distanceTexturePath", "distance")
    layer = src.find("foliageLayer")
    la = layer.attrib
    lattr = (f'densityMapChannelOffset="{la.get("densityMapChannelOffset", "0")}" '
             f'numDensityMapChannels="{la.get("numDensityMapChannels", "4")}" '
             f'plantSeparation="1.0 0.4" plantOffset="0 0" plantLayoutRotation="true" '
             f'shapeSource="{la.get("shapeSource")}" alignsToSun="false"')
    out = ['<?xml version="1.0" encoding="utf-8"?>',
           '<foliageType xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
           'xsi:noNamespaceSchemaLocation="../../../shared/xml/schema/foliageType.xsd">',
           '    ' + translate_fruittype(ww_ft), '',
           f'    <foliageLayer {lattr}>']
    sd = layer.find("foliageStateDefaults")
    if sd is not None:
        # FS25 needs a non-zero block width/height or the foliage is ZERO-SIZED = invisible (WW's tobacco.xml omits them;
        # its plants never rendered). Default any missing dimension to 1.0.
        if not sd.get("width"):
            sd.set("width", "1.0")
        if not sd.get("height"):
            sd.set("height", "1.0")
        out.append('        ' + ET.tostring(sd, encoding="unicode").strip())
    for ld in layer.findall("foliageLodDefaults"):
        out.append('        ' + ET.tostring(ld, encoding="unicode").strip())
    for i, st in enumerate(layer.findall("foliageState")):
        flags = " ".join(state_flags(st.get("name"), i))
        dm = st.get("distanceMap")
        dm_attr = ""
        if dm:
            dm_attr = f' distanceMap="{dist_dir}/{dm}"' if "/" not in dm else f' distanceMap="{dm}"'
        shapes = st.findall("foliageShape")
        if not shapes:
            out.append(f'        <foliageState name="{st.get("name")}" {flags}{dm_attr} />')
            continue
        out.append(f'        <foliageState name="{st.get("name")}" {flags}{dm_attr}>')
        for sh in shapes:
            out.append('            <foliageShape>')
            for lod in sh.findall("foliageLod"):
                out.append(f'                <foliageLod blockShape="{lod.get("blockShape")}" />')
            out.append('            </foliageShape>')
        out.append('        </foliageState>')
    out += ['    </foliageLayer>', '</foliageType>', '']
    return "\n".join(out)


# --------------------------------------------------------------------------- fillTypes

def custom_filltype_xml(ww_ft, categories):
    """A clean FS25 <fillType> from WW's economy (name/title/mass/price/monthly factors) + local hud icon."""
    name = (ww_ft.get("name") or "").upper()
    title = ww_ft.get("title") or name.title()
    phys = ww_ft.find("physics"); eco = ww_ft.find("economy")
    mass = phys.get("massPerLiter", "0.5") if phys is not None else "0.5"
    ang = phys.get("maxPhysicalSurfaceAngle", "22") if phys is not None else "22"
    price = eco.get("pricePerLiter", "1.0") if eco is not None else "1.0"
    icon = f"hud_fill_{name.lower()}.dds"
    have_icon = os.path.exists(os.path.join(WW_HUD, icon))
    L = [f'    <fillType name="{name}" title="{escape(title)}" showOnPriceTable="true" unitShort="$l10n_unit_literShort">']
    L.append(f'        <physics massPerLiter="{mass}" maxPhysicalSurfaceAngle="{ang}" />')
    if have_icon:
        # FS25 fill icon = <image hud="..."/> (NOT <hud filename>, which the loader ignores -> default/wrong icon).
        # Map-local ref resolves relative to the maps/ dir; icons are copied to maps/hud/fillTypes/.
        L.append(f'        <image hud="hud/fillTypes/{icon}" />')
    L.append(f'        <economy pricePerLiter="{price}">')
    factors = eco.find("factors") if eco is not None else None
    if factors is not None:
        L.append('            <factors>')
        for fac in factors.findall("factor"):
            L.append(f'                <factor period="{fac.get("period")}" value="{fac.get("value")}" />')
        L.append('            </factors>')
    L.append('        </economy>')
    L.append('    </fillType>')
    return "\n".join(L), (icon if have_icon else None)


def build_filltypes(new_fills, categories_for):
    """Full map fillTypes.xml = FS25 base (all $data-absolute, copyable) + custom crop/windrow fills, with the customs
    added to the handling/sell categories so combines/trailers/sell points accept them. Copies needed hud icons."""
    base = open(FS25_FILL, encoding="utf-8-sig").read()
    icons = []
    blocks = []
    for name, ww_ft in new_fills.items():
        xml, icon = custom_filltype_xml(ww_ft, categories_for.get(name, CROP_CATS))
        blocks.append(xml)
        if icon:
            icons.append(icon)
    # insert custom fillTypes just before </fillTypes>
    base = re.sub(r"(\s*)</fillTypes>", "\n" + "\n".join(blocks) + r"\1</fillTypes>", base, count=1)
    # add each custom name to its categories (append to the category's text)
    for name, cats in categories_for.items():
        for cat in cats:
            base = re.sub(r'(<fillTypeCategory name="%s"[^>]*>)([^<]*)(</fillTypeCategory>)' % re.escape(cat),
                          lambda m: m.group(1) + (m.group(2).rstrip() + " " + name + " ") + m.group(3), base, count=1)
    os.makedirs(os.path.join(MAPS, "config"), exist_ok=True)
    open(os.path.join(MAPS, "config", "fillTypes.xml"), "w", encoding="utf-8").write(base)
    if icons:
        os.makedirs(HUD_DIR, exist_ok=True)
        for ic in icons:
            shutil.copy(os.path.join(WW_HUD, ic), os.path.join(HUD_DIR, ic))
    return len(new_fills), len(icons)


# --------------------------------------------------------------------------- main

def main():
    fruits = ww_fruittypes()
    fills = ww_filltypes()
    tree = ET.parse(I3D); root = tree.getroot()
    fml, files = fruits_fml(root)
    fs25_base = fs25_base_crops()                                   # STABLE reference (NOT the mutated FML)

    port, native, skip = [], [], []
    for name in fruits:
        if name.lower() in fs25_base:
            continue                                               # FS25 ships it by default (base crop / deco / grass)
        # PREFER FS25-native foliage over WW's port: FS25 registers its native crops (e.g. onion) GLOBALLY, so pointing
        # at WW's OWN copy (a different file, same fruitType name) makes the engine register a SECOND "onion" = a dupe.
        # Referencing FS25's canonical $data file de-dupes against that global registration (same file = same fruit).
        if os.path.exists(os.path.join(FS25_FOLIAGE, name, name + ".xml")):
            native.append(name)
        elif os.path.exists(os.path.join(WW_FOLIAGE, name, name + ".xml")):
            port.append(name)
        else:
            skip.append(name)

    # 0) idempotency: drop any prior custom-crop FoliageType + its map-local File ref (so re-classification/re-pointing
    #    takes effect - e.g. onion PORT->NATIVE) and remove a now-stale ported foliage folder.
    mine = set(port) | set(native)
    files_el0 = root.find("Files")
    for t in list(fml.findall("FoliageType")):
        if t.get("name") in mine:
            xmlid = t.get("foliageXmlId")
            fml.remove(t)
            for f in list(files_el0):
                if f.get("fileId") == xmlid and (f.get("filename") or "").startswith("foliage/"):
                    files_el0.remove(f)                            # only our map-local refs; never touch $data
    for crop in native:                                            # a crop now sourced from $data shouldn't keep a ported copy
        for old in (os.path.join(OUT, "foliage", crop), os.path.join(MAPS, "foliage", crop)):
            if os.path.isdir(old):
                shutil.rmtree(old, ignore_errors=True)
    if os.path.isdir(os.path.join(OUT, "foliage")):                # remove the old mod-ROOT /foliage location entirely
        shutil.rmtree(os.path.join(OUT, "foliage"), ignore_errors=True)   # (foliage now lives under maps/foliage/)

    # 1) PORT: copy WW foliage folder to maps/foliage/<crop>/ + convert <crop>.xml to FS25. The i3d FoliageType File ref
    #    and the map fruitType ref both resolve relative to the MAPS dir (the i3d lives in maps/), so foliage MUST sit at
    #    maps/foliage/ (NOT mod-root/foliage/, which the loader can't reach -> "Failed to load foliage type").
    for crop in port:
        s = os.path.join(WW_FOLIAGE, crop)
        d = os.path.join(MAPS, "foliage", crop)
        os.makedirs(d, exist_ok=True)
        for rt, _, fs in os.walk(s):
            rel = os.path.relpath(rt, s)
            for f in fs:
                if f == crop + ".xml":
                    continue                                       # regenerated below
                dd = os.path.join(d, rel) if rel != "." else d
                os.makedirs(dd, exist_ok=True)
                shutil.copy(os.path.join(rt, f), os.path.join(dd, f))
        open(os.path.join(d, crop + ".xml"), "w", encoding="utf-8").write(
            convert_foliage_xml(os.path.join(s, crop + ".xml"), fruits[crop]))

    # 2) terrain fruits FML: add a File + FoliageType per PORT (mod-root foliage/..) and NATIVE ($data foliage/..)
    maxfid = max((int(f.get("fileId")) for f in root.iter("File") if (f.get("fileId") or "").isdigit()), default=0)
    fid = maxfid + 1
    files_el = root.find("Files")
    have = {t.get("name") for t in fml.findall("FoliageType")}      # idempotent: skip crops already wired
    added = []
    for crop in port + native:
        if crop in have:
            continue
        ref = (f"foliage/{crop}/{crop}.xml" if crop in port else f"$data/foliage/{crop}/{crop}.xml")
        ET.SubElement(files_el, "File", {"fileId": str(fid), "filename": ref})
        ET.SubElement(fml, "FoliageType", {"name": crop, "foliageXmlId": str(fid)})
        added.append((crop, ref)); fid += 1
    # >32 fruit FoliageTypes need 6 typeIndex channels (2^5=32 slots); FML + gdm must agree (else "GDM wrong number of
    # channels" crash). FS25/Kansas fruits maps are 11ch/6tic; ours was generated 10ch/5tic. Bump the FML and re-encode
    # the gdm to match (preserving painted grass/meadow).
    n_types = len(fml.findall("FoliageType"))
    gdm_status = "unchanged (<=32 types)"
    if n_types > 32:
        fml.set("numChannels", "11"); fml.set("numTypeIndexChannels", "6"); fml.set("compressionChannels", "6")
        gdm_status = upgrade_fruits_gdm()
    tree.write(I3D, encoding="iso-8859-1", xml_declaration=True)

    # 3) fruitTypes.xml = every fruit FoliageType's foliage xml. THE TWO LOADERS USE DIFFERENT PREFIXES for the SAME file
    #    (verified vs Smoky Mountain, a working FS25 custom-crop map): the i3d FML File ref is relative to the i3d's dir
    #    ("foliage/<c>/<c>.xml" -> maps/foliage/...), but the map <fruitType filename> is relative to MOD-ROOT and must
    #    carry the map-dir prefix ("maps/foliage/<c>/<c>.xml"; Smoky uses "mapAS/foliage/..."). Bare "foliage/..." here
    #    silently fails to register the fruit (crop shows in Prices via its fillType but is NOT plantable). $data stays.
    fmap = {f.get("fileId"): f.get("filename") for f in root.iter("File")}
    refs = []
    for t in fml.findall("FoliageType"):
        fn = fmap.get(t.get("foliageXmlId"), "")
        if t.get("name") in ("decoFoliage", "decoBushUS", "forestPlants", "waterPlants", "decoBush"):
            continue                                               # deco layers are not farmable fruits
        refs.append("maps/" + fn if fn.startswith("foliage/") else fn)   # map-local -> mod-root-relative maps/foliage/..
    lines = ['<?xml version="1.0" encoding="utf-8" standalone="no" ?>', '<map>', '    <fruitTypes>']
    lines += [f'        <fruitType filename="{r}" />' for r in dict.fromkeys(refs)]
    lines += ['    </fruitTypes>']
    lines += fruit_categories_section(port + native)               # harvester/planter membership (combine header + sow/plant)
    lines += ['</map>', '']
    os.makedirs(os.path.join(MAPS, "config"), exist_ok=True)
    open(os.path.join(MAPS, "config", "fruitTypes.xml"), "w", encoding="utf-8").write("\n".join(lines))

    # 4) fillTypes.xml: base + custom crop fills + their windrows. Category assignment: raw crops -> handling/sell,
    #    windrows -> bale/sell.
    base_fills = fs25_base_fills()                                  # never redefine an FS25 base fill (e.g. STRAW windrow)
    new_fills = {}
    cat_for = {}
    for crop in port + native:
        up = crop.upper()
        if up in fills and up not in base_fills:
            new_fills[up] = fills[up]; cat_for[up] = CROP_CATS
        win = fruits[crop].find("windrow")                          # crop's windrow fill (hemp_windrow/alfalfa_windrow)
        if win is not None and win.get("name"):
            wu = win.get("name").upper()
            if wu in fills and wu not in base_fills and wu not in new_fills:
                new_fills[wu] = fills[wu]; cat_for[wu] = WINDROW_CATS
    nf, ni = build_filltypes(new_fills, cat_for)

    # 5) map.xml -> point at the config files (strip the inline meadow-only <fruitTypes> block first)
    mxp = os.path.join(MAPS, "map.xml"); mx = open(mxp, encoding="utf-8").read()
    mx = re.sub(r'[ \t]*<fruitTypes>\s*<fruitType\b.*?</fruitTypes>\s*\n?', '', mx, flags=re.S)
    for tag, ref in (("fruitTypes", "maps/config/fruitTypes.xml"), ("fillTypes", "maps/config/fillTypes.xml")):
        if re.search(rf'<{tag}\b[^>]*filename=', mx):
            mx = re.sub(rf'<{tag}\b[^>]*filename="[^"]*"\s*/>', f'<{tag} filename="{ref}" />', mx)
        else:
            mx = mx.replace("</map>", f'    <{tag} filename="{ref}" />\n</map>')
    open(mxp, "w", encoding="utf-8").write(mx)

    print(f"[crops] PORT {len(port)}: {port}")
    print(f"[crops] NATIVE (FS25 foliage) {len(native)}: {native}")
    if skip:
        print(f"[crops] skipped (no foliage assets) {len(skip)}: {skip}")
    print(f"[crops] fruits FML now {n_types} FoliageTypes (typeIdxCh={fml.get('numTypeIndexChannels')}); gdm {gdm_status}")
    print(f"[crops] fruitTypes.xml {len(dict.fromkeys(refs))} fruits | fillTypes.xml +{nf} custom fills ({ni} hud icons)")


if __name__ == "__main__":
    main()
