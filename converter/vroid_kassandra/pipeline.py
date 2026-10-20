"""Convert a VRoid .vrm into a GLB rigged to Kassandra's skeleton.

Runs inside Blender (``blender -b -P run_converter.py -- ...``).

Steps:
 1. Import Kassandra's reference GLB (exported with AnvilToolkit) and the VRM.
 2. Work out which way each model faces and how tall each one is.
 3. Match every VRM humanoid bone to a Kassandra bone by position.
 4. Scale the VRM uniformly to Kassandra's height (its proportions are kept).
 5. Rotate the VRM limbs into Kassandra's rest pose (angles only).
 6. Move the skin weights onto Kassandra's bones and bind to her skeleton.
 7. Pack textures into atlases, split parts that use too many bones, triangulate.
 8. Export a GLB that AnvilToolkit's Mesh Viewer can import over Kassandra.
"""

import json
import math
import os
import re
import shutil
import tempfile

import bpy  # noqa: I001 - bpy must load before bmesh
import bmesh
import numpy as np
from mathutils import Matrix, Quaternion, Vector

from . import atlas, humanoid, matching
from . import vrm as vrmio

HELPER_BONE = re.compile(r"(roll|aim|twist)", re.IGNORECASE)


class ConversionError(Exception):
    pass


class Options:
    def __init__(self, vrm, reference, out_dir, mapping=None, target_height=None,
                 proportions="fit", atlas_size=4096, max_bones=42, max_influences=4,
                 replace_mesh=None, opaque_material=None, cutout_material=None,
                 blank_other_meshes=False, target_yaw=None, no_fingers=False,
                 output_name=None):
        self.vrm = vrm
        self.reference = reference
        self.out_dir = out_dir
        self.mapping = mapping
        self.target_height = target_height
        self.proportions = proportions
        self.atlas_size = atlas_size
        self.max_bones = max_bones
        self.max_influences = max_influences
        self.replace_mesh = replace_mesh
        self.opaque_material = opaque_material
        self.cutout_material = cutout_material
        self.blank_other_meshes = blank_other_meshes
        self.target_yaw = target_yaw
        self.no_fingers = no_fingers
        self.output_name = output_name


def log(msg):
    print(f"[vroid-kassandra] {msg}", flush=True)


# --------------------------------------------------------------------------- helpers

def op_kwargs(op, **kwargs):
    """Drop keyword arguments this Blender version's operator does not know."""
    props = set(op.get_rna_type().properties.keys())
    return {k: v for k, v in kwargs.items() if k in props}


def import_gltf(path):
    before = set(bpy.data.objects)
    op = bpy.ops.import_scene.gltf
    result = op(**op_kwargs(op, filepath=path, bone_heuristic="BLENDER",
                            guess_original_bind_pose=False, import_shading="NORMALS"))
    if "FINISHED" not in result:
        raise ConversionError(f"Blender could not import {path}")
    new = [o for o in bpy.data.objects if o not in before]
    # The importer adds helper objects (bone display shapes) that are not part of the file.
    helpers = [o for o in new if any(c.name.startswith("glTF_not_exported") for c in o.users_collection)]
    for o in helpers:
        remove_object(o)
    return [o for o in new if o not in helpers]


def world_verts(obj):
    n = len(obj.data.vertices)
    co = np.empty(n * 3, dtype=np.float64)
    obj.data.vertices.foreach_get("co", co)
    co = co.reshape(n, 3)
    mw = np.array(obj.matrix_world)
    return co @ mw[:3, :3].T + mw[:3, 3]


def bbox(points):
    pts = np.asarray(points)
    return pts.min(axis=0), pts.max(axis=0)


def set_active(obj):
    bpy.context.view_layer.update()
    for o in bpy.context.view_layer.objects:
        if o is not None:
            o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def mode(name):
    if bpy.context.object and bpy.context.object.mode != name:
        bpy.ops.object.mode_set(mode=name)


def remove_object(obj):
    data = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if data is not None and data.users == 0:
        if isinstance(data, bpy.types.Mesh):
            bpy.data.meshes.remove(data)
        elif isinstance(data, bpy.types.Armature):
            bpy.data.armatures.remove(data)


