# Nouveau HPD/DDC DKMS fix

This project builds a maintained Nouveau module for the Quadro K4200 DVI-I to
VGA EDID investigation and a separate GK104 legacy-video decode experiment.
The confirmed display fix lets DRM try DDC when HPD is low on a non-DisplayPort
output. The video-context backport is a candidate that still needs K4200
validation.

## Source layout

This directory is the canonical source for the fix and its build. Make changes
here; `/usr/src/nouveau-hpd-ddc-*` and `/var/lib/dkms/nouveau-hpd-ddc/*` are
generated DKMS staging data and are replaced during installation.

| Path | Contents |
| --- | --- |
| `patches/hpd-low-ddc-probe.patch` | Confirmed HPD-low/DDC fix |
| `patches/video/gk104-legacy-video-context-nonpriv.patch` | Candidate backport for legacy GK104 MSVLD/MSPDEC/MSPPP context mappings |
| `dkms/check-gk104-video-context.py` | Fail-closed detection of vulnerable, fixed, and unknown kernel source layouts |
| `patches/diagnostic/ibuf-state-snapshot.patch` | Optional read-only IBUF diagnostic |
| `patches/diagnostic/dac-powered-ddc-probe.patch` | Optional DAC-powered DDC diagnostic |
| `patches/diagnostic/ack-slot-sampler.patch` | Optional read-only PNVIO ACK-slot sampler |
| `patches/diagnostic/d014-init-snapshot.patch` | Optional read-only PNVIO port-0 init snapshot |
| `patches/diagnostic/pnvio-d014-sense-matrix.patch` | Optional bounded sampling after normal I2C line drives |
| `patches/diagnostic/gk104-pnvio-hw-ddc.patch` | Opt-in GK104/K4200 GOP-derived hardware DDC diagnostic |
| `patches/diagnostic/gk104-post-gpio31-trace.patch` | Opt-in read-only GK104 POST decision and GPIO31 interpreter trace |
| `docs/GK104-PNVIO-HW-I2C-TRANSCRIPT.md` | Permanent K4200 GOP register and command transcript |
| `docs/BOARD-PAD-POST-DIAGNOSTIC.md` | POST/GPIO31 trace scope and interpretation |
| `docs/STATIC-ANALYSIS.md` | Current static-analysis conclusions and eliminated hypotheses |
| `docs/NOUVEAU-VAAPI-VIDEO-DECODE-ISSUE.md` | Original GK104/NVE4 VA-API failure capture |
| `docs/VP5-VIDEO-DECODE.md` | Candidate backport rationale and K4200 validation plan |
| `dkms/` | DKMS config, build script, and hooks |
| `docs/BUG-REPORT.md` | Hardware and diagnostic evidence |
| `debian/` | Debian package metadata and maintainer scripts |
| `install.sh`, `uninstall.sh`, `verify-after-reboot.sh` | Local install, cleanup, and verification entry points |

## Install

Install the current build (confirmed HPD/DDC fix plus the GK104 video candidate):

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
ACK-slot DKMS module loaded. The installer explicitly includes the temporary
modprobe option in the generated initramfs, where Nouveau is loaded during
early boot. If the active sysfs parameter is unreadable, the verifier reports
the path as unknown; ACK-slot samples themselves confirm that it ran.

After saving the output, run a normal `pkexec ./install.sh` to rebuild without
diagnostics and remove the temporary `NvI2C=1` module option for the next boot.
`pkexec ./uninstall.sh` also removes that option.

## GK104 GOP-backed hardware DDC diagnostic

To test the K4200's GOP-derived PNVIO hardware controller with the monitor
connected, install this diagnostic by itself:

    pkexec ./install.sh --diag-pnvio-hw-ddc

