# FFplay staged-export coded and visible geometry audit

## Question and result

Does the progressive staging candidate turn the H.264 coded surface extent
into the visible frame extent, potentially exporting a 1080-line allocation
for a 1088-line VA surface or changing the frame's visible crop?

The pinned source paths keep those values separate. FFmpeg configures VA
frames from `AVCodecContext.coded_width/coded_height`; its VA pool calls
`vaCreateSurfaces()` with `AVHWFramesContext.width/height`. Mesa stores those
dimensions in the VA surface template. The candidate copies the decoded
`pipe_video_buffer` width/height into the staging buffer, copies the original
VA surface template into the staging wrapper, and exports descriptor
width/height from that template. FFmpeg's VAAPI-to-DRM map then copies the
source `AVFrame.width/height` to the mapped DRM frame after export. This source
chain preserves the coded storage extent in the exported descriptor and the
visible extent in the mapped frame.

For the retained 1920x1080 input, saved decode traces report 1920x1088 VA
surfaces. A descriptor height of 1088 therefore describes that allocated
surface. FFmpeg separately carries the visible AVFrame height through the
DRM mapping. The candidate does not rewrite the descriptor to 1080.

This resolves the static coded-height/visible-height question for this path.
It does not prove actual exported pitches/modifiers, bottom-padding contents,
field parity, chroma, pixels, or visible ffplay behavior. Runtime acceptance
remains outstanding.

## Pinned source inputs

| Source | Identity |
|---|---|
| Mesa candidate `src/gallium/frontends/va/surface.c` | SHA-256 `dc84e79326ee5e546f296279e65eb9dc468080833de875b82391fba09c26706c` |
| Mesa VA surface source before patches 0001+0002 | SHA-256 `f66d404ad556926a37caffa5d120a04a74b4a36af63e322ce578b6840e8ffcc0` |
| FFmpeg `n8.0.1/libavcodec/vaapi_decode.c` | SHA-256 `dd9db7f515923d288935af0620498cea97054561bce67a7e797d3facc5764586` |
| FFmpeg `n8.0.1/libavutil/hwcontext_vaapi.c` | SHA-256 `84e9bd59a0cc88a542d3a3692c001251e6c7c833a01390d60244b47843eacd51` |
| FFmpeg `n8.0.1/libavutil/hwcontext.c` | SHA-256 `0fe06b514578f52257d78ef749fe5927332590ec326f091631ef9b9e0cba4524` |
| Mesa `src/gallium/auxiliary/vl/vl_video_buffer.c` | SHA-256 `0147d36208b53f1f92220f910b26d2964c7d3d6f349ec63e14f587bf7491bb04` |

The FFmpeg files were read from the official tagged source URLs:

```text
https://raw.githubusercontent.com/FFmpeg/FFmpeg/n8.0.1/libavcodec/vaapi_decode.c
https://raw.githubusercontent.com/FFmpeg/FFmpeg/n8.0.1/libavutil/hwcontext_vaapi.c
https://raw.githubusercontent.com/FFmpeg/FFmpeg/n8.0.1/libavutil/hwcontext.c
```

## Source path

1. FFmpeg `vaapi_decode.c` assigns `frames->width/height` from
   `avctx->coded_width/coded_height` and creates the decode context with those
   coded dimensions.
2. FFmpeg `hwcontext_vaapi.c` allocates pool surfaces with
   `hwfc->width/height` and calls `vaExportSurfaceHandle()` for DRM PRIME.
3. Mesa VA surface creation stores the VA allocation width/height in
   `surf->templat`. The staging helper copies `src->width/height`; the
   staging wrapper retains the template width/height while clearing only its
   `interlaced` flag.
4. Mesa exports `VADRMPRIMESurfaceDescriptor.width/height` from
   `surf->templat.width/height`.
5. FFmpeg `vaapi_map_to_drm_esh()` copies `src->width/height` into the mapped
   DRM `AVFrame` after exporting the storage descriptor. Its dimensions remain
   those of the source frame.

## Regression

The patch-source test now asserts that the staging buffer follows the source
storage dimensions, the staging template preserves the original width/height,
and the exported descriptor uses that template extent. It does not assert that
storage height equals visible height.

Command:

```sh
MESA_SURFACE_C_SOURCE=/home/keivan/.cache/nouveau-vaapi-followup-20260928/private-functional-no-mesa-diag-20260930/source-functional/mesa-26.0.8/src/gallium/frontends/va/surface.c \
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  validation/mesa-ffplay-progressive-export-candidate/tests/test_progressive_export_patch.py
```

Result recorded for this checkpoint: **4/4 passed, zero skips**. The candidate
was not installed or loaded, and no VA/GPU workload, module operation,
package installation, or reboot occurred.