def strip_suffix(name):
    return re.sub(r"\.\d{3}$", "", name)


# --------------------------------------------------------------------------- reference

class Reference:
    def __init__(self, objects, opts):
        arms = [o for o in objects if o.type == "ARMATURE"]
        if not arms:
            raise ConversionError(
                "The reference GLB has no skeleton. In AnvilToolkit, open Kassandra's mesh "
                "in the Mesh Viewer together with her skeleton and export it as GLB.")
        self.arm = max(arms, key=lambda a: len(a.data.bones))
        self.meshes = [o for o in objects if o.type == "MESH"]
        mw = self.arm.matrix_world
        self.heads = {b.name: tuple(mw @ b.head_local) for b in self.arm.data.bones}
        self.parents = {b.name: (b.parent.name if b.parent else None) for b in self.arm.data.bones}
        self.skeleton = matching.TargetSkeleton(self.heads, self.parents)
        bone_pts = np.array(list(self.heads.values()))
        bmin, bmax = bbox(bone_pts)
        if self.meshes:
            mesh_pts = np.concatenate([world_verts(o) for o in self.meshes])
            mmin, mmax = bbox(mesh_pts)
        else:
            mmin, mmax = bmin, bmax
        mesh_h, bone_h = mmax[2] - mmin[2], bmax[2] - bmin[2]
        if self.meshes and mesh_h > 0.5 * bone_h:
            lo, hi = mmin, mmax
        else:
            lo, hi = bmin, bmax
        self.ground = float(lo[2])
        self.center = np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, self.ground])
        self.height = float(opts.target_height or (hi[2] - lo[2]))
        if self.height <= 0:
            raise ConversionError("Could not measure Kassandra's height from the reference GLB.")

        if opts.replace_mesh:
            found = [o for o in self.meshes if o.name == opts.replace_mesh or o.data.name == opts.replace_mesh]
            if not found:
                raise ConversionError(f"--replace-mesh {opts.replace_mesh!r} is not in the reference GLB. "
                                      f"Meshes found: {[o.name for o in self.meshes]}")
            self.main = found[0]
        elif self.meshes:
            self.main = max(self.meshes, key=lambda o: len(o.data.vertices))
        else:
            self.main = None
        self.material_names = self._materials_by_area(self.main)

    @staticmethod
    def _materials_by_area(obj):
        if obj is None:
            return []
        counts = {}
        for poly in obj.data.polygons:
            if poly.material_index < len(obj.material_slots):
                mat = obj.material_slots[poly.material_index].material
                if mat is not None:
                    counts[mat.name] = counts.get(mat.name, 0) + 1
        names = sorted(counts, key=lambda n: -counts[n])
        for slot in obj.material_slots:
            if slot.material and slot.material.name not in names:
                names.append(slot.material.name)
        return names


# --------------------------------------------------------------------------- vrm side

class VrmModel:
    def __init__(self, path, tmpdir):
        self.gltf, self.binchunk = vrmio.read_glb(path)
        self.version = vrmio.vrm_version(self.gltf)
        human_nodes = vrmio.humanoid_nodes(self.gltf)
        names = vrmio.node_names(self.gltf)
        glb = os.path.join(tmpdir, "model.glb")
        shutil.copyfile(path, glb)  # Blender's importer wants a .glb extension
        objects = import_gltf(glb)
        arms = [o for o in objects if o.type == "ARMATURE"]
        if not arms:
            raise ConversionError("The VRM has no skeleton.")
        self.arm = max(arms, key=lambda a: len(a.data.bones))
        self.meshes = [o for o in objects if o.type == "MESH"
                       and any(m.type == "ARMATURE" for m in o.modifiers)]
        if not self.meshes:
            raise ConversionError("The VRM has no skinned meshes.")
        self.extra_objects = [o for o in objects if o is not self.arm and o not in self.meshes]
        bones = self.arm.data.bones
        self.bone_of = {}
        for hb, node in human_nodes.items():
            node_name = names[node]
            bname = node_name if node_name in bones else node_name[:63]
            if bname not in bones:
                continue
            if hb in humanoid.FOLD_INTO_PARENT:
                continue
            if hb in humanoid.KNOWN_BONES:
                self.bone_of[hb] = bname
        self.folded = {names[n]: hb for hb, n in human_nodes.items() if hb in humanoid.FOLD_INTO_PARENT}
        self.present = set(self.bone_of)
        self.humanoid_of = {b: h for h, b in self.bone_of.items()}
        self.materials = {m["name"]: m for m in vrmio.material_info(self.gltf)}

    def joint_world(self, hb):
        return tuple(self.arm.matrix_world @ self.arm.data.bones[self.bone_of[hb]].head_local)

    def mesh_bounds(self):
        return bbox(np.concatenate([world_verts(o) for o in self.meshes]))


