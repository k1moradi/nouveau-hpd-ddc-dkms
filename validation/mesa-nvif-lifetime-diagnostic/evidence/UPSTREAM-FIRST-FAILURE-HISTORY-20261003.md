# Upstream source history for the saved first failures — 2026-10-03

This is a CPU-only source/history audit. It does not change functional source
or establish hardware causality. Upstream source was read from Mesa GitLab at
`main` commit `8fc4981d2d25a32732296a90bf9eaa0371f8c715` (2026-10-03).

## FFPLAY: interlaced export rejection follows the advertised capability

The current upstream NV12 surface-creation path sets
`templat.interlaced` to the inverse of
`PIPE_VIDEO_CAP_SUPPORTS_PROGRESSIVE` when no explicit modifier is supplied.
The Nouveau VP3 driver reports `PIPE_VIDEO_CAP_SUPPORTS_PROGRESSIVE` as
`false`. Its VP3 video-buffer constructor requires an interlaced template,
sets `buffer->base.interlaced = true`, and allocates field-separated
`PIPE_TEXTURE_2D_ARRAY` resources. The saved ffplay capture independently
observed `interlaced=1` for the surface it tried to export.

The current upstream `vlVaExportSurfaceHandle()` returns
`VA_STATUS_ERROR_INVALID_SURFACE` when `surf->buffer->interlaced` is true.
That guard is therefore consistent with Mesa's current rule that progressive
surfaces should be used when supported, but the Nouveau VP3 capability path
still supplies an interlaced buffer. For the saved ffplay request, the
interlaced flag itself reaches the explicit rejection before normal PRIME
resource export. This identifies the contract boundary; it does not prove
that a particular staging implementation weaves fields, chroma, color, and
resource lifetime correctly.

The relevant history explains how that boundary arose:

1. Mesa's original export implementation converted interlaced input to a
   progressive buffer using `vl_compositor_yuv_deint_full()` with
   `VL_COMPOSITOR_WEAVE` before exporting.
2. Commit `578e10e1` changed when interlaced storage was allocated.
3. Commit `fcfa68a6` reverted that allocation strategy, but retained an
   export-time progressive allocation and weave operation; it did **not**
   remove progressive conversion from export.
4. Commit `c324364f` changed surface allocation to use interlaced surfaces
   only when progressive is unsupported, removed the export-time weave
   conversion, and made export return `VA_STATUS_ERROR_INVALID_SURFACE` for an
   interlaced buffer. Nouveau's VP3 path reports progressive unsupported, so
   it remains on the interlaced side of that split.

The existing local progressive-export patch candidate restores an explicit
interlaced-to-progressive staging step at export. That is consistent with the
older Mesa export design, but remains a **candidate**. Its compile/link and
source-contract checks do not establish field parity, UV correctness,
synchronization, reuse safety, dma-buf lifetime, or visible ffplay playback.

## MPV: current upstream still contains both source candidates

At the same upstream `main` commit, Nouveau subchannel `NEW` calls
`drmCommandWrite()` with the root Nouveau DRM fd, while subchannel `DEL` calls
it with `obj->parent->handle`. The H.264 VP3 reference identity assertion
`dec->refs[refs[j]->valid_ref].vidbuf == refs[j]` also remains present.
These confirm the source patterns are still upstream. They do not establish
that either pattern caused the saved runtime failure.

Two upstream VA lifetime commits dated 2026-09-02 are relevant to future
comparison:

- `dcf4b942` adds reference counting for `pipe_video_codec`, including
  reference-held codec teardown and unlocking the VA driver mutex around
  fence waits.
- `96c11b68` keeps codec references on surfaces/buffers so fence wait and
  destruction use the codec associated with that resource.

The pinned local Mesa source tree at
`/home/keivan/.cache/nouveau-mesa-26.0.8-1ubuntu0.3/source/mesa-26.0.8`
does not contain those codec-reference changes: its VA context still directly
destroys `context->decoder` and uses the older context mutex path. The newer
commits can alter decoder/surface lifetime timing, so they are a possible
separate comparison after Variant A establishes whether the wrong-fd/stale-key
chain reproduces. They do not change the current upstream NVIF `NEW`/`DEL` fd
asymmetry, and they do not remove the VP3 identity assertion. Do not combine
them with the first wrong-fd A/B.

## Source identities and primary history

Upstream `main` snapshot hashes:

```text
src/gallium/frontends/va/surface.c
6efb729716e91593a78af8e2c07e42091a41343a8fd9431afd3ba63c54dd2023

src/gallium/winsys/nouveau/drm/nouveau.c
fefb35c2e3923a42381bef37fb4cd240688786e97263d5dca10feb62a36bb311

src/gallium/drivers/nouveau/nouveau_vp3_video.c
d5cc2a6692f0db2a7218957c98d8ccf4e3792c70b8393d3e5cbc89ac46489a48

src/gallium/drivers/nouveau/nouveau_vp3_video_vp.c
3c44d8153d08764aacf012cd46186819a120979c34f87adb0a3bee592d898c9f
```

The pinned local 26.0.8 `surface.c` SHA-256 is
`5b961abc23314c119ea0d1633ffca0b9ff77ac4b392d0de8a32fe128e9c1753b`; it has
the same progressive-capability assignment and interlaced-export rejection.

Primary upstream links:

- [Current Mesa main snapshot](https://gitlab.freedesktop.org/mesa/mesa/-/tree/8fc4981d2d25a32732296a90bf9eaa0371f8c715)
- [Original VA surface-export implementation, with progressive conversion](https://gitlab.freedesktop.org/mesa/mesa/-/commit/c4ed39f85b1ebd062eaa51880fcc79cfbcb4e5c3)
- [Interlaced surface allocation change](https://gitlab.freedesktop.org/mesa/mesa/-/commit/578e10e1571b40c86f3348f2f36e080f34d1a4ed)
- [Revert that allocation change while retaining export weave](https://gitlab.freedesktop.org/mesa/mesa/-/commit/fcfa68a632e5711cc657b103c9a0384928e9bf49)
- [Progressive-capability split and removal of export weave](https://gitlab.freedesktop.org/mesa/mesa/-/commit/c324364f3925db190a0c013c148f901f6633151f)
- [VA `pipe_video_codec` reference counting](https://gitlab.freedesktop.org/mesa/mesa/-/commit/dcf4b9426814c4aa698c3a54ff73579f252d8ad9)
- [Codec references retained on surfaces and buffers](https://gitlab.freedesktop.org/mesa/mesa/-/commit/96c11b68e7f479ecbdc715ab0f28fa0113e09e28)

## Conclusions

```text
MPV FIRST FAILURE:
The saved first failure remains the replacement-context PP-first
vaRenderPicture() allocation error. The wrong-fd/stale ABI16 key mechanism
remains source-supported, not runtime-proven. Newer VA codec-reference commits
are a separate lifetime-timing comparison, not a fix for that fd asymmetry.

FFPLAY FIRST FAILURE:
The saved PRIME export is rejected because the decoded Nouveau VP3 buffer is
interlaced and current Mesa refuses interlaced `vaExportSurfaceHandle()` input.
The capability and buffer source paths explain why the flag is set.

SHARED ROOT CAUSE:
NO EVIDENCE. The immediate boundaries differ; shared lower-level lifetime
effects remain possible but are not established.

FIX STATUS:
CANDIDATES only. No runtime correctness or accepted functional fix.

VISIBLE HARDWARE PLAYBACK:
FAIL until demonstrated otherwise.
```

No GPU workload, module change, initramfs update, or reboot was performed.
`main` remains HOLD.
