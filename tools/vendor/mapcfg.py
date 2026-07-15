"""
Map-size configuration. EVERYTHING scales from MAP_M (map size in metres). Ratios verified from the working 16x
build (see the corpus). A 16x FS25 map = 8192 m; a 4x map = 4096 m (unitsPerPixel stays 2).

Only resolutions scale. Terrain attributes (patchSize 65, heightScale 255, the occluder set, the standard layer
block, the foliage system) and absolute-metre things (the 100ha field, the sun, spawn) are size-INDEPENDENT.
"""


class Cfg:
    UNITS_PER_PIXEL = 2

    def __init__(self, map_m, name, tag, mod=None, title=None, i3d=None, starter_field=True, micro_displacement=False):
        self.map_m = map_m                                   # 8192 (16x) / 4096 (4x); map.xml width/height
        self.name = name                                     # "Empty16x" - map id
        self.mod = mod or f"FS25_{name}"                     # mod folder (override for conversions, e.g. FS25_WildWest_16x)
        self.i3d = i3d or f"{name.lower()}.i3d"              # empty16x.i3d (override to reuse a source i3d name)
        self.title = title or f"Empty {tag}"                 # "Empty 16x" (override with the real map title)
        self.starter_field = starter_field                   # starters get the 100ha placeholder field; conversions=False
        self.micro_displacement = micro_displacement         # FS25 terrain-layer displacement. OFF by default: we never
        #   populate the FS25 height-detail density, so it corrugates any non-flat terrain (washboard/diamond bumps).
        self.dem_res = map_m // self.UNITS_PER_PIXEL + 1     # 4097 for 8192 (patch edges share -> +1)
        self.weight_res = min(map_m, 8192)                   # 1 px/m, CAP 8192^2 (West End 64x keeps weights 8192^2)
        # 2 px/m, but CAPPED at 16384^2. Larger density textures OOM the GPU: a 64x at 32768^2 crashed with
        # "Failed to allocate ImageResource for DensityTexture, probably out of memory". Capped -> 64x = 1 px/m
        # (same density-texture size as the working 16x, so it fits). .gdm mapSizeLog = log2(res) - 5.
        self.density_res = min(map_m * 2, 16384)
        self.overview_res = map_m                            # 8192 (1 px/m)
        self.disp_size = self.density_res                    # DisplacementLayer size = density res
        d = self.density_res                                 # infolayer .grle resolutions
        # RESOLUTION MATCHED TO A PROPER WORKSHOP MAP (Huron County), 2026-07-14. The field-work + field/farmland
        # rasters were previously d//4 (a "verified vs Kansas" guess) = 4096^2 on a 16x map = 2 m/cell. Huron ships
        # them at FULL density res (16384^2 = 0.5 m/cell), farmland at d//2 (8192^2 = 1 m/cell). At d//4 our field
        # BOUNDARIES quantize to 2 m steps, so a field's reported areaHa (which getMaxCutLiters sizes the harvest
        # requirement from) diverges from its true crop-covered area -> harvest contracts come up short. Match Huron.
        self.il_res = {"indoorMask": d, "tipCollision": d, "tipCollisionGenerated": d,
                       "navigationCollision": d // 2,
                       "placementCollision": d // 2, "placementCollisionGenerated": d // 4,
                       "farmland": d // 2, "fieldType": d,
                       # field-work LEVEL maps (fertilizer/lime/plow/roller/stubble) at FULL res like Huron.
                       # Required for spray/fertilize contracts + the fertilizer harvest bonus. See fs25-empty-map#1.
                       "sprayLevel": d, "limeLevel": d, "plowLevel": d,
                       "stubbleShredLevel": d, "rollerLevel": d,
                       # field weed system (crop weeds / herbicide). blank = no weeds. else map.xml borrows mapUS's.
                       "weed": d}


CFG16 = Cfg(8192, "Empty16x", "16x")
CFG4 = Cfg(4096, "Empty4x", "4x")
CFG64 = Cfg(16384, "Empty64x", "64x")   # 64x = 16384 m; density + weights CAPPED (see Cfg) - loads clean, matches West End
