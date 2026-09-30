# BAR2 instance-memory map diagnostic

The serialized Mesa teardown test removed the VP idle-fence timeout and
MSPPP/CTXSW recovery from the tested 3000-frame run, but two BAR2/HOST_CPU read
PTE faults remained. The faults were reported at BAR2 offsets `0x377000` and
`0x388000`; their caller and mapping lifetime are still unknown. This diagnostic
adds read-only tracing to the NV50 instance-memory BAR2 mapping and access
paths. It does not alter mapping, MMIO, fence, scheduler, or recovery behavior.
The address filter only selects 32-bit accesses and mapping lifetimes which
overlap pages `0x377000` or `0x388000`. The records share one global kernel
ratelimiter, capped at eight combined mapping/access records per second, not
eight per record type, so unrelated BAR2 traffic cannot flood the log.

The patch is in
[`patches/diagnostic/gk104-bar2-instmem-map-trace.patch`](../patches/diagnostic/gk104-bar2-instmem-map-trace.patch).
Its `diag_bar2_map` parameter defaults to false. Mapping records must be
enabled from Nouveau's initial module load to observe mappings created during
GPU initialization, so the diagnostic package is separate from the normal
`0.1.13` and prior `0.1.13-diag1` packages.

## Current module and package state

The latest live check on 2026-09-30 reports kernel `7.0.0-34-generic`, with
`nouveau-hpd-ddc/0.1.13-diag1` still loaded at srcversion
`B19B8AAE48467545E652509`. The separate `nouveau-hpd-ddc/0.1.13-diag2` package
is installed on disk at srcversion `72DEE2B4ECFF77AD3764039`, but has not yet
been loaded because the machine has not rebooted since installation. The
currently loaded module therefore has no `diag_bar2_map`, `diag_fence_wait`,
or `diag_ctxsw` parameter files.

The current dracut image `/boot/initrd.img-7.0.0-34-generic` contains the
`diag2` module and the exact one-boot option
`options nouveau diag_bar2_map=1`; both were verified with `lsinitrd`. The
host-side option file was then removed. After reboot, the post-reboot verifier
must pass before running a capture.

The preserved module artifacts are checked by their srcversions:

- Production baseline `0.1.13`: `57AE1B168D50DB546CD87A1`.
- Existing diagnostic `0.1.13-diag1`: `B19B8AAE48467545E652509`.
- Expected BAR2 diagnostic `0.1.13-diag2`: `72DEE2B4ECFF77AD3764039`.

Do not reuse a DKMS version for different module contents. Do not register or
install `diag2` if its resulting srcversion differs from the expected value.

## Build and inspect without installing

The preparation helper copies the current functional and diagnostic patches
into the unique `0.1.13-diag2` source tree, creates explicit markers for the
legacy nonstall, VP fence/CTXSW, and BAR2 diagnostics, and runs a one-job DKMS
build. It does not run `dkms install`, replace the on-disk module, update the
initramfs, or reload Nouveau.

```bash
cd ~/nouveau-hpd-ddc-dkms
sudo env NOUVEAU_DKMS_JOBS=1 tools/prepare-bar2-map-diagnostic.sh
```

The helper requires a clean checkout of the review branch. It records the
commit and tree IDs, hashes every file under `dkms/`, the preparation script,
and each applied patch, and verifies the Ubuntu source package version and
archive SHA-256 (`linux-source-7.0.0` `7.0.0-34.34`,
`a874e1fb08d2ee695b08e0c8ce6fd2c76a4bf7ffa98882fbabd233380ef8a85a`). The
same provenance is copied into the staged DKMS source. The installer requires
a clean checkout with matching commit/tree and recomputes the input manifest
before it can install the artifact.

The helper fails unless the running and on-disk module is still `diag1`, the
preserved `.13` and `diag1` module artifacts have their expected srcversions,
the exact kernel headers and Ubuntu source archive are available, and no
`diag2` source/build entry already exists. After a successful build, inspect
the printed evidence directory and confirm:

- DKMS reports `0.1.13-diag2` as **built**, not installed.
- The module vermagic starts with `7.0.0-34-generic`.
- The module srcversion is exactly `72DEE2B4ECFF77AD3764039`.
- `modinfo -p` lists `diag_fence_wait`, `diag_ctxsw`, and `diag_bar2_map`.
- The module contains the IDLE_FENCE, CTXSW, BAR2_MAP, and BAR2_ACCESS trace
  markers.
