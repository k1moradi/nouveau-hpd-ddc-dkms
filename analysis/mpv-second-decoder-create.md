# mpv VA context recreation and second Nouveau decoder creation

Status as of 2026-10-01: **the original fatal decode-path failure is localized to BSP engine-object NEW returning `-EEXIST` during decoder creation after VA context recreation; the wrong-fd DEL explanation is a source-supported candidate, not complete runtime proof**. A private candidate advanced mpv into visible playback but produced corrupt video, so there is still no playback fix. The renderer capability-probe export rejection is separate from ffplay's confirmed decoded-surface rejection unless later evidence connects them.

## OBSERVED CONTEXT LIFETIME

The saved X11-enabled Mesa/libva traces are:

- `/home/keivan/nouveau-vaapi-app-validation/mpv-libva-trace-opengl-20260930T214151Z/libva.trace.214151.thd-0x000222ba`
- `/home/keivan/nouveau-vaapi-app-validation/ffmpeg-libva-trace-x11-20260930T214345Z/libva.trace.214345.thd-0x00022445`

They use the X11-enabled functional-only Mesa DSO with SHA-256 `aceac163eabeefe5e4c1f2eb38469239044e30671aebcec743e4277f5897b536` and the known H.264 input whose SHA-256 is `d1bab5275bcb585791fbfb15c801c1aab582256e7b7fca280c76f78a0a1c1ec2`.

mpv successfully submits its initial H.264 preflight picture through `vaBeginPicture()`, both `vaRenderPicture()` calls, and `vaEndPicture()`. This trace does not prove that the frame was presented. mpv then destroys VA context 4 and creates a replacement context with the same profile/entrypoint/dimensions. The trace has no `vaDestroySurfaces()` between those context operations; the existing pool/surface IDs are retained and reused. A new `vaBeginPicture()` succeeds. The first new `vaRenderPicture()` contains the H.264 picture-parameter buffer (672 bytes) and IQ matrix (240 bytes); Mesa lazily creates the Gallium codec while handling picture parameters, and the call returns `VA_STATUS_ERROR_ALLOCATION_FAILED`. Later `INVALID_CONTEXT` messages are downstream.

The reviewed FFmpeg control does not recreate the context at that point and its corresponding parameter submission succeeds. The compared first-picture data match in all examined H.264 fields: picture size 672 bytes, IQ matrix size 240 bytes, slice-parameter size 3128 bytes, and slice-data size 6160 bytes. The picture parameters have the same frame-0 values, empty reference list, dimensions (120x68 macroblocks), 5 reference frames, chroma format 1, sequence fields `32977`, and picture fields `0x510`; only VA surface/buffer IDs differ. The first frame's slice parameters also match in the printed fields, including data size 6160, bit offset 42, first MB 0, slice type 2, and QP delta -14. The IQ matrices contain the same values (all 16 in this frame). Both first pictures complete successfully in FFmpeg and mpv. This narrows the decode transition that matters to context destruction/recreation, but does not prove that retained surfaces are invalid or leaked.

The trace files are per-thread, so the first error in the entire mpv process needs to be distinguished from the first fatal decode error. mpv's renderer/interop thread (`thd-0x000222c9`) first gets `VA_STATUS_ERROR_OPERATION_FAILED` from `vaDeriveImage()` on a 128x128 capability-test surface at timestamp `33311.942058`. FFmpeg's X11 `hwdownload` run has the same failed `vaDeriveImage()` capability probe at `33425.537289`; both later use their respective supported paths. mpv's renderer thread then gets `VA_STATUS_ERROR_INVALID_SURFACE` from `vaExportSurfaceHandle()` on another 128x128 probe surface (surface ID 3, DRM PRIME 2 memory type `0x40000000`, flags `0x5`). That thread continues and successfully exports later 128x128 probe surfaces with other render-target configurations. FFmpeg's readback control does not call `vaExportSurfaceHandle()`; it calls `vaSyncSurface()` and `vaGetImage()` successfully. These renderer-probe failures are the earliest non-success statuses, but the trace does not show them as fatal, and it does not record an mpv export attempt for a decoded 1920x1088 surface before the decode-context failure. Do not label either probe status as the proven cause of mpv's blank playback.