The canonical transcript at
[docs/GK104-PNVIO-HW-I2C-TRANSCRIPT.md](docs/GK104-PNVIO-HW-I2C-TRANSCRIPT.md)
records the GOP routines, register values, command/status fields, four-byte
chunking, final STOP command, timeout, and retry behavior. The ROM binary stays
outside Git.

This first diagnostic is restricted to GK104, physical PNVIO port 0
(register 0xd014), DCB selector 3, and exactly the two DRM EDID block-0
shapes: msg0 address 0x50, flags 0, one-byte offset 0; msg1 address 0x50,
flags I2C_M_RD, length 1 or 128. The one-byte read is DRM's DDC-presence
probe. Selector 3 maps to 400 kHz in this K4200 GOP only. Other buses,
selectors, and message forms stay on Linux i2c-algo-bit.

The dispatcher chooses the path before acquiring the NVKM bus lock. The
hardware transfer acquires the NVKM bus/pad once. Unsupported transactions
call Linux i2c-algo-bit directly, so its existing callbacks acquire/release
the bus without a nested lock. Once a hardware transaction may have started,
an error is never replayed through bitbang. For the exact offset-zero EDID
probe/base-block shapes only, a failed 400 kHz read gets the GOP-derived
100 kHz hardware retry after its bounded D014 line-recovery sequence. If the
100 kHz hardware read also fails and recovery succeeds, the diagnostic follows
the GOP's 100-to-60 transition and software transaction, including the
segment-pointer write to wire address 0x60. That software path is limited to
the same EDID block-0 shapes; it is not a general adapter fallback.

For this diagnostic boot the installer stages config=NvI2CHw=1 in the
modprobe config and initramfs. It does not force NvI2C=1. Keep the monitor
connected, reboot once, and run:

    ./verify-after-reboot.sh

Run the verifier as your normal user; it does not need sudo. It reads the
current boot's kernel journal and public module/DKMS metadata, and reports when
the active Nouveau sysfs config is not readable. In restricted shells where
`NoNewPrivs` is set, `sudo` may refuse before starting the script.

The verifier reports the active option, DKMS/module match, selector and
message shape from the kernel log, controller result, EDID size, and header.
A successful presence probe is ret=2, bytes=1, chunks=1. DRM should then
request the full base block; success is ret=2, bytes=128, chunks=32. Check
the EDID header and preserve the full log if the bytes are invalid or either
transfer fails. No samples means the run is inconclusive.
This is diagnostic work, not a resolution fix.

Run a normal pkexec ./install.sh after saving the result. That rebuilds without
the optional patch and removes NvI2CHw=1 from the next initramfs. Uninstall
also removes the temporary module option.

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

## GK104 POST and GPIO31 trace

To record the hardware-derived POST decision, the effective `nvbios_post()`
argument, and whether the normal VBIOS interpreter reaches the GPIO31 assert
and release writes, use:

```bash
pkexec ./install.sh --diag-board-pad-post
```

This diagnostic is GK104-only and read-only. It adds no register write, GPIO
toggle, DDC transaction, `NvI2C`/`NvI2CHw` option, or manual POST action. The
verifier prints the raw `0x2240c` decision, the effective POST callback
argument after overrides, and each reached `0xd68c` write with its
interpreter cursor and `execute` state. `execute=1` on a GPIO record means the
existing opcode passes the interpreter's execution guard; a POST call with
`execute=1` does not prove that conditional VBIOS flow reaches the GPIO
subroutine. A missing GPIO record is therefore inconclusive by itself.
Full interpretation and commands are in
[docs/BOARD-PAD-POST-DIAGNOSTIC.md](docs/BOARD-PAD-POST-DIAGNOSTIC.md).

Keep the monitor connected for one reboot, then run
`./verify-after-reboot.sh` as the normal user. After saving the trace, run a
normal `pkexec ./install.sh` to rebuild without the optional patch.

## GK104 legacy video context mapping candidate

