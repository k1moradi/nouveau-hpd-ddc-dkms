# Nouveau HPD/DDC DKMS fix

This project builds a maintained Nouveau module for the Quadro K4200 DVI-I to
VGA EDID investigation. The confirmed fix lets DRM try DDC when HPD is low on a
non-DisplayPort output. It does not change DisplayPort detection.

## Source layout

This directory is the canonical source for the fix and its build. Make changes
here; `/usr/src/nouveau-hpd-ddc-*` and `/var/lib/dkms/nouveau-hpd-ddc/*` are
generated DKMS staging data and are replaced during installation.

| Path | Contents |
| --- | --- |
| `patches/hpd-low-ddc-probe.patch` | Confirmed HPD-low/DDC fix |
| `patches/diagnostic/ibuf-state-snapshot.patch` | Optional read-only IBUF diagnostic |
| `dkms/` | DKMS config, build script, and hooks |
| `docs/BUG-REPORT.md` | Hardware and diagnostic evidence |
| `debian/` | Debian package metadata and maintainer scripts |
| `install.sh`, `uninstall.sh`, `verify-after-reboot.sh` | Local install, cleanup, and verification entry points |

## Install

Install the confirmed fix:

```bash
pkexec ./install.sh
```

For the current read-only PNVIO input-buffer investigation, include the
diagnostic patch:

```bash
pkexec ./install.sh --diag-ibuf
```

The installer removes the known older DKMS revisions (0.1.0 through 0.1.5),
copies this project's DKMS files and patches into the package staging directory,
builds Nouveau for the running kernel, installs it, and updates the initramfs.
It also cleans only this project's temporary build/test directories under
`/tmp`.

After reboot, run:

```bash
./verify-after-reboot.sh
```

With `--diag-ibuf`, Nouveau logs `BOOT0`, the DDC bit-bang register before and
after its existing initialization write, PNVIO register `0xe500`, devinit
status, and IBUF register `0xe1b8` when physical I2C port 0 is initialized.
The first sample is during preinit, before VBIOS POST. The next is during normal
I2C initialization, after POST and the intervening device fini pass. The
existing `0xd014 = 0x7` write still happens exactly once; the diagnostic adds
read-only samples around it. IBUF readback validity remains under investigation:
do not interpret its bits as enabled until the control register samples
validate the read. In particular, `0xffffffff` is unvalidated, not proof that
all four input buffers are enabled.

## Build behavior

`dkms/dkms-build.sh` extracts only `drivers/gpu/drm/nouveau/` from the exact
Ubuntu `linux-source` package for the target kernel. It applies the patch files
from the staged copy of this project and builds against the matching installed
headers. A patch that no longer applies stops the build for review. The build
uses GCC and all online logical CPUs by default; set `NOUVEAU_DKMS_JOBS=<N>` to
limit parallelism or `NOUVEAU_DKMS_TMPDIR=/path` to choose a build location.

The optional diagnostic is enabled only by `install.sh --diag-ibuf`. A normal
install replaces the staged source and removes its diagnostic marker.

To create the Debian package from this same canonical source tree, run:

```bash
./build-deb.sh
```

The generated package and staging tree live under ignored `build/` output.

## Remove the override

```bash
pkexec ./uninstall.sh
```

This removes all known DKMS revisions from 0.1.0 through 0.1.6, their source
staging directories, and project temporary trees, then refreshes module
dependencies and initramfs files. Nouveau uses the distribution module after
reboot.

## Investigation notes

The hardware setup and captured evidence are in [docs/BUG-REPORT.md](docs/BUG-REPORT.md).
The IBUF experiment remains diagnostic: the K4200 VBIOS setting bit 17 does not
prove that bit 16 should be enabled, so the patch does not modify `0xe1b8`.

Version 0.1.6 organizes the canonical patches, retires earlier DKMS revisions
during installation, and fixes executable hook invocation. Versions 0.1.1 to
0.1.5 added Ubuntu kernel build compatibility, exact source retrieval, GCC
selection, and bounded temporary-build cleanup.

Secure Boot key enrollment is outside the scope of this package. On systems
that enforce Secure Boot, sign and enroll the locally built module according
to the system's policy.