For the decode path, mpv and FFmpeg both configure H.264 High/VLD, 1920x1088 NV12 surfaces, and a context with flag `1` and zero explicit render targets. Their first `vaBeginPicture()` succeeds. The first `vaRenderPicture()` call has picture parameters followed by the IQ matrix and succeeds in both. The second call has the slice parameters and slice data and also succeeds in both. FFmpeg then keeps context 3 and submits further frames, with successful `vaSyncSurface()`/`vaGetImage()` readback. mpv instead successfully destroys context 4 after its preflight frame, successfully creates a replacement context (the numeric VA context ID is reused), retains the surface pool, and replays frame 0 to target surface 2. That replacement context's first `vaRenderPicture()` contains picture parameters at buffer index 0 and IQ matrix at index 1; it returns `VA_STATUS_ERROR_ALLOCATION_FAILED`. The following `vaEndPicture()` returns `VA_STATUS_ERROR_INVALID_CONTEXT` and is downstream.

The explanation that mpv's H.264 preflight/flush causes the reset is source-supported but the runtime call stack for the reset itself has not been captured. Treat that as an explanation to verify, not as the root cause.

## DECODER #1

The first `nvc0_create_decoder()` constructs successfully and handles the first picture. The libva trace proves successful VA submissions and `vaEndPicture()` for that initial frame; it does not by itself account for every Nouveau channel/BO lifetime.

## TEARDOWN

mpv/libavcodec destroys the old VA context while retaining the VA surface pool. The reason is consistent with its probe/reinitialization behavior, but needs a runtime stack to pin down. Context recreation is valid API behavior; the driver must tolerate it. Do not change mpv's preflight or disable the recreate transition as a workaround.

## DECODER #2

The replacement context's first picture-parameter buffer handler creates the codec. In Mesa 26.0.8, `src/gallium/frontends/va/context.c` leaves a bitstream decoder unset at `vaCreateContext()`; `src/gallium/frontends/va/decode.c:53-70` calls `pipe->create_video_codec()` while handling picture parameters and maps a `NULL` result to `VA_STATUS_ERROR_ALLOCATION_FAILED`. `picture.c:209-224` processes buffers in array order, so the first `VAPictureParameterBufferType` fails before the following IQ matrix is handled. `nvc0_context.c:473` wires this callback to `nvc0_create_decoder()` on GK104. The valid v5 runtime probe now identifies its first failed constructor operation: stage 4, BSP engine-object NEW (`class=0x95b1`) returns `-EEXIST`; channel and pushbuf setup passed. Thus the VA status is only the outer translation; the meaningful lower-level result is the BSP object creation collision.

The first frame has no reference-frame entries in either the successful FFmpeg submission or mpv's failing replacement-context submission. The `vaRenderPicture()` call fails while lazily creating the decoder, before slice parameters/data are submitted for that replayed frame. Therefore the saved direct-mpv failure does not establish the `nouveau_vp3_fill_picparm_h264_vp()` reference-array assertion as the cause. The `vaapi-copy` assertion remains a separate downstream/error-path issue until its own first failure is traced.

### Source-only follow-up: VP3 reference-slot identity across decoder recreation

In the pinned Mesa 26.0.8 source, `struct nouveau_vp3_video_buffer` stores
`valid_ref`, while each `nouveau_vp3_decoder` owns its separate `refs[17]`
table (`nouveau_vp3_video.h:29-35, 63-113`). `nvc0_create_decoder()` allocates
the decoder with `CALLOC_STRUCT`, so a replacement decoder begins with an empty
reference table (`nvc0_video.c:115-120`). The only assignment to a buffer's
`valid_ref` found in the Nouveau VP3 sources is in
`nouveau_vp3_handle_references()`, when that decoder registers the current
target into one of its slots (`nouveau_vp3_video_vp.c:161-178`). The decoder
destructor does not clear `valid_ref` on video buffers.

The H.264 VP path calls `nouveau_vp3_fill_picparm_h264_vp()` first, then
`nouveau_vp3_handle_references()`, then
`nouveau_vp3_fill_picparm_h264_vp_refs()` (`nouveau_vp3_video_vp.c:409-413`).
While filling the picture parameters, each non-null reference buffer is
checked with
`assert(dec->refs[refs[j]->valid_ref].vidbuf == refs[j])`
before the new decoder's reference handler gets a chance to inspect or
register it (`nouveau_vp3_video_vp.c:280-304`). A target surface is different:
the handler can assign it a slot before the later target assertion at line
325.

This creates a concrete candidate failure for context recreation that retains
the VA surface pool: a reference buffer may carry its old decoder's `valid_ref`
index, but the replacement decoder's slot at that index is empty or belongs to
another buffer. If that buffer is submitted as a reference before it has been
registered as a target in the new decoder, the H.264 picture-parameter assertion
can fail. This is a decoder-local reference-slot identity issue; it does not by
itself prove that the underlying VA surface or allocation was destroyed.

