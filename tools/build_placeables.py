"""
build_placeables.py - PHASE 4b: WW functional placeables -> FS25. Two paths:

  * SELLING STATIONS (WW FS22 type="sellingStation") -> GENERATE a native FS25-schema station XML per sell point
    (maps/<out_subdir>/), referenced $mapdir$/maps/<out_subdir>/<name> (the shipping-FS25 Kansas pattern). Each
    carries WW's storeData <name> (correct price-screen label - fixes "Harbor doesn't appear") + broad
    fillTypeCategories (accepts EVERY FS25 crop/product, DLC-proof - fixes "crops can't be sold") on a base-game
    TRIGGER-ONLY i3d (no building -> WW's extracted buildings supply the visual). The placeable is offset-compensated
    (base i3d unload-node local offset + WW net-yaw recovered from GE's flipped euler) so the unload trigger lands on
    the EXACT FS22 spot. Idempotent (rewrites maps/<out_subdir>/).

  * OTHER placeables (barns/houses/gas/animal/production) -> FS25 base-game placeable by name: OVERRIDE table +
    auto-match against the FS25 $data/placeables index, SKIP for WW-custom with no analog.

Writes maps/placeables.xml (the map's default placeables). Positions from WW.
"""
import os, re, sys, json, math, shutil, collections, xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

WWREPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import convert_env
CONV = json.load(open(os.path.join(WWREPO, "tools", os.environ.get("MAP_CONVERT", "wildwest.convert.json")), encoding="utf-8"))
SRC = convert_env.source_dir(CONV)                                       # FS22 original (read-only source of truth)
SRC_MAPS = os.path.dirname(os.path.join(SRC, CONV["source"]["map_i3d"].replace("/", os.sep)))   # FS22 map-data dir (map-agnostic: dirname(map_i3d); mapUS maps nest under maps/mapUS)
FS22_DATA = os.environ.get("FS22_DATA", r"C:\Program Files (x86)\Steam\steamapps\common\Farming Simulator 22\data")  # FS22 install $data root
WW = os.path.join(SRC_MAPS, "placeables.xml")                     # the source map's own placeables list
FS25P = os.path.join(os.environ.get("FS25_DATA", r"C:\Program Files (x86)\Steam\steamapps\common\Farming Simulator 25\data"), "placeables")
FS25_DATA = os.path.dirname(FS25P)                               # $data root
OUT = os.path.join(WWREPO, "out", CONV["identity"]["mod"])
OUT_PXML = os.path.join(OUT, "maps", "placeables.xml")

# NON-selling placeable routing (map-specific, from config). Selling stations are generated via 'sellpoints'.
_PM = CONV.get("placeable_map", {})
OVERRIDE = {k: v for k, v in _PM.get("override", {}).items() if not k.startswith("_")}   # WW basename -> FS25 base-game path
SKIP = set(_PM.get("skip", []))                                                          # WW-custom, no FS25 analog
HDR = ['<?xml version="1.0" encoding="utf-8" standalone="no"?>',
       '<placeables version="1" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
       'xsi:noNamespaceSchemaLocation="https://validation.gdn.giants-software.com/xml/fs25/savegame_placeables.xsd">']

# --- selling-station generation (config-driven) ---
SELL = CONV.get("sellpoints", {})
CATS = SELL.get("categories", "SELLINGSTATION_FIELDFRUITS SELLINGSTATION_PRODUCTS SELLINGSTATION_PRODUCTSFOOD "
                              "SELLINGSTATION_BALES SELLINGSTATION_WOOD")
SUBDIR = SELL.get("out_subdir", "sellpoints_fs25")
VIS_MODEL = SELL.get("visible_model")                            # visible sell-platform i3d loaded onto the functional trigger base (FS22 pattern)
VIS_LINK = SELL.get("visible_model_link", "unloadNodeTrailer")   # node in the base i3d to attach the visible model to
VIS_OFF_Y = float(SELL.get("visible_model_offset_y", 0.0))       # sink the sell platform into the ground (top slightly proud; no tractor hump)
YAW_OFF = float(SELL.get("yaw_offset", 0.0))                     # extra yaw to orient the sell model (like the street-light arm)
FS25_FILLTYPES = set()                                           # valid FS25 fill-type names (to filter the FS22 lists to what FS25 has)
try:
    for _ft in ET.parse(os.path.join(FS25_DATA, "maps", "maps_fillTypes.xml")).getroot().iter("fillType"):
        if _ft.get("name"):
            FS25_FILLTYPES.add(_ft.get("name").upper())
