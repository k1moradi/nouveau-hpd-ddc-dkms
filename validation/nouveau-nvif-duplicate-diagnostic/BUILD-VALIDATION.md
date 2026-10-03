# NVIF duplicate-layer diagnostic build validation

This records CPU-only source/build validation for patch 0009. It does not
claim runtime validation, fix the BAR2/PTE issue, or authorize installation.

## Pinned inputs

The patch applies after the v1-v8 review stack in
`review/gk104-vaapi-selftest-v8-20261002`.

```text
patch SHA-256
442b14bfb53201dc7c9e7ac8bcfd6a1f74bb94280a8d766ffc119bcf20d327fc

pre-patch nouveau_abi16.c SHA-256
3d4c81dd87c8e426b7e948eb448a49075d1df54ab22c68b8e181a0b88bd28fec

pre-patch nvkm/core/ioctl.c SHA-256
2a18013840cee2298edadd19171b083cfed2bb7227a13303ac9afad34a107e18
```

The patch was applied with `patch --fuzz=0` to two separate copies of that
source stack. Each copy was cleaned before its build. The scratch trees and
complete logs are retained outside this repository under:

```text
/home/keivan/nouveau-vaapi-app-validation/nvif-duplicate-buildcheck-20261002/
```

## Diagnostic-enabled build

Command, with the scratch source path abbreviated as `$M`:

```bash
nice -n 10 make \
  -C /usr/src/linux-headers-7.0.0-34-generic \
  M="$M" CONFIG_DRM_NOUVEAU=m \
  KCFLAGS=-DCONFIG_DRM_NOUVEAU_SELFTEST=1 W=1 -j2 modules
```

Result: **PASS**, exit status 0. The resulting `nouveau.ko` has:

```text
SHA-256  84427a962c56399f81b10c40676b24add4e0ca5be3847599f7e75bfd6fcad75d
srcversion 29C4D0E409ABB2711FC9A10
vermagic 7.0.0-34-generic SMP preempt mod_unload modversions
```

Both ABI16 and NVKM diagnostic strings are present in the enabled module.

The log contains the existing compiler-name and pahole-version mismatch
notices, plus BTF skipped because `vmlinux` is unavailable. It contains no
compile error and no patch-attributed compiler warning.

## Diagnostic-disabled guard build

The two changed translation units were compiled without the selftest define:

```bash
nice -n 10 make \
  -C /usr/src/linux-headers-7.0.0-34-generic \
  M="$M" CONFIG_DRM_NOUVEAU=m W=1 -j2 \
  nouveau_abi16.o nvkm/core/ioctl.o
```

Result: **PASS**, exit status 0. Neither resulting object contains the
`NOUVEAU_DIAG_NVIF_DUP` strings, confirming that the diagnostic code is
compiled out when the selftest define is absent.

## Source test

Run against the pinned v1-v8 source tree:

```bash
PYTHONDONTWRITEBYTECODE=1 \
NOUVEAU_KERNEL_SOURCE_ROOT=/home/keivan/nouveau-vaapi-app-validation/master-handoff-build-20261002/source-gitapply-stack-v8-20261002 \
python3 -m unittest -v tests/test_nvif_duplicate_diagnostic.py
```

Result: **2/2 PASS**. The tests verify both pinned source hashes and strict
zero-fuzz patch application plus the two duplicate-only log sites.

## Scope boundary

No module was installed, replaced, loaded, or unloaded. No initramfs was
updated, no reboot occurred, and no GPU workload or selftest was run. The
diagnostic identifies which existing `-EEXIST` return path fires; it does not
correct that path and is not a BAR2/PTE fix.
