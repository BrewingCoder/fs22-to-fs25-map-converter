# Wild West 16x — FS22 → FS25 Conversion

Convert **Wild West 16x by Cazz64/OAG** (FS22 v1.9.0.0) to run in Farming Simulator 25, Python-driven,
as a **full custom-asset port** (the map should look identical to the FS22 original).

> **⚠️ The plan BELOW (GE10 baking, `src/` transform, `place_trees.py`/`gt_trees`) is the SUPERSEDED first approach.**
> Current work = a **native Python pipeline** on the `fs25-empty-map` engine: `python tools/convert.py`
> (**18 steps**: start, terrain, densities, farmland, fields, ground_texture, ground_cover, flats, road_grade,
> buildings, **curtains**, **placeables**, **crops**, lights, trees, **water**, **shop**, **fixup**). Output lives at
> `out/<mod>/` (deployed to the FS25 mods dir via a junction). READ the FS22 original → GENERATE onto a clean FS25
> skeleton; never copy map-data. Map-agnostic goal = a reusable FS22→FS25 TOOL: point it at another map's config via
> `MAP_CONVERT=<name>.convert.json` (`tools/classify.py` tags scene shapes by content for maps without clean groups).
> See **KEY DISCOVERIES (economy + crops + map-agnostic)** below for the 07-07/08 additions.

## KEY DISCOVERIES (native pipeline, 2026-07-06)
- **⚠️ LOAD HANG at 55% = out-of-range rotation.** A placed `<ReferenceNode>` with a rotation euler component
  outside **[-180, 180)** SILENTLY hangs the FS25 map load at ~55% (stalls in post-i3d finalize right after the
  foliage `FTG '...densityMap_fruits.gdm'` log line; **NO error logged**; needs a forced exit). Fix: normalize
  every emitted euler `a = (a+180)%360 - 180`. Found when street lights placed at `yaw+90` hit 270°. **Isolation
  method:** strip the suspect group from the deployed i3d, reload — if it loads, that group is the culprit.
- **Street lights = base-game FS25 "Street Light" references.** Extracting FS22 poles didn't translate (bad
  texture/light). Instead `build_lights.py` references `$data/placeables/mapEU/brandless/lightsResidential/
  streetLight01/streetLight01.i3d` (the **$300 "Street Light"**; the non-mapEU `streetLight01` is a different
  $100 one) at each FS22 fixture's world pos + yaw (`YAW_OFFSET=90`, normalized). Native mesh + night-masked
  lights; empty UserAttributes so safe to reference; 4341 refs, 1 File ref. CONFIRMED good in-game.
- **Trees: conifer vs deciduous by NEIGHBOURHOOD LINEARITY, not distance-to-road.** The author placed
  roadside/town trees as single-file SPLINE rows; big conifers there stand out. `build_trees.py` measures PCA
  linearity of each tree's neighbours (within 18 m): single-file (lin>0.70) or lone conifer → deciduous; stand
  conifer → stays conifer; deciduous stays deciduous. Validated: the author's own deciduous are 94% linear.
  Map-agnostic (no road-subtree dependency).
- **Screenshot the game = grab the primary monitor** (PowerShell .NET `CopyFromScreen` of `PrimaryScreen`).
  FS25 runs borderless on primary; don't reach for the debugger TCP screenshot endpoint.
- **Ground foliage = the grass TEXTURE, exactly.** `build_ground_cover`: `pastures = grass & ~dirt & ~field`, NO road
  margin/dilation (grass-weight & road-weight are mutually exclusive+adjacent). If grass creeps onto a road, the
  ground TEXTURE is wrong (fix 2a) — never draw the foliage back. RESOLUTION: foliage density map is 0.5 m/px
  (16384², the GPU-OOM cap). Edge source was WW's 1 m weight NEAREST-upscaled (blocky); now `ww_mask` bilinear-
  upsamples the antialiased 0–255 weight to the 0.5 m grid + thresholds (`thresh` = closeness knob) → 0.5 m edge
  = the foliage floor. GE's sub-0.5 m brush is the terrain-texture weight layer (separate), not the foliage density.