except Exception:
    pass


def _custom_field_fruit_fills():
    """WW custom crop fills to make sellable at the general sale points. build_crops runs BEFORE this step and adds
    each custom crop to SELLINGSTATION_FIELDFRUITS in the MAP's fillTypes.xml; return those the FS25 base doesn't
    ship (hemp/tobacco/...). We append these to every EXPLICIT-fillType station (the broad-category fallback stations
    already accept them via the category), so the custom fruits can actually be sold."""
    p = os.path.join(OUT, "maps", "config", "fillTypes.xml")
    if not os.path.exists(p):
        return []
    try:
        root = ET.parse(p).getroot()
    except ET.ParseError:
        return []
    for cat in root.iter("fillTypeCategory"):
        if (cat.get("name") or "").upper() == "SELLINGSTATION_FIELDFRUITS":
            return [m.upper() for m in (cat.text or "").split() if m.upper() not in FS25_FILLTYPES]
    return []


CUSTOM_FILLS = _custom_field_fruit_fills()
SELL_SKIP = set(SELL.get("skip", []))                            # sellingStation-type points that are NOT crop sinks (manure etc.)
NAME_OVERRIDES = {k: v for k, v in SELL.get("name_overrides", {}).items() if not k.startswith("_")}
TEMPLATE = os.path.join(FS25P, SELL.get("template",
           "mapUS/sellingPoints/grainBargeTerminal01Triggers/grainBargeTerminal01Triggers.xml").replace("/", os.sep))
SCENE_TAGS = ("TransformGroup", "Shape", "Light", "AudioSource", "Camera")

# --- production points: WW productionPoint -> native FS25 productionPoint on the GENERIC production i3d, VISUAL HIDDEN ---
PROD = CONV.get("productions", {})
PROD_GENERIC = os.path.join(FS25P, PROD.get("generic_dir", "brandless/productionPointsGeneric").replace("/", os.sep))
PROD_GENERIC_REL = PROD.get("generic_dir", "brandless/productionPointsGeneric")   # $data-relative
PROD_SUBDIR = PROD.get("out_subdir", "production_fs25")
PROD_MAP = {k: v for k, v in PROD.get("map", {}).items() if not k.startswith("_")}   # WW basename -> FS25 generic folder
PROD_OFFSET = {k: v for k, v in PROD.get("trigger_offset", {}).items() if not k.startswith("_")}  # basename -> [dx,dz] fine-tune nudge
# Align the FS25 generic's trigger nodes to the FS22 point's authored positions, matched by SEMANTIC function - NOT by
# node-id name, which varies wildly (the unload node is 'exactFillRootNode' in dairy/bakery, 'exactFillNode' in
# grainMill/oilMill/sugarMill, 'unloadTrigger' in grapeProcessingUnit, and wood/fiber points sawmill/carpentry/spinnery
# have NO fill unload at all - they take input via woodTrigger/palletTrigger/baleTrigger). Every INPUT trigger (fill/wood/
# pallet/bale + their AI/activation helpers) is snapped to the FS22 apron (first present of INPUT_FS22); player + markers
# map to their same-named FS22 nodes. Each spec = (FS25 element tag, attribute, ordered FS22 i3dMapping id fallback);
# findall so repeated elements all move. Result: at the raw WW origin every trigger lands where FS22 had it.
INPUT_FS22 = ("unloadTrigger", "woodTrigger", "baleTrigger", "palletTrigger")   # FS22 apron position (first present)
_AI_FS22 = ("unloadTriggerAINode", "sellingStationAINode") + INPUT_FS22
TRIG_ROLES = [
    (".//unloadTrigger", "exactFillRootNode",     INPUT_FS22),
    (".//woodTrigger",   "triggerNode",           INPUT_FS22),
    (".//woodTrigger",   "activationTriggerNode", INPUT_FS22),
    (".//palletTrigger", "triggerNode",           INPUT_FS22),
    (".//baleTrigger",   "triggerNode",           INPUT_FS22),
    (".//unloadTrigger", "aiNode",                _AI_FS22),
    (".//woodTrigger",   "aiNode",                _AI_FS22),
    (".//palletTrigger", "aiNode",                _AI_FS22),
    (".//baleTrigger",   "aiNode",                _AI_FS22),
    (".//playerTrigger", "node",                  ("playerTrigger",)),
    (".//hotspot",       "linkNode",              ("unloadTriggerMarker", "woodSellTriggerMarker", "unloadTrigger")),
    (".//hotspot",       "teleportNode",          ("playerTriggerMarker", "playerTrigger")),
]


