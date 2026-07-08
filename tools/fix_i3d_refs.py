"""
fix_i3d_refs.py - final INTEGRITY GATE that keeps the generated map from CRASHING on load. Two classes of fault,
both from WW's cazz16x reusing low ids / shipping FS22 shaders:

 1. DANGLING i3d ref: a Material child (Texture/Normalmap/Glossmap/Custommap/...) or customShaderId points at a
    fileId with NO <File> (WW's id-reuse collapses under extraction) -> "file reference N not found in i3d files
    section" -> CRASH. Fix: strip the offending child / drop the shader attr (cosmetic loss, not fatal).

 2. MISSING SHADER-DEFAULT TEXTURE: the copied FS22 shaders in maps/fs22/shaders/*.xml declare relative
    defaultFilename textures (e.g. glowShader -> ../shared/materialHolders/defaultGlow_diffuse.png). When a glow/
    emissive material provides no map of its own it falls back to that default; if the default was never copied the
    shader fails to init -> CRASH (this is what the harbor's lamp materials tripped). Fix: copy every missing
    RELATIVE shader-default texture from the FS22 install (as .dds; the engine resolves the .png ref to it). $data/*
    defaults resolve against the FS25 install and are left alone.

Idempotent. Run LAST (convert.py step "fixup"), after every mesh/material/shader step, before deploy.
"""
import os, re, json, shutil
import xml.etree.ElementTree as ET

WW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONV = json.load(open(os.path.join(WW, "tools", os.environ.get("MAP_CONVERT", "wildwest.convert.json")), encoding="utf-8"))
MAPS = os.path.join(WW, "out", CONV["identity"]["mod"], "maps")
I3D = os.path.join(MAPS, CONV["identity"]["i3d"])
FS22_DATA = os.environ.get("FS22_DATA", r"C:\Program Files (x86)\Steam\steamapps\common\Farming Simulator 22\data")


def strip_dangling(root):
    files = {f.get("fileId") for f in root.iter("File")}
    n_tex = n_shader = 0
    for m in root.iter("Material"):
        for child in list(m):
            fid = child.get("fileId")
            if fid and fid not in files:                 # dangling texture/map slot -> strip (cosmetic, not a crash)
                m.remove(child); n_tex += 1
        cs = m.get("customShaderId")
        if cs and cs not in files:                       # dangling shader -> drop (falls back to default shader)
            del m.attrib["customShaderId"]; n_shader += 1
    mats = {m.get("materialId") for m in root.iter("Material")}
    bad = [s.get("name") for s in root.iter("Shape")
           for mid in (s.get("materialIds") or "").split(",") if mid and mid not in mats]
    return n_tex, n_shader, bad


def fix_fs22_shaders(root):
    """FS22 shaders copied to maps/fs22/shaders/ (glowShader/translucencyShader/...) FAIL to compile in FS25 and
    crash mid-load. For each fs22/shaders/X.xml File: if FS25 ships a native $data/shaders/X.xml, REPOINT to it
    (base-game shader = compiles + upgrade-proof); if FS25 has NO equivalent (e.g. silageBaleShader), DROP the
    customShaderId on every material using it so it falls back to the engine default shader (cosmetic, not a crash).
    Returns (repointed, dropped)."""
    fs25_shaders = os.path.join(os.environ.get("FS25_DATA", r"C:\Program Files (x86)\Steam\steamapps\common\Farming Simulator 25\data"), "shaders")
    repoint = 0; no_fs25 = set()
    for f in root.iter("File"):
        m = re.match(r"fs22/shaders/(\w+\.xml)$", f.get("filename") or "")
        if not m:
            continue
        if os.path.exists(os.path.join(fs25_shaders, m.group(1))):
            f.set("filename", "$data/shaders/" + m.group(1)); repoint += 1
        else:
            no_fs25.add(f.get("fileId"))               # FS25 lacks this shader -> materials fall back to default
    drop = 0
    for mat in root.iter("Material"):
        if mat.get("customShaderId") in no_fs25:
            mat.attrib.pop("customShaderId", None); mat.attrib.pop("customShaderVariation", None); drop += 1
    return repoint, drop


def copy_shader_defaults():
    """Copy every missing RELATIVE default texture referenced by the copied FS22 shaders (maps/fs22/shaders/*.xml)."""
    shdir = os.path.join(MAPS, "fs22", "shaders")
    copied = missing = 0
    if not os.path.isdir(shdir):
        return copied, missing
    for f in os.listdir(shdir):
        if not f.endswith(".xml"):
            continue
        txt = open(os.path.join(shdir, f), encoding="utf-8", errors="ignore").read()
        for dfn in re.findall(r'defaultFilename\s*=\s*"([^"]+)"', txt):
            if dfn.startswith("$"):                       # $data default -> resolves against the FS25 install, leave it
                continue
            rel = os.path.normpath(os.path.join("fs22", "shaders", dfn.replace("/", os.sep)))   # relative to the shader
            base = os.path.splitext(rel)[0]
            if any(os.path.exists(os.path.join(MAPS, base + e)) for e in (".dds", ".png", os.path.splitext(rel)[1])):
                continue                                  # some form already present
            after = rel.split(os.sep, 1)[1]               # drop the leading 'fs22/' -> the $data-relative path
            src = next((p for p in (os.path.join(FS22_DATA, os.path.splitext(after)[0] + e) for e in (".dds", ".png"))
                        if os.path.exists(p)), None)
            if src:
                dst = os.path.join(MAPS, base + os.path.splitext(src)[1])
                os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copy(src, dst); copied += 1
            else:
                missing += 1
    return copied, missing


def main():
    tree = ET.parse(I3D); root = tree.getroot()
    n_tex, n_shader, bad = strip_dangling(root)
    n_repoint, n_drop = fix_fs22_shaders(root)
    tree.write(I3D, encoding="utf-8", xml_declaration=True)
    sh_copied, sh_missing = copy_shader_defaults()
    print(f"[fixup] stripped {n_tex} dangling material->file refs, {n_shader} dangling shaders | "
          f"repointed {n_repoint} FS22 shaders -> $data, dropped {n_drop} no-FS25-shader mats to default | "
          f"copied {sh_copied} missing shader-default textures ({sh_missing} unresolved)"
          + (f" | WARNING {len(bad)} shapes ref a missing material: {bad[:5]}" if bad else ""))


if __name__ == "__main__":
    main()
