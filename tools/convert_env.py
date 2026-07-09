"""
convert_env.py - resolve per-MACHINE install locations for the converter, so the shipped map configs stay portable
(no absolute personal paths). A <map>.convert.json stores only the RELATIVE mod folder name (source.mod); the FS22
mods ROOT comes from the environment (set by the GUI or the shell), defaulting to the standard FS22 mods folder.

  FS22_MODS  - folder holding the FS22 source mods (default: ~/Documents/My Games/FarmingSimulator2022/mods)

Backward compatible: if a config still carries an absolute source.dir, that wins (dev override).

Also holds the PyInstaller freeze helpers, so the same code runs from source (python) and from a frozen exe:
  - app_home()   : real WRITABLE base for out/ + logs/ (folder of the exe when frozen; repo root otherwise)
  - bundle_tools(): dir holding the tool .py + configs + vendor (the PyInstaller bundle when frozen; <repo>/tools else)
  - tool_argv()  : subprocess argv to run a bundled tool, frozen-aware (re-invokes the exe with --run-tool)
  - dispatch()   : if argv starts with --run-tool, runpy the named bundled tool and exit (the frozen subprocess entry)
"""
import os, sys


def frozen():
    return getattr(sys, "frozen", False)


def app_home():
    """Real, writable base dir for OUTPUT (out/, logs/). $FS_CONVERT_HOME wins (propagated to subprocesses);
    else the folder of the exe when frozen, or the repo root (this file lives in <repo>/tools/)."""
    env = os.environ.get("FS_CONVERT_HOME")
    if env:
        return env
    if frozen():
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def bundle_tools():
    """Dir holding the tool scripts + configs + vendor. The PyInstaller bundle's tools/ when frozen (data added via
    --add-data 'tools;tools'), else this file's own dir (<repo>/tools)."""
    if frozen():
        return os.path.join(sys._MEIPASS, "tools")
    return os.path.dirname(os.path.abspath(__file__))


def tool_argv(tool_name, *extra):
    """argv to run a bundled tool script as a subprocess, frozen-aware.
    frozen:     [exe, --run-tool, <name>, *extra]   (re-invokes this same exe in tool mode)
    from source:[python, <repo>/tools/<name>, *extra]"""
    if frozen():
        return [sys.executable, "--run-tool", tool_name, *[str(e) for e in extra]]
    return [sys.executable, os.path.join(bundle_tools(), tool_name), *[str(e) for e in extra]]


def dispatch(argv):
    """Frozen subprocess entry: if argv == [--run-tool, <name>, *args], runpy that bundled tool as __main__ and
    return its exit code; else return None (so the caller proceeds to launch the GUI)."""
    if len(argv) >= 2 and argv[0] == "--run-tool":
        import runpy
        tools = bundle_tools()
        if tools not in sys.path:
            sys.path.insert(0, tools)   # runpy doesn't add the script dir; tools import convert_env + each other
        path = os.path.join(tools, argv[1])
        sys.argv = [path] + list(argv[2:])
        try:
            runpy.run_path(path, run_name="__main__")
            return 0
        except SystemExit as e:
            return e.code if isinstance(e.code, int) else (0 if not e.code else 1)
    return None


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