def resolve_ref(ref, maps):
    """A node ref in a placeable xml is either an i3dMapping id or a direct node path ('0>8|0'). -> the node path."""
    if not ref:
        return None
    if ref in maps:
        return maps[ref]
    return ref if (">" in ref or "|" in ref) else None


def _generic_outputs():
    """{FS25 generic-production folder: set(OUTPUT fill types)} - the auto-match table."""
    out = {}
    if os.path.isdir(PROD_GENERIC):
        for d in sorted(os.listdir(PROD_GENERIC)):
            x = os.path.join(PROD_GENERIC, d, d + ".xml")
            if os.path.exists(x):
                s = {o.get("fillType", "").upper() for o in ET.parse(x).getroot().iter("output") if o.get("fillType")}
                if s:
                    out[d] = s
    return out


PROD_GENERIC_OUTPUTS = _generic_outputs()


def match_generic(wr):
    """Pick the FS25 generic-production folder whose OUTPUT fill types best overlap this WW point's outputs. Robust to
    name differences (WW raisinFactory -> FS25 grapeProcessingUnit; both output GRAPEJUICE+RAISINS). None if nothing
    overlaps (animal-food mixers, tree nursery -> no FS25 production analog -> handled as a normal placeable/skip)."""
    ww = {o.get("fillType", "").upper() for o in wr.iter("output") if o.get("fillType")}
    best, score = None, 0
    for d, outs in PROD_GENERIC_OUTPUTS.items():
        n = len(ww & outs)
        if n > score:
            best, score = d, n
    return best


def _placeholder_positions():
    """(x,z) of every BUILDING WW dropped in a top-level building group (config scene_groups.buildings) - i.e. the
    base-game production/sell bodies WW placed in its 'placeholders' group. Lets us decide, per production point,
    whether WW actually ships a building there (hide the FS25 generic + show WW's) or not (keep the FS25 generic
    visible - some points, e.g. grainMill, have no WW building and relied on the base-game model)."""
    pts = []
    i3d = os.path.join(SRC, CONV["source"]["map_i3d"].replace("/", os.sep))
    groups = set(CONV.get("scene_groups", {}).get("buildings", []))
    try:
        scene = ET.parse(i3d).getroot().find("Scene")
    except Exception:
        return pts
    for top in scene:
        if top.get("name") in groups:
            for c in top:
                t = (c.get("translation") or "0 0 0").split()
                if len(t) >= 3:
                    pts.append((float(t[0]), float(t[2])))
    return pts


PLACEHOLDERS_XZ = _placeholder_positions()


def has_ww_building(pos, tol=5.0):
    """True if WW placed a building within tol metres of this point (so we hide the FS25 generic and show WW's)."""
    px, pz = float(pos[0]), float(pos[2])
    return any(abs(px - x) < tol and abs(pz - z) < tol for x, z in PLACEHOLDERS_XZ)


def fs22_xml(fn):
    """WW placeable ref '$moddir$FS22_WildWest_16x/maps/.../x.xml' -> the real file under the FS22 source dir, or None."""
    if not fn.startswith("$moddir$") or "/" not in fn:
        return None
    rel = fn.split("/", 1)[1]                                     # drop '$moddir$<modname>/'
    p = os.path.join(SRC, rel.replace("/", os.sep))
    return p if os.path.exists(p) else None


