# BAR2 fault and v3 VMA correlation diagnostic

This package contains diagnostic-only Linux Nouveau instrumentation for the
v3 private instmem lifecycle. It does not modify mappings or PTEs and does not
walk VMM state from interrupt context.

## Diagnostic behavior

The IRQ-side `NOUVEAU_DIAG_BAR2_FAULT` record uses the four register values
already read by `gf100_fifo_intr_mmu_fault_unit()`. It adds no MMIO read,
allocation, lock, sleep, channel search, or VMM lookup, and is emitted before
the existing fault-recovery call.

The v3 process-context records carry a monotonic `test` ID and stages for
allocation, initial and nested maps, release, reacquisition, verification,
and destruction. `nvkm_memory_bar2_vma_snapshot()` is available only in
selftest builds. The NV50 callback takes the instmem mutex and reports the
BAR2 VMA only while the memory object's map refcount is positive and both the
VMA and CPU mapping exist. It does not call the side-effecting
`nvkm_memory_bar2()` helper.

Non-refresh records after `nvkm_done()` use the last observed range when one
exists, otherwise `src=unavailable`; they do not query the VMA. The stage log
after a `nvkm_kmap()` attempt may query the accessor, but the callback reports
`src=current` only if its locked checks see an active map ref, VMA, and CPU
mapping. Object pointer values in this current log format are ignored as
identities by the offline parser.

## Offline fault/VMA correlation

`correlate_v3.py` consumes a saved current-boot kernel journal in JSONL form:

```sh
journalctl -k -b --no-pager -o json > boot-kernel.jsonl
python3 correlate_v3.py boot-kernel.jsonl > v3-correlation.json
```

It requires kernel transport, a single boot ID, and monotonic timestamps on
every diagnostic record. It reconstructs the fault VA from `VAHI:VALO` and
compares it with VMA ranges reported as `src=current`. Cached
`src=last_observed` values never create a match. Failed or unavailable
snapshots during an active-map stage, a changed range while references are
held, equal timestamps, transition windows, overlapping numeric ranges, a
missing final release boundary between the nested map and reacquisition, and
incomplete test lifecycles remain inconclusive. A lifecycle must begin with
`allocated src=unavailable`, follow legal stage transitions, and end with
`destroying` then `destroyed`. Early failure cleanup is allowed only from a
reachable stage; in particular, `released` cannot go directly to cleanup
because the patched selftest calls and logs `nvkm_kmap()` reacquisition before
any later failure path.

The correlator identifies three source-visible transition windows:

| Edge | Outcome | Why the interval is unresolved |
|---|---|---|
| `allocated` → `kmap_acquired` | `INCONCLUSIVE_INITIAL_KMAP_TRANSITION_WINDOW` | The first VMA snapshot is logged only after `nvkm_kmap()` returns. |
| `released` → `reacquired` | `INCONCLUSIVE_REACQUIRE_TRANSITION_WINDOW` | The mapping may be absent or may have become active before the reacquisition record. |
| `destroying` → `destroyed` | `INCONCLUSIVE_DESTROY_TRANSITION_WINDOW` | The memory unref and destruction occur between these records. |

These labels identify timing gaps only. They do not establish that a fault
belongs to the selftest or a particular memory object. Other impossible stage
edges are reported as `INCONCLUSIVE_TEST_LIFECYCLE_INCOMPLETE`. The kernel
patch does not log each cleanup `nvkm_done()`; the range from the last active
snapshot to `destroying` is treated as a release transition, not as a proven
continuously mapped interval.

The parser also enforces the logger's `src`/`rc` contract per test ID:
`allocated` and non-refresh stages report `rc=0`; `current` means a successful
refresh; `last_observed` means a failed refresh after a current snapshot was
seen; and `unavailable` means a failed refresh before any current snapshot.
Cached snapshot state is not reset during one test ID.

