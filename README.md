# wild-west-fs25

Python-driven conversion of **Wild West 16x by Cazz64/OAG** from Farming Simulator 22 to Farming Simulator 25,
as a **full custom-asset port** (the map should look identical to the FS22 original).

- **Output mod:** `src/FS25_WildWest_16x/` (working base copied from the FS22 source, transformed in place; git-ignored)
- **Tools:** `tools/` — idempotent conversion scripts (recon, fence-strip, tree-swap, terrain remap, material migration)
- **Plan + status:** `docs/CONVERSION.md`

## Hard rules
- **No fences** anywhere in the map.
- **Trees** use FS25 base-game analogs (no FS22 tree ports — they lack seasonal variants).
- Never edit the FS22 source in place; never trigger FS25 mod updates.

## Quick start
```
python tools/scan_i3d.py        # inventory the scene (File refs, node types, materials, trees, fences)
```

See `docs/CONVERSION.md` for the format deltas and phase plan.
