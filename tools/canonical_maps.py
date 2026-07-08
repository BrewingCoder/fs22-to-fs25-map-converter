"""
Canonical, MAP-AGNOSTIC FS22->FS25 lookups for the conversion tool. These are UNIVERSAL (ship with the tool);
a per-map config may override any entry. Keep US (mapUS/mapAS) targets only - NO EU assets.
"""

# FS22 terrain ground-type (layer name, minus the 01-04 variant) -> FS25 base-game US terrain layer.
# Left side = what FS22 maps commonly name their layers; right side = layers present in the mapUS terrain node.
GROUND_FS22_TO_FS25 = {
    # grass family
    "grass": "grass", "grassDry": "grassDirtPatchyDry", "grassDryPatchy": "grassDirtPatchyDry",
    "grassCliff": "grass", "grassFresh": "grassFreshShort", "meadow": "grass",
    # dirt / mud family
    "dirt": "mudDark", "mud": "mudDark", "animalMud": "mudDark", "mudDark": "mudDark", "mudLight": "mudLight",
    "pathway": "mudTracks", "dirtDark": "mudDark",
    # gravel family
    "gravel": "gravel", "gravelDirt": "gravel", "gravelDust": "gravelSmall", "gravelGrass": "gravel",
    "gravelMoss": "gravelPebblesMoss", "pathwayGravel": "gravel", "riverBed": "gravel",
    # hard surfaces
    "concrete": "concrete", "concreteTilesAlpine": "concrete", "plate": "concrete", "cobblestone": "concretePebbles",
    "asphalt": "asphalt", "asphaltAlpine": "asphalt", "asphaltDusty": "asphaltDusty",
    # rock / sand / forest
    "rock": "rock", "mountainRock": "rock", "mountainRockDark": "rock", "stone": "rock",
    "sand": "sand", "beachSand": "sand", "beachSandWet": "sand",
    "forestGround": "forestLeaves", "forest": "forestGrass",
    # misc that usually carry ~no weight -> harmless fallbacks
    "waterPuddle": "mudDark",
}

# FS22 tree species (from tree node name "{species}[Var##]_stage##") -> FS25 base-game analog dir under
# $data/maps/trees/. NO FS22 tree ports (they lack seasons). Unknown species fall back to oak.
TREE_FS22_TO_FS25 = {
    "birch": "betulaErmanii", "oak": "oak", "pine": "pinusSylvestris", "spruce": "lodgepolePine",
    "stonepine": "pinusTabuliformis", "maple": "acerCampestre", "beech": "fagusSylvatica",
    "willow": "salixAlba", "poplar": "poplar", "aspen": "populusTremula", "ash": "fraxinusExcelsior",
    "chestnut": "aesculus", "linden": "tiliaCordata", "cypress": "cupressus", "fir": "lodgepolePine",
    "cedar": "pinusTabuliformis", "alder": "betulaErmanii", "elm": "oak", "walnut": "oak",
}
TREE_DEFAULT = "oak"

# FS25 fruits-density foliage encoding (constant across the 11ch [6,5] format we build).
FOLIAGE_GRASS_STATE = 3      # harvestReady = tall pasture grass
# grass typeIdx is DERIVED per map from the FoliageType order (1-based) - see analyze_map.py.
