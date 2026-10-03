# Post-reboot CPU-only check — 2026-10-03

## Scope

This note records a read-only baseline check and review of existing pixel
artifacts. No VA/GPU workload was launched by this continuation. No module,
module parameter, package, initramfs, or boot state was changed. The repository
was on `review/gk104-vaapi-selftest-v8-20261002` at `478f49e4f1d3df6d0fb299cce13c15676887425e`;
`main` remained at `7b44b1c1e282eac7c54c1cfa8c758118cd66312c`.

## Supervisor review

The external review examined `f8ac22b`. The current review branch includes
`5a486fa` (`validation: harden detached MPV run gates`) and contains the
reviewed safeguards:

- `start` requires `--arm` and a deployment manifest. The hidden `run` action
  consumes a one-use manager token before journal cursor capture.
- A successful run requires natural MPV exit, exit status zero, a complete
  passive journal window, confirmed Nouveau VAAPI H.264 activity, the pinned
  driver mapped by the MPV process, and no kernel or cleanup failure.
- The deployment manifest pins the installed module path/hash/srcversion/
  vermagic, initramfs and embedded module hashes, `diag_ctxsw=N`, VA DSO, MPV,
  and input. Runtime preflight verifies those values.
- Process-group cleanup checks for surviving members after SIGKILL. The child
  environment is allowlisted, and the desktop gate checks an active local X11
  user session on `seat0` with a physical VT.

CPU-only validation on the current branch:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m unittest discover -s tests -v
Ran 39 tests — PASS, no skips

PYTHONDONTWRITEBYTECODE=1 python3 detached_mpv_supervisor.py self-test
SUPERVISOR_SELF_TEST_PASS matcher=pass hard-stop-cases=18 journal-trigger=pass process-group-tree=stopped

PYTHONDONTWRITEBYTECODE=1 python3 bar2_baseline.py --require-zero preflight-correction.txt
BAR2_HOST_CPU_PTE_COUNT=3
BAR2_BASELINE=CONTAMINATED
exit status 2, expected for this saved three-fault fixture

AST_PARSE=PASS files=4
git diff --check=PASS
supervisor package __pycache__=absent
```

The tests include direct-run refusal without/with an invalid token, one-use
token replay refusal, incomplete/operator-cancelled observation failure,
deployment-artifact mismatch rejection, process-group survivor failure, and
the three saved BAR2 fault lines. This CPU result does not validate deployment
or GPU behavior.

## Current boot baseline

Read-only checks at `2026-10-03T12:00:34-07:00` observed:

```text
boot_id=03f7b214-ea03-4be8-af92-f6a073942e9d
kernel=7.0.0-34-generic
loaded Nouveau srcversion=354DCD95A5804DE00E20EFB
modinfo path=/lib/modules/7.0.0-34-generic/updates/dkms/nouveau.ko.zst
installed module SHA-256=88abb555831095d6a71ff4db6378c3d3ec2ee13cd99a153e4eaff31397820a39
vermagic=7.0.0-34-generic SMP preempt mod_unload modversions
service DISPLAY=unset
```

The loaded module is the old `diag4-v3` build, not the reviewed v1-v8 plus
duplicate-layer diagnostic module. No matching reviewed deployment manifest
was supplied. `loginctl` showed an active local X11 user session on `seat0`
and VT 2, but this service has no `DISPLAY`.

The read-only command
`PYTHONDONTWRITEBYTECODE=1 python3 detached_mpv_supervisor.py check-only --no-desktop`
returned status 2:

```text
BAR2_HOST_CPU_PTE_COUNT=1
error=clean-boot BAR2/HOST_CPU/PTE baseline required; found 1
error=clean-boot hard-stop baseline required; found 1
```

The matching current-boot journal record was:

```text
2026-10-03T11:39:41-07:00 Inspiron1564Linux kernel: nouveau 0000:01:00.0: fifo: fault 00 [READ] at 0000000000471000 engine 05 [BAR2] client 07 [HUB/HOST_CPU] reason 02 [PTE] on channel -1 [00ffbb7000 unknown]
```

This event is unattributed. It does not establish a connection to playback,
the source of the faulting access, or a BAR2 production root cause. This boot
fails the clean-baseline gate; no GPU experiment should run on it.

## Pixel evidence recheck

The screenshot
`mpv-full-visible-20261001T155451Z/desktop-user-reported-pixelation.png`
has SHA-256
`6a08474969bb974c8868fa2a3adda6676799af964b4362dd248509c173e5e270`.
Visual inspection shows a coherent image without an obvious macroblock grid.
It is not frame-synchronized to the decoder.

An approximate luma comparison used the screenshot active crop `(0,162)-(1920,979)`
and the software-only reference crops `(0,131)-(1920,948)`, converted to gray,
and downsampled to 480x204 with Pillow BOX. The comparisons were:

| Software reference | Approximate media time | Luma MAE | Luma RMSE | Correlation |
|---|---:|---:|---:|---:|
| `soft-017.png` | 530.000 s | 2.448 / 255 | 5.514 / 255 | 0.995747 |
| `soft-018.png` | 530.250 s | 2.456 / 255 | 5.517 / 255 | 0.995744 |

These nearby CPU-decoded scenes are an approximate alignment, not a comparison
against a matching hardware frame. Exact software NV12 references exist at
PTS 530021, 530063, 530105, 530146, and 530188 (time base 1/1000), but no
hardware NV12 payload exists for those timestamps.

The retained hardware/software raw NV12 pairs are only PTS 42 (1.750 s) and
PTS 201 (8.375 s), verified by matching each raw-frame MD5 against the saved
`framemd5` records. At PTS 42, seven Y bytes differ by one level and UV is
identical. At PTS 201, two Y bytes differ by one level and UV is identical.
The broader first-300-frame comparison has 270 matching and 30 differing
frame hashes; only those two frames have been inspected bytewise. None of this
evidence classifies the reported late-playback artifact.

## Result

The BAR2 matcher and detached supervisor are CPU-tested on the review branch.
The new boot has a real BAR2/HOST_CPU/PTE event and is not a clean test boot.
The screenshot and early raw comparisons do not resolve the reported
pixelation; an exact-PTS hardware capture around 530 seconds remains absent.
`main` remains HOLD. No hardware run is authorized by this evidence.
