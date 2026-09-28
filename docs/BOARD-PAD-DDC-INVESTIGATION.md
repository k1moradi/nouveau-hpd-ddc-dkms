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
All three XPIO subtables decode as `UNUSED`. EXTDEV entry 0 is an INA3221
with raw address `0x80` and bus selector 0. That selector is not CCB/I2C bus
index 0: the I2C table maps the primary-bus selector to CCB 2 on this ROM.
The INA3221 therefore does not share DVI-I's CCB 0 / PNVIO port 0 path.

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
| Main init script 4, `0xaba0` | `0xe600=a060a060`, `0xe60c=80000a0a`, `0xe610=00020000`, `0xe614=f83e0000`, `0xe618=80000a0a`, `0xe61c=00020000`, `0xe620=f83e0000` | Written by a register sequence. Their role in this board's DDC path is not established; do not infer one from adjacency. |
| Main init script 4, `0xaba0` | `0xe1b8` | Mask `0xfffdffff`, value `0x00020000`: bit 17 is set, selecting I2C1 in the documented `IBUF_ENABLE_0` mapping. |

EnvyTools describes `0xe500 + n * 0x50` as AUXCH `SETUP` space. Nouveau's
`g94_i2c_pad_mode()` also applies mode changes to this register family for
shared/hybrid pads. The K4200 maps the failing I2C0 bus to an exclusive port;
the four shared PNVIO buses are I2C6–9 and the associated AUX buses are
I2C10–13. Therefore these four zero writes do not establish a missing mode
write for the failing I2C0 path.

The register semantics of `0xe600–0xe620` remain unidentified. The user
reports finding the same seven writes and values in a GP107 VBIOS whose DCB
lists only DP/eDP/HDMI outputs. That ROM is not present in this workspace, so
the cross-board comparison has not been independently reproduced here. If
accurate, it demotes this sequence as a K4200-specific VGA/DDC switch; it
still does not establish the registers' function or rule out indirect effects.

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

## Cross-ROM comparison: K4200 `80.04.FE.00.15`

The candidate comparison image was extracted from
`/home/keivan/Downloads/272453.rom.zip` to
`/home/keivan/Downloads/272453.rom`. Its SHA-1
(`cf5120d7328483b88d6af3592bdc6a0a8a2f3328`) and MD5
(`12eace5948c367d31111550cc3d6cd56`) match the supplied listing values;
SHA-256 is
`c2c030d0acbeb2777bfcaa556052fd5242079da2a5bbcc7c41e6d278e93cd5cc`.
EnvyTools identifies VBIOS `80.04.FE.00.15`, PCI device `10de:11b4`, and
subsystem `10de:1096`; the image also contains the board string
`GK104 P2004 SKU 0503 VGA BIOS (HWDIAG)`.

The `.03` comparison input is the member of
`/home/keivan/Downloads/k4200-vbios.rom.tar.xz`. Its SHA-1, MD5, and SHA-256
match the already documented `/home/keivan/Downloads/k4200-vbios.rom` byte for
byte. Neither ROM binary is part of this repository.

### PCI option-ROM layout and legacy image

| Image | Raw size | Prefix | Legacy PCI image | EFI PCI image | Decompressed GOP |
|---|---:|---:|---:|---:|---:|
| `.03` | 184,320 (`0x2d000`) | none | offset `0`, size `0xf400` | offset `0xf400`, size `0x10e00` | 133,536 bytes, SHA-256 `ba478d3458e8323d20ac18de1034885aaf099a3d2c35edb0485a4c52599d8684` |
| `.15` | 221,696 (`0x36200`) | `NVGI`, `0x600` bytes | offset `0x600`, size `0xf400` | offset `0xfa00`, size `0x11200` | 135,776 bytes, SHA-256 `81f255ed9727ee6e3b426d6ea9b2ba647e85d1fb8f4a940dc4a067dda9f5fdbe` |

The `.15` legacy image begins at file offset `0x600`; both legacy images are
therefore aligned at their image starts before comparison. Their `0xf400`
bytes differ at 135 byte positions. The differing relative ranges are
`0x39`, `0x3b–0x3c`, `0xeb–0xec`, `0x25e`, `0x370`, `0xf231–0xf285`,
`0xf287–0xf2b0`, and `0xf3ff`. The entire half-open range
`[0x371, 0xf230)` is identical. The init-script table at `0x4f11`, main
script 0 at `0x8696`, GPIO31 subroutine `[0x8e70, 0x8e98)`, and main script 4
`[0xaba0, 0xac72)` are byte-identical. EnvyTools' decoded legacy output for
the two aligned images is identical after removing its single VBIOS-version
line; the same parser warnings appear for both.

