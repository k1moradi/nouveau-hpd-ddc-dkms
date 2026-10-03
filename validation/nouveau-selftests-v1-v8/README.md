# Nouveau diagnostic selftests v1-v8 review bundle

This directory carries an **uninstalled diagnostic-only** Linux Nouveau
selftest patch series for independent review. It is not a production fix and
does not change the release package's install path.

## Source and build provenance

- Target kernel: Ubuntu `7.0.0-34-generic`.
- Source package: `linux-source-7.0.0` version `7.0.0-34.34`.
- Source archive SHA-256:
  `a874e1fb08d2ee695b08e0c8ce6fd2c76a4bf7ffa98882fbabd233380ef8a85a`.
- Headers package: `linux-headers-7.0.0-34-generic` version `7.0.0-34.34`.
- Compiler: GCC `15.2.0-16ubuntu1`.
- `CONFIG_DRM_NOUVEAU=m`, `CONFIG_DEBUG_FS=y`.
- `CONFIG_KUNIT` is unset in this Ubuntu kernel configuration. Patch v1's
  KUnit portion was therefore not compiled or run as a KUnit target; it is
  recorded as **SKIP**, not PASS.

The patches in `patches/ubuntu-7.0.0-34/` are ordered v1 through v8 and are
the versions used in the recorded Ubuntu source build. The source-only
adaptations for v2, v3, and v6 change hunk coordinates; the original package
versions are retained in `patches/original-v1-v6/`. Run
`python3 verify_hunk_metadata.py` to verify that those three adaptations
change only hunk-header coordinates.

Patch v7 hardens the selftests: it validates Falcon base identity before
base-relative MMIO, populates the runlist cache, serializes all suites through
one nonblocking gate, and bounds fixed-size chip/name strings. Patch v8 moves
lazy runlist discovery under the existing runtime-PM reference and uses the
NVIF helper's GR runlist mask in the FIFO selftest.

## Static regression checks

The source-contract tests require the `nouveau_debugfs.c` used for the
candidate stack:

```bash
NOUVEAU_SELFTEST_SOURCE=/path/to/kernel-source/drivers/gpu/drm/nouveau/nouveau_debugfs.c \
  python3 tests/test_selftest_hardening.py
```

They cover the Falcon check-before-MMIO ordering, PM-before-lazy-runlist
ordering and cleanup, the shared selftest gate, and bounded protocol strings.
The recorded v8 candidate result is 8/8 passing.

## Build evidence

`evidence/` contains the complete diagnostic-enabled and
diagnostic-disabled `W=1` build logs and module metadata. Both v8 builds were
clean builds against the matching Ubuntu headers and completed successfully.
The resulting module was not installed, loaded, or placed in an initramfs.

The v8 diagnostic module SHA-256 is
`c94da213f85dce0c95ab81bfa5e4b92e8dcb95e786b018cd0e8a1c9d9b0e6de8`, with
srcversion `8FA47D2883A08EF0A1B7767` and vermagic
`7.0.0-34-generic SMP preempt mod_unload modversions`. The `.ko` itself is
not committed; its hash and `modinfo` output are recorded instead.

The only build notices were that the selected GCC executable name and pahole
version differ from the values recorded when the Ubuntu kernel was built,
and BTF was skipped because the matching `vmlinux` is unavailable.

## Runtime status

No diagnostic selftest has run from this patch series. No module was
installed or loaded, no initramfs was changed, and no reboot or GPU workload
was started for this validation. Runtime readiness remains pending external
review and a separate supervised hardware test. The project acceptance goal
remains incomplete; `main` stays on HOLD.
