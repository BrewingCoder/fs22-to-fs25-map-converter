"""
gen_i3d.py - generate maps/empty16x.i3d (scene graph + terrain node) 100% from scratch. See docs/31_terrain_node.md
for what every element means. NOTHING is copied from another map; every attribute is emitted with intent.
$data/... refs are base-game engine assets (shaders/textures) - allowed, they carry no map data.

Scene = perspective camera + directional sun + terrain node + overview camera + spawn point + empty gameplay.
The terrain is heightmap-driven, so there are NO Shapes and NO .i3d.shapes file.
"""
import os
import xml.etree.ElementTree as ET

HEIGHT_SCALE = 255          # DEM value/65535 * heightScale = world metres

# ---- file registry: filename -> stable fileId, de-duplicated (many DistanceTextures share one $data texture) --
class Reg:
    def __init__(self): self.by_name = {}; self.order = []; self._n = 1
    def fid(self, filename):
        if filename not in self.by_name:
            self.by_name[filename] = str(self._n); self.order.append(filename); self._n += 1
        return self.by_name[filename]
R = Reg()

# ---- nodeId counter (unique per scene-graph node) ------------------------------------------------------------
_nid = [1000]
def nid():
    _nid[0] += 1; return str(_nid[0])

GRASS = "$data/maps/mapUS/textures/terrain/grass01_"
DIST  = "$data/maps/textures/terrain/ground/distance/"
# terrainDetail: (match="groundType;angle;spray") -> $data distance texture basename. Value 0 = bare ground
# (shows the base grass layer) which is all a blank map ever renders; the rest exist so the ground-state system
# is fully wired even though our density is empty.
DISTANCE_TEXTURES = [
    ("1;*;0","stubbleTillage"),("2;*;0","cultivated"),("3;*;0","cultivated"),("4;*;0","cultivated"),
    ("5;*;0","cultivated"),("6;*;0","cultivated"),("7;*;0","sown"),("8;*;0","directSown"),
    ("9;*;0","cultivated"),("10;*;0","cultivated"),("11;*;0","harvestReady"),("12;*;0","harvestReadyOther"),
    ("13;*;0","grass"),("14;*;0","grassCut"),
    ("*;*;1","fertilizer"),("*;*;2","manure"),("*;*;3","liquidManure"),("*;*;4","lime"),
    ("*;*;5","straw"),("*;*;6","maizeChopper"),
    ("1;*;1","stubbleTillageFertilized"),("7;*;1","directSownFertilized"),("11;*;1","harvestReadyFertilized"),
    ("12;*;1","harvestReadyOtherFertilized"),("13;*;1","grassFertilized"),("14;*;1","grassCutFertilized"),
]
# InfoLayers: (name, numChannels, [(groupName, first, num, [(val,label),...])] or None, local_file or None)
INFOLAYERS = [
    ("environment", 4, [("Area Type",0,3,[(0,"Open Land"),(1,"City"),(2,"Village"),(3,"Harbor"),(4,"Industrial"),(5,"Open Water")]),
                        ("Water",3,1,[(0,"No Water"),(1,"Water")])], "$data/maps/mapUS/data/infoLayer_environment.png"),
    ("farmlands", 8, [("Lands",0,8,[(i,f"Farmland {i}") for i in range(1,10)])], "data/infoLayer_farmland.grle"),
    ("indoorMask", 1, [("State",0,1,[(0,"Outdoor"),(1,"Indoor")])], "data/infoLayer_indoorMask.grle"),
    ("navigationCollision", 1, None, "data/infoLayer_navigationCollision.grle"),
    ("tipCollision", 1, [("Type",0,1,[(0,"Default"),(1,"Blocked")])], "data/infoLayer_tipCollision.grle"),
    ("tipCollisionGenerated", 2, [("Type",0,2,[(0,"Default"),(1,"Blocked"),(2,"Blocked Wall")])], "data/infoLayer_tipCollisionGenerated.grle"),
    ("placementCollision", 1, [("Type",0,1,[(0,"Default"),(1,"Blocked")])], "data/infoLayer_placementCollision.grle"),
    ("placementCollisionGenerated", 1, [("Type",0,1,[(0,"Default"),(1,"Blocked")])], "data/infoLayer_placementCollisionGenerated.grle"),
    ("fieldType", 1, [("Type",0,1,[(0,"Default"),(1,"Rice")])], "data/infoLayer_fieldType.grle"),
]


