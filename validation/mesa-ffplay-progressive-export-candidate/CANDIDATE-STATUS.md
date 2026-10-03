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
- The candidate requests a five-second `fence_finish()` timeout before
  exporting the staging resource. A CPU-only audit of the pinned Nouveau
  implementation found that nonzero timeout values are ignored and dispatched
  to a blocking fence wait, so this call does **not** establish a five-second
  upper bound. See
  [`evidence/SOURCE-SYNC-LIFETIME-AUDIT-20261003.md`](evidence/SOURCE-SYNC-LIFETIME-AUDIT-20261003.md).
- Staging resources are destroyed on both success and failure paths after the
  export code has obtained its handles.

The path has not established correct luma/chroma row parity, color preservation,
decoder-to-compositor synchronization, repeated surface reuse, or dma-buf FD
lifetime. The compositor parameters currently specify limited range and BT.709;
their suitability for the pinned input and other stream metadata needs pixel
comparison. A successful export alone would not close these issues.

## CPU-only synchronization and lifetime source audit (2026-10-03)

The additional source audit found that the requested five-second compositor
fence timeout is not honored by the pinned Nouveau `fence_finish()` path. It
also found no explicit decoder-fence dependency in the candidate's direct
compositor call. Driver-level resource ordering may still provide the needed
dependency; the source path reviewed here does not prove it. The caller holds
`drv->mutex` during the wait, preserving surface/compositor state but
serializing VA frontend operations for the duration. No lock change or
synchronization fix is included. Exact source hashes and limits are recorded in
[`evidence/SOURCE-SYNC-LIFETIME-AUDIT-20261003.md`](evidence/SOURCE-SYNC-LIFETIME-AUDIT-20261003.md).

## Additional CPU-only source and input audit (2026-10-03)

The input `/home/keivan/test_1080p.mkv` hash was rechecked as
`d1bab5275bcb585791fbfb15c801c1aab582256e7b7fca280c76f78a0a1c1ec2`.
The following command reports H.264 High, 1920x1080, progressive,
limited-range (`tv`) BT.709 primaries/transfer/matrix:

```sh
ffprobe -v error -select_streams v:0 \
  -show_entries stream=codec_name,profile,width,height,pix_fmt,field_order,color_range,color_space,color_transfer,color_primaries \
  -of json /home/keivan/test_1080p.mkv
```

That matches the candidate's selected limited-range BT.709 color parameters
for this input; it says nothing about untagged or differently tagged media.

The Mesa 26.0.8 source path was followed for the candidate's NV12-to-NV12
conversion. `vlVaPostProcCompositor()` selects `vl_compositor_yuv_deint_full()`
when both buffers are YUV. That path renders the Y and UV planes with the YUV
weave shaders. Those shaders sample array layer 0 for one field and layer 1
for the other, then select field samples by output-row position. This confirms
the candidate uses Mesa's YUV weave path rather than its RGB-output weave
path. It does not prove that the VP3 array-layer ordering and row/chroma
sampling match this decoded surface; parity and pixel comparison remain open.

Source blobs from the candidate's Mesa 26.0.8 tree:

| File | SHA-256 |
|---|---|
| `src/gallium/frontends/va/postproc.c` | `6ca76b96dbaabb457d91b4ed1f5faabc0d260adf1bde202373c6f6d8374e4787` |
| `src/gallium/auxiliary/vl/vl_compositor.c` | `22169b77a760e86e05f32f40975a4ecc505bf29f055489a051700e4dd4c1ff9e` |
| `src/gallium/auxiliary/vl/vl_compositor_cs.c` | `98b176f97e15385bd602f7aa90cd3aaa0829b597db22f866482e5703f2b8c394` |

## Runtime boundary

The plugin was not installed or loaded, and the candidate was not run against
ffplay. No GPU workload, module operation, package install, or reboot was
performed for this checkpoint. Do not call this a functional fix or merge it
into the release branch. Required next evidence is exact-frame NV12 comparison
against software output, parity/color checks, explicit synchronization and
descriptor-lifetime validation, then visible ffplay playback with VAAPI decode
confirmed.

`main` remains HOLD.
