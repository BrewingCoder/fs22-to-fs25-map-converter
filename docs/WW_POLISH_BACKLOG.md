# Wild West — Polish Backlog

WW-specific polish items to work through one at a time. Not started until the user picks one.

---

## #1 — Streetlamps snag big equipment (remove their collision) — ✅ DONE + IN-GAME CONFIRMED (2026-07-11)

**Problem:** WW is a big map with big equipment driving the streets, and the equipment keeps getting
hung up on the streetlamps' collision. Constant snagging.

**User's proposed approach:** Make a local **copy** of the base-game FS25 streetlamp, add it to our
map as some kind of base/reference asset, **remove its collision**, and place that modified version
instead of the stock one.

**Tech notes (from memory / to verify):**
- `tools/build_lights.py` currently places the base-game **"Street Light"**
  `$data/placeables/mapEU/brandless/lightsResidential/streetLight01/streetLight01.i3d` as an i3d
  **ReferenceNode** at each fixture — **4341 references**.
- That `streetLight01.i3d` has a **static `col` collision shape** (verified earlier) — that's the snag
  source. It also carries LODs + spot(night)/point lights + glow shapes + empty `<UserAttributes>`.
- Approach = vendor a local copy of `streetLight01.i3d` (+ its `.shapes`/textures as needed) into the
  WW map, strip/disable the `col` collision shape (or its collision mask), then repoint build_lights'
  File ref from `$data/...` to the local copy. Keep lights/mesh intact; only kill collision.
- Open Q when we start: strip the `col` node outright vs zero its `collisionMask`/filter; whether the
  `.shapes` needs local copy or the col is inline; keep it map-agnostic (config-drivable) per the
  converter mandate.

**Status:** DONE 2026-07-11 (pending in-game confirm). `build_lights.py` now vendors a LOCAL copy of the
street light with the `col` collider stripped → `maps/vendor/streetLight01/streetLight01_noCol.i3d`
(+ its `.shapes`; textures stay `$data`), and repoints all 4341 references to it. Config knob
`assets.street_light_strip_collision` (default true). Verified: `col` gone, pole mesh + glow + both
Lights intact. Needs FS25 map reload to confirm equipment no longer snags.

---

## #2 — Offset street/field-lining tree splines away from the road — ❌ REVERTED (different path chosen)

