# FS22 to FS25 Map Converter

## What it does:
- Converts an FS22 map to an FS25 Map, replacing FS22 game objects with similar (to a degree) FS25 objects. This doesn't "transplant" data as much as it "translates".
- It reads the terrain, the fields, the crop types, the trees, etc. and builds a fully from-new FS25 map for you.
- This is a general tool; you can see the two map configs I've been working with while developing the tool and easily create one for any of your desired maps.

## What it DOES NOT do:
- It does NOT distribute ANY artifacts as part of this repository. Those artifacts are sometimes (c) the original author. and we respect that. So you won't find any map downloads here. Just the tools to covert them.
- It does NOT add NEW FS25 features to an FS22 map.  It will inject FS25 features (like production points, Similar trees, etc) when it does find something that it can translate.  For example if your map author used custom trees, or FS22 trees, it attempts to deduce the tree type and choses a similar FS25 tree.

## Where it is at:

This is DECIDEDLY a beta product.  It's part of a bigger effort that I've created for map development based on a designer that is not as obtuse and is far more automated than Giant's editor (Think ARC-GIS ability with auto map creation). While working on that effort I had to write code to basically create GRLE, DEM, GDM, i3D files by hand. Creating those libraries unlocked the ability for me to read FS22 maps into their core definitions, then rebuild them  with FS25 objects.  

With that said; this tool NEVER writes to your FS22 maps. So there's no harm in at least trying it; 

Known issues:

- Snow -- haven't gotten there; don't know when I will
- Required mods -- You'll see that I own a copy of Levi's Map.  Was a supporter of Levi for a long time. his FS22 map is by far one of my favorites, right up there with Cazz64's Wild West (Get better dude, we miss your mods).  Unfortunately a lot of maps ship with "Required mods" that are home grown; like Levi's.  The tool doesn't support converting those yet.


# The Technical Details

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
