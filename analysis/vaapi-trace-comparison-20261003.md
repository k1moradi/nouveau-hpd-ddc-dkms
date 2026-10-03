# VA-API trace comparison: MPV, FFmpeg, and ffplay

This note condenses the CPU-only comparison of the retained libva traces and
the relevant Mesa source paths. It does not report a new playback test. No
player, VA/GPU workload, module change, install, or reboot was performed for
this analysis.

## Evidence identity

| Artifact | Identity |
|---|---|
| MPV decoder-thread libva trace | `mpv-libva-trace-opengl-20260930T214151Z/libva.trace.214151.thd-0x000222ba`, SHA-256 `91068c15fe5f1a7b80cb23a0cceb1befd664248dc656b5ba9d963ecf5a86ba54` |
| MPV renderer/interop libva trace | `mpv-libva-trace-opengl-20260930T214151Z/libva.trace.214151.thd-0x000222c9`, SHA-256 `4ea7dcc6272dcf1a09cb0b28be9ece78f0cdb8502c67b23a54db17f3d0756d59` |
| FFmpeg frame-0 libva trace | `ffmpeg-libva-trace-x11-20260930T214345Z/libva.trace.214345.thd-0x00022445`, SHA-256 `677643a068a224698e85929165ecd263f0520dfc0c5199f4f767661fe2b55bfa` |
| Mesa VA DSO used in both runs | SHA-256 `aceac163eabeefe5e4c1f2eb38469239044e30671aebcec743e4277f5897b536` |
| Input video | SHA-256 `d1bab5275bcb585791fbfb15c801c1aab582256e7b7fca280c76f78a0a1c1ec2` |
| Mesa source | 26.0.8; archive SHA-256 `caf1c0061a68e88dfa74967a7e780c0e85d65b6c4e334cd69095a5dc54ad78bc` |
| ffplay single-hit export probe | SHA-256 `a4ae1918c5e279b0afcd69e7109e134b45c78e1aa8e10772acad83855723acf6` |
| User-reported pixelation screenshot | SHA-256 `6a08474969bb974c8868fa2a3adda6676799af964b4362dd248509c173e5e270` |
| Software-only NV12 frames at PTS 530.021–530.188 | SHA-256 `7773decbceeb9f9d39a2538da54e797e9934e723f7ec734ed7d0a931ded0af09` |
| Software self-comparison JSON | SHA-256 `4088f73a0e2397edcc4cb5f370d46b02411d246d60d322480dd8708d92bd5d75` |

The trace paths and detailed source/control-flow analysis are recorded in
[`mpv-second-decoder-create.md`](mpv-second-decoder-create.md),
[`ffplay-vp3-prime-export.md`](ffplay-vp3-prime-export.md), and the
[independent raw-trace verification](../../nouveau-vaapi-app-validation/analysis/first-failure-source-verification-20261002.md).

## MPV versus FFmpeg

The first non-success status in the MPV renderer/interop trace is
`vaDeriveImage()` returning `VA_STATUS_ERROR_OPERATION_FAILED` on a temporary
128x128 probe surface at trace time `33311.942058`. This is not the fatal
decode divergence. FFmpeg's full-size decode surface also returns
`OPERATION_FAILED` from `vaDeriveImage()` and proceeds with `vaGetImage()`.

MPV's preflight frame 0 succeeds. MPV then destroys its VA context and
creates a replacement while retaining and reusing the surface pool. On the
replacement context, `vaBeginPicture(context=4, target=surface 2)` succeeds.
The picture-parameter and IQ buffers map and unmap successfully, but the first
replacement `vaRenderPicture()` batch returns
`VA_STATUS_ERROR_ALLOCATION_FAILED` at `33312.032716`. The batch contains
picture parameters (672 bytes) followed by the IQ matrix (240 bytes). The
subsequent `vaEndPicture() -> VA_STATUS_ERROR_INVALID_CONTEXT` is cleanup
fallout.

The Mesa 26.0.8 path explains the boundary:

```text
vlVaRenderPicture() processes submitted buffers in order
  -> H.264 picture-parameter handler lazily creates the video codec
  -> nvc0_create_decoder() returns NULL
  -> VA frontend returns ALLOCATION_FAILED
```