**2026-07-11 — REVERTED by user request** ("undo all the recent tree changes. we are going to take a
different path"). All of the offset/clearance/corridor work below was backed out: `build_trees.py`
restored to HEAD (`git restore`), the `tree_palette.road_*` config knobs removed. Trees regenerated to
original positions (75,374, no offset, no culling). **New direction: curate the tree PALETTE instead of
moving trees** — first step, user blacklisted **`northernCatalpa` + `boxelder`** from the deciduous
list (now `["americanElm","oak","aspen","beech","betulaErmanii","shagbarkHickory"]`). The low-hanging-
canopy-over-road problem will be addressed by species choice, not spline offsetting. (Original attempt
notes retained below for reference.)

---

### [superseded] Original #2 attempt — offset tree splines away from the road

**Problem:** We switched the street/field-lining trees to **all hardwood** (deciduous, for seasonal
color). Hardwoods have a **wide, low-hanging canopy** (unlike firs, which grow up and narrow), so now
the roadside trees hang low **over the street** — hurting driving visibility and **blocking equipment
with branches**. WW is a large map with lots of **meadow buffer between the street and the fields**, so
there's room to push these trees back.

**User's proposed approach:** Add a **config customization** to **offset those tree splines a given
distance from the road**. **Start with 10 m.**

**Tech notes (from memory / to verify):**
- `tools/build_trees.py` places trees at WW trunk world-positions. The street/field-lining trees are
  the **spline rows** (single-file lines) — already detectable: build_trees uses **PCA linearity**
  (neighbours within `LIN_R=18m`; `linearity=(l1-l2)/(l1+l2)`; spline rows sit at `lin~0.9`, stands
  `~0.36`, threshold `LIN_TH=0.70`). That same metric that flips single-file conifers→deciduous can
  identify which trees to offset.
- Offset = move each spline tree **perpendicular to its spline, away from the road**, by the config
  distance (default **10 m**). Need the road corridor to know which side is "away" — reuse the
  `build_road_grade` road footprint (or nearest-road-direction) to pick the offset sign.
- Config-drive per the converter mandate: new knob (e.g. `tree_palette.spline_road_offset_m: 10`) in
  `<map>.convert.json`; map-agnostic core.
- Open Qs when we start: offset ALL linear rows or only those within Xm of a road (field-only lines
  shouldn't move)? re-snap Y to DEM after moving; avoid pushing trees onto the field/into other trees;
  10 m may need tuning per-corridor.

**Status:** DONE 2026-07-11 (pending in-game confirm). `build_trees.py` now offsets roadside spline
trees away from the road: reuses the `WW_roads>roads` corridor (like `build_road_grade`) +
`distance_transform_edt(return_indices)` to push each **spline** tree within `road_offset_max_dist_m`
(25 m) of a road **directly away from its nearest road cell** by `road_offset_m` (10 m), then re-snaps
Y. Line shape preserved; both sides of a road part symmetrically; field-lining rows (>25 m from any
road) untouched. Result: **11,021 roadside spline trees moved 10 m**; all 75,374 trees in-bounds, sane
Y. Config: `tree_palette.road_offset_m` / `road_offset_max_dist_m` (0 disables).

**REFINEMENT 2026-07-11 (in-game feedback):** offset alone left spline-END trees poking toward the
street (perpendicular offset at a line's end/curve can push toward a cross-street) + originally-very-
close trees only reached ~12-14 m where wide hardwood canopies still overhang. Added a **clearance
filter** (`tree_palette.road_min_clear_m`, default 12 m): after the offset, DELETE any tree still within
that of a road center. Post-offset distances: main lines sit 15 m+ back, so 12 m culls the ~62
overhangers without gutting lines (14 m≈200, 15 m≈340; don't exceed ~15 m or the lines thin). Live via
junction. Pending in-game confirm the canopies clear the street; one-number tune if not.

**ROOT-CAUSE FIX 2026-07-11 ("birch in the pavement"):** user reported a birch literally IN the road
despite the clearance filter. Diagnosis: the corridor was built from the **`WW_roads>roads` subgroup
ONLY**, but the offending trees sit on **BRIDGE / TUNNEL** road segments (`Bridges`, `tunnels`
subgroups). Measured: roads-only corridor = **0 trees within 12 m** (filter thought it was clean);
all-3-subgroups corridor = **91 trees within 12 m** (27 within 5 m). The offset+filter never saw 2 of
the 3 road subgroups. **Fix:** `road_corridor()` now rasterizes **ALL `WW_roads` subgroups** (not just
`ROADS_GRP`) and **dilates the pivot mask** by `tree_palette.road_footprint_m` (default 5 m ≈ pavement
half-width) so pivots that sit ~on the centerline but are sparse along a road close into a continuous
corridor and on-pavement trees read ~0 m. After fix: **0 trees within 12 m** of the all-groups corridor
(offset moved 12,486; clearance removed 492; 74,882 trees remain). Live via junction; needs map reload.

---

## #3 — Road height mismatch at intersection (vehicles jump + crash)

**Problem:** In one area of the map there are **considerable height issues**. FIRST one: a **crossroads
intersection** where the roadway on the **left is ~0.25 m higher than the one on the right** — you can
see the **vertical facing (riser) of the road shape** at the seam. Vehicles crossing it **JUMP, then
crash**. (User notes this is the first of several height issues in the same area — more to come.)

**Location:** minimap/AutoDrive readout at the spot = **heading 256.4°, X ≈ 1438, Z ≈ 4298** (WW 16x).
Screenshot: `docs/backlog/03_intersection_step_x1438_z4298.png`.

**Tech notes (from memory / to verify):**
- Road SURFACES come from `tools/build_flats.py` — WW's baked road meshes copied **wholesale** with
  their baked world transforms (nested up to 9 deep; XYZ euler). A ~0.25 m step = two adjacent road
  shapes meeting at different Y (either inherent in WW's original mesh transforms, or a seam between
  road segments).
- `tools/build_road_grade.py` carves the **terrain** down to road height — it does NOT alter the road
  meshes, so it won't fix a mesh-vs-mesh step at a junction.
- Likely fix path: a **per-map tweak** (à la GT's tweaks system / a targeted node-Y nudge) to align the
  two segments, OR detect + smooth Y discontinuities between adjacent road surfaces. Decide when we
  start whether it's a one-off nudge for this junction or a general "seam-leveling" pass.
- Get the exact offending shape(s): at that position, find the road-surface nodes whose world-Y differ
  across the seam; nudge the higher one down (or meet in the middle) ~0.25 m.

**RE-CONFIRMED still present 2026-07-11:** user drove back to the seam (readout **heading 7.7°, X ≈ 1437,
Z ≈ 4303**) — the raised road-edge lip/step is clearly visible along the left pavement edge. Screenshot:
`docs/backlog/03_intersection_step_recheck_x1437_z4303.png`. Untouched by the #2 tree fix (separate mesh
issue). Best next step: alembic on the live map to pin the exact offending road shape's world-Y at the
seam, then a targeted per-map tweak to level the two segments.

**FIX IN PROGRESS 2026-07-11 — new `i3d_shape` spatial shape-tweak system:** road shapes are unnamed,
deeply-nested `Shape` nodes sharing `shapeId`s, so the existing name-based `tweaks` couldn't target them.
Added a **spatial shape selector** to `build_tweaks.py` (`"where": "i3d_shape"`): pick mesh Shapes by a
WORLD-coord `area` box (+ optional `group`/`name`/`shape_id`/`expect`), then translate/rotate/delete them;
`dy` is world-space, converted to each node's local frame. It PRINTS every hit's world x/y/z + shapeId so
the box can be tuned (shape origins sit off the visible mesh). The intersection at world (−2663, 200):
east arm = `Street100m` (id506) + `TJunction.000` (id507); first tweak drops them **dy −0.10 m** (`expect: 2`)
to level the seam vs the gas-entrance road to the west. Config lives in `wildwest.convert.json` → `tweaks.list`.
Applied via full `convert.py` (tweaks are the final step; **don't run `build_tweaks` standalone twice** — the
deltas compound). Pending in-game confirm; tune the value/side/box from the printed hits if the step persists.

**REAL ROOT CAUSE 2026-07-11 (found in GIANTS Editor) — STACKED DUPLICATE ROAD TILES:** the `i3d_shape`
dy −0.10 attempt was on the WRONG shape and was reverted (`build_flats` regen restored original Y). In GE
the user lifted the road shape and found an **exact copy directly beneath it**. Diagnosis: the FS22 source
map ships **redundant coincident road tiles** — the SAME mesh (shapeId) at the SAME world position with the
SAME material, stacked **2–3 deep** (WW: **251 locations, 221 tripled + 30 doubled = 472 duplicate copies**;
dominated by shapeId 489, the us_cross surface). `build_flats` copied them through faithfully (source and
output both had 9356 instances / 472 dupes). Three coplanar identical surfaces **z-fight** (shimmer) and the
stacked collision makes vehicles **bump/step** — that's the "jump," not a height mismatch. Verified the 3
copies are byte-identical in every attribute (shapeId 489, material 15, collision 0x601c) except name/nodeId.
They coincide via DIFFERENT parent groups (e.g. `073` under `014` + `001` under a separate identically-placed
group), so only a WORLD-space comparison finds them.

**FIX (user chose dedup over a tiny-Z separation):** added a **dedup pass to `build_flats`**
(`flats.dedup_road_shapes`, default on): walks `WW_roads`, groups Shapes by (shapeId, world-xyz@1cm,
materialIds), keeps the first of each set and removes the coincident twins (~472 removed, 251 unique surfaces
kept). Map-agnostic — any converted map with this source pattern benefits. Applied via full `convert.py`
(buildings/curtains/shop share the map `.shapes`, so `build_flats` can't run standalone). Pending in-game
confirm the shimmer + bump are gone.

**SECOND LAYER 2026-07-11 — template coplanar z-fight (489 vs 490):** after dedup, the user found in GE the
us_cross tile STILL needed a **local Z +0.002** nudge. Cause: the us_cross template stacks TWO DIFFERENT
surface meshes at the same spot — shapeId **489** (`073`) coplanar with shapeId **490** — and dedup (which only
removes same-shapeId twins) correctly left both. They z-fight. Confirmed **template-wide: 248 of ~270 us_cross
crossings** have the 489/490 coplanar pair, so it shimmers at every intersection. The parent chain is rotated
(`hwyEntrance 0 -90 0`, template ~88.66°), so a world dz ≠ the local nudge the user did in GE.

**FIX:** extended `build_tweaks` `i3d_shape` with (a) **`local: true`** (add dx/dy/dz straight to the node's
LOCAL translation, matching GE's Transform panel under a rotated parent) and (b) **optional `area`** (a
`shape_id`/`name`-only tweak hits every instance). Added ONE tweak: `{i3d_shape, group WW_roads, shape_id 489,
translate, local, dz 0.002}` → nudges every 489 tile local Z +2mm map-wide, breaking the coplanarity. Verified
in GE on one instance; applied to all ~270 via full convert. Config-reversible (change dz or delete the entry).

**Status:** dedup (472 stacked twins removed) + template 489-tile local-Z +0.002 (all us_cross) applied via full
convert; pending in-game confirm the shimmer/seam is gone map-wide.

---

## #4 — Repeating deep craters in the road shoulder — DONE (pending in-game confirm)

**Problem:** The **shoulder/verge** of the road has **giant, deep craters** that **repeat** down its
length. Any vehicle that's a bit wide, or drifts slightly off the pavement, drops into one and
**crashes**. Same corridor as #3, ~20 m west.

**Location:** readout at the spot = **heading 269.3°, X ≈ 1369, Z ≈ 4298** (WW 16x). Same road/area as
#3 (Z ≈ 4298; X ~1370–1440). Screenshot: `docs/backlog/04_shoulder_craters_x1369_z4298.png`.

**Tech notes (from memory / to verify):**
- Strong smell of a **`tools/build_road_grade.py` artifact**. That tool carves the terrain DOWN to road
  height along the corridor: composes each road-surface shape's world transform → rasterizes the
  footprint → `scipy.distance_transform_edt` for corridor + **nearest-road-height**, weighted
  **4 m core → 11 m shoulder** taper, **LOWER-only**. The **"repeat"** pattern is the key clue —
  periodic depressions suggest a **stationing / nearest-neighbour sampling artifact** (scalloping
  between sampled road stations, or the nearest-road-height pulling the shoulder down to a lower
  segment's Y) in the shoulder taper zone just off the pavement.
- Tuning knobs live in build_road_grade: **CORE_M / SHOULDER_M / SKIP_M**. Fix likely = smooth the
  carve height field (interpolate along the corridor instead of nearest-station) and/or soften the
  shoulder taper so it can't gouge pits.
- Confirm it's terrain (carve) vs road-mesh shoulder geometry by checking terrain-Y vs pavement-Y along
  the shoulder at this spot when we start.

**ROOT CAUSE (confirmed by DEM analysis):** it WAS the terrain carve. `build_road_grade` carves terrain
down to each road SHAPE-ORIGIN's Y. 268 road origins sit >2 m below terrain, and 267 of those are
**lone-low pivot/sub-shape outliers** (down to −23 m below their road neighbours) — NOT continuous cuts.
The carve dug a ~5 m pit (SKIP_M cap) around each → 87 repeating shoulder craters.

**FIX (DONE 2026-07-11, verified):** `build_road_grade.py` drops road-origin points whose Y deviates
> `OUTLIER_DROP_M` (2.5 m) from their local road-neighbour MEDIAN (radius `OUTLIER_R`=15 m) before
rasterizing. A straight/steep grade sits ~at its neighbour median (dev≈0) so real roads are untouched;
continuous cuts kept. Result: dropped 710 outliers (8784→8074 kept); **carve>1.5 m clusters 87→0**, max
carve **5.00→0.88 m**, roads still flush (buried 15%→0%). Constants global (generic tuning, like
CORE_M/SHOULDER_M/SKIP_M). NOTE: road_grade runs BEFORE trees in `convert.py`, so a **full
`convert.py` rebuild** is needed to re-snap tree Y onto the corrected DEM (standalone re-run only fixed
the DEM). Needs in-game confirm the shoulder pits are gone.

**FOLLOW-UP 2026-07-11 — "air under the pavement" (same section, distinct failure):** user reported the
crater area is now *low* with **air visible under the pavement edge** — the road mesh floats over terrain
that sits below it. Root cause: `build_road_grade` was **lower-only** (carve terrain DOWN to meet the
road; never raise). Anywhere terrain sat *below* the pavement → floating road → air gap. Measured that
section (world ≈ −2742, 208; see coord note below): **17 of 35 road shapes floating 0.3–2 m above
terrain** (mapwide: 8% of road cells floating >10 cm). **Fix:** road_grade now grades **symmetrically** —
LOWER where buried (unchanged) AND **RAISE terrain up to meet floating pavement**, both tapered over the
CORE→SHOULDER band. Capped by `FILL_MAX_M` (2.5 m): small floats (at-grade roads / overpass ramps sitting
proud) are filled; genuine **bridge/valley spans** (gap > cap, e.g. the 11.99 m max) are left floating as
intended. Result: **floating road cells 8% → 1%** (residual = the real bridges), fill max 2.50 m (capped),
carve/crater fix intact (buried 15%→0%, carve max 0.88 m). In the trouble section: floating 17 → **0**,
gap now ≤ 0.23 m (flush). `FILL_MAX_M` is a global tuning constant like CORE/SHOULDER/SKIP. NOTE: #3 (the
~0.25 m mesh-vs-mesh step) is SEPARATE — it's pavement-surface-vs-pavement-surface, not terrain, so this
terrain fill does not address it.

**FILL REVERTED 2026-07-11 (caused worse artifacts):** in-game, the symmetric fill produced a raised/
sunken tan terrain blob **at every road-shape intersection** (user: "it's like that at every shape
intersection"; "the end of the street shapes ... lowering them BELOW the ground") — because road pivots
are SPARSE dots, so `distance_transform` spreads each pivot's fill in a circular fan → raising builds
mounds and buries pavement ends (lowering had blended invisibly). Backed out the fill: `build_road_grade`
is lower-only again; `FILL_MAX_M` removed. **KEPT the crater outlier-drop** (the confirmed part of #4 —
drop road-origins >2.5 m off local median). A proper "air under the pavement" fix needs the DENSE
pavement FOOTPRINT (actual road-mesh vertices), not sparse pivots + a fan — deferred.

**Status:** crater outlier-drop DONE (kept); symmetric-fill for air-gaps REVERTED (blob artifacts).
Air-under-pavement remains OPEN — revisit only with a real footprint mask, not pivot fans.

---

## #5 — Harvest yield ~10% short of contract expectation (economy disconnect)

**Problem:** A disconnect between our **fruit harvest yields** and what the game **expects** for a field.
Ran **3 contracts on NPC-generated fields**, each with the standard **91% delivery goal** (you only need
to deliver 91% of what the game thinks the field should produce). On **all 3**, the user came up **~10%
short of that 91% goal** despite **harvesting + delivering 100%** of what the field actually produced.
So our fields yield materially **less than the game's economic model expects for their area**.

**Validation:** Ran contracts on **Back Country** (base/refmap) — had **plenty of product left over** on
both. So it's **WW-specific**, not a general FS contract quirk. WW short on all 3; base surplus on both.

**User's proposed approach:** Likely need **alembic** to pull **real numbers on an active job** —
expected liters vs actual harvested liters — to localize the disconnect.

**Tech leads / hypotheses (to test):**
- **Expected-yield basis = field AREA × fruit yield/sqm.** If the contract's "expected production" is
  computed from the declared field **polygon area** but our **planted foliage doesn't fill that area**
  (headlands, meadow/exclusion masking, foliage not reaching the field edges, or the plow/plantable
  mask smaller than the field poly), actual harvest < expected → systematic short. Compare field-poly
  area vs actual harvestable-foliage area.
- **Growth / yield state:** if crops aren't reaching **full growth-state yield** (the seasonal growth
  curve / `fruitTypes` wiring was flagged incomplete in [[wild-west-fs25-conversion]] — grass
  "short-but-not-tall"), harvest liters would be systematically reduced across every field. A ~10%
  flat deficit smells like a yield-multiplier / growth-state issue, not random.
- **Fruit economy params:** WW fruit registry vs base — check `litersPerSqm`/yield in the fruitTypes
  the fields use vs what the base game expects for the contract math.
- **Foliage density encoding:** densityMap_fruits value / cell coverage slightly under full.
- **Alembic plan:** attach to a running job, read the contract's expected-liters field + the field's
  computed area + FruitType yield params + actual harvested liters as it runs; diff expected vs actual
  to see whether the gap is AREA (coverage) or PER-SQM (yield/growth).

**Status:** SHELVED 2026-07-11 — blocked on a good playable savegame (user deleted the test saves during the
road-fix iteration). Resume once there's a stable save with an active contract to profile via alembic
(expected-liters vs actual). Bigger investigation.

---
---

# V-Next / Esoteric Features

Forward-looking feature ideas (not bugs). Numbered F1, F2, … Discuss + design before building.

---

## F1 — Deliberate field entrances / access paths from the road

**Seed observation:** At **Field 10** (readout **heading 239.6°, X ≈ 4735, Z ≈ 4348**) there's a
natural-looking **path cutting through the meadow toward the road** — a de-facto field entrance. The
user didn't expect it (thought the pasture was purely **ground-texture meadow**), and wants to
**identify the mechanic that generated it**. Only seen at Field 10 so far, but only ~10 fields visited.
Screenshot: `docs/backlog/F1_field10_path_x4735_z4348.png`.

**Part A — investigate:** figure out what produced this path. Candidates: a **dirt/path strip in WW's
FS22 ground texture** carried over by `build_ground_texture`; a **gap in the pasture-foliage mask**
(build_ground_cover) where a WW weight layer or field-edge exclusion left a bare lane; a leftover
spline/trail; or a field-headland artifact. Nail down the source so we can control it.

**Part B — the feature (deliberate entrances):**
- Give **each field an entrance like this** from the road — possibly **TWO** entrances per field.
- Add a **square/perimeter path around the field** that sits **halfway between the road and the
  workable field area** (a ring access track).
- **Pick entry points where the terrain is generally FLAT** — avoid steep field edges (especially
  near **highway overpasses**, which have steep grade). **AVOID example:**
  `docs/backlog/F1_AVOID_overpass_slope_x4692_z2323.png` (heading 117.9°, X ≈ 4692, Z ≈ 2323 /
  "Field-14") — ground ramps up steeply to an overpass (occluded by trees upper-left); a flatness
  test on the DEM at candidate entry points should reject spots like this.
- **Keep clear of obstacles:** no **trees** or **streetlights** within **~20 m** of each entrance
  (coordinate with `build_trees` #2 offset + `build_lights` #1, and the tree/light placement passes).

**Tech notes / cross-cutting (to design):**
- Touches: field polygons (`build_fields`/`ww_fields`), road corridor (`build_road_grade`/roads
  footprint) to find road-adjacent + flat entry candidates, terrain slope (DEM) for flatness test,
  ground texture + foliage (carve the path + clear meadow along it), and obstacle clearing in
  `build_trees` / `build_lights` (20 m exclusion around entrances).
- Config-drive per the converter mandate (entrances-per-field, ring offset = halfway road↔workable,
  flatness threshold, obstacle clearance radius).
- Sequencing: entrances must be computed BEFORE tree/light placement so those passes can honor the
  20 m exclusion (or a post-pass that removes trees/lights inside entrance zones).

**Part A ANSWERED 2026-07-12:** the Field 10 "path/tire-tracks" is **painted FS22 ground-texture detail**,
carried over by `build_ground_texture`. The `pathway01-04` layers are painted **0%** (unused); `dirt` is
painted ~13% (fields + lanes). So it's not a spline/physics-track/queryable route - just brushed dirt detail.
`build_ground_cover` keeps meadow foliage OFF `dirt` (`EXCLUDE_LAYERS`), which is *why* a dirt strip shows
through the grass as a bare lane.

**Part B ATTEMPT 1 (2026-07-12) - built, then BACKED OUT:** wrote `build_field_entrances.py` (convert step
16): for each field, up to 2 road-adjacent (<=25 m) + flat (<=6 deg) entrances; paint a dirt (mudDark) wheel
track road->field; clear meadow foliage on the track; remove trees+lights within 20 m. Ran clean: **107
entrances / 55 of 82 fields**, 350 trees + 24 lights cleared. **User reported it "turned the whole fields
into grass texture" -> removed from convert.py + full-convert restore.** BUT a full data audit could NOT
reproduce field corruption from the tool: gdm round-trip lossless, **0 field-interior gdm cells changed**
(all cleared cells are meadow/track), mudDark only adds track px (fields stay ~78% dirt), PNG format
identical. So the tool provably doesn't touch field texture/foliage. **Next time: reproduce the "grass" LIVE
(screenshot a field with entrances applied) before re-enabling** - may be the meadow/track look or an unrelated
interaction. Tool + config (`field_entrances`) retained, disabled; identification/paint/clear logic intact.

**Part B ATTEMPT 2 (2026-07-12) - re-enabled, WORKS:** re-ran in-pipeline. In-game the dirt entrance track
reads correctly (bare dirt lane through the tall meadow) and the FIELDS look fine (crops intact) - the earlier
"fields->grass" was NOT the field interiors (data audit already proved that); not reproduced. New refinement
from the user: "entrance cannot be near a lightpost; should split the lightposts in the middle." Added
**pole-gap-aware placement**: load WW_lights positions (KDTree), and for each candidate stretch pick the point
whose ROAD CROSSING is FARTHEST from any streetlight -> the track threads the gap between poles. Split clearance:
TREES within `clear_trees_m` (20), STREETLIGHTS only within `clear_lights_m` (6) of the crossing (so poles are
KEPT, not deleted). Result: entrances now sit a median **18.5 m** from the nearest pole (min 7.6 m), **0 poles
removed**, 171 trees cleared. Config knobs updated (`clear_trees_m`/`clear_lights_m` replace `clear_radius_m`).

**Status:** Part A done. Part B WORKING (dirt tracks + pole-gap threading + tree clearance); in-game re-test after
the pole-threading rebuild. Remaining polish ideas: tune min pole-gap, track width, `into_field_m`; the 27 no-road
fields still get nothing (need longer access tracks). Then F2 can spur off these entrance points.

---

## F2 — Auto-generate the AutoDrive graph from roads + field entrances

**Feature:** Programmatically generate the map's **AutoDrive waypoint graph** instead of hand-placing
it. We **know where the roads are** (we place them) and — once **F1** lands — **where the field
entrances are**, so the whole network can be derived from that geometry. (Related to / depends on F1.)

**Inputs (all data we already produce or will):**
- **Road placements / centerlines** — from the road surfaces (`build_flats`) / road corridor
  (`build_road_grade` rasterized footprint) / WW's original road splines if recoverable. Centerlines
  → waypoint chains; **intersections → junction nodes** (multi-connection).
- **Field entrances (F1)** — each entrance → a **spur** off the nearest road waypoint into the field,
  plus a **named marker** (destination) per field entrance.
- Optionally: sell points / farmyard / production placeables (`build_placeables`) → markers so AD can
  route to unload/sell locations (e.g. the "Western Port-Unload" marker seen in-game).

**Output:** an **AutoDrive network** = waypoints (id, x/y/z, connections, flags: dual/reverse) +
**markers** (named destinations). Format = AutoDrive's route XML (per-map config in the savegame,
`AutoDrive_<map>_config.xml` / the AD routes file).

**Tech notes / to verify:**
- We already have a **hand/baseline AD network** for WW in-repo ([[wild-west-autodrive-baseline]],
  `C:/repos/wild-west-fs25/autodrive`) — use it as the **format reference + validation target** (does
  our generated graph load + drive like the baseline?).
- Waypoints follow road centerlines at a fixed spacing; connections dual (bidirectional) on 2-way
  roads; junctions link crossing chains; snap Y to DEM. Lane offset from centerline optional.
- Config-drive spacing, marker naming, which placeables become markers. Map-agnostic core (reads the
  same road/entrance data any converted map produces → AD graph "for free").
- Sequencing: runs AFTER roads + F1 entrances (+ ideally placeables) are placed.

**Status:** backlog (v-next). Depends on road data (have it) + F1 entrances. Validate against the
existing WW AD baseline.