# --------------------------------------------------------------------------- steps

def bake_transform(objects, matrix):
    """Apply ``matrix`` (world space) to object data and reset object transforms."""
    worlds = {o: o.matrix_world.copy() for o in objects}
    done = set()
    for o in objects:
        if o.data is not None and o.data not in done:
            m = matrix @ worlds[o]
            if isinstance(o.data, bpy.types.Mesh):
                o.data.transform(m, shape_keys=True)
            else:
                o.data.transform(m)
            done.add(o.data)
    for o in objects:
        o.parent = None
        o.matrix_world = Matrix.Identity(4)
    bpy.context.view_layer.update()


def fit_pose(model, mapping, ref):
    """Rotate VRM bones so each limb points the way Kassandra's does."""
    arm = model.arm
    set_active(arm)
    mode("POSE")
    pbones = arm.pose.bones
    tarm_mw = ref.arm.matrix_world

    def tgt(hb):
        return tarm_mw @ ref.arm.data.bones[mapping[hb]].head_local

    def cur(hb):
        return arm.matrix_world @ pbones[model.bone_of[hb]].head

    _, ordered = humanoid.build_hierarchy(model.present)
    for hb in ordered:
        if hb == "hips":
            continue
        child = humanoid.direction_child(hb, model.present)
        if child is None or mapping[hb] == mapping[child]:
            continue
        dt = tgt(child) - tgt(hb)
        dv = cur(child) - cur(hb)
        if dt.length < 1e-6 or dv.length < 1e-6:
            continue
        q = dv.rotation_difference(dt)
        side = "left" if hb.startswith("left") else "right" if hb.startswith("right") else None
        markers = humanoid.hand_side_markers(side, model.present) if side and hb.endswith("Hand") else None
        if markers and all(m in mapping for m in markers) and mapping[markers[0]] != mapping[markers[1]]:
            axis = dt.normalized()
            sv = q @ (cur(markers[0]) - cur(markers[1]))
            st = tgt(markers[0]) - tgt(markers[1])
            sv -= axis * sv.dot(axis)
            st -= axis * st.dot(axis)
            if sv.length > 1e-6 and st.length > 1e-6:
                angle = math.atan2(axis.dot(sv.cross(st)), sv.dot(st))
                q = Quaternion(axis, angle) @ q
        pb = pbones[model.bone_of[hb]]
        pivot = pb.head.copy()
        rot = q.to_matrix().to_4x4()
        pb.matrix = Matrix.Translation(pivot) @ rot @ Matrix.Translation(-pivot) @ pb.matrix
        bpy.context.view_layer.update()
    posed = {pb.name: arm.matrix_world @ pb.head for pb in pbones}
    posed_tail = {pb.name: arm.matrix_world @ pb.tail for pb in pbones}
    mode("OBJECT")
    return posed, posed_tail


def bake_pose_into_meshes(model):
    dg = bpy.context.evaluated_depsgraph_get()
    for obj in model.meshes:
        ev = obj.evaluated_get(dg)
        m = ev.to_mesh()
        n = len(obj.data.vertices)
        if len(m.vertices) != n:
            ev.to_mesh_clear()
            raise ConversionError(f"Mesh {obj.name} changed vertex count while posing")
        co = np.empty(n * 3, dtype=np.float32)
        m.vertices.foreach_get("co", co)
        ev.to_mesh_clear()
        if obj.data.shape_keys:
            obj.shape_key_clear()
        obj.data.vertices.foreach_set("co", co)
        obj.data.update()
        for mod in list(obj.modifiers):
            obj.modifiers.remove(mod)