The valid v5 hardware probe identifies the first failed constructor operation
as BSP engine-object NEW (`class=0x95b1`) returning `-EEXIST`, after channel
and pushbuf setup succeeded. The other candidate kernel duplicate-key layer
has not yet been identified by that runtime capture.

The compared trace fields do not explain why construction succeeds in the
FFmpeg control but fails after MPV's context recreation. After removing trace
prefixes and normalizing only the dynamic current-surface ID, these printed
fields match:

| VA block | FFmpeg frame 0 vs MPV preflight | FFmpeg frame 0 vs MPV replacement |
|---|---|---|
| H.264 picture parameters | Equal, 27 printed fields | Equal, 27 printed fields |
| IQ matrix | Equal, 30 printed fields | Equal, 30 printed fields |
| Slice parameters | Equal, 26 printed fields | Incomplete; decoder creation fails before slice submission |
| Slice-data payload bytes | Incomplete; payload bytes are not in the trace | Incomplete |

This is equality of fields printed by libva tracing, not byte-for-byte equality
of the VA structures or payloads. The smallest demonstrated lifecycle
difference is MPV destroying and recreating the decode context before the
replacement submission. The retained surface pool is part of that legal
sequence; these traces do not prove that the pool is defective.

The wrong-file-descriptor subchannel DEL path in the pinned Mesa source is a
strong candidate for the `-EEXIST` result. Runtime proof still needs the same
NVIF key across decoder A NEW/DEL and decoder B NEW, the DEL fd and result, and
the exact ABI16 or NVKM duplicate-return layer. See the
[NVIF delete-fd analysis](mpv-second-decoder-create.md#resource-invariant)
and the [review-branch duplicate-layer diagnostic](../validation/nouveau-nvif-duplicate-diagnostic/README.md).

## ffplay export boundary

FFplay reaches decoding and then asks FFmpeg's VAAPI-to-DRM mapper to export a
decoded surface using DRM PRIME 2 with separate layers. The captured surface
is NV12 and marked interlaced. Mesa returns
`VA_STATUS_ERROR_INVALID_SURFACE` at the explicit interlaced-buffer guard in
`vlVaExportSurfaceHandle()`, before ordinary resource-handle export. Nouveau
VP3 represents its decoded fields in array layers. Removing that guard would
misrepresent field-separated storage as a progressive surface, so it is not a
valid fix. A progressive staging/export path remains unproven.

The MPV failure occurs during lazy decoder construction; the ffplay failure
occurs later when exporting a decoded surface. These captures show no common
immediate root cause. They do not rule out a deeper shared issue.

## Reported pixelation screenshot

The saved user screenshot at
`mpv-full-visible-20261001T155451Z/desktop-user-reported-pixelation.png` is a
coherent scene and does not show an obvious macroblock grid in the still. The
nearest saved software references are at PTS 530.021, 530.063, 530.105,
530.146, and 530.188 seconds; the nearest rendered reference depicts the same
scene. The screenshot's media time is approximate, and there is no captured
VAAPI NV12 frame at those PTS values. Therefore this visual inspection does not
confirm or disprove the reported transient hardware pixelation.

The file `pixelation-reference-20261002/software-self-compare.json` compares
the software frame file to itself (its `hardware_raw_path` points to that same
software raw file). Its identical hashes are a software self-consistency
result, **not** a hardware-versus-software comparison. Exact PTS-matched
hardware bytes and repeated VAAPI extraction results are still needed to
distinguish decode corruption from presentation or a nondeterministic race.

## Status

```text
MPV first fatal boundary: replacement-context decoder creation; BSP NEW -EEXIST observed
Wrong-fd DEL -> duplicate key: strong source-supported hypothesis, runtime causality open
ffplay first failure: decoded interlaced VP3 surface rejected at PRIME export
MPV/ffplay shared immediate cause: no evidence
Reported transient pixelation: unresolved; no hardware frame at screenshot PTS
BAR2/PTE cause: separate and unresolved
Visible hardware playback: FAIL / not accepted
main: HOLD
```
