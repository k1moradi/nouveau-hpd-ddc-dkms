# BAR2 instance-memory map diagnostic

The serialized Mesa teardown test removed the VP idle-fence timeout and
MSPPP/CTXSW recovery from the tested 3000-frame run, but two BAR2/HOST_CPU read
PTE faults remained. The faults were reported at BAR2 offsets `0x377000` and
`0x388000`; their caller and mapping lifetime are still unknown. This diagnostic
adds read-only tracing to the NV50 instance-memory BAR2 mapping and access
paths. It does not alter mapping, MMIO, fence, scheduler, or recovery behavior.
The address filter only selects 32-bit accesses and mapping lifetimes which
overlap pages `0x377000` or `0x388000`. In `diag2`, mapping-lifecycle and
access records share one global kernel ratelimiter capped at eight combined
records per second. This bounds logging, but access bursts can consume the
budget before a relevant mapping-lifecycle record is emitted.

The patch is in
[`patches/diagnostic/gk104-bar2-instmem-map-trace.patch`](../patches/diagnostic/gk104-bar2-instmem-map-trace.patch).
Its `diag_bar2_map` parameter defaults to false. Mapping records must be
enabled from Nouveau's initial module load to observe mappings created during
GPU initialization, so the diagnostic package is separate from the normal
`0.1.13` and prior `0.1.13-diag1` packages.

## Verified module and package state

The latest verified hardware captures used kernel `7.0.0-34-generic` and the
loaded/on-disk `nouveau-hpd-ddc/0.1.13-diag2` module at srcversion
`72DEE2B4ECFF77AD3764039`. `diag_bar2_map`, `diag_fence_wait`, and `diag_ctxsw`
were enabled for those runs. The prepared `diag3` build described below will
be a separate DKMS version and will not replace `diag2` unless it passes a
separate review and install gate.

For the initial `diag2` boot, the dracut image was verified with `lsinitrd` to
contain both the module and the one-boot setting
`options nouveau diag_bar2_map=1`. The host-side option was removed after the
boot verifier passed so later generated initramfs images would not enable the
probe automatically.

The preserved module artifacts are checked by their srcversions:

- Production baseline `0.1.13`: `57AE1B168D50DB546CD87A1`.
- Existing diagnostic `0.1.13-diag1`: `B19B8AAE48467545E652509`.
- Expected BAR2 diagnostic `0.1.13-diag2`: `72DEE2B4ECFF77AD3764039`.

Do not reuse a DKMS version for different module contents. Do not register or
install `diag2` if its resulting srcversion differs from the expected value.

## Historical diag2 build record

This preparation completed before the hardware captures. Do not rerun it to
create different contents under the already-installed `0.1.13-diag2` version.

The helper required a clean checkout of the review branch. It recorded the
commit and tree IDs, hashes every file under `dkms/`, the preparation script,
and each applied patch, and verifies the Ubuntu source package version and
archive SHA-256 (`linux-source-7.0.0` `7.0.0-34.34`,
`a874e1fb08d2ee695b08e0c8ce6fd2c76a4bf7ffa98882fbabd233380ef8a85a`). The
same provenance was copied into the staged DKMS source. The installer required
a clean checkout with matching commit/tree and recomputed the input manifest
before installing the artifact.

At build time, the helper required loaded and on-disk `diag1`, preserved `.13`
and `diag1` artifacts with their expected srcversions, the exact kernel
headers/source archive, and an absent `diag2` source/build entry. Its recorded
build checks were:

- DKMS reports `0.1.13-diag2` as **built**, not installed.
- The module vermagic starts with `7.0.0-34-generic`.
- The module srcversion is exactly `72DEE2B4ECFF77AD3764039`.
- `modinfo -p` lists `diag_fence_wait`, `diag_ctxsw`, and `diag_bar2_map`.
- The module contains the IDLE_FENCE, CTXSW, BAR2_MAP, and BAR2_ACCESS trace
  markers.
- The evidence directory records the compressed module and complete DKMS
  build log with SHA-256 hashes.

The regular installer remains on version `0.1.13`; the diag2 preparation was
the only path that staged the base BAR2 patch.

