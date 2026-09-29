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
