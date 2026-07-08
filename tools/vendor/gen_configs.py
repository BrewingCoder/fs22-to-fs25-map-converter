"""
gen_configs.py - map.xml, farmlands.xml, modDesc, overview.png, empty start configs, icon. Scales from cfg:
map size -> map.xml width/height + overview resolution; cfg.name/title/i3d name the mod. See 10_moddesc / 20_mapxml.
$data sub-config refs are base-game engine defaults (carry no map data).
"""
import os
from PIL import Image

MAP_XML = '''<?xml version="1.0" encoding="utf-8" standalone="no" ?>
<map width="{m}" height="{m}" imageFilename="maps/overview.png" mapFieldColor="0.1500 0.1195 0.0953" mapGrassFieldColor="0.1470 0.1441 0.0823">
    <filename>maps/{i3d}</filename>
    <sounds filename="$data/maps/mapUS/sounds/sounds.xml" />
    <environment filename="$data/maps/mapUS/config/environment.xml" />
    <weed filename="$data/maps/mapUS/config/weed.xml" />
    <fieldGround filename="$data/maps/mapUS/config/fieldGround.xml" />
    <!-- Register MEADOW as a growable/mowable fruit ON TOP of the auto-loaded stock list. The game reads ONLY THE
         FIRST <fruitTypes> element in map.xml: shipping an explicit stock-list element first (as we did until
         2026-07-06) makes the game IGNORE this block entirely - the log then shows the 25 stock fruits load and
         MEADOW never registers. The stock $data/maps/maps_fruitTypes.xml auto-loads when no filename override is
         given (mapUS.xml itself carries only this inline block), so this must be the ONLY fruitTypes element. -->
    <fruitTypes>
        <fruitType filename="$data/foliage/meadow/meadowUS/meadowUS.xml" />
    </fruitTypes>
    <fruitTypeCategories>
        <fruitTypeCategory name="MOWER">MEADOW</fruitTypeCategory>
    </fruitTypeCategories>
    <fillTypes filename="$data/maps/maps_fillTypes.xml" />
    <densityMapHeightTypes filename="$data/maps/maps_densityMapHeightTypes.xml" />
    <bales filename="$data/maps/maps_bales.xml" />
    <additionalFiles>
        <!-- Material holders the engine binds runtime systems to. WITHOUT tireTrackMaterialHolder +
             fillPlane_materialHolder, g_currentMission.tireTrackSystem is nil and DEPOSITING anything on the
             ground (mower/forager windrow, straw swath, heaps) throws DensityMapHeightUtil:tireTrackSystemId nil
             every frame and FREEZES the game. A from-scratch map must load these (all base-game $data). -->
        <additionalFile filename="$data/shared/materialHolders/tireTrackMaterialHolder.i3d" />
        <additionalFile filename="$data/fillPlanes/fillPlane_materialHolder.i3d" />
        <additionalFile filename="$data/effects/effects.xml" />
        <additionalFile filename="$data/shared/materialHolders/tensionBelts/tensionBeltMaterialHolder.i3d" />
        <additionalFile filename="$data/shared/materialHolders/waterSimulationMaterialHolder.i3d" />
    </additionalFiles>
    <!-- Register the non-fruit foliage layers so they are MOWABLE / paintable. Without <paintableFoliages> the
         'meadow' foliage renders but the game won't mow it or track its harvest state. layerNames must match
         FoliageType names in the map.i3d FoliageSystem. -->
    <decoFoliages>
        <decoFoliage layerName="decoFoliage" startChannel="0" numChannels="4" mowable="true" />
        <decoFoliage layerName="forestPlants" startChannel="0" numChannels="4" mowable="true" />
        <decoFoliage layerName="waterPlants" startChannel="0" numChannels="4" mowable="true" />
        <decoFoliage layerName="decoBushUS" startChannel="0" numChannels="4" />
        <decoFoliage layerName="decoBush" startChannel="0" numChannels="4" />
        <mapping name="grassShort" layerName="decoFoliage" state="9" />
    </decoFoliages>
    <paintableFoliages>
        <paintableFoliage layerName="grass" startChannel="0" numStateChannels="4" />
        <paintableFoliage layerName="meadow" startChannel="0" numStateChannels="4" />
        <paintableFoliage layerName="decoBushUS" startChannel="0" numStateChannels="4" />
        <paintableFoliage layerName="decoFoliage" startChannel="0" numStateChannels="4" />
    </paintableFoliages>
    <farmlands filename="maps/farmlands.xml" />
    <aiSystem filename="$data/maps/mapUS/config/aiSystem.xml" />
    <npcs filename="$data/maps/maps_npcs.xml" />
    <missions vehicleFilename="$dataS/missionVehicles.xml" />
</map>
'''

FARMLANDS_XML = '''<?xml version="1.0" encoding="utf-8" standalone="no" ?>
<map xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="../../../shared/xml/schema/farmlands.xsd">
    <farmlands densityMapFilename="maps/data/infoLayer_farmland.grle" numChannels="8" pricePerHa="60000">
        <farmland id="1" priceScale="1" npcName="FARMER" defaultFarmProperty="true" />
        <farmland id="2" priceScale="1" npcName="HELPER" />
    </farmlands>
</map>
'''

MODDESC = '''<?xml version="1.0" encoding="utf-8" standalone="no" ?>
<modDesc descVersion="{descVersion}">
    <author>fs25-empty-map</author>
    <version>1.0.0.0</version>
    <title><en>{title}</en></title>
    <description><en>A flat {title} map, generated 100% from scratch, with a 100ha owned wheat field.</en></description>
    <iconFilename>icon.dds</iconFilename>
    <maps>
        <map id="{name}" className="Mission00" filename="$dataS/scripts/mission00.lua" configFilename="maps/map.xml" defaultVehiclesXMLFilename="maps/vehicles.xml" defaultPlaceablesXMLFilename="maps/placeables.xml" defaultItemsXMLFilename="maps/items.xml" defaultHandToolsXMLFilename="$data/maps/mapUS/config/handTools.xml">
            <title><en>{title}</en></title>
            <iconFilename>icon.dds</iconFilename>
        </map>
    </maps>
</modDesc>
'''


def _w(path, text):
    open(path, "w", encoding="utf-8").write(text)


def build(cfg, mod_dir, maps_dir, desc_version="100"):
    _w(os.path.join(maps_dir, "map.xml"), MAP_XML.format(m=cfg.map_m, i3d=cfg.i3d))
    # overview = the static map picture the game displays and overlays fields onto (must be map-sized). Empty grass.
    Image.new("RGB", (cfg.overview_res, cfg.overview_res), (72, 88, 48)).save(os.path.join(maps_dir, "overview.png"))
    _w(os.path.join(maps_dir, "farmlands.xml"), FARMLANDS_XML)
    for name, root in (("vehicles", "vehicles"), ("placeables", "placeables"), ("items", "items")):
        _w(os.path.join(maps_dir, name + ".xml"),
           f'<?xml version="1.0" encoding="utf-8" standalone="no" ?>\n<{root}></{root}>\n')
    _w(os.path.join(mod_dir, "modDesc.xml"), MODDESC.format(descVersion=desc_version, name=cfg.name, title=cfg.title))
    ok = "ok"
    try:
        Image.new("RGBA", (256, 256), (54, 110, 40, 255)).save(os.path.join(mod_dir, "icon.dds"))
    except Exception as e:
        ok = f"icon FAILED ({e})"
    print(f"gen_configs: {cfg.title} - map.xml/modDesc/farmlands/overview({cfg.overview_res}^2)/icon [{ok}]")
