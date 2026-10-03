# ffplay progressive-export candidate

**Status: compiled/linked candidate; not runtime-validated.** Patches 0001 and
0002 have each been applied and built as a target translation unit plus VA
plugin link. Patch 0002 changes the output-fence wait. Neither patch has been
installed or exercised on hardware. The saved ffplay capture proves that
VAAPI decode reaches `vaExportSurfaceHandle()` and Mesa rejects the interlaced
VP3 NV12 field-array surface before ordinary DRM PRIME export. The candidate
stages that surface into a progressive NV12 buffer using Gallium's existing
`VL_COMPOSITOR_WEAVE` path, then exports the staging buffer.

## Pinned source artifacts

| Artifact | SHA-256 |
|---|---|
| Baseline `surface.c` after the two retained functional Mesa fixes | `f66d404ad556926a37caffa5d120a04a74b4a36af63e322ce578b6840e8ffcc0` |
| Patch 0001: progressive staging | `e8e7611f591c237bf4c2d5c993b93625af1d5610a3277b6698c1c501d5858560` |
| Patch 0002: bounded fence polling | `6865f60a222043ddd2e31f5c8cfad84d4bd3c7dff588988a39eda03281d5d0e1` |
| `surface.c` after patch 0001 | `ca7e3b5fbd1f0c915d0efb6143ee620a883a17507d2a90442ef2b512b516c3da` |
| `surface.c` after patches 0001 + 0002 | `dc84e79326ee5e546f296279e65eb9dc468080833de875b82391fba09c26706c` |
| `surface.c.o` built from patch 0001 only | `8e14dd7aa5fdf016d5fd76e7746ae56f731534a81a0783fbd2faf4deb23b5a62` |
| VA plugin linked from patch 0001 only | `b9b749c67085045dd5fac3506774bb5789fbf94d4d88c45941539d0f3d256138` |
| `surface.c.o` built from patches 0001 + 0002 | `f8746d843411d62ca384d2942280215422fb0f4b47b8258a4f37c875bf83b7a1` |
| VA archive with patches 0001 + 0002 | `e5f060df8d4e7779e1264b4c94df2c559feaa42b54dc1cec3661ad0dbb4809a1` |
| VA plugin linked from patches 0001 + 0002 | `53f4ef5e5a87257fbc0253acdd4dcb3869d3a93d9be86ab682a62475eaabd176` |

The baseline source contains the native VA render-target clear and serialized
distinct-channel VP3 teardown fixes. Both candidate patches change only
`src/gallium/frontends/va/surface.c`. The patch sequence is listed above.

Run the source-contract checks against the pinned baseline `surface.c`:

```bash
: "${MESA_SURFACE_C_SOURCE:?set the pinned baseline surface.c path}"
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  validation/mesa-ffplay-progressive-export-candidate/tests/test_progressive_export_patch.py
```

The suite rejects absent or mismatched source/patch hashes, applies both
patches in order with zero fuzz in a temporary tree, and verifies each
intermediate/final source hash, the zero-timeout polling loop, and key
staging/cleanup ordering. These are source-contract tests, not a runtime
compositor or pixel test. The exact command and result are recorded in
[`evidence/BOUNDED-WAIT-PATCH-VALIDATION-20261003.md`](evidence/BOUNDED-WAIT-PATCH-VALIDATION-20261003.md).

## Build evidence

Patch 0001 strictly applies with GNU `patch --fuzz=0` and reproduces the
`ca7e3b5f...` candidate source hash; its compile/link artifacts are retained
as the comparison build. The final patch-0002 source also strictly applies and
reproduces `dc84e793...`. Its translation unit was compiled with the exact
compile command and warning options extracted from the existing configured
Mesa 26.0.8 Ninja build. The output object and depfile were redirected to a
new private directory. A copy of the patch-0001 VA archive was updated with
only the new `surface.c.o`; the configured plugin link command was then
replayed with that archive and its output redirected to the same isolated
directory. Compile and link both exited 0 with empty diagnostic logs.

The baseline, patch-0001, and patch-0002 VA archives have the same 11 members
in the same order. Only `surface.c.o` changes at each step. The patch-0002
plugin has the same dynamic dependencies and exported dynamic symbol set as
the patch-0001 plugin. Full command records, hashes, and baseline before/after
checks are in
[`evidence/BOUNDED-WAIT-PATCH-BUILD-20261003.md`](evidence/BOUNDED-WAIT-PATCH-BUILD-20261003.md).

This is a target translation-unit compile plus plugin relink, not a clean
full-Mesa rebuild. An earlier fresh Meson setup did not complete because the
host Python lacked Mako; no package was installed. The existing configured
build was read only to recover its commands, and the baseline files remained
unchanged. The scratch build artifacts are under:

```text
/home/keivan/nouveau-vaapi-app-validation/ffplay-progressive-export-candidate-20261002/
```

## Source behavior and required review

- The old interlaced rejection remains for Windows and protected surfaces.
- Non-protected interlaced NV12 uses `VL_COMPOSITOR_WEAVE` into a progressive
  NV12 video buffer; export then operates on that staging buffer.
- Export is gated on compositor support and shader initialization.
- Patch 0001's direct nonzero `fence_finish()` call has no deadline on Nouveau.
  Patch 0002 changes it to repeated zero-timeout polls against a monotonic
  deadline, sleeping for at most 1 ms between polls. The pinned Nouveau path
  treats timeout zero as a poll. This is a practical deadline loop, not a
  real-time guarantee. Patch 0002 is built and linked but not executed. See
  [`evidence/SOURCE-SYNC-LIFETIME-AUDIT-20261003.md`](evidence/SOURCE-SYNC-LIFETIME-AUDIT-20261003.md).
- Staging resources are destroyed on both success and failure paths after the
  export code has obtained its handles.

The pinned native Nouveau source supports decoder-write to compositor-read
ordering through implicit GEM reservation fences when both accesses reference
the same plane BOs with the audited read/write domains. This is source evidence,
not hardware evidence, and it does not prove the VP3 internal pipeline, output
fence completion on hardware, correct luma/chroma row parity, color preservation,
repeated surface reuse, or dma-buf FD lifetime. The compositor parameters currently
specify limited range and BT.709; their suitability for the pinned input and
other stream metadata still needs pixel comparison. A successful export alone
would not close these issues.

## CPU-only synchronization and lifetime source audit (2026-10-03)

The audit found a source-supported implicit GEM-fence dependency between native
VP3 writes and compositor reads of the same plane BOs. It also found that
Nouveau ignores the requested nonzero output-fence timeout. Patch 0002 adds a
deadline-based loop using zero-timeout polls; it has now compiled and linked
with the pinned build configuration, but it has not been executed. The caller
still holds `drv->mutex` during the loop, so a delayed fence can serialize VA
operations for approximately five seconds. This remains a production
latency/concurrency blocker. Exact source hashes and claim limits are recorded in
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
into the release branch. Review the mutex-held wait behavior, then obtain
exact-frame NV12 comparison against software, parity/color checks, repeated
surface/descriptor lifetime validation, and visible ffplay playback with
VAAPI decode confirmed on an approved clean desktop boot.

`main` remains HOLD.