`ONE_NUMERIC_ACTIVE_VMA_CANDIDATE` means only that the numeric BAR2 fault VA
fell within a range reported current during an active map-reference interval.
It is **not** proof that a particular allocation caused the fault, that the
hardware used that mapping, or that the fault is related to video. The parsed
instance address is reported separately and is not used for ownership
inference. `NO_OBSERVED_ACTIVE_V3_VMA_MATCH` means no VMA interval present in
the supplied input contained the numeric fault VA; it does not prove the input
is a complete journal or that no unobserved mapping existed. No VMM/PTE state
is read by this analysis. The parser validates the current record field sets,
including 32-bit register widths, and rejects extra fields until the schema is
explicitly updated.

CLI status 0 means at least one BAR2 fault record was parsed and a correlation
report was emitted; it does not mean the fault was explained. Status 3 means
there were no fault records to analyze, status 4 means records span multiple
boots and were not compared, and status 2 means the input failed validation.
An empty fault set is not evidence of a clean or healthy BAR2 path.

The parser's CPU-only regression suite is:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  validation/nouveau-bar2-vma-diagnostic/tests/test_correlate_v3.py
```

## Pinned source and patch

- Target kernel: Ubuntu `7.0.0-34-generic`, source package `7.0.0-34.34`.
- Source archive SHA-256:
  `a874e1fb08d2ee695b08e0c8ce6fd2c76a4bf7ffa98882fbabd233380ef8a85a`.
- Base is the reviewed v1-v8 source stack with duplicate-layer diagnostic
  0009 applied.
- Patch 0009 SHA-256:
  `442b14bfb53201dc7c9e7ac8bcfd6a1f74bb94280a8d766ffc119bcf20d327fc`.
- Patch 0010 SHA-256:
  `472886814c7dd67ff2685bc50791030f24f4267f6a22be16f57ce5c9e1b4818e`.
- The four pre-0010 source hashes are pinned in
  `tests/test_bar2_vma_diagnostic.py`.

Apply patch 0010 at the kernel source root with `git apply --check` followed
by `git apply`; both checks were performed against the pinned v1-v8 source
tree with whitespace errors rejected.

## Validation and limits

See [`evidence/BUILD-VALIDATION-20261003.md`](evidence/BUILD-VALIDATION-20261003.md)
for the earlier CPU test and W=1 build commands and module hashes. The offline
parser and its separate CPU results are recorded in
[`evidence/VMA-CORRELATOR-CPU-VALIDATION-20261003.md`](evidence/VMA-CORRELATOR-CPU-VALIDATION-20261003.md).
The later lifecycle-completeness review correction and updated test counts are
recorded in
[`evidence/VMA-CORRELATOR-REVIEW-FOLLOWUP-20261003.md`](evidence/VMA-CORRELATOR-REVIEW-FOLLOWUP-20261003.md).

The latest transition-window and source/return-code regression results are
recorded in
[`evidence/VMA-CORRELATOR-TRANSITION-VALIDATION-20261004.md`](evidence/VMA-CORRELATOR-TRANSITION-VALIDATION-20261004.md).
The clean full W=1 enabled and disabled module links are recorded in
[`evidence/FULL-MODULE-BUILD-VALIDATION-20261004.md`](evidence/FULL-MODULE-BUILD-VALIDATION-20261004.md).
Both scratch links passed, but neither result was installed, signed, loaded,
or placed in an initramfs. The later current-boot PTE recheck is recorded in
[`evidence/CURRENT-BOOT-PTE-RECHECK-20261004.md`](evidence/CURRENT-BOOT-PTE-RECHECK-20261004.md):
the old module remains loaded and that boot is ineligible for GPU testing.
The reviewed deployment manifest, installed/initramfs provenance, a fresh
eligible boot, and a visible logged-in desktop remain prerequisites. No GPU
selftest or VA-API workload ran. This is correlation instrumentation, not a
BAR2/PTE repair. `main` remains on HOLD.