def sub(parent, tag, **attrs):
    return ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})


import json
# The base-game standard terrain layer block, extracted from mapUS (see docs/32_terrain_layers.md). We reproduce
# the whole structure (86 Layers + 37 OverlayLayers + 43 CombinedLayers + 21 CombinedOverlayLayers) referencing
# $data textures, but with OUR OWN weights: empty terrain -> grass01 = full weight, every other layer = one
# shared blank weight. The terrain shader/cache build requires this full structure.
LAYERDEF = json.load(open(os.path.join(os.path.dirname(__file__), "base_terrain_layers.json"), encoding="utf-8"))
_TEXMAP = [("detailMap", "detailMapId"), ("normalMap", "normalMapId"),
           ("heightMap", "heightMapId"), ("displacementMap", "displacementMapId")]


def emit_layer_block(layers_el):
    la = sub(layers_el, "LayerAttributes")
    for a in LAYERDEF["LayerAttribute"]:
        sub(la, "LayerAttribute", **a)
    blank_w = R.fid("data/blank_weight.png"); full_w = R.fid("data/grass_weight.png")
    for L in LAYERDEF["Layer"]:
        attrs = {k: v for k, v in L.items() if not k.endswith("Map")}
        for tk, ak in _TEXMAP:
            if L.get(tk):
                attrs[ak] = R.fid(L[tk])
        attrs["weightMapId"] = full_w if L["name"] == "grass01" else blank_w   # grass everywhere, rest hidden
        sub(layers_el, "Layer", **attrs)
    for O in LAYERDEF["OverlayLayer"]:
        attrs = {k: v for k, v in O.items() if not k.endswith("Map")}
        for tk, ak in _TEXMAP:
            if O.get(tk):
                attrs[ak] = R.fid(O[tk])
        sub(layers_el, "OverlayLayer", **attrs)
    for C in LAYERDEF["CombinedLayer"]:
        sub(layers_el, "CombinedLayer", **C)
    for C in LAYERDEF["CombinedOverlayLayer"]:
        sub(layers_el, "CombinedOverlayLayer", **C)


# The terrain's foliage/vegetation cell system, extracted from base-game mapUS (base_foliage.json). Present in
# every real terrain; absent it was the last <Layers> structural gap. 4 FoliageMultiLayers (groundFoliage, fruits,
# weed, stones), all FoliageType foliage.xml refs are $data. We point each layer at OUR OWN blank local density so
# nothing is actually planted (empty map), but the structure exists so the terrain build is complete.
FOLIAGE = json.load(open(os.path.join(os.path.dirname(__file__), "base_foliage.json"), encoding="utf-8"))


def emit_foliage_system(layers_el):
    fs = sub(layers_el, "FoliageSystem", **FOLIAGE["attrs"])
    for L in FOLIAGE["layers"]:
        base = L["density"].rsplit("/", 1)[-1].rsplit(".", 1)[0]   # e.g. densityMap_fruits
        fml = sub(fs, "FoliageMultiLayer", densityMapId=R.fid(f"data/{base}.gdm"), **L["attrs"])
        for t in L["types"]:
            sub(fml, "FoliageType", name=t["name"], foliageXmlId=R.fid(t["foliage"]))