def net_yaw(rot):
    """WW rotation 'rx ry rz' -> net Y yaw. GE round-trips a yaw to a FLIPPED euler ('rx=+-180, rz=+-180'); recover it."""
    r = (rot or "0 0 0").split() + ["0", "0", "0"]
    rx, ry, rz = float(r[0]), float(r[1]), float(r[2])
    return ry + 180.0 if (abs(abs(rx) - 180) < 1 and abs(abs(rz) - 180) < 1) else ry


def node_offset(i3d_path, node_path):
    """Local translation of a node (i3dMapping path 'L>a|b|c') relative to the placeable root = sum of translations
    from the top scene node down. (Trigger i3ds are shallow + axis-aligned, so summing translations is exact.)"""
    scene = ET.parse(i3d_path).getroot().find("Scene")
    top = [c for c in scene if c.tag in SCENE_TAGS]
    left, _, rest = node_path.partition(">")
    node = top[int(left)]
    off = [0.0, 0.0, 0.0]

    def add(n):
        t = (n.get("translation") or "0 0 0").split()
        for i in range(3):
            off[i] += float(t[i])
    add(node)
    for idx in (rest.split("|") if rest else []):
        node = [c for c in node if c.tag in SCENE_TAGS][int(idx)]
        add(node)
    return off


def node_chain(scene, node_path):
    """Return the list of scene nodes from the top scene child down to the target (i3dMapping path 'L>a|b|c')."""
    top = [c for c in scene if c.tag in SCENE_TAGS]
    left, _, rest = node_path.partition(">")
    node = top[int(left)]
    chain = [node]
    for idx in (rest.split("|") if rest else []):
        node = [c for c in node if c.tag in SCENE_TAGS][int(idx)]
        chain.append(node)
    return chain


def base_i3d(placeable_root, data_dir):
    """The placeable's <base><filename> resolved to a real i3d path (a $data ref -> data_dir)."""
    ref = (placeable_root.find(".//base/filename").text or "").strip()
    return os.path.join(data_dir, ref[len("$data/"):].replace("/", os.sep)) if ref.startswith("$data/") else ref


def align_triggers(root, generic_folder, ww_src):
    """Rewrite the hidden FS25 generic i3d's trigger leaf nodes to the FS22 point's authored LOCAL positions so, placed
    at the raw WW origin, unload/pallet/player triggers + markers land on the exact FS22 spots (apron + door), not
    inside WW's building. Roles are matched by FUNCTION (TRIG_ROLES) so this works whatever each generic names its nodes;
    any role the FS22 point doesn't define is left at the generic's default."""
    gen_root = ET.parse(os.path.join(PROD_GENERIC, generic_folder, generic_folder + ".xml")).getroot()
    fs25_maps = {m.get("id"): m.get("node") for m in gen_root.iter("i3dMapping")}
    fs22_root = ET.parse(ww_src).getroot()
    fs22_maps = {m.get("id"): m.get("node") for m in fs22_root.iter("i3dMapping")}
    fs22_i3d = base_i3d(fs22_root, FS22_DATA)
    scene = root.find("Scene")
    n = 0
    for findexpr, attr, fs22_ids in TRIG_ROLES:
        fs22_id = next((k for k in fs22_ids if k in fs22_maps), None)   # first FS22 node that exists
        if not fs22_id:
            continue
        tgt = node_offset(fs22_i3d, fs22_maps[fs22_id])
        for el in gen_root.findall(findexpr):                          # every matching FS25 element (repeats move too)
            fs25_path = resolve_ref(el.get(attr), fs25_maps)
            if not fs25_path:
                continue
            chain = node_chain(scene, fs25_path)
            anc = [0.0, 0.0, 0.0]                         # summed ancestor translations (leaf excluded)
            for nd in chain[:-1]:
                t = (nd.get("translation") or "0 0 0").split()
                for j in range(3):
                    anc[j] += float(t[j])
            chain[-1].set("translation", "%g %g %g" % (tgt[0] - anc[0], tgt[1] - anc[1], tgt[2] - anc[2]))
            n += 1
    return n


