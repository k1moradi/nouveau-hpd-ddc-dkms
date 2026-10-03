# Raw VA-API first-failure revalidation (2026-10-03)

This note records a CPU-only re-read of the saved libva traces, ffplay GDB
capture, and pinned Mesa 26.0.8 / Linux 7.0 source. No player, GPU workload,
module operation, or reboot was run.

## Capture identity and integrity

The MPV and FFmpeg trace captures share boot ID
`ca0a9d45-6ded-4a78-b4a1-6c4f57c2a775`, kernel `7.0.0-34-generic`, loaded
Nouveau srcversion `57AE1B168D50DB546CD87A1`, VA DSO SHA-256
`aceac163eabeefe5e4c1f2eb38469239044e30671aebcec743e4277f5897b536`, and
input SHA-256
`d1bab5275bcb585791fbfb15c801c1aab582256e7b7fca280c76f78a0a1c1ec2`.
The traces are per-thread files. Both MPV files pass that capture directory's
`sha256sum -c SHA256SUMS`; the FFmpeg trace hash was independently recomputed
as `677643a068a224698e85929165ecd263f0520dfc0c5199f4f767661fe2b55bfa`.

The MPV trace was a bounded `--frames=1` diagnostic using
`timeout --signal=INT --kill-after=2s 2s`; it ended with exit 137 after
repeated allocation errors, so it is not a normal playback result. The
FFmpeg X11 control ran VAAPI decode plus `hwdownload` for 10 frames and exited
0. The ffplay GDB capture is boot
`3ed57c67-baf7-443b-b4c8-06e1be85632a`, with the same kernel, loaded
srcversion, VA DSO hash, and input hash; its saved GDB log SHA-256 is
`a4ae1918c5e279b0afcd69e7109e134b45c78e1aa8e10772acad83855723acf6`.

The separate v5 constructor probe is a different run and boot, but its
manifest records the same input and VA DSO hashes. It is corroborating
constructor evidence, not a record from the exact libva trace event.

## MPV FIRST FAILURE

There is an earlier non-successful probe and a later decode-stopping failure.
The earliest non-success VA result is `vaDeriveImage()` on a temporary
128x128 surface, returning `VA_STATUS_ERROR_OPERATION_FAILED` at trace time
`33311.942058` on libva trace worker `thd-0x000222c9`. A temporary-surface
`vaExportSurfaceHandle()` probe then returns `VA_STATUS_ERROR_INVALID_SURFACE`
at `33311.942251` for temporary surface 3; later temporary export probes
succeed. These calls do not stop MPV's initial decode. The 1920x1088 decode
surface's `vaDeriveImage()` also returns `OPERATION_FAILED` (`33311.949128`),
as does FFmpeg's full-size surface probe (`33425.537289`); FFmpeg then
succeeds with `vaGetImage()`.

The first decode-stopping error is on worker trace
`thd-0x000222ba`:

```text
vaBeginPicture(context=4, render_target=surface 2)
    -> SUCCESS at 33312.014800
vaRenderPicture(context=4, num_buffers=2)
    -> VA_STATUS_ERROR_ALLOCATION_FAILED at 33312.032716
vaEndPicture(context=4)
    -> VA_STATUS_ERROR_INVALID_CONTEXT at 33312.032791
```

The failed batch contains:

| Order | Buffer | VA type | Size | Trace-visible H.264 state |
|---:|---:|---|---:|---|
| 0 | 5 | `VAPictureParameterBufferType` | 672 bytes | Current surface 2, frame index 0, flags `0x8`, empty reference list, 120x68 macroblocks, `num_ref_frames=5` |
| 1 | 6 | `VAIQMatrixBufferType` | 240 bytes | Default all-16 scaling matrices |

The matching H.264 picture block also prints top/bottom field order counts
65536, `seq_fields=32977`, `pic_fields=0x510`, `frame_num=0`, 8-bit luma and
chroma, and chroma format IDC 1. Its printed 120x68 macroblock grid derives
from width/height minus-one values 119 and 67.

`vaBufferInfo`, `vaMapBuffer`, and `vaUnmapBuffer` succeed for both buffers.
Mesa's `vlVaRenderPicture()` handles submitted buffers in order and stops on
the first failing handler (`src/gallium/frontends/va/picture.c`, around
lines 209-224). The first buffer is H.264 picture parameters. Its handler
parses the parameters and, when `context->decoder` is NULL, calls
`pipe->create_video_codec()`; a NULL decoder becomes
`VA_STATUS_ERROR_ALLOCATION_FAILED` (`src/gallium/frontends/va/decode.c`,
around lines 13-70). The IQ buffer is not the cause of this failed call. The
subsequent `vaEndPicture() -> INVALID_CONTEXT` is cleanup after decoder
creation failed.