def build_terrain(cfg, scene):
    t = sub(scene, "TerrainTransformGroup", name="terrain", static="true",
            collisionFilterGroup="0x100", collisionFilterMask="0xfffff9c3", nodeId=nid(),
            heightMapId=R.fid("data/map_dem.png"), patchSize="65", heightScale=HEIGHT_SCALE,
            unitsPerPixel="2", maxLODDistance="750", lodBlendStart="200", lodBlendEnd="300",
            lodTextureSize="8192", lodBlendStartDynamic="50", lodBlendEndDynamic="97",
            detailLodBlendDelta="5", materialId="1", castShadowMap="true",
            occNumLODs="0", occMaxLODDistance="300", occPatchSize="65", occLevel="1",
            occDistanceWeight="1", occMaxAdjacentFaces="100")
    # OccluderLods must be PRESENT (defines the occluder mesh LODs) even though occNumLODs=0 (occlusion culling
    # off). Present-with-occNumLODs=0 is the config a working clean 16x terrain uses: it avoids the non-manifold
    # (which occurs when the child is absent) without triggering the occluder LOD *build* that crashes when >0.
    occ = sub(t, "OccluderLods")
    sub(occ, "OccluderLod", occLodLevel="0", occFaceCount="100", occMaxHausdorffDistance="10",
        occMaxHausdorffDistanceExtra="10", occMinHorizontalDistance="1")
    layers = sub(t, "Layers")

    # 1) the FULL base-game standard layer block (LayerAttributes + 86 Layers + 37 OverlayLayers + 43
    # CombinedLayers + 21 CombinedOverlayLayers) reproduced from base_terrain_layers.json (extracted from
    # base-game mapUS). The terrain shader/cache build REQUIRES this standard structure - a 1-layer terrain
    # crashes the cache build. Empty terrain: grass01 weight = full, every other layer = shared blank weight.
    emit_layer_block(layers)

    # 3) terrainDetail - ground-state decals (blank density -> only value 0 shows)
    td = sub(layers, "DetailLayer", name="terrainDetail", densityMapId=R.fid("data/densityMap_ground.gdm"),
             numDensityMapChannels="11", compressionChannels="11", cellSize="8", objectMask="0xff00ff",
             decalLayer="1", viewDistance="75", blendOutDistance="5", densityMapShaderNames="terrainDetailMap",
             combinedValuesChannels="0 4 0;4 3 0;7 3 0;10 1 0", channelOverlayTypes="groundDetail;;spray")
    for match, tex in DISTANCE_TEXTURES:
        sub(td, "DistanceTexture", match=match, fileId=R.fid(DIST+tex+"_distance_diffuse.png"))

    # 4) terrainDetailHeight - fill/heap-height decals. Uses groundDetailHeight_mat (materialId 2). mapUS's
    # version of that material has ONLY a heightNoiseMap (no specular DDS), so it doesn't crash like WW's did.
    sub(layers, "DetailLayer", name="terrainDetailHeight", densityMapId=R.fid("data/densityMap_height.gdm"),
        numDensityMapChannels="12", compressionChannels="6", cellSize="8", objectMask="0xff00ff",
        decalLayer="2", materialId="2", viewDistance="75", blendOutDistance="5",
        densityMapShaderNames="terrainFillMap", combinedValuesChannels="0 6 0",
        heightFirstChannel="6", heightNumChannels="6", maxHeight="4")

    # 5) runtime displacement (no file)
    sub(layers, "DisplacementLayer", name="terrainDisplacement", size=cfg.disp_size, tileSize="16",
        numChannels="6", cellSize="2", viewDistance="25", blendOutDistance="5", maxHeight="0.2",
        densityMapShaderNames="terrainDisplacementMap")

    # 5b) foliage/vegetation cell system (base-game structure, blank densities = nothing planted)
    emit_foliage_system(layers)

    # 6) info layers
    for name, nch, groups, localfile in INFOLAYERS:
        il = sub(layers, "InfoLayer", name=name, fileId=R.fid(localfile), numChannels=nch, runtime="true")
        for gname, first, gnum, opts in (groups or []):
            g = sub(il, "Group", name=gname, firstChannel=first, numChannels=gnum)
            for val, label in opts:
                sub(g, "Option", value=val, name=label)


