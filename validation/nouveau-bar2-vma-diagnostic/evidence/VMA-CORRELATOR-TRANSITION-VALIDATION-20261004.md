# BAR2 VMA correlator transition validation

## Scope

This CPU-only checkpoint tightens the offline correlator after review of the
v3 lifecycle state machine. It changes the correlator, its synthetic tests,
and its documentation only. Kernel patch 0010 and the supervisor implementation
were not changed. No VA/GPU workload or player was started; no module, package,
initramfs, or boot state was changed; no reboot was initiated.

## Review corrections

- Lifecycle validation now uses explicit legal stage edges and reachable
  cleanup predecessors. A completed trace ending `released -> destroying ->
  destroyed` is rejected because the pinned selftest reacquires with
  `nvkm_kmap()` and records `reacquired` before any subsequent failure cleanup.
- Faults in `allocated -> kmap_acquired`, `released -> reacquired`, and
  `destroying -> destroyed` are reported as explicit inconclusive transition
  windows. These are timing classifications only; they do not identify an
  owner or establish causation.
- Other impossible stage transitions are reported as
  `INCONCLUSIVE_TEST_LIFECYCLE_INCOMPLETE` and cannot yield a numeric candidate.
- The parser validates `src`/`rc` against the pinned logger behavior for each
  test ID: allocated and non-refresh records have `rc=0`; `current` is a
  successful refresh; `last_observed` follows a previously observed current
  snapshot and a failed refresh; and `unavailable` is a failed refresh before
  any current snapshot.
- The final map-reference cleanup still has no per-`nvkm_done()` log in patch
  0010. The parser treats the last active snapshot to `destroying` as a release
  transition and never calls a cached range current. No kernel patch change was
  made in this checkpoint.

## CPU validation

Commands were run against the tracked supervisor, current correlator, pinned
kernel source baseline, and retained PTS comparator. All suites completed with
zero skips:

```sh
cd /home/keivan/nouveau-hpd-ddc-dkms/validation/detached-mpv-supervisor/tests
PYTHONPATH=.. PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  test_bar2_baseline.py test_detached_mpv_supervisor.py
# 51/51 passed; includes the saved three-line BAR2 fixture and 0x471000 case

cd /home/keivan/nouveau-hpd-ddc-dkms
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  validation/nouveau-bar2-vma-diagnostic/tests/test_correlate_v3.py
# 33/33 passed

NOUVEAU_VMA_BASE_ROOT=/home/keivan/nouveau-vaapi-app-validation/master-handoff-build-20261002/source-gitapply-stack-v8-20261002 \
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  validation/nouveau-bar2-vma-diagnostic/tests/test_bar2_vma_diagnostic.py
# 8/8 passed

cd /home/keivan/nouveau-vaapi-app-validation/mpv-full-visible-20261001T155451Z/pixelation-reference-20261002
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_compare_hw_nv12.py
# 8/8 passed
```

Total: **100/100 CPU tests passed, zero skips**.

Additional checks:

```sh
cd /home/keivan/nouveau-hpd-ddc-dkms/validation/detached-mpv-supervisor
PYTHONDONTWRITEBYTECODE=1 python3 detached_mpv_supervisor.py self-test
# SUPERVISOR_SELF_TEST_PASS matcher=pass hard-stop-cases=18 journal-trigger=pass process-group-tree=stopped

cd /home/keivan/nouveau-hpd-ddc-dkms
python3 - <<'PY'
import ast
from pathlib import Path
paths = [
    Path('validation/nouveau-bar2-vma-diagnostic/correlate_v3.py'),
    Path('validation/nouveau-bar2-vma-diagnostic/tests/test_correlate_v3.py'),
    Path('validation/detached-mpv-supervisor/detached_mpv_supervisor.py'),
    Path('validation/detached-mpv-supervisor/tests/test_bar2_baseline.py'),
    Path('validation/detached-mpv-supervisor/tests/test_detached_mpv_supervisor.py'),
]
for path in paths:
    ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
print(f'AST_PARSE_PASS files={len(paths)}')
PY

git diff --check
find validation/nouveau-bar2-vma-diagnostic validation/detached-mpv-supervisor \
  -type d -name __pycache__ -print
# no output; no generated __pycache__ directories
```

The read-only baseline command was:

```sh
cd /home/keivan/nouveau-hpd-ddc-dkms/validation/detached-mpv-supervisor
PYTHONDONTWRITEBYTECODE=1 python3 detached_mpv_supervisor.py check-only --no-desktop
```

It reported kernel `7.0.0-34-generic`, loaded Nouveau srcversion
`354DCD95A5804DE00E20EFB`, `BAR2_HOST_CPU_PTE_COUNT=0`,
`BASELINE_CHECK_PASS`, `DEPLOYMENT_VERIFIED=false`, and `RUN_ELIGIBLE=false`.
This is baseline-only information from the old diag4-v3 module, not approval
to run hardware tests.

## Limits and remaining gates

The correlator output remains numeric-address correlation only. It does not
prove allocation identity, hardware mapping use, or fault causation. The input
journal's completeness is not proven by the parser. No hardware VMA correlation
was performed. The current service is headless and the reviewed diagnostic
module remains undeployed. A pristine full diagnostic-enabled and
full diagnostic-disabled W=1 module build, reviewed deployment manifest,
installed/initramfs provenance, a visible logged-in desktop, and a zero-hard-stop
boot remain prerequisites before any GPU experiment. `main` remains HOLD.

## Artifact hashes at validation

```text
85c166fdb50eaf09078c9f2c0593abfc9a7b4ebddbaad84bf9d4086af6187ff1  validation/nouveau-bar2-vma-diagnostic/README.md
c79b18b725f72806c6b3da69967bf09c2e4507ea38f34c6a7187147193f0d657  validation/nouveau-bar2-vma-diagnostic/correlate_v3.py
1f1212d74f394c35b9588610d52e49321603366e3ed97fd290bfae1521ad8036  validation/nouveau-bar2-vma-diagnostic/tests/test_correlate_v3.py
472886814c7dd67ff2685bc50791030f24f4267f6a22be16f57ce5c9e1b4818e  validation/nouveau-bar2-vma-diagnostic/patches/0010-drm-nouveau-correlate-instmem-vma-selftest.patch (unchanged)
```
