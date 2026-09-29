# VP idle-fence diagnostic DKMS build

This is a temporary, opt-in diagnostic build for the GK104 video teardown
investigation. It adds the `diag_fence_wait` and `diag_ctxsw` Nouveau module
parameters and observational logging around the legacy channel-idle fence and
GF100/GK104 context-switch timeout recovery. With both parameters left at zero,
the diagnostic paths remain disabled. The patch does not change fence packets,
timeouts, scheduling, or recovery.

The normal installer remains on DKMS version `0.1.13`. The diagnostic installer
uses the separate version `0.1.13-diag1`, refuses to overwrite an existing
diagnostic version, and preserves the `.13` source and built module for
rollback. It carries both existing video fixes (GK104 non-privileged legacy
contexts and the experimental legacy nonstall event index) alongside the
observational trace. It is restricted to the reviewed `7.0.0-34-generic`
kernel and source version.

## Build provenance check (2026-09-29)

The first isolated DKMS build was correctly blocked before installation because
its `srcversion` was `04E9CC457A2BA367431330B`, while the reviewed private
diagnostic build was `B19B8AAE48467545E652509`. This was a real source-set
difference, not a reason to relax the expected `srcversion`: the first DKMS
build log said the legacy FIFO nonstall patch was disabled. The diagnostic
staging code had copied the patch file but omitted its
`experimental-legacy-nonstall.enabled` marker, so `nvkm/engine/fifo/uchan.c`
did not contain the already-tested nonstall fix.

The package staging was corrected to include that marker. After rebuilding
the existing, not-yet-installed `0.1.13-diag1` entry, the DKMS log showed all
four expected patches applied, including the legacy FIFO nonstall patch and
the VP fence/CTXSW diagnostic. The rebuilt module reports the reviewed
`B19B8AAE48467545E652509` `srcversion`; the expected value was not changed.

The corrected DKMS source/header manifest and the reviewed private build
manifest each contain 1,211 entries and have the same SHA-256:
`b2be9c2206cce6ce3c9ef908129af2b7d03b53f9d73958752a791d52a42f5b68`. The
builds also used the same Ubuntu kernel headers, `.config`, `Module.symvers`,
generated configuration headers, GCC 15.2.0, and two parallel jobs. Their
compiled Nouveau object-target lists match. Hashes of the linked loadable
sections (`.text`, `.rodata`, `.data`, init/exit text and data, and
`.data..read_mostly`) match between the reviewed private artifact and the
corrected DKMS artifact. Their complete `.ko` file hashes differ because the
private artifact retains debug sections while the DKMS output is stripped;
this does not change the matching code/data section contents or `srcversion`.

DKMS deletes its temporary build tree after a successful build, so that tree's
`nouveau.mod` and per-object `.cmd` files are not available for a later
file-by-file comparison. The retained logs show matching patch application,
compiler version, job count, and object-target list; the exact transient
command files were not preserved. The corrected diagnostic remains **built,
not installed**. At this checkpoint the running and on-disk production module
remain `0.1.13`, with `srcversion` `57AE1B168D50DB546CD87A1`.

## Install and verify

Run from the repository on the existing `.13` baseline:

```bash
sudo tools/install-vp-fence-diagnostic.sh
sudo tools/verify-vp-fence-diagnostic.sh pre-reboot
```

The installer builds with two jobs by default to limit memory use. It records
the prior module, diagnostic module, build log, hashes, DKMS versions, vermagic,
and srcversion in a user cache directory printed at completion. It installs
the new on-disk module and refreshes initramfs; it does not unload or reload
the running Nouveau module.

After the installation checks pass, reboot and verify the newly loaded module:

```bash
sudo reboot
```

Then:

```bash
cd ~/nouveau-hpd-ddc-dkms
sudo tools/verify-vp-fence-diagnostic.sh post-reboot
```

The post-reboot verifier requires the diagnostic srcversion, DKMS status,
module parameters, and both parameter values `0`. Enable diagnostics only after
that check:

```bash
echo 1 | sudo tee /sys/module/nouveau/parameters/diag_fence_wait
echo 1 | sudo tee /sys/module/nouveau/parameters/diag_ctxsw
```

Then run exactly one controlled capture with the private native-surface Mesa
candidate:

```bash
DRI="$HOME/.cache/nouveau-vaapi-followup-20260928/private-instrumented-native-surface-fix-candidate/prefix/lib/x86_64-linux-gnu/dri"
tools/vaapi-capture.sh private-instrumented "$DRI"
```

Do not run another condition in the same boot if the capture reports
`STOP_A_B=1`.

## Roll back

To put the preserved `.13` module back on disk without deleting either DKMS
version, run:

```bash
sudo tools/rollback-vp-fence-diagnostic.sh
sudo reboot
```

The rollback helper uses `dkms install --force` for `.13`, checks the expected
baseline srcversion, and refreshes initramfs. It does not reload Nouveau. Do not
remove the diagnostic DKMS version before restoring `.13`: removing the active
diagnostic version would restore Ubuntu's original module, not the prior `.13`
module.