def ww_sell_config(wr):
    """READ the FS22 station's actual accepted products + PRICE INDEX. Returns {FILLTYPE(upper): (priceScale, greatDemand,
    disablePriceDrop)}, filtered to fill types FS25 has. Union of every <fillType> price entry + every unloadTrigger's
    fillTypes list (accepted-but-unpriced types default to index 1)."""
    fills = {}
    for ft in wr.findall(".//sellingStation/fillType"):
        n = (ft.get("name") or "").upper()
        if n and (not FS25_FILLTYPES or n in FS25_FILLTYPES):
            fills[n] = (ft.get("priceScale", "1"), ft.get("supportsGreatDemand", "true"), ft.get("disablePriceDrop", "false"))
    for ut in wr.findall(".//sellingStation/unloadTrigger"):
        for t in (ut.get("fillTypes") or "").split():
            n = t.upper()
            if n and (not FS25_FILLTYPES or n in FS25_FILLTYPES) and n not in fills:
                fills[n] = ("1", "true", "false")
    return fills


def selling_station_xml(fills):
    """Build an FS25 <sellingStation> block that reproduces the FS22 acceptance + per-type price index (relative to 1)."""
    names = " ".join(sorted(fills))
    entries = "\n".join(f'        <fillType name="{n}" priceScale="{p}" supportsGreatDemand="{g}" disablePriceDrop="{d}" />'
                        for n, (p, g, d) in sorted(fills.items()))
    return ('    <sellingStation supportsExtension="false" litersForFullPriceDrop="200000" fullPriceRecoverHours="48">\n'
            f'        <unloadTrigger exactFillRootNode="unloadNodeTrailer" fillTypes="{names}" priceScale="1" aiNode="unloadNodeAI"/>\n'
            f'        <palletTrigger triggerNode="palletTrigger" fillTypes="{names}" priceScale="1" />\n'
            f'        <baleTrigger triggerNode="baleTrigger" deleteLitersPerSecond="10000" fillTypes="{names}" priceScale="1" />\n'
            f'{entries}\n'
            '    </sellingStation>')


def unload_offset():
    """Parse the station TEMPLATE to find its exactFillRootNode and that node's local offset in its base i3d."""
    tp = ET.parse(TEMPLATE).getroot()
    st = tp.find(".//sellingStation")
    efn = st.find("unloadTrigger").get("exactFillRootNode")
    # exactFillRootNode is either a direct node path ("0>3", sellingStationGeneric) or a named i3dMapping id (barge)
    node_path = efn if (">" in efn or "|" in efn) else {m.get("id"): m.get("node") for m in tp.iter("i3dMapping")}[efn]
    base_ref = tp.find(".//base/filename").text.strip()
    base_i3d = os.path.join(FS25_DATA, base_ref[len("$data/"):].replace("/", os.sep))
    return node_offset(base_i3d, node_path)


def make_hidden_i3d(generic_folder, dst_path, ww_src=None):
    """Copy the FS25 generic production i3d, HIDE its visual (every renderable Shape -> nonRenderable; occluders off)
    so WW's own extracted building shows through, and repoint its relative <Shapes>/<File> refs to $data so it still
    loads its geometry/collision from the base game. When ww_src is given, RE-POSITION the trigger nodes to the FS22
    point's authored locals (align_triggers) so they match WW's building. Trigger nodes are otherwise untouched."""
    src = os.path.join(PROD_GENERIC, generic_folder, generic_folder + ".i3d")
    base = "$data/placeables/" + PROD_GENERIC_REL + "/" + generic_folder + "/"   # generic_dir is relative to placeables/
    tree = ET.parse(src); root = tree.getroot()
    # <Shapes externalShapesFile> does NOT expand $data (unlike <File>) -> the mesh/COLLISION geometry fails to load and
    # every trigger becomes an empty transform group (markers show but nothing triggers). COPY the .shapes next to the
    # hidden i3d and reference it relatively.
    shapes_name = os.path.splitext(os.path.basename(dst_path))[0] + ".i3d.shapes"   # unique per WW point (matches dst)
    shutil.copy(os.path.join(PROD_GENERIC, generic_folder, generic_folder + ".i3d.shapes"),
                os.path.join(os.path.dirname(dst_path), shapes_name))
    for sh in root.iter("Shapes"):
        sh.set("externalShapesFile", shapes_name)
    for fl in root.iter("File"):
        f = fl.get("filename") or ""
        if f and not f.startswith("$") and not (len(f) > 1 and f[1] == ":"):
            fl.set("filename", base + f)
    for s in root.iter("Shape"):
        nm = (s.get("name") or "").lower()
        if s.get("nonRenderable") != "true":
            s.set("nonRenderable", "true")               # hide the FS25 generic model
        if s.get("occluder") == "true":
            s.set("occluder", "false")                   # a hidden occluder would wrongly cull WW's visible building
        if "collision" in nm:                            # generic building solid hull (collision/tipCollision) -> disable:
            for k in ("collisionMask", "collisionFilterGroup", "collisionFilterMask"):
                s.attrib.pop(k, None)                     # WW's extracted building supplies the real collision; leaving this
            s.set("collision", "false")                  # invisible hull is what got the player STUCK. Trigger shapes
            #                                              (exactFillRootNode/palletTrigger) keep their collision - not matched.
    moved = align_triggers(root, generic_folder, ww_src) if ww_src else 0   # snap triggers to FS22 authored positions
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    tree.write(dst_path, encoding="utf-8", xml_declaration=True)
    return moved


