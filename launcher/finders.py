"""Find Blender, Steam and Assassin's Creed Odyssey on a Windows PC.

Standard library only, so it works inside the PyInstaller .exe. Every
function takes optional roots so it can be tested on any OS.
"""

import glob
import os
import re
import shutil
import subprocess
import sys

AC_ODYSSEY_APPID = "812140"
BLENDER_STEAM_APPID = "365670"
MIN_BLENDER = (4, 2)


def _winreg_value(root_name, key, value):
    if sys.platform != "win32":
        return None
    try:
        import winreg
        root = getattr(winreg, root_name)
        with winreg.OpenKey(root, key) as k:
            return winreg.QueryValueEx(k, value)[0]
    except OSError:
        return None


def steam_root():
    for root, key in (("HKEY_CURRENT_USER", r"Software\Valve\Steam"),
                      ("HKEY_LOCAL_MACHINE", r"SOFTWARE\WOW6432Node\Valve\Steam"),
                      ("HKEY_LOCAL_MACHINE", r"SOFTWARE\Valve\Steam")):
        for value in ("SteamPath", "InstallPath"):
            path = _winreg_value(root, key, value)
            if path and os.path.isdir(path):
                return os.path.normpath(path)
    default = os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Steam")
    return default if os.path.isdir(default) else None


def steam_libraries(root=None):
    """All Steam library folders (the main one first)."""
    root = root or steam_root()
    if not root:
        return []
    libs = [root]
    vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
    try:
        with open(vdf, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return libs
    for match in re.finditer(r'"path"\s+"([^"]+)"', text):
        path = os.path.normpath(match.group(1).replace("\\\\", "\\"))
        if path not in libs and os.path.isdir(path):
            libs.append(path)
    return libs


def steam_app_dir(appid, libraries=None):
    """Install folder of a Steam app, read from its appmanifest."""
    for lib in libraries if libraries is not None else steam_libraries():
        manifest = os.path.join(lib, "steamapps", f"appmanifest_{appid}.acf")
        try:
            with open(manifest, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        m = re.search(r'"installdir"\s+"([^"]+)"', text)
        if m:
            path = os.path.join(lib, "steamapps", "common", m.group(1))
            if os.path.isdir(path):
                return path
    return None


def ac_odyssey_dir(libraries=None):
    return steam_app_dir(AC_ODYSSEY_APPID, libraries)


def blender_version(exe):
    """(major, minor) of a Blender executable, or None if it does not run."""
    try:
        flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=60,
                             creationflags=flags).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"Blender\s+(\d+)\.(\d+)", out)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _version_from_path(path):
    m = re.search(r"(\d+)\.(\d+)", os.path.basename(os.path.dirname(path)))
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def blender_candidates(program_dirs=None, libraries=None):
    """Possible blender executables, newest first."""
    exe = "blender.exe" if sys.platform == "win32" else "blender"
    found = []
    env = os.environ.get("BLENDER")
    if env:
        found.append(env)
    if program_dirs is None:
        program_dirs = [os.environ.get(v) for v in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)")]
        program_dirs.append(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"))
    versioned = []
    for base in filter(None, program_dirs):
        versioned += glob.glob(os.path.join(base, "Blender Foundation", "*", exe))
        versioned += glob.glob(os.path.join(base, "Blender*", exe))
    found += sorted(set(versioned), key=_version_from_path, reverse=True)
    steam_blender = steam_app_dir(BLENDER_STEAM_APPID, libraries)
    if steam_blender:
        found.append(os.path.join(steam_blender, exe))
    on_path = shutil.which("blender")
    if on_path:
        found.append(on_path)
    seen, result = set(), []
    for path in found:
        key = os.path.normcase(os.path.abspath(path))
        if key not in seen and os.path.isfile(path):
            seen.add(key)
            result.append(path)
    return result


def find_blender(check_version=True):
    """Return (path, version) of the first usable Blender, or (None, None)."""
    for path in blender_candidates():
        if not check_version:
            return path, None
        version = blender_version(path)
        if version and version >= MIN_BLENDER:
            return path, version
    return None, None
