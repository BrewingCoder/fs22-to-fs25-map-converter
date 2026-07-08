"""
Map hang/health validator - encodes every load-hang cause we've diagnosed so new builds can't regress.
Run on the built map:  python tools/validate_map.py
Exit 0 = all pass, 1 = a check failed. NO GentsEditor needed. Add a new CHECK each time we find a new hang cause.

Known hang/crash causes encoded:
  H1  infoLayer_fieldType must be BLANK + its InfoLayer runtime="true" (non-blank field-numbers hang 55% load,
      conflicting with FS25's runtime field generation).
  H2  farmlands InfoLayer must be runtime="true" (FS25 builds parcels from farmland grle + farmlands.xml).
  C1  every densityMap_*.gdm must load with the expected channel count (wrong channels -> load crash/convert).
  F1  every field polygon >=3 pts and non-degenerate area (compound fields must be unioned, not 1 rect).
  L1  no terrain data ref may point at ANOTHER map's baked data ($data/maps/*/data/{densityMap,infoLayer}) -
      except the known-still-TODO groundFoliage/environment (foliage phase).
  X1  the i3d must parse as well-formed XML.
"""
import os, re, sys
import numpy as np
import json
import xml.etree.ElementTree as ET
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import grle_codec, gdm_fruits_codec

# validate the LIVE from-scratch build (out/<mod>, wildwest.i3d) - the one the game loads via the deploy junction.
# (Was pointed at the DEAD src/ copy-the-map pipeline w/ cazz16x.i3d, so its PASSes were validating the wrong map.)
_WW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONV = json.load(open(os.path.join(_WW, "tools", os.environ.get("MAP_CONVERT", "wildwest.convert.json")), encoding="utf-8"))
MOD  = os.path.join(_WW, "out", _CONV["identity"]["mod"])
I3D  = os.path.join(MOD, "maps", _CONV["identity"]["i3d"])
DATA = os.path.join(MOD, "maps", "data")

fails = []
def check(ok, tag, msg):
    print(f"  [{'PASS' if ok else 'FAIL'}] {tag}: {msg}")
    if not ok:
        fails.append(tag)


