"""Pack several VRoid textures into one atlas and remap UVs to it.

The game material that replaces Kassandra's body uses a single texture, so
every VRoid material in a group (opaque or cut-out) is laid out on a grid.
"""

import math
import os

import bpy
import numpy as np

PAD = 4  # pixels kept free around each tile so mipmaps do not bleed


def _tile_grid(count):
    k = max(1, math.ceil(math.sqrt(count)))
    return k


def build_atlas(entries, size, name, out_dir, keep_alpha):
    """entries: list of dicts with keys key, image_path (or None), factor (rgba).

    Writes ``<out_dir>/<name>.png`` and returns (bpy image, placements) where
    placements maps key -> (u0, v0, du, dv) in UV space.
    """
    k = _tile_grid(len(entries))
    tile = size // k
    atlas = np.zeros((size, size, 4), dtype=np.float32)
    atlas[..., 3] = 1.0
    placements = {}
    for i, entry in enumerate(entries):
        col, row = i % k, i // k
        if entry.get("image_path"):
            img = bpy.data.images.load(entry["image_path"], check_existing=False)
            img.scale(tile, tile)
            px = np.empty(tile * tile * 4, dtype=np.float32)
            img.pixels.foreach_get(px)
            px = px.reshape(tile, tile, 4)
            bpy.data.images.remove(img)
        else:
            px = np.ones((tile, tile, 4), dtype=np.float32)
        px = px * np.asarray(entry.get("factor", (1, 1, 1, 1)), dtype=np.float32)
        if not keep_alpha:
            px[..., 3] = 1.0
        atlas[row * tile:(row + 1) * tile, col * tile:(col + 1) * tile] = px
        u0 = (col * tile + PAD) / size
        v0 = (row * tile + PAD) / size
        span = (tile - 2 * PAD) / size
        placements[entry["key"]] = (u0, v0, span, span)
    image = bpy.data.images.new(name, size, size, alpha=True)
    image.pixels.foreach_set(np.clip(atlas, 0.0, 1.0).ravel())
    path = os.path.join(out_dir, f"{name}.png")
    image.filepath_raw = path
    image.file_format = "PNG"
    image.save()
    image.pack()
    return image, placements, path


def remap_uvs(mesh, face_keys, placements, uv_layer_name=None):
    """Move each face's UVs into the tile of its material key.

    face_keys: per-polygon key (or None to leave the face alone).
    """
    layer = mesh.uv_layers.get(uv_layer_name) if uv_layer_name else mesh.uv_layers.active
    if layer is None:
        return
    n = len(mesh.loops)
    uv = np.empty(n * 2, dtype=np.float32)
    layer.data.foreach_get("uv", uv)
    uv = uv.reshape(n, 2)
    loop_start = np.empty(len(mesh.polygons), dtype=np.int32)
    loop_total = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("loop_start", loop_start)
    mesh.polygons.foreach_get("loop_total", loop_total)
    for pi, key in enumerate(face_keys):
        if key is None or key not in placements:
            continue
        u0, v0, du, dv = placements[key]
        s, t = loop_start[pi], loop_total[pi]
        block = uv[s:s + t]
        # UVs that sit wholly in another 0..1 tile are shifted back first.
        offset = np.floor(block.min(axis=0) + 1e-6)
        offset[(block.max(axis=0) - offset) > 1.0 + 1e-6] = 0.0
        block = np.clip(block - offset, 0.0, 1.0)
        uv[s:s + t, 0] = u0 + block[:, 0] * du
        uv[s:s + t, 1] = v0 + block[:, 1] * dv
    layer.data.foreach_set("uv", uv.ravel())


def make_material(name, image, cutout):
    mat = bpy.data.materials.new(name)
    if bpy.app.version < (5, 0, 0) and not mat.use_nodes:
        try:
            mat.use_nodes = True
        except Exception:  # Blender 5+: always node based
            pass
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = image
    tex.location = (-400, 200)
    nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 1.0
    if cutout:
        # glTF exporter turns "alpha -> Round -> BSDF alpha" into alphaMode MASK.
        rnd = nt.nodes.new("ShaderNodeMath")
        rnd.operation = "ROUND"
        rnd.location = (-150, -100)
        nt.links.new(tex.outputs["Alpha"], rnd.inputs[0])
        nt.links.new(rnd.outputs[0], bsdf.inputs["Alpha"])
        for attr, value in (("blend_method", "CLIP"), ("surface_render_method", "DITHERED")):
            if hasattr(mat, attr):
                try:
                    setattr(mat, attr, value)
                except Exception:
                    pass
    return mat
