# Detached MPV supervisor and BAR2 baseline gate

This directory freezes the CPU-tested supervisor and the literal BAR2/HOST_CPU/PTE
baseline matcher. The runner is prepared for a later logged-in desktop experiment;
it has not been armed or run here.

## Safety boundary

Do not use `start --arm` until the reviewed module is installed under its distinct
identity, installed and initramfs provenance is pinned, a fresh desktop boot has
the required module identity, and the complete current-boot preflight reports zero
BAR2/HOST_CPU/PTE and zero hard-stop signatures. A headless service has no `DISPLAY`
and must not launch this workload. `self-test` creates only a CPU dummy process tree.

The follower reads the kernel journal and stops the complete MPV process group on
the first BAR2/HOST_CPU/PTE, any Nouveau FIFO MMU fault, `SCHED_ERROR 0a`, or the
other listed GPU/kernel hard-stop signatures. Concurrent stop requests are
serialized. After stopping, the supervisor passively observes the journal for 30
seconds and saves a bounded delta; it does not restart or continue the workload.

## CPU-only validation

From this directory:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 python3 detached_mpv_supervisor.py self-test
```

The tests use `preflight-correction.txt`, which preserves the three saved BAR2
PTE records, and `tests/mpv-vaapi-positive-fixture.log`, which contains only
the positive decoder-classification lines needed by the unit test.

The supervisor embeds this machine's pinned input/plugin paths and hashes. It is
not a portable launcher. Its current expected Nouveau srcversion is also a
historical pin; after any reviewed deployment, create a new explicit module
manifest rather than accepting whichever module happens to be loaded. See
`evidence/CPU-VALIDATION-20261003.md` for the exact results and the current
no-run boundary.
