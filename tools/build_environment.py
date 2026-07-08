"""
build_environment.py - PHASE 2 (look): adopt Smoky Mountain's TUNED environment = the atmospheric HAZE.
Smoky's config/environment.xml references base $data sky + colorGrading, but heavily TUNES the values (denser
ground fog, retuned light-scattering / sun / clouds / ground-albedo) - that tuning IS the smoky/hazy US look.
We adopt it: read Smoky's environment.xml, repoint its ONE local ref (the env-zone infolayer, mapAS/ -> maps/),
write it as our maps/config/environment.xml, generate a uniform env-zone layer, and point map.xml at it.
US-middle-latitude, base assets only (this is the Smoky LOOK reference the user chose - not WW-data copying).
"""
import os, sys, json, re
import numpy as np
import xml.etree.ElementTree as ET
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import grle_codec

SMOKY_ENV = r"C:\repos\refmaps\SmokyMountainFarming\mapAS\config\environment.xml"
WW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONV = json.load(open(os.path.join(WW, "tools", "wildwest.convert.json"), encoding="utf-8"))
OUT = os.path.join(WW, "out", CONV["identity"]["mod"])


def main():
    txt = open(SMOKY_ENV, encoding="utf-8").read()
    txt = txt.replace("mapAS/data/infoLayer_environment.grle", "maps/data/infoLayer_environment.grle")
    txt = re.sub(r'[ \t]*<twister[^>]*/>\r?\n?', '', txt)   # drop Smoky's local twister effect (mapAS-local, unwanted)
    remaining = [ln.strip() for ln in txt.splitlines() if "mapAS/" in ln]
    if remaining:
        print("  WARN unhandled mapAS/ refs:", remaining[:5])

    os.makedirs(os.path.join(OUT, "maps", "config"), exist_ok=True)
    open(os.path.join(OUT, "maps", "config", "environment.xml"), "w", encoding="utf-8").write(txt)

    # uniform env-zone layer (512^2 all-0) so the whole map uses the tuned environment
    open(os.path.join(OUT, "maps", "data", "infoLayer_environment.grle"), "wb").write(
        grle_codec.encode(np.zeros((512, 512), np.uint8)))

    # point map.xml at our environment
    mxp = os.path.join(OUT, "maps", "map.xml"); mx = ET.parse(mxp); mr = mx.getroot()
    e = mr.find("environment")
    if e is None:
        e = ET.SubElement(mr, "environment")
    e.set("filename", "maps/config/environment.xml")
    mx.write(mxp, encoding="utf-8", xml_declaration=True)
    print("environment: adopted Smoky's tuned env (haze) -> maps/config/environment.xml + uniform env-zone; map.xml repointed")


if __name__ == "__main__":
    main()