def build(cfg, out_i3d):
    root = ET.Element("i3D", {
        "name": cfg.i3d, "version": "1.6",
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "xsi:noNamespaceSchemaLocation": "http://i3d.giants.ch/schema/i3d-1.6.xsd"})
    asset = sub(root, "Asset"); sub(asset, "Export", program="fs25-empty-map/gen_i3d.py", version="1.0")

    files_el = ET.SubElement(root, "Files")        # filled after refs are registered (below)
    mats = sub(root, "Materials")
    # terrain material -> base-game terrain shader
    sub(mats, "Material", name="terrainMaterial_mat", materialId="1",
        customShaderId=R.fid("$data/shaders/terrainShader.xml"))
    # ground-detail-height material (materialId 2) used by terrainDetailHeight. This is BASE-GAME mapUS's spec:
    # groundHeightShader + a single heightNoiseMap custom map, diffuseColor 0. NOTE it has NO Texture/Normalmap/
    # Glossmap - WW added a dummyFillplane_specular.dds the base game doesn't ship, which is what crashed us before.
    ghm = sub(mats, "Material", name="groundDetailHeight_mat", materialId="2", diffuseColor="0 0 0 0",
              customShaderId=R.fid("$data/shaders/groundHeightShader.xml"))
    sub(ghm, "Custommap", name="heightNoiseMap", fileId=R.fid("$data/shared/groundHeightNoise.dds"))

    scene = sub(root, "Scene")
    # The sun (directional Light) is registered with the atmosphere/Lighting system by an onCreate UserAttribute
    # (added after the Scene) -> Environment.onCreateSunLight. THAT callback, not scene position or objectMask, is
    # how the game finds THE sun; without it updateAtmosphere gets a nil light id and spams setLightColor errors
    # every frame. Attributes are base-game mapUS's exact sun.
    sun_id = nid()
    sub(scene, "Light", name="sun", translation="0 400 0", rotation="-47.2604 -28.1861 0",
        objectMask="0x83ff0000", nodeId=sun_id, type="directional", color="1 1 1", emitDiffuse="true",
        emitSpecular="true", castShadowMap="true", depthMapBias="0.001", depthMapSlopeScaleBias="2",
        depthMapSlopeClamp="0.001", depthMapResolution="2048", shadowFarDistance="1000",
        shadowExtrusionDistance="200", softShadowsLightSize="0.5", softShadowsLightDistance="15",
        softShadowsDepthBiasFactor="1", softShadowsMaxPenumbraSize="0.5", numShadowMapSplits="5",
        shadowMapSplitDistancesParameter="0.9", lastShadowMapSplitBboxMin="-1024,-128,-1024",
        lastShadowMapSplitBboxMax="1024,148,1024", range="10000", scattering="true")
    sub(scene, "Camera", name="persp", translation="0 60 0", rotation="-30 0 0", visibility="false",
        nodeId=nid(), fov="60", nearClip="0.1", farClip="10000", orthographicHeight="2200")
    build_terrain(cfg, scene)
    if not cfg.micro_displacement:
        # Kill washboard/diamond corrugation on non-flat terrain WITHOUT breaking the tire-track/snow systems.
        # The corrugation comes from the PER-LAYER micro-tessellation attrs (`displacementMaxHeight` /
        # `displacementScale` on each <Layer>/<OverlayLayer>/<DistanceTexture>) - those are always-on per-texture
        # noise, so we zero them. We do NOT touch the two structural layer maxHeights:
        #   - `terrainDetailHeight` (DetailLayer) maxHeight=4  -> fill/height data; tireTrackSystem + SnowSystem read
        #     it (zeroing it => SnowSystem.updateSnowShader divide-by-zero every frame).
        #   - `terrainDisplacement` (DisplacementLayer) maxHeight=0.2 -> the runtime rut/track displacement; the
        #     engine's tireTrackSystem needs it > 0 to initialize (else g_currentMission.tireTrackSystem stays nil and
        #     ANY mow/drive/plow floods `DensityMapHeightUtil:… tireTrackSystemId` errors and FREEZES the game).
        # Both are driven by the (blank) densityMap_height, which adds 0 height everywhere -> no visible corrugation.
        terr = next(x for x in scene.iter("TerrainTransformGroup"))
        for el in terr.iter():
            if el is terr:
                continue
            for a in ("displacementMaxHeight", "displacementScale"):
                if el.get(a) not in (None, "0"):
                    el.set(a, "0")
    sub(scene, "Camera", name="cameraOverView", translation="0 7300 0", rotation="-90 0 0", nodeId=nid(),
        fov="60", nearClip="0.1", farClip="10000", orthographicHeight="1")
    # spawn point. Registers via onCreate=Mission00.onCreateStartPoint (added below). WITHOUT it the game finds no
    # spawn point -> "Player could not find any valid spawn position". y just above the flat 31 m terrain.
    csp_id = nid()
    sub(scene, "TransformGroup", name="careerStartPoint", translation="0 32 0", rotation="0 0 0", nodeId=csp_id)
    sub(scene, "TransformGroup", name="gameplay", nodeId=nid())   # empty container

    # 100ha owned wheat field, dead centre (STARTER maps ONLY - cfg.starter_field). A conversion sets
    # starter_field=False so the engine injects NO placeholder field; the real map supplies its own fields + crops.
    # field1 carries the field attributes; polygonPoints = the 4 corners; nameIndicator (child 1) doubles as teleport.
    field1_id = None
    if cfg.starter_field:
        fields_id = nid()
        fields_grp = sub(scene, "TransformGroup", name="fields", nodeId=fields_id)
        field1_id = nid()
        field1 = sub(fields_grp, "TransformGroup", name="field1", translation="0 32 0", nodeId=field1_id)
        pp = sub(field1, "TransformGroup", name="polygonPoints", nodeId=nid())
        for x, z in ((-500, -500), (500, -500), (500, 500), (-500, 500)):
            sub(pp, "TransformGroup", name="p", translation=f"{x} 0 {z}", nodeId=nid())
        sub(field1, "TransformGroup", name="nameIndicator", translation="0 0 0", nodeId=nid())

    # Register special scene nodes with FS25 via onCreate scriptCallback UserAttributes - THIS is how the game finds
    # the sun / spawn point / field system (not by name or scene position).
    uas = sub(root, "UserAttributes")
    callbacks = [(sun_id, "Environment.onCreateSunLight"), (csp_id, "Mission00.onCreateStartPoint")]
    if cfg.starter_field:
        callbacks.append((fields_id, "FieldUtil.onCreate"))
    for node_id, cb in callbacks:
        ua = sub(uas, "UserAttribute", nodeId=node_id)
        sub(ua, "Attribute", name="onCreate", type="scriptCallback", value=cb)
    # field1's own attributes (missionAllowed lets contracts spawn; indices point at its polygonPoints/nameIndicator)
    if cfg.starter_field:
        fua = sub(uas, "UserAttribute", nodeId=field1_id)
        for nm, ty, val in (("angle", "float", "0"), ("missionAllowed", "boolean", "true"),
                            ("missionOnlyGrass", "boolean", "false"), ("nameIndicatorIndex", "string", "1"),
                            ("polygonIndex", "string", "0"), ("teleportIndicatorIndex", "string", "1")):
            sub(fua, "Attribute", name=nm, type=ty, value=val)

    # now emit <Files> in registration order
    for fn in R.order:
        ET.SubElement(files_el, "File", {"fileId": R.by_name[fn], "filename": fn})

    ET.indent(root, space="  ")
    ET.ElementTree(root).write(out_i3d, encoding="iso-8859-1", xml_declaration=True)
    n_data = sum(1 for f in R.order if f.startswith("data/"))
    print(f"gen_i3d: wrote {out_i3d}  ({len(R.order)} File refs, {n_data} map-local + {len(R.order)-n_data} $data)")
    return sorted(f for f in R.order if f.startswith("data/"))   # the local files our data gens must produce


if __name__ == "__main__":
    import mapcfg
    build(mapcfg.CFG16, os.path.join(os.path.dirname(__file__), "..", "out", "FS25_Empty16x", "maps", "empty16x.i3d"))