MPV's preceding preflight picture on context 4/surface 2 completes picture
and IQ submission, slice-parameter and slice-data submission, and
`vaEndPicture()` successfully. It then destroys context 4 and its config,
creates a replacement context that reuses numeric ID 4, and begins another
picture on the existing surface 2. No `vaDestroySurfaces()` for surface 2
occurs before the failing submission. This is the smallest observed lifecycle
difference from the successful FFmpeg control, whose captured context 3 and
surface 1 remain in use through the successful frame-0 decode.

Both captured frame-0 paths use H.264 High/VLD, 1920x1088 geometry, NV12,
one surface in the corresponding `vaCreateSurfaces()` request, the same
numeric surface-attribute entries (`type=1, flags=2, value=0x3231564e` and
`type=6, flags=2, value=1`), and contexts with zero explicit render targets.
A binary-safe comparison of the printed H.264 picture-parameter
and IQ-matrix blocks finds them equal after normalizing only the dynamic
`CurrPic.picture_id`. The current frame is frame 0 with the same printed
sequence/picture values and empty reference list. Printed slice-parameter
fields also match between FFmpeg frame 0 and MPV's successful preflight;
replacement-context slice parameters are unavailable because failure occurs
before their submission. Both successful frame-0 submissions report
slice-data size 6160, but the trace does not contain slice-data bytes. The
trace therefore does not establish raw VA-structure or bitstream-byte
identity.

### Constructor failure reached from that VA path

The separate v5 GDB probe records constructor invocation 2 failing at GK104
BSP engine-object NEW, class `0x95b1`, with return `-EEXIST`. Channel creation
and push-buffer setup succeed before that point. The source path is:

```text
H.264 picture-parameter handler
  -> lazy create_video_codec()
  -> nvc0_create_decoder()
  -> BSP nouveau_object_new()
  -> DRM_NOUVEAU_NVIF NEW returns -EEXIST
  -> decoder creation returns NULL
  -> VA reports ALLOCATION_FAILED
```

Mesa 26.0.8's subchannel NEW uses the root DRM fd and a pointer-derived object
key; subchannel DEL instead passes `obj->parent->handle` as the fd and ignores
the return (`src/gallium/winsys/nouveau/drm/nouveau.c`, around lines 150-174
and 230-246). This supports a stale-key hypothesis but does not prove it.
The saved runtime records lack the old/new object key and DEL return. Linux
7.0 source has two possible `-EEXIST` sites: the earlier per-file ABI16 key
check (`drivers/gpu/drm/nouveau/nouveau_abi16.c`, around lines 118-135 and
761-800) and the later NVKM client-object insertion check
(`nvkm/core/ioctl.c`, around lines 132-148). The v5 probe did not identify
which site returned the error.

### Upstream Mesa source/history check

