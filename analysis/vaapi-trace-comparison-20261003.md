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

## Upstream Mesa source check

The public Mesa GitLab `main` ref was checked directly on 2026-10-03 and
resolved to `888a19e27844002061f4e0b7e98b293ccc352160` (commit date
2026-10-03). The 26.0.8 release tag resolves to
`60e95b787857afbc9a00b693b91c0d9c8923a430` (2026-05-27). I fetched only the
three affected source files from those immutable refs; no repository checkout
or system source was changed.

| Source path | Mesa 26.0.8 tag SHA-256 | Mesa main SHA-256 | Finding |
|---|---|---|---|
| `src/gallium/winsys/nouveau/drm/nouveau.c` | `2140bca6de1666e4517ebabf97a63b417db43bfbc575198d7835d40ad92dc57f` | `fefb35c2e3923a42381bef37fb4cd240688786e97263d5dca10feb62a36bb311` | New uses `drm->fd` and `(uintptr_t)obj`; DEL still passes `obj->parent->handle` to `drmCommandWrite()` and ignores its result. `nouveau_object_channel_new()` stores the returned channel ID in `obj->handle`, and the libdrm declaration's first parameter is `int fd`. |
| `src/gallium/drivers/nouveau/nouveau_vp3_video_vp.c` | `3c44d8153d08764aacf012cd46186819a120979c34f87adb0a3bee592d898c9f` | `3c44d8153d08764aacf012cd46186819a120979c34f87adb0a3bee592d898c9f` | Byte-identical. The `valid_ref` slot assignment and H.264 identity assertion remain unchanged. |
| `src/gallium/frontends/va/surface.c` | `5b961abc23314c119ea0d1633ffca0b9ff77ac4b392d0de8a32fe128e9c1753b` | `6efb729716e91593a78af8e2c07e42091a41343a8fd9431afd3ba63c54dd2023` | The interlaced-surface `INVALID_SURFACE` guard remains. Upstream's later export-FD deduplication change is after that guard and does not make VP3's field-array surface exportable. |

The GitLab file history since the 26.0.8 tag reports no changes to
`nouveau_vp3_video_vp.c`. `nouveau.c` has two intervening changes (a null
pointer arithmetic fix and a dangling fence pointer fix); neither changes
subchannel DEL. Thus current upstream has not corrected either the DEL fd
argument or the VP3 reference-slot assertion.

