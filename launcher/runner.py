"""Build the Blender command line the launcher runs (no GUI code, so it can be tested)."""

import os
import sys


def resource_dir():
    """Folder holding the bundled converter (PyInstaller unpacks it to _MEIPASS)."""
    if hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def converter_script():
    return os.path.join(resource_dir(), "converter", "run_converter.py")


def build_command(blender, vrm, reference, out_dir, opts):
    cmd = [blender, "-b", "--factory-startup", "-P", converter_script(), "--",
           "--vrm", vrm, "--reference", reference, "--out", out_dir,
           "--proportions", opts.get("proportions", "fit"),
           "--atlas-size", str(opts.get("atlas_size", 4096))]
    if opts.get("no_fingers"):
        cmd.append("--no-fingers")
    if opts.get("mapping"):
        cmd += ["--mapping", opts["mapping"]]
    return cmd
