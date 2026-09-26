# K4200 Nouveau DDC static-analysis checkpoint

## Scope

Hardware under investigation:

- NVIDIA Quadro K4200, GK104GL
- PCI ID `10de:11b4`, subsystem `10de:1096`
- DVI-I -> passive DVI-to-VGA -> VGA monitor
- Linux/Nouveau detects the analog load but does not obtain EDID
- The same physical path reads EDID under Windows
- A manually supplied 1920x1080 reduced-blanking mode works, so the analog RGB path itself is functional

The objective is an upstream-quality kernel/root-cause fix, not a userspace modeline, EDID override, global kernel parameter, or boot-time script.

Canonical project base used for this checkpoint:

`a71c9f65ae09bc63414464ec859561e7df449da2`

## Confirmed bug already fixed

`nvkm_outp_detect()` handled HPD-low inconsistently with its own comment. For a non-DP output it returned the low GPIO result (`0`, NOT_PRESENT), which prevented DRM from probing DDC. The project patch changes the non-DP HPD-low path to `-EINVAL`, which maps to UNKNOWN and allows DDC probing.

Observed behavior changed from `nvif_outp_detect -> ret=0` to `ret=2` (UNKNOWN), and DDC probing now occurs. This part is considered confirmed.

## Confirmed remaining failure

DDC reaches address `0x50` on Nouveau I2C bus 0 but the address phase is not acknowledged:

```text
i2c_write: i2c-0 #0 a=050 ... [00]
i2c_read:  i2c-0 #1 a=050 ...
i2c_result: i2c-0 n=2 ret=-6
```

`-6` is `-ENXIO` in this path. The failure occurs before EDID payload parsing, so checksum/parser issues are not implicated.

All eight available Nouveau PNVIO buses were probed at `0x50` and returned ENXIO. Both generic `i2c-algo-bit` and Nouveau internal bitbang (`NvI2C=1`) fail. This makes a wrong DCB bus index or a generic bitbang algorithm defect unlikely.

For GF119/GK104 PNVIO bus 0 the bitbang register is `0x00d014`. Sampling during EDID traffic showed values `0x04`, `0x15`, `0x26`, and `0x37`, which vary with the commanded state. At each captured address ACK slot Nouveau read `0x37` (bits 4/5 both high). EnvyTools names these bits `SCL_IN` and `SDA_IN`, and Nouveau uses them as sense inputs, but neither source says whether they reflect the external pad level or a local loopback/status path. The observed high SDA therefore describes Nouveau's register read; it does not independently prove the physical connector's SDA level.

## VBIOS / connector facts

Decoded DCB data ties both the DVI-I TMDS and analog outputs to I2C index 0 and connector 0. The connector is DVI-I with HPD_0. The I2C entry is PNVIO location 0.

The previously suspicious DCB `unk00_4 = 3` field is an I2C speed selection, not a routing/mux selector.

Analog encoder scripts decode as `0x0000`, so there is no obvious omitted analog encoder-init script to replay.

## Static target 1: exclusive PNVIO pad `.mode`

Hypothesis investigated: `gf119_i2c_pad_x_func` accidentally lacks `.mode`, so an exclusive GK104 PNVIO pad never gets the `0xe500/0xe50c` I2C/AUX routing writes used by shared pads.

Conclusion: **strongly weakened / very low confidence**.

Evidence:

- Linux v7.0 deliberately defines `.mode = g94_i2c_pad_mode` for the shared/hybrid GF119 pad, but not for the exclusive pad.
- This distinction predates the 2015 object-model refactor. Pre-refactor GK104 used the generic `nv04` pad class for exclusive pads and the `nv94` mode-switching pad class for shared pads.
- The same architectural pattern persists across adjacent/later families.
- `g94_i2c_pad_mode()` calculates its register base from `NVKM_I2C_PAD_HYBRID(0)`, so attaching it mechanically to an exclusive pad would produce the wrong register addressing model.
- EnvyTools prints an explicit `shared N` field when the CCB shared flag is present. The recorded K4200 I2C0 decode has PNVIO location 0 and no shared field, consistent with construction as an exclusive pad.