One newer upstream change is relevant to the MPV context/surface lifetime, but
is **not a demonstrated fix**. Commits [`dcf4b942`](https://gitlab.freedesktop.org/mesa/mesa/-/commit/dcf4b9426814c4aa698c3a54ff73579f252d8ad9)
and [`96c11b68`](https://gitlab.freedesktop.org/mesa/mesa/-/commit/96c11b68e7f479ecbdc715ab0f28fa0113e09e28)
(2026-09-02, after Mesa 26.0.8) add `pipe_video_codec` reference counting and
retain a codec reference on VA surfaces/buffers for fence wait/destruction.
`vlVaEndPicture()` references the decoder from the surface when it has a
destroyable fence, and context destruction drops only the context's own
reference. A surface that still owns the prior codec can therefore keep that
decoder and its Nouveau object wrappers alive while the replacement decoder
is constructed. This intersects MPV's observed context destroy/recreate with
retained surfaces and is worth a separate, controlled comparison. It can
change pointer-reuse timing, but it does not change the wrong-fd NVIF DEL call:
when the old codec is eventually released, DEL can still fail and leave its
ABI16 key behind. A single successful replacement under this lifetime model
would therefore not prove the stale-key defect is fixed; paired NEW/DEL key
and return records over repeated decoder lifecycles are still needed. The
separate commit
[`46bf88fb`](https://gitlab.freedesktop.org/mesa/mesa/-/commit/46bf88fb65e47fca213cdf17f9c7cf0f827c3e64)
changes PRIME plane-FD deduplication but leaves the earlier interlaced guard
intact.

Pinned current-source links: [Nouveau winsys](https://gitlab.freedesktop.org/mesa/mesa/-/blob/888a19e27844002061f4e0b7e98b293ccc352160/src/gallium/winsys/nouveau/drm/nouveau.c),
[VP3 H.264 reference handling](https://gitlab.freedesktop.org/mesa/mesa/-/blob/888a19e27844002061f4e0b7e98b293ccc352160/src/gallium/drivers/nouveau/nouveau_vp3_video_vp.c),
[VA surface export](https://gitlab.freedesktop.org/mesa/mesa/-/blob/888a19e27844002061f4e0b7e98b293ccc352160/src/gallium/frontends/va/surface.c),
and the [`drmCommandWrite()` declaration](https://gitlab.freedesktop.org/mesa/libdrm/-/blob/b97cbde15c5c3abfe44d78e8f57139e50f612fec/xf86drm.h#L609).

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

## CPU-only continuation checkpoint (2026-10-03 14:01 PDT)

- Current service state was re-read serially: boot `03f7b214-ea03-4be8-af92-f6a073942e9d`, kernel `7.0.0-34-generic`, loaded Nouveau srcversion `354DCD95A5804DE00E20EFB`, and `DISPLAY` unset. The read-only `journalctl -k -b` baseline matcher found one BAR2/HOST_CPU/PTE at `0x471000`, with `channel -1 [00ffbb7000 unknown]`. `check-only --no-desktop` returned `PREFLIGHT_FAIL` and `RUN_ELIGIBLE=false`. No VA/GPU workload or module operation was performed.
- The BAR2 matcher regression suite passed 5/5, including the three literal saved fault records and the `--require-zero` contaminated-baseline exit. The detached-supervisor suite passed 47/47; its synthetic self-test reported 18 hard-stop cases, journal-trigger handling, and complete process-group cleanup. The first combined suite invocation from the repository root failed to import the supervisor module because the README's package-directory `PYTHONPATH` was not used; rerunning with the documented working directory and command passed. No source change was needed.
- I re-inspected the 1920x1080 user-reported screenshot (SHA-256 `6a08474969bb974c8868fa2a3adda6676799af964b4362dd248509c173e5e270`). This still looks coherent and has no obvious macroblock grid. The approximate software-frame match depicts the same scene, and the amplified residual follows edges; screenshot timing is not frame-exact, so this does not rule out the reported transient.
- The retained 300-frame VAAPI-hwdownload and software checksum records have the same 300 PTS values, 1 through 300: 270 hashes match and 30 differ. Only two of those pairs have retained full NV12 bytes: PTS 42 (1.750 s) differs in 7 Y bytes by at most one level, with UV exact; PTS 201 (8.375 s) differs in 2 Y bytes by at most one level, with UV exact. These early samples are not the reported scene, and the other 28 differing hashes were not byte-inspected.
- Five software-only NV12 references exist at PTS 530.021, 530.063, 530.105, 530.146, and 530.188 s. Their self-comparison is bit-identical, validating only the reference/comparator plumbing. There is no hardware NV12 frame at any of those PTS values, so there is still no exact hardware/software comparison at the reported pixelation time.
- The supervisor remains preparation-only. Its one-shot authorization token, current-boot kernel-journal visibility gate, strict module/initramfs/DSO manifest, bounded journal follower, delayed post-stop scan, SIGINT/SIGTERM/SIGKILL escalation, and survivor check are covered by CPU tests. It was not armed or launched. The current boot is contaminated and this service has no display, so it is not eligible for a GPU run.
- Provenance caveat: an earlier parallel read-only command in this continuation reported boot `b8635dbe-6227-4f10-aa35-dbbdbd906aa9` with loaded srcversion `57AE1B168D50DB546CD87A1`. The later serial reads at 14:01 and 14:05 PDT both reported boot `03f7b214-ea03-4be8-af92-f6a073942e9d` with srcversion `354DCD95A5804DE00E20EFB` and the `0x471000` PTE. No reboot was initiated by this continuation. The reason for the differing snapshots is unknown; revalidate boot ID and full installed/loaded/initramfs provenance before any future run.
- Validation command, run from `validation/detached-mpv-supervisor/`:
  `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m unittest discover -s tests -v`
  Result: 47/47 passed. The accompanying `PYTHONDONTWRITEBYTECODE=1 python3 detached_mpv_supervisor.py self-test` also passed. This checkpoint records evidence only; it does not change the failure analysis or playback status.
