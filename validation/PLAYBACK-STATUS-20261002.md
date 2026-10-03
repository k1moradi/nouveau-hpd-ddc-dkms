# Playback root-cause status — 2026-10-02

This note records the current evidence boundary for the K4200/GK104 VA-API
playback goal. It does not claim playback works.

## MPV first failure

The saved v5 runtime probe observes the replacement decoder's first
`VAPictureParameterBufferType` submission entering lazy `nvc0_create_decoder()`.
Channel and pushbuf setup succeed; BSP engine-object NEW (class `0x95b1`)
returns `-EEXIST`, which becomes
`VA_STATUS_ERROR_ALLOCATION_FAILED` from `vaRenderPicture()`. The exact kernel
duplicate table (ABI16 or NVKM) and decoder-A DEL key/return are not captured.
The old Gallium winsys calls NVIF NEW with the root DRM fd but passes
`obj->parent->handle` as the `drmCommandWrite()` fd for subchannel DEL. This is
a strong source-level defect candidate, but the retained evidence does not
close its causal link to the observed `-EEXIST`.

A separate later MPV core reaches the VP3 H.264 reference-slot identity
assertion. Source analysis shows that a retained buffer can carry `valid_ref`
from a destroyed decoder into a new decoder's independently initialized slot
table. The saved runtime data do not identify the asserting reference or slot,
so this remains a source-supported hypothesis. The diagnostic-only candidate
is in [the VP3 reference diagnostic bundle](mesa-vp3-reference-diagnostic/README.md).

## ffplay first failure

The saved ffplay trace shows `vaExportSurfaceHandle()` on a decoded VP3
surface returning `VA_STATUS_ERROR_INVALID_SURFACE` at Mesa's explicit
interlaced-buffer rejection, before `resource_get_handle()`/DRM PRIME export.
The decoded Y and UV resources each contain two field layers. Removing the
guard would expose field-separated storage as progressive NV12 and is not a
valid fix. MPV's constructor failure and ffplay's export rejection currently
have **no demonstrated shared root cause**.

## Pixelation evidence

The saved user-reported pixelation screenshot is visually coherent and has no
obvious macroblock grid, but a still image cannot disprove a transient artifact.
Five software NV12 references exist at PTS 530.021, 530.063, 530.105, 530.146,
and 530.188 seconds around the screenshot's approximate 08:50.083 media time.
The saved comparison JSON is a software self-comparison only. There is no
captured VAAPI raw frame at those PTS values, so the reported artifact has not
been compared frame-exactly against software output.

The two previously retained hardware raw frames are PTS 42 and 201 near the
opening. They have only 7 and 2 changed luma bytes respectively, each by one
level, with UV identical; those samples do not explain the reported artifact
near 530 seconds. The eight desktop screenshots compared with nearby CPU
frames are coarse time matches, not pixel-exact hardware-frame comparisons.

## Current boot gate

Read-only state observed during this continuation:

```text
kernel: 7.0.0-34-generic
boot ID: 8a436489-d363-4749-a3e0-2f134e34931a
loaded Nouveau srcversion: 354DCD95A5804DE00E20EFB (diag4 candidate)
BAR2/HOST_CPU/PTE: 0 in the current-boot kernel journal
PROP RT_WIDTH/RT_HEIGHT overrun records: 67
CTXSW_TIMEOUT: 1 at monotonic 3543.056 s
Xorg channel kill: 1 at monotonic 3543.061 s
Xorg failed-to-idle: 1 at monotonic 3567.324 s
player processes running at inspection: none
```

This boot is not suitable for another GPU/player test because the serious
video/rendering-engine failure signatures already occurred and Xorg was
affected. No player was launched in this continuation. This observation does
not establish whether the prior failure was caused by playback, desktop work,
or another GPU client.
The filtered kernel records are preserved in
[`evidence/current-boot-stop-signatures-20261002.txt`](evidence/current-boot-stop-signatures-20261002.txt).

## Status

```text
MPV FIRST FAILURE: stage-4 BSP object NEW returned -EEXIST (observed)
FFPLAY FIRST FAILURE: interlaced VP3 export rejected before PRIME handle export (observed)
SHARED ROOT CAUSE: no evidence
FUNCTIONAL FIX: not established
VISIBLE HARDWARE PLAYBACK: FAIL / not accepted
MAIN MERGE: HOLD
```

The Linux v1-v8 diagnostic selftest review bundle is separately recorded at
[`validation/nouveau-selftests-v1-v8`](nouveau-selftests-v1-v8/README.md).
