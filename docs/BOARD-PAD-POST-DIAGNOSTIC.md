# GK104 POST and GPIO31 trace

## Purpose

This opt-in trace observes whether Nouveau requests VBIOS POST, what effective
`execute` value reaches `nvbios_post()`, and whether the existing BIOS
interpreter reaches the two K4200 GPIO31 writes. It addresses the remaining
software-only question from the board/pad audit: whether the conditional VBIOS
sequence containing the GPIO31 pulse runs during Nouveau initialization.

The diagnostic is limited to GK104 (`chipset == 0xe4`). It adds no DDC
transaction, GPIO operation, hardware register write, module parameter, or
manual POST action. At `gf100_devinit_preinit()`, the existing `0x02240c` read
is retained in a local variable and logged; the same value computes the
existing `base->post` decision. At the devinit callback, the log records the
actual boolean argument immediately before the existing `nvbios_post()` call.
In the BIOS interpreter, logs are emitted only for resolved register
`0x00d68c` and values `0x00002000` or `0x00001000`; the existing write still
runs under its original execution condition.

The GPIO log's `vbios_offset` is the interpreter cursor (`init->offset`) at
the write-dispatch point. Since some op handlers advance that cursor before
calling the common register-write helper, it can identify the current script
position without necessarily being the opcode's first byte.

## Enable and verify

Run from the canonical repository:

```bash
pkexec ./install.sh --diag-board-pad-post
```

Keep the monitor connected for one reboot, then run as the normal user:

```bash
./verify-after-reboot.sh
```

The installer stages only the diagnostic marker. It does not set `NvI2C` or
`NvI2CHw`. Run a normal `pkexec ./install.sh` after collecting the log to
rebuild without this patch.

## Log records

```text
DDC_DIAG: BOARD_PAD phase=devinit-post-decision r2240c=........ raw_post=0|1
DDC_DIAG: BOARD_PAD phase=devinit-post-call execute=0|1
DDC_DIAG: BOARD_PAD phase=gpio31-script vbios_offset=...... reg=0000d68c value=00002000|00001000 execute=0|1
```

- `raw_post` records the value Nouveau computes directly from bit 1 of
  `0x02240c`, before any later option override.
- `devinit-post-call execute` records the effective argument passed to
  `nvbios_post()` after overrides.
- A GPIO31 record proves the interpreter reached that register-write opcode.
  `execute=1` means the existing interpreter execution guard allows its
  existing write; `execute=0` means the reached opcode is skipped by current
  script conditions.
- `execute=1` at the POST call does not prove that the conditional subroutine
  was reached. Conversely, absence of a GPIO31 record is inconclusive by
  itself: the conditional flow may have skipped that operation.

Interpret the three levels separately. If POST is requested and both GPIO31
operations are logged with `execute=1`, the normal firmware pulse ran; this
weakens the theory that Linux merely failed to replay that pulse. If POST is
skipped, or the operation is absent/skipped, retain the distinction between
the raw POST decision, the effective POST call, and conditional interpreter
flow. This trace alone cannot identify GPIO31's board function or establish
that it controls DDC.

Do not manually toggle GPIO31 or write undocumented PNVIO/GPIO registers based
on these records.
