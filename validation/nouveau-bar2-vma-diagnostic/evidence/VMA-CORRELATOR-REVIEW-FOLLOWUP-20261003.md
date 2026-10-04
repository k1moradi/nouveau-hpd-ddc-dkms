# BAR2 correlator lifecycle review follow-up

## Finding and scope

The external review supplied a truncated synthetic journal sequence that
started at `nested_acquired`, omitted both `allocated` and `kmap_acquired`,
then included later cleanup records. The previous parser produced
`ONE_NUMERIC_ACTIVE_VMA_CANDIDATE` for a fault inside that partial interval.
This was reproduced against the pre-change parser before editing.

This follow-up changes only the offline correlator, its synthetic tests, and
documentation. It does not change kernel patch 0010 or any module. No VA/GPU
workload or player was started; no module, package, initramfs, or boot state was
changed; no reboot was initiated.

## Changes

- A complete lifecycle must start with `allocated src=unavailable`, contain
  only an ordered prefix of the recorded success stages, and end with
  `destroying` followed by `destroyed`.
- A selftest may fail early. Its observed stage prefix may therefore be
  shorter, but it cannot skip or reorder stages. Cleanup must still be
  complete. A fault in the unlogged release interval remains inconclusive.
- An active numeric candidate is rejected when its lifecycle is incomplete.
- The no-match outcome is now
  `NO_OBSERVED_ACTIVE_V3_VMA_MATCH`; it means only that the supplied input has
  no observed active range containing the numeric fault address. It does not
  prove journal completeness or absence of an unobserved mapping.
- `allocated` records must use the source emitted by the current kernel patch,
  `unavailable`. VMA and fault records reject unknown fields; raw fault
  registers are range-checked to their emitted widths.
- The synthetic complete lifecycle now matches the patch's initial
  `allocated src=unavailable` record.

## Validation

The saved review counterexample is covered by
`test_missing_initial_map_stages_cannot_yield_numeric_candidate`; separate
tests cover a missing allocation record, reordered stages, valid early failure,
the emitted allocation source, and the weaker no-observed-match label.

The full CPU-only validation used these commands:

```sh
cd /home/keivan/nouveau-hpd-ddc-dkms/validation/detached-mpv-supervisor/tests
PYTHONPATH=.. PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  test_bar2_baseline.py test_detached_mpv_supervisor.py
# 51/51 passed, no skips

cd /home/keivan/nouveau-hpd-ddc-dkms
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  validation/nouveau-bar2-vma-diagnostic/tests/test_correlate_v3.py
# 24/24 passed, no skips

NOUVEAU_VMA_BASE_ROOT=/home/keivan/nouveau-vaapi-app-validation/master-handoff-build-20261002/source-gitapply-stack-v8-20261002 \
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  validation/nouveau-bar2-vma-diagnostic/tests/test_bar2_vma_diagnostic.py
# 8/8 passed, no skips

cd /home/keivan/nouveau-vaapi-app-validation/mpv-full-visible-20261001T155451Z/pixelation-reference-20261002
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_compare_hw_nv12.py
# 8/8 passed, no skips
```

Total: **91/91 CPU tests passed, zero skips**. The saved three-line BAR2
baseline fixture matched all three faults. The source-contract suite still
checks that the kernel instrumentation uses pre-read fault registers and
never queries a VMA after the final map reference is released. Python AST
parsing and `git diff --check` passed.

## Current runtime boundary

The latest read-only check on boot
`85152730-d7e1-49d9-b65d-00d01dbdada5` reported kernel
`7.0.0-34-generic`, loaded Nouveau srcversion
`354DCD95A5804DE00E20EFB`, and BAR2/HOST_CPU/PTE count zero. It reported
`BASELINE_CHECK_PASS`, `DEPLOYMENT_VERIFIED=false`, and `RUN_ELIGIBLE=false`.
This remains baseline information only: the old diag4-v3 module is loaded,
the reviewed deployment manifest is absent, and this service has no desktop
`DISPLAY`. No hardware experiment was performed.

The parser reports numeric correlation only. It cannot identify an allocation
or establish that the hardware used a matching mapping. The final cleanup
release timestamp is still absent from kernel patch 0010; the parser treats
the affected interval conservatively. A full diagnostic-disabled module link
and final deployment manifest also remain pre-deployment gates.

`main` remains HOLD.