This hypothesis does **not** explain the original v5 mpv failure. That failure
returns from decoder construction before VP picture-parameter submission, and
the captured replacement frame has an empty reference list. The saved candidate
playback does not identify the assertion line, target frame, `valid_ref`, or
current decoder slot contents, so the later assertion mechanism remains
unconfirmed. Do not remove the assertion or treat resetting `valid_ref` as a
fix without a runtime trace showing the relevant buffer/decoder generations and
reference list.

### Upstream history check (2026-10-02)

Fetched `src/gallium/drivers/nouveau/nouveau_vp3_video_vp.c` from Mesa upstream
commit `7550e0aa4941780fe953bd7018f79961162b75ea` (2026-01-12). Its full-file
SHA-256 is `3c44d8153d08764aacf012cd46186819a120979c34f87adb0a3bee592d898c9f`,
identical to the local Mesa 26.0.8 file. The current upstream snapshot
therefore retains the same `valid_ref` assignment and H.264 reference assertion;
this is not a local-only source divergence. It does not prove the behavior is
correct or that no later branch changes it.

The upstream file history identifies commit
[`a41aad843108cec1901c88a76d5ceb4ede2e062b`](https://gitlab.freedesktop.org/mesa/mesa/-/commit/a41aad843108cec1901c88a76d5ceb4ede2e062b)
(2014-09-11, “nouveau: rework reference frame handling”) as the change that
introduced the current style of reusable reference-slot selection and target
decoded-field bookkeeping. The reviewed change does not establish any
decoder-recreation generation tracking for `valid_ref`. This historical design
context keeps the cross-decoder slot concern plausible, but runtime evidence is
still required to show an actual stale reference reaches the assertion.

The newer VA frontend has a separate surface/fence lifetime change. Mesa commits
[`dcf4b942`](https://gitlab.freedesktop.org/mesa/mesa/-/commit/dcf4b9426814c4aa698c3a54ff73579f252d8ad9)
and
[`96c11b68`](https://gitlab.freedesktop.org/mesa/mesa/-/commit/96c11b68e7f479ecbdc715ab0f28fa0113e09e28)
(2026-09-02) add reference counting for `pipe_video_codec` and retain codec
references from VA surfaces/buffers used for fence wait/destruction. In the
newer `vlVaEndPicture()` path, the surface holds a codec reference when the
codec provides `destroy_fence`; context destruction releases its own codec
reference rather than forcibly detaching every surface fence. This is a
plausible generic lifecycle improvement to inspect against delayed surface
fences and the observed post-submit CTXSW, but it does not alter the Nouveau
NVIF subchannel DEL fd or directly explain the stage-4 ABI16 `-EEXIST`. It is
not a proven backport candidate for that failure.

## SHARED ROOT CAUSE

**No evidence of a shared root cause in the captured failures.** mpv's original fatal decode path is a BSP object NEW collision during decoder reconstruction. ffplay's actual decoded-surface path is rejected at the VA DRM PRIME exporter because the VP3 buffer is interlaced/field-array. mpv's separate 128x128 capability-probe `INVALID_SURFACE` is not evidence that its decoded surfaces hit the same exporter failure. A deeper common issue is not ruled out, but none is demonstrated.

## FIRST FAILED OPERATION

The first failing VA buffer is the replacement context's `VAPictureParameterBufferType`; it reaches `nvc0_create_decoder()` and is translated to `VA_STATUS_ERROR_ALLOCATION_FAILED` when the constructor returns `NULL`. The valid v5 stage probe identified the first failed constructor operation as BSP engine-object NEW (`class=0x95b1`) returning `-EEXIST` after channel and pushbuf setup passed. This is hardware-observed. The likely stale pointer-key explanation comes from the wrong-fd delete path below, but the v5 probe did not capture the old/new object keys or DEL syscall return.

The prepared v3 GDB run remains explicitly inconclusive and is retained as historical evidence. Its command file parsed offline and saw constructor entries, but its `break nvc0_create_decoder+OFFSET` locations were pending before the DSO loaded and did not classify any constructor stage. It retried until the 90-second timeout: 5,775 constructor-entry markers, 5,773 VA allocation-failure messages, zero stage markers, wrapper exit 137. It also added 453 BAR2/HOST_CPU/PTE read faults at offsets `0x52b000` (336), `0x53c000` (38), and `0x54d000` (79). No old PRIV/CTXSW/PROP/failed-idle/GPU-reset signatures were present in that delta.

That v3 run is preserved at `/home/keivan/nouveau-vaapi-app-validation/mpv-second-nvc0-create-stage-v3-20261001T095105Z/`; `result-audit.txt` and `SHA256SUMS` record the incomplete result and hashes. It is not evidence that a particular constructor operation failed or that BAR2 caused the VA error. No further GPU test should be run in that boot.

The v3 attempt was invalid for stage attribution because its offset breakpoints were pending before the private DSO loaded; function-entry breakpoints ran, but no stage breakpoint did. The replacement v4 probe catches the load of the exact X11-enabled DSO, then sources machine-address breakpoints. Its frozen files are:

- Main script: `/home/keivan/.cache/vaapi-app-source-review/probes/mpv-second-nvc0-create-stage-v4.gdb`, SHA-256 `eac2f7a4614ec3e318fda3ead71112e670c0904cf9980c74f273c549d69858a0`.
- Stage script: `/home/keivan/.cache/vaapi-app-source-review/probes/mpv-second-nvc0-create-stage-v4-stages.gdb`, SHA-256 `305d166115589f2a5de571f6a6763fd2363c03414ddfaf3606cf3d1d090c6ecf`.

Offline validation passed on 2026-10-01: the main script parsed with `/bin/true` and reported `NOUVEAU_DIAG_NVC0_CREATE_NOT_REACHED`; all 22 stage breakpoints resolved against the pinned DSO as concrete addresses; and a CPU-only synthetic `dlopen()` test caught a known failure in constructor invocation 2, recorded its return, and stopped. The logs and DSO/script hashes are preserved in `/home/keivan/.cache/vaapi-app-source-review/probes/mpv-second-nvc0-create-stage-v4-validation-20261001/`.

This validates the debugger control flow, not the real mpv result. The v4 probe was later found inconclusive because its DSO alias load trigger did not arm the stage breakpoints. The corrected v5 probe subsequently produced the stage-4 `-EEXIST` result above. The `CTXSW_TIMEOUT` and FFmpeg channel-kill wording in the earlier draft referred to a historical boot and is not a current-state claim; consult the timestamped VA-API handoff for current boot evidence and runtime gates.

## RESOURCE INVARIANT

A replacement decoder must be constructible after a prior decoder/context has been destroyed, even when the VA surface pool is retained and later reused. The v5 result narrows the violated condition to a duplicate BSP object key at NEW time. Mesa derives subchannel object/token from the userspace object address; its current DEL call passes `obj->parent->handle` as the `drmCommandWrite()` fd, while NEW uses the root DRM fd. Linux ABI16 stores engine-object keys per DRM file and returns `-EEXIST` for a duplicate key. This makes a failed DEL plus allocator address reuse a strong source-supported explanation. It remains unproven at runtime because no captured trace records decoder A's key, DEL fd/result, and decoder B's key together.

## PATCH

The private one-line candidate changes subchannel DEL to use the owning root DRM fd. It was loaded for a short `--hwdec=vaapi-copy` mpv run: mpv advanced to about 29 seconds and exited 0, but the user observed a triangular blank/corrupt image; a shorter replay appeared black over dark opening content. This is **not** a functional playback fix. The candidate run had no constructor-stage instrumentation, so it does not prove that BSP `-EEXIST` disappeared. Do not suppress errors, change context recreation, or treat decoder progress as successful playback.

## VALIDATION

1. The v5 runtime probe classified decoder #2's first failing stage as BSP object NEW, `-EEXIST`; do not repeat the old generic constructor probe.
2. The wrong-fd DEL candidate was built from the exact Mesa source and tested privately. Playback remains visibly incorrect, and that test did not log constructor #2's stage.
3. Historical stop condition: the boot observed during the original candidate validation contained `CTXSW_TIMEOUT` and a killed FFmpeg channel. For any future runtime test, use the current handoff's boot-specific preflight rather than carrying that old state forward. Do not install `.14` or change system Mesa as part of the diagnostic run.
4. After the current BAR2 diagnostic experiment has been handled and a fresh logged-in desktop boot passes its clean-baseline gate, capture one bounded visible mpv run with the candidate through at least the bright title-card interval, save a desktop screenshot and kernel delta, and ask the user to classify visible output. Do not count VAAPI selection, progress, or frame hashes as a pass unless the video is visibly correct.
5. If the visual output is still corrupt, compare a controlled CPU-decoded mpv reference using the same output path only as a diagnostic to separate presentation from decoded-frame content. Keep that comparison separate from the hardware playback acceptance result. Then investigate the exact corrupt-frame/presentation path before changing the source again.
