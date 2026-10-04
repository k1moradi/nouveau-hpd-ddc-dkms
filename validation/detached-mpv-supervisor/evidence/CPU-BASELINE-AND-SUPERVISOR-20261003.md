# CPU-only BAR2 baseline, pixel evidence, and supervisor checkpoint

## Scope

This checkpoint fixes a supervisor error path and records CPU-only verification.
No VA/GPU workload or player was started. No module, initramfs, package, or
system configuration was changed, and no reboot was initiated.

## BAR2 matcher and supervisor

`detached_mpv_supervisor.preflight()` has a four-value return contract:

```text
errors, loaded_srcversion, journal_snapshot, deployment_observed
```

The journal-query `OSError` / timeout path previously returned only three
values. It now returns the empty snapshot and the accumulated deployment
metadata, allowing both `check-only` and the service path to report a normal
fail-closed result rather than raising an unpacking exception.

Regression coverage checks both `OSError` and `TimeoutExpired`, plus the
`check-only` CLI timeout path. The CLI returns 2, prints `PREFLIGHT_FAIL` and
`RUN_ELIGIBLE=false`, and emits no traceback.

The BAR2 matcher regression reads the pinned saved three-line fixture
`preflight-correction.txt`; all three literal `BAR2` / `HOST_CPU` / `PTE`
records match, and the required-zero gate returns contaminated. Near misses,
empty input, and the later saved `0x471000` record are covered as well.

Validation, with no skipped tests:

```text
cd validation/detached-mpv-supervisor/tests
PYTHONPATH=.. PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  test_bar2_baseline.py test_detached_mpv_supervisor.py
Ran 51 tests ... OK

cd /home/keivan/nouveau-vaapi-app-validation/mpv-full-visible-20261001T155451Z/pixelation-reference-20261002
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_compare_hw_nv12.py
Ran 8 tests ... OK

cd /home/keivan/nouveau-hpd-ddc-dkms/validation/detached-mpv-supervisor/tests
PYTHONDONTWRITEBYTECODE=1 python3 ../detached_mpv_supervisor.py self-test
SUPERVISOR_SELF_TEST_PASS matcher=pass hard-stop-cases=18 \
  journal-trigger=pass process-group-tree=stopped
```

The detached stop path escalates SIGINT, SIGTERM, and SIGKILL, checks for live
process-group members after SIGKILL, and fails the run if the leader or a
survivor remains. The synthetic test exercises journal-triggered group cleanup;
it does not launch any media process. The supervisor was not armed or started.

`git diff --check` and AST parsing of the matcher, supervisor, and both test
modules pass.

## Pixelation evidence

The user-reported screenshot is pinned by SHA-256
`6a08474969bb974c8868fa2a3adda6676799af964b4362dd248509c173e5e270`. Direct
inspection shows a coherent scene with no obvious macroblock grid. The saved
approximate comparison to nearby software-only frames reports global,
downsampled luma SSIM-style `0.99621`, correlation `0.99624`, MAE `2.53/255`,
and RMSE `5.22/255`. This is not frame-synchronized and cannot rule out a
localized or transient artifact.

The retained packed hardware/software NV12 pair contains two early frames.
Their raw MD5s map through the retry run's `framemd5` records to exact PTS 42
(`1.750 s`) and 201 (`8.375 s`):

| PTS | Y bytes changed | Max Y delta | UV bytes changed |
|---:|---:|---:|---:|
| 42 | 7 | 1 | 0 |
| 201 | 2 | 1 | 0 |

These are near-bit-exact early controls from a separate run, not captures of
the screenshot run. The software NV12 reference at PTS 530.021, 530.063,
530.105, 530.146, and 530.188 seconds remains available, but no hardware NV12
payload at those PTS values is retained. The reported late-scene pixelation is
therefore unresolved.

## Current boot gate

Read-only `check-only --no-desktop` on boot
`85152730-d7e1-49d9-b65d-00d01dbdada5` reports:

```text
kernel=7.0.0-34-generic
loaded_srcversion=354DCD95A5804DE00E20EFB
BAR2_HOST_CPU_PTE_COUNT=0
BASELINE_CHECK_PASS
DEPLOYMENT_VERIFIED=false
RUN_ELIGIBLE=false
```

The corrected current-boot scan is clean for the signatures it checks, but the
loaded module is still the old diag4-v3 identity, no reviewed deployment
manifest is present, and this service has no `DISPLAY`. This result does not
authorize a GPU run. A logged-in desktop and exact reviewed deployment
provenance remain required.

## Development-attempt note

The first combined unittest invocation was made from the repository root and
failed to import the supervisor because its test directory was not on
`PYTHONPATH`. The first draft of the new mocks then exposed a test-helper
context-manager/binding mistake. Both were corrected; the exact invocations
above are the final passing runs. No production code was changed by either
failed test attempt.
