"""
build_overview.py - GENERATE the FS25 map overview from WW's own map picture (read -> understand -> generate).
Reads WW's overview.dds (the map's top-down PDA image) and writes it as the FS25 overview.png at its native
4096^2 (matches Huron/Juotca, both 16x; a .png overview loads - the empty starters prove it). This replaces the
engine's solid-green placeholder. INTERIM: once we have terrain textures we'd re-render an overview from OUR own
map; until then WW's own picture is the right background. No copying - read the image, write our overview.
"""
import os, json
from PIL import Image

WW = os.environ.get("FS_CONVERT_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import convert_env
CONV = json.load(open(os.path.join(WW, "tools", os.environ.get("MAP_CONVERT", "wildwest.convert.json")), encoding="utf-8"))
FS22 = convert_env.source_dir(CONV)
OUT = os.path.join(WW, "out", CONV["identity"]["mod"])


def main():
    cands = ["maps/overview.dds", "maps/mapUS/overview.dds", "maps/mapUS/data/mapUS_overview.dds", "map_preview.dds"]
    src = next((os.path.join(FS22, *c.split("/")) for c in cands if os.path.exists(os.path.join(FS22, *c.split("/")))), None)
    dst = os.path.join(OUT, "maps", "overview.png")
    if src is None:
        print("overview: no source found -> keeping engine placeholder"); return
    im = Image.open(src); im.load(); im.save(dst)
    print(f"overview: read {os.path.basename(src)} {im.size} {im.mode} -> {dst}")


if __name__ == "__main__":
    main()
