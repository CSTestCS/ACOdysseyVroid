# VRoid → Kassandra (Assassin's Creed Odyssey)

Replace Kassandra with your own VRoid Studio avatar (`.vrm`) in the Steam version of
Assassin's Creed Odyssey.

- **Keeps your avatar's proportions.** The model is scaled *uniformly* to Kassandra's
  height. Nothing is stretched, so legs, head and body keep their VRoid shape.
- **Rigged to Kassandra's skeleton**, so all of her animations drive your avatar.
- **One click.** Run `VroidKassandra.exe`, pick your `.vrm`, press **Convert**.

## What you need

| | Where to get it | Why |
|---|---|---|
| Assassin's Creed Odyssey (Steam) | Steam | the game |
| [Blender](https://www.blender.org/download/) 4.2 or newer | blender.org or Steam (free) | does the 3D work in the background; you never have to open it |
| [AnvilToolkit](https://www.nexusmods.com/assassinscreedodyssey/mods/266) | Nexus Mods (free) | reads and writes the game's `.forge` files |
| Your avatar as `.vrm` | VRoid Studio → Export → VRM (0.x or 1.0) | |

## How to install

1. Download `VroidKassandra-windows.zip` from this repository's **Releases** page (or the
   latest **Actions** build) and unzip it anywhere.
2. **Once only: export Kassandra as a reference.** In AnvilToolkit, unpack the forge that
   holds Kassandra's body mesh. Open that mesh with its skeleton in the Mesh Viewer, then
   **Save As → glTF (.glb)**. This file stays on your PC. Game files are never shipped
   with this mod.
3. Run **`VroidKassandra.exe`** (or drag your `.vrm` onto it). Pick:
   - your `.vrm`
   - the Kassandra `.glb` from step 2 (remembered next time)
   - Blender (found automatically in most cases)
4. Press **Convert**. After about a minute, the output folder opens. It contains:
   - `<name>_kassandra.glb`: your avatar, rigged to Kassandra's skeleton
   - `<name>_opaque_atlas.png` / `<name>_cutout_atlas.png`: its textures
   - `NEXT_STEPS.txt`: the final steps, filled in with your file and material names
5. Follow `NEXT_STEPS.txt`: drag the `.glb` onto AnvilToolkit's Mesh Viewer, assign the two
   textures, save, and repack the forge into the game folder.

> **Why the last step isn't automatic:** no tool can write Odyssey's mesh format without
> AnvilToolkit, which only works through its window and can't be redistributed. Kassandra's
> files are Ubisoft's, so they can't be shipped either. Once you've repacked the forge, you
> can make a Forger patch to share your avatar as a single drop-in file.

No `.exe`? Drop your `.vrm` onto **`Convert VRoid.bat`**. Kassandra's reference has to sit
next to it as `kassandra_reference.glb`.

## How it works

1. **Measure.** Both models are measured feet-to-top. The VRoid is scaled by
   `Kassandra height ÷ VRoid height` on all three axes, so its proportions don't change.
2. **Match bones.** Each VRM humanoid bone (hips, spine, arms, fingers, legs, toes…) is
   matched to Kassandra's bones by position and bone length. This also works when the
   exported bone names are just hash numbers. The tool also works out which way each
   model faces.
3. **Pose.** Each VRoid limb is rotated to point the way Kassandra's does (T-pose → A-pose).
   Only angles change, never lengths.
4. **Skin.** VRoid skin weights move onto the matched Kassandra bones. Hair, skirt and
   sleeve helper bones go to the body part they hang from.
5. **Game limits.** Textures are packed into one opaque and one cut-out (hair, lashes)
   atlas. Parts using more than the game's 42 bones per material are split, and the mesh
   is triangulated.
6. **Export.** The result is a GLB with Kassandra's exact skeleton and her mesh and
   material IDs, ready for AnvilToolkit's Mesh Viewer.

`report.json` shows the scale factor, heights and how well each bone matched.
`mapping.json` lists the bone matches. Edit it and pass it back (see below) if a joint
bends in the wrong place.

## Options

In the app (also available on the command line):

- **Bind fingers to the hand**: uses fewer bones, so there's no extra material slot, but
  the fingers won't bend.
- **Experimental: move Kassandra's joints to the VRoid's joints**: joints bend exactly where
  the VRoid's joints are. This edits Kassandra's skeleton, which other characters may
  share, so test carefully. The default keeps her skeleton unchanged.
- **Texture size**: 2048, 4096 (default) or 8192.

Command line (any OS with Blender):

```
blender -b --factory-startup -P converter/run_converter.py -- \
    --vrm avatar.vrm --reference kassandra.glb --out out/ \
    [--mapping out/mapping.json] [--no-fingers] [--proportions skeleton] \
    [--target-height H] [--replace-mesh NAME] [--opaque-material ID] [--cutout-material ID] \
    [--blank-other-meshes] [--atlas-size 4096]
```

## Limitations

- **Not yet tested inside the game.** The converter is tested end to end against a
  stand-in Kassandra skeleton (see Development), not the real game files.
  Please report problems from the AnvilToolkit import with `report.json` attached.
- **Face:** VRoid expressions (blink, mouth shapes) are blend shapes. Odyssey animates
  faces with bones, so the avatar's face stays neutral.
- **Hair and skirt physics:** VRM spring bones aren't simulated. Hair and skirts move with
  the head, body and legs.
- **Outfits:** the avatar replaces one mesh (the largest in the reference, or
  `--replace-mesh`). Other gear Kassandra wears still draws on top, so hide it in game
  (transmog/invisible gear) or export it with `--blank-other-meshes`.
- **Shading:** VRoid's toon (MToon) shading becomes the game's normal shading. The
  textures are kept.

## Development

```
pip install bpy pytest        # Blender as a Python module (bpy 5.x needs Python 3.13; bpy 4.2 needs 3.11)
pytest tests -q
```

The tests build a stand-in Kassandra skeleton: A-pose, twist bones on joints, face and
weapon bones, optionally hashed names and different facings. They convert a VRoid-made
sample VRM (both VRM 1.0 and VRM 0.x), then check that:

- every bone matches correctly
- the skeleton is unchanged
- the height matches
- each material uses at most 42 bones
- weights add up to 1
- bending Kassandra's elbow moves the avatar's hand

GitHub Actions runs them on Blender 4.2 and 5.2 and builds `VroidKassandra.exe` with
PyInstaller.

```
converter/run_converter.py        Blender entry point
converter/vroid_kassandra/        vrm.py (VRM reader), humanoid.py, matching.py (bone matching),
                                  pipeline.py (Blender steps), atlas.py (textures)
launcher/VroidKassandra.py        the Windows app; finders.py locates Steam, the game and Blender
tests/                            stand-in Kassandra generator and end-to-end tests
```