I checked Mesa GitLab `main` at commit
[`8fc4981d2d25a32732296a90bf9eaa0371f8c715`](https://gitlab.freedesktop.org/mesa/mesa/-/blob/8fc4981d2d25a32732296a90bf9eaa0371f8c715/src/gallium/winsys/nouveau/drm/nouveau.c).
The raw `src/gallium/winsys/nouveau/drm/nouveau.c` file at that commit has
SHA-256
`fefb35c2e3923a42381bef37fb4cd240688786e97263d5dca10feb62a36bb311`.
Its `nouveau_object_subchan_new()` passes `drm->fd` to
`drmCommandWrite()`, while `nouveau_object_subchan_del()` passes
`obj->parent->handle` and ignores the ioctl result, matching the Mesa 26.0.8
source under investigation.

The GitLab file-history query returned 17 commits. The only diff among those
that touched the subchannel NEW/DEL functions or the parent-handle DEL call was
the initial `nouveau: import libdrm_nouveau` commit
`821f4c8d99a3068758db834a5c219082a9609b3c` from 2024-03-13; it introduced the
same fd asymmetry. I found no later correction in the tracked file history up
to the queried `main` commit. This makes the candidate worth testing against
upstream, but does not prove that the DEL fd caused this runtime `-EEXIST` or
that changing it alone fixes playback.

The failed replacement picture parameter has no valid reference entries, so
the later VP3 `vidbuf == refs[j]` assertion is not reached on this first
failure path. The saved assertion belongs to a separate crash capture and
does not identify its offending VA surface or decoder generation. It cannot
explain this first decoder-construction failure from available evidence.

## FFPLAY FIRST FAILURE

The v4 GDB capture shows FFmpeg's `av_hwframe_map()` path calling
`vaExportSurfaceHandle()` for decoded surface 1, with memory type
`0x40000000` (DRM PRIME 2) and flags `4` (separate layers). Mesa's diagnostic
finds a valid surface and backing buffer with `interlaced=1`, then returns
status 6 at the explicit interlaced-surface guard in
`src/gallium/frontends/va/surface.c`, before `get_surfaces()` or
`resource_get_handle()`. The VP3 NV12 buffer constructor marks this form
interlaced and creates two-layer texture arrays
(`src/gallium/drivers/nouveau/nouveau_vp3_video.c`, around lines 101-133).
For this captured call, the error text `invalid VASurfaceID` does not mean
surface 1 was absent from the handle table: Mesa resolves it and its buffer,
then rejects the representation. The GDB capture has no libva monotonic
timestamp for the call; only the run's start/end wall-time bracket is saved.
FFmpeg's pinned `libavutil/hwcontext_vaapi.c` sets the separate-layer flag,
calls `vaExportSurfaceHandle()`, logs export failure, and returns `AVERROR(EIO)`
to the `av_hwframe_map()` caller when VA reports this error.

This is an export-representation failure after decode, distinct from MPV's
replacement-decoder construction failure. It does not prove the two have no
deeper shared lifetime cause.

### Upstream Mesa source comparison

At the same Mesa GitLab `main` commit recorded above, `vlVaExportSurfaceHandle()`
still returns `VA_STATUS_ERROR_INVALID_SURFACE` when
`surf->buffer->interlaced` is true, before retrieving surfaces or exporting a
resource (`src/gallium/frontends/va/surface.c`, lines 1188-1193 at that
revision). The raw file SHA-256 is
`6efb729716e91593a78af8e2c07e42091a41343a8fd9431afd3ba63c54dd2023`. The
current Nouveau VP3 buffer code still sets `interlaced = true` and allocates
`PIPE_TEXTURE_2D_ARRAY` storage; its file SHA-256 is
`d5cc2a6692f0db2a7218957c98d8ccf4e3792c70b8393d3e5cbc89ac46489a48`.
Therefore this upstream snapshot has not removed the captured export rejection
or supplied a progressive representation for these field-separated buffers.

Two later VA frontend commits in that upstream history are relevant to retained
surface/decoder lifetime, but not a demonstrated fix for either first failure:
[`dcf4b9426814c4aa698c3a54ff73579f252d8ad9`](https://gitlab.freedesktop.org/mesa/mesa/-/commit/dcf4b9426814c4aa698c3a54ff73579f252d8ad9)
adds `pipe_video_codec` refcounting, and
[`96c11b68e7f479ecbdc715ab0f28fa0113e09e28`](https://gitlab.freedesktop.org/mesa/mesa/-/commit/96c11b68e7f479ecbdc715ab0f28fa0113e09e28)
retains codec references in surfaces/buffers for fence wait/destruction. Their
diffs may change when an old decoder is destroyed while surfaces remain alive;
they do not change the NVIF subchannel DEL fd, and the current export function
still has the interlaced guard. Treat these changes as a separate later
lifecycle comparison, not as evidence that the wrong-fd or export candidates
are fixed.

## Evidence caveat and status

The MPV trace directory's historical `result-audit.txt` says
`libva_trace_exists=no`, while its checksummed per-thread trace files exist
and verify. I preserved that historical artifact; its boolean must not be
used to discard the thread files. The reason for that stale/ambiguous field
was not established.

```text
MPV FIRST FAILURE:
  replacement-context picture-parameter handling -> lazy decoder creation
  -> BSP NVIF NEW returns -EEXIST in a separate corroborating probe

FFPLAY FIRST FAILURE:
  decoded interlaced VP3 surface -> DRM PRIME 2 export -> interlaced guard

SHARED ROOT CAUSE:
  NO EVIDENCE; a deeper common lifetime cause is not ruled out

FIX STATUS:
  CANDIDATE: wrong-fd and progressive-staging changes; neither is accepted

VISIBLE HARDWARE PLAYBACK:
  FAIL

MAIN MERGE READINESS:
  HOLD
```