- The evidence directory records the compressed module and complete DKMS
  build log with SHA-256 hashes.

The regular installer remains on version `0.1.13`; this diagnostic helper is
the only path that stages the extra BAR2 patch.

## Install and boot once with early mapping tracing

After reviewing a successful build artifact, install it on disk with:

```bash
sudo tools/install-bar2-map-diagnostic.sh
sudo tools/verify-bar2-map-diagnostic.sh pre-reboot
```

The install helper uses the unique `0.1.13-diag2` version, verifies both
preserved module artifacts, keeps the currently loaded `diag1` module running,
and updates the initramfs. It does not reboot or reload Nouveau.

This Lubuntu installation uses a dracut-generated initramfs. To enable the
parameter for one boot without editing a bootloader command line, temporarily
put a modprobe option in the initramfs. The host-side config is removed after
the image is built, so it will not affect later module loads:

```bash
K=$(uname -r)
CFG=/etc/modprobe.d/99-nouveau-diag-bar2-once.conf
printf 'options nouveau diag_bar2_map=1\n' | sudo tee "$CFG"
sudo dracut --force --include "$CFG" "$CFG" \
    "/boot/initrd.img-$K" "$K"
sudo lsinitrd -f "$CFG" "/boot/initrd.img-$K"
sudo unlink "$CFG"
```

On this Lubuntu dracut setup, the modprobe file was not included by a plain
`dracut --force`; the explicit `--include` is required. Confirm `lsinitrd`
prints exactly `options nouveau diag_bar2_map=1`, then reboot. After
reconnecting, verify before enabling the other probes:

```bash
sudo tools/verify-bar2-map-diagnostic.sh post-reboot
```

The verifier requires the loaded `diag2` srcversion, `diag_bar2_map=Y`, and
either the one-time kernel argument or the exact option in the dracut image. It
also requires `diag_fence_wait` and `diag_ctxsw` to remain off until explicitly
enabled. After the verifier passes, regenerate the initramfs without the
temporary option; this leaves the already-loaded `diag_bar2_map=Y` unchanged
for the current boot and prevents it from carrying into the next boot:

```bash
sudo dracut --force "/boot/initrd.img-$(uname -r)" "$(uname -r)"
cat /sys/module/nouveau/parameters/diag_bar2_map
```

Then enable the other two parameters, verify their readback, and use the
already validated serialized-teardown native-surface Mesa candidate and
existing capture harness.
Verify that the exact candidate plugin is selected before the capture:

```bash
DRI="$HOME/.cache/nouveau-vaapi-followup-20260928/private-instrumented-native-surface-fix-serialized-teardown-candidate/prefix/lib/x86_64-linux-gnu/dri"
printf '%s  %s\n' \
  'd841244e592f171adebdbeaa556c97cb2e1bf639fceba81b0a829c9720b1119f' \
  "$DRI/libgallium_drv_video.so" | sha256sum --check -
readlink -f "$DRI/nouveau_drv_video.so"
```

The expected SHA-256 is for the `libgallium_drv_video.so` containing both the
native-surface clear correction and serialized VP3 teardown order. Do not use
the earlier native-surface-only candidate for this BAR2 comparison. Respect
`STOP_A_B=1`: do not run another GPU condition in the same boot after a
critical Nouveau failure.

The mapping hooks may have logged object creation during module/GPU
initialization, before the live capture's `journalctl -n 0` follower begins.
Preserve those earlier records in the same capture directory by opting in to a
current-boot journal snapshot:

```bash
sudo sh -c '
printf 1 > /sys/module/nouveau/parameters/diag_fence_wait
printf 1 > /sys/module/nouveau/parameters/diag_ctxsw
printf "diag_fence_wait="; cat /sys/module/nouveau/parameters/diag_fence_wait
printf "diag_ctxsw="; cat /sys/module/nouveau/parameters/diag_ctxsw
'

DRI="$HOME/.cache/nouveau-vaapi-followup-20260928/private-instrumented-native-surface-fix-serialized-teardown-candidate/prefix/lib/x86_64-linux-gnu/dri"
NOUVEAU_CAPTURE_BOOT_KERNEL_LOG=1 \
    tools/vaapi-capture.sh private-instrumented "$DRI"
```

