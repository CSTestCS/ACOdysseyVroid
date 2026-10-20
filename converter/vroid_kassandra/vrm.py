"""Read the parts of a .vrm (binary glTF) file that the converter needs.

Pure Python: no Blender required, so it can be unit tested on its own.
Supports both VRM 0.x (``extensions.VRM``) and VRM 1.0 (``extensions.VRMC_vrm``).
"""

import json
import os
import re
import struct

# VRoid parts drawn with transparent textures even when a file marks them OPAQUE.
CUTOUT_NAME_HINT = re.compile(r"(eyeline|eyelash|lash|brow|highlight)", re.IGNORECASE)

GLB_MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942


class VrmError(Exception):
    pass


def read_glb(path):
    """Return (json_dict, bin_bytes_or_None) for a .glb/.vrm file."""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 20:
        raise VrmError(f"{path}: file is too small to be a VRM/GLB")
    magic, version, length = struct.unpack_from("<III", data, 0)
    if magic != GLB_MAGIC:
        raise VrmError(f"{path}: not a binary glTF/VRM file")
    if version != 2:
        raise VrmError(f"{path}: unsupported glTF container version {version}")
    offset = 12
    gltf = None
    binchunk = None
    while offset + 8 <= min(length, len(data)):
        clen, ctype = struct.unpack_from("<II", data, offset)
        chunk = data[offset + 8:offset + 8 + clen]
        if ctype == CHUNK_JSON:
            gltf = json.loads(chunk.decode("utf-8"))
        elif ctype == CHUNK_BIN and binchunk is None:
            binchunk = chunk
        offset += 8 + clen
    if gltf is None:
        raise VrmError(f"{path}: no JSON chunk found")
    return gltf, binchunk


def vrm_version(gltf):
    ext = gltf.get("extensions", {})
    if "VRMC_vrm" in ext:
        return "1.0"
    if "VRM" in ext:
        return "0.x"
    return None


def humanoid_nodes(gltf):
    """Return {humanoid_bone_name: node_index} using VRM 1.0 bone names.

    VRM 0.x thumb names are kept as-is (they are handled by the humanoid
    hierarchy, which accepts both naming schemes).
    """
    ext = gltf.get("extensions", {})
    result = {}
    if "VRMC_vrm" in ext:
        bones = ext["VRMC_vrm"].get("humanoid", {}).get("humanBones", {})
        for name, info in bones.items():
            if isinstance(info, dict) and "node" in info:
                result[name] = int(info["node"])
    elif "VRM" in ext:
        for item in ext["VRM"].get("humanoid", {}).get("humanBones", []):
            if "bone" in item and item.get("node", -1) >= 0:
                result[item["bone"]] = int(item["node"])
    else:
        raise VrmError("This file has no VRM humanoid data. Export it from VRoid Studio as .vrm.")
    for required in ("hips", "spine", "head", "leftUpperArm", "rightUpperArm",
                     "leftUpperLeg", "rightUpperLeg", "leftLowerLeg", "rightLowerLeg"):
        if required not in result:
            raise VrmError(f"VRM humanoid is missing required bone '{required}'")
    return result


def node_names(gltf):
    return [n.get("name", f"node_{i}") for i, n in enumerate(gltf.get("nodes", []))]


def material_info(gltf):
    """List of dicts: name, alpha_mode, base_color_factor, image_index (or None)."""
    textures = gltf.get("textures", [])
    out = []
    for i, m in enumerate(gltf.get("materials", [])):
        pbr = m.get("pbrMetallicRoughness", {})
        tex = pbr.get("baseColorTexture")
        image_index = None
        if tex is not None and tex.get("index", -1) < len(textures):
            image_index = textures[tex["index"]].get("source")
        mode = m.get("alphaMode", "OPAQUE")
        # VRM 0.x stores the real render mode in its own material properties.
        out.append({
            "name": m.get("name", f"material_{i}"),
            "alpha_mode": mode,
            "alpha_cutoff": m.get("alphaCutoff", 0.5),
            "base_color_factor": pbr.get("baseColorFactor", [1.0, 1.0, 1.0, 1.0]),
            "image_index": image_index,
        })
    vrm0 = gltf.get("extensions", {}).get("VRM", {}).get("materialProperties")
    if vrm0:
        by_name = {p.get("name"): p for p in vrm0}
        for info in out:
            prop = by_name.get(info["name"])
            if not prop:
                continue
            tags = prop.get("tagMap", {})
            render = tags.get("RenderType", "")
            if render == "TransparentCutout":
                info["alpha_mode"] = "MASK"
            elif render == "Transparent":
                info["alpha_mode"] = "BLEND"
    for info in out:
        if info["alpha_mode"] == "OPAQUE" and CUTOUT_NAME_HINT.search(info["name"]):
            info["alpha_mode"] = "MASK"
    return out


def extract_image(gltf, binchunk, image_index, out_dir):
    """Write image ``image_index`` to ``out_dir`` and return its path."""
    img = gltf["images"][image_index]
    mime = img.get("mimeType", "image/png")
    ext = ".jpg" if "jpeg" in mime or "jpg" in mime else ".png"
    path = os.path.join(out_dir, f"vrm_image_{image_index}{ext}")
    if "bufferView" in img:
        if binchunk is None:
            raise VrmError("image refers to a missing binary chunk")
        view = gltf["bufferViews"][img["bufferView"]]
        start = view.get("byteOffset", 0)
        data = binchunk[start:start + view["byteLength"]]
    else:
        raise VrmError("external image files are not supported; export a self-contained .vrm")
    with open(path, "wb") as f:
        f.write(data)
    return path
