"""
convert.py - NATIVE Wild West FS22 -> FS25 conversion. READ the FS22 original -> UNDERSTAND -> GENERATE.
NO copying. Foundation = the fs25-empty-map ENGINE (clean, load-verified). Each layer generates onto it.

Order (locked with user): start -> terrain -> densities -> farmland -> fields -> ground_texture ->
  ground_cover -> flats -> road_grade.
  start        : engine skeleton, WW identity, starter_field=False (no placeholder field/crop)
  terrain      : WW landform (build_terrain.py)   -- reads FS22 DEM + heightScale
  densities    : engine's blank densities + base-game FoliageSystem (fallow fields; cultivation from fields.xml)
  farmland     : buyable/non-buyable from the FS22 comments (build_farmland.py)
  fields       : WW's 82 field polygons -> FS25 field system (build_fields.py)
  ground_texture: 2a - repaint terrain layers from WW's weight masks (build_ground_texture.py)
  ground_cover : 2b - grass + meadowUS wildflowers on pastures, off roads/fields (build_ground_cover.py)
  flats        : 2c - WW roads/Bridges/tunnels (ver=7 meshes raw-copied) + ROAD collision + decalLayer decals
  road_grade   : 2c - carve DEM flush under the road corridors (needs flats' WW_roads); needs scipy
  (build_flats caches the 2min .shapes decode in scratchpad/flats_ents.pkl; re-decodes if absent)

Usage: python tools/convert.py            (run all + deploy)
"""
import os, sys, json, shutil, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # so convert_env + sibling tools import (incl. when frozen-runpy'd)
import convert_env
sys.path.insert(0, os.environ.get("FS25_EMPTY_MAP_TOOLS", os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")))   # engine skeleton (override per machine)
import mapcfg, gen_i3d, gen_data, gen_configs
_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if hasattr(subprocess, "CREATE_NO_WINDOW") else {}

WW = convert_env.app_home()                       # writable OUTPUT base (exe folder when frozen; repo root from source)
os.environ["FS_CONVERT_HOME"] = WW                # propagate to the per-step tool subprocesses
MAP_CONVERT = os.environ.setdefault("MAP_CONVERT", "wildwest.convert.json")   # map-agnostic: subprocess tools inherit this
CONV = json.load(open(MAP_CONVERT if os.path.isabs(MAP_CONVERT)               # GUI passes an abspath; else read from the bundle
                      else os.path.join(convert_env.bundle_tools(), MAP_CONVERT), encoding="utf-8"))
IDN = CONV["identity"]
OUT = os.path.join(WW, "out", IDN["mod"]); MAPS = os.path.join(OUT, "maps"); DATA = os.path.join(MAPS, "data")
# deploy target = the FS25 folder-mods dir (in Documents, NOT the Steam install). Overridable for other machines.
MODS = os.environ.get("FS25_MODS", os.path.join(os.path.expanduser("~"), "Documents", "My Games", "FarmingSimulator2025", "mods"))
_c = CONV.get("cfg", {})
CFG = mapcfg.Cfg(_c.get("map_m", 8192), _c.get("name", "WildWest16x"), _c.get("size", "16x"),
                 mod=IDN["mod"], title=IDN["title"], i3d=IDN["i3d"], starter_field=False)


def _tool(name):
    subprocess.run(convert_env.tool_argv(name), check=True, **_NO_WINDOW)   # frozen-aware: [python, tools/name] or [exe, --run-tool, name]


def start():
    shutil.rmtree(OUT, ignore_errors=True); os.makedirs(DATA)
    gen_i3d.build(CFG, os.path.join(MAPS, CFG.i3d)); gen_data.build(CFG, DATA); gen_configs.build(CFG, OUT, MAPS)
    _tool("build_overview.py")   # replace the engine's green placeholder with WW's real overview picture
    print("[start] engine skeleton + WW identity + overview (no placeholder field)")


def densities():
    print("[densities] engine blank densities + base-game FoliageSystem (fallow fields; cultivation via fields.xml)")


STEPS = [("start", start), ("terrain", lambda: _tool("build_terrain.py")), ("densities", densities),
         ("farmland", lambda: _tool("build_farmland.py")), ("fields", lambda: _tool("build_fields.py")),
         ("field_fertility", lambda: _tool("build_field_fertility.py")), # fertilize+lime NPC crop fields (sprayLevel/limeLevel) so harvest contracts reach getMaxCutLiters (proper maps ship fields pre-fertilized)
         ("ground_texture", lambda: _tool("build_ground_texture.py")),   # 2a terrain paint
         ("ground_cover", lambda: _tool("build_ground_cover.py")),       # 2b grass+meadowUS foliage (off roads/fields)
         ("flats", lambda: _tool("build_flats.py")),                     # 2c roads/Bridges/tunnels + collision + decals
         ("road_grade", lambda: _tool("build_road_grade.py")),           # 2c carve terrain flush under the road corridors
         ("buildings", lambda: _tool("build_buildings.py")),             # 4a WW town meshes + FS22 textures (copied from the FS22 install)
         ("curtains", lambda: _tool("build_curtains.py")),               # 4c map curtain / backdrop (WW horizone: distant hills + bg mountains); no collision
         ("crops", lambda: _tool("build_crops.py")),                     # 4d WW custom FS22 crops (hemp/tobacco/...) -> FS25 foliage packages + full fruit/fill registry
         ("placeables", lambda: _tool("build_placeables.py")),           # 4b WW functional placeables -> FS25 base-game (sell/buy/production/animal). AFTER crops: sale points read the custom crop fills so they're sellable

         ("lights", lambda: _tool("build_lights.py")),                   # 5 WW light fixtures -> base-game FS25 "Street Light" i3d references (native mesh+light; no FS22 extraction)
         ("trees", lambda: _tool("build_trees.py")),                     # 3 base-game FS25 trees at WW positions (deciduous near roads)
         ("field_entrances", lambda: _tool("build_field_entrances.py")), # F1 per-field access lane (dirt track road->meadow + foliage/tree/pole clearance); consumes scan_field_entrances plan
         ("water", lambda: _tool("build_water.py")),                     # 6 FS25-native oceanShader plane at the water level (terrain clips to basins); no FS22 assets
         ("shop", lambda: _tool("build_shop.py")),                       # 7 port WW's functional vehicle shop (spawn places + trigger + onCreate UAs) into gameplay
         ("fixup", lambda: _tool("fix_i3d_refs.py")),                     # FINAL integrity gate: strip dangling material->file refs (WW id-reuse) that crash the loader
         ("tweaks", lambda: _tool("build_tweaks.py"))]                    # LAST word: per-map manual overrides from config "tweaks" (rotate/move/delete a named placeable or i3d node)


def deploy():
    link = os.path.join(MODS, IDN["mod"])
    subprocess.run(["powershell", "-NoProfile", "-Command",
        f"if(Test-Path '{link}'){{(Get-Item '{link}').Delete()}}; "
        f"New-Item -ItemType Junction -Path '{link}' -Target '{OUT}' | Out-Null"], check=True)
    print(f"deployed: {link} -> {OUT}")


if __name__ == "__main__":
    pos = [a for a in sys.argv[1:] if not a.startswith("--")]     # step-count arg, ignoring flags
    no_deploy = "--no-deploy" in sys.argv
    n = int(pos[0]) if pos else len(STEPS)
    skip = {s.strip() for s in os.environ.get("SKIP_STEPS", "").split(",") if s.strip()}   # isolation: SKIP_STEPS=buildings,curtains
    for i, (name, fn) in enumerate(STEPS[:n], 1):
        if name in skip:
            print(f"[STEP {i}/{n}] {name} -- SKIPPED (SKIP_STEPS)", flush=True); continue
        print(f"[STEP {i}/{n}] {name}", flush=True)               # progress marker (the UI parses this)
        fn()
    if not no_deploy:
        deploy()
    print(f"\n== native build: steps 1-{n} ({', '.join(s[0] for s in STEPS[:n])}) -> {OUT} ==")