## KEY DISCOVERIES (economy + crops + map-agnostic, 2026-07-07/08)
- **Sell points = GENERATE native FS25 station XMLs** (`build_placeables`, Kansas pattern) carrying WW's price-screen
  name + broad `fillTypeCategories` (or the FS22 station's exact per-type price index) + the visible sell platform
  (`sellingStationGenericNoCover.i3d`, sunk −0.2 m) at the exact FS22 unload spot. Map-local placeables MUST be
  registered in `storeItems.xml` (mod-root-relative path) or they never instantiate. `$mapdir$` = MOD ROOT.
- **Production points = FS25 GENERIC producer i3d, VISUAL hidden, FS22 recipes injected.** `build_placeables`
  auto-picks the FS25 generic (`brandless/productionPointsGeneric/<x>`) by **OUTPUT fill-type overlap** (robust to name
  differences: WW `raisinFactory` → FS25 `grapeProcessingUnit`), hides its model so WW's extracted building shows,
  copies the `.shapes` local (`<Shapes externalShapesFile>` does NOT expand `$data`), and **snaps the trigger nodes to
  the FS22 point's OWN authored positions** (unload apron + WRENCH door) matched by semantic role. If WW ships no
  building at the spot (grainMill) keep the FS25 generic VISIBLE. All 10 WW production points wired.
- **Custom crops (`build_crops`, step "crops").** The map was meadow-only in its fruit registry, so this ALSO completes
  the base crop registry. Ports WW's OWN foliage (`maps/foliage/<crop>/`) → FS25 `foliageType` (embed+translate the
  `<fruitType>` economy; derive per-state flags from the state NAME — invisible/green*/harvest ready/dead/harvested/cut
  — NOT FS22 indices, which don't align). **The two loaders use DIFFERENT path prefixes for the same foliage:** i3d FML
  File ref = `foliage/<c>/<c>.xml` (rel the i3d's `maps/` dir); map `fruitTypes.xml` ref = `maps/foliage/<c>/<c>.xml`
  (rel MOD-ROOT, map-dir-prefixed — Smoky Mountain's convention). **39 fruit types need 6 typeIndex bits** → bump the
  fruits FML to 11/6 AND re-encode `densityMap_fruits.gdm` 10/5→11/6 (preserve painted grass/meadow) or FS25 crashes.
  Missing `foliageStateDefaults` width/height = invisible crop (tobacco). Harvester compat = `<fruitTypeCategories>`
  (GRAINHEADER=combine, SOWINGMACHINE/PLANTER=sow) which REPLACE the base list → emit full base membership + additions.
  Beet/root diggers (TOPLIFTINGHARVESTER) can't take custom fruits without a custom header. 8 crops port + onion (FS25
  native, referenced to de-dupe).
- **Map-agnostic: source paths + content-detect (West End test map, `westend.convert.json`).** mapUS-based maps nest
  their data under `maps/mapUS/` → derive the FS22 map-data dir from `dirname(source.map_i3d)`, not hardcoded `maps/`.
  West End's roads are extensive but INVISIBLE to group-name extraction (generic material `lambert2`, scattered under
  `Railway1_2`/`Lights`) → `classify.py` now tags by **texture filename** (asphalt/gravel), and **`build_flats`
  content-detects** roads via classify tags + `prune_to_shapes` (rebuild a minimal placement-preserving tree) when no
  road group matches (WW's group path unchanged). 28,977 West End roads extracted. Cross-mod texture refs (`../` into a
  sibling REQUIRED mod, e.g. `FS22_WestEnd_Vehicles`) mirror into `maps/imported/`. STILL group-based (need the same
  refactor): build_buildings/build_lights/build_trees.

## Sources / layout
- **FS22 source (read-only input):** `<FS22 mods folder>/<source.mod>` (e.g. `%USERPROFILE%\Documents\My Games\FarmingSimulator2022\mods\FS22_WildWest_16x`); resolved from `$FS22_MODS` + the config's `source.mod`.
- **FS25 base game (target reference):** the FS25 `data` folder, from `$FS25_DATA`.
- **This repo:** `out/<mod>/` = the GENERATED output mod (built from scratch onto the fs25-empty-map skeleton, NOT a
  copy of the source; `convert.py` rmtrees + rebuilds it, then junction-deploys to the FS25 mods dir). `tools/` =
  conversion scripts + per-map `*.convert.json` configs. `docs/` = this plan + logs. (`src/` = the SUPERSEDED
  copy-the-map pipeline, kept only for its RE'd knowledge; `validate_map.py` now targets `out/`.)
- Source scene: `maps/cazz16x.i3d` (WW) / `maps/mapUS/map.i3d` (West End). Output scene: `maps/<identity.i3d>`
  (`wildwest.i3d` / `westend.i3d`) + `.i3d.shapes`, both generated.

## Hard caveats (user)
1. **NO fences** — strip every fence i3d `<File>` + placed node (34 fence file refs in the i3d).
2. **Trees = FS25 base-game analogs** — repath `$data/maps/trees/{fs22 species}` → FS25 species (same base
   path, different species dirs). No FS22 tree ports (they lack seasonal variants).
3. **Full custom-asset port** — migrate every bundled building/sellpoint/prop i3d to FS25 shaders/materials.

## Format deltas (verified 2026-07-02)
- **i3d schema `version="1.6"` on BOTH** (FS22 GE 9.0.6 / FS25 GE 10.0.4) → scene graph is portable via text xform.
- **Terrain `<Layer>`:** FS25 adds `heightMapId, displacementMapId, displacementMaxHeight, porosityAtZeroRoughness,
  porosityAtFullRoughness, firmness, viscosity, firmnessWet`; **2 weight PNGs/layer** (FS22 = 4).
- **TerrainTransformGroup:** FS22 `collisionMask` + `unitsPerPixel=2` + `lodTextureSize=8192`; FS25
  `collisionFilterGroup=0x100 collisionFilterMask=0xfffff9c3` + `castShadowMap=true` + `lodTextureSize=2048`.
- **Ground-type palette differs** → map FS22 types onto FS25 `groundTypes.xml`
  {animalMud, asphalt, cobblestones, concrete, dirt, flagstones, forestGround, grass, gravel, plates, rock, sand}
  and consolidate weight files.
- **densityMaps:** FS25 adds `densityMap_groundFoliage`; ref/format `.gdm`↔`.png` shift to verify.
- **Binary terrain** (`.shapes`, `.terrain.*.cache`) re-baked in GE10 (headless).
- **Shaders/materials:** FS25 shader set differs — per-i3d material migration (the heavy full-port part).

## Ground-type mapping (draft — refine when building Phase 3)
| FS22 layer            | FS25 type    |
|-----------------------|--------------|
| grass, grassDry, grassDryPatchy, grassCliff_DLC | grass |
| beachSand, beachSandWet | sand |
| mountainRock, mountainRockDark, riverBed | rock |
| asphaltAlpine_DLC     | asphalt |
| concreteTilesAlpine_DLC | flagstones |
| cobblestone           | cobblestones |
| plate                 | plates |
| gravel, gravelDirt, gravelDust, gravelGrass, gravelMoss, pathwayGravel | gravel |
| pathway               | dirt |
| dirt, mud, waterPuddle | dirt |
| concrete              | concrete |
| animalMud             | animalMud |
| forestGround          | forestGround |

## Tree analog mapping (draft — refine when building Phase 2)
FS22 species present: birch, oak (+ others TBD from full i3d scan). FS25 species available: americanElm,
apple, aspen, beech, betulaErmanii, boxelder, cherry, chineseElm, deadwood, downyServiceBerry, goldenRain,
japaneseZelkova, lodgepolePine, northernCatalpa, oak, pinusSylvestris, pinusTabuliformis, shagbarkHickory,
tiliaAmurensis, treesRavaged. Draft: birch→betulaErmanii (or aspen), oak→oak, pine→lodgepolePine.

## Phases (status)
- [x] **P0 Recon** — deltas mapped, repo scaffolded, base copied. Scene = 271,906 shapes / 646 mats / 18 shaders.
- [x] **P1 Foundation** — `tools/convert_config.py` writes FS25 `modDesc.xml` (descVersion=100, FS22 Lua
  extraSourceFiles disabled) + `maps/map.xml` (FS25 schema; WW size/hotspots/farmlands/environment kept;
  non-WW configs point at FS25 base; groundTypeMappings first-pass = revised in P3). Both well-formed.
- [x] **P2a Strip fences** — `tools/strip_fences.py --apply`: removed 4,805 fence shapes (13 fence materials)
  + pruned 5,539 empty groups + 2 fence placeables. i3d well-formed, 266,413 shapes remain, 0 fences.
- [~] **P2b Trees** — `tools/extract_trees.py`: extracted **75,374** tree placements → `docs/tree_placements.json`
  (pine 33k→pinusSylvestris, spruce 28k→lodgepolePine, oak 8.4k→oak, birch 5.8k→betulaErmanii,
  stonepine 32→pinusTabuliformis). KEY: 75k instances share only ~40 unique prototypes (species×stage×var);
  baked static trees don't count against the 14k runtime-plant cap. STRATEGY = **prototype swap**: replace
  the ~40 shared FS25 tree geometries/materials (GE phase), keep all 75k transforms, re-point instances.
  `--strip` flag removes FS22 tree groups (run together with FS25 placement to avoid a barren intermediate).
- [ ] **P3 Terrain** — *(CRITICAL PATH — confirmed sole load blocker, see load test below)* rebuild the
  `TerrainTransformGroup`. Design: transplant the FS25 mapUS terrain node (`docs/fs25_mapUS_terrain_node.xml`,
  86 layers = 43 types×2 variants, all with diffuse/normal/HEIGHT/DISPLACEMENT textures — the missing
  height/displacement is what crashed GE), keep WW's dimensions + DEM (`data/map_dem.png`,
  unitsPerPixel=2/8192m), and generate FS25-layer weight PNGs by remapping WW's FS22 weight data through the
  ground-type table. Terrain texture File refs use `.png` names, resolve to `.dds` on disk (engine handles ext).
  **Design confirmed:** node refs 613 fileIds (502 terrain tex @ `$data/maps/mapUS/...` = exist in FS25, 84
  weights, 27 misc); terrain material = `terrainMaterial_mat` id 1464 customShaderId 2 (empty body, trivial);
  WW weights 8192px already match FS25 (1px/m); DEM 4097px@2m/px=8192m -> set node unitsPerPixel=2.
  43 CombinedLayer ground-types (ASPHALT, GRASS, MOUNTAINROCK, SAND, MUDLIGHT, FOREST_GRASS, GRAVELSMALL,
  ROCK_FLOOR_TILES, ROCK_FLOOR_TILES_PATTERN, DIRT_GRAVEL...). Transplant method: copy node+its Files+Material
  from mapUS, offset all ids by +100000 (avoid WW id collisions), repoint heightMapId->WW map_dem.png &
  weights->local, set unitsPerPixel=2.
  - **P3a (get it loading):** transplant + blank weights (grass01=255 base, rest 0) + STRIP FoliageSystem/
    DetailLayer/FoliageMultiLayer (cut deps/risk). Goal: WW landform renders as all-grass, GE no crash.
  - **P3b (paint parity):** remap WW's 98 FS22-layer weights -> FS25 layer weights via ground-type table.

### LOAD TESTS #2-#5 (2026-07-02) — TERRAIN SOLVED, MAP RENDERS
- #2/#3: terrain transplant -> terrain texture arrays now build (`New Terrain resolution 4096`), fixed the
  2 stray weight files (identify weight files by the node's weightMapId set, not the `_weight` filename).
- #3b: KEY FIX — stripping `DetailLayer`/`FoliageSystem`/`ProceduralPlacementMasks` from the terrain node
  CRASHES GE right after geometry build (the renderer/LOD needs them). `STRIP_TAGS = set()` (keep all). Proven
  by bisection: terrain-only crashed when stripped, LOADED (onFileOpen) when kept. Control: stock mapUS loads
  fine through our harness.
- **#5: FULL MAP LOADS + RENDERS** in GE (onFileOpen + Virtual Texture init). Screenshot shows WW landform
  (grassy hills = real DEM), all scene objects, buildings white (missing FS22 textures = P4), trees as
  billboards/white (need P2b swap+materials), fences GONE. Scenegraph intact (WildWest/WildWest2/gameplay...).
- **REMAINING: post-load HARD CRASH** (no log line after Virtual Texture; crash reporter dialog). Lead: the
  transplanted terrain's DetailLayer/FoliageSystem still point at mapUS densityMaps -> scale mismatch on WW's
  8192m terrain (`may need space for up to 16 instances / 16 cells`). FIX NEXT: repoint terrain detail/foliage
  densityMapId + InfoLayer fileIds to WW-sized local maps (or WW's own densityMaps). Also investigate red arcs
  in viewport (splines/traffic/gameplay paths?).
- **GE harness works** (tools/convert.ps1 + Start-Process editor.exe "<i3d>"; poll editor_log for
  onFileOpen/Virtual Texture; screenshot via scratchpad\shot.ps1). Save (when needed) = Pi4 HID.

### LOAD TEST #6 (2026-07-02) — STABLE FULL MAP  ✅
- **Root-caused the post-load crash by bisection** (tools/build_subset.py + ge_loadtest.ps1): scene subset
  minus WildWest2 = STABLE; WildWest2 minus its `trees` child = STABLE. **The FS22 baked trees crash GE**
  (226,122 shapes under WildWest2/trees, using the FS22 `SEASONAL_BILLBOARD180` tree shader id 1765 that GE10
  can't handle). Terrain-only survived 100s+, so terrain is NOT the crash.
- FIX: `extract_trees.py --strip` now runs in convert.ps1 -> removes all 75,374 FS22 tree groups (replaced by
  FS25 analogs in P2b-GE). Also enhanced `strip_fences.py` to remove fence-NAMED TransformGroups (982 of them:
  farmFence/fenceBoardGap/fenceresidentiallHome... — leaked past the material/shape test).
- **RESULT: full map loads STABLE in GE** (onFileOpen + Virtual Texture, editor alive, no crash). State =
  real WW landform + all buildings/objects, NO fences, NO trees (temporarily), buildings untextured (P4).
- IMPORTANT: building shaders (buildingShader etc.) do NOT crash GE (WildWest+WildWest2-minus-trees stable);
  only the tree billboard shader did. So P4 is about missing TEXTURES, not crashes.

### LOAD TEST #7 (2026-07-02) — FS25 TREES PLACED ✅
- `tools/place_trees.py`: read docs/tree_placements.json, emit 75,374 `<ReferenceNode referenceId=fileId>`
  under a `gt_trees` group + 12 unique FS25 tree `<File>`s ($data/maps/trees/{species}/{species}_stageNN.i3d) -
  EXACTLY how mapUS places its 16k trees. FS22 stage -> nearest available FS25 stage. Idempotent.
- **Map loads STABLE with all 75k FS25 trees + renders them** (green conifers/oak/birch). Screenshot confirms:
  real landform + FS25 trees + roads + buildings, fences gone. i3d down to 23.6 MB (compact refs vs 226k baked
  shapes). NO FS22 tree shader (analogs bring FS25 shaders) = no crash.
- shot.ps1 upgraded to PrintWindow (PW_RENDERFULLCONTENT) - captures GE viewport even under the user's Claude
  desktop overlay. Red arcs in viewport = editor spline gizmos (traffic/AI paths), not in-game geometry.

## Phase status
P0/P1/P2a/P2b/P3a DONE. Map loads + renders in FS25 GE: landform + FS25 trees + all objects, no fences.
convert.ps1 = full idempotent pipeline (config, fences, trees strip+place, terrain). REMAINING:
- **P4 textures/materials** (biggest visual gap now): buildings are white = FS22 textures ($data/maps/mapAlpine,
  mapFR, FS22 mapUS...) absent in FS25. Full port = bundle FS22 textures into mod + repath refs; migrate any
  FS22-only shaders. (Building shaders don't crash, just missing textures.)
- **P3b terrain paint parity** (currently all-grass base): remap WW's FS22 weight PNGs -> FS25 layer weights.
- Investigate red spline gizmos; in-game FS25 load test; farmlands/fields/environment XML FS25 migration.

### LOAD TEST #1 (2026-07-02) — GE10 opened the converted map
- **Scene graph loaded fine** (reached `onFileOpen`; fence-strip didn't break it; foliage i3ds parsed). The
  large-map culling autoload from gods-thumbprint also applied (helps WW's 8192m map).
- **GE crashed building terrain texture arrays.** Root cause = terrain layer mismatch (exactly P3):
  (1) FS22 layers point at FS22 base textures (`mapFR/`, `mapAlpine/`) absent in FS25;
  (2) FS22 layers have NO height/displacement maps -> FS25 shader needs them -> empty-string texture array ->
  crash (`terrain base layer height texture not set` x98; `Failed to create texture array '' `).
- 3,148 other errors are just missing FS22 textures (cosmetic, fixed in P4) - none blocked the load.
- **Verdict: scene is FS25-loadable; terrain is the single crash blocker. P3 must land before GE opens it.**
- [ ] **P4 Materials/shaders** — migrate FS22 materials → FS25 shaders across all i3ds (full port).
- [ ] **P5 GE10 re-bake + test** — tree prototype swap + re-place; re-bake terrain/shapes; load in FS25; iterate.

## Conventions
- Idempotent tools (re-runnable). Never trigger FS25 mod updates. Never edit the FS22 source in place.
- `.shapes` can be authored/edited in pure Python via the gt_shapes codec if a targeted geometry fix is needed.

## Pasture ground cover: SOLVED + IN-GAME CONFIRMED 2026-07-06 (the foliage type-index saga)
**Symptom (weeks of restarts):** whatever we baked into `densityMap_fruits.gdm`, pastures rendered the WRONG
foliage — broadleaf crops / leggy flowers / bare — never tall mowable grass.

**Root causes (two, independent):**
1. `map.xml` had `<fruitTypes filename="$data/maps/maps_fruitTypes.xml"/>` BEFORE the inline meadowUS block — the
   game honors only the FIRST `<fruitTypes>` element, so MEADOW never registered (stock list auto-loads anyway;
   mapUS ships only the inline block). Fixed in `fs25-empty-map/tools/gen_configs.py` + added
   `<fruitTypeCategory name="MOWER">MEADOW</fruitTypeCategory>` (mowability = MOWER membership).
2. The gdm value encoding is ENGINE-INTERNAL. It is NOT `(childIdx<<5)|state` under any convention (child, child+1
   = manager terrainDataPlaneIndex, skip-waterPlants, XML channel patterns — all disproven). Ground truth came from
   GIANTS Editor: the Foliage panel's checked "Foliage Channels" = the bits of the on-disk value, verified by
   painting meadow@harvestReady in GE and byte-diffing the gdm.

**The values (Wild West / mapUS-ordered FML):** `meadow@harvestReady = 131` (meadowUS = the dense tall grass +
wildflower mix — what mapUS/Back Roads borders actually are), `grass@harvestReady = 134`.
`build_ground_cover.py` bakes pastures 75% meadow@131 + 25% grass@134 (`WW_DIAG_VALUE=<n>` bakes a raw value).
**Confirmed in-game 2026-07-06: borders render tall grass + flowers.**

**Map-agnostic path for the converter:** `tools/dump_foliage_values.py` — enumerates layers (i3d FML children) +
states (foliage XML `<foliageState name>`), runs GE headless (flag-gated `zz_foliage_value_dump.lua`), has the
ENGINE write every (layer,state) into test cells, decodes the scratch gdm → `foliage_values.json`. Run once per
converted map; feed the values to the baker. (Built, pending first validation run.)

**Debug tooling that cracked it:** orchestrator FarmOrch dispatch actions (`fruit-dump` = live g_fruitTypeManager
dump incl. terrainDataPlaneIndex, `read-density`, `paint-grid`) + alembic-live attach; GIANTS Editor as the
authoritative encoder. GE saves clobber baked gdms (and vice versa) — never Ctrl+S GE over a fresh bake.

