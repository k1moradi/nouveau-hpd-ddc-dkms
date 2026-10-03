# Runtime harness hardening checkpoint

This checkpoint strengthens the CPU-side MPV NVIF lifetime capture and pair
correlator. It does not contain GPU/runtime evidence and does not authorize
installing either Mesa DSO or the diagnostic kernel module.

## Changes

- The runner fingerprints the Nouveau module file selected by `modinfo` after
  installation/signing/compression. It records the resolved canonical path,
  compressed/signed file SHA-256, module srcversion, and vermagic; it rejects a
  selected module whose srcversion does not match the loaded module or whose
  vermagic targets another kernel.
- The pair parser re-hashes the recorded module file, requires canonical path
  spelling, and requires both runs to use the same module file identity.
- Kernel hard stops are classified only from `_TRANSPORT=kernel` journal
  records. Workload `SIGBUS`/`Bus error` records are recognized only under the
  `nouveau-nvif-lifetime` systemd-cat identifier. Unrelated userspace strings
  containing `WARNING`, `BAR2`, or `PTE` do not contaminate the kernel result.
- Kernel hard stops take precedence over workload errors and NVIF lifecycle
  results. A tagged workload fatal also prevents an A/B success result.
- Process cleanup monitors the process group after the launcher exits and
  verifies that no live non-zombie descendant remains after signal escalation.

## Validation

The focused CPU suites passed **66/66** tests:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  tests/test_capture_nvif_lifetime_run.py \
  tests/test_parse_nvif_lifetime_ab.py
```

Python syntax compilation passed for the capture runner, pair parser, and both
test modules. `git diff --check` passed. No module install/load, initramfs
update, reboot, or GPU/video workload was performed for this checkpoint.

## Provenance limitation

The installed module-file hash is generated from the selected file during each
runtime capture and is rechecked during later parsing. A separately approved
deployment manifest containing the expected signed/compressed module hash
cannot be created until the reviewed diagnostic module is installed. Until
that deployment artifact exists, this checkpoint proves observed A/B module
identity and capture-to-analysis file stability, not conformance to a
pre-pinned installed binary hash.