def assign_humanoid(model):
    """Body part (humanoid bone) that every VRM bone belongs to, from the rest pose.

    Humanoid bones belong to themselves. A chain of secondary bones (hair,
    skirt, sleeves) belongs to the part its root sits on: normally its
    humanoid parent, but twist/roll helpers and bones hanging off the hips
    pick the nearest of the parent and its child limbs (VRoid hangs thigh and
    upper-arm twist bones off the hips and shoulders).
    """
    parent_of, _ = humanoid.build_hierarchy(model.present)
    kids = {}
    for h, p in parent_of.items():
        kids.setdefault(p, []).append(h)
    bones = model.arm.data.bones

    def seg_dist(hb, point):
        bone = bones[model.bone_of[hb]]
        a = bone.head_local
        child = humanoid.direction_child(hb, model.present)
        b = bones[model.bone_of[child]].head_local if child else bone.tail_local
        ab = b - a
        t = 0.0 if ab.length_squared < 1e-12 else max(0.0, min(1.0, (point - a).dot(ab) / ab.length_squared))
        return (a + ab * t - point).length

    assign = {}

    def visit(bone):
        name = bone.name
        if name in model.humanoid_of:
            assign[name] = model.humanoid_of[name]
        elif name in model.folded:
            assign[name] = "head" if "head" in model.present else "hips"
        elif bone.parent is None:
            assign[name] = "hips"
        elif bone.parent.name not in model.humanoid_of:
            assign[name] = assign[bone.parent.name]
        else:
            a = model.humanoid_of[bone.parent.name]
            cands = [a]
            if HELPER_BONE.search(name) or a == "hips":
                cands += [k for k in kids.get(a, []) if k != "spine"]
            assign[name] = min(cands, key=lambda h: seg_dist(h, bone.head_local))
        for child in bone.children:
            visit(child)

    for root in [b for b in bones if b.parent is None]:
        visit(root)
    return assign


def reparent_helpers(model, assign):
    """Hang secondary chains under the body part they belong to, so they follow it when posed."""
    arm = model.arm
    moves = [(b.name, model.bone_of[assign[b.name]]) for b in arm.data.bones
             if b.name not in model.humanoid_of and b.parent is not None
             and b.parent.name in model.humanoid_of
             and model.humanoid_of[b.parent.name] != assign[b.name]]
    if not moves:
        return 0
    set_active(arm)
    mode("EDIT")
    eb = arm.data.edit_bones
    for name, new_parent in moves:
        eb[name].use_connect = False
        eb[name].parent = eb[new_parent]
    mode("OBJECT")
    return len(moves)


def vrm_bone_targets(model, mapping, assign, no_fingers):
    """Kassandra bone for every VRM bone (humanoid and secondary)."""
    def target_of(hb):
        if no_fingers and humanoid.is_finger(hb):
            side = "left" if hb.startswith("left") else "right"
            return mapping[f"{side}Hand"]
        return mapping[hb]

    return {name: target_of(hb) for name, hb in assign.items()}


def join_meshes(meshes):
    if len(meshes) == 1:
        return meshes[0]
    main = max(meshes, key=lambda o: len(o.data.vertices))
    with bpy.context.temp_override(active_object=main, object=main,
                                   selected_objects=meshes, selected_editable_objects=meshes):
        bpy.ops.object.join()
    return main


