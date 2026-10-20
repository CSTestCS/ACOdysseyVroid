"""End-to-end tests. Need Blender's Python module: ``pip install bpy`` (Python 3.13 for bpy 5.x).

Sample VRM: set VROID_SAMPLE to a .vrm file, or the test downloads the
VRoid-made sample from the three-vrm repository.

    pytest tests -q
"""

import json
import os
import subprocess
import sys
import urllib.request

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "converter"))

bpy = pytest.importorskip("bpy")

import gltf_tools  # noqa: E402
import make_fake_kassandra  # noqa: E402
from vroid_kassandra import pipeline, vrm  # noqa: E402

SAMPLE_URL = ("https://raw.githubusercontent.com/pixiv/three-vrm/dev/packages/three-vrm/"
              "examples/models/VRM1_Constraint_Twist_Sample.vrm")


@pytest.fixture(scope="session")
def sample_vrm(tmp_path_factory):
    path = os.environ.get("VROID_SAMPLE")
    if path:
        return path
    path = str(tmp_path_factory.mktemp("sample") / "sample.vrm")
    try:
        urllib.request.urlretrieve(SAMPLE_URL, path)
    except Exception as exc:  # pragma: no cover - offline
        pytest.skip(f"could not download sample VRM: {exc}")
    return path


@pytest.fixture(scope="session")
def sample_vrm0(sample_vrm, tmp_path_factory):
    path = str(tmp_path_factory.mktemp("sample0") / "sample_vrm0.vrm")
    gltf_tools.make_vrm0(sample_vrm, path)
    return path


def convert(tmp_path, vrm_path, hashed=False, yaw=0.0, helmet=False, **kw):
    tmp_path.mkdir(parents=True, exist_ok=True)
    ref = str(tmp_path / "kassandra.glb")
    expected = make_fake_kassandra.make(ref, hashed=hashed, yaw=yaw, helmet=helmet)
    out = str(tmp_path / "out")
    opts = pipeline.Options(vrm=vrm_path, reference=ref, out_dir=out, atlas_size=1024, **kw)
    report = pipeline.convert(opts)
    return ref, out, report, expected


def check_output(ref, out, report, proportions="fit"):
    ref_gltf, _ = vrm.read_glb(ref)
    gltf, binchunk = vrm.read_glb(os.path.join(out, report["output"]))

    # Same skeleton as Kassandra's, so AnvilToolkit can bind it.
    ref_joints = gltf_tools.joint_locals(ref_gltf)
    out_joints = gltf_tools.joint_locals(gltf)
    assert set(ref_joints) == set(out_joints)
    if proportions == "fit":
        for name, m in ref_joints.items():
            assert np.allclose(m, out_joints[name], atol=1e-4), name

    # Height matches Kassandra's within 1%.
    assert abs(report["result_height"] / report["kassandra_height"] - 1) < 0.01

    joint_names = [gltf["nodes"][j]["name"] for j in gltf["skins"][0]["joints"]]
    for mesh in gltf["meshes"]:
        for prim in mesh["primitives"]:
            joints = gltf_tools.accessor(gltf, binchunk, prim["attributes"]["JOINTS_0"])
            weights = gltf_tools.accessor(gltf, binchunk, prim["attributes"]["WEIGHTS_0"])
            assert np.allclose(weights.sum(axis=1), 1.0, atol=0.01)
            used = {joint_names[j] for j in np.unique(joints[weights > 0])}
            assert len(used) <= 42, (mesh["name"], len(used))
            assert "TEXCOORD_0" in prim["attributes"]
    return gltf


def test_named_skeleton(tmp_path, sample_vrm):
    ref, out, report, expected = convert(tmp_path, sample_vrm)
    mapping = json.load(open(os.path.join(out, "mapping.json")))["bones"]
    for hb, bone in expected.items():
        assert mapping[hb] == bone, hb
    gltf = check_output(ref, out, report)
    assert gltf["meshes"][0]["name"] == "Kassandra_Body"
    mats = {m["name"] for m in gltf["materials"]}
    assert "1001" in mats and "1002" in mats
    cut = next(m for m in gltf["materials"] if m["name"] == "1002")
    assert cut.get("alphaMode") == "MASK"
    assert os.path.exists(os.path.join(out, "VRM1_Constraint_Twist_Sample_opaque_atlas.png")) or \
        any(f.endswith("_opaque_atlas.png") for f in os.listdir(out))


@pytest.mark.parametrize("yaw", [90.0, 180.0])
def test_hashed_and_turned_skeleton(tmp_path, sample_vrm, yaw):
    ref, out, report, expected = convert(tmp_path, sample_vrm, hashed=True, yaw=yaw)
    mapping = json.load(open(os.path.join(out, "mapping.json")))["bones"]
    for hb, bone in expected.items():
        assert mapping[hb] == bone, hb
    check_output(ref, out, report)


