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
- failure in both phases: weakens the entire DAC/load-detect-state family.

### Follow-up result: both DAC phases fail

The 2026-09-26 connected-monitor run used `--diag-dac-ddc --diag-d014-sense`.
Repeated `DAC_POWERED_DDC` records failed both immediately after entering the
non-normal DAC state and after the load-sense delay: `i2c_transfer()` returned
`-5` (`-EIO`) with `edid0=ff`. This is a different errno from the earlier
`-ENXIO` probes, but neither phase completed the two-message transfer. The
same boot recorded 72 ACK-slot samples, all `0xd014=0x37` with `ack=0`.

The verifier reported `PRE_LOAD_DDC: NO TRACE SAMPLE`, meaning it did not find
the complete baseline I2C trace sequence it expects before the first DAC
diagnostic. This does not negate the phase results or ACK-slot samples, but the
run has no parsed baseline trace for a direct before/after comparison. No
firmware-EDID snapshot was requested in this run; a prior enabled snapshot was
all zeroes and invalid.

Result: neither the non-normal DAC state nor active load sense after the
settling interval was sufficient to make the sampled `0x50` transfer succeed.
The DAC/load-sense-state family is now **low confidence**. This does not prove
the physical connector voltage or exclude every DAC-related side effect.

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

The connected-monitor capture logged 32/32 allowed transitions. The sampled
bits 4/5 tracked the commanded low/release states in these records; repeated
reads while both lines were released were stable at `0x37`. At all 72 captured
address ACK slots, the three reads were also `0x37` and Nouveau reported
`ack=0`. This confirms the register responds to the observed transitions, but
does not distinguish external-pad sensing from local loopback or an isolated
path. No persistent register change is justified by the current evidence.

The full 184,320-byte ROM referenced in earlier notes is not present in the
canonical repository or the currently available Downloads directory. The
VBIOS conclusions above therefore remain limited to the previously recorded
DCB, encoder-script, GPIO31, and `e1b8` decodes; a fresh neighborhood-wide
register-write audit requires that ROM or a complete EnvyTools decode.

### Standard DDC-routing GPIOs

EnvyTools defines explicit VBIOS GPIO functions including `I2C_OR_DDC`, `I2C_SCL_KEEPER_CIRCUIT_ENABLE`, and `DVI_DAC_SWITCH`. The decoded K4200 GPIO/mux evidence has not exposed an active matching control for this DVI-I path. This further weakens a conventional missing GPIO mux/enable theory, though it cannot rule out a vendor-private board net.

## Current candidate ranking after static analysis and diagnostics

1. HPD-low detect logic bug — **confirmed and fixed**.
2. DDC address-phase no-ACK at `0x50` — **confirmed remaining symptom**.
3. Undocumented board-level level-shifter/rail/reset dependency — **low-medium, and the strongest remaining board-specific family**.
4. D014 input-bit external-pad semantics — **unresolved after read-only capture; no write justified**.
5. DAC non-normal/load-detect state or active load-sense state changes the electrical DDC environment — **low after repeated failure in both diagnostic phases**.
6. Undocumented target-bus IBUF state — **low**.
7. GPIO31 direct DDC enable — **very low**.
8. Known `e1b8` VBIOS sequence initializes target I2C0 — **very low**.
9. Exclusive `pad_x` missing the known hybrid `.mode` programming — **very low**.
10. Wrong DCB bus index — **very unlikely**.
11. Generic bitbang implementation defect — **very unlikely**.
12. Missing analog encoder script — **very unlikely**.

## Static-analysis ceiling

The remaining uncertainty is increasingly about undocumented electrical
behavior inside the GPU/board rather than an identifiable missing Nouveau
software step. Static analysis cannot determine the PCB destination of GPIO31,
the D014 input bits' electrical sampling point, or the function of a private
board-level DDC control.

The DAC/load-detect probe failed in both phases, and the D014 drive/sense
capture is complete. The full 184,320-byte K4200 VBIOS was subsequently
captured through Nouveau's read-only debugfs vbios.rom file and analyzed
without BAR access. The ROM binary remains outside Git. Its GOP controller
transcript is preserved in
[GK104-PNVIO-HW-I2C-TRANSCRIPT.md](GK104-PNVIO-HW-I2C-TRANSCRIPT.md).
That transcript now gates a narrowly scoped GK104 hardware-DDC diagnostic;
the remaining uncertainty is whether that controller can retrieve block 0
from this connected monitor.

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

