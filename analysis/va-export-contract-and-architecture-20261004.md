# Nouveau VA PRIME export contract and repair location

Status: source-level and standalone-probe preparation only. The probe has been
compiled, not run. No claim is made that the current Mesa rejection violates
the VA contract.

## Advertised capability versus one private surface

In the pinned Mesa 26.0.8 tree, `vlVaQuerySurfaceAttributes()` advertises
Linux `VA_SURFACE_ATTRIB_MEM_TYPE_DRM_PRIME_2` in the configuration's
`VASurfaceAttribMemoryType` mask and advertises supported pixel formats
including NV12. These records are configuration-level attributes. The source
does not encode a promise that every internal representation for a given
fourcc, including Nouveau VP3's two-field-array representation, can be exported
with every requested layer layout.

The generic `vlVaExportSurfaceHandle()` obtains the `pipe_video_buffer` and
returns `VA_STATUS_ERROR_INVALID_SURFACE` when `buffer->interlaced` is true,
before calling `pipe_screen::resource_get_handle()`. This prevents the
four-field VP3 representation from being mislabeled as the two-layer
DRM-PRIME-2 NV12 layout. The pinned VP3 buffer has a Nouveau-specific
`create_video_buffer()` implementation, sets `interlaced=true`, and exposes
two array layers for Y and two for UV through generic `pipe_video_buffer`
callbacks.

The standalone probe in
[`../validation/va-export-contract-diagnostic/README.md`](../validation/va-export-contract-diagnostic/README.md)
creates the same H.264 High/VLD config as the pinned FFmpeg decoder: both call
`vaCreateConfig()` with a null attribute list and count zero. Its regression
hashes the FFmpeg source and checks that call plus the PRIME-2 export flags.
The probe uses the byte-identical input recorded by the historical ffplay GDB
capture. That capture reached a Nouveau VP3 field-array surface with
`buffer->interlaced=1`, although the encoded stream is progressive; the AVFrame
field flag therefore does not describe Gallium's private storage layout. The
revised probe pins and verifies the current distro Nouveau VA DSO before VA
initialization and in `/proc/self/maps`, then queries the config and
synchronizes/exports the first actual decoded surface with FFmpeg's flags. It
is a precise runtime diagnostic for the observed input/surface class, but has
not been executed. Its result will record the advertised config attributes and
export result; the attributes alone do not promise support for every private
surface layout.

The previous ffplay capture recorded input SHA-256
`d1bab5275bcb585791fbfb15c801c1aab582256e7b7fca280c76f78a0a1c1ec2`, matching
the probe input exactly. The new probe targets the current distro DSO
`/usr/lib/x86_64-linux-gnu/libgallium-26.0.8-1ubuntu0.3.so` (SHA-256
`64c5508bb7167947f932c950d5e2d2c314f631fc3919de82f757c3769758b79f`). This
differs from the historical private DSO; the two driver binaries are not
conflated. The probe sets the Nouveau VA driver search path, checks the
`nouveau_drv_video.so` symlink target, and refuses to continue unless the
expected DSO appears in its own process map.

## Existing Gallium hook review

The reviewed `pipe_screen` exposes generic `resource_get_handle()` for a
resource. The reviewed `pipe_video_buffer` has resource/sampler/surface access
callbacks, and Nouveau VP3 supplies those callbacks for its field-array
resources. The reviewed `PIPE_VIDEO_CAP_*` enum has general video/VPP
capabilities but no capability for exporting an interlaced surface as
progressive NV12, no generic video-surface export callback, and no
driver-supplied normalized export descriptor hook.

The generic VA frontend is currently the layer that converts a `VASurfaceID`
to the standard PRIME-2 descriptor, so a standards-compatible normalization
could live there. However, a fallback should not silently alter every driver's
interlaced surface behavior. If the runtime probe confirms a contract gap, the
design review should first decide between:

- a Nouveau/Gallium video-buffer export/normalization hook that keeps the
  field-array knowledge in the driver; or
- a small explicit Gallium capability that lets the generic VA frontend use
  its compositor to normalize only surfaces whose driver declares that
  operation valid.

No existing hook expresses this behavior. A new capability or hook should be
designed only after the runtime probe confirms the contract gap and the
progressive-weave candidate proves its pixels and lifetime. Do not add a
driver-name or application-name check, and do not remove the interlaced guard
without providing a valid two-layer NV12 descriptor.

## Source pins

The source files used for this audit are:

| File | SHA-256 |
|---|---|
| Mesa VA `surface.c` | `f66d404ad556926a37caffa5d120a04a74b4a36af63e322ce578b6840e8ffcc0` |
| Nouveau `nouveau_vp3_video.c` | `c9b2158ac53faaa408d1a67dc3c61945d4e4da1a8f851b324eccc503180dd5fa` |
| Gallium `p_screen.h` | `0fdc1d82c68ab560f1c5c4ac0a901c98f358985d230f61d8e530adcca9c147d4` |
| Gallium `p_video_enums.h` | `25f5c968eae73bbd686f3c552a8ab26415a53d494af016f84c1f50b390a7235c` |
| VA `va.h` used by compile | `4d3771e3dd53733e3d40fe74109a83487ebe78f78274bc747b5afa8a9b190cc0` |
| VA `va_drmcommon.h` used by compile | `a71b5899f36f33bdccf540cc8663b335057ef5daa123e2b2af11f783d76dfc27` |
| FFmpeg installed `avcodec.h` | `0367ee7e67763630d632f193e3a6ef643d30049898a5b8ea82c30c85bf4ab291` |
| FFmpeg installed `hwcontext_vaapi.h` | `6f6c6a5250dd0f901cdc7de8b9b3db26102719b7e056cd17500009096bfd9b39` |
| FFmpeg source `libavformat/avformat.h` | `3abd044fbd0c6be3cb38b821c4e7f32f1da3f623cb0db6b552dd93374cbb4fe1` |
| FFmpeg source `libavcodec/vaapi_decode.c` | `dd9db7f515923d288935af0620498cea97054561bce67a7e797d3facc5764586` |
| FFmpeg source `libavutil/hwcontext_vaapi.c` | `84e9bd59a0cc88a542d3a3692c001251e6c7c833a01390d60244b47843eacd51` |

The FFmpeg binary identifies as `8.0.1-3ubuntu2`. The source audit remains
about the generic VA PRIME contract; it does not prove a runtime contract
violation, correct pixels, or visible ffplay playback.