Do not add `g94_i2c_pad_mode()` to `pad_x` without new contradictory evidence.

## Static target 2: DAC/load-detect side effects

The DRM connector path tries DDC before analog load detection. Only after DDC failure does the analog path call `nv50_dac_detect()` and then `nvif_outp_load_detect()`.

The full load-detect call chain is:

```text
nouveau_connector_detect
  -> nouveau_connector_ddc_detect / drm_get_edid
  -> detect_analog
     -> nv50_dac_detect
        -> nvif_outp_load_detect
           -> nvkm_uoutp_mthd_load_detect
              -> nvkm_outp_acquire_or
              -> nv50_dac_sense
              -> nvkm_outp_release_or
```

`nvkm_outp_acquire_ior()` / `nvkm_outp_acquire_or()` only establish software ownership (`outp->ior`, `ior->asy.outp`, link/acquired state). They do not program GPIO, PNVIO, DAC power, or BIOS scripts.

On GK104 the DAC implementation uses `nv50_dac_power()` and `nv50_dac_sense()`.

`nv50_dac_sense()` performs the following hardware-changing sequence:

1. `dac->func->power(dac, false, true, false, false, false)`
2. write `0x00100000 | loadval` to `0x61a00c + doff`
3. wait about 9.5 ms
4. read/clear `0x61a00c + doff`
5. disable the non-normal DAC state

There is no load-detect-time VBIOS script invocation and no GPIO API call hidden in this path.

A key refinement is that Nouveau display init already calls each IOR power callback with `normal=true, pu=true, data=true, vsync=true, hsync=true`. `nv50_dac_power()` uses a different register field when `normal=false` (the load-detect path shifts the control bits by 16). Therefore the remaining hypothesis is **not simply “DDC needs the DAC powered.”** It is more specifically that the DAC's **non-normal/load-detect control state**, active load-sense state, or an electrical side effect of those states may make DDC responsive.

This makes the two-phase diagnostic especially discriminating:

- immediate success after `normal=false` power programming, before the load-sense write: implicates the non-normal DAC control state;
- immediate ENXIO but settled/load-sense success: implicates active load-sense state or settling time;
- ENXIO in both powered phases: substantially weakens the entire DAC/load-detect-state family.

## Static target 3: GPIO31 / `0x00d68c`, `0x00d604`, and `0x00e1b8`

### GPIO31 mapping

On GF119/GK104 GPIO hardware the per-line registers are at `0x00d610 + line * 4`. Therefore:

```text
0x00d610 + 31 * 4 = 0x00d68c
```

So `0x00d68c` is definitively GPIO31. `0x00d604` is the GPIO output update/trigger register used by Nouveau after changing output state.

Nouveau's GF119 GPIO drive encoding uses:

```c
data = ((dir ^ 1) << 13) | (out << 12);
```

Thus the observed VBIOS sequence states decode as:

- `0x2000`: GPIO31 configured as an output and driven low;
- `0x1000`: GPIO31 released/input-disabled from output drive, with high output latch state.

The sequence is therefore best understood as a deliberate **active-low assertion/pulse followed by release**, not a persistent “DDC enable high” state.

The electrical PCB net attached to GPIO31 is not documented by Nouveau/EnvyTools. It could theoretically reset a board device, but there is no static evidence tying it specifically to DDC. The same GPIO register/trigger mechanism is also used by Nouveau for unrelated board functions such as memory-voltage/VREF control, so the raw register sequence is not display-specific.

Result: direct “GPIO31 is the missing DDC enable” is **very low confidence**. A more generic “GPIO31 resets some external board device that indirectly affects DDC” remains **low confidence** and is not resolvable statically without board documentation or a proprietary-driver trace.

### `IBUF_ENABLE_0` mapping

EnvyTools documents `0x00e1b8` as `IBUF_ENABLE_0`: it enables input buffers and does not affect output functionality. Bits 16-19 map to I2C0-I2C3.

The K4200 VBIOS operation:

```text
R[e1b8] &= fffdffff
R[e1b8] |= 00020000
```

clears and then sets **bit 17**, which is documented as I2C1. The failing DVI-I DDC path is I2C0 (`0x00d014`). Therefore the known VBIOS `e1b8` sequence is not an initialization of the failing I2C0 input buffer under the documented mapping.