This directly checks the previously audited structures: the decoded DCB/I2C
route and the legacy init scripts containing the `0xd68c`, `0xd604`,
`0xe1b8`, `0xe600–0xe620`, and `0x1590` operations are unchanged between
these revisions. The 135 differing bytes are outside the identical span;
their exact purpose is not inferred here.

### GOP DDC code comparison

The `.03` EFI image was extracted from the complete `.03` ROM. For `.15`, the
`0x600`-byte `NVGI` prefix was removed before passing the PCI option-ROM chain
to UEFIRomExtract. The extractor source is pinned at
[`a368a0fe0b757c7e234c1d28b1203fbb8f88d9c9`](https://github.com/ccharon/UEFIRomExtract/tree/a368a0fe0b757c7e234c1d28b1203fbb8f88d9c9).
The `.03` GOP hash reproduces the image used for the earlier protocol
transcript. The extracted PE `.text` section has matching RVA and raw-file
offsets for the ranges below. Each listed `.03` range was compared against
the corresponding `.15` range byte-for-byte:

| Routine | `.03` RVA | `.15` RVA | Compared bytes | Result |
|---|---:|---:|---:|---|
| D000 status waiter | `0x10d78` | `0x11468` | `0xbc` | identical |
| Enter software/D014 mode | `0x10e34` | `0x11524` | `0x80` | identical |
| Enter hardware mode | `0x10eb4` | `0x115a4` | `0x38` | identical |
| Sample SDA | `0x10eec` | `0x115dc` | `0x154` | identical |
| Wait for SCL | `0x11040` | `0x11730` | `0x8c` | identical |
| Hardware read | `0x110cc` | `0x117bc` | `0x188` | identical |
| Speed programming | `0x11254` | `0x11944` | `0xdc` | identical |
| Controller initialization | `0x11330` | `0x11a20` | `0x1d0` | identical |
| STOP/START | `0x11500` | `0x11bf0` | `0x88` | identical |
| Recovery | `0x11588` | `0x11c78` | `0x78` | identical |
| Software byte writer | `0x11600` | `0x11cf0` | `0xf8` | identical |
| Software byte reader | `0x116f8` | `0x11de8` | `0xeb` | identical |

All listed `.15` entry points are relocated by `0x6f0` from `.03`. A raw
search found the little-endian immediate for `0x0000d014` seven times in
each extracted GOP. It found no corresponding 32-bit immediate byte pattern
for `0xd018`, `0xe1b8`, `0xe600–0xe620`, `0x1590`, or `0xd68c`. This is a
bounded byte-pattern check, not a claim that those values cannot be formed by
other instruction sequences.

### Assessment

The later `.15` image does not provide evidence of a changed DVI-I board-init
sequence, DCB/I2C route, GPIO31 pulse, or low-level GOP DDC protocol. Its
standard PCI ROM chain includes a slightly larger GOP image, but the audited
DDC routines above are byte-identical after relocation. This comparison does
not identify a missing writable control and does not justify another GPIO,
IBUF, D014, or undocumented-register experiment. It leaves the same boundary:
the available ROM/GOP evidence does not distinguish a board-level DDC path
problem from an internal pad/input-routing issue.

Reproduction tools and inputs:

```text
EnvyTools nvbios source: f102b82381f3f11cee113d16374c87091db039d9
UEFIRomExtract source:  a368a0fe0b757c7e234c1d28b1203fbb8f88d9c9
.03 input SHA-256:      6c768eb2d34ab65bdde6137e66b5fbe750a750f9553aabff51e614e1168c4ed9
.15 input SHA-256:      c2c030d0acbeb2777bfcaa556052fd5242079da2a5bbcc7c41e6d278e93cd5cc
```

## NVIDIA BIT and display-script table audit

EnvyTools reports BIT `U` as unparsed, so this pass decoded the documented
BIT and display-script structures directly from the hash-verified `.03` ROM
above. The relevant format references are NVIDIA's [BIT specification](https://nvidia.github.io/open-gpu-doc/BIOS-Information-Table/BIOS-Information-Table.html),
[BIT display-pointer/display-script specification](https://download.nvidia.com/open-gpu-doc/pascal/1/BIT_DISPLAY_PTRS-U-BIT_DP_PTRS-d.pdf),
and [DCB 4.x specification](https://nvidia.github.io/open-gpu-doc/DCB/DCB-4.x-Specification.html).
The ROM binary remains outside Git.

The BIT header is at `0x01c0`. Its documented tokens give these results:

| Token | ROM value | Result |
|---|---|---|
| `2`, v1 | data at `0x024e`: `0000 0000` | Both the I2C-script-table pointer and external-hardware-monitor init-script pointer are null. |
| `B`, v2 | POST callback word `0x0000`; SYSTEM callback word `0x0000` | No BIT `B` callback is selected in either field. |
| `I`, v1 | private boot-script pointer `0xace4` | The byte at `0xace4` is `0x71`; EnvyTools decodes it as `DONE`, so this pointer does not lead to additional boot-script operations. |
| `U`, v1 | data at `0x0339`: `e6 4c 00` | Display-script table pointer is `0x4ce6`; flags byte is zero. |

The display-script table at `0x4ce6` has version 2.1, a 5-byte header, 2-byte
pointer entries, 25 entries, and 12-byte IED records. The non-null entries map
against the active DCB paths as follows. Resource masks below are the decoded
DCB output-resource bits; “unmatched” means no active DCB entry has the
corresponding type/location/resource combination.

| IED index | Key fields: type / location / output-resource mask / heads | DCB mapping |
|---:|---|---|
| 0 | CRT / 0 / `0xf` / `0xf` | DCB 1, analog CRT (the DVI-I VGA path) |
| 6 | TMDS / 0 / `0x1` / `0xf` | DCB 0, DVI-I digital TMDS |
| 7 | TMDS / 0 / `0x2` / `0xf` | DCB 4, TMDS |
| 8 | TMDS / 0 / `0x4` / `0xf` | DCB 6, TMDS |
| 9 | TMDS / 0 / `0x8` / `0xf` | Unmatched |
| 12 | DisplayPort / 0 / `0x1` / `0xf` | Unmatched |
| 13 | DisplayPort / 0 / `0x2` / `0xf` | DCB 3, DisplayPort |
| 14 | DisplayPort / 0 / `0x4` / `0xf` | DCB 5, DisplayPort |
| 15 | DisplayPort / 0 / `0x8` / `0xf` | Unmatched |
| 16 | TMDS / 1 / `0xf` / `0xf` | Unmatched |

IED 0 is the sole CRT-keyed row and matches DCB 1 on the applicable fields:
both are CRT, location 0, and their head/output-resource masks overlap DCB 1's
head mask and DAC 1 resource. Its key also contains sublink mask `3`, but the
DCB 4.x specification reserves the CRT-specific information word; sublink
assignment is specified for digital output types. That value therefore does
not establish a separate analog route.

The complete script chain for this analog IED is:

| Field | Value | Result |
|---|---:|---|
| IED flags | `0x01` | Bit 0 is reserved; the documented driver-skip (`0x02`) and manual-power (`0x04`) bits are clear. |
| Runtime count | 1 | One runtime selector entry. |
| InitScript | `0x0000` | No init script. |
| OffINT1 / OffINT2 | `0x0000` / `0x0000` | No off scripts. |
| Runtime Protocol / DeviceFlags | `0xff` / `0x00` | Protocol wildcard; no device flags set. |
| OnINT2 / OnINT3 | `0x52b2` / `0x0000` | One OnINT2 clock-mode list; no OnINT3 list. |
| SorClkMode at `0x52b2` | frequency `0`, script `0x52b6` | The only, zero-frequency fallback record points to `0x52b6`. |
| Script at `0x52b6` | opcode `0x71` | EnvyTools decodes `0x71` as `DONE`; no display-script operation follows. |

### Assessment and limits

This closes the previously unparsed, standard BIT `U` path for the analog
DCB 1 output: it supplies no analog init/off sequence and its only runtime
script is a `DONE` stub. Together with the null BIT `2` pointers, zero BIT
`B` callback words, and the BIT `I` private boot-script stub, this audit finds
no standard-table I2C/display initialization sequence that accounts for the
missing DDC ACK or identifies a writable DDC enable.

This is a format-aware decode of the recorded ROM, not proof that every
pre-Linux firmware path executed every table, nor that vendor-private or
computed-address code contains no relevant operation. It does not change the
existing safety conclusion: the audit provides no basis for speculative
GPIO, `e1b8`, `e600–e620`, `0x1590`, or D014 writes. A UEFI EDID-protocol
query remains a separate dynamic experiment; it is not answered by this
static table decode.

## BIT `p` PMU Init-From-ROM image

The K4200 ROM's BIT `p` v1 table identifies the deprecated pre-core82 PMU
Init-From-ROM image. Its fields resolve to logical code pointer `0x15198`,
size `0x5ff4` (24,564 bytes), image ID `0x0c`, and info structure
`0x64cf`. Applying the NVIDIA BIT specification's EFI-image adjustment puts
the image at file offset `0x25f98`, ending at `0x2bf8c`, within the
184,320-byte (`0x2d000`) ROM. The extracted `/tmp/k4200-pmu-ifr-kepler.bin`
matches that ROM slice byte-for-byte (SHA-256
`eab925408691f34683c6e6abae369f96782b426051f7e34c91a7a965937f2f2a`); the
ROM and extracted image remain outside Git.

The image is now decoded in its numbered 256-byte FUC4 blocks. A follow-up
address-flow pass found UAS-tagged `D[]` accesses: the IFR prefixes computed
register offsets with `0x14000000` and uses that path for the PNVIO
`D010/D014/D008` controller offsets. Its `e320/e32c` RMW sequence is guarded
by an unsigned `port <= 5` branch and therefore does not run for the failing
CCB0/port-0 path. The sequence remains unexplained for port indices above 5;
the exact UAS translation and this image's execution on the observed boot
remain limits. Nouveau's GK104 PMU firmware compiled into the driver is a
separate runtime firmware path. The instruction-level analysis and Linux pad
comparison are recorded in
[`GK104-FIRMWARE-RESIDUAL-AUDIT.md`](GK104-FIRMWARE-RESIDUAL-AUDIT.md).

## EXTDEV / ICCSENSE INA3221 bus mapping

The ROM's EXTDEV v4 entry at `0x55d2` is `4e 80 40 02`: INA3221 type,
8-bit address byte `0x80`, and bus-selector bit 0. Nouveau shifts the
address byte right once, giving Linux 7-bit address `0x40`; selector 0 means
`NVKM_I2C_BUS_PRI`, not CCB index 0.

The DCB I2C table at `0x5442` has header defaults byte `0x52`, printed by
EnvyTools as primary 2 / secondary 5. Linux v7 `nvkm_i2c_bus_find()` maps
`PRI` through the low nibble, so EXTDEV 0 resolves to CCB 2. The CCB 2 table
entry is PNVIO location 2. In the GF119-derived PNVIO bus implementation,
the MMIO address is `0xd014 + drive * 0x20`; CCB 2 therefore uses `0xd054`.
By contrast, DCB 0 and DCB 1 (the DVI-I digital and analog paths) both name
I2C 0, which is PNVIO location 0 at `0xd014`. The INA3221 is consequently
not a same-bus ACK discriminator for the failing DVI-I DDC path.

The K4200 ICCSENSE table at `0x7d6a` contains an active mode-1 rail entry for
EXTDEV 0 with three enabled 5-milliohm channels and configuration `0x7807`.
GK104's Nouveau chipset table selects the GF100 ICCSENSE implementation
([device table](https://github.com/torvalds/linux/blob/v7.0/drivers/gpu/drm/nouveau/nvkm/engine/device/base.c));
when its oneinit processes this entry, it validates an INA3221 by reading
ID registers `0xff` and `0xfe` at address `0x40` on the selected CCB 2 bus.
If validation succeeds, the later ICCSENSE init writes configuration
`0x7807` to register `0x00`. Thus the existing full ICCSENSE path is not a
read-only experiment. A successful INA response would demonstrate reception
on CCB 2, but would not establish reception on CCB 0 / `0xd014` or resolve
the DVI-I branch fault. No additional sensor probe or kernel patch is
justified by this table alone.

The mapping follows the Linux v7.0 Nouveau implementations of
[`nvkm_i2c_bus_find()`](https://github.com/torvalds/linux/blob/v7.0/drivers/gpu/drm/nouveau/nvkm/subdev/i2c/base.c),
[`nvbios_extdev_parse()`](https://github.com/torvalds/linux/blob/v7.0/drivers/gpu/drm/nouveau/nvkm/subdev/bios/extdev.c),
[`nvkm_iccsense_create_sensor()`](https://github.com/torvalds/linux/blob/v7.0/drivers/gpu/drm/nouveau/nvkm/subdev/iccsense/base.c),
and [`gf119_i2c_bus_new()`](https://github.com/torvalds/linux/blob/v7.0/drivers/gpu/drm/nouveau/nvkm/subdev/i2c/busgf119.c).

## GPIO20 `GENERIC_INITIALIZED` and GOP-computed access

The line-20 GPIO record is function `0x30` (`GENERIC_INITIALIZED`) with
default 0, which decodes to input/released. Script 0's condition at `0x89b7`
selects between `GPIO_NE` (whose exclusion list omits `0x30`) and a following
`INIT_GPIO`; after the `NOT`, either enabled branch resets GPIO20 to that
default if the script executes. The observed Linux boot had `execute=0`, so
Nouveau did not run this script path. Pre-Linux execution is not known.

The GOP also contains a generic table-driven GPIO helper computing
`0xd610 + 4*line`, which can address `0xd660` for line 20. This closes the
literal-only search gap, but it does not prove that firmware invoked the
helper for GPIO20 during boot. The decoded PMU IFR and GOP data-flow findings
are summarized, with their limitations, in
[`GK104-FIRMWARE-RESIDUAL-AUDIT.md`](GK104-FIRMWARE-RESIDUAL-AUDIT.md).
