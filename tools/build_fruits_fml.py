"""
build_fruits_fml.py - add the `groundFoliage` FoliageType (Smoky's dark forest-floor clutter: dry branches +
fallen leaves = brown, ferns/nettles/cover-foliage = green) to our engine's fruits FoliageMultiLayer, so we can
render it. We KEEP our aligned 10ch/5typeIdx/5comp layer (Smoky's is misaligned 12/6/5 - copying it would risk our
working crops); we just append one type -> $data/foliage/forestPlants/groundFoliage.xml. Idempotent.
Run on the engine-skeleton i3d (after start). Prints the new typeIdx for build_ground_cover.
"""
import os, sys, json
import xml.etree.ElementTree as ET

GF_XML = "$data/foliage/forestPlants/groundFoliage.xml"
WW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONV = json.load(open(os.path.join(WW, "tools", "wildwest.convert.json"), encoding="utf-8"))
OUT = os.path.join(WW, "out", CONV["identity"]["mod"])
I3D = os.path.join(OUT, "maps", CONV["identity"]["i3d"])


def main():
    tree = ET.parse(I3D); root = tree.getroot()
    files_el = root.find("Files")
    terrain = next(root.iter("TerrainTransformGroup"))
    fml = next(f for f in terrain.iter("FoliageMultiLayer")
               if "grass" in [x.get("name") for x in f.findall("FoliageType")])
    fts = fml.findall("FoliageType")
    if any(ft.get("name") == "groundFoliage" for ft in fts):
        print(f"groundFoliage already present at typeIdx {[ft.get('name') for ft in fts].index('groundFoliage')}")
        return

    fid = str(max(int(f.get("fileId")) for f in files_el) + 1)
    ET.SubElement(files_el, "File", {"fileId": fid, "filename": GF_XML})
    # new FoliageType: copy an existing deco type's attributes, change name + foliageXmlId
    tmpl = next(ft for ft in fts if ft.get("name") == "forestPlants")
    attrs = dict(tmpl.attrib); attrs["name"] = "groundFoliage"; attrs["foliageXmlId"] = fid
    new = ET.Element("FoliageType", attrs)
    last = fts[-1]
    children = list(fml)
    fml.insert(children.index(last) + 1, new)     # right after the last FoliageType -> next typeIdx

    tree.write(I3D, encoding="utf-8", xml_declaration=True)
    print(f"added groundFoliage FoliageType at typeIdx {len(fts)} -> {GF_XML}")


if __name__ == "__main__":
    main()