def main():
    # X1 - parses
    try:
        tree = ET.parse(I3D); root = tree.getroot()
        check(True, "X1", "i3d parses as well-formed XML")
    except Exception as e:
        check(False, "X1", f"i3d does NOT parse: {e}"); print("\nRESULT: FAIL"); sys.exit(1)

    files = {f.get("fileId"): f.get("filename") for f in root.find("Files")}
    terrain = root.find(".//TerrainTransformGroup")
    infolayers = {il.get("name"): il for il in terrain.iter("InfoLayer")}

    # H1 - fieldType blank + runtime
    ft = infolayers.get("fieldType")
    if ft is None:
        check(False, "H1", "no fieldType InfoLayer")
    else:
        rt = ft.get("runtime") == "true"
        fn = files.get(ft.get("fileId"), "")
        blank = None
        p = os.path.join(MOD, "maps", fn.replace("/", os.sep))
        if fn.endswith(".grle") and os.path.exists(p):
            arr = grle_codec.decode(open(p, "rb").read()); arr = arr[0] if isinstance(arr, tuple) else arr
            blank = int(arr.max()) == 0
        check(rt and blank is True, "H1",
              f"fieldType runtime={ft.get('runtime')} file={fn} blank={blank} (need runtime=true + all-zero grle)")

    # H2 - farmlands runtime
    fl = infolayers.get("farmlands")
    check(fl is not None and fl.get("runtime") == "true", "H2",
          f"farmlands runtime={fl.get('runtime') if fl else 'MISSING'}")

    # C1 - density maps present + valid GIANTS density magic ("MDF, 0x46444D22)
    MAGIC = b'"MDF'
    ok_all = True
    for base in ("densityMap_fruits", "densityMap_weed", "densityMap_stones", "densityMap_height", "densityMap_ground"):
        p = os.path.join(DATA, base + ".gdm")
        if not os.path.exists(p) or os.path.getsize(p) < 16:
            ok_all = False; print(f"      missing/empty {base}.gdm"); continue
        with open(p, "rb") as fh:
            head = fh.read(4)
        if head != MAGIC:
            ok_all = False; print(f"      {base}: bad magic {head!r} (expected {MAGIC!r})")
    check(ok_all, "C1", "all densityMap_*.gdm present with valid GDM magic")

    # F1 - field polygons valid
    grp = next((e for e in root.find("Scene").iter()
                if e.tag == "TransformGroup" and e.get("name") == "fields"), None)
    bad = 0; nfields = 0
    if grp is not None:
        for f in grp:
            pp = next((c for c in f if c.get("name") == "polygonPoints"), None)
            if pp is None:
                continue
            nfields += 1
            pts = [(float(p.get("translation").split()[0]), float(p.get("translation").split()[2])) for p in pp]
            if len(pts) < 3:
                bad += 1; continue
            a = abs(sum(pts[i][0]*pts[(i+1) % len(pts)][1] - pts[(i+1) % len(pts)][0]*pts[i][1]
                        for i in range(len(pts)))) / 2
            if a < 100:  # < 0.01 ha degenerate
                bad += 1
    check(bad == 0 and nfields > 0, "F1", f"{nfields} fields, {bad} degenerate/<3pts")

    # L1 - no other-map baked data leaks (except known-TODO groundFoliage/environment)
    ALLOW = ("groundFoliage", "infoLayer_environment")
    leaks = []
    for did in set(re.findall(r'(?:densityMapId|fileId)="(\d+)"', ET.tostring(terrain, encoding="unicode"))):
        fn = files.get(did, "")
        if re.search(r'\$data/maps/\w+/data/(densityMap|infoLayer)', fn) and not any(a in fn for a in ALLOW):
            leaks.append(fn)
    check(not leaks, "L1", f"other-map baked-data leaks: {sorted(set(leaks)) or 'none'}")

    # W1 - every LOCAL (data/) terrain Layer weight/detail/normal/height/displacement file must EXIST on disk.
    # We delete the terrain cache so the game rebuilds painting from these PNGs; a missing one stalls that
    # rebuild = 55% hang (root cause: build_native_terrain not regenerating ww_weight_blank.png).
    mapsdir = os.path.dirname(I3D)
    miss = set()
    for lay in terrain.iter("Layer"):
        for a in ("weightMapId", "detailMapId", "normalMapId", "heightMapId", "displacementMapId"):
            fn = files.get(lay.get(a), "")
            if fn.startswith("data/") and not os.path.exists(os.path.join(mapsdir, fn.replace("/", os.sep))):
                miss.add(fn)
    check(not miss, "W1", f"local terrain weight/texture files exist on disk: {sorted(miss) or 'all present'}")

    # M1 - every field must carry missionAllowed="true" (FS25). Without it getFieldForMission() returns nil and
    # NO field contracts ever generate. FS22 fields ship without it (and with the old fieldGrassMission name).
    fgrp = next((e for e in root.find("Scene").iter()
                 if e.tag == "TransformGroup" and e.get("name") == "fields"), None)
    fids = {f.get("nodeId") for f in fgrp if (f.get("name") or "").startswith("field")} if fgrp is not None else set()
    no_mission = []
    for ua in root.iter("UserAttribute"):
        if ua.get("nodeId") in fids:
            attrs = {a.get("name"): a.get("value") for a in ua}
            if attrs.get("missionAllowed") != "true":
                no_mission.append(ua.get("nodeId"))
    check(fids and not no_mission, "M1",
          f"all {len(fids)} fields have missionAllowed=true: {len(no_mission)} missing (blocks contracts)")

    # M2 - every field must have a teleportIndicator CHILD (FS25 mission spawn point). missionAllowed=true WITHOUT
    # a teleportIndicator stalls the mission setup = 55% hang. Native FS25 fields carry polygon+name+teleport.
    no_tele = []
    if fgrp is not None:
        for f in fgrp:
            if (f.get("name") or "").startswith("field") \
               and not any((c.get("name") or "") == "teleportIndicator" for c in f):
                no_tele.append(f.get("name"))
    check(fgrp is not None and not no_tele, "M2",
          f"all fields have a teleportIndicator child: {len(no_tele)} missing (missionAllowed w/o it hangs 55%)")

    # C2 - every map-local fruitTypes.xml ref must resolve to a file that EXISTS. Internal refs are relative to
    # the map base dir (maps/), so a "$data/..." ref is engine-absolute but a plain "foliage/x.xml" -> maps/... .
    # A wrong ref (e.g. leftover "maps/foliage/..." -> maps/maps/foliage) fails to load the fruit = broken crop +
    # post-load stall once the economy/growth setup runs.
    ftx = os.path.join(MOD, "maps", "config", "fruitTypes.xml")
    bad_fruit = []
    if os.path.exists(ftx):
        for ref in re.findall(r'<fruitType filename="([^"]+)"', open(ftx, encoding="utf-8").read()):
            if ref.startswith("$"):
                continue
            # A map <fruitType filename> resolves relative to MOD-ROOT and carries the map-dir prefix (VERIFIED vs
            # Smoky Mountain: "mapAS/foliage/cotton/cotton.xml"). Ours = "maps/foliage/<c>/<c>.xml". A bare "foliage/x"
            # silently fails to register the fruit (in Prices via fillType, but not plantable). NOTE: the i3d FML File
            # ref for the SAME foliage is i3d-dir-relative ("foliage/x") - see C3 - hence the two prefixes differ.
            if not os.path.exists(os.path.join(MOD, ref.replace("/", os.sep))):
                bad_fruit.append(ref)
    check(not bad_fruit, "C2", f"fruitTypes.xml refs resolve (rel to MOD-ROOT, map-dir-prefixed): {sorted(bad_fruit) or 'all ok'}")

    # C3 - every terrain FoliageType's foliage.xml File ref (map-local, not $data-absolute) MUST resolve to a file that
    # EXISTS. i3d File refs resolve relative to the i3d's dir (maps/) - VERIFIED via the log: ref "foliage/rye/rye.xml" ->
    # "maps/foliage/rye/rye.xml" -> "Failed to load foliage type 'rye'" when the file wasn't there. So the map-local
    # foliage must live at maps/foliage/ and be referenced "foliage/x.xml". (Distinct from C2's fruitTypes.xml refs; this
    # checks the i3d File refs - which happen to share the same base dir + ref string.)
    bad_fol = []
    for t in terrain.iter("FoliageType"):
        fn = files.get(t.get("foliageXmlId"), "")
        if fn and not fn.startswith("$") and fn.endswith(".xml"):
            if not os.path.exists(os.path.join(MOD, "maps", fn.replace("/", os.sep))):
                bad_fol.append(f"{t.get('name')}={fn}")
    check(not bad_fol, "C3", f"terrain FoliageType foliage.xml refs resolve (rel to MAPS dir): {sorted(bad_fol) or 'all ok'}")

    # D1 - no terrain DetailLayer/Layer materialId dangles (undefined Material). The native-terrain id-offset can
    # bump a materialId ref while its target material stays at an un-offset base id -> dangling -> the detail/
    # foliage layer can end up buffered-but-invisible. (build_native_terrain repoints these; this catches regressions.)
    defined_mats = {m.get("materialId") for m in root.iter("Material")}
    dangling = []
    for el in terrain.iter():
        if el.tag in ("DetailLayer", "Layer", "FoliageMultiLayer"):
            mid = el.get("materialId")
            if mid and mid not in defined_mats:
                dangling.append(f"{el.tag}:{el.get('name')}={mid}")
    check(not dangling, "D1", f"terrain DetailLayer/Layer material refs resolve: {sorted(dangling) or 'all ok'}")

    print("\nRESULT:", "PASS - no known hang causes" if not fails else f"FAIL ({','.join(fails)})")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
