"""
convert_ui.py - a thin desktop front-end over tools/convert.py for the FS22 -> FS25 map converter.

Pick a map (config) + your FS22 and FS25 install folders, hit Convert, watch the log. This UI holds NO conversion
logic: it just collects inputs, sets the env vars the pipeline already reads (MAP_CONVERT, FS22_DATA, FS25_DATA,
FS25_MODS), and runs `tools/convert.py` as a subprocess, streaming its output. Everything map-specific still lives in
the chosen <map>.convert.json; the two install folders are per-machine (env), never in the config.

Run:  python convert_ui.py       (needs only Python's stdlib tkinter)
"""
import os, sys, glob, json, threading, subprocess, queue, datetime

# --- freeze-aware paths (works from source AND from a PyInstaller exe) ---
FROZEN = getattr(sys, "frozen", False)
APP_HOME = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.abspath(__file__))   # writable base (out/, logs/)
TOOLS = os.path.join(sys._MEIPASS, "tools") if FROZEN else os.path.join(APP_HOME, "tools")              # bundled tool scripts/configs
sys.path.insert(0, TOOLS)
import convert_env
# Frozen subprocess entry: when re-invoked as `exe --run-tool <script>`, run that tool and exit BEFORE any GUI.
if FROZEN:
    _rc = convert_env.dispatch(sys.argv[1:])
    if _rc is not None:
        sys.exit(_rc)

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

REPO = APP_HOME
CONVERT = os.path.join(TOOLS, "convert.py")
LOG_DIR = os.path.join(APP_HOME, "logs")   # timestamped per-run logs; attach to GitHub issues
_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if hasattr(subprocess, "CREATE_NO_WINDOW") else {}

DEF_FS22 = os.environ.get("FS22_DATA", r"C:\Program Files (x86)\Steam\steamapps\common\Farming Simulator 22\data")
DEF_FS25 = os.environ.get("FS25_DATA", r"C:\Program Files (x86)\Steam\steamapps\common\Farming Simulator 25\data")
DEF_FS22_MODS = os.environ.get("FS22_MODS", os.path.join(os.path.expanduser("~"),
                          "Documents", "My Games", "FarmingSimulator2022", "mods"))
DEF_MODS = os.environ.get("FS25_MODS", os.path.join(os.path.expanduser("~"),
                          "Documents", "My Games", "FarmingSimulator2025", "mods"))


def list_configs():
    """Shipped map configs = every tools/*.convert.json. Returns [(label, abspath), ...]."""
    out = []
    for p in sorted(glob.glob(os.path.join(TOOLS, "*.convert.json"))):
        try:
            c = json.load(open(p, encoding="utf-8"))
            title = c.get("identity", {}).get("title") or os.path.basename(p)
        except Exception:
            title = os.path.basename(p)
        out.append((f"{title}   ({os.path.basename(p)})", p))
    return out


