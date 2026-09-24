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
