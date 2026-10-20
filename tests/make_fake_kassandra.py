"""Build a stand-in for Kassandra's AnvilToolkit export, for tests.

The real game files cannot be shipped, so this makes a GLB with a game-style
skeleton: A-pose, 1.75 m, twist bones sitting on joints, face bones, a weapon
bone, a ground 'Reference' root, four spine bones and two materials.

Run inside Blender's Python (``python -c`` with the bpy module also works):
    make(path, hashed=False, yaw=0)
"""

import hashlib
import math

import bpy  # noqa: I001 - bpy must load before bmesh
import bmesh
from mathutils import Matrix, Vector

C45 = math.cos(math.radians(45))


def _skeleton():
    b = {}  # name: (parent, head)
    b["Reference"] = (None, (0, 0, 0))
    b["Hips"] = ("Reference", (0, 0, 0.98))
    b["Spine"] = ("Hips", (0, 0, 1.05))
    b["Spine1"] = ("Spine", (0, 0, 1.17))
    b["Spine2"] = ("Spine1", (0, 0, 1.29))
    b["Spine3"] = ("Spine2", (0, 0, 1.38))
    b["Neck"] = ("Spine3", (0, 0, 1.50))
    b["Head"] = ("Neck", (0, 0, 1.58))
    b["HeadEnd"] = ("Head", (0, 0, 1.75))
    b["Jaw"] = ("Head", (0, -0.03, 1.60))
    for side, sx in (("L", 1), ("R", -1)):
        b[f"{side}_Eye"] = ("Head", (0.03 * sx, -0.08, 1.66))
        b[f"{side}_Shoulder"] = ("Spine3", (0.03 * sx, 0, 1.44))
        arm = Vector((0.17 * sx, 0, 1.43))
        down = Vector((C45 * sx, 0, -C45))
        fore = arm + down * 0.28
        hand = fore + down * 0.25
        b[f"{side}_Arm"] = (f"{side}_Shoulder", tuple(arm))
        b[f"{side}_ArmRoll"] = (f"{side}_Arm", tuple(arm))
        b[f"{side}_ForeArm"] = (f"{side}_Arm", tuple(fore))
        b[f"{side}_ForeArmRoll"] = (f"{side}_ForeArm", tuple((fore + hand) / 2))
        b[f"{side}_Hand"] = (f"{side}_ForeArm", tuple(hand))
        b[f"{side}_Weapon"] = (f"{side}_Hand", tuple(hand + Vector((0, -0.05, -0.05))))
        for fname, spread in (("Index", -0.03), ("Middle", -0.01), ("Ring", 0.01), ("Pinky", 0.03)):
            root = hand + down * 0.08 + Vector((0, spread, 0))
            parent = f"{side}_Hand"
            for i in range(3):
                n = f"{side}_{fname}{i + 1}"
                b[n] = (parent, tuple(root + down * 0.03 * i))
                parent = n
        root = hand + Vector((0.01 * sx, -0.03, -0.02))
        parent = f"{side}_Hand"
        for i in range(3):
            n = f"{side}_Thumb{i + 1}"
            b[n] = (parent, tuple(root + Vector((0.015 * sx, -0.02, -0.015)) * i))
            parent = n
        b[f"{side}_UpLeg"] = ("Hips", (0.09 * sx, 0, 0.95))
        b[f"{side}_UpLegRoll"] = (f"{side}_UpLeg", (0.09 * sx, 0, 0.95))
        b[f"{side}_Leg"] = (f"{side}_UpLeg", (0.09 * sx, 0, 0.52))
        b[f"{side}_Foot"] = (f"{side}_Leg", (0.09 * sx, 0.03, 0.09))
        b[f"{side}_Toe"] = (f"{side}_Foot", (0.09 * sx, -0.10, 0.02))
        b[f"{side}_ToeEnd"] = (f"{side}_Toe", (0.09 * sx, -0.16, 0.02))
    return b


EXPECTED = {
    "hips": "Hips", "head": "Head", "neck": "Neck",
    "leftUpperArm": "L_Arm", "leftLowerArm": "L_ForeArm", "leftHand": "L_Hand",
    "rightUpperArm": "R_Arm", "rightLowerArm": "R_ForeArm", "rightHand": "R_Hand",
    "leftUpperLeg": "L_UpLeg", "leftLowerLeg": "L_Leg", "leftFoot": "L_Foot", "leftToes": "L_Toe",
    "rightUpperLeg": "R_UpLeg", "rightLowerLeg": "R_Leg", "rightFoot": "R_Foot", "rightToes": "R_Toe",
    "leftIndexProximal": "L_Index1", "leftLittleProximal": "L_Pinky1",
    "rightMiddleIntermediate": "R_Middle2",
}


def hashed_name(name):
    return "0x" + hashlib.md5(name.encode()).hexdigest()[:8].upper()