def transfer_weights(obj, bone_targets, fallback, max_influences):
    names = [g.name for g in obj.vertex_groups]
    group_target = [bone_targets.get(n, fallback) for n in names]
    per_vertex = []
    for v in obj.data.vertices:
        acc = {}
        for g in v.groups:
            if g.weight <= 0 or g.group >= len(group_target):
                continue
            t = group_target[g.group]
            acc[t] = acc.get(t, 0.0) + g.weight
        if not acc:
            acc = {fallback: 1.0}
        top = sorted(acc.items(), key=lambda kv: -kv[1])[:max_influences]
        total = sum(w for _, w in top)
        per_vertex.append([(t, w / total) for t, w in top])
    obj.vertex_groups.clear()
    groups = {}
    for i, infl in enumerate(per_vertex):
        for t, w in infl:
            vg = groups.get(t)
            if vg is None:
                vg = groups[t] = obj.vertex_groups.new(name=t)
            vg.add([i], w, "REPLACE")
    return sorted(groups)


def move_skeleton_to_vrm(ref, model, mapping, posed):
    """Experimental: move Kassandra's joints onto the VRM joints."""
    arm = ref.arm
    inv = arm.matrix_world.inverted()
    set_active(arm)
    mode("EDIT")
    ebones = arm.data.edit_bones
    parent_of, ordered = humanoid.build_hierarchy(model.present)
    for hb in ordered:
        p = parent_of[hb]
        if p is not None and mapping[p] == mapping[hb]:
            continue
        eb = ebones[mapping[hb]]
        delta = inv @ posed[model.bone_of[hb]] - eb.head
        stack = [eb]
        while stack:
            b = stack.pop()
            b.head += delta
            b.tail += delta
            stack.extend(b.children)
    mode("OBJECT")


def bind(obj, arm):
    obj.parent = arm
    obj.matrix_parent_inverse = arm.matrix_world.inverted()
    mod = obj.modifiers.new("Armature", "ARMATURE")
    mod.object = arm


def build_materials(obj, model, opts, out_dir, opaque_name, cutout_name):
    mesh = obj.data
    slots = [s.material for s in obj.material_slots]
    slot_info = []
    for mat in slots:
        info = model.materials.get(strip_suffix(mat.name)) if mat else None
        if info is None and mat is not None:
            info = model.materials.get(mat.name)
        slot_info.append(info)

    tmp = tempfile.mkdtemp(prefix="vrm_img_")
    groups = {"opaque": [], "cutout": []}
    for i, info in enumerate(slot_info):
        cut = info is not None and info["alpha_mode"] in ("MASK", "BLEND")
        path = None
        if info is not None and info["image_index"] is not None:
            path = vrmio.extract_image(model.gltf, model.binchunk, info["image_index"], tmp)
        factor = info["base_color_factor"] if info else (1, 1, 1, 1)
        groups["cutout" if cut else "opaque"].append({"key": i, "image_path": path, "factor": factor,
                                                      "name": info["name"] if info else f"slot{i}"})
    if opaque_name == cutout_name:
        groups = {"opaque": groups["opaque"] + groups["cutout"], "cutout": []}

    placements, new_mats, slot_to_new = {}, [], {}
    for gname, entries in groups.items():
        if not entries:
            continue
        mat_name = opaque_name if gname == "opaque" else cutout_name
        image, place, _ = atlas.build_atlas(
            entries, opts.atlas_size, f"{opts.output_name}_{gname}_atlas", out_dir,
            keep_alpha=(gname == "cutout" or opaque_name == cutout_name))
        placements.update(place)
        new_mats.append(atlas.make_material(mat_name, image, cutout=(gname == "cutout")))
        for e in entries:
            slot_to_new[e["key"]] = len(new_mats) - 1
    face_keys = [p.material_index for p in mesh.polygons]
    atlas.remap_uvs(mesh, face_keys, placements)
    shutil.rmtree(tmp, ignore_errors=True)

    new_index = np.array([slot_to_new.get(p.material_index, 0) for p in mesh.polygons], dtype=np.int32)
    mesh.materials.clear()
    for m in new_mats:
        mesh.materials.append(m)
    mesh.polygons.foreach_set("material_index", new_index)
    for m in slots:
        if m is not None and m.users == 0:
            bpy.data.materials.remove(m)
    return {g: [e["name"] for e in entries] for g, entries in groups.items()}


