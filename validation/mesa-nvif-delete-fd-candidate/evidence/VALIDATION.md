# MPV NVIF delete-fd candidate

Date: 2026-10-01. This is an isolated private candidate. It has been loaded and hardware-tested, but it has **not** produced correct visible playback.

## Candidate change

`nouveau-subchannel-delete-use-drm-fd.patch` changes one argument in Mesa's Nouveau winsys: NVIF subchannel deletion now passes the root Nouveau DRM file descriptor (`nouveau_drm(obj->parent)->fd`) to `drmCommandWrite()`, matching the creation path. The original call passed the parent object's `handle`, which for VP3 engine objects is the FIFO channel identifier. The file descriptor correction is source-supported; its relationship to the observed `-EEXIST` remains to be confirmed at runtime.

Patch SHA-256: `6ff3fdf5b3a38f07246830c78acf46cf222dd3ec2114a87a03526d5f8721df2c`.

## Build and provenance

- Based on the previously validated diagnostic-free Mesa 26.0.8 X11/VA build and its two functional patches.
- The patch was applied with `patch --fuzz=0`.
- Only `src/gallium/winsys/nouveau/drm/nouveau.c` was changed.
- The isolated X11 VA target built with Ninja `-j1` (8 initial build steps, including generated header; 4 actual compile/link steps).
- Candidate plugin: `prefix/lib/x86_64-linux-gnu/dri/libgallium_drv_video.so`.
- Candidate plugin SHA-256: `938dc0ff6c22cc2141e111431f6d56344529b3507e0d7d121ad7333ceaf80512`.
- `nouveau_drv_video.so` resolves to the candidate plugin.
- `ldd` reports no unresolved dependencies; the plugin has no Mesa diagnostic markers.
- After artifact capture, the source file was restored byte-for-byte to the clean source replay (SHA-256 `2140bca6de1666e4517ebabf97a63b417db43bfbc575198d7835d40ad92dc57f`).
- The reusable build tree was rebuilt from the restored source and its plugin matches the original X11 VA binary `aceac163eabeefe5e4c1f2eb38469239044e30671aebcec743e4277f5897b536`.
- The candidate exists only in this private validation directory. It was not installed under `/usr` or registered with DKMS. It was loaded by the private VA-API wrapper for the mpv tests below.

## Runtime evidence that motivates the candidate

The valid v5 capture is `../mpv-second-nvc0-create-stage-v5-stage4-20261001T130605Z/`. Decoder construction #2 failed at stage 4, BSP engine-object creation (`class=0x95b1`), returning `-EEXIST`; channel and pushbuf stages passed. A separate BAR2/HOST_CPU/PTE event at `0x5a9000` occurred in that boot. No further GPU workload should run in that boot.

In source, Mesa's creation path sends `NEW` on the root DRM fd and uses `(uintptr_t)obj` as the NVIF object key. The corresponding deletion path passes `obj->parent->handle` as the `drmCommandWrite()` fd argument, ignores the ioctl result, and then frees the userspace wrapper.

The matching kernel's legacy Nouveau ABI16 ioctl path provides a direct explanation for the duplicate key. For the channel-routed NEW used here, `nouveau_abi16_ioctl_new()` first calls `nouveau_abi16_obj_new(abi16, ENGOBJ, args->object)`. That per-DRM-file object table returns `-EEXIST` when the pointer-derived key is already present, before forwarding the NVIF NEW to the NVKM object constructor. The corresponding DEL removes that entry from the ABI16 table. Channel free destroys the kernel channel and its child NVIF objects, but `nouveau_abi16_chan_fini()` does not remove engine-object entries from the separate `abi16->objects` list; those entries are cleared on DRM client teardown. A DEL sent using the wrong fd can therefore leave the old key recorded after channel free, and allocator reuse of the Mesa wrapper address can make decoder #2's BSP NEW fail with `-EEXIST`.

This makes the wrong-fd deletion a strong source-supported explanation of the v5 result. The capture did not record decoder A/B object keys, the actual DEL fd, or its ioctl return value, so runtime pointer reuse and the exact return site are not yet proven. The separate NVKM object-tree insertion path can also return `-EEXIST` if reached with a duplicate key.

## Hardware result

The candidate was tested with production Nouveau `.13` and mpv `--hwdec=vaapi-copy`. The first 30-second run selected VA-API copy decoding, advanced playback, and exited 0, but the user saw a diagonal triangular split with blank/corrupt video. A later 20-second replay appeared black; that replay ended before a bright title card in the source, so the screenshot alone is not a conclusive bright-frame comparison. The user's direct visual report of triangular corruption is sufficient to classify visible playback as **FAIL**. In this investigation, successful decoding or playback-clock progress does not count as success unless the desktop shows the video correctly.

An FFmpeg hardware decode plus `hwdownload` of 300 NV12 frames exited 0 with no decode errors. Of the corresponding software-vs-hardware frame MD5s, 270/300 matched. Selected frames 41 and 200 differed by at most one luma level in a few bytes (7 and 2 bytes respectively), with exact chroma matches; frame 200's PNGs were byte-identical. This suggests those sampled decoded pixels are close to the software reference, but it does not explain the visible mpv corruption or establish correct presentation.

A later targeted hardware frame capture triggered `CTXSW_TIMEOUT` and a channel kill. That boot is retired from GPU testing; reboot before any further VA-API workload. The candidate is not a playback fix or release patch. The runtime evidence is consistent with decoder reconstruction advancing under this candidate, but the `-EEXIST` lifetime hypothesis is not fully proven because the run did not capture the old/new object keys and delete ioctl result.

The separately proven ffplay interlaced-surface export rejection remains a different issue. No ffplay export fix is included here.