def make(path, hashed=False, yaw=0.0, helmet=False):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    spec = _skeleton()
    rename = hashed_name if hashed else (lambda n: n)
    arm_data = bpy.data.armatures.new("KassandraSkel")
    arm = bpy.data.objects.new("KassandraSkel", arm_data)
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    rot = Matrix.Rotation(math.radians(yaw), 3, "Z")
    children = {}
    for n, (p, _) in spec.items():
        children.setdefault(p, []).append(n)
    for n, (_p, head) in spec.items():
        eb = arm_data.edit_bones.new(rename(n))
        h = Vector(head)
        kids = [k for k in children.get(n, []) if (Vector(spec[k][1]) - h).length > 1e-4]
        tail = Vector(spec[kids[0]][1]) if kids else h + Vector((0, 0, 0.03))
        eb.head = rot @ h
        eb.tail = rot @ tail
    for n, (p, _) in spec.items():
        if p:
            arm_data.edit_bones[rename(n)].parent = arm_data.edit_bones[rename(p)]
    bpy.ops.object.mode_set(mode="OBJECT")

    body = bpy.data.materials.new("1001")
    hair = bpy.data.materials.new("1002")
    mesh = bpy.data.meshes.new("Kassandra_Body")
    obj = bpy.data.objects.new("Kassandra_Body", mesh)
    bpy.context.scene.collection.objects.link(obj)
    mesh.materials.append(body)
    mesh.materials.append(hair)
    bm = bmesh.new()
    deform = bm.verts.layers.deform.verify()
    groups = {}

    def segment(bone, a, b, radius, mat=0):
        a, b = Vector(a), Vector(b)
        d = b - a
        if d.length < 1e-4:
            return
        res = bmesh.ops.create_cone(bm, cap_ends=True, segments=8, radius1=radius, radius2=radius,
                                    depth=d.length)
        q = Vector((0, 0, 1)).rotation_difference(d.normalized())
        m = Matrix.Translation(rot @ ((a + b) / 2)) @ rot.to_4x4() @ q.to_matrix().to_4x4()
        bmesh.ops.transform(bm, matrix=m, verts=res["verts"])
        gi = groups.setdefault(bone, len(groups))
        for v in res["verts"]:
            v[deform][gi] = 1.0
        for f in {f for v in res["verts"] for f in v.link_faces}:
            f.material_index = mat

    H = {n: spec[n][1] for n in spec}
    segment("Hips", H["Hips"], H["Spine1"], 0.15)
    segment("Spine2", H["Spine1"], H["Neck"], 0.16)
    segment("Neck", H["Neck"], H["Head"], 0.05)
    segment("Head", H["Head"], (0, 0, 1.75), 0.09)
    segment("Head", (0, 0.03, 1.62), (0, 0.06, 1.76), 0.10, mat=1)
    for s in ("L", "R"):
        segment(f"{s}_Arm", H[f"{s}_Arm"], H[f"{s}_ForeArm"], 0.045)
        segment(f"{s}_ForeArm", H[f"{s}_ForeArm"], H[f"{s}_Hand"], 0.035)
        segment(f"{s}_Hand", H[f"{s}_Hand"], H[f"{s}_Middle3"], 0.03)
        segment(f"{s}_UpLeg", H[f"{s}_UpLeg"], H[f"{s}_Leg"], 0.07)
        segment(f"{s}_Leg", H[f"{s}_Leg"], H[f"{s}_Foot"], 0.05)
        segment(f"{s}_Foot", H[f"{s}_Foot"], H[f"{s}_ToeEnd"], 0.035)
    bm.to_mesh(mesh)
    bm.free()
    for bone, _gi in sorted(groups.items(), key=lambda kv: kv[1]):
        obj.vertex_groups.new(name=rename(bone))
    obj.parent = arm
    mod = obj.modifiers.new("Armature", "ARMATURE")
    mod.object = arm

    export = [arm, obj]
    if helmet:
        hmesh = bpy.data.meshes.new("Kassandra_Helmet")
        hobj = bpy.data.objects.new("Kassandra_Helmet", hmesh)
        bpy.context.scene.collection.objects.link(hobj)
        hb = bmesh.new()
        bmesh.ops.create_cube(hb, size=0.25, matrix=Matrix.Translation(rot @ Vector((0, 0, 1.68))))
        hb.to_mesh(hmesh)
        hb.free()
        hmesh.materials.append(bpy.data.materials.new("1003"))
        hobj.vertex_groups.new(name=rename("Head")).add(list(range(len(hmesh.vertices))), 1.0, "REPLACE")
        hobj.parent = arm
        hobj.modifiers.new("Armature", "ARMATURE").object = arm
        export.append(hobj)
    bpy.context.view_layer.update()
    for o in export:
        o.select_set(True)
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True,
                              export_skins=True, export_animations=False)
    return {k: rename(v) for k, v in EXPECTED.items()}


if __name__ == "__main__":
    import sys
    make(sys.argv[-1])
