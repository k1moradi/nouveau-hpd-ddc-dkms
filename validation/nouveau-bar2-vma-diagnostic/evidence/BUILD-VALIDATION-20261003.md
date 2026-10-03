# CPU-only supervisor and BAR2 VMA diagnostic validation

## Scope

This checkpoint adds a fail-closed kernel-journal visibility check to the
detached MPV supervisor, clarifies that unpinned `check-only` is only a
baseline check, and adds diagnostic-only BAR2 fault/VMA correlation for v3.
It contains no production Nouveau change.

No VA/GPU workload was started. No module was installed, loaded, or unloaded;
the initramfs and packages were not changed; no reboot occurred.

## CPU tests

From `validation/detached-mpv-supervisor/`:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
  python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 \
  python3 detached_mpv_supervisor.py self-test
```

Result: **47/47 tests passed, no skips**. This includes the five saved BAR2
baseline matcher cases. The three preserved fault lines match their literal
BAR2/HOST_CPU/PTE signatures, while the `--require-zero` gate rejects them.
The supervisor tests cover visible current-boot kernel journal metadata,
empty/inaccessible/mismatched journals, explicit run eligibility output,
process-group stop behavior, and late BAR2 failures.

The synthetic supervisor check reported:

```text
SUPERVISOR_SELF_TEST_PASS matcher=pass hard-stop-cases=18 journal-trigger=pass process-group-tree=stopped
```

The VMA diagnostic source-contract command was:

```sh
NOUVEAU_VMA_BASE_ROOT=/home/keivan/nouveau-vaapi-app-validation/master-handoff-build-20261002/source-gitapply-stack-v8-20261002 \
PYTHONDONTWRITEBYTECODE=1 \
  python3 -m unittest -v \
  validation/nouveau-bar2-vma-diagnostic/tests/test_bar2_vma_diagnostic.py
```

Result: **8/8 passed, no skips**. The checks pin the four source inputs,
require zero-fuzz patch application, enforce active-map-ref VMA lookup, forbid
VMA queries after the last map reference is dropped, verify the IRQ log uses
only the four existing MMIO values before recovery, and check that no
perturbative mapping helper is used.

`checkpatch.pl --no-tree --strict` reported zero errors, warnings, or checks.
Python AST parsing and `git diff --check` passed.

The first enabled build attempt exposed that a local variable named `current`
collided with the kernel's `current` macro. The draft was rejected and changed
to `current_vma`; the final patch passes the W=1 build below.

## Build provenance

Target: `7.0.0-34-generic`; installed matching headers;
`CONFIG_DRM_NOUVEAU=m`; GCC `15.2.0-16ubuntu1`; serial `-j1`, `nice -n 10`.
The source stack is v1-v8 plus patch 0009 and this patch 0010. Full logs are
retained in this directory.

Diagnostic-enabled full module command:

```sh
nice -n 10 make \
  -C /usr/src/linux-headers-7.0.0-34-generic \
  M=/tmp/nouveau-v3-bar2-build-20261003/source/drivers/gpu/drm/nouveau \
  CONFIG_DRM_NOUVEAU=m \
  KCFLAGS=-DCONFIG_DRM_NOUVEAU_SELFTEST=1 W=1 -j1 modules
```

The final enabled build completed with **exit 0** and linked `nouveau.ko`.
Its SHA-256 was
`42558c59f262ef8a50b24ccd65c46678d3c481b660af08f2a008aa111d1dd92b`,
size `248047176` bytes, srcversion `936407678F3DA1E8515F5EC`, and vermagic
`7.0.0-34-generic SMP preempt mod_unload modversions`. The enabled module
contained the v3 VMA, raw BAR2 fault, and ABI16/NVKM duplicate-layer strings.
The log contains only the known compiler-name and pahole-version mismatch
notices plus BTF skipped because `vmlinux` is unavailable; no patch-attributed
compiler warning was found.

Diagnostic-disabled guard check, after `make clean`, compiled all three
changed C translation units with `W=1` and no selftest define:

```sh
nice -n 10 make \
  -C /usr/src/linux-headers-7.0.0-34-generic \
  M=/tmp/nouveau-v3-bar2-build-20261003/source/drivers/gpu/drm/nouveau \
  CONFIG_DRM_NOUVEAU=m W=1 -j1 \
  nouveau_debugfs.o nvkm/engine/fifo/gf100.o \
  nvkm/subdev/instmem/nv50.o
```

Result: **exit 0**. The disabled check is a changed-translation-unit compile,
not a second full module link. No `NOUVEAU_DIAG_V3_VMA` or
`NOUVEAU_DIAG_BAR2_FAULT` strings were present in those objects.

## Current boot gate

The read-only command
`python3 validation/detached-mpv-supervisor/detached_mpv_supervisor.py check-only --no-desktop`
returned:

```text
kernel=7.0.0-34-generic
loaded_srcversion=354DCD95A5804DE00E20EFB
BAR2_HOST_CPU_PTE_COUNT=1
PREFLIGHT_FAIL
RUN_ELIGIBLE=false
error=clean-boot BAR2/HOST_CPU/PTE baseline required; found 1
error=clean-boot hard-stop baseline required; found 1
```

The observed fault on the old diag4-v3 module is the unattributed
BAR2/HOST_CPU/PTE read at `0x471000`, with `channel -1 [00ffbb7000 unknown]`.
This boot is not eligible for v3, MPV, ffplay, or pixel capture. The reviewed
module deployment and a clean zero-hard-stop baseline are still required.

## Pixelation evidence

The user-reported screenshot SHA-256 is
`6a08474969bb974c8868fa2a3adda6676799af964b4362dd248509c173e5e270`. It is
visually coherent on inspection, but that does not rule out a transient
artifact. The approximate screenshot/software comparison around 530 seconds
is not frame-synchronized (MAE `2.528`, RMSE `5.213`, SSIM-style score
`0.996215`).

Exact software NV12 references exist at PTS `530021`, `530063`, `530105`,
`530146`, and `530188` in time base `1/1000`; no hardware NV12 capture exists
at those timestamps. The only raw hardware/software pairs are early controls
at 1.750 s and 8.375 s: 7 and 2 one-level Y differences respectively, with
exact UV. The reported late pixelation remains unresolved.

`main` remains HOLD. No BAR2/PTE production fix is claimed.
