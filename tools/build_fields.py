"""
build_fields.py - GENERATE the FS25 field system from the FS22 ORIGINAL (read -> understand -> generate).
Reads WW's 82 field polygons (ww_fields) and generates FS25 field nodes (polygonPoints + nameIndicator),
the FieldUtil.onCreate registration, per-field UserAttributes, and a BLANK fields.xml (FS25 then populates every
field from its polygon = default crops + contracts; per-field <ground>/<fruit> entries suppress that), injected
into the engine-skeleton i3d. No copying.
"""
import os, sys, json
import xml.etree.ElementTree as ET
sys.path.insert(0, os.path.dirname(__file__))
import ww_fields

WW = os.environ.get("FS_CONVERT_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import convert_env
CONV = json.load(open(os.path.join(WW, "tools", os.environ.get("MAP_CONVERT", "wildwest.convert.json")), encoding="utf-8"))
FS22 = convert_env.source_dir(CONV)
OUT = os.path.join(WW, "out", CONV["identity"]["mod"])
I3D = CONV["identity"]["i3d"]
MAP_M = CONV.get("cfg", {}).get("map_m", 8192)


def fmt(v):
    return " ".join(f"{x:.6g}" for x in v)


def owned_field_nums(fields):
    """Field numbers whose parcel is defaultFarmProperty (owned by the player at game start). FS25 auto-populates only
    NPC/contract fields; the player's OWN fields are NOT auto-initialised and sit as raw terrain unless fields.xml gives
    them a <ground> state (verified: base mapUS config/fields.xml ships PLOWED/HARVEST_READY entries for exactly its 4
    starter fields, and nothing for NPC fields). We find the owned fields by majority-sampling the GENERATED farmland
    grid under each polygon (robust to the field-#/farmland-id parity remap being skipped on some maps)."""
    fx = os.path.join(OUT, "maps", "farmlands.xml")
    grid = os.path.join(OUT, "maps", "data", "infoLayer_farmland.grle")
    if not (os.path.exists(fx) and os.path.exists(grid)):
        return []
    import re
    default_ids = {int(m.group(1)) for m in re.finditer(
        r'<farmland id="(\d+)"[^/]*defaultFarmProperty="true"', open(fx, encoding="utf-8").read())}
    if not default_ids:
        return []
    import numpy as np
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")); import grle_codec
    from shapely.geometry import Polygon, Point
    arr = np.asarray(grle_codec.decode(open(grid, "rb").read())[0]); N = arr.shape[0]
    def px(w): return min(max(int((w + MAP_M / 2) / MAP_M * N), 0), N - 1)
    owned = []            # (creation_index, farmland_id) for each field on an owned parcel
    for idx, f in enumerate(fields, 1):   # idx = 1-based position in the fields group = the id FS25 assigns the field
        poly = Polygon(f["polygon"]); minx, minz, maxx, maxz = poly.bounds
        votes = {}
        for wx in np.linspace(minx, maxx, 9):
            for wz in np.linspace(minz, maxz, 9):
                if poly.contains(Point(wx, wz)):
                    v = int(arr[px(wz), px(wx)])
                    if v > 0:
                        votes[v] = votes.get(v, 0) + 1
        if not votes:
            cx, cz = poly.centroid.coords[0]; votes = {int(arr[px(cz), px(cx)]): 1}
        fid_farm = max(votes, key=votes.get)
        if fid_farm in default_ids:
            owned.append((idx, fid_farm))
    # Only emit owned-field entries when each owned field sits on its OWN farmland (field-#/farmland-id parity holds).
    # On parity-SKIP maps (West End: fields 31-35 all share farmland 81) multiple entries collide on one farmland and
    # FS25's FieldManager rejects them ("There already exists field 'N' on farmland 'M'") then stalls at shader compile.
    # In that case fall back to a BLANK fields.xml (owned fields just won't be pre-plowed) so the map still loads.
    farmlands = [fl for _, fl in owned]
    if len(set(farmlands)) != len(farmlands):
        print(f"fields: owned-field layout SKIPPED - {len(owned)} owned field(s) share farmland parcels "
              f"(no 1:1 parity); fields.xml left blank to avoid FieldManager conflicts")
        return []
    # fields.xml fieldId = the field's CREATION INDEX (1-based position in the fields group). FS25 keys fields by that
    # index, NOT the node name or farmland id (log-verified: node 'field33' becomes 'Field 31' because it's the 31st
    # field created). Using the wrong key silently fails to attach the <ground> state -> owned property, no workable
    # ground ("Missing area for FieldUpdateTask"). On sequential maps (WW) index == node number so this is unchanged.
    return sorted(idx for idx, _ in owned)


def bake_owned_ground(fields):
    """FS25 auto-generates workable field ground only for NPC/contract fields. The player's OWNED fields on a parity-
    broken parcel (field-id != farmland-id, e.g. West End's merged fields 30/31 on farmlands 95/96) get the parcel +
    label but NO cultivable ground from fields.xml alone (fields.xml's runtime <ground> gen relies on that parity). So
    BAKE tilled ground into densityMap_ground under each owned field polygon - the same terrainDetail value gen_data
    uses for the from-scratch starter field (GROUND_SOWN=7) - guaranteeing workable land regardless of the id mismatch.
    Owned = the field's parcel is defaultFarmProperty (majority-sampled on the generated farmland grid). Returns #baked."""
    fx = os.path.join(OUT, "maps", "farmlands.xml")
    grid = os.path.join(OUT, "maps", "data", "infoLayer_farmland.grle")
    gd = os.path.join(OUT, "maps", "data", "densityMap_ground.gdm")
    if not all(os.path.exists(p) for p in (fx, grid, gd)):
        return 0
    import re, numpy as np
    default_ids = {int(m.group(1)) for m in re.finditer(
        r'<farmland id="(\d+)"[^/]*defaultFarmProperty="true"', open(fx, encoding="utf-8").read())}
    if not default_ids:
        return 0
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
    import grle_codec, gdm_codec, binfmt
    from shapely.geometry import Polygon, Point
    from PIL import Image, ImageDraw
    GROUND_SOWN = 7
    farm = np.asarray(grle_codec.decode(open(grid, "rb").read())[0]); FN = farm.shape[0]
    def fpx(w): return min(max(int((w + MAP_M / 2) / MAP_M * FN), 0), FN - 1)
    _g = gdm_codec.decode(open(gd, "rb").read())               # gdm_codec returns the array directly (grle returns a tuple)
    ground = np.asarray(_g[0] if isinstance(_g, tuple) else _g).astype(np.uint16); N = ground.shape[0]
    def gpx(w): return min(max(int((w + MAP_M / 2) / MAP_M * N), 0), N - 1)
    baked = []
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
            mask = Image.new("L", (N, N), 0)
            ImageDraw.Draw(mask).polygon([(gpx(x), gpx(z)) for x, z in poly.exterior.coords], fill=1)
            ground[np.asarray(mask, dtype=bool)] = GROUND_SOWN
            baked.append(f["num"])
    if baked:
        binfmt.paint_gdm(gd, N, ground, 11, 1, 0)
    return baked


def main():
    from shapely.geometry import Polygon
    fields = ww_fields.read_fs22_fields(os.path.join(FS22, CONV["source"]["map_i3d"]))
    # per-map field_fixups: merge/split FS22 fields that share a farmland into FS25's one-field-per-farmland model
    # (must match build_farmland, which carves the matching new parcels). No-op when the map defines no field_fixups.
    fields = ww_fields.resolve_fields(fields, CONV.get("field_fixups", []))
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else len(fields)   # generate only the first X fields
    fields = fields[:limit]
    i3d_path = os.path.join(OUT, "maps", I3D)
    tree = ET.parse(i3d_path); root = tree.getroot()
    scene = root.find("Scene")
    uas = root.find("UserAttributes")
    if uas is None:
        uas = ET.SubElement(root, "UserAttributes")
    # idempotent: drop any prior fields group + its UserAttributes so re-running with a new count is clean
    old = next((c for c in scene if c.get("name") == "fields"), None)
    if old is not None:
        old_ids = {e.get("nodeId") for e in old.iter() if e.get("nodeId")}
        scene.remove(old)
        for u in list(uas):
            if u.get("nodeId") in old_ids:
                uas.remove(u)
    nid = max((int(e.get("nodeId")) for e in root.iter() if (e.get("nodeId") or "").isdigit()), default=0) + 1

    def NID():
        nonlocal nid; v = nid; nid += 1; return str(v)

    fg_id = NID()
    fg = ET.SubElement(scene, "TransformGroup", {"name": "fields", "nodeId": fg_id})
    ua = ET.SubElement(uas, "UserAttribute", {"nodeId": fg_id})
    ET.SubElement(ua, "Attribute", {"name": "onCreate", "type": "scriptCallback", "value": "FieldUtil.onCreate"})

    for f in fields:
        # num = the ORIGINAL FS22 field number (carried through ww_fields), used verbatim for the node name and
        # the on-map Note label. Distributed AutoDrive/Courseplay configs are keyed to these numbers, so we must
        # NOT re-enumerate 1..N here (that would shift every number after any gap or dropped field).
        num = f["num"]
        ox, oy, oz = f["origin"]
        fid = NID()
        fn = ET.SubElement(fg, "TransformGroup", {"name": f"field{num}", "translation": fmt(f["origin"]), "nodeId": fid})
        pp = ET.SubElement(fn, "TransformGroup", {"name": "polygonPoints", "nodeId": NID()})
        for pn, (wx, wz) in enumerate(f["polygon"], 1):
            ET.SubElement(pp, "TransformGroup", {"name": f"point{pn}", "translation": fmt([wx - ox, 0.0, wz - oz]), "nodeId": NID()})
        ni = ET.SubElement(fn, "TransformGroup", {"name": "nameIndicator",
             "translation": fmt([f["indicator"][0] - ox, 0.0, f["indicator"][2] - oz]), "nodeId": NID()})
        # Base-game Note label format: "fieldN\n<area> ha" (the PDA/map marker text). \n serializes as &#10;.
        ha = Polygon(f["polygon"]).area / 10000.0
        ET.SubElement(ni, "Note", {"name": "Note", "text": f"field{num}\n{ha:.2f} ha",
                      "fixedSize": "true", "color": "1 1 1 1", "nodeId": NID()})
        # teleportIndicator (mission spawn) = a DEDICATED child at the field CENTRE (centroid). Playbook gotcha #10:
        # missionAllowed=true with NO teleportIndicator child -> 55% hang. Index 2 (polygonPoints=0, nameIndicator=1).
        cx, cz = Polygon(f["polygon"]).centroid.coords[0]
        ET.SubElement(fn, "TransformGroup", {"name": "teleportIndicator",
                      "translation": fmt([cx - ox, 0.0, cz - oz]), "nodeId": NID()})
        fua = ET.SubElement(uas, "UserAttribute", {"nodeId": fid})
        for nm, ty, val in (("angle", "float", "0"), ("missionAllowed", "boolean", "true"),
                            ("missionOnlyGrass", "boolean", "false"), ("nameIndicatorIndex", "string", "1"),
                            ("polygonIndex", "string", "0"), ("teleportIndicatorIndex", "string", "2")):
            ET.SubElement(fua, "Attribute", {"name": nm, "type": ty, "value": val})
    tree.write(i3d_path, encoding="utf-8", xml_declaration=True)

    # fields.xml - entries for the OWNED (defaultFarmProperty) fields ONLY. FS25 auto-populates NPC/contract fields
    # from their polygon (default crops + contracts) when they're ABSENT here, so we leave those out. But it does NOT
    # auto-initialise the player's OWN fields - without an explicit <ground> state they sit as un-workable raw terrain
    # (the exact symptom on WW's starter fields 7 & 11). So we lay each owned field out as a PLOWED, empty, ready-to-
    # plant field - identical to base mapUS config/fields.xml (which ships PLOWED/HARVEST_READY entries for just its 4
    # starter fields). fieldId = the field number (== creation order == farmland/PDA id on parity maps like WW).
    owned = owned_field_nums(fields)
    entries = "".join(
        f'        <field fieldId="{num}" weedState="0" plowLevel="1">\n'
        f'            <fruit type="UNKNOWN" />\n'
        f'            <ground type="PLOWED" angle="0" />\n'
        f'            <spray type="NONE" level="0" />\n'
        f'        </field>\n' for num in owned)
    open(os.path.join(OUT, "maps", "fields.xml"), "w", encoding="utf-8").write(
        '<?xml version="1.0" encoding="utf-8" standalone="no" ?>\n'
        '<map xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xsi:noNamespaceSchemaLocation="../../../../shared/xml/schema/fields.xsd">\n'
        '    <fields>\n'
        f'{entries}'
        '    </fields>\n'
        '</map>\n')

    # wire map.xml
    mx_path = os.path.join(OUT, "maps", "map.xml"); mx = ET.parse(mx_path); mr = mx.getroot()
    (mr.find("fields") if mr.find("fields") is not None else ET.SubElement(mr, "fields")).set("filename", "maps/fields.xml")
    mx.write(mx_path, encoding="utf-8", xml_declaration=True)

    baked = bake_owned_ground(fields)   # bake tilled ground into densityMap_ground for owned fields (FS25 won't for them)
    print(f"fields: {len(fields)} generated (polygonPoints + nameIndicator + teleportIndicator@centre + "
          f"FieldUtil.onCreate + per-field UA) | fields.xml lays out {len(owned)} OWNED field(s) as PLOWED "
          f"ready-to-plant: {owned} | baked workable ground for {len(baked)} owned field(s): {baked}")


if __name__ == "__main__":
    main()
