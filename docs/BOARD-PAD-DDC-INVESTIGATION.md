# K4200 board and pad DDC investigation

## Checkpoint and scope

This is a separate static-analysis series based on the verified GOP
transport checkpoint `dc8e2b7890f60bda2bffdd802d8a1c03393ce46e` on
`review/gk104-pnvio-hw-ddc-20260926`. It does not change `main`, the kernel
patches, or hardware state. The K4200 ROM remains outside Git.

The question is now limited to the remaining ambiguity after the 2026-09-27
GOP transport run: is ACK absent from the board-level DDC path, or does ACK
reach the GPU but remain hidden by an undocumented pad or input-routing
state?

### Result

- The recovered full ROM has a normal DCB route for the DVI-I analog output:
  DCB I2C bus 0, PNVIO physical location 0, register `0x00d014`, with no
  shared-pad flag.
- The decoded VBIOS init and display scripts contain no write to
  `0x00d014` or `0x00d018`.
- The decoded GPIO table assigns functions only to lines 0 through 24. It
  does not assign GPIO31 or a standard DDC/I2C switch, keeper-enable, or
  DVI-DAC-switch function.
- A main init-script subroutine does pulse GPIO31. Its electrical/board
  function is unknown; neither the table nor the script associates it with
  DDC.
- The known `0x00e1b8` script operation selects I2C1 (bit 17), not the
  failing I2C0 path (bit 16).
- The four `0xe5xx` writes address the shared/hybrid AUXCH pad setup registers;
  the DCB-selected I2C0 path is exclusive and does not use that shared-pad
  mode callback.
- Linux's GK104 devinit chooses whether to execute VBIOS POST from bit 1 of
  `0x02240c`. On the 0.1.11 boot, the read-only trace recorded
  `0x02240c=0x2`, `raw_post=0`, and effective `execute=0`; it recorded no
  GPIO31 interpreter writes. The raw and effective decisions agree, so no
  override changed the result. Nouveau therefore did not replay the POST path
  on that boot. This says nothing conclusive about whether pre-Linux firmware
  had already run the pulse.

No specific missing DDC enable has been identified. The evidence does not
distinguish an open/disabled board path from an internal pad/input-routing
state.

## ROM and tool provenance

ROM source:

```text
/home/keivan/Downloads/k4200-vbios.rom
size:   184,320 bytes
SHA256: 6c768eb2d34ab65bdde6137e66b5fbe750a750f9553aabff51e614e1168c4ed9
```

The ROM was originally captured through Nouveau's read-only debugfs
`vbios.rom` file. It was not fetched through a live BAR read. The extracted
EFI GOP image is 133,536 bytes with SHA256
`ba478d3458e8323d20ac18de1034885aaf099a3d2c35edb0485a4c52599d8684`; this
matches the image used for the separate GOP protocol transcript.

Tool sources used for this pass:

