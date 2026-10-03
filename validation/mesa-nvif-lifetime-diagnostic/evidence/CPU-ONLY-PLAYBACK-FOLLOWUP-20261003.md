# CPU-only playback follow-up — 2026-10-03 14:01 UTC

This checkpoint revalidates saved evidence and diagnostic tooling only. It did
not launch mpv, ffplay, FFmpeg, VLC, or any other VA/GPU workload. No module,
package, initramfs, or boot configuration was changed, and no reboot occurred.

## Required status

```text
MPV FIRST FAILURE:
Replacement-context frame-0 vaRenderPicture() submits a VAPictureParameter
buffer first (672 bytes; IQ matrix buffer is 240 bytes). vaBeginPicture() and
the PP/IQ map/unmap operations succeed, but the PP-first render batch returns
VA_STATUS_ERROR_ALLOCATION_FAILED. Mesa's lazy decoder creation reaches BSP
NVIF object NEW class 0x95b1 and returns -EEXIST in the saved v5 capture.
The wrong-fd subchannel DEL / stale ABI16 key explanation is source-supported,
but runtime causal A/B evidence is still absent.

FFPLAY FIRST FAILURE:
The saved capture requests VAAPI-to-DRM-PRIME mapping. Mesa resolves the
decoded NV12 surface with interlaced=1 and returns
VA_STATUS_ERROR_INVALID_SURFACE at the explicit interlaced-export guard,
before normal resource-handle export.

SHARED ROOT CAUSE:
NO EVIDENCE. These are separately observed first-failure boundaries.

FIX STATUS:
CANDIDATES only. The Mesa DEL-fd A/B and progressive ffplay staging paths
have not established runtime correctness. No functional fix is accepted.

VISIBLE HARDWARE PLAYBACK:
FAIL until demonstrated otherwise.
```

## CPU regression checks

The following CPU-only commands passed in this continuation:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  test_bar2_baseline.py test_detached_mpv_supervisor.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_compare_hw_nv12.py
```

Results:

| Suite | Result |
|---|---:|
| BAR2 baseline matcher | 5/5 |
| Detached mpv supervisor | 19/19 |
| Timestamped NV12 comparator | 8/8 |

The BAR2 fixture test reads `preflight-correction.txt`, requires exactly the
three saved BAR2/HOST_CPU/PTE fault records at `0x3f3000`, `0x5d6000`, and
`0x5da000`, and confirms `--require-zero` returns status 2 while printing all
three. Literal bracket matching and representative near misses also pass.

The supervisor tests exercise clean and late-fault outcomes, first-stop
classification, whole process-group cleanup, fail-closed journal errors, and
the passive post-stop journal window. A delayed PTE is included by the
synthetic post-stop test. The supervisor remains unarmed; these tests do not
validate a live systemd/GPU run.

## Saved pixelation evidence

The user-reported screenshot is
`/home/keivan/nouveau-vaapi-app-validation/mpv-full-visible-20261001T155451Z/desktop-user-reported-pixelation.png`
(SHA-256 `6a08474969bb974c8868fa2a3adda6676799af964b4362dd248509c173e5e270`).
It is a coherent still without an obvious macroblock grid. This does not rule
out a transient artifact in another frame.

The saved screenshot samples around media 10:07–10:29 depict the same scenes
as the nearby software-decoded references. Their comparison uses an
unsynchronized screenshot status time and searches a +/-1.5 second window;
residuals are dominated by scene edges and motion. It is an approximate visual
comparison, not frame-exact hardware evidence.

Software NV12 references around the screenshot's approximate 530.083-second
media time are available at PTS 530.021, 530.063, 530.105, 530.146, and
530.188 seconds. No hardware NV12 payload or hardware showinfo log exists at
those PTS values, so the late reported artifact cannot be compared pixel for
pixel from retained evidence.

The only retained raw hardware/software NV12 pairs are the two opening-segment
frames extracted at PTS 42 (1.750 s) and PTS 201 (8.375 s). This continuation
rechecked their MD5 hashes against the corresponding hardware and software
`framemd5` records before comparing bytes:

| PTS | Y bytes changed | Maximum Y difference | UV bytes changed | Bounds in Y plane |
|---:|---:|---:|---:|---|
| 42 | 7 | 1 | 0 | x=1160..1163, y=454..462 |
| 201 | 2 | 1 | 0 | x=1214..1215, y=479 |

The paired hardware/software MD5s are `34edb90f3ce1bc87b8f7cf7a3349a739` /
`e8c23d70516e34fe9e3c39ebcf0937ee` at PTS 42 and
`62aa15e73151b5d6ecf7b08df0c46acb` /
`99b7a6c94e43e2f9907a548fae41e405` at PTS 201. Each matches its respective
saved `framemd5` entry. In the broader 300-frame checksum comparison, 270
hashes match and 30 differ; the raw byte inspection above covers only PTS 42
and 201, not the late user-reported scene.

These sparse one-level luma differences do not explain or clear the reported
late pixelation. A PTS-exact hardware capture at the reported scene remains
necessary.

The five-frame self-comparison in `pixelation-reference-20261002/` is only a
software artifact/hash and comparator-plumbing check; it is not hardware
evidence.

## Current read-only playback gate

The present boot is `8a436489-d363-4749-a3e0-2f134e34931a` on
`7.0.0-34-generic`. The loaded and selected Nouveau module is diag4-v3,
srcversion `354DCD95A5804DE00E20EFB`, file
`/lib/modules/7.0.0-34-generic/updates/dkms/nouveau.ko.zst`, SHA-256
`88abb555831095d6a71ff4db6378c3d3ec2ee13cd99a153e4eaff31397820a39`.
`detached_mpv_supervisor.py check-only --no-desktop` refuses this boot because
the pinned playback module is production `.13` (srcversion
`57AE1B168D50DB546CD87A1`) and the current kernel journal contains 70 hard-stop
records: 67 PROP/RT overrun signatures, one CTXSW timeout, one channel-kill,
and one failed-to-idle. The corrected BAR2 matcher finds zero PTE events on
this boot; that does not make the hard-stop baseline clean.

No media process was running during the read-only process check. `DISPLAY` is
unset in this service. No player was opened and the supervisor was not armed.
Do not use this boot for playback validation.

## Source and release state

The side-by-side VA trace timeline is recorded in
`FIRST-FAILURE-RAW-REVALIDATION-20261003.md`. FFplay's source audit is in
[`analysis/ffplay-vp3-prime-export.md`](../../../analysis/ffplay-vp3-prime-export.md).
The evidence supports distinct immediate failures, without proving they are
ultimately unrelated.

The review branch was at `d3997148cc78a2b04b2cbce19e6e128a4fb8cec8` when this
checkpoint was prepared. `main` remains at
`7b44b1c1e282eac7c54c1cfa8c758118cd66312c` and HOLD. No release promotion is
recommended from these CPU-only checks.
