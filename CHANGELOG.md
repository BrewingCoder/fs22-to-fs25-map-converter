# Changelog

All notable changes to the FS22 → FS25 Map Converter are recorded here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/); versions are date-stamped.

## [0.2.0] - 2026-07-09

### Added
- **Per-map "tweaks" system** (`tools/build_tweaks.py`): declarative per-map overrides applied as the final
  conversion step — rotate / translate / scale / delete a named placeable or i3d node. One-off fixes (e.g. a sell
  point facing the wrong way) live in the map's `convert.json` and survive every re-run. Fails loud on 0 or >1 matches.
- **Buy points**: buying stations (buy manure, lime, fertilizer, seeds, DEF, …) are generated from the source map's
  own native buy-point models, with the FS22 → FS25 trigger collision rewired so the load trigger actually fires.
- **FS22 required-mods awareness**: config `source.mod` resolves the source map against your FS22 mods folder, and the
  GUI gained an "FS22 mods folder" field.

### Fixed
- **Owned starter fields now load as workable farmland.** FS25 auto-populates only NPC/contract fields from their
  polygon; the player's *own* (`defaultFarmProperty`) fields need an explicit ground state or they sit as un-workable
  raw terrain. `fields.xml` now emits a PLOWED, ready-to-plant entry for every owned field (matching how base-game
  mapUS ships its starter fields). NPC fields stay blank so FS25 still auto-populates them.
- **Field-number parity (FS22 ↔ FS25).** FS25 numbers a field by the id of the farmland under it, whereas FS22 uses the
  field node name. Field parcels are now relabelled to their FS22 field numbers so distributed AutoDrive / Courseplay
  configs line up. Gracefully skips (keeps FS22 ids) when a map places several fields on one farmland parcel.
- **Map-agnostic external shapes filename.** The `<mapname>.i3d.shapes` file is now derived from the map identity
  instead of a hardcoded `wildwest.i3d.shapes`, so converting any non-Wild-West map no longer fails at the
  water/buildings/curtains steps (`build_flats`, `build_buildings`, `build_curtains`).
- **Custom crops are sellable.** The crops step runs before placeables so general sale points read the custom crop
  fills and accept them.
- **Robustness on maps whose scene groups differ from Wild West.** `build_lights` and `build_trees` now skip cleanly
  ("0 found — skipped") instead of crashing when a map has no matching light/tree group; farmland parity degrades
  gracefully; `placeables.xml` lookup also finds `placeablesSINGLEPLAYER.xml` / `…MULTIPLAYER.xml`.

### Changed
- GUI (`convert_ui.py`): freeze-aware paths and timestamped per-run file logging.
- PyInstaller/frozen-aware tool dispatch and output redirection (`convert_env.py`).

## [0.1.0] - 2026-07-08

### Added
- Initial public release: portable, self-contained FS22 → FS25 map converter, extracted from the local development
  repo (no map artifacts distributed — tools only).
- One-command pipeline (`python tools/convert.py`) that reads an FS22 map and builds a from-scratch FS25 map:
  terrain, farmland, fields, ground textures, roads/bridges/collision, buildings, curtains, crops, placeables,
  lights, trees, water, and the vehicle shop.
- Two worked map configurations to model your own from (Wild West 16x, West End).
- Python GUI launcher (`convert_ui.py`) with run logging.
- Vendored, reverse-engineered GIANTS asset codecs (GRLE / GDM / .i3d.shapes) so the pipeline needs no GIANTS Editor.