## Historical diag2 installation and early tracing boot

This installation and reboot already occurred. The commands below document
how that one-time early-parameter boot was prepared; do not rerun the diag2
installer or reuse this version for the rate-limit follow-up.

The install helper used the unique `0.1.13-diag2` version, verified the
preserved module artifacts, kept the loaded `diag1` module running, and updated
the initramfs without rebooting or reloading Nouveau.

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

Three captures completed in that boot:

| Capture | Result |
| --- | --- |
| `/home/keivan/nouveau-vaapi-captures/20260930T082658Z-private-instrumented-12497` | 3,000 output frames, 3,002 decoded, zero decode errors, FFmpeg exit 0, `STOP_A_B=0`; no BAR2/PTE or other stop signature. |
| `/home/keivan/nouveau-vaapi-captures/20260930T083455Z-private-instrumented-13895` | Full 40,561-frame file, 40,561 decoded, zero decode errors, FFmpeg exit 0; wall time 534.72 seconds; `STOP_A_B=0`; no BAR2/PTE or other stop signature. |
| `/home/keivan/nouveau-vaapi-captures/20260930T085548Z-private-instrumented-16322` | Repeated full 40,561-frame file, 40,561 decoded, zero decode errors, FFmpeg exit 0; wall time 535.15 seconds; `STOP_A_B=0`; no BAR2/PTE or other stop signature. |

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

The repeated full-file capture's SHA-256 values also verified:

```text
boot-kernel.log  a781117dd3758e4f92bc99a6922f34fe94f68d1467885099f6bf93533c8dc0ad
kernel.log       3b4bf799fd3a27aa747e8c961a14164b9bfe385ba0332ad3477ddc5db45166fc
ffmpeg.log       c8779225f5b6a4fe071deaa6fbb2d3564e440c652da4655d4bcd668a0928cbb3
transcript.txt   602f318469a3da5a5c1fcd313e5f0ae75f6c782788ae6c7aba18736c70cd9d93
```

The second full-file run again recorded a live mapping at `0x377000`, accesses
to offsets `0x200`, `0x204`, and `0x208`, a before-write record at `0x20c`,
and destruction of the mapping near FFmpeg exit. It again logged destruction
of a mapping spanning `0x388000` without a corresponding map-creation record.
Source review found that the shared rate-limit state allows an access record
to suppress a later map record. The missing creation record therefore cannot
be interpreted as proof that the mapping was not created or that no callback
ran.

Across these three captures, 84,122 output frames were produced and 84,124
frames were decoded with zero decode errors. The prior BAR2 faults did not
reproduce in these `diag2` runs. This does not prove the BAR2 issue is fixed:
earlier captures recorded faults at `0x377000` and `0x388000`, and these clean
runs cannot establish whether those faults are intermittent or dependent on
another client/state. Keep the BAR2 root cause unresolved and do not infer that
the Mesa clear or serialized-teardown changes eliminated it.

## Follow-up diagnostic candidate: independent log budgets

The repeated full-file runs exposed a coverage limitation in `diag2`: a shared
eight-record-per-second limiter applies to both address-filtered map lifecycle
records and fast access records. The source proves these record classes share
that budget. The evidence does not prove which particular callback was
suppressed, but it permits a burst of accesses to hide a later map creation.

The standalone follow-up patch
[`gk104-bar2-map-rate-limit-isolation.patch`](../patches/diagnostic/gk104-bar2-map-rate-limit-isolation.patch)
adds an independent `HZ, 8` limiter for map, eviction, and destroy records;
the existing access records retain their own `HZ, 8` limiter. This keeps each
record class bounded and caps their combined output at sixteen records per
second. The diagnostic is opt-in and does not change mapping or access
behavior. It is applied only after the base BAR2 patch through a separate
marker, so the existing `diag2` source and installed artifact remain
unchanged.

The map record also labels itself `budget=lifecycle` so the new code has a
distinct module-string marker and captures identify which budget emitted it.
The strict patch applies with `--fuzz=0` to the cached exact Ubuntu
`7.0.0-34.34` `nv50.c` preimage, and a regression check verifies the map and
access callbacks use their distinct limiter states.

