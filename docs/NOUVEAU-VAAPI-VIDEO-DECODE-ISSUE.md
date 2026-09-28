# Nouveau VA-API hardware decode failure on GK104

## Status

This is a separate Nouveau issue from the K4200 DVI-I/DDC investigation. It
records a reproducible NVE4 hardware video-decode failure for follow-up; it
does not contain a proposed fix. The root cause is not yet established.

## Environment from the captured run

- GPU: Quadro K4200, GK104 / NVE4
- FFmpeg: `8.0.1-3ubuntu2`
- VA-API: libva `1.23`
- VA-API driver: Mesa Gallium `26.0.8-1ubuntu0.3`, reporting `for NVE4`
- Render device: `/dev/dri/renderD128`
- Kernel release: not included in the pasted output; collect it with `uname -r`
- Firmware: the accompanying analysis says the missing-firmware issue had
  already been addressed, but the exact filenames and versions are not in the
  capture

The FFmpeg build lists `vaapi` among its compiled hardware-acceleration
methods. That list alone does not prove a usable GPU path; the decode log
below additionally shows the Nouveau VA-API driver opening and identifying
the NVE4 device.

## Reproduction

The test input was generated locally as a 30-second, 1920x1080, 30-fps,
H.264 High-profile, 8-bit 4:2:0 stream using software `libx264`:

```bash
ffmpeg \
    -f lavfi \
    -i 'testsrc2=size=1920x1080:rate=30' \
    -t 30 \
    -c:v libx264 \
    -preset veryfast \
    -pix_fmt yuv420p \
    /tmp/h264-test.mp4
```

Software decoding of the generated file completed:

```bash
time ffmpeg -hide_banner -loglevel error \
    -hwaccel none -i /tmp/h264-test.mp4 -f null -
```

Reported elapsed time was `9.408s` (`8.634s` user, `0.374s` system).

The failing VA-API decode command was:

```bash
ffmpeg \
    -hide_banner \
    -loglevel verbose \
    -hwaccel vaapi \
    -hwaccel_device /dev/dri/renderD128 \
    -hwaccel_output_format vaapi \
    -i /tmp/h264-test.mp4 \
    -vf 'hwdownload,format=nv12' \
    -f null -
```

It was also run with `-loglevel error`; the failure reproduced.

## Captured failure

The VA-API driver opened successfully and reported Mesa Gallium for NVE4.
The decode then produced this sequence:

```text
nouveau: kernel rejected pushbuf: No such device
nouveau: ch9: krec 0 pushes 1 bufs 3 relocs 0
nouveau: ch9: buf 00000000 ... 0x26d2000 0x8000
nouveau: ch9: buf 00000001 ... 0x2860000 0x100000
nouveau: ch9: buf 00000002 ... 0x2d60000 0x400000
nouveau: ch9: psh 00000000 0000000058 000000009c
```

The accompanying method dump is:

```text
class 0x200541c0, subchannel 2:
  0x0700=0x00020013  0x0704=0x00028601
  0x0708=0x00028607  0x070c=0x00028605  0x0710=0x00000003
class 0x20084100, subchannel 2:
  0x0400=0x00028600  0x0404=0x0002d600  0x0408=0x00000200
  0x040c=0x0002d76a  0x0410=0x003e9600  0x0414=0x0002d602
  0x0418=0x00016800  0x041c=0x00000000
class 0x200140c0, subchannel 2:
  0x0300=0x00000000
```

The pasted analysis tentatively identifies this as the H.264 BSP/MSVLD
submission path; confirm the exact method sequence against the Mesa version
used by this run before treating that identification as established. The
logged method dump proves what userspace prepared, not that the GPU executed
it; the `-ENODEV` pushbuf rejection may have occurred before submission.

The log continues with:

```text
nouveau: kernel rejected pushbuf: Invalid argument
nouveau: ch10: krec 0 pushes 1 bufs 0 relocs 0
Segmentation fault (core dumped)
```

The VA-API attempt took `33.230s` elapsed (`0.174s` user, `28.905s` system).
The long system time is recorded as a symptom of the failing path, not as a
measurement of normal hardware decode performance.

## What is established and what remains a hypothesis

The attached Linux v7.0 source review found that
`nouveau_gem_ioctl_pushbuf()` returns `-ENODEV` when the selected channel's
`chan->killed` flag is already set at ioctl entry
([source](https://github.com/torvalds/linux/blob/v7.0/drivers/gpu/drm/nouveau/nouveau_gem.c#L697-L731)).
That makes a prior channel-kill event a plausible explanation for the first
`No such device` message. The pasted capture does not include the kernel
journal around the failure, so it does not establish when channel 9 was
killed, what killed it, or whether the displayed push buffer caused an earlier
fault.

The primary diagnostic split for the next run is:

- **FIFO / VM / PTE / BAR2 fault:** investigate the reported GPU virtual
  addresses and mappings for the buffers above. Similar BAR2/HOST_CPU PTE
  faults were reported separately on this machine, but this capture does not
  establish a causal link.
- **MSVLD / Falcon / firmware fault:** verify the kernel's engine-specific
  status and firmware-load messages immediately before the first channel
  kill. The current pasted output does not include those kernel records.
- **Userspace cleanup failure:** the FFmpeg segmentation fault follows the
  rejected submissions and is secondary in this reproduction. Preserve it as
  a possible separate Mesa/libdrm error-unwind issue after the kernel failure
  is understood.

The synthetic input is ordinary H.264 High, 8-bit 4:2:0. A prior High10
compatibility question does not explain this run.

## Next diagnostic capture

Capture the kernel journal while reproducing the exact command. In one
terminal:

```bash
sudo journalctl -kf -o short-monotonic | tee ~/nouveau-vaapi-kernel.log
```

In another terminal, run the VA-API command above once. Stop the journal
capture after FFmpeg exits, then extract the relevant records:

```bash
grep -Ein \
  'channel.*killed|fifo|fault|pte|bar2|msvld|mspdec|msppp|falcon|ucode|firmware|trap|error|intr' \
  ~/nouveau-vaapi-kernel.log
```

Keep roughly 20–30 lines before and after the first channel-kill or engine
fault record. Also include `uname -r` and the firmware-load lines so the
kernel source and firmware state can be tied to the reproduction. The existing
boot journal can be checked before repeating the test with:

```bash
sudo journalctl -k -b --no-pager | \
  grep -Ein 'channel.*killed|fifo.*fault|msvld|mspdec|msppp|falcon|ucode|BAR2|PTE'
```

Until that event is captured, do not treat the error as a confirmed Falcon
firmware defect, a confirmed GPU-VM/PTE defect, or a VA-API userspace bug.
This note preserves the failure and the next evidence needed to diagnose it;
no kernel or firmware changes are proposed here.
