# CPU-only validation: BAR2 matcher, MPV supervisor, and pixel records

## Reproducible checks

The copied review package was tested without launching a player or touching the
GPU. From `validation/detached-mpv-supervisor/`:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 python3 detached_mpv_supervisor.py self-test
```

Results: 5 BAR2 matcher tests and 17 supervisor tests passed, with no skips. The
BAR2 tests load all three exact saved fault records from the preserved fixture,
verify their addresses (`0x3f3000`, `0x5d6000`, `0x5da000`), and verify that
`--require-zero` rejects them. The supervisor suite covers first-stop behavior,
`SCHED_ERROR 0a`, any Nouveau FIFO MMU fault, kernel BUG/Oops/WARNING and
sanitizer/lockdep signatures, delayed PTE capture, and concurrent stop requests.
The synthetic supervisor command reported
`SUPERVISOR_SELF_TEST_PASS matcher=pass hard-stop-cases=18 journal-trigger=pass process-group-tree=stopped`.

The PTS-exact NV12 comparator suite also passed 8/8 from
`mpv-full-visible-20261001T155451Z/pixelation-reference-20261002/`.
Python AST parsing passed for the matcher, supervisor, and their tests. An
initial supervisor test run exposed a missing `sys` import in the new synthetic
process-group regression; that was corrected and the 17-test run then passed.

## Pixelation evidence

The saved user screenshot (`desktop-user-reported-pixelation.png`, SHA-256
`6a08474969bb974c8868fa2a3adda6676799af964b4362dd248509c173e5e270`) is a
coherent frame with no obvious macroblock grid on visual inspection. That does
not rule out a transient artifact on another frame. The downsampled screenshot
comparison around the estimated 530.083-second media position is approximate,
not synchronized to a hardware frame; its global luma metrics were MAE 2.528,
RMSE 5.213, and SSIM-style score 0.996215.

The available raw hardware/software NV12 pair contains only PTS 42 (1.750 s) and
PTS 201 (8.375 s), established by matching each payload's MD5 to the saved
framemd5 records. Both are 1920x1080 packed NV12. At PTS 42, hardware differs
from software in 7 Y bytes by one level (bounds x=1160..1163, y=454..462), with
UV identical. At PTS 201, 2 adjacent Y bytes differ by one level (x=1214..1215,
y=479), with UV identical. These opening-seconds captures do not explain the
reported late-playback artifact.

A separate software-only reference contains exact PTS 530021, 530063, 530105,
530146, and 530188 at time base 1/1000. **There is no retained hardware NV12
payload at any of those PTS.** Therefore no frame-exact hardware/software
comparison of the user-reported scene is available. The earlier 300-frame
checksum comparison had 270 matching and 30 differing hashes; pixel-level
characterization exists only for the two early pairs above.

## Current service state and run boundary

After the user's reboot, the observed boot ID was
`03f7b214-ea03-4be8-af92-f6a073942e9d`, kernel
`7.0.0-34-generic`, and loaded Nouveau srcversion
`354DCD95A5804DE00E20EFB` (old diag4-v3, not the reviewed v1-v8/duplicate
diagnostic module). The installed module selected by `modinfo` is
`/lib/modules/7.0.0-34-generic/updates/dkms/nouveau.ko.zst`. The corrected
BAR2 matcher reports zero matching events in this boot. The service has no
`DISPLAY`, and the loaded module identity is wrong for the planned diagnostic
campaign, so this is not an eligible GPU-test boot. A separate scan using the
supervisor's expanded kernel hard-stop classifier also found zero matching
records; those two zero counts do not substitute for the required reviewed
module identity or logged-in desktop.

The supervisor source's `EXPECTED_SRCVERSION` is still the prior `.13` test
pin (`57AE1B168D50DB546CD87A1`), not the loaded `354DCD...` module and not the
reviewed v1-v8 diagnostic build. A later run needs an explicit, reviewed pin for
the selected deployment artifact; this continuation does not rewrite it.

One read-only `check-only --no-desktop` attempt was externally bounded at 20
seconds and ended with timeout status 124; no media/GPU workload was launched
and no journal/supervisor child remained. This is not recorded as a preflight
pass. The verified srcversion mismatch and missing desktop already require a
refusal. No module, parameter, package, initramfs, or boot change was made.