An initial 2026-09-26 attempt produced no ACK-slot or matrix samples. Root
inspection showed the loaded module's `config` parameter was `(null)`, and the
generated dracut initramfs contained the DKMS module but not
`/etc/modprobe.d/99-nouveau-i2c-test.conf`. The option had only been staged on
the root filesystem, after Nouveau was loaded from the initramfs. The installer
now adds that modprobe file to dracut's `install_items` before refreshing the
image. Subsequent boots confirmed the internal I2C path and captured the
samples described in Static target 4. An enabled firmware snapshot from an
earlier boot contained all zeroes and an invalid header; it was not enabled for
the later DAC-phase run.

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

## GK104 GOP-backed hardware DDC diagnostic

The opt-in patches/diagnostic/gk104-pnvio-hw-ddc.patch is enabled only by
install.sh --diag-pnvio-hw-ddc. It preserves the high DCB nibble as speed_sel
and registers a dispatcher only when all of these are true: chipset GK104,
physical register 0xd014, and selector 3. For this K4200 GOP, selector 3 maps
to 400 kHz.

The first hardware transactions are limited to the exact Linux DRM EDID
block-0 shapes: msg0 address 0x50, flags 0, one-byte offset 0; msg1 address
0x50, flags I2C_M_RD, length 1 or 128. The one-byte DDC-presence probe uses
the same GOP read helper. Its length argument controls the remaining-byte
loop; for length 1, the helper emits one final D000 read command (`0x90000057`)
and reads one byte from D00C. The transcript records this derivation along
with D004 address packing, D008/D010 setup, completion status, 128-byte
four-byte continuation, final stop marker, timeout, and cleanup. The ROM is
not stored in the repository.

The custom dispatcher chooses the backend before acquiring the bus. Hardware
takes NVKM bus/pad once. Unsupported shapes call exported i2c_bit_algo directly
and let its pre_xfer/post_xfer callbacks acquire/release. Adapter functionality
comes from the same Linux algorithm. Hardware errors are never replayed through
bitbang. The exact read-only EDID probe/base-block forms may receive the GOP's
single 400-to-100 kHz hardware retry after bounded D014 line recovery; a
read-only preflight rejection can instead fall back after releasing the NVKM
lock.

The diagnostic stages config=NvI2CHw=1, not NvI2C=1. Unsupported-shape
records include bounded details for both messages, including flags, lengths,
and the first write byte. The verifier reports the active option, one-byte
probe and full-read counts, transfer selector and shape, controller result,
module/DKMS match, and EDID header. The earlier boot reached no hardware
transfer because the dispatcher admitted only length 128; it is inconclusive
about the controller itself.

### First hardware-backed boot

The 2026-09-26 verifier output confirms the DKMS module path and loaded
`srcversion` match, and `NvI2CHw=1` is confirmed by the dispatcher's kernel
records. The backend started and returned from 40 exact one-byte DRM DDC
presence probes. Every result was `ret=-5`, `bytes=0`, `chunks=0`,
`status=30000050`, with `d014_restored=00000037`. No 128-byte read followed
because the one-byte presence probe failed; the DVI-I EDID sysfs file remained
empty.

The result's controller status field is `(0x30000050 >> 29) & 3 == 1`; the
Linux diagnostic maps any nonzero completed status to `-EIO`. At the time,
the GOP waiter's status-1 return path had not yet been documented. These
records prove the hardware backend was entered, but they do not show whether
the D004 write or D000 read command occurred. The logged `0xd014` value shows
that the original low control bits were restored after each attempt.

The start records also show the first attempt began with `D008=0001010e`,
while subsequent attempts began with `D008=010a0043`; each attempt ended with
`D014=00000037` restored. This is consistent with the GOP-derived D008 setup
persisting across attempts and the line-control bits being restored. It does
not establish that the first implementation issued a D000 read command.

The ROM audit resolves the wait-call ordering. In GOP helper RVA 0x110cc, the
status waiter at 0x10d78 is called from 0x11127 after checking that the
destination and length are nonzero. Its return is ignored; the helper then
writes D004 at 0x11158 and issues the D000 read command at 0x111b6. The next
wait at 0x111bc is checked at 0x111c1, and failure skips reading D00C. A final
wait at 0x11211 precedes the STOP command and is ignored. The 40 earlier
records therefore remain ambiguous, but they may all reflect the pre-command
wait that the first Linux implementation incorrectly treated as fatal.

