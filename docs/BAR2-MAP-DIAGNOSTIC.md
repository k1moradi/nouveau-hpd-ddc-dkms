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

The post-reboot check confirmed that kernel `7.0.0-34-generic` is running
`nouveau-hpd-ddc/0.1.13-diag1`, with loaded and on-disk srcversion
`B19B8AAE48467545E652509`. Its `diag_fence_wait` and `diag_ctxsw` parameters
were both disabled after reboot. The BAR2-specific `0.1.13-diag2` package is
prepared separately and must pass its DKMS build and artifact checks before it
is installed.

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

On the next boot, pass this one-time kernel command-line argument so mapping
creation is traced from module initialization:

```text
nouveau.diag_bar2_map=1
```

At the GRUB menu, edit the normal Ubuntu entry, append the argument to the
`linux` line, and boot that entry. Do not add it permanently to `/etc/default/grub`.
After reconnecting, verify before enabling the other probes:

```bash
sudo tools/verify-bar2-map-diagnostic.sh post-reboot
```

The verifier requires the loaded `diag2` srcversion, the kernel argument, and
`diag_bar2_map=Y`; it also requires `diag_fence_wait` and `diag_ctxsw` to remain
off until they are explicitly enabled. For the one controlled capture, enable
those two parameters, verify their readback, then use the already validated
serialized-teardown native-surface Mesa candidate and existing capture harness.
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
