"""VRM humanoid bone hierarchy (VRM 0.x and 1.0 names)."""

SIDES = ("left", "right")
FINGERS = ("Index", "Middle", "Ring", "Little")

# Each bone lists its possible parents, best first. The first one present in
# the model is used, so optional bones (upperChest, shoulders, toes, ...) can
# be missing.
_PARENTS = {
    "hips": [],
    "spine": ["hips"],
    "chest": ["spine"],
    "upperChest": ["chest", "spine"],
    "neck": ["upperChest", "chest", "spine"],
    "head": ["neck"],
}
for _s in SIDES:
    _PARENTS.update({
        f"{_s}Shoulder": ["upperChest", "chest", "spine"],
        f"{_s}UpperArm": [f"{_s}Shoulder", "upperChest", "chest", "spine"],
        f"{_s}LowerArm": [f"{_s}UpperArm"],
        f"{_s}Hand": [f"{_s}LowerArm"],
        f"{_s}UpperLeg": ["hips"],
        f"{_s}LowerLeg": [f"{_s}UpperLeg"],
        f"{_s}Foot": [f"{_s}LowerLeg"],
        f"{_s}Toes": [f"{_s}Foot"],
    })
    # Thumb: VRM 1.0 = Metacarpal/Proximal/Distal, VRM 0.x = Proximal/Intermediate/Distal.
    thumb = [f"{_s}Thumb{p}" for p in ("Metacarpal", "Proximal", "Intermediate", "Distal")]
    for i, name in enumerate(thumb):
        _PARENTS[name] = list(reversed(thumb[:i])) + [f"{_s}Hand"]
    for finger in FINGERS:
        chain = [f"{_s}{finger}{p}" for p in ("Proximal", "Intermediate", "Distal")]
        for i, name in enumerate(chain):
            _PARENTS[name] = list(reversed(chain[:i])) + [f"{_s}Hand"]

# Bones folded into their parent instead of being matched on their own.
FOLD_INTO_PARENT = {"leftEye", "rightEye", "jaw"}

# Preferred child used to measure a bone's direction, best first.
_DIRECTION_CHILD = {
    "hips": ["spine"],
    "spine": ["chest", "upperChest", "neck"],
    "chest": ["upperChest", "neck"],
    "upperChest": ["neck"],
    "neck": ["head"],
}
for _s in SIDES:
    _DIRECTION_CHILD.update({
        f"{_s}Shoulder": [f"{_s}UpperArm"],
        f"{_s}UpperArm": [f"{_s}LowerArm"],
        f"{_s}LowerArm": [f"{_s}Hand"],
        f"{_s}Hand": [f"{_s}MiddleProximal", f"{_s}RingProximal", f"{_s}IndexProximal"],
        f"{_s}UpperLeg": [f"{_s}LowerLeg"],
        f"{_s}LowerLeg": [f"{_s}Foot"],
        f"{_s}Foot": [f"{_s}Toes"],
    })
    thumb = [f"{_s}Thumb{p}" for p in ("Metacarpal", "Proximal", "Intermediate", "Distal")]
    for i, name in enumerate(thumb[:-1]):
        _DIRECTION_CHILD[name] = thumb[i + 1:]
    for finger in FINGERS:
        chain = [f"{_s}{finger}{p}" for p in ("Proximal", "Intermediate", "Distal")]
        for i, name in enumerate(chain[:-1]):
            _DIRECTION_CHILD[name] = chain[i + 1:]

KNOWN_BONES = set(_PARENTS) | FOLD_INTO_PARENT


def build_hierarchy(present):
    """Return (parent_of, ordered) for the humanoid bones in ``present``.

    ``ordered`` lists bones so every parent comes before its children.
    Unknown or folded bones are skipped.
    """
    parent_of = {}
    # Canonical order keeps results deterministic and visits the spine chain
    # (neck) before side branches (shoulders).
    for name in [n for n in _PARENTS if n in present]:
        parent = None
        for cand in _PARENTS[name]:
            if cand in present:
                parent = cand
                break
        if name != "hips" and parent is None:
            continue
        parent_of[name] = parent
    ordered = []
    remaining = dict(parent_of)
    while remaining:
        progressed = False
        for name, parent in list(remaining.items()):
            if parent is None or parent in ordered:
                ordered.append(name)
                del remaining[name]
                progressed = True
        if not progressed:  # pragma: no cover - would mean a cycle in the table above
            raise RuntimeError(f"cannot order humanoid bones: {sorted(remaining)}")
    return parent_of, ordered


def direction_child(name, present):
    for cand in _DIRECTION_CHILD.get(name, []):
        if cand in present:
            return cand
    return None


def hand_side_markers(side, present):
    """Two finger roots spanning the palm (index side, little side), or None."""
    a = next((f"{side}{f}Proximal" for f in ("Index", "Middle") if f"{side}{f}Proximal" in present), None)
    b = next((f"{side}{f}Proximal" for f in ("Little", "Ring") if f"{side}{f}Proximal" in present), None)
    if a and b:
        return a, b
    return None


def is_finger(name):
    return any(k in name for k in ("Thumb", "Index", "Middle", "Ring", "Little"))