- EnvyTools `nvbios`, source commit
  [`f102b82381f3f11cee113d16374c87091db039d9`](https://github.com/envytools/envytools/tree/f102b82381f3f11cee113d16374c87091db039d9).
- UEFIRomExtract, source commit
  [`a368a0fe0b757c7e234c1d28b1203fbb8f88d9c9`](https://github.com/ccharon/UEFIRomExtract/tree/a368a0fe0b757c7e234c1d28b1203fbb8f88d9c9).

The relevant EnvyTools invocations were:

```sh
nvbios -p scripts /home/keivan/Downloads/k4200-vbios.rom
nvbios -p dcb,gpio,i2c,conn,mux /home/keivan/Downloads/k4200-vbios.rom
```

The utility reported unparsed/unknown BIT tables `B`, `C`, `I`, `S`, `U`,
`V`, `x`, and `u`, and could not parse IUNK21 table version 1.1. The DCB,
GPIO, I2C, connector, mux, main init-script, and display-script structures
used below were decoded. Findings are limited to those decoded structures;
the parser warnings are why absence claims are phrased as “no decoded script
operation found,” rather than a proof that no private firmware mechanism
exists anywhere in the ROM.

## DCB and GPIO mapping

| VBIOS entry | Decode | Relevance |
|---|---|---|
| DCB 0 | TMDS, I2C 0, connector 0 | DVI-I digital function shares DCB bus 0 |
| DCB 1 | Analog, I2C 0, connector 0 | Failing VGA DDC route |
| I2C 0 | PNVIO, location 0, selector `3`, no `shared` field | Exclusive PNVIO port 0 at `0x00d014` |
| Connector 0 | DVI-I, HPD_0 | Board connector association |
| I2C 6–9 | PNVIO locations 6–9, shared 0–3 | Shared/hybrid pads |
| I2C 10–13 | AUXCH locations 0–3, shared 6–9 | AUX channels attached to shared pads |

The GPIO table is version 4.1 and lists assigned GPIO lines 0–24. It does not
list GPIO31. It also contains no assignment for EnvyTools' named
`I2C_OR_DDC`, `I2C_SCL_KEEPER_CIRCUIT_ENABLE`, or `DVI_DAC_SWITCH` functions.
All three XPIO subtables decode as `UNUSED`. The EXTDEV table lists an
INA3221 at address `0x80` on bus 0; it is not identified as a DDC control.

These tables weaken the hypothesis of a conventional, VBIOS-described DDC
GPIO mux or enable. They do not exclude an undocumented private net or a
control outside these tables.

## Decoded PNVIO/GPIO script operations

### D registers

The decoded init/display scripts touch these D-register addresses:

| Script location | D-register writes | Decode |
|---|---|---|
| Main init script 0, `0x8696` | `0xd624`, `0xd630`, `0xd634`, `0xd670`, `0xd740`, `0xd744`, `0xd748`, `0xd790`, `0xd794`, `0xd798` | GPIO/configuration and special-input setup; no DDC-port register |
| Main init script 1, `0x8e99` | `0xd638`, `0xd604` | GPIO10/MEM_VREF output setup and GPIO trigger |
| Subroutine `0x8e70`, called from script 0 at `0x8d32` | `0xd68c`, `0xd604` | GPIO31 state changes and output trigger |
| Display scripts `0x5c4a`, `0x5dbd`, `0x5f4a`, `0x60d7` | `0xd618`, `0xd620`, `0xd604` | No-op masked writes in the decoded scripts |
| Display script `0x6206` | `0xd61c`, `0xd604` | No-op masked writes in the decoded script |

There is no decoded init/display-script write to `0xd014` or `0xd018`. In
particular, the ROM does not show a write to a documented DDC-enable field at
either address. Nouveau's existing runtime initialization of
`0xd014 = 0x7` is separate from these VBIOS script operations.

The named table functions for relevant assigned lines are GPIO8
`THERM_SHUTDOWN` (`0xd630`), GPIO9 `THERM_ALERT` (`0xd634`), GPIO10
`MEM_VREF` (`0xd638`), and GPIO24 `SWAP_RDY_OUT` (`0xd670`). The `0xd740`,
`0xd744`, `0xd748`, `0xd794`, and `0xd798` accesses configure special-input
registers. None is assigned to a DDC function in the K4200 GPIO table.

### GPIO31 pulse

The VBIOS computes GPIO31's register as `0xd610 + 31 * 4 = 0xd68c`. EnvyTools
defines bit 14 there as the GPIO input. Condition-table entry `0x2c` checks:

```text
(R[0x00d68c] & 0x00004000) == 0
```

Main init script 0 reaches a call to subroutine `0x8e70` in the surrounding
condition flow. The subroutine contains:

```text
0x8e70: R[0x00d68c] = 0x00002000
0x8e79: R[0x00d604] = 0x00000001
0x8e82: CONDITION_TIME 0x2c
0x8e86: R[0x00d68c] = 0x00001000
0x8e8f: R[0x00d604] = 0x00000001
```

Under Nouveau's GF119 GPIO encoding, `0x2000` drives the line low and
`0x1000` releases it with the high output latch. This is a conditional
assert-then-release pulse. GPIO31 is not assigned a function in the decoded
K4200 GPIO table. The pulse could affect an undocumented board device, but
there is no evidence identifying it as DDC power, reset, or routing control.

The subroutine's `CONDITION_TIME 0x2c` opcode is distinct from main script's
condition-table entry `0x2c`; this report does not treat the two as the same
condition or infer a delay from the shared numeric value.

### E registers

The decoded init scripts and called subroutines touch these E-register
addresses:

| Script location | E-register writes | Decode/status |
|---|---|---|
| Main init script 0, `0x8696`, and its clock-related subroutines | `0xe114`, `0xe118`, `0xe11c`, `0xe120`; `0xe800`, `0xe808`, `0xe820`, `0xe828`, `0xe82c`, `0xe830`; `0xe920`, `0xe924`, `0xe928`, `0xe92c`, `0xe960`, `0xe964`, `0xe968`, `0xe96c`, `0xe9a0`, `0xe9a4`, `0xe9b0`, `0xe9c0`, `0xe9c4`, `0xe9c8`, `0xe9cc`, `0xe9e0`, `0xe9e4`, `0xe9f0`, `0xe9f8` | `0xe114–0xe120` are PNVIO PWM registers. The remaining sequence is part of the decoded init/clock setup; no operation is identified as I2C0 routing. |
| Main init script 4, `0xaba0` | `0xe500`, `0xe550`, `0xe5a0`, `0xe5f0` | Each decodes as an RMW with `AND=0xffffffff`, `OR=0`, so it preserves the old value. These addresses are AUXCH `SETUP` registers in EnvyTools' PNVIO map and overlap Nouveau's shared/hybrid pad-mode registers. |
| Main init script 4, `0xaba0` | `0xe600`, `0xe60c`, `0xe610`, `0xe614`, `0xe618`, `0xe61c`, `0xe620` | Written by a register sequence. Their role in this board's DDC path is not established; do not infer one from adjacency. |
| Main init script 4, `0xaba0` | `0xe1b8` | Mask `0xfffdffff`, value `0x00020000`: bit 17 is set, selecting I2C1 in the documented `IBUF_ENABLE_0` mapping. |

EnvyTools describes `0xe500 + n * 0x50` as AUXCH `SETUP` space. Nouveau's
`g94_i2c_pad_mode()` also applies mode changes to this register family for
shared/hybrid pads. The K4200 maps the failing I2C0 bus to an exclusive port;
the four shared PNVIO buses are I2C6–9 and the associated AUX buses are
I2C10–13. Therefore these four zero writes do not establish a missing mode
write for the failing I2C0 path.

The `0xe600–0xe620` sequence remains an open register-identification question.
No source or table evidence found in this pass ties those writes to I2C0,
GPIO31, or the DVI-I DDC pair. They are retained here so a future register
map discovery can revisit them without repeating the ROM scan.

### Complete decoded address inventory

For reproducibility, the unique D/E MMIO write addresses found in the decoded
init/display scripts and their decoded subroutines are listed below. This is
an inventory of parser output, not a claim that unknown BIT tables contain no
additional private operations.

```text
D: 0xd604, 0xd618, 0xd61c, 0xd620, 0xd624, 0xd630, 0xd634, 0xd638,
   0xd670, 0xd68c, 0xd740, 0xd744, 0xd748, 0xd790, 0xd794, 0xd798

E: 0xe114, 0xe118, 0xe11c, 0xe120, 0xe500, 0xe550, 0xe5a0, 0xe5f0,
   0xe600, 0xe60c, 0xe610, 0xe614, 0xe618, 0xe61c, 0xe620, 0xe800,
   0xe808, 0xe820, 0xe828, 0xe82c, 0xe830, 0xe920, 0xe924, 0xe928,
   0xe92c, 0xe960, 0xe964, 0xe968, 0xe96c, 0xe9a0, 0xe9a4, 0xe9b0,
   0xe9c0, 0xe9c4, 0xe9c8, 0xe9cc, 0xe9e0, 0xe9e4, 0xe9f0, 0xe9f8
```

## POST state and the conditional GPIO31 sequence

The presence of a VBIOS operation does not prove that Nouveau reruns it on a
given boot. Linux v7.0 maps GK104 to `gf100_devinit_new()` in
[`device/base.c`](https://github.com/torvalds/linux/blob/v7.0/drivers/gpu/drm/nouveau/nvkm/engine/device/base.c).
`gf100_devinit_preinit()` sets the devinit POST decision from bit 1 of
`0x02240c`: POST is requested when that bit is clear, and the source comments
describe it as a flag set by devinit and cleared on suspend. The devinit
wrapper passes that decision to `nvbios_post()`. The NV50/GF100 init path
executes the first display-encoder script pointer for each DCB output only
when POST is requested. See Linux v7.0
[`gf100.c`](https://github.com/torvalds/linux/blob/v7.0/drivers/gpu/drm/nouveau/nvkm/subdev/devinit/gf100.c),
[`base.c`](https://github.com/torvalds/linux/blob/v7.0/drivers/gpu/drm/nouveau/nvkm/subdev/devinit/base.c),
[`nv50.c`](https://github.com/torvalds/linux/blob/v7.0/drivers/gpu/drm/nouveau/nvkm/subdev/devinit/nv50.c), and
[`nv04.c`](https://github.com/torvalds/linux/blob/v7.0/drivers/gpu/drm/nouveau/nvkm/subdev/devinit/nv04.c).

The K4200 init-script table at ROM offset `0x4f11` has seven entries. In the
decoded table, script 0 is at `0x8696` and script 4 is at `0xaba0`. Script 4
ends at `0xac71` by setting bit 1 of `0x02240c`. This makes a completed
firmware initialization a plausible source of the bit that Nouveau later
observed, but the ROM decode alone does not prove which scripts the pre-Linux
firmware actually invoked.

The GPIO31 call inside script 0 is conditional. At `0x8d15`, script 0 tests
condition `0x2c`; the condition table defines it as
`(R[0x00d68c] & 0x4000) == 0`. A following `NOT` at `0x8d24` inverts the
execution state before the call to subroutine `0x8e70` at `0x8d32`. Under the
Linux v7.0 [init-interpreter semantics](https://github.com/torvalds/linux/blob/v7.0/drivers/gpu/drm/nouveau/nvkm/subdev/bios/init.c),
the call is therefore taken on the false branch of condition `0x2c` (GPIO31
input bit 14 was set when sampled). The subroutine contains the `0x2000`
assert and `0x1000` release writes. The
`CONDITION_TIME 0x2c` inside that subroutine is a different opcode from the
script's condition test; it polls the same table condition and must not be
read as evidence that the main-script branch was taken.

This ordering and condition narrow the question but do not settle it. The
observed `0x02240c=0x2` is consistent with firmware reaching script 4, yet
does not establish that it also ran script 0, that the condition at `0x8d15`
was false, or that the GPIO31 subroutine completed. Unknown BIT tables and
the proprietary pre-Linux invocation path also limit what the decoded script
table proves.

The opt-in 0.1.11 diagnostic in
[`gk104-post-gpio31-trace.patch`](../patches/diagnostic/gk104-post-gpio31-trace.patch)
records the `0x02240c` value and computed POST decision, the effective
`execute` argument passed to `nvbios_post()`, and any reached interpreter
operation writing `0x00d68c` with value `0x2000` or `0x1000`. It adds no
register write or I2C transaction. The captured 0.1.11 boot has one decision
record (`r2240c=00000002 raw_post=0`), one callback record
(`execute=0`), and zero GPIO31 interpreter records. The DKMS module path and
`srcversion` matched the loaded module. The raw and effective POST decisions
agree, so no option override changed the result. This establishes that
Nouveau did not execute the POST scripts or these GPIO31 writes on the observed
boot; it does not show whether pre-Linux firmware had executed them. Even a
confirmed pulse would not identify GPIO31's electrical board function.

## Relation to the GOP transport result

The separate [GK104 PNVIO hardware-I2C transcript](GK104-PNVIO-HW-I2C-TRANSCRIPT.md)
and the 0.1.10 boot result in [STATIC-ANALYSIS.md](STATIC-ANALYSIS.md) show
that caller setup/recovery, 400 and 100 kHz hardware transfers, and the
GOP-specific software-line transaction all failed to obtain EDID data. The
new VBIOS pass adds no supported protocol variant and does not justify more
clock/retry experiments. It identifies only a conditional, function-unknown
GPIO31 pulse and a testable question about whether Nouveau executes its
containing init script.

The connected-only capture and all static evidence still cannot locate the
missing ACK on the physical path. A measurement at the VGA connector and,
if accessible, on both sides of any board-level buffer remains the direct way
to separate board-path failure from GPU pad/input-sense behavior.

## Safety and next steps

- No ROM binary is committed.
- No BAR access, register write, or new kernel diagnostic was performed for
  this documentation checkpoint.
- Do not add a GPIO31 toggle, `0xe600` write, `0xe1b8` write, or D014 upper-bit
  modification based on this audit.
- The 0.1.11 read-only POST/GPIO31 trace is complete; no further POST-decision
  trace is needed. Treat GPIO31 as an unknown board signal until separate
  evidence maps its net or behavior.
- The remaining distinction is physical: observe SDA/SCL at the VGA connector
  and, if accessible, on both sides of any board-level buffer. Software-side
  ACK logs alone cannot distinguish the remaining electrical explanations.
