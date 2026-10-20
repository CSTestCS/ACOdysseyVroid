"""Match VRM humanoid joints to bones of an unknown game skeleton by position.

Game skeletons exported by AnvilToolkit may have bone names that are hashes,
so matching is done geometrically: both skeletons are normalised (feet on the
ground, centred, scaled to height 1, facing -Y), then each humanoid bone is
matched to the nearest target bone that is a descendant of its parent's match.
A one-step look-ahead (the bone's child must also have a close match below the
candidate) keeps helper/twist bones that sit on a joint from winning.

Pure numpy: no Blender required.
"""

import math

import numpy as np

from . import humanoid

YAWS = (0.0, 90.0, 180.0, 270.0)
SHARE_PENALTY = 0.02
DEPTH_PENALTY = 0.001
LOOKAHEAD_WEIGHT = 0.5
LEAF_PENALTY = 0.015  # a childless target bone (jaw, eye, weapon) rarely carries a body part
LOW_CONFIDENCE = 0.06  # 6% of body height
LOW_CONFIDENCE_LIMB = 0.12  # limb lengths differ between body types, so allow more


def rot_z(deg):
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def facing_rotation(left_vec):
    """Yaw (degrees) that turns a body whose left side points along ``left_vec``
    to face -Y (its left then points to +X)."""
    angle = math.degrees(math.atan2(left_vec[1], left_vec[0]))
    return -angle


def normalize(points, center, height, yaw_deg):
    """points: {name: xyz}. Returns {name: np.array} centred, scaled, rotated."""
    rot = rot_z(yaw_deg)
    c = np.asarray(center, dtype=float)
    return {k: rot @ ((np.asarray(v, dtype=float) - c) / height) for k, v in points.items()}


class TargetSkeleton:
    def __init__(self, heads, parents):
        """heads: {bone: xyz}; parents: {bone: parent_or_None}."""
        self.names = list(heads)
        self.parents = dict(parents)
        children = {n: [] for n in self.names}
        for n, p in self.parents.items():
            if p is not None and p in children:
                children[p].append(n)
        self.children = children
        self.depth = {}
        for n in self.names:
            d, p = 0, self.parents.get(n)
            while p is not None:
                d += 1
                p = self.parents.get(p)
            self.depth[n] = d
        self.desc = {}
        for n in self.names:
            out, stack = [], list(children[n])
            while stack:
                c = stack.pop()
                out.append(c)
                stack.extend(children[c])
            self.desc[n] = out


# Bones whose direction is aligned to the target while matching, so the
# source limb is turned into the target's pose (T-pose vs A-pose) before the
# next joint down the chain is matched.
ROTATE_AT = {"spine", "chest", "upperChest", "neck"} | {
    f"{s}{b}" for s in humanoid.SIDES for b in ("Shoulder", "UpperArm", "LowerArm", "UpperLeg", "LowerLeg")}
# Limbs differ in pose between models (T-pose vs A-pose), so their joints are
# scored by bone length; everything else by offset from the parent joint.
LENGTH_SCORED = {f"{s}{b}" for s in humanoid.SIDES for b in ("Shoulder", "UpperArm", "LowerArm", "UpperLeg", "LowerLeg")}
ANGLE_WEIGHT = 0.1


def _rotation_between(a, b):
    """3x3 matrix rotating direction a onto direction b."""
    a = a / np.linalg.norm(a)
    b = b / np.linalg.norm(b)
    v = np.cross(a, b)
    c = float(np.dot(a, b))
    if np.linalg.norm(v) < 1e-9:
        if c > 0:
            return np.eye(3)
        axis = np.cross(a, [1.0, 0, 0])
        if np.linalg.norm(axis) < 1e-6:
            axis = np.cross(a, [0, 1.0, 0])
        axis /= np.linalg.norm(axis)
        return 2 * np.outer(axis, axis) - np.eye(3)
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * (1.0 / (1.0 + c))


def _angle(a, b):
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return float(np.arccos(np.clip(np.dot(a, b) / (na * nb), -1.0, 1.0)))


