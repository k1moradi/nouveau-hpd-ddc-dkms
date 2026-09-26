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
| `patches/diagnostic/d014-init-snapshot.patch` | Optional read-only PNVIO port-0 init snapshot |
| `patches/diagnostic/pnvio-d014-sense-matrix.patch` | Optional bounded sampling after normal I2C line drives |
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
`./verify-after-reboot.sh`. Under the documented input interpretation,
SCL-high/SDA-low is consistent with an ACK and SDA-high with no ACK at the
sampled input. The external-pad mapping is unvalidated. This connected-only
capture cannot distinguish a monitor that does not pull SDA low from an
intervening board-level buffer or signal-path issue. A missing sample is
inconclusive; check the active `NvI2C=1` option and confirm that the
ACK-slot DKMS module loaded. The active sysfs parameter is root-readable; if
the verifier cannot read it non-interactively, ACK-slot samples themselves
confirm that the internal transfer path ran.

After saving the output, run a normal `pkexec ./install.sh` to rebuild without
diagnostics and remove the temporary `NvI2C=1` module option for the next boot.
`pkexec ./uninstall.sh` also removes that option.

To capture the firmware-transferred EDID base block alongside the ACK-slot
samples in the same diagnostic boot, install both opt-in diagnostics:

```bash
pkexec ./install.sh --diag-ack-slot --diag-firmware-edid
```

The firmware snapshot runs only on the DVI-I analog fallback path. It checks
whether this GPU is marked as the firmware-primary display, then logs whether
the retained 128-byte EDID base block has a valid header and checksum, its
analog/digital input bit and extension count, and the raw bytes. It is
read-only: it does not attach the firmware EDID to the connector, add modes, or
change live DDC behavior. `./verify-after-reboot.sh` displays these records
alongside the ACK-slot samples. A valid analog block is evidence that firmware
transferred an analog EDID; it is not by itself proof that the EDID belongs to
the currently connected monitor.

To inspect D014 input bits during ordinary Nouveau DDC probing, use:

```bash
pkexec ./install.sh --diag-d014-sense --diag-firmware-edid
```

This enables the internal I2C path and ACK-slot sampler, then captures at most
32 line-drive transitions for address `0x50` on physical PNVIO register
`0xd014`. It adds only read-only MMIO samples after line-drive operations
Nouveau already performs, with a 1 μs settle delay per captured transition (at
most 32 μs total). It adds no line-drive operation, DDC transfer, or separate
DAC-powered DDC probe. When both output-release bits are set, it takes three
consecutive reads.
It also records the raw D014 value immediately before and after the existing
`0x7` bus-init write. The `--diag-firmware-edid` flag captures the independent
firmware base-block snapshot during the same boot. Neither flag adds a DDC
transaction. Use `./verify-after-reboot.sh` to inspect these records.

EnvyTools names bits 4 and 5 `SCL_IN` and `SDA_IN`, and Nouveau uses them as
the sense results. The available static descriptions do not establish whether
those samples represent the external pad level or a local loopback/status path.
Treat this capture as evidence about register behavior; do not interpret a
high SDA sample as proof that the monitor or board path failed to pull the
physical line low without independent validation.

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
