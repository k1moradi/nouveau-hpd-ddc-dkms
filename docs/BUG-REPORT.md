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

The next software diagnostic is the opt-in
[`patches/diagnostic/ibuf-state-snapshot.patch`](../patches/diagnostic/ibuf-state-snapshot.patch).
It reads `IBUF_ENABLE_0` at physical PNVIO port 0 initialization and logs the
full value plus bits 16-19. It never writes that register. The two boot samples
occur before VBIOS POST and later during normal I2C initialization, after the
intervening device fini pass. This tests the input-buffer hypothesis without
changing GPIO, I2C, or output state.
