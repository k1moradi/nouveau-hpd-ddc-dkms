# Kepler VP5 video-decode backport candidate

## Scope

This change is intentionally limited to the first observed hardware-decode
failure: the legacy Kepler video channel is killed before Mesa's BSP/MSVLD
pushbuf reaches the ioctl successfully. It does not suppress channel errors,
ignore MMU faults, alter firmware, or special-case the Quadro K4200 PCI ID.

## Root-cause evidence

The legacy Nouveau GEM pushbuf ioctl returns `-ENODEV` when the channel's
`killed` flag is already set. The kill notification is emitted after NVKM marks
the FIFO channel errored. Therefore the printed Mesa BSP pushbuf is downstream
of the initiating kernel fault rather than proof that its `0x700`, `0x400`, or
`0x300` methods are invalid.

Linux v7.0's `gk104_ectx_ctor()` maps every engine context with
`gf100_vmm_map_v0.priv = 1`. A Nouveau patch posted upstream on 2026-09-13
identifies this as a regression for Kepler's legacy MSVLD, MSPDEC, and MSPPP
video engines: those engines issue non-privileged MMU requests, so a privileged
engine-context mapping causes a FIFO `PRIV_VIOLATION` fault and kills the
channel. The posted fix keeps privileged mappings for other engines and clears
`args.priv` only for those three legacy video engines
([mailing-list patch](https://lists.openwall.net/linux-kernel/2026/09/13/861)).

The target NVE4 path uses the same GK104 FIFO engine-context code. MSVLD,
MSPDEC, and MSPPP are the engines behind Falcon bases `0x084000`, `0x085000`,
and `0x086000`, respectively. Mesa's Kepler video path creates separate BSP,
VP, and PPP channels/objects and the observed BSP method sequence is the normal
H.264 submission sequence.

This makes the mapping fix a strong root-cause backport candidate, but the
K4200-specific first fault still needs to be captured after reboot to close the
proof loop. Before calling the issue fixed, confirm that the pre-patch run shows
an MSVLD/MSPDEC/MSPPP fault (especially reason `0x05 [PRIV_VIOLATION]`) and that
the same workload completes without that fault after the patch.

## Why only patch 1 first

The same upstream series contains a second, independent legacy-FIFO nonstall
event-index fix. Do not fold it into the first K4200 experiment. Applying one
behavioral change at a time makes the result attributable. If this context-map
fix removes the channel kill but decode then stalls waiting for fence progress,
apply/test the nonstall-event fix as a second experiment.

## Patch-1 follow-up evidence

The first K4200 run with patch 1 removed the original channel-kill behavior,
but later produced a SIGBUS in `nvc0_decoder_bsp_next()` while copying a 1 MiB
BSP BO. The core shows `dec->bsp_ptr == NULL`; the input chunks were only 3
and 9 bytes, so their payload size does not explain the huge rounded BO size.

The current evidence points to a possible fence wait timeout before this copy:
Mesa maps the BSP BO in `nvc0_decoder_bsp_begin()`, Nouveau's BO-map path
waits through GEM CPU_PREP, and that wait is bounded at 30 seconds. The core
does not contain the CPU_PREP return value, so the wait timeout remains an
inference rather than a directly captured result.

The isolated next candidate is the legacy FIFO nonstall event-index change.
GK104's FIFO interrupt notifies global event index 0, while the shared
`nvkm_uchan_uevent()` path currently registers nonstall events at
`runl->id`. The upstream candidate uses the runlist ID only when a FIFO has
a per-runlist nonstall constructor; legacy FIFOs use index 0. This is a
specific match for the suspected wait, but K4200 causality still requires an
A/B run.

Version 0.1.13 carries this as an opt-in build flag so the patch-1-only
baseline remains available:

```bash
pkexec env NOUVEAU_DKMS_JOBS=2 ./install.sh --experimental-legacy-nonstall
```

See [the isolated experiment notes](LEGACY-FIFO-NONSTALL-EXPERIMENT.md) for
verification, rollback, and result interpretation. Do not count patch 2 as
validated until the same VA-API reproduction completes without a fault.

## Build behavior

`dkms/dkms-build.sh` checks the extracted Ubuntu source before applying the
backport. `dkms/check-gk104-video-context.py` recognizes the vulnerable
privileged-map preimage and the upstream three-engine switch form. It applies
the patch only to the recognized vulnerable form, skips the recognized fixed
form, and aborts on any other layout. Patch application is strict with
`patch --forward --batch`; a changed source layout is not guessed around.

## A/B validation

Keep the extracted VP5 firmware constant throughout the comparison.

1. Reproduce once with the clean current DKMS build and save the kernel log.
2. Reproduce once with the stock Ubuntu Nouveau module for the same kernel and
   firmware.
3. Install the candidate DKMS build from this patch, reboot, and verify that the
   DKMS module is loaded.
4. Run the deterministic H.264 VA-API test.
5. Repeat the hardware-decode test 20 times after the first successful run.
6. Re-run `./verify-after-reboot.sh` to confirm the existing HPD/DDC behavior.

Capture at least 30 kernel-log lines before the first channel-kill/fault line.
For the pre-patch failure, record engine, client, access, reason, fault address,
channel ID, and channel instance.

### Primary VA-API test

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

Pass means all 900 frames complete through VA-API with no `kernel rejected
pushbuf`, video-engine MMU fault, channel kill, engine trap, or segmentation
fault. Do not accept software fallback.

### Repeated test

```bash
for i in $(seq 1 20); do
    echo "=== run $i ==="
    ffmpeg \
        -hide_banner \
        -loglevel error \
        -hwaccel vaapi \
        -hwaccel_device /dev/dri/renderD128 \
        -hwaccel_output_format vaapi \
        -i /tmp/h264-test.mp4 \
        -f null - || break
done
```

## Regression surface

The code change is in GK104-family engine-context mapping setup. It changes the
mapping privilege bit only when the engine type is MSVLD, MSPDEC, or MSPPP.
It does not touch display detection, HPD/DDC, firmware loading, normal graphics
channels, unrelated engine types, or the userspace `killed` handling path.