def match(src, present, target, tgt_pos, overrides=None):
    """Match humanoid bones to target bones.

    src: {humanoid: np.array} normalised source joints
    present: set of humanoid bone names in the source
    target: TargetSkeleton
    tgt_pos: {bone: np.array} normalised target heads
    overrides: {humanoid: target_bone} forced matches

    Returns (mapping, distances, total_error). Distances are in units of
    body height.
    """
    overrides = overrides or {}
    parent_of, ordered = humanoid.build_hierarchy(present)
    kids = {}
    for h, p in parent_of.items():
        kids.setdefault(p, []).append(h)
    S = {h: np.array(v, dtype=float) for h, v in src.items() if h in parent_of}
    mapping, dists = {}, {}
    limits = {}
    used = set()
    total = 0.0
    names = target.names

    def subtree(h):
        out, stack = [], list(kids.get(h, []))
        while stack:
            x = stack.pop()
            out.append(x)
            stack.extend(kids.get(x, []))
        return out

    def chain_child(p, h):
        return p in ROTATE_AT and humanoid.direction_child(p, present) == h

    def length_scored(p, h):
        return p in LENGTH_SCORED and humanoid.direction_child(p, present) == h

    def offset_cost(h, p, c, pt):
        return float(np.linalg.norm((S[h] - S[p]) - (tgt_pos[c] - tgt_pos[pt])))

    def length_cost(h, p, c, pt):
        vs, vt = S[h] - S[p], tgt_pos[c] - tgt_pos[pt]
        return abs(np.linalg.norm(vs) - np.linalg.norm(vt)) + ANGLE_WEIGHT * _angle(vs, vt)

    for h in ordered:
        p = parent_of[h]
        hc = humanoid.direction_child(h, present)
        if h in overrides and overrides[h] in target.depth:
            best = overrides[h]
        else:
            if p is None:
                cands, base_depth, parent_t = names, 0, None
            else:
                parent_t = mapping[p]
                cands = [c for c in target.desc[parent_t] if c not in used] + [parent_t]
                base_depth = target.depth[parent_t]
            best, best_score = None, float("inf")
            for c in cands:
                if p is None:
                    d0 = float(np.linalg.norm(tgt_pos[c] - S[h]))
                    if d0 > 0.35:
                        continue
                elif length_scored(p, h):
                    d0 = length_cost(h, p, c, parent_t)
                else:
                    d0 = offset_cost(h, p, c, parent_t)
                if d0 > 0.35:
                    continue
                score = d0
                if hc is not None:
                    ls = np.linalg.norm(S[hc] - S[h])
                    look = min((abs(ls - np.linalg.norm(tgt_pos[x] - tgt_pos[c])) for x in target.desc[c]),
                               default=1.0)
                    score += LOOKAHEAD_WEIGHT * look
                if c == parent_t:
                    score += SHARE_PENALTY
                if not target.children[c] and not (h.endswith("Distal") or h.endswith("Toes")):
                    score += LEAF_PENALTY
                score += DEPTH_PENALTY * (target.depth[c] - base_depth)
                if score < best_score:
                    best, best_score = c, score
            if best is None:
                best = parent_t if parent_t is not None else names[0]
        mapping[h] = best
        if p is None or best != mapping[p]:
            used.add(best)
        # Distance after matching: position relative to the parent joint.
        if p is None:
            d = float(np.linalg.norm(tgt_pos[best] - S[h]))
        else:
            d = length_cost(h, p, best, mapping[p]) if length_scored(p, h) else offset_cost(h, p, best, mapping[p])
        if p is not None and chain_child(p, h):
            vs, vt = S[h] - S[p], tgt_pos[best] - tgt_pos[mapping[p]]
            # Turn the source limb so this bone points where the target's does.
            if np.linalg.norm(vs) > 1e-9 and np.linalg.norm(vt) > 1e-9:
                rot = _rotation_between(vs, vt)
                pivot = S[p].copy()
                for x in subtree(p):
                    S[x] = pivot + rot @ (S[x] - pivot)
        dists[h] = d
        limits[h] = LOW_CONFIDENCE_LIMB if p is not None and length_scored(p, h) else LOW_CONFIDENCE
        total += d
    match.last_limits = limits
    return mapping, dists, total


def match_best_yaw(src, present, target, tgt_heads, tgt_center, tgt_height, overrides=None,
                   forced_yaw=None):
    """Try the four axis-aligned facings of the target; keep the best match.

    Returns dict with mapping, distances, error, yaw (degrees that turn the
    target to face -Y).
    """
    best = None
    for yaw in ([forced_yaw] if forced_yaw is not None else YAWS):
        tpos = normalize(tgt_heads, tgt_center, tgt_height, yaw)
        mapping, dists, err = match(src, present, target, tpos, overrides)
        if best is None or err < best["error"]:
            best = {"mapping": mapping, "distances": dists, "error": err, "yaw": yaw,
                    "limits": dict(match.last_limits)}
    best["low_confidence"] = sorted(h for h, d in best["distances"].items() if d > best["limits"][h])
    return best
