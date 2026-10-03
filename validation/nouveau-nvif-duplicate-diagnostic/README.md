# Nouveau NVIF duplicate-layer diagnostic

**Status: source/build diagnostic candidate only.** This patch distinguishes
the two source paths that can produce the stage-4 BSP `-EEXIST` observed by
the v5 constructor probe:

- `layer=abi16`: the per-client ABI16 object-key list rejected the key before
  calling into NVKM;
- `layer=nvkm`: NVKM constructed the object but rejected insertion into its
  per-client object tree.

The messages are compiled only when
`CONFIG_DRM_NOUVEAU_SELFTEST` is enabled and run only on the existing duplicate
error branches. They report the object key and class; the ABI16 record also
reports the channel token. The diagnostic does not add object lookups,
allocations, references, locks, waits, recovery, or a change to return values.

## Patch inputs

The patch is intended to apply after the reviewed Ubuntu 7.0.0-34 v1-v8 stack.
The pinned pre-patch files are:

```text
drivers/gpu/drm/nouveau/nouveau_abi16.c
3d4c81dd87c8e426b7e948eb448a49075d1df54ab22c68b8e181a0b88bd28fec

drivers/gpu/drm/nouveau/nvkm/core/ioctl.c
2a18013840cee2298edadd19171b083cfed2bb7227a13303ac9afad34a107e18
```

## What a runtime result would establish

```text
layer=abi16
    -> the duplicate is in the per-file ABI16 key registry

layer=nvkm
    -> the duplicate is in NVKM's object tree

no diagnostic marker with an observed -EEXIST
    -> the instrumentation or another return site needs review
```

An ABI16 result is consistent with the Mesa wrong-fd `DEL` candidate because
channel teardown destroys NVIF child objects but does not remove the separate
ABI16 key record. It would still not by itself prove that the `DEL` used the
wrong fd or that the allocator reused decoder A's key. Those values require
paired userspace NEW/DEL logging.

This is diagnostic-only; it does not fix the error. The current runtime boot
has pre-existing PROP/RT, CTXSW, channel-kill, and failed-to-idle signatures,
so no live run is authorized by this artifact. Do not install it or run a GPU
workload until a separate clean-boot gate passes. `main` remains HOLD.

## Validation

The source-contract test strictly applies the patch with zero fuzz and checks
that both markers remain inside their duplicate-only branches:

```bash
NOUVEAU_KERNEL_SOURCE_ROOT=/path/to/v1-v8-linux-source \
  PYTHONDONTWRITEBYTECODE=1 \
  python3 -m unittest -v tests/test_nvif_duplicate_diagnostic.py
```

This test is not a substitute for the diagnostic-enabled kernel compile or a
runtime measurement. No module installation, initramfs update, or reboot is
part of this patch review.

The patch has since passed a clean diagnostic-enabled Nouveau module build and
the two changed translation units also compile with the diagnostic guard
disabled. Exact commands and artifacts are recorded in
[`BUILD-VALIDATION.md`](BUILD-VALIDATION.md). Runtime behavior remains
untested.
