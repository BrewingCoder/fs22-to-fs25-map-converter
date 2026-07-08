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

WW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import convert_env
CONV = json.load(open(os.path.join(WW, "tools", os.environ.get("MAP_CONVERT", "wildwest.convert.json")), encoding="utf-8"))
FS22 = convert_env.source_dir(CONV)
OUT = os.path.join(WW, "out", CONV["identity"]["mod"])
I3D = CONV["identity"]["i3d"]


def fmt(v):
    return " ".join(f"{x:.6g}" for x in v)


def main():
    from shapely.geometry import Polygon
    fields = ww_fields.read_fs22_fields(os.path.join(FS22, CONV["source"]["map_i3d"]))
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

    # fields.xml - BLANK (namespaced shell only), hard-coded. A BLANK fields.xml lets FS25 populate EVERY field
    # from its polygon (default crops) and makes them all contract-eligible. Per-field <ground>/<fruit> entries
    # SUPPRESS that: with them, only ~2/10 fields got crops+contracts (user-verified). Change this ONLY if we
    # deliberately need to pin per-field state later.
    open(os.path.join(OUT, "maps", "fields.xml"), "w", encoding="utf-8").write(
        '<?xml version="1.0" encoding="utf-8" standalone="no" ?>\n'
        '<map xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xsi:noNamespaceSchemaLocation="../../../../shared/xml/schema/fields.xsd">\n'
        '    <fields>\n'
        '    </fields>\n'
        '</map>\n')

    # wire map.xml
    mx_path = os.path.join(OUT, "maps", "map.xml"); mx = ET.parse(mx_path); mr = mx.getroot()
    (mr.find("fields") if mr.find("fields") is not None else ET.SubElement(mr, "fields")).set("filename", "maps/fields.xml")
    mx.write(mx_path, encoding="utf-8", xml_declaration=True)

    print(f"fields: {len(fields)} generated (polygonPoints + nameIndicator + teleportIndicator@centre + "
          f"FieldUtil.onCreate + per-field UA + fields.xml BLANK)")


if __name__ == "__main__":
    main()
