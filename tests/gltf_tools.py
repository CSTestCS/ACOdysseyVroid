"""Small glTF helpers for tests (numpy only)."""

import json
import os
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "converter"))
from vroid_kassandra import vrm  # noqa: E402

COMP = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def accessor(gltf, binchunk, index):
    acc = gltf["accessors"][index]
    view = gltf["bufferViews"][acc["bufferView"]]
    dtype = np.dtype(COMP[acc["componentType"]])
    n = NCOMP[acc["type"]]
    start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    stride = view.get("byteStride", dtype.itemsize * n)
    raw = np.frombuffer(binchunk, dtype=np.uint8, count=stride * (acc["count"] - 1) + dtype.itemsize * n,
                        offset=start)
    rows = [np.frombuffer(raw[i * stride:i * stride + dtype.itemsize * n].tobytes(), dtype=dtype)
            for i in range(acc["count"])] if stride != dtype.itemsize * n else None
    if rows is None:
        arr = np.frombuffer(raw.tobytes(), dtype=dtype).reshape(acc["count"], n)
    else:
        arr = np.array(rows)
    if acc.get("normalized"):
        arr = arr.astype(np.float32) / np.iinfo(dtype).max
    return arr


def node_trs(node):
    if "matrix" in node:
        return np.array(node["matrix"], dtype=float).reshape(4, 4).T
    t = np.array(node.get("translation", [0, 0, 0]), dtype=float)
    x, y, z, w = node.get("rotation", [0, 0, 0, 1])
    s = np.array(node.get("scale", [1, 1, 1]), dtype=float)
    r = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    m = np.eye(4)
    m[:3, :3] = r * s
    m[:3, 3] = t
    return m


def joint_locals(gltf):
    """{joint name: local 4x4} for the first skin."""
    skin = gltf["skins"][0]
    return {gltf["nodes"][j]["name"]: node_trs(gltf["nodes"][j]) for j in skin["joints"]}


def write_glb(path, gltf, binchunk):
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    bn = binchunk + b"\0" * ((4 - len(binchunk) % 4) % 4)
    total = 12 + 8 + len(js) + 8 + len(bn)
    with open(path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, total))
        f.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
        f.write(struct.pack("<II", len(bn), 0x004E4942) + bn)


VRM1_TO_VRM0_THUMB = {"ThumbMetacarpal": "ThumbProximal", "ThumbProximal": "ThumbIntermediate"}


def make_vrm0(src, dst):
    """Rewrite a VRM 1.0 file as VRM 0.x (humanoid list, model facing -Z)."""
    gltf, binchunk = vrm.read_glb(src)
    bones = gltf["extensions"].pop("VRMC_vrm")["humanoid"]["humanBones"]
    human = []
    for name, info in bones.items():
        for old, new in VRM1_TO_VRM0_THUMB.items():
            if name.endswith(old):
                name = name[:-len(old)] + new
        human.append({"bone": name, "node": info["node"]})
    gltf["extensions"]["VRM"] = {"humanoid": {"humanBones": human}, "materialProperties": []}
    gltf["extensionsUsed"] = [e for e in gltf.get("extensionsUsed", []) if e != "VRMC_vrm"] + ["VRM"]
    scene = gltf["scenes"][gltf.get("scene", 0)]
    gltf["nodes"].append({"name": "VRM0Root", "rotation": [0, 1, 0, 0], "children": scene["nodes"]})
    scene["nodes"] = [len(gltf["nodes"]) - 1]
    write_glb(dst, gltf, binchunk)