The diagnostic follow-up now records D000 before controller setup, the
pre-command wait result, D004, the last D000 command, command-completion
status, final-wait result, and `failure_stage`. It follows the GOP by
continuing after a nonzero pre-command wait, checking the per-command wait,
and recording but ignoring the final-wait return. D008 programming, command
values, fallback behavior, and the no-replay rule are unchanged. The next
boot result is recorded below.

### Second hardware-backed boot

The verifier reports that the loaded module path and `srcversion` match the
DKMS build, `NvI2CHw=1` is confirmed by dispatcher logs, and the diagnostic
marker is present. It counted 36 hardware starts and results, all for the
one-byte presence probe; there were no successful probes and no 128-byte
reads. The ACK-slot sampler reported zero samples, as expected because this
boot enabled `NvI2CHw=1`, not `NvI2C=1`.

Every displayed attempt has the same result shape:

```text
failure_stage=read-command
pre_idle_ret=0
d004=00000050
command=90000057
command_status=30000050
ret=-5 bytes=0 chunks=0
final_wait_ret=-5 final_status=30000050
d014_restored=00000037
```

The first attempt began with `d000_before=10000000` and
`d008=0001010e`; subsequent attempts began with `d000_before=00000000` and
`d008=010a0043`. Since every pre-command wait returned zero and every record
shows `failure_stage=read-command`, the diagnostic did issue the GOP-derived
read command. Bit 31 of `0x30000050` is clear, and its decoded status field is
1. Thus this is a completed command with a non-success GOP result, not the
pre-command failure from the earlier run and not a busy-bit timeout. The
final wait sees the same status; the GOP ignores that final wait and proceeds
to cleanup. The display driver never reaches the 128-byte request because
the one-byte probe fails.

The ROM audit shows that its status waiter returns internal value 2 for
status field 1 on the ordinary completed path. The request dispatcher treats
only a zero helper result as success and sends nonzero results into its
failure/retry path. This confirms status 1 is a GOP-level transfer failure,
but does not establish that it means an address NACK. The waiter routes
status fields 2 and 3 through the separate object-flag check at offset
`0x30`; it can return zero or `-1` depending on that flag. The Linux
diagnostic's generic `status != 0` mapping must not be generalized into a
production implementation.

The GOP EDID reader's success-gated method calls were resolved by the later
caller/vtable audit below. The 100 kHz diagnostic reproduces the dispatcher's
400-to-100 retry, but its first 400 kHz attempt omitted the caller's initial
line recovery and the speed setter's wait-plus-STOP sequence. The hardware
status therefore is not yet an exact comparison with the GOP's initial read
sequence. The later `nve4_fuc084*` firmware-load failures in the verifier
occurred minutes after these DDC attempts and concern video-decode firmware;
the log does not connect them to the DDC status.

### GOP-derived 100 kHz retry audit

The local K4200 GOP transcript now covers the 0x11c60 fallback and its
0x11dc0 recovery callback. A completed nonzero read-helper result at 400 kHz
selects 100 kHz, programs D008 with `(old & 0xfffff10e) | 0x10e`, performs
the ignored status wait and STOP command, then invokes the bus recovery
callback. If SDA is observed high during its bounded recovery, the dispatcher
recurses through the same hardware-read helper. The Linux diagnostic mirrors
only this 400-to-100 hardware retry for the already-admitted offset-zero EDID
probe and base-block read. It does not add the GOP's later 60 kHz path or
replay through bitbang.

The recovery callback puts D014 into software line mode, waits 10 microseconds,
then performs at most 16 cycles of SCL low/high and SDA sampling. Each cycle
also invokes the GOP's 0x11588 line sequence, which waits up to six 1-microsecond
polls for SCL to rise. The Linux diagnostic uses Nouveau's existing D014
drive/sense callbacks for those lines, restores the saved low three D014 bits
at transfer exit, and retains 100 kHz for subsequent diagnostic EDID requests.
The hardware behavior still requires monitor-connected verification; static
agreement with the GOP transcript is not a runtime success.

### EDID caller vtable and 100-to-60 software fallback audit

The full K4200 ROM (`k4200-vbios.rom`, SHA-256 recorded in the transcript) was
re-extracted and the decompressed EFI image hash matched the permanent
transcript. At RVA `0x1aec0`, the EDID reader loads its bus object from
`[this+8]`, then calls vtable slots `+0x10`, `+0x70`, and `+0x68`. The vtable
at RVA `0x1120`, installed by the constructor at `0xcc60` (instruction
`0xccaa`), maps those slots to controller initialization `0x11330`, line
recovery `0x11dc0`, and request dispatcher `0x11c60`. The caller ignores the
initializer's return value, skips the transfer unless recovery returns true,
and then calls the dispatcher. Thus the pre-read methods are specifically
the same K4200 bus methods used by the later hardware transfer.

