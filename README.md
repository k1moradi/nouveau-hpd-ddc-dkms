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
| `patches/diagnostic/dac-powered-ddc-probe.patch` | Optional DAC-powered DDC diagnostic |
| `patches/diagnostic/ack-slot-sampler.patch` | Optional read-only PNVIO ACK-slot sampler |
| `docs/STATIC-ANALYSIS.md` | Current static-analysis conclusions and eliminated hypotheses |
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

To test whether the monitor ACKs DDC only in the analog DAC load-detect state, use:

```bash
pkexec ./install.sh --diag-dac-ddc
```

The diagnostic flags may be combined.  `--diag-dac-ddc` performs one EDID
byte-0 transaction after the DAC enters its non-normal load-detect power state and
another after the existing load-sense delay while load-sense remains active. It uses
the DCB/VBIOS-selected I2C bus and does not modify IBUF, GPIO, or PNVIO routing
state.

For the K4200 ACK-slot capture, use:

```bash
pkexec ./install.sh --diag-ack-slot
```

This flag also enables the DAC-powered probe, which generates known EDID
`0x50` transactions. For address ACK slots on physical PNVIO port 0, Nouveau
logs three consecutive read-only samples of register `0xd014`. The first sample
supplies the existing ACK decision; the log decodes SCL input bit 4 and SDA
input bit 5 for all three reads. For this diagnostic build, the DKMS script
compiles Nouveau's existing internal bit-bang implementation, which the
Ubuntu headers otherwise leave disabled. It then selects that path by staging
`options nouveau config=NvI2C=1` for the next boot and includes it in the
initramfs. Normal builds do not enable the internal implementation.

Keep the monitor connected for the diagnostic boot, then run
`./verify-after-reboot.sh`. An SCL-high/SDA-low sample means an ACK reached the
GPU input; SDA-high means no ACK was observed there. This connected-only
capture cannot distinguish a monitor that does not pull SDA low from an
intervening board-level buffer or signal-path issue. A missing sample is
inconclusive; check the active `NvI2C=1` option and confirm that the
ACK-slot DKMS module loaded. The active sysfs parameter is root-readable; if
the verifier cannot read it non-interactively, ACK-slot samples themselves
confirm that the internal transfer path ran.

After saving the output, run a normal `pkexec ./install.sh` to rebuild without
diagnostics and remove the temporary `NvI2C=1` module option for the next boot.
`pkexec ./uninstall.sh` also removes that option.

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

The optional diagnostics are enabled only by their `install.sh` flags. A normal
install replaces the staged source and removes all diagnostic markers and the
temporary `NvI2C=1` module option.

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