The diagnostic readback `IBUF_ENABLE_0=ffffffff` must not be interpreted as “all input buffers enabled.” Static documentation does not establish reliable GK104 readback semantics for this register; all-ones may be genuine, read-as-ones, gated/inaccessible, or otherwise misleading. No write experiment should be based only on this readback.

Result: “Nouveau failed to replay the K4200 VBIOS's target-bus IBUF write” is **very low confidence**. A different undocumented I2C0 input-buffer state remains **low confidence** only because the register read semantics are unresolved.

## Static target 4: GF119 PNVIO D014 input semantics and initialization write

The DCB-selected physical bus-0 bitbang register is `0x00d014`. EnvyTools
documents bits 0/1 as `SCL_OUT`/`SDA_OUT`, bits 2/3 as the mode field, and bits
4/5 as `SCL_IN`/`SDA_IN` on GF119 and later. Nouveau reads bits 4/5 from this
register in its `sense_scl` and `sense_sda` callbacks.

This is a long-standing driver model, not a new v7.0 interpretation. Linux
[v4.2 `gf110.c`](https://github.com/torvalds/linux/blob/v4.2/drivers/gpu/drm/nouveau/nvkm/subdev/i2c/gf110.c)
already read bits 4 and 5 for SCL/SDA sense and initialized the port with state
`0x7`; the 2015 refactor moved the same behavior into
[`busgf119.c`](https://github.com/torvalds/linux/commit/2aa5eac5163f).
The current [EnvyTools PNVIO description](https://github.com/envytools/envytools/blob/master/rnndb/io/pnvio.xml)
uses the same `*_IN` names. These sources establish that Nouveau has treated
the bits as inputs for years, but they do not establish whether GK104 samples
the external pad, a local output loopback, or another internal status point.

The init value `0x7` sets the two output-release bits and selects bitbang mode
through bit 2. The documented bit 3 is the upper mode bit and is cleared; bits
4/5 are named inputs. The register description leaves higher bits unnamed, so
an undocumented writable field cannot be excluded, but neither Nouveau
history nor EnvyTools documents a DDC-routing function cleared by this write.
`0xd018` is a separate undocumented per-port register and is not touched by
`0xd014 = 0x7`.

The previous opt-in IBUF snapshot logs the full `0xd014` value immediately
before and after the existing init write. A narrower `--diag-d014-sense`
capture now does the same without reading `0xe1b8`, then samples after up to 32
ordinary DDC line-drive transitions for address `0x50` on physical register
`0xd014`. Each captured transition adds a 1 μs settle delay, up to 32 μs total;
the sampler adds no line-drive operations or DDC transactions. When both output
bits are released, it performs two additional immediate reads, giving three
samples for that released state. Only the first 32 transitions are logged for
the lifetime of the bus.

This capture can establish whether the reported sense bits change with
Nouveau's own drive/release commands and whether repeated reads are stable. A
perfect match would strengthen local-loopback or isolated-path possibilities;
different input values would show the sampled state can diverge from the
commanded output, but would not prove the exact point in the electrical path
being sensed. No persistent register change is justified by the current
evidence.

The full 184,320-byte ROM referenced in earlier notes is not present in the
canonical repository or the currently available Downloads directory. The
VBIOS conclusions above therefore remain limited to the previously recorded
DCB, encoder-script, GPIO31, and `e1b8` decodes; a fresh neighborhood-wide
register-write audit requires that ROM or a complete EnvyTools decode.

### Standard DDC-routing GPIOs

EnvyTools defines explicit VBIOS GPIO functions including `I2C_OR_DDC`, `I2C_SCL_KEEPER_CIRCUIT_ENABLE`, and `DVI_DAC_SWITCH`. The decoded K4200 GPIO/mux evidence has not exposed an active matching control for this DVI-I path. This further weakens a conventional missing GPIO mux/enable theory, though it cannot rule out a vendor-private board net.

## Current candidate ranking after static analysis

1. HPD-low detect logic bug — **confirmed and fixed**.
2. DDC address-phase no-ACK at `0x50` — **confirmed remaining symptom**.
3. D014 input-bit electrical mapping or undocumented init side effect — **unresolved, worth read-only capture; no write justified**.
4. DAC non-normal/load-detect state or active load-sense state changes the electrical DDC environment — **medium-high as a focused, testable mechanism; not proven**.
5. Undocumented board-level level-shifter/rail/reset dependency — **low-medium, but near the static-analysis ceiling**.
6. Undocumented target-bus IBUF state — **low**.
7. GPIO31 direct DDC enable — **very low**.
8. Known `e1b8` VBIOS sequence initializes target I2C0 — **very low**.
9. Exclusive `pad_x` missing the known hybrid `.mode` programming — **very low**.
10. Wrong DCB bus index — **very unlikely**.
11. Generic bitbang implementation defect — **very unlikely**.
12. Missing analog encoder script — **very unlikely**.

## Static-analysis ceiling

The remaining uncertainty is increasingly about undocumented electrical behavior inside the GPU/board rather than an identifiable missing Nouveau software step. Static analysis cannot determine the PCB destination of GPIO31 or the exact electrical side effects of the DAC load-detect registers on this board.

The two-phase DAC/load-detect DDC diagnostic is available as an opt-in probe.
The ACK-slot sampler has captured the register's input bits, but their external
pad semantics remain unvalidated. `--diag-d014-sense` is the next
software-only discriminator.
Do not invent a permanent register write before observing those results.

## Experiment safety

Prefer:

- static source/VBIOS analysis;
- read-only instrumentation;
- existing Nouveau abstractions and normal locking;
- minimal diagnostic patches that preserve raw I2C return codes.

Avoid:

- `nvapoke` or live arbitrary BAR writes;
- speculative `e1b8` writes;
- global userspace workarounds presented as fixes;
- hard-coded bus indices when `outp->i2c` already identifies the DCB-selected bus;
- adding hybrid-pad `.mode` handling to exclusive pads without new evidence.

## ACK-slot diagnostic

`patches/diagnostic/ack-slot-sampler.patch` adds an optional sampler to the
internal bit-bang algorithm. With `--diag-ack-slot`, the DAC load-detect probe
generates known EDID `0x50` transactions and Nouveau samples physical PNVIO
register `0xd014` three times during each address ACK slot. The first raw read
continues to decide the transaction result; the other reads are observational.
The sampler does not write PNVIO registers and is restricted by the physical
register address, not Nouveau's encoded bus ID.

The diagnostic build defines `CONFIG_NOUVEAU_I2C_INTERNAL`, which Ubuntu's
headers otherwise leave unset and which gates compilation of this existing
implementation. The diagnostic boot sets `config=NvI2C=1` so that algorithm
runs. Under the documented input interpretation, SCL bit 4 high with SDA bit
5 low is consistent with an ACK, while SDA bit 5 high is consistent with no
ACK at the sampled input. The input-to-pad mapping remains unvalidated. Because
this test keeps the monitor connected, even a confirmed external pad sample
could not distinguish a monitor response from an intervening board-level
buffer or path failure. Missing samples are inconclusive until the active
module configuration confirms `NvI2C=1`.

## Firmware EDID snapshot

`patches/diagnostic/firmware-edid-snapshot.patch` adds a separate opt-in,
read-only diagnostic enabled with `--diag-firmware-edid`. It runs when Nouveau
falls through to analog detection and only for DVI-I. The diagnostic checks
whether the PCI device is marked as the firmware-primary display GPU, then
examines the 128-byte base EDID retained in `sysfb_primary_display`. It logs
the header score, base-block checksum, analog/digital input bit, extension
count, and both 64-byte halves of the raw block.

The diagnostic does not depend on legacy fbdev, call `fb_firmware_edid()`,
attach the firmware data to a DRM connector, add modes, or write hardware. Its
validity test covers only the base block because this firmware handoff retains
128 bytes even when the EDID advertises extensions. A valid analog base block
shows that firmware transferred an analog EDID, but does not alone prove that
it belongs to the currently connected monitor. The flag can be combined with
`--diag-ack-slot` so firmware and live DDC evidence are captured in one boot.
