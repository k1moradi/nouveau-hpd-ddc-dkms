# drm/nouveau: DVI-I analog display skipped when HPD low; DDC fallback contradicts nvkm_outp_detect() comment

## Hardware / software

- GPU: NVIDIA Quadro K4200, GK104GL, PCI 10de:11b4, subsystem 10de:1096
- VBIOS: 80.04.fe.00.03
- Connection: GPU DVI-I -> passive DVI-to-VGA adapter -> VGA cable -> monitor
- Ubuntu/Lubuntu 26.04, x86_64
- Kernel: 7.0.0-34-generic
- Nouveau / Mesa NVK
- The identical physical connection is detected correctly by the NVIDIA Windows driver.

## Symptom

Nouveau detects `DVI-I-1` as connected via analog load detection, but exposes no EDID and only fallback modes up to 1024x768. A manually added 1920x1080 reduced-blanking mode works correctly.

## VBIOS routing

`nvbios` decodes connector 0 as DVI-I. Both the TMDS and ANALOG DCB entries use I2C index 0, and I2C 0 is PNVIO loc 0.

Relevant decoded entries:

```
DCB 0: type 2 [TMDS]  I2C 0 ... CONN 0 [DVI_I]
DCB 1: type 0 [ANALOG] I2C 0 ... CONN 0 [DVI_I]
I2C 0: type 0x05 [PNVIO] loc 0
CONN 0: type 0x30 [DVI_I] tag 0 HPD_0
```

## Tracing

Nouveau trace confirms bus 0000 is constructed and initialized, but connector detection performs no DDC transaction on it. Analog `LOAD_DETECT` repeatedly reports a load.

Function tracing of one `echo detect > /sys/class/drm/card1-DVI-I-1/status` produced only:

```
nouveau_connector_detect
nouveau_connector_ddc_detect
nvif_outp_detect
nvif_outp_detect
```

There was no `drm_get_edid`, `nvif_outp_edid_get`, or `nvkm_i2c_bus_acquire`.

A kretprobe on `nvif_outp_detect()` returned 0 for both DVI-I output objects:

```
outp_detect_ret ... ret=0
outp_detect_ret ... ret=0
```

`enum nvif_outp_detect_status` maps 0 to NOT_PRESENT. In `nouveau_connector_ddc_detect()`, NOT_PRESENT immediately continues to the next encoder, skipping the I2C/DDC fallback.

## Suspected bug

In `drivers/gpu/drm/nouveau/nvkm/engine/disp/outp.c`, `nvkm_outp_detect()` says:

```
/* TODO: Look into returning NOT_PRESENT if !HPD on DVI/HDMI.
 *
 * It's uncertain whether this is accurate for all older chipsets,
 * so we're returning UNKNOWN, and the DRM will probe DDC instead.
 */
if (outp->info.type == DCB_OUTPUT_DP)
        return 0;
...
return ret;
```

When HPD is present but low, `ret` is 0, so the non-DP path actually returns NOT_PRESENT rather than UNKNOWN. The observed control flow matches this exactly.

## Maintained fix

For HPD-present-but-low non-DP outputs, return a negative value so
`nvkm_uoutp_mthd_detect()` maps it to `NVIF_OUTP_DETECT_V0_UNKNOWN` and DRM
performs the DDC probe. The maintained patch is
[`patches/hpd-low-ddc-probe.patch`](../patches/hpd-low-ddc-probe.patch):

```diff
 if (outp->info.type == DCB_OUTPUT_DP)
         return 0;
+
+return -EINVAL;
 }
```

The fix preserves DP behavior and follows the existing comment for
DVI/HDMI/analog outputs. It has been applied in Nouveau: connector detection
now proceeds to DDC probing.

## Remaining DDC failure

- EDID address `0x50` returns `ENXIO` on all eight Nouveau PNVIO buses.
- VBIOS routing maps the DVI-I analog/TMDS outputs to PNVIO bus 0. Bus 0 uses
  bitbang mode; Nouveau drives and senses SCL/SDA, but sees no address ACK.
- The same GPU, adapter, cable, and monitor work under Windows.
- A previous full ROM dump was 184,320 bytes and includes a GPIO-31 sequence.
  The sysfs ROM read exposed only a 59,392-byte first image. GPIO 31's function
  remains unknown, so it is not treated as proven DDC power control.
- The DDC +5 V rail has not been measured.
- After rebooting with the initial IBUF diagnostic, both samples were
  `IBUF_ENABLE_0=ffffffff I2C[3:0]=f`. This is unvalidated readback, not proof
  that any input buffer is enabled; no IBUF-setting patch is justified.

The opt-in diagnostic in
[`patches/diagnostic/ibuf-state-snapshot.patch`](../patches/diagnostic/ibuf-state-snapshot.patch)
now logs `PMC_BOOT_0`, the DDC bit-bang register before and after its existing
initialization write, PNVIO register `0xe500`, devinit status, and IBUF register
`0xe1b8`. The VBIOS interpreter and this diagnostic use the normal
`nvkm_rd32()` path for MMIO reads; static analysis found no GK104-specific
accessor for `0xe1b8`. The existing `0xd014 = 0x7` write still occurs exactly
once. The two samples occur before VBIOS POST and during later normal I2C
initialization, after the intervening device fini pass. The additional reads
validate whether the IBUF value can be interpreted on this GPU.

### DAC-powered DDC diagnostic

Static tracing shows that `nvkm_uoutp_mthd_load_detect()` privately acquires the
analog output before calling `nv50_dac_sense()`. At that point `dac->asy.outp`
identifies the acquired output and `outp->i2c` is the DCB/VBIOS-selected DDC
bus. GK104 uses the GF119 DAC implementation, whose `.sense` callback is
`nv50_dac_sense()` and whose `.power` callback is `nv50_dac_power()`.

The opt-in `patches/diagnostic/dac-powered-ddc-probe.patch` therefore performs
two EDID byte-0 read transactions on that existing bus in the load-detect path:
one after the DAC enters its non-normal power-control state, before load-sense starts,
and one after the existing load-sense delay while load-sense is still active. It does
not write IBUF, GPIO, or PNVIO routing state. A successful first `ret=2` implicates the
non-normal DAC state; settled-only success implicates load-sense or settling. Failure
in both phases substantially weakens the whole DAC/load-detect-state hypothesis.

### ACK-slot sampler

The opt-in `patches/diagnostic/ack-slot-sampler.patch` instruments Nouveau's
internal bit-bang algorithm at the address ACK slot for I2C address `0x50`.
`--diag-ack-slot` enables the DAC-powered probe, compiles the existing internal
bit-bang implementation (disabled by this Ubuntu header config), and stages
`config=NvI2C=1` for the diagnostic boot. On physical PNVIO register
`0xd014`, it logs three raw reads; the first read remains the transaction's
ACK/NACK decision. SCL input is bit 4 and SDA input is bit 5. SCL high with
SDA low means an ACK reached the GPU input. SDA high means no ACK was observed
there, but this connected-only experiment cannot distinguish the monitor from
an intervening board-level signal path. The sampler adds no PNVIO writes. The
verifier reports the active module option when readable and prints the samples.
