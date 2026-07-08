"""
convert_env.py - resolve per-MACHINE install locations for the converter, so the shipped map configs stay portable
(no absolute personal paths). A <map>.convert.json stores only the RELATIVE mod folder name (source.mod); the FS22
mods ROOT comes from the environment (set by the GUI or the shell), defaulting to the standard FS22 mods folder.

  FS22_MODS  - folder holding the FS22 source mods (default: ~/Documents/My Games/FarmingSimulator2022/mods)

Backward compatible: if a config still carries an absolute source.dir, that wins (dev override).
"""
import os


def default_fs22_mods():
    return os.path.join(os.path.expanduser("~"), "Documents", "My Games", "FarmingSimulator2022", "mods")


def source_dir(conv):
    """Absolute path to the FS22 source mod. Prefers an explicit source.dir (dev override); otherwise joins the
    relative source.mod onto $FS22_MODS (or the default FS22 mods folder)."""
    s = conv.get("source", {})
    if s.get("dir"):
        return s["dir"]
    mod = s.get("mod")
    if not mod:
        raise KeyError("config source needs either 'dir' (absolute) or 'mod' (folder name under the FS22 mods root)")
    return os.path.join(os.environ.get("FS22_MODS", default_fs22_mods()), mod)
