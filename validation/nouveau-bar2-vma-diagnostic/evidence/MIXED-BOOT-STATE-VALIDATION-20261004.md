# BAR2 VMA correlator mixed-boot state validation

## Scope

This CPU-only follow-up fixes a parser-state isolation defect found in review.
The VMA `src`/`rc` cache validator now keys state by `(boot_id, test_id)`;
test IDs are local to one boot and cannot carry a cached VMA snapshot across
boots. Error messages include both identifiers. Two regressions cover
cross-boot cache contamination and complete lifecycles that reuse the same
test ID. The kernel diagnostic patch and supervisor were not changed.

No VA/GPU workload or player was started. No module, package, initramfs, or
boot state was changed, and no reboot was initiated.

## CPU validation

All suites completed with zero skips:

```sh
cd /home/keivan/nouveau-hpd-ddc-dkms/validation/detached-mpv-supervisor/tests
PYTHONPATH=.. PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  test_bar2_baseline.py test_detached_mpv_supervisor.py
# 51/51 passed

cd /home/keivan/nouveau-hpd-ddc-dkms
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  validation/nouveau-bar2-vma-diagnostic/tests/test_correlate_v3.py
# 35/35 passed

NOUVEAU_VMA_BASE_ROOT=/home/keivan/nouveau-vaapi-app-validation/master-handoff-build-20261002/source-gitapply-stack-v8-20261002 \
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  validation/nouveau-bar2-vma-diagnostic/tests/test_bar2_vma_diagnostic.py
# 8/8 passed

cd /home/keivan/nouveau-vaapi-app-validation/mpv-full-visible-20261001T155451Z/pixelation-reference-20261002
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_compare_hw_nv12.py
# 8/8 passed
```

Total: **102/102 CPU tests passed, zero skips**.

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

## Limits

This fixes mixed-boot input validation only. It does not change the numeric
address correlation limits, prove journal completeness, identify an owning
allocation, or establish fault causation. No hardware correlation was run.
The current boot's previously recorded BAR2/HOST_CPU/PTE event and old loaded
module remain disqualifying for a controlled experiment. The reviewed module
is undeployed, and `main` remains HOLD.

## Artifact hashes

```text
3b90b9d47fc394f33194e82070ea3fbd22a457dddb6a753e90dbf8b1071483dd  validation/nouveau-bar2-vma-diagnostic/correlate_v3.py
55a2230fa47274b6cff578ef10822684291879a21a051c3ccbf3dec4d0d45486  validation/nouveau-bar2-vma-diagnostic/tests/test_correlate_v3.py
472886814c7dd67ff2685bc50791030f24f4267f6a22be16f57ce5c9e1b4818e  validation/nouveau-bar2-vma-diagnostic/patches/0010-drm-nouveau-correlate-instmem-vma-selftest.patch (unchanged)
```