def gen_production_xml(generic_folder, ww_root, ww_name, base_ref):
    """FS25 generic production XML (its correct triggers/pallet-spawner/POI/i3dMappings) with base -> base_ref (our
    hidden i3d when WW has a building here, else the $data generic i3d = keep it visible), <name> -> WW's, and
    <productions>+<storage> swapped for the FS22 originals (faithful input/output cycles)."""
    txt = open(os.path.join(PROD_GENERIC, generic_folder, generic_folder + ".xml"), encoding="utf-8").read()
    # keep the WW name only if it's a real literal; a raw FS22 $l10n_ key won't resolve in FS25's namespace, so leave
    # the FS25 generic's own (valid) name in that case.
    if ww_name and not ww_name.startswith("$l10n"):
        txt = re.sub(r"<name>.*?</name>", "<name>%s</name>" % escape(ww_name), txt, count=1, flags=re.S)
    txt = re.sub(r"(<base>.*?<filename>).*?(</filename>)",
                 lambda m: m.group(1) + base_ref + m.group(2), txt, count=1, flags=re.S)
    for tag in ("productions", "storage"):
        el = ww_root.find(".//productionPoint/" + tag)
        if el is not None:
            block = ET.tostring(el, encoding="unicode").strip()
            txt = re.sub(r"<%s\b.*?</%s>" % (tag, tag), lambda m: block, txt, count=1, flags=re.S)
    return txt


