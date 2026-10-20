"""VroidKassandra.exe - turn a VRoid .vrm into a Kassandra replacement for AC Odyssey.

Double-click it, or drag a .vrm onto it. It finds Blender, runs the converter
and opens the output folder with step-by-step instructions.
"""

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import finders
from runner import build_command

APP_NAME = "VRoid to Kassandra"
CONFIG_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "VroidKassandra")
CONFIG_FILE = os.path.join(CONFIG_DIR, "settings.json")


def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except OSError:
        pass


def default_output_dir():
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    return os.path.join(docs if os.path.isdir(docs) else os.path.expanduser("~"), "VroidKassandra")


def open_folder(path):
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - opening a folder for the user
    else:
        subprocess.Popen(["xdg-open", path])


class App:
    def __init__(self, root, initial_vrm=None):
        self.root = root
        self.cfg = load_config()
        self.log_queue = queue.Queue()
        self.proc = None
        root.title(APP_NAME)
        root.minsize(640, 480)

        self.vrm = tk.StringVar(value=initial_vrm or "")
        self.reference = tk.StringVar(value=self.cfg.get("reference", ""))
        self.blender = tk.StringVar(value=self.cfg.get("blender", ""))
        self.out_dir = tk.StringVar(value=self.cfg.get("out_dir", default_output_dir()))
        self.no_fingers = tk.BooleanVar(value=self.cfg.get("no_fingers", False))
        self.skeleton_mode = tk.BooleanVar(value=self.cfg.get("proportions") == "skeleton")
        self.atlas_size = tk.StringVar(value=str(self.cfg.get("atlas_size", 4096)))

        frm = ttk.Frame(root, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)
        rows = [
            ("VRoid model (.vrm)", self.vrm, self.pick_vrm),
            ("Kassandra reference (.glb)", self.reference, self.pick_reference),
            ("Blender", self.blender, self.pick_blender),
            ("Output folder", self.out_dir, self.pick_out),
        ]
        for i, (label, var, cmd) in enumerate(rows):
            ttk.Label(frm, text=label).grid(row=i, column=0, sticky="w", pady=3)
            ttk.Entry(frm, textvariable=var).grid(row=i, column=1, sticky="ew", padx=6)
            ttk.Button(frm, text="Browse…", command=cmd).grid(row=i, column=2)

        opts = ttk.LabelFrame(frm, text="Options", padding=8)
        opts.grid(row=4, column=0, columnspan=3, sticky="ew", pady=8)
        ttk.Checkbutton(opts, text="Bind fingers to the hand (fewer bones, fingers won't bend)",
                        variable=self.no_fingers).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(opts, text="Experimental: also move Kassandra's joints to the VRoid's joints",
                        variable=self.skeleton_mode).grid(row=1, column=0, columnspan=2, sticky="w")
        ttk.Label(opts, text="Texture size").grid(row=2, column=0, sticky="w")
        ttk.Combobox(opts, textvariable=self.atlas_size, values=("2048", "4096", "8192"), width=8,
                     state="readonly").grid(row=2, column=1, sticky="w")

        btns = ttk.Frame(frm)
        btns.grid(row=5, column=0, columnspan=3, sticky="ew")
        self.go = ttk.Button(btns, text="Convert", command=self.start)
        self.go.pack(side="left")
        ttk.Button(btns, text="How do I get the Kassandra reference?", command=self.help_reference).pack(
            side="left", padx=8)
        self.status = ttk.Label(btns, text="")
        self.status.pack(side="right")

        self.log = tk.Text(frm, height=14, wrap="word", state="disabled")
        self.log.grid(row=6, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
        frm.rowconfigure(6, weight=1)

        self.write("Welcome! Pick your VRoid .vrm and Kassandra's reference .glb, then press Convert.\n")
        game = finders.ac_odyssey_dir()
        self.write(f"AC Odyssey (Steam): {game}\n" if game else
                   "AC Odyssey was not found in your Steam libraries (that's OK for converting).\n")
        if not self.blender.get() or not os.path.isfile(self.blender.get()):
            self.write("Looking for Blender…\n")
            threading.Thread(target=self.detect_blender, daemon=True).start()
        root.after(100, self.drain)

    # ----- file pickers
    def pick_vrm(self):
        p = filedialog.askopenfilename(title="VRoid model", filetypes=[("VRM model", "*.vrm"), ("All", "*.*")])
        if p:
            self.vrm.set(p)

    def pick_reference(self):
        p = filedialog.askopenfilename(title="Kassandra reference exported from AnvilToolkit",
                                       filetypes=[("glTF binary", "*.glb"), ("All", "*.*")])
        if p:
            self.reference.set(p)

    def pick_blender(self):
        p = filedialog.askopenfilename(title="blender.exe", filetypes=[("Blender", "blender.exe"), ("All", "*.*")])
        if p:
            self.blender.set(p)

    def pick_out(self):
        p = filedialog.askdirectory(title="Output folder")
        if p:
            self.out_dir.set(p)

    def help_reference(self):
        messagebox.showinfo(APP_NAME, (
            "The converter needs Kassandra's own mesh and skeleton to rig your model to.\n\n"
            "1. Install AnvilToolkit (Nexus Mods).\n"
            "2. In AnvilToolkit, open the game's forge that holds Kassandra's body mesh.\n"
            "3. Open the mesh in the Mesh Viewer (with its skeleton) and use Save As → glTF (.glb).\n"
            "4. Pick that .glb here. You only need to do this once."))

    # ----- blender detection
    def detect_blender(self):
        path, version = finders.find_blender()
        self.log_queue.put(("blender", path, version))

    # ----- conversion
    def write(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def start(self):
        vrm, ref, blender = self.vrm.get().strip(), self.reference.get().strip(), self.blender.get().strip()
        problems = []
        if not os.path.isfile(vrm):
            problems.append("Choose your VRoid .vrm file.")
        if not os.path.isfile(ref):
            problems.append("Choose Kassandra's reference .glb (see the help button).")
        if not os.path.isfile(blender):
            problems.append("Blender 4.2 or newer is needed (free from blender.org or Steam). "
                            "Install it, then pick blender.exe.")
        if problems:
            messagebox.showwarning(APP_NAME, "\n".join(problems))
            return
        name = os.path.splitext(os.path.basename(vrm))[0]
        out_dir = os.path.join(self.out_dir.get().strip() or default_output_dir(), name)
        opts = {"proportions": "skeleton" if self.skeleton_mode.get() else "fit",
                "atlas_size": int(self.atlas_size.get() or 4096), "no_fingers": self.no_fingers.get()}
        self.cfg.update({"reference": ref, "blender": blender, "out_dir": self.out_dir.get(), **opts})
        save_config(self.cfg)
        cmd = build_command(blender, vrm, ref, out_dir, opts)
        self.go.configure(state="disabled")
        self.status.configure(text="Converting…")
        self.write(f"\nConverting {os.path.basename(vrm)} (this can take a minute)…\n")
        threading.Thread(target=self.run, args=(cmd, out_dir), daemon=True).start()

    def run(self, cmd, out_dir):
        flags = 0x08000000 if sys.platform == "win32" else 0  # no console window
        ok = False
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                    encoding="utf-8", errors="replace", creationflags=flags)
            for line in proc.stdout:
                if line.startswith("[vroid-kassandra]"):
                    self.log_queue.put(("log", line.replace("[vroid-kassandra] ", ""), None))
                    ok = ok or "DONE" in line
            proc.wait()
            ok = ok and proc.returncode == 0
        except OSError as exc:
            self.log_queue.put(("log", f"Could not start Blender: {exc}\n", None))
        self.log_queue.put(("done", out_dir, ok))

    def drain(self):
        try:
            while True:
                kind, a, b = self.log_queue.get_nowait()
                if kind == "log":
                    self.write(a)
                elif kind == "blender":
                    if a:
                        self.blender.set(a)
                        self.write(f"Found Blender {b[0]}.{b[1]}: {a}\n")
                    else:
                        self.write("Blender 4.2+ was not found. Install it from blender.org or Steam, "
                                   "then pick blender.exe with Browse.\n")
                elif kind == "done":
                    self.go.configure(state="normal")
                    if b:
                        self.status.configure(text="Done")
                        self.write(f"\nDone! Files are in {a}\nOpen NEXT_STEPS.txt there for the last steps.\n")
                        open_folder(a)
                    else:
                        self.status.configure(text="Failed")
                        self.write("\nConversion failed. The messages above say why.\n")
        except queue.Empty:
            pass
        self.root.after(100, self.drain)


def main():
    initial = next((a for a in sys.argv[1:] if a.lower().endswith(".vrm")), None)
    root = tk.Tk()
    App(root, initial)
    root.mainloop()


if __name__ == "__main__":
    main()
