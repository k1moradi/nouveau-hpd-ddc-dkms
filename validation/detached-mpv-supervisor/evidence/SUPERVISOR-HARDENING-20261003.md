# Detached MPV supervisor hardening evidence

## Scope and safety

This CPU-only checkpoint updates the supervisor on
`review/gk104-vaapi-selftest-v8-20261002`, based on parent commit
`f8ac22b1fee5b3a771735d8f6a9a49b94d5f5cc1`. It does not change or promote
`main` (`7b44b1c1e282eac7c54c1cfa8c758118cd66312c`).

No VA/GPU workload was started. No module, parameter, package, initramfs, or
boot change was made by this continuation. The user had rebooted before this
work began; no reboot was initiated here.

## Supervisor changes

- `start` requires both `--arm` and a strict deployment manifest. The detached
  `run` action consumes a one-use manager token before capturing a journal
  cursor, so direct service invocation refuses before reaching MPV.
- A successful result requires natural MPV completion, exit status zero, a
  completed passive journal tail, confirmed Nouveau VAAPI H.264 decode, the
  pinned VA DSO mapped in MPV, and no cleanup or kernel failure. Operator stop
  or an incomplete tail is nonzero.
- The deployment manifest pins kernel, installed module path/hash/srcversion/
  vermagic, the manifest-named initramfs and embedded Nouveau module hashes,
  the exact `diag_ctxsw=N` setting, VA DSO and alias, MPV, and input. Runtime
  checks use the selected `modinfo` module and verify `/proc/<pid>/maps` by
  path, device, inode, and hash.
- `diag_ctxsw` is mode `0600` on this host. If ordinary sysfs reading is
  denied, the manifest verifier tries only `sudo -n /usr/bin/cat`; it fails
  closed if noninteractive read access is unavailable. Tests mock this path;
  this continuation did not invoke sudo for the parameter.
- The child receives an allowlisted environment. The desktop gate requires an
  active local user X11 session on `seat0` and a positive VT. Process-group
  cleanup checks for survivors after SIGKILL. Journal sync is required before
  cursor capture and the final snapshot.
- MPV screenshot IPC records `time-pos` before requesting the screenshot and
  refuses to label a screenshot if that timestamp is missing or invalid.

## CPU validation

From `validation/detached-mpv-supervisor/`:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 python3 detached_mpv_supervisor.py self-test
PYTHONDONTWRITEBYTECODE=1 python3 tests/test_bar2_baseline.py -v
PYTHONDONTWRITEBYTECODE=1 python3 -c 'import ast,pathlib; ast.parse(pathlib.Path("detached_mpv_supervisor.py").read_text()); ast.parse(pathlib.Path("tests/test_detached_mpv_supervisor.py").read_text()); print("AST_PARSE=PASS (2 files)")'
git diff --check
```

Results:

- **39/39 unit tests passed**, no skips. This includes the three exact saved
  BAR2/PTE records, arm-token refusal/replay, deployment manifest mismatches,
  process-group survivor handling, protected sysfs access, desktop gating,
  DSO mapping, environment allowlisting, incomplete-tail behavior, and
  timestamped screenshot IPC.
- The separate BAR2 test command passed **5/5**. It confirms addresses
  `0x3f3000`, `0x5d6000`, and `0x5da000` match and that `--require-zero`
  rejects the saved contaminated fixture.
- The supervisor self-test reported
  `SUPERVISOR_SELF_TEST_PASS matcher=pass hard-stop-cases=18 journal-trigger=pass process-group-tree=stopped`.
- AST parsing passed for the implementation and test file. `git diff --check`
  passed. No `__pycache__` directory remained in the package.
- One standalone matcher invocation initially omitted the `tests/` path and
  failed to locate the file; the corrected command above passed. No source or
  fixture was changed as a result.

## Current boot check

Read-only state at `2026-10-03T10:35:11-07:00`:

```text
boot_id=03f7b214-ea03-4be8-af92-f6a073942e9d
kernel=7.0.0-34-generic
modinfo_filename=/lib/modules/7.0.0-34-generic/updates/dkms/nouveau.ko.zst
loaded_srcversion=354DCD95A5804DE00E20EFB
modinfo_vermagic=7.0.0-34-generic SMP preempt mod_unload modversions
installed_module_sha256=88abb555831095d6a71ff4db6378c3d3ec2ee13cd99a153e4eaff31397820a39
DISPLAY=unset
```

`check-only --no-desktop` returned 0 with
`BAR2_HOST_CPU_PTE_COUNT=0` and `PREFLIGHT_PASS`. The three-line fixture tests
also passed. The loaded module is still the old `diag4-v3` build, not the
reviewed v1-v8 plus duplicate-layer diagnostic module. There is no matching
deployment manifest, and this service has no desktop display. Therefore this
is only a clean current-boot matcher/preflight result; it is not an eligible
GPU-test boot and does not validate the reviewed module.

## Pixelation evidence

The user-reported screenshot is
`mpv-full-visible-20261001T155451Z/desktop-user-reported-pixelation.png`
(SHA-256
`6a08474969bb974c8868fa2a3adda6676799af964b4362dd248509c173e5e270`, captured
2026-10-01 09:03:49 PDT). Visual inspection shows a coherent frame without an
obvious macroblock grid. The screenshot is not synchronized to a decoder PTS.

For a bounded approximate comparison, the active screenshot crop
`(0,162)-(1920,979)` and the software-only reference crops
`(0,131)-(1920,948)` were converted to luma and downsampled with Pillow BOX to
480x204. Against `soft-017.png` (approximately media 530.000 s), MAE was
2.448/255 and RMSE 5.514/255. Against `soft-018.png` (approximately 530.250 s),
MAE was 2.456/255 and RMSE 5.517/255. These are nearby software frames, not an
exact hardware-frame comparison. The prior 24-fps search found its best
approximate match at 530.083 s (MAE 2.528, RMSE 5.213); its amplified residual
follows scene edges and shows no obvious square block pattern.

An exact-PTS software NV12 reference exists at PTS 530021, 530063, 530105,
530146, and 530188 (time base 1/1000; combined SHA-256
`7773decbceeb9f9d39a2538da54e797e9934e723f7ec734ed7d0a931ded0af09`). No
hardware NV12 frame was retained at those timestamps, so the reported late
artifact remains unresolved.

The saved matched hardware/software raw pairs are only early controls:

- PTS 42 (1.750 s): 7 Y bytes differ by one level, UV identical.
- PTS 201 (8.375 s): 2 Y bytes differ by one level, UV identical.

These are far from the reported ~530 s scene and do not explain it. The
pixelation cause remains open.

## Review boundary

This checkpoint hardens CPU-only tooling. Before any GPU run, a reviewed
diagnostic module must be deployed with a matching post-install manifest and
initramfs evidence, followed by a logged-in desktop boot with zero BAR2/PTE and
hard-stop baseline. `main` remains HOLD.