def main():
    idx = {}
    for root, dirs, fs in os.walk(FS25P):
        for f in fs:
            if f.endswith(".xml") and f not in idx:
                idx[f] = os.path.relpath(os.path.join(root, f), FS25P).replace(os.sep, "/")

    tmpl_txt = open(TEMPLATE, encoding="utf-8").read()
    ux, uy, uz = unload_offset()
    outdir = os.path.join(OUT, "maps", SUBDIR)
    proddir = os.path.join(OUT, "maps", PROD_SUBDIR)
    for d in (outdir, proddir):
        if os.path.isdir(d):
            for f in os.listdir(d):
                os.remove(os.path.join(d, f))                    # idempotent: clear prior generated files
        os.makedirs(d, exist_ok=True)

    r = ET.parse(WW).getroot()
    lines = list(HDR)
    placed = collections.Counter(); skipped = collections.Counter(); stations = collections.Counter()
    prods = collections.Counter(); i = 0
    for pl in r.iter("placeable"):
        fn = pl.get("filename") or ""
        name = os.path.basename(fn)
        pos = (pl.get("position") or "0 0 0").split()
        src = fs22_xml(fn)

        # 1) SELLING STATION -> generate a native FS25 station carrying WW's name + broad categories, exact spot
        if src:
            try:
                wr = ET.parse(src).getroot()
            except ET.ParseError:
                wr = None
            if wr is not None and wr.get("type") == "sellingStation" and name not in SELL_SKIP:
                nm_el = wr.find(".//storeData/name")
                ww_name = (nm_el.text or name).strip() if nm_el is not None else name
                ww_name = NAME_OVERRIDES.get(name, ww_name)      # bake literals for FS22 base-game $l10n_* keys
                yaw = net_yaw(pl.get("rotation")) + YAW_OFF      # orient the sell model (+CCW, like the street-light arm)
                yaw = (yaw + 180.0) % 360.0 - 180.0              # normalize to [-180,180) - out-of-range yaw hangs the load
                th = math.radians(yaw)
                rx = ux * math.cos(th) + uz * math.sin(th)       # R_y(yaw) * unload offset
                rz = -ux * math.sin(th) + uz * math.cos(th)
                px = float(pos[0]) - rx                           # place so the unload node lands on the WW spot
                pz = float(pos[2]) - rz
                txt = re.sub(r"<name>.*?</name>", "<name>%s</name>" % escape(ww_name), tmpl_txt, count=1, flags=re.S)
                # reproduce the FS22 station's ACTUAL accepted products + price index (relative to 1); fall back to
                # broad categories only if the FS22 station declared no fill config
                fills = ww_sell_config(wr)
                if fills:
                    for cf in CUSTOM_FILLS:                       # make WW custom crops sellable at every explicit-fillType station too
                        fills.setdefault(cf, ("1", "true", "false"))
                    txt = re.sub(r"<sellingStation.*?</sellingStation>", lambda m: selling_station_xml(fills), txt, flags=re.S)
                else:
                    txt = re.sub(r'fillType(?:Categories|s)="[^"]*"', 'fillTypeCategories="%s"' % CATS, txt)
                if VIS_MODEL:                                     # attach the visible sell-platform model at the unload node (FS22 dynamicallyLoadedPart pattern)
                    dlp = (f'    <dynamicallyLoadedParts>\n'
                           f'        <dynamicallyLoadedPart node="0" linkNode="{VIS_LINK}" position="0 {VIS_OFF_Y:g} 0" filename="{VIS_MODEL}" />\n'
                           f'    </dynamicallyLoadedParts>\n')
                    txt = txt.replace("</base>", "</base>\n" + dlp, 1)
                open(os.path.join(outdir, name), "w", encoding="utf-8").write(txt)
                i += 1
                # $mapdir$ = the MOD ROOT (verified in-game), and our map data lives under maps/ -> $mapdir$/maps/<subdir>
                lines.append(f'    <placeable filename="$mapdir$/maps/{SUBDIR}/{name}" uniqueId="ww_{i}" '
                             f'position="{px:.3f} {float(pos[1]):.3f} {pz:.3f}" rotation="0 {yaw:g} 0" farmId="0"/>')
                stations[ww_name] += 1
                continue

            # 1b) PRODUCTION POINT -> native FS25 productionPoint on the GENERIC i3d, FS22 recipes+storage injected.
            #     The FS25 generic is chosen by OUTPUT fill-type overlap (config PROD_MAP is an override) so this works
            #     for EVERY WW production point, not just dairy. If WW ships a building at this spot (placeholders group),
            #     HIDE the FS25 generic visual + snap its triggers to the FS22 authored spots so WW's building shows with
            #     working triggers (drops the base-game double-up). If WW ships NO building here (e.g. grainMill), keep
            #     the FS25 generic VISIBLE (native triggers) - it IS the intended building.
            if wr is not None and wr.get("type") == "productionPoint":
                gf = PROD_MAP.get(name) or match_generic(wr)
                if gf:
                    stem = os.path.splitext(name)[0]
                    show_ww = has_ww_building(pos)               # WW building present -> hide FS25 generic + align
                    if show_ww:
                        moved = make_hidden_i3d(gf, os.path.join(proddir, stem + "_hidden.i3d"), ww_src=src)
                        base_ref = "maps/" + PROD_SUBDIR + "/" + stem + "_hidden.i3d"   # mod-root-relative hidden i3d
                    else:
                        moved = 0
                        base_ref = "$data/placeables/" + PROD_GENERIC_REL + "/" + gf + "/" + gf + ".i3d"  # visible base
                    nm_el = wr.find(".//storeData/name")
                    ww_name = (nm_el.text or name).strip() if nm_el is not None else name
                    ww_name = NAME_OVERRIDES.get(name, ww_name)
                    open(os.path.join(proddir, name), "w", encoding="utf-8").write(gen_production_xml(gf, wr, ww_name, base_ref))
                    rot = (pl.get("rotation") or "0 0 0").split()
                    ry = rot[1] if len(rot) > 1 else "0"
                    # optional local-frame nudge on top of the FS22-aligned triggers (default [0,0])
                    dx, dz = PROD_OFFSET.get(name, [0.0, 0.0])
                    th = math.radians(float(ry))
                    px = float(pos[0]) + dx * math.cos(th) + dz * math.sin(th)
                    pz = float(pos[2]) - dx * math.sin(th) + dz * math.cos(th)
                    i += 1
                    lines.append(f'    <placeable filename="$mapdir$/maps/{PROD_SUBDIR}/{name}" uniqueId="ww_{i}" '
                                 f'position="{px:.3f} {pos[1]} {pz:.3f}" rotation="0 {ry} 0" farmId="0"/>')
                    prods[ww_name] += 1
                    vis = f"WW building, {moved} triggers aligned" if show_ww else "no WW building -> FS25 generic visible"
                    print(f"  [prod] {name:22s} -> generic '{gf}' ({vis})")
                    continue

        # 2) OTHER placeable -> FS25 base-game by name
        if name in SKIP:
            skipped[name] += 1; continue
        fs25 = OVERRIDE.get(name) or idx.get(name)
        if not fs25:
            skipped[name] += 1; continue
        rot = (pl.get("rotation") or "0 0 0").split()
        ry = rot[1] if len(rot) > 1 else "0"
        i += 1
        lines.append(f'    <placeable filename="$data/placeables/{fs25}" uniqueId="ww_{i}" '
                     f'position="{pos[0]} {pos[1]} {pos[2]}" rotation="0 {ry} 0" farmId="0"/>')
        placed[name] += 1

    lines += ['</placeables>', '']
    open(OUT_PXML, "w", encoding="utf-8").write("\n".join(lines))

    # storeItems.xml: FS25 requires EVERY map-placed placeable to be registered as a store item, else it logs
    # "not defined in store items" and the placeable never instantiates (no model, no sell trigger, no map POI).
    # Base maps list all of theirs. Register every placeable we emit (deduped), + reference it from map.xml.
    fns, seen = [], set()
    for fn in re.findall(r'filename="([^"]+)"', "\n".join(lines)):
        # storeItems wants a MOD-ROOT-RELATIVE path (Huron pattern), NOT $mapdir$ (which doesn't resolve here);
        # keep $data refs as-is. Map-local placeables MUST be registered here or they never instantiate.
        store_fn = fn[len("$mapdir$/"):] if fn.startswith("$mapdir$/") else fn
        if store_fn not in seen:
            seen.add(store_fn); fns.append(store_fn)
    si = ['<?xml version="1.0" encoding="utf-8" standalone="no"?>', '<storeItems>']
    si += [f'    <storeItem xmlFilename="{fn}"/>' for fn in fns]
    si += ['</storeItems>', '']
    open(os.path.join(OUT, "maps", "storeItems.xml"), "w", encoding="utf-8").write("\n".join(si))
    mapxml = os.path.join(OUT, "maps", "map.xml")
    mx = open(mapxml, encoding="utf-8").read()
    if "<storeItems " not in mx:                              # inject the map.xml reference once (idempotent)
        mx = mx.replace("</map>", '    <storeItems filename="maps/storeItems.xml" />\n</map>')
        open(mapxml, "w", encoding="utf-8").write(mx)
    print(f"storeItems.xml: {len(fns)} placeables registered + map.xml referenced")
    print(f"placeables.xml: {i} total | {sum(stations.values())} generated stations | "
          f"{sum(prods.values())} production points | "
          f"{sum(placed.values())} base-game ({len(placed)} types) | skipped {sum(skipped.values())} ({len(skipped)} types)")
    if prods:
        print("  production points (WW recipes, WW visual):", dict(prods))
    print(f"  unload-offset={ux:.3f} {uy:.3f} {uz:.3f}  categories='{CATS}'")
    print("  stations:", dict(stations))
    print("  skipped (WW-custom / no FS25 analog):", dict(skipped))


if __name__ == "__main__":
    main()
