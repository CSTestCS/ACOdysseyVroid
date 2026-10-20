"""Command line for the converter.

    blender -b --factory-startup -P run_converter.py -- --vrm my.vrm --reference kassandra.glb --out outdir
"""

import argparse
import sys
import traceback


def parse(argv):
    p = argparse.ArgumentParser(prog="run_converter.py",
                                description="Rig a VRoid .vrm to Kassandra's skeleton for AC Odyssey.")
    p.add_argument("--vrm", required=True, help="VRoid model (.vrm, VRM 0.x or 1.0)")
    p.add_argument("--reference", required=True,
                   help="Kassandra's mesh + skeleton exported from AnvilToolkit's Mesh Viewer as .glb")
    p.add_argument("--out", required=True, help="output folder")
    p.add_argument("--name", dest="output_name", help="base name for output files")
    p.add_argument("--mapping", help="mapping.json from an earlier run, edited to fix bone matches")
    p.add_argument("--target-height", type=float,
                   help="Kassandra's height in the reference file's units (default: measured)")
    p.add_argument("--proportions", choices=("fit", "skeleton"), default="fit",
                   help="fit (default): keep Kassandra's skeleton, keep the VRoid's mesh shape. "
                        "skeleton (experimental): also move her joints to the VRoid's joints")
    p.add_argument("--atlas-size", type=int, default=4096, help="texture atlas size in pixels")
    p.add_argument("--max-bones", type=int, default=42, help="bones allowed per material (game limit)")
    p.add_argument("--max-influences", type=int, default=4, help="bone weights per vertex")
    p.add_argument("--no-fingers", action="store_true",
                   help="bind fingers to the hand (fewer bones; fingers will not bend)")
    p.add_argument("--replace-mesh", help="name of the reference mesh to replace (default: the largest)")
    p.add_argument("--opaque-material", help="Kassandra material ID for skin/clothes (default: largest)")
    p.add_argument("--cutout-material", help="Kassandra material ID for hair/lashes (default: 2nd largest)")
    p.add_argument("--blank-other-meshes", action="store_true",
                   help="also export the other reference meshes as invisible placeholders")
    p.add_argument("--target-yaw", type=float, choices=(0.0, 90.0, 180.0, 270.0),
                   help="force Kassandra's facing instead of detecting it")
    return p.parse_args(argv)


def main(argv=None):
    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    args = parse(argv)
    from . import pipeline
    opts = pipeline.Options(
        vrm=args.vrm, reference=args.reference, out_dir=args.out, mapping=args.mapping,
        target_height=args.target_height, proportions=args.proportions, atlas_size=args.atlas_size,
        max_bones=args.max_bones, max_influences=args.max_influences, replace_mesh=args.replace_mesh,
        opaque_material=args.opaque_material, cutout_material=args.cutout_material,
        blank_other_meshes=args.blank_other_meshes, target_yaw=args.target_yaw,
        no_fingers=args.no_fingers, output_name=args.output_name)
    try:
        pipeline.convert(opts)
    except (pipeline.ConversionError, Exception) as exc:  # report every failure plainly
        if not isinstance(exc, pipeline.ConversionError):
            traceback.print_exc()
        print(f"[vroid-kassandra] ERROR: {exc}", flush=True)
        return 1
    print("[vroid-kassandra] DONE", flush=True)
    return 0
