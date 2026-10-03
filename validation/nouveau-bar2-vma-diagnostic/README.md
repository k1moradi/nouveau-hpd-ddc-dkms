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

After the last `nvkm_done()`, the test logs only the cached
`range_source=last_observed` range. It does not query the VMA until a new
`nvkm_kmap()` has succeeded. Object pointer values are short-lived diagnostic
labels; they are not allocation identity across destruction, and matching a
fault address to a VMA range is numerical address-range correlation only.

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
for exact CPU test and W=1 build commands, module hashes, and the current
clean-boot gate result.

The enabled module build linked successfully, but the resulting module was
not installed, signed, loaded, or placed in an initramfs. No GPU selftest or
VA-API workload ran. This is correlation instrumentation, not a BAR2/PTE
repair. `main` remains on HOLD.