def split_bone_limit(obj, limit, spare_names):
    """Give extra material slots to faces so no slot uses more than ``limit`` bones."""
    mesh = obj.data
    vgroups = [set(g.group for g in v.groups if g.weight > 0) for v in mesh.vertices]
    added = []
    slot_count = len(mesh.materials)
    face_mat = [p.material_index for p in mesh.polygons]
    for mi in range(slot_count):
        faces = [p for p in mesh.polygons if p.material_index == mi]
        fbones = {p.index: frozenset().union(*(vgroups[v] for v in p.vertices)) for p in faces}
        if len(frozenset().union(*fbones.values())) <= limit if fbones else True:
            continue
        chunks = []  # [bone set, slot index]
        for p in sorted(faces, key=lambda p: sorted(fbones[p.index])):
            fb = fbones[p.index]
            for chunk in chunks:
                if len(chunk[0] | fb) <= limit:
                    chunk[0] |= fb
                    face_mat[p.index] = chunk[1]
                    break
            else:
                if not chunks:
                    slot = mi
                else:
                    base = mesh.materials[mi]
                    name = spare_names.pop(0) if spare_names else f"{strip_suffix(base.name)}_part{len(chunks) + 1}"
                    mat = base.copy()
                    mat.name = name
                    mesh.materials.append(mat)
                    slot = len(mesh.materials) - 1
                    added.append(mat.name)
                chunks.append([set(fb), slot])
                face_mat[p.index] = slot
    mesh.polygons.foreach_set("material_index", np.array(face_mat, dtype=np.int32))
    return added


