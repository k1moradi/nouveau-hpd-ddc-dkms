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

## Proposed fix

For HPD-present-but-low non-DP outputs, return a negative value so `nvkm_uoutp_mthd_detect()` maps it to `NVIF_OUTP_DETECT_V0_UNKNOWN` and DRM performs the DDC probe:

```diff
 if (outp->info.type == DCB_OUTPUT_DP)
         return 0;
+
+return -EINVAL;
 }
```

This preserves DP behavior while implementing the behavior described by the existing comment for DVI/HDMI/analog outputs.

## Additional observations

- `/sys/class/drm/card1-DVI-I-1/edid` is 0 bytes.
- With the default I2C implementation a direct userspace transaction to bus 0 / address 0x50 returns ENXIO.
- `nouveau.config=NvI2C=1` was also tested; EDID remained 0 bytes.
- The proposed control-flow fix should therefore be tested first; a second lower-level DDC issue may still be revealed after DDC is no longer skipped.