The harness writes the snapshot to `boot-kernel.log` before it starts the live
journal follower and includes that file in `SHA256SUMS`. The boot snapshot is
for chronology and map-lifetime analysis; the live `kernel.log` remains the
source for determining whether the decode run emitted a stop signature.

## Rollback

To restore the previously tested diagnostic module on disk without reloading
Nouveau:

```bash
sudo tools/rollback-bar2-map-diagnostic.sh
```

This reinstalls `0.1.13-diag1` with its expected srcversion and updates the
initramfs. Reboot without the one-time BAR2 parameter to return to the prior
diagnostic baseline. `uninstall.sh` removes both diagnostic DKMS revisions
along with the regular project versions.

## Hardware result: diag2 captures on 2026-09-30

The dracut boot passed `tools/verify-bar2-map-diagnostic.sh post-reboot` with
the expected loaded and on-disk srcversion `72DEE2B4ECFF77AD3764039`.
`diag_bar2_map` was enabled by the one-boot dracut option; the fence and CTXSW
probes were then enabled for the captures. The tested Mesa plugin was the
private native-surface plus serialized-teardown build pinned above. No Mesa
package, DKMS module, or kernel image was installed or replaced during either
capture.

Two captures completed in that boot:

| Capture | Result |
| --- | --- |
| `/home/keivan/nouveau-vaapi-captures/20260930T082658Z-private-instrumented-12497` | 3,000 output frames, 3,002 decoded, zero decode errors, FFmpeg exit 0, `STOP_A_B=0`; no BAR2/PTE or other stop signature. |
| `/home/keivan/nouveau-vaapi-captures/20260930T083455Z-private-instrumented-13895` | Full 40,561-frame file, 40,561 decoded, zero decode errors, FFmpeg exit 0; wall time 534.72 seconds; `STOP_A_B=0`; no BAR2/PTE or other stop signature. |

For the full-file capture, SHA-256 verification passed for all recorded files:

```text
boot-kernel.log  3ec23b3b3998acaf52d314de25e4ae2db2f2b681bec0b96a091162077d717001
kernel.log       b26dbe4843f6d50f4603b173cceda595246623e3bbea7365c5794ee01791f5ea
ffmpeg.log       f5acd6cba5b71e5fc9048f009106a7f3f7ad2a4dffa7f4fef8b671c44a9628cc
transcript.txt   fa5124695f67b14df67947806e3df63ec09170f79d4c994108ac2a473286b619
```

The full-file run positively exercised the private VA driver and surface-clear
trace. The clear records used the expected `1920x544` luma and `960x272`
chroma render-target dimensions. BSP, VP, and PPP idle fences completed; the
VP chid 4 fence advanced from `0xaa2e` to its target `0xaa31` and returned
success after 21 jiffies. The capture had no PROP overrun, `CTXSW_TIMEOUT`,
channel kill, failed-idle, or BAR2/PTE record. SDDM remained inactive at both
capture boundaries.

The BAR2 trace saw a live one-page mapping at VMA `0x377000` (backing
`0xff82f000`) during the run, including successful writes at offsets
`0x200`, `0x204`, and `0x208`, plus a before-write record for `0x20c`. The
logger reported suppressed callbacks, so the trace is not a complete access
history. In the full-file capture that mapping was destroyed at monotonic
`1922.743289`, about 0.036 seconds before FFmpeg exited. At teardown, another
mapping covering VMA range `[0x379000, 0x3a3000)` was destroyed. This range
includes the previously faulting offset `0x388000`, but neither the boot
snapshot nor the live diagnostic log has a matching map-creation record for
that object; the logger also reports suppressed map callbacks.
Therefore the trace does not establish which mapping, if any, owned either
historical fault address when the earlier faults occurred.

These clean 3,000-frame and full-file samples show that the prior BAR2 faults
did not reproduce in these `diag2` runs. They do not prove the BAR2 issue is
fixed: earlier captures recorded faults at `0x377000` and `0x388000`, and a
single clean boot cannot establish whether those faults are intermittent or
dependent on another client/state. Keep the BAR2 root cause unresolved and do
not infer that the Mesa clear or serialized-teardown changes eliminated it.