The separate `tools/prepare-bar2-map-diagnostic-v3.sh` helper stages
`nouveau-hpd-ddc/0.1.13-diag3` from a clean committed tree and builds it without
installing it. The clean discovery build from review commit
`42d0f6b60485587050795e2b4cd4c7a247d10752` produced srcversion
`9F90A7EB5A9E1505E0B6708`; that value is now pinned in the helper, so a
subsequent mismatch fails the build gate. The pinned clean rebuild from
`94cc54875b1fff7a863dd483a0482c2572446046` produced the same srcversion and
module bytes apart from the GNU build-id descriptor. Its compressed module
SHA-256 is `d437863bd12473c8dbba7104cf8bccdc7a2b67fafe10a9e0d5f963a168245aca`.
The `diag2` and normal preparation paths do not create the follow-up marker.
BAR2's historical root cause remains unknown; `diag3` exists to collect a more
complete mapping lifetime if the fault recurs.

The version-specific install, verify, and rollback helpers are
`tools/install-bar2-map-diagnostic-v3.sh`,
`tools/verify-bar2-map-diagnostic-v3.sh`, and
`tools/rollback-bar2-map-diagnostic-v3.sh`. Installation puts the unique
diagnostic version on disk while leaving the running `diag2` loaded. It stages
`diag_bar2_map=1` in this machine's dracut image for one boot, verifies that
the image contains both the pinned module and option, then removes the host
modprobe file. It does not reboot or reload Nouveau. The verifier's
`clear-early-option` mode regenerates the initramfs after the diagnostic boot
so later boots do not automatically enable BAR2 logging; the live module
parameter stays enabled until reboot.

After a successful install verification and a fresh boot, use this sequence:

```bash
sudo tools/verify-bar2-map-diagnostic-v3.sh post-reboot
sudo tools/verify-bar2-map-diagnostic-v3.sh clear-early-option
sudo sh -c '
printf 1 > /sys/module/nouveau/parameters/diag_fence_wait
printf 1 > /sys/module/nouveau/parameters/diag_ctxsw
printf "diag_bar2_map="; cat /sys/module/nouveau/parameters/diag_bar2_map
printf "diag_fence_wait="; cat /sys/module/nouveau/parameters/diag_fence_wait
printf "diag_ctxsw="; cat /sys/module/nouveau/parameters/diag_ctxsw
'

DRI="$HOME/.cache/nouveau-vaapi-followup-20260928/private-instrumented-native-surface-fix-serialized-teardown-candidate/prefix/lib/x86_64-linux-gnu/dri"
printf '%s  %s\n' \
  'd841244e592f171adebdbeaa556c97cb2e1bf639fceba81b0a829c9720b1119f' \
  "$DRI/libgallium_drv_video.so" | sha256sum --check -
NOUVEAU_CAPTURE_FRAMES=40561 NOUVEAU_CAPTURE_BOOT_KERNEL_LOG=1 \
  tools/vaapi-capture.sh private-instrumented "$DRI"
```

The capture is a full-file decode with the already validated native-surface
and serialized-teardown Mesa build. It is intended to establish whether the
address-filtered BAR2 map/access records identify the mappings covering
`0x377000` or `0x388000` if either PTE fault recurs. A clean run is
non-reproduction evidence; it does not by itself identify the historical
fault's cause. Respect `STOP_A_B=1` and stop further GPU work in that boot.

After the candidate changes are committed and pushed, the preparation command
is:

```bash
cd ~/nouveau-hpd-ddc-dkms
sudo env NOUVEAU_DKMS_JOBS=1 tools/prepare-bar2-map-diagnostic-v3.sh
```

It requires `diag2` loaded and on disk, a clean review-branch checkout, and the
exact Ubuntu kernel source archive. It registers/builds only the new
`0.1.13-diag3` DKMS source; it does not install the module, regenerate the
initramfs, reload Nouveau, or reboot. Review its build log, output module hash,
vermagic, srcversion, parameters, and lifecycle-budget marker before preparing
any install step.