That caller order identifies two steps absent from the first Linux hardware
diagnostic attempt:

- GOP initialization `0x11330` calls the status waiter, writes D010=`0xf4240`,
  enters hardware mode, invokes speed setter `0x11254`, then applies the D008
  initialization mask. The speed setter itself writes the rate field, calls
  the waiter and ignores its result, then writes D000 STOP=`0x8000000c`.
  Linux currently writes D010, D014, and D008 init/speed fields directly; it
  does not issue that initialization wait-plus-STOP.
- The EDID caller invokes recovery `0x11dc0` after initialization and before
  the first transfer. Linux currently recovers only after a failed 400 kHz
  attempt, as part of the 100 kHz retry.

The command failures at 400 and 100 kHz therefore weaken a clock-only theory,
but do not rule out a missing pre-read sequence. The next controller
comparison should reproduce these exact caller-side steps before treating the
first hardware attempt as GOP-equivalent.

The same dispatcher has a further path after a failed 100 kHz command: it sets
the rate field to 60 kHz, invokes the speed setter (including its wait and
STOP), calls line recovery again, then recurses into software transfer
`0x11b34`. The 60 and 100 kHz cases use the same D008 mask/value. The
dispatcher changes the rate field, but the software bit timing uses the bus
`+0x24` delay period. Helper `0x11e58` can recalculate that period; this
100-to-60 transition calls `0x11254` directly and does not call `0x11e58`, so
the exact line clock is not established as 60 kHz from this path alone.

The 60 kHz software transaction is materially different from the Linux
two-message EDID request. For base block 0, routine `0x11b34` uses the
arguments from the EDID reader to issue:

```text
START, 0x60 0x00, REPEATED START,
0xa0 0x00, REPEATED START, 0xa1,
read bytes (ACK except final NACK), STOP
```

Here `0x60` is the segment-pointer wire address (7-bit `0x30`), followed by
segment zero. Linux's tested sequence begins at `0xa0 0x00` and then repeated
starts at `0xa1`; the extra segment-pointer write has not been exercised by
the prior Nouveau bitbang runs. This makes the GOP's final software fallback
a distinct, bounded diagnostic candidate after the caller-side initialization
and initial recovery sequence are reproduced.

### Third hardware-backed boot

The 0.1.8 verifier reports a matching loaded DKMS module and active
`NvI2CHw=1`: 38 starts/results, one 400 kHz attempt, 38 100 kHz attempts, one
successful recovery cycle, zero successful one-byte probes, and no 128-byte
reads. The first attempt returned `0x30000050` after command `0x90000057` at
400 kHz. The 100 kHz retry changed D008 from `0x010a0043` to `0x010a010e`,
completed one recovery cycle, then returned the same command status with zero
bytes. Subsequent direct 100 kHz attempts returned the same status. Status
field 1 remains an unnamed GOP transfer failure, not a proven address NACK.

On direct 100 kHz attempts, the `phase=attempt rate_khz=100 ret=-5` record is
the primary result. The final `attempt400_ret`/`attempt100_ret` fields are
misleading for those transfers: they contain the primary result and the
unused retry slot (`-EOPNOTSUPP`), not a fresh 400 kHz result and a 100 kHz
retry result. Use the phase-specific attempt records when interpreting this
boot.

### Caller-sequence diagnostic follow-up

The 0.1.8 boot does not establish an exact GOP/Nouveau comparison. Static
analysis found that the GOP caller performs an initial ignored D000 wait,
programs the rate, performs the speed-setter's ignored wait and STOP, applies
the initializer D008 mask, and runs recovery `0x11dc0` before the first read.
The 0.1.8 Linux diagnostic lacked that ordering and recovery before its first
400 kHz command.

Version 0.1.9 adds those steps in the recovered order. It logs
`phase=caller-init` with both wait results and D008 snapshots, then
`phase=caller-recovery` with the recovery result, cycles, and D014 state. It
issues no EDID command or 100 kHz retry if caller recovery fails. After a
successful recovery it re-enters hardware mode and retains the existing
400-to-100 retry behavior. This change is diagnostic code only; its hardware
result remains pending until the next connected-monitor boot.
