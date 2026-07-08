"""
classify.py - content-based scene classification for the FS22->FS25 conversion TOOL. Map-agnostic: it tags every
scene shape by WHAT IT IS (material-name keywords + Light-node presence + collision), NOT by group name - so it
works on WW's `roads`/`Buildings`/`trees` and West End's `WestEnd`/`Trees`/`forest` alike. The technique tools
(flats/buildings/trees/lights/placeables) consume these tags instead of hardcoded group names.

Decision policy (which technique each class routes to) lives in ROUTE: trees + functional placeables -> base-game
FS25; road/building/light/sign/deco -> extract-from-FS22; fence -> skip. Reports per-class + per-top-group counts
+ what it couldn't confidently place, so you see what the tool found and where a map needs a human.
"""
import os, sys, json, collections
import xml.etree.ElementTree as ET

WW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import convert_env
CONV = json.load(open(os.path.join(WW, "tools", os.environ.get("MAP_CONVERT", "wildwest.convert.json")), encoding="utf-8"))
FS22_I3D = os.path.join(convert_env.source_dir(CONV), CONV["source"]["map_i3d"])

# material-name keywords -> class, checked in this order (earlier wins). Light + collision are handled separately.
CLASS_KW = {
    "tree":  ("trunk", "branch", "leaf", "leaves", "birch", "oak", "pine", "spruce", "maple", "aspen", "beech",
              "poplar", "stonepine", "fir", "cedar", "willow", "larch", "hickory", "elm", "catalpa", "tree"),
    "water": ("water", "ocean", "river", "lake", "pond", "sea"),
    "sign":  ("sign", "billboard", "advertis"),
    "fence": ("chainlink", "fence"),
    "road":  ("asphalt", "road", "street", "verge", "tarmac", "pavement", "kerb", "curb", "lane", "highway",
              "gravel", "cobble", "sidewalk"),
}
# where each class goes: extract from FS22, base-game FS25 analog, or skip
ROUTE = {"tree": "base-game FS25", "light": "extract", "road": "extract", "building": "extract",
         "sign": "extract", "water": "extract", "fence": "SKIP", "deco": "extract"}


def build_tagger(root):
    # Classify by material NAME *and* its TEXTURE filenames: mappers reuse generic material names (West End's roads are
    # material "lambert2" - a Maya default - so name keywords miss them), but the diffuse/normal texture path is telling
    # ("asphalt_diffuse1.dds", "railTracks_diffuse.dds"). Fold both into the match string.
    files = {f.get("fileId"): (f.get("filename") or "").lower() for f in root.iter("File")}
    mats = {}
    for m in root.iter("Material"):
        texs = " ".join(files.get(c.get("fileId"), "") for c in m if c.get("fileId"))
        mats[m.get("materialId")] = (m.get("name") or "").lower() + " " + texs
    scene = root.find("Scene")
    parent = {c: p for p in scene.iter() for c in p}
    light_groups = {id(parent[l]) for l in scene.iter("Light") if l in parent}

    def in_light(sh):
        n = sh
        for _ in range(3):
            n = parent.get(n)
            if n is None:
                return False
            if id(n) in light_groups:
                return True
        return False

    def tag(sh):
        if in_light(sh):
            return "light"
        mn = " ".join(mats.get(m, "") for m in (sh.get("materialIds") or "").split(","))
        for cls, kws in CLASS_KW.items():
            if any(k in mn for k in kws):
                return cls
        if sh.get("collisionMask") or sh.get("collision"):
            return "building"
        return "deco"
    return tag, scene


def classify(root):
    tag, scene = build_tagger(root)
    top_of = {}
    for top in scene:
        if top.tag == "TransformGroup":
            for sh in top.iter("Shape"):
                top_of[id(sh)] = top.get("name")
    totals = collections.Counter(); by_top = collections.defaultdict(collections.Counter)
    for sh in scene.iter("Shape"):
        t = tag(sh); totals[t] += 1; by_top[top_of.get(id(sh), "?")][t] += 1
    return totals, by_top


def main():
    print(f"classifying {os.path.basename(FS22_I3D)} ({os.path.getsize(FS22_I3D)//1048576}MB)...", flush=True)
    root = ET.parse(FS22_I3D).getroot()
    totals, by_top = classify(root)
    print("\n=== class totals (shapes) -> route ===")
    for cls, n in totals.most_common():
        print(f"  {cls:9} {n:>8}  -> {ROUTE.get(cls,'?')}")
    print("\n=== by top-level group (dominant classes) ===")
    for top, c in sorted(by_top.items(), key=lambda x: -sum(x[1].values())):
        print(f"  {top:24} {dict(c.most_common(4))}")


if __name__ == "__main__":
    main()
