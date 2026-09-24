# nouveau-hpd-ddc DKMS patch

This DKMS package fixes a Nouveau output-detection control-flow problem seen on an NVIDIA Quadro K4200 (GK104) using DVI-I -> passive DVI-to-VGA -> VGA monitor.

The VBIOS maps the DVI-I analog/TMDS connector to I2C/DDC bus 0. Linux detects the analog load, but `nvkm_outp_detect()` returns `0` when connector HPD is low. The NVIF layer maps `0` to `NOT_PRESENT`, and `nouveau_connector_ddc_detect()` then skips the DDC/EDID probe. This contradicts the nearby source comment saying non-DP HPD-low should be `UNKNOWN` so DRM can probe DDC.

The patch changes only the non-DP, HPD-present-but-low path to return `-EINVAL`, which the NVIF wrapper maps to `UNKNOWN`. DisplayPort still returns NOT_PRESENT when HPD is low.

## Install

From this directory:

```bash
sudo ./install.sh
```

The installer removes the temporary Nouveau debug configuration used during diagnosis, installs DKMS/build prerequisites plus Ubuntu's `linux-source` metapackage, builds the patched `nouveau.ko` for the running kernel, installs it under DKMS, runs `depmod`, and rebuilds the initramfs.

Reboot and run:

```bash
./verify-after-reboot.sh
```

This patch fixes the confirmed control-flow bug that prevents Nouveau from attempting DDC when non-DP HPD is low. Earlier direct I2C testing also failed to read address 0x50, so EDID may still remain unavailable after this fix; if that happens, the next bug is lower in the GK104 PNVIO/DDC path rather than in connector detection.

## Future kernels

`AUTOINSTALL=yes` lets DKMS rebuild for newly installed kernels. The build script obtains the complete Nouveau source from the matching Ubuntu `linux-source-X.Y.Z` package, preferring the installed source tarball. If the exact source package isn't installed, it attempts to download the exact binary source package version matching the target Ubuntu kernel ABI.

If Nouveau's source layout changes or the patch no longer applies cleanly, the build intentionally fails rather than making a guessed modification. If Ubuntu/upstream already contains the intended fallback, the script detects that and builds without reapplying the patch.

## Roll back

```bash
sudo ./uninstall.sh
```

This removes the DKMS override and rebuilds the initramfs so the stock Ubuntu Nouveau module is used again.

## Secure Boot

This package does not manage Machine Owner Keys. The test system has Secure Boot disabled. On systems enforcing Secure Boot, the locally built module must be signed/enrolled according to that system's policy.

### Ubuntu build-tree note (0.1.1)

Version 0.1.1 prepares a separate Ubuntu kernel output tree before compiling Nouveau, following Canonical's documented single-module rebuild sequence (`outputmakefile`, `archprepare`, `prepare`, `M=scripts`, then the driver subtree). This fixes the 0.1.0 DKMS build failure caused by treating Nouveau's in-tree source subtree as a generic external module.

### Ubuntu linux-source archive discovery (0.1.2)

Version 0.1.2 fixes source-archive discovery on Ubuntu packages that expose `/usr/src/linux-source-X.Y.Z.tar.bz2` as a link while storing the actual archive under `/usr/src/linux-source-X.Y.Z/linux-source-X.Y.Z.tar.bz2`. The DKMS build now searches both levels for an actual archive, including after extracting an exact-version `.deb` downloaded with `apt-get download`.

### Faster exact-source retrieval (0.1.4)

Version 0.1.4 removed the full-kernel extraction/output-tree preparation used by 0.1.1/0.1.2. It uses the exact matching Ubuntu `linux-source-X.Y.Z` archive, selectively extracts only `drivers/gpu/drm/nouveau/`, applies the patch there, and builds that subtree as an external module against the target kernel's installed `/lib/modules/<ABI>/build` headers and `Module.symvers`.

### GCC and CPU parallelism (0.1.5)

Version 0.1.5 explicitly forces GNU GCC for both target and host C compilation (`CC=gcc`, `HOSTCC=gcc`) and removes inherited `LLVM`, `LLVM_IAS`, `CC`, and `HOSTCC` environment selections before invoking Kbuild. This prevents a shell or distribution default from silently switching the DKMS build to Clang.

The build detects all online logical CPUs with `nproc` (falling back to `_NPROCESSORS_ONLN`) and uses that value for `make -j`. Set `NOUVEAU_DKMS_JOBS=<N>` to override parallelism on memory-constrained systems. Set `NOUVEAU_DKMS_CC=<compiler>` only if intentionally overriding GCC.

## Temporary build directory and RAM cleanup

Version 0.1.4 uses a unique `/tmp/nouveau-hpd-ddc.<kernel>.*` directory for the
selectively extracted Nouveau source and object files. On systems where `/tmp`
is `tmpfs`, this makes the compile faster but consumes RAM/swap temporarily.
The DKMS build script installs an EXIT/HUP/INT/TERM trap and removes that tree
whether the build succeeds, fails, or is interrupted. `install.sh`, `uninstall.sh`,
and the DKMS clean hook also remove only this project's stale temporary trees.
They never clear arbitrary `/tmp` content.

Set `NOUVEAU_DKMS_TMPDIR=/path/on/disk` before a manual DKMS build if you want
to avoid tmpfs entirely.
