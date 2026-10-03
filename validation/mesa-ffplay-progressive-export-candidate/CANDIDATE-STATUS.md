# ffplay progressive-export candidate

**Status: compile/link candidate only; not runtime-validated.** The saved
ffplay capture proves that VAAPI decode reaches `vaExportSurfaceHandle()` and
Mesa rejects the interlaced VP3 NV12 field-array surface before ordinary DRM
PRIME export. This candidate stages that surface into a progressive NV12
buffer using Gallium's existing `VL_COMPOSITOR_WEAVE` path, then exports the
staging buffer.

## Pinned source artifacts

| Artifact | SHA-256 |
|---|---|
| Baseline `surface.c` after the two retained functional Mesa fixes | `f66d404ad556926a37caffa5d120a04a74b4a36af63e322ce578b6840e8ffcc0` |
| Candidate patch | `e8e7611f591c237bf4c2d5c993b93625af1d5610a3277b6698c1c501d5858560` |
| Patched candidate `surface.c` | `ca7e3b5fbd1f0c915d0efb6143ee620a883a17507d2a90442ef2b512b516c3da` |
| Candidate generated `surface.c.o` | `8e14dd7aa5fdf016d5fd76e7746ae56f731534a81a0783fbd2faf4deb23b5a62` |
| Relinked `libgallium_drv_video.so` | `b9b749c67085045dd5fac3506774bb5789fbf94d4d88c45941539d0f3d256138` |

The baseline source contains the native VA render-target clear and serialized
distinct-channel VP3 teardown fixes. The candidate patch changes only
`src/gallium/frontends/va/surface.c`. The exact patch is
[`patches/0001-va-export-interlaced-surface-through-progressive-staging.patch`](patches/0001-va-export-interlaced-surface-through-progressive-staging.patch).

Run the source-contract checks against the pinned baseline `surface.c`:

```bash
: "${MESA_SURFACE_C_SOURCE:?set the pinned baseline surface.c path}"
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  validation/mesa-ffplay-progressive-export-candidate/tests/test_progressive_export_patch.py
```

The suite rejects an absent or mismatched source/patch hash, applies the patch
with zero fuzz in a temporary tree, and verifies the resulting candidate hash
and key staging/cleanup ordering. These are source-contract tests, not a
functional compositor or pixel test.

## Build evidence

The patch strictly applies with GNU `patch --fuzz=0` and reproduces the pinned
candidate source hash. The candidate translation unit compiled with the
existing configured Mesa build's command and warning options. The VA driver
plugin linked with that object replacing the baseline object and the remaining
objects from the configured build. A clean Meson setup did not complete because
the host Python lacks the required Mako module; no package was installed.

This was a target translation-unit compile plus plugin relink, not a clean
full-Mesa rebuild. The original scratch build and compile/link environment are
under:

```text
/home/keivan/nouveau-vaapi-app-validation/ffplay-progressive-export-candidate-20261002/
```

## Source behavior and required review

- The old interlaced rejection remains for Windows and protected surfaces.
- Non-protected interlaced NV12 uses `VL_COMPOSITOR_WEAVE` into a progressive
  NV12 video buffer; export then operates on that staging buffer.
- Export is gated on compositor support and shader initialization.
- A pipe flush fence is waited up to five seconds before exporting the staging
  resource.
- Staging resources are destroyed on both success and failure paths after the
  export code has obtained its handles.

The path has not established correct luma/chroma row parity, color preservation,
decoder-to-compositor synchronization, repeated surface reuse, or dma-buf FD
lifetime. The compositor parameters currently specify limited range and BT.709;
their suitability for the pinned input and other stream metadata needs pixel
comparison. A successful export alone would not close these issues.

## Runtime boundary

The plugin was not installed or loaded, and the candidate was not run against
ffplay. No GPU workload, module operation, package install, or reboot was
performed for this checkpoint. Do not call this a functional fix or merge it
into the release branch. Required next evidence is exact-frame NV12 comparison
against software output, parity/color checks, explicit synchronization and
descriptor-lifetime validation, then visible ffplay playback with VAAPI decode
confirmed.

`main` remains HOLD.
