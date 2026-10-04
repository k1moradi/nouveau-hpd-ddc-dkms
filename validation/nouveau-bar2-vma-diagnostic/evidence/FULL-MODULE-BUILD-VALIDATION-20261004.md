# Full enabled and disabled module builds

## Scope and safety

This was a CPU-only source/build check in the scratch tree
`/home/keivan/nouveau-vaapi-app-validation/nouveau-vma-fullbuild-20261004`.
The retained source input was
`nvif-duplicate-buildcheck-20261002/source-enabled` (reviewed v1-v8 stack
with duplicate-layer diagnostic 0009). Patch 0010 was applied to the scratch
copy after `git apply --check`; each configuration started with a clean
`make ... clean`.

Pinned source identities:

```text
kernel source archive SHA-256:
  a874e1fb08d2ee695b08e0c8ce6fd2c76a4bf7ffa98882fbabd233380ef8a85a
patch 0009 SHA-256:
   442b14bfb53201dc7c9e7ac8bcfd6a1f74bb94280a8d766ffc119bcf20d327fc
patch 0010 SHA-256:
   472886814c7dd67ff2685bc50791030f24f4267f6a22be16f57ce5c9e1b4818e
```

The four patch-0010 preimage files matched the SHA-256 pins in
`tests/test_bar2_vma_diagnostic.py`. The build output was not installed,
signed, compressed, loaded, or copied into an initramfs. No package or boot
state changed and no VA/GPU workload ran.

## Toolchain

```text
headers package: linux-headers-7.0.0-34-generic 7.0.0-34.34
compiler: gcc (Ubuntu 15.2.0-16ubuntu1) 15.2.0
/usr/bin/gcc SHA-256:
  b5f1b773a7c733738352000c92a077dc5852a1a2fc6d836b1e411be1e9ec5f88
/usr/bin/x86_64-linux-gnu-gcc SHA-256:
  b5f1b773a7c733738352000c92a077dc5852a1a2fc6d836b1e411be1e9ec5f88
pahole: unavailable
```

Kbuild printed a compiler-name warning although both compiler names resolve to
the same executable hash and report the same GCC version. It also reported
that the expected pahole version 131 was unavailable (`pahole` reports as
version 0); BTF generation was skipped because matching `vmlinux` is not
available. Neither warning was a C compilation or link error.

## Commands and results

For both builds, `KHEADERS=/usr/src/linux-headers-7.0.0-34-generic` and
`M=/home/keivan/nouveau-vaapi-app-validation/nouveau-vma-fullbuild-20261004/source/drivers/gpu/drm/nouveau`.

Enabled, after `make -C "$KHEADERS" M="$M" clean`:

```sh
nice -n 10 make -C "$KHEADERS" M="$M" \
  CONFIG_DRM_NOUVEAU=m \
  KCFLAGS=-DCONFIG_DRM_NOUVEAU_SELFTEST=1 W=1 -j1 modules
```

Result: **exit 0**, full module link. The linked scratch artifact was:

```text
SHA-256: 0fefbe31e4b75206670c0a50006f1cc0ca4524a5211420cdd0c16a8cf649f9c8
size: 248104080 bytes
srcversion: 936407678F3DA1E8515F5EC
vermagic: 7.0.0-34-generic SMP preempt mod_unload modversions
```

It contains `NOUVEAU_DIAG_V3_VMA`, `NOUVEAU_DIAG_BAR2_FAULT`, and the
ABI16/NVKM `NOUVEAU_DIAG_NVIF_DUP` markers.

Disabled, after a second `make -C "$KHEADERS" M="$M" clean`:

```sh
nice -n 10 make -C "$KHEADERS" M="$M" \
  CONFIG_DRM_NOUVEAU=m W=1 -j1 modules
```

Result: **exit 0**, full module link. The linked scratch artifact was:

```text
SHA-256: a2a1dcbd18916414d8dc4d1f7d073275ad542ea4f0faac5dd432fb830affef61
size: 247939216 bytes
srcversion: 936407678F3DA1E8515F5EC
vermagic: 7.0.0-34-generic SMP preempt mod_unload modversions
```

The disabled module contains no `NOUVEAU_DIAG_V3_VMA` or
`NOUVEAU_DIAG_BAR2_FAULT` strings. The complete enabled and disabled Kbuild
logs are retained outside the repository at the scratch path above; each log
SHA-256 is
`cfedcc1342168fc345802994a8f7666eec5ac8b65f1b5582b4b79ca5fac6c077`.

## Runtime boundary

These full links close the enabled/disabled build gate only. A later read-only
preflight on boot `85152730-d7e1-49d9-b65d-00d01dbdada5` found one
BAR2/HOST_CPU/PTE event and returned `PREFLIGHT_FAIL` with
`RUN_ELIGIBLE=false`; the old `354DCD95A5804DE00E20EFB` module remains loaded
and no desktop `DISPLAY` is available. See
[`CURRENT-BOOT-PTE-RECHECK-20261004.md`](CURRENT-BOOT-PTE-RECHECK-20261004.md).

The scratch artifacts are not deployment candidates. A signed/compressed
installed module, initramfs identity, reviewed deployment manifest, fresh
eligible boot, and visible logged-in desktop are still required before a
controlled GPU test. `main` remains HOLD.