def triangulate(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()


def keep_first_uv(mesh):
    while len(mesh.uv_layers) > 1:
        mesh.uv_layers.remove(mesh.uv_layers[-1])


def make_placeholder(name, mesh_name, material_name, arm, bone, position):
    mesh = bpy.data.meshes.new(mesh_name)
    p = tuple(position)
    mesh.from_pydata([p, p, p], [], [(0, 1, 2)])
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    if material_name:
        mat = bpy.data.materials.get(material_name) or bpy.data.materials.new(material_name)
        mesh.materials.append(mat)
    vg = obj.vertex_groups.new(name=bone)
    vg.add([0, 1, 2], 1.0, "REPLACE")
    bind(obj, arm)
    return obj


def export_glb(path, objects):
    bpy.context.view_layer.update()
    for o in bpy.context.view_layer.objects:
        if o is not None:
            o.select_set(o in objects)
    op = bpy.ops.export_scene.gltf
    result = op(**op_kwargs(
        op, filepath=path, export_format="GLB", use_selection=True, export_skins=True,
        export_animations=False, export_morph=False, export_yup=True, export_apply=False,
        export_texcoords=True, export_normals=True, export_tangents=False,
        export_materials="EXPORT", export_image_format="AUTO", export_def_bones=False,
        export_all_influences=False, export_rest_position_armature=True,
        export_cameras=False, export_lights=False, export_extras=False,
        export_vertex_color="NONE", export_shared_accessors=False))
    if "FINISHED" not in result:
        raise ConversionError("glTF export failed")


def write_next_steps(out_dir, report, opaque_name, cutout_name, groups):
    atlases = sorted(f for f in os.listdir(out_dir) if f.endswith("_atlas.png"))
    lines = [
        "VRoid -> Kassandra: what to do with these files",
        "=" * 48,
        "",
        f"Model:          {report['output']}",
        f"Replaces mesh:  {report['replaced_mesh']}",
        f"Scale applied:  x{report['scale_factor']:.4f} (VRoid now {report['result_height']:.3f} tall,"
        f" Kassandra {report['kassandra_height']:.3f})",
        "",
        "1. Back up the game's .forge file that holds Kassandra's mesh (the one you",
        "   exported the reference from). Keep the backup somewhere safe.",
        "2. In AnvilToolkit, open that same Kassandra mesh in the Mesh Viewer.",
        f"3. Drag {report['output']} onto the Mesh Viewer window.",
        "   Meshes with the same ID are replaced by the VRoid.",
        "4. Textures. Give each material below the matching atlas as its diffuse",
        "   (albedo) texture. Convert the PNG to DDS first if AnvilToolkit asks.",
    ]
    for atlas_file in atlases:
        kind = "cutout" if "_cutout_" in atlas_file else "opaque"
        mat = cutout_name if kind == "cutout" else opaque_name
        lines.append(f"     material {mat:<14} <- {atlas_file}")
    for extra in report["extra_material_slots"]:
        lines.append(f"     material {extra:<14} <- the opaque atlas (extra slot for the bone limit)")
    lines += [
        "5. Save in the Mesh Viewer, repack the forge (keep compression on) and put it",
        "   back in the game folder. Start the game and check Kassandra.",
        "",
        "If a body part bends at the wrong place, edit mapping.json (VRM bone ->",
        "Kassandra bone) and convert again with it. report.json lists how well",
        "each bone matched.",
    ]
    if report["warnings"]:
        lines += ["", "Warnings:"] + [f"- {w}" for w in report["warnings"]]
    with open(os.path.join(out_dir, "NEXT_STEPS.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# --------------------------------------------------------------------------- main

def convert(opts):
    os.makedirs(opts.out_dir, exist_ok=True)
    if not opts.output_name:
        opts.output_name = re.sub(r"[^A-Za-z0-9_-]+", "_", os.path.splitext(os.path.basename(opts.vrm))[0]) or "vroid"
    bpy.ops.wm.read_factory_settings(use_empty=True)
    tmpdir = tempfile.mkdtemp(prefix="vroid_kassandra_")
    warnings = []
    try:
        log(f"Importing Kassandra reference: {opts.reference}")
        ref = Reference(import_gltf(opts.reference), opts)
        log(f"  skeleton '{ref.arm.name}' with {len(ref.heads)} bones, height {ref.height:.4f}")
        log(f"Importing VRoid model: {opts.vrm}")
        model = VrmModel(opts.vrm, tmpdir)
        log(f"  VRM {model.version}, {len(model.present)} humanoid bones, {len(model.meshes)} meshes")

        vmin, vmax = model.mesh_bounds()
        v_height = float(vmax[2] - vmin[2])
        v_ground = float(vmin[2])
        joints = {hb: model.joint_world(hb) for hb in model.present}
        left_vec = np.array(joints["leftUpperArm"]) - np.array(joints["rightUpperArm"])
        v_yaw = matching.facing_rotation(left_vec)
        v_center = np.array([(vmin[0] + vmax[0]) / 2, (vmin[1] + vmax[1]) / 2, v_ground])
        src = matching.normalize(joints, v_center, v_height, v_yaw)

        overrides = {}
        if opts.mapping:
            with open(opts.mapping, "r", encoding="utf-8") as f:
                overrides = {k: v for k, v in json.load(f).get("bones", {}).items() if v in ref.heads}
        result = matching.match_best_yaw(src, model.present, ref.skeleton, ref.heads, ref.center,
                                         ref.height, overrides, opts.target_yaw)
        mapping = result["mapping"]
        log(f"  matched bones (facing {result['yaw']:.0f} deg, mean error "
            f"{100 * result['error'] / max(1, len(mapping)):.1f}% of height)")
        if result["low_confidence"]:
            warnings.append("These bones matched loosely; check mapping.json: " + ", ".join(result["low_confidence"]))

        # Uniform scale to Kassandra's height, turned to face her way, feet on her ground.
        scale = ref.height / v_height
        hips_v = Vector(joints["hips"])
        hips_t = Vector(ref.heads[mapping["hips"]])
        rot = Matrix.Rotation(math.radians(v_yaw - result["yaw"]), 4, "Z")
        m = (Matrix.Translation((hips_t.x, hips_t.y, ref.ground)) @ rot @ Matrix.Scale(scale, 4)
             @ Matrix.Translation((-hips_v.x, -hips_v.y, -v_ground)))
        bake_transform([model.arm] + model.meshes + model.extra_objects, m)
        log(f"  scaled VRoid by {scale:.4f} (VRoid height {v_height:.3f} -> {ref.height:.4f})")

        assign = assign_humanoid(model)
        moved = reparent_helpers(model, assign)
        if moved:
            log(f"  re-parented {moved} helper bone chains to the limb they belong to")
        posed, _ = fit_pose(model, mapping, ref)
        bake_pose_into_meshes(model)
        bone_targets = vrm_bone_targets(model, mapping, assign, opts.no_fingers)

        if opts.proportions == "skeleton":
            move_skeleton_to_vrm(ref, model, mapping, posed)
            warnings.append("Skeleton mode moved Kassandra's joints to the VRoid's joints. Test in game "
                            "carefully: other characters that share her skeleton may change too.")

        obj = join_meshes(model.meshes)
        for o in [model.arm] + model.extra_objects:
            remove_object(o)
        used_bones = transfer_weights(obj, bone_targets, mapping["hips"], opts.max_influences)
        keep_first_uv(obj.data)

        # Free the reference names so the new mesh takes Kassandra's mesh ID.
        replaced = []
        main_name = ref.main.name if ref.main else "Kassandra"
        main_mesh_name = ref.main.data.name if ref.main else main_name
        others = []
        for o in ref.meshes:
            mats = [s.material.name for s in o.material_slots if s.material]
            if o is not ref.main:
                others.append((o.name, o.data.name, mats[0] if mats else None))
            replaced.append(o.name)
            remove_object(o)
        for mat in list(bpy.data.materials):
            if mat.users == 0:
                bpy.data.materials.remove(mat)
        obj.name = main_name
        obj.data.name = main_mesh_name

        names = list(ref.material_names) or [f"{opts.output_name}_body"]
        opaque_name = opts.opaque_material or names[0]
        cutout_name = opts.cutout_material or (names[1] if len(names) > 1 else opaque_name)
        groups = build_materials(obj, model, opts, opts.out_dir, opaque_name, cutout_name)
        spare = [n for n in names if n not in (opaque_name, cutout_name)]
        triangulate(obj)
        added = split_bone_limit(obj, opts.max_bones, spare)
        if added:
            warnings.append(f"The VRoid uses more than {opts.max_bones} bones per material, so extra "
                            f"material slots were added: {', '.join(added)}. Point them at the same "
                            "atlas texture in AnvilToolkit, or rerun with --no-fingers.")
        bind(obj, ref.arm)

        export_objs = [ref.arm, obj]
        if opts.blank_other_meshes:
            hips = ref.arm.matrix_world @ ref.arm.data.bones[mapping["hips"]].head_local
            for name, mesh_name, mat in others:
                export_objs.append(make_placeholder(name, mesh_name, mat, ref.arm, mapping["hips"], hips))

        out_glb = os.path.join(opts.out_dir, f"{opts.output_name}_kassandra.glb")
        export_glb(out_glb, export_objs)
        log(f"Wrote {out_glb}")

        vmin2, vmax2 = bbox(world_verts(obj))
        report = {
            "output": os.path.basename(out_glb),
            "vrm": os.path.basename(opts.vrm),
            "vrm_version": model.version,
            "reference": os.path.basename(opts.reference),
            "proportions": opts.proportions,
            "scale_factor": scale,
            "vroid_height_original": v_height,
            "kassandra_height": ref.height,
            "result_height": float(vmax2[2] - vmin2[2]),
            "facing_yaw_deg": result["yaw"],
            "replaced_mesh": main_name,
            "removed_reference_meshes": replaced,
            "materials": {opaque_name: groups.get("opaque", []), cutout_name: groups.get("cutout", [])}
            if opaque_name != cutout_name else {opaque_name: groups.get("opaque", [])},
            "extra_material_slots": added,
            "bones_used": used_bones,
            "match_distance_percent": {h: round(100 * d, 2) for h, d in result["distances"].items()},
            "warnings": warnings,
        }
        with open(os.path.join(opts.out_dir, "report.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        with open(os.path.join(opts.out_dir, "mapping.json"), "w", encoding="utf-8") as f:
            json.dump({"_help": "VRM humanoid bone -> Kassandra bone. Edit and pass with --mapping "
                                "to fix a wrong match, then convert again.",
                       "bones": mapping}, f, indent=2)
        write_next_steps(opts.out_dir, report, opaque_name, cutout_name, groups)
        for w in warnings:
            log("WARNING: " + w)
        return report
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
