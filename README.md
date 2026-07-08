# FS22 to FS25 Map Converter

## Preamble (More my ramblings)
What was your favorite map EVER?  Mine was probably Fenton Forest or Hazzard County under FS17? Why? With Fenton forest it was because the map seemed to have biomes. You started by a river. You could go north into the hills or far north into extremely steep slopes. If you wanted flat you head across the bridge over to the island and work that. It had everything.  Hazzard was exciting to me because it felt the same way; but was US based with a bit of a red-neck twinge.  And I am decidedly a RED NECK.  

There are two things I really don't like about the Farming Sim modding community.  The first and foremost is peoples attempt at monetization -- especially people that add LUA script that disable thier mods after some period of time so those without the ability to script are forced into continued expensive patreon subscriptions just to keep using them.  Or the modders that hid thier mods behind other forms of pay-walls -- and charge excessive amounts of money to build you that custom truck with a Carolina Squat (YUK).    The second thing is less a "Don't like" but more of a sadness.  It's that good modders leave; or leave behind a map.  I get it -- you move on to other games to work with -- I know I flip games all the time. I can never decide from day to day whether I want to build FS 25 maps, Do custom C# in Space Engineers, work on my Minecraft Mods, or [Redacted].  The real shame is that Giant doesn't offer a means to save these old maps... Seriously; I want to farm, this game is, after all, a farming simulator. It's not not an Emergency Services Simulator. (Yes.. I'm referring to the recent DLCs).  These old maps deserve version ports.  If I ever get around to converting either Hazzard County or Lousiana Bayou I have permission to release those mods from the author.  But generally speaking I respect modders; and won't release versions I've converted -- which is why I'm releasing the tooling I used to covert a few maps myself.  This tool is in its infancy; and it's not for the weak of heart.  You need python (Try the Windows Store, it's free) and you need to know what you're doing.  But if the stars align for you this tool will DEFINITELY Convert Cazz's Wild West 16x map from FS22 to FS25.  It will *probably* mostly convert any smaller FS22 map that uses base game data$ elements.  There are a TON of customizations you can tweak by creating a custom configuration for a translation -- and I'll eventually get around to documenting them. 

Soap box complete.  Proceed with trepidation:


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