def test_vrm0(tmp_path, sample_vrm0):
    ref, out, report, expected = convert(tmp_path, sample_vrm0)
    assert report["vrm_version"] == "0.x"
    mapping = json.load(open(os.path.join(out, "mapping.json")))["bones"]
    for hb, bone in expected.items():
        assert mapping[hb] == bone, hb
    check_output(ref, out, report)


def test_no_fingers_needs_no_extra_slots(tmp_path, sample_vrm):
    ref, out, report, _ = convert(tmp_path, sample_vrm, no_fingers=True)
    assert report["extra_material_slots"] == []
    assert not any("Index" in b or "Thumb" in b for b in report["bones_used"])
    check_output(ref, out, report)


def test_other_meshes_dropped_or_blanked(tmp_path, sample_vrm):
    ref, out, report, _ = convert(tmp_path / "a", sample_vrm, helmet=True)
    gltf = check_output(ref, out, report)
    assert [m["name"] for m in gltf["meshes"]] == ["Kassandra_Body"]

    ref, out, report, _ = convert(tmp_path / "b", sample_vrm, helmet=True, blank_other_meshes=True)
    gltf, binchunk = vrm.read_glb(os.path.join(out, report["output"]))
    helmet = next(m for m in gltf["meshes"] if m["name"] == "Kassandra_Helmet")
    pos = gltf_tools.accessor(gltf, binchunk, helmet["primitives"][0]["attributes"]["POSITION"])
    assert len(pos) <= 3 and np.ptp(pos, axis=0).max() < 1e-6  # one invisible, zero-size triangle


def test_skeleton_mode_moves_joints(tmp_path, sample_vrm):
    ref, out, report, _ = convert(tmp_path, sample_vrm, proportions="skeleton")
    gltf = check_output(ref, out, report, proportions="skeleton")
    ref_gltf, _ = vrm.read_glb(ref)
    a = gltf_tools.joint_locals(ref_gltf)["L_ForeArm"]
    b = gltf_tools.joint_locals(gltf)["L_ForeArm"]
    assert not np.allclose(a[:3, 3], b[:3, 3], atol=1e-3)


def test_output_deforms_with_kassandra_bones(tmp_path, sample_vrm):
    """Bend Kassandra's left elbow: the VRoid's left hand must follow, the right must not."""
    ref, out, report, _ = convert(tmp_path, sample_vrm)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    objs = pipeline.import_gltf(os.path.join(out, report["output"]))
    arm = next(o for o in objs if o.type == "ARMATURE")
    mesh = next(o for o in objs if o.type == "MESH")
    hand_l = arm.matrix_world @ arm.data.bones["L_Hand"].head_local
    hand_r = arm.matrix_world @ arm.data.bones["R_Hand"].head_local
    rest = pipeline.world_verts(mesh)
    near_l = np.linalg.norm(rest - np.array(hand_l), axis=1) < 0.05
    near_r = np.linalg.norm(rest - np.array(hand_r), axis=1) < 0.05
    assert near_l.sum() > 20 and near_r.sum() > 20
    pb = arm.pose.bones["L_ForeArm"]
    pb.rotation_mode = "XYZ"
    pb.rotation_euler = (1.2, 0, 0)
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    ev = mesh.evaluated_get(dg)
    m = ev.to_mesh()
    posed = np.array([mesh.matrix_world @ v.co for v in m.vertices])
    ev.to_mesh_clear()
    moved = np.linalg.norm(posed - rest, axis=1)
    assert np.median(moved[near_l]) > 0.1
    assert np.median(moved[near_r]) < 1e-4


def test_blender_command_line(tmp_path, sample_vrm):
    """The script runs the way the Windows launcher calls it.

    With BLENDER=/path/to/blender this uses a real Blender; otherwise it runs
    the same script with this Python's bpy module.
    """
    ref = str(tmp_path / "kassandra.glb")
    make_fake_kassandra.make(ref)
    out = tmp_path / "cli_out"
    script = os.path.join(HERE, "..", "converter", "run_converter.py")
    args = ["--", "--vrm", sample_vrm, "--reference", ref, "--out", str(out), "--atlas-size", "512"]
    blender = os.environ.get("BLENDER")
    cmd = [blender, "-b", "--factory-startup", "-P", script] if blender else [sys.executable, script]
    proc = subprocess.run(cmd + args, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[vroid-kassandra] DONE" in proc.stdout
    steps = (out / "NEXT_STEPS.txt").read_text()
    assert "Kassandra_Body" in steps and "_opaque_atlas.png" in steps

    bad = subprocess.run(cmd + ["--", "--vrm", ref, "--reference", ref, "--out", str(out)],
                         capture_output=True, text=True)
    assert bad.returncode != 0
    assert "ERROR" in bad.stdout
