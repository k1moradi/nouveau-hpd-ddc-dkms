# MPV and ffplay first-failure comparison

This is a CPU-only evidence summary of the saved VA traces, ffplay debugger
capture, and Mesa 26.0.8 source audit. It does not claim a functional fix or
visible-playback pass. The full working analysis is retained at
`/home/keivan/nouveau-vaapi-app-validation/analysis/first-failure-source-comparison.md`.

## Evidence identity

All three captures use the known input SHA-256
`d1bab5275bcb585791fbfb15c801c1aab582256e7b7fca280c76f78a0a1c1ec2`. The MPV
and FFmpeg trace runs use the same Mesa VA DSO SHA-256
`aceac163eabeefe5e4c1f2eb38469239044e30671aebcec743e4277f5897b536`.

| Capture | File | SHA-256 |
|---|---|---|
| MPV libva worker trace | `mpv-libva-trace-opengl-20260930T214151Z/libva.trace.214151.thd-0x000222ba` | `91068c15fe5f1a7b80cb23a0cceb1befd664248dc656b5ba9d963ecf5a86ba54` |
| FFmpeg frame-0 libva worker trace | `ffmpeg-libva-trace-x11-20260930T214345Z/libva.trace.214345.thd-0x00022445` | `677643a068a224698e85929165ecd263f0520dfc0c5199f4f767661fe2b55bfa` |
| ffplay export debugger capture | `ffplay-interlaced-rejection-probe-v4-20261001T082058Z/gdb-ffplay.log` | `a4ae1918c5e279b0afcd69e7109e134b45c78e1aa8e10772acad83855723acf6` |

The trace files contain NUL bytes; they were rechecked as binary-safe libva
trace data. Hashes above were independently recomputed from the saved files.

## MPV first failure

The earliest non-successful MPV VA result is a `vaDeriveImage()` probe returning
`VA_STATUS_ERROR_OPERATION_FAILED`. That is not the decode-stopping divergence:
the successful FFmpeg `hwdownload` control also receives that result and
continues through `vaGetImage()`.

MPV's preflight picture decodes successfully. It then destroys and recreates
the decoder context while retaining its VA surface pool. On the replacement
context, `vaBeginPicture(context=4, surface=2)` succeeds. The first fatal call
is:

```text
vaRenderPicture(context=4, buffers=[5, 6])
  buffer 5: VAPictureParameterBufferType, 672 bytes
  buffer 6: VAIQMatrixBufferType, 240 bytes
  result: VA_STATUS_ERROR_ALLOCATION_FAILED
```

The following `vaEndPicture() -> VA_STATUS_ERROR_INVALID_CONTEXT` is cleanup
fallout. Buffer-info, map, and unmap operations succeed before the failing
submission.

Mesa 26.0.8's `vlVaRenderPicture()` processes those buffers in caller order.
The picture-parameter handler lazily calls `create_video_codec()` when the
replacement context has no decoder; a NULL decoder is translated to
`VA_STATUS_ERROR_ALLOCATION_FAILED`. A valid v5 constructor-stage capture
localizes the failure further: BSP NVIF object NEW, class `0x95b1`, returns
`-EEXIST` after channel creation and push-buffer setup succeed.

The successful FFmpeg frame-0 control has the same printed picture-parameter
and IQ-matrix fields as MPV's successful preflight and failed replacement
frame. Its printed slice-parameter fields also match MPV's successful
preflight; replacement slice parameters are unavailable because failure occurs
earlier. Slice-data contents are not printed, so byte-for-byte input equality
is not established. FFmpeg keeps its context through the captured successful
decode; MPV's decoder/context reconstruction is the smallest established
lifecycle difference, not proof that retaining surfaces is defective.

Mesa source reveals a candidate invariant failure: subchannel NEW uses the
root DRM fd and a pointer-derived object key, while subchannel DEL passes the
parent channel handle as the `drmCommandWrite()` fd and ignores its return.
This can leave an ABI16 per-client key behind if the DEL is misdirected. The
saved v5 capture lacks the old/new keys, DEL return, and exact duplicate layer,
so that explanation remains **source-supported but not runtime-proven**. The
matched A/B logger and fail-closed correlator are in this directory; they have
not been run on hardware.

## ffplay first failure

The debugger capture shows FFmpeg's VAAPI-to-DRM mapping path reaches
`vaExportSurfaceHandle()` with DRM PRIME 2 separate-layer export for decoded
surface 1. Mesa observes an NV12 surface with `interlaced=1` and returns
`VA_STATUS_ERROR_INVALID_SURFACE` (status 6) at the explicit interlaced-surface
guard, before `get_surfaces()` and normal resource export. Nouveau VP3 stores
the two fields in array-layer resources and marks that video buffer
interlaced. Removing the guard alone would expose the wrong representation as
progressive NV12.

A progressive staging-buffer patch exists and compiles/links in an isolated
candidate build. It has no field-parity, pixel, synchronization, repeated-use,
or dma-buf-lifetime validation; it is not an accepted fix.

## Comparison and status

```text
MPV FIRST FAILURE:
  replacement-context picture-parameter submission -> lazy decoder creation
  -> BSP object NEW returns -EEXIST

FFPLAY FIRST FAILURE:
  post-decode DRM PRIME export rejects the interlaced VP3 surface

SHARED ROOT CAUSE:
  NO EVIDENCE; a deeper common lifetime cause is not ruled out

FIX STATUS:
  MPV correct-DEL-fd change and ffplay progressive staging are candidates;
  neither is a proven production fix

VISIBLE HARDWARE PLAYBACK:
  FAIL

MAIN MERGE READINESS:
  HOLD
```

BAR2/HOST_CPU/PTE remains a separate unresolved track; these captures do not
establish it as the cause of either first failure.