The normal DKMS build includes a candidate backport for the legacy Kepler
MSVLD, MSPDEC, and MSPPP engine-context mappings. The upstream report describes
privileged mappings causing `PRIV_VIOLATION` faults on GK107; the same
`gk104_ectx_ctor()` source is used by the K4200's GK104 FIFO path. This is a
strong candidate, not yet a confirmed K4200 root cause.

Before installing this revision, capture the current kernel log around one
reproduction and preserve the engine, client, access, reason, fault address,
channel ID, and channel instance. The most useful confirmation is a legacy
video engine fault with reason `0x05 [PRIV_VIOLATION]` immediately before the
channel is killed. Then install and reboot:

```bash
pkexec ./install.sh
```

Run the deterministic H.264 VA-API test in
[`docs/VP5-VIDEO-DECODE.md`](docs/VP5-VIDEO-DECODE.md). Keep the nonstall-event
follow-up out of this first test; it is a separate change for a possible later
fence-completion hang. The DKMS source detector applies the mapping patch only
to the recognized vulnerable function, skips the recognized three-engine
fix, and aborts on any unknown layout. It does not key the change to the
K4200 PCI ID. A successful build alone does not establish that the K4200 fault
was caused by this mapping or that decode is fixed.

The installer removes the known older DKMS revisions (0.1.0 through 0.1.11),
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

The optional diagnostics are enabled only by their install.sh flags. A normal
install replaces the staged source and removes all diagnostic markers and the
temporary NvI2C=1 or NvI2CHw=1 module option.

To create the Debian package from this same canonical source tree, run:

```bash
./build-deb.sh
```

The generated package and staging tree live under ignored `build/` output.

## Remove the override

```bash
pkexec ./uninstall.sh
```

This removes all known DKMS revisions from 0.1.0 through 0.1.13, their source
staging directories, and project temporary trees, then refreshes module
dependencies and initramfs files. Nouveau uses the distribution module after
reboot.

## Investigation notes

The hardware setup and captured evidence are in [docs/BUG-REPORT.md](docs/BUG-REPORT.md).
The IBUF experiment remains diagnostic: the K4200 VBIOS setting bit 17 does not
prove that bit 16 should be enabled, so the patch does not modify `0xe1b8`.

Version 0.1.13 adds an opt-in, isolated legacy FIFO nonstall-event index
experiment for video-decode fence progress. Run it with
`pkexec env NOUVEAU_DKMS_JOBS=2 ./install.sh --experimental-legacy-nonstall`;
run `pkexec env NOUVEAU_DKMS_JOBS=2 ./install.sh` without flags to rebuild the
patch-1-only baseline. See
[the experiment notes](docs/LEGACY-FIFO-NONSTALL-EXPERIMENT.md) for evidence,
verification, and rollback. Version 0.1.12 adds the
fail-closed GK104 legacy-video context mapping backport and its K4200
validation notes. Version 0.1.11 adds the read-only
GK104 POST-decision, effective POST-call,
and conditional GPIO31 interpreter trace. Version 0.1.10 corrects the GOP
software-line recovery and adds the
ROM-derived 100-to-60 software EDID path after the 400/100 kHz hardware
attempts fail. It logs each segment/address stage and captures returned EDID
bytes. Version 0.1.9 added the GOP caller's ordered initialization waits,
speed-setter STOP, initializer mask, and pre-read line recovery. Version 0.1.8
added the 400-to-100 kHz hardware retry and bounded D014 line recovery. Version 0.1.7 introduced the
hardware DDC diagnostic and permanent GOP transcript. Version 0.1.6
organizes the canonical patches,
retires earlier DKMS revisions during installation, and fixes executable hook
invocation. Versions 0.1.1 to
0.1.5 added Ubuntu kernel build compatibility, exact source retrieval, GCC
selection, and bounded temporary-build cleanup.

Secure Boot key enrollment is outside the scope of this package. On systems
that enforce Secure Boot, sign and enroll the locally built module according
to the system's policy.
