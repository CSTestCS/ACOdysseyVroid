"""Tests for the Windows launcher's discovery logic (run on any OS)."""

import os
import stat
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "launcher"))

import finders  # noqa: E402


def make_library(root, appid, installdir):
    apps = root / "steamapps"
    (apps / "common" / installdir).mkdir(parents=True)
    (apps / f"appmanifest_{appid}.acf").write_text(
        f'"AppState"\n{{\n\t"appid"\t\t"{appid}"\n\t"installdir"\t\t"{installdir}"\n}}\n')
    return apps / "common" / installdir


def test_finds_odyssey_in_second_library(tmp_path):
    main = tmp_path / "Steam"
    (main / "steamapps").mkdir(parents=True)
    other = tmp_path / "Games"
    game = make_library(other, finders.AC_ODYSSEY_APPID, "Assassin's Creed Odyssey")
    escaped = str(other).replace("\\", "\\\\")
    (main / "steamapps" / "libraryfolders.vdf").write_text(
        f'"libraryfolders"\n{{\n\t"0"\n\t{{\n\t\t"path"\t\t"{main}"\n\t}}\n'
        f'\t"1"\n\t{{\n\t\t"path"\t\t"{escaped}"\n\t}}\n}}\n')
    libs = finders.steam_libraries(str(main))
    assert os.path.normpath(str(other)) in libs
    assert finders.ac_odyssey_dir(libs) == str(game)


def test_missing_game_returns_none(tmp_path):
    assert finders.ac_odyssey_dir([str(tmp_path)]) is None


def test_blender_candidates_newest_first(tmp_path, monkeypatch):
    monkeypatch.delenv("BLENDER", raising=False)
    monkeypatch.setattr(finders.shutil, "which", lambda name: None)
    exe = "blender.exe" if sys.platform == "win32" else "blender"
    paths = []
    for ver in ("4.2", "5.2", "4.5"):
        d = tmp_path / "Blender Foundation" / f"Blender {ver}"
        d.mkdir(parents=True)
        p = d / exe
        p.write_text("")
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
        paths.append(str(p))
    found = finders.blender_candidates(program_dirs=[str(tmp_path)], libraries=[])
    assert [os.path.basename(os.path.dirname(f)) for f in found] == ["Blender 5.2", "Blender 4.5", "Blender 4.2"]


def test_build_command_passes_options():
    import runner
    cmd = runner.build_command("blender", "a.vrm", "k.glb", "out",
                               {"proportions": "fit", "atlas_size": 2048, "no_fingers": True})
    assert cmd[:4] == ["blender", "-b", "--factory-startup", "-P"]
    assert cmd[4].endswith(os.path.join("converter", "run_converter.py"))
    assert os.path.isfile(cmd[4])
    i = cmd.index("--")
    assert cmd[i + 1:i + 3] == ["--vrm", "a.vrm"] and "--no-fingers" in cmd and "2048" in cmd