class App:
    def __init__(self, root):
        self.root = root
        self.proc = None
        self.logf = None
        self.log_path = None
        self.q = queue.Queue()
        root.title("FS22 -> FS25 Map Converter")
        root.geometry("820x620")
        root.minsize(680, 500)

        self.configs = list_configs()
        self.cfg_paths = {label: path for label, path in self.configs}

        frm = ttk.Frame(root, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)
        r = 0

        # --- Map / config ---
        ttk.Label(frm, text="Map (config):").grid(row=r, column=0, sticky="w", pady=4)
        self.cfg_var = tk.StringVar(value=self.configs[0][0] if self.configs else "")
        self.cfg_cb = ttk.Combobox(frm, textvariable=self.cfg_var, state="readonly",
                                   values=[c[0] for c in self.configs])
        self.cfg_cb.grid(row=r, column=1, sticky="ew", pady=4)
        ttk.Button(frm, text="Browse...", command=self.browse_cfg).grid(row=r, column=2, padx=(6, 0))
        r += 1

        # --- FS22 install ---
        self.fs22 = self._dir_row(frm, r, "FS22 install folder:", DEF_FS22,
                                  "The Farming Simulator 22 'data' folder (base textures the map reuses).")
        r += 1
        # --- FS22 mods (source map location) ---
        self.fs22_mods = self._dir_row(frm, r, "FS22 mods folder:", DEF_FS22_MODS,
                                  "The folder holding the FS22 source map mod (the config's source.mod is looked up here).")
        r += 1
        # --- FS25 install ---
        self.fs25 = self._dir_row(frm, r, "FS25 install folder:", DEF_FS25,
                                  "The Farming Simulator 25 'data' folder (base placeables / trees).")
        r += 1
        # --- FS25 mods (deploy target) ---
        self.mods = self._dir_row(frm, r, "FS25 mods folder:", DEF_MODS,
                                  "Where the finished mod is linked so the game sees it (in Documents, not Steam).")
        r += 1

        # --- options ---
        opt = ttk.Frame(frm)
        opt.grid(row=r, column=0, columnspan=3, sticky="w", pady=(6, 2))
        self.deploy_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="Deploy to game mods when done (create junction)",
                        variable=self.deploy_var).pack(side="left")
        r += 1

        # --- actions + progress ---
        act = ttk.Frame(frm)
        act.grid(row=r, column=0, columnspan=3, sticky="ew", pady=(8, 4))
        act.columnconfigure(2, weight=1)
        self.run_btn = ttk.Button(act, text="Convert", command=self.start)
        self.run_btn.grid(row=0, column=0)
        self.stop_btn = ttk.Button(act, text="Stop", command=self.stop, state="disabled")
        self.stop_btn.grid(row=0, column=1, padx=(6, 0))
        self.pbar = ttk.Progressbar(act, mode="determinate", maximum=100)
        self.pbar.grid(row=0, column=2, sticky="ew", padx=(12, 0))
        r += 1

        self.status = ttk.Label(frm, text="Ready.", foreground="#444")
        self.status.grid(row=r, column=0, columnspan=3, sticky="w")
        r += 1

        # --- log ---
        logf = ttk.Frame(frm)
        logf.grid(row=r, column=0, columnspan=3, sticky="nsew", pady=(6, 0))
        frm.rowconfigure(r, weight=1)
        logf.rowconfigure(0, weight=1); logf.columnconfigure(0, weight=1)
        self.log = tk.Text(logf, wrap="none", height=16, bg="#111", fg="#ddd",
                           insertbackground="#ddd", font=("Consolas", 9))
        self.log.grid(row=0, column=0, sticky="nsew")
        ys = ttk.Scrollbar(logf, orient="vertical", command=self.log.yview)
        ys.grid(row=0, column=1, sticky="ns"); self.log["yscrollcommand"] = ys.set
        self.log.configure(state="disabled")

        self.root.after(100, self._drain)

    def _dir_row(self, frm, r, label, default, tip):
        ttk.Label(frm, text=label).grid(row=r, column=0, sticky="w", pady=4)
        var = tk.StringVar(value=default)
        ent = ttk.Entry(frm, textvariable=var)
        ent.grid(row=r, column=1, sticky="ew", pady=4)
        ttk.Button(frm, text="Browse...",
                   command=lambda: self._pick_dir(var)).grid(row=r, column=2, padx=(6, 0))
        return var

    def _pick_dir(self, var):
        d = filedialog.askdirectory(initialdir=var.get() if os.path.isdir(var.get()) else REPO)
        if d:
            var.set(os.path.normpath(d))

    def browse_cfg(self):
        p = filedialog.askopenfilename(title="Choose a .convert.json", initialdir=TOOLS,
                                       filetypes=[("Convert config", "*.convert.json"), ("JSON", "*.json")])
        if p:
            label = f"(custom)   {os.path.basename(p)}"
            self.cfg_paths[label] = p
            vals = list(self.cfg_cb["values"]) + [label]
            self.cfg_cb["values"] = vals
            self.cfg_var.set(label)

    # --- validation ---
    def _validate(self):
        cfg = self.cfg_paths.get(self.cfg_var.get())
        if not cfg or not os.path.isfile(cfg):
            return None, "Pick a map config."
        fs22, fs22_mods, fs25, mods = self.fs22.get(), self.fs22_mods.get(), self.fs25.get(), self.mods.get()
        if not os.path.isdir(os.path.join(fs22)):
            return None, "FS22 install folder not found."
        if not os.path.isdir(fs25) or not os.path.isdir(os.path.join(fs25, "placeables")):
            return None, "FS25 install folder must contain a 'placeables' subfolder (point at the FS25 'data' dir)."
        try:
            source = json.load(open(cfg, encoding="utf-8")).get("source", {})
            src = source.get("dir") or (os.path.join(fs22_mods, source["mod"]) if source.get("mod") else None)
            if src and not os.path.isdir(src):
                return None, f"This map's source mod is missing:\n{src}\n(check the FS22 mods folder)"
        except Exception as e:
            return None, f"Config unreadable: {e}"
        return {"cfg": cfg, "fs22": fs22, "fs22_mods": fs22_mods, "fs25": fs25, "mods": mods}, None

    # --- run ---
    def start(self):
        opts, err = self._validate()
        if err:
            messagebox.showerror("Can't start", err); return
        self._set_running(True)
        self._clear_log()
        self.pbar["value"] = 0
        self.status.config(text="Converting...")
        env = dict(os.environ)
        env["MAP_CONVERT"] = opts["cfg"]        # abspath: convert.py's os.path.join(tools, MAP_CONVERT) resolves to it
        env["FS22_DATA"] = opts["fs22"]
        env["FS22_MODS"] = opts["fs22_mods"]
        env["FS25_DATA"] = opts["fs25"]
        env["FS25_MODS"] = opts["mods"]
        env["FS_CONVERT_HOME"] = APP_HOME       # writable output base (out/); subprocesses inherit it
        env["PYTHONUNBUFFERED"] = "1"
        # per-run timestamped log file (date+time) - full transcript for investigating failures / GitHub issues
        self._open_log(opts)
        cmd = convert_env.tool_argv("convert.py")   # frozen-aware: [python, convert.py] or [exe, --run-tool, convert.py]
        if not self.deploy_var.get():
            cmd.append("--no-deploy")
        threading.Thread(target=self._worker, args=(cmd, env), daemon=True).start()

    def _open_log(self, opts):
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            now = datetime.datetime.now()
            name = os.path.splitext(os.path.basename(opts["cfg"]))[0]
            self.log_path = os.path.join(LOG_DIR, f"convert_{name}_{now:%Y-%m-%d_%H-%M-%S}.log")
            self.logf = open(self.log_path, "w", encoding="utf-8")
            header = [
                "FS22 -> FS25 Map Converter - run log",
                f"time         : {now.isoformat(timespec='seconds')}",
                f"config       : {opts['cfg']}",
                f"FS22 install : {opts['fs22']}",
                f"FS22 mods    : {opts['fs22_mods']}",
                f"FS25 install : {opts['fs25']}",
                f"FS25 mods    : {opts['mods']}",
                f"deploy       : {self.deploy_var.get()}",
                f"python       : {sys.version.split()[0]}   platform: {sys.platform}",
                "=" * 72, "",
            ]
            self.logf.write("\n".join(header)); self.logf.flush()
            self._append(f"Logging to: {self.log_path}\n\n")
        except Exception as e:
            self.logf = None
            self._append(f"[warning] could not open log file: {e}\n")

    def _worker(self, cmd, env):
        try:
            self.proc = subprocess.Popen(cmd, cwd=REPO, env=env, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True, bufsize=1, **_NO_WINDOW)
            for line in self.proc.stdout:
                self.q.put(("log", line))
            code = self.proc.wait()
            self.q.put(("done", code))
        except Exception as e:
            self.q.put(("log", f"\n[launcher error] {e}\n")); self.q.put(("done", -1))
        finally:
            self.proc = None

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.q.put(("log", "\n[stopped by user]\n"))

    def _drain(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._append(payload)
                    if self.logf:
                        try:
                            self.logf.write(payload); self.logf.flush()
                        except Exception:
                            pass
                    if payload.startswith("[STEP "):
                        try:
                            i, n = payload[6:payload.index("]")].split("/")
                            self.pbar["value"] = 100.0 * int(i) / int(n)
                            self.status.config(text=f"Step {i}/{n}: {payload.split(']',1)[1].strip()}")
                        except Exception:
                            pass
                elif kind == "done":
                    self._finish(payload)
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    def _finish(self, code):
        self._set_running(False)
        if self.logf:
            try:
                self.logf.write(f"\n{'=' * 72}\nEXIT {code}   ({datetime.datetime.now().isoformat(timespec='seconds')})\n")
                self.logf.close()
            except Exception:
                pass
            self.logf = None
        log_note = f"\n\nLog saved to:\n{self.log_path}" if self.log_path else ""
        if code == 0:
            self.pbar["value"] = 100
            self.status.config(text="Done. Map converted" + (" + deployed." if self.deploy_var.get() else "."))
            messagebox.showinfo("Finished", "Conversion complete." + log_note)
        else:
            self.status.config(text=f"Failed (exit {code}). See log: {os.path.basename(self.log_path or '')}")
            messagebox.showerror("Failed", f"Conversion failed (exit {code}). Check the log for the failing step."
                                 + log_note + "\n\n(Attach this log file to a GitHub issue.)")

    # --- ui helpers ---
    def _set_running(self, running):
        self.run_btn.config(state="disabled" if running else "normal")
        self.stop_btn.config(state="normal" if running else "disabled")

    def _append(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self):
        self.log.configure(state="normal"); self.log.delete("1.0", "end"); self.log.configure(state="disabled")


if __name__ == "__main__":
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")   # native-ish on Windows; harmless elsewhere
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
