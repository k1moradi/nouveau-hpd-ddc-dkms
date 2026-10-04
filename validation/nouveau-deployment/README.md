# Reviewed Nouveau diagnostic deployment and first-use runbook

Status: CPU/build preparation only. No module has been installed or loaded,
the initramfs has not been changed, no VA/GPU workload has been run, and this
service has not rebooted the machine. The current loaded module remains the old
`354DCD95A5804DE00E20EFB`; it is not eligible for the planned experiments.

The deployment plan pins kernel `7.0.0-34-generic`, diagnostic Nouveau
srcversion `936407678F3DA1E8515F5EC`, both kernel patch hashes, Mesa A/B DSOs,
the supervisor and diagnostic scripts, the H.264 input, and the exact retained
software NV12 reference. The retained enabled `.ko` and its build manifest are
created by the separate clean-build step described below. The previously
completed full disabled build is evidence only; it contains no diagnostic
markers and was not changed by this CPU-only work.

## 1. Build and retain the exact enabled module

Run this only after all review-branch changes are committed and the worktree is
clean. The builder refuses another branch, a moved `main`, a dirty worktree, a
different review commit, source/patch hash mismatch, unexpected srcversion or
vermagic, or absent diagnostic markers. It cleans and builds the pinned source
overlay, then retains the raw module, logs, source fingerprint, compiler
identity, and checksums outside the Git tree:

```sh
cd /home/keivan/nouveau-hpd-ddc-dkms
BUILD_DIR=/home/keivan/nouveau-vaapi-app-validation/v3-deploy-20261004-reboot-ready
PYTHONDONTWRITEBYTECODE=1 python3 validation/nouveau-deployment/build_retained_module.py \
  --source-root /home/keivan/nouveau-vaapi-app-validation/nouveau-vma-fullbuild-20261004/source \
  --kernel-headers /usr/src/linux-headers-7.0.0-34-generic \
  --nvif-duplicate-patch validation/nouveau-nvif-duplicate-diagnostic/patches/0009-drm-nouveau-log-nvif-duplicate-layer.patch \
  --vma-lifecycle-patch validation/nouveau-bar2-vma-diagnostic/patches/0010-drm-nouveau-correlate-instmem-vma-selftest.patch \
  --output-dir "$BUILD_DIR" \
  --review-commit "$(git rev-parse HEAD)"
```

This produces `nouveau.ko`, `build-manifest.json`, `SHA256SUMS`, the clean and
reverse-patch-check logs, and the W=1 build log. It does not install, sign,
compress, load, or reboot. The output directory must not already exist.

The header package's `make kernelrelease` target prints the upstream base
`7.0.14`, while its generated `include/config/kernel.release` and
`include/generated/utsrelease.h` both pin the distro target to
`7.0.0-34-generic`. The builder checks that those two generated target fields
agree, records the upstream base separately, and the completed module must
still pass exact `vermagic=7.0.0-34-generic ...` validation.

## 2. Inspect the install plan, then finalize only from the controlled desktop

First run the default read-only mode. It must print `INSTALL_PLAN_ONLY=true`
and `NO_SYSTEM_CHANGES=true`:

```sh
cd /home/keivan/nouveau-hpd-ddc-dkms
BUILD_DIR=/home/keivan/nouveau-vaapi-app-validation/v3-deploy-20261004-reboot-ready
python3 validation/nouveau-deployment/finalize_install.py \
  --build-manifest "$BUILD_DIR/build-manifest.json" \
  --raw-module "$BUILD_DIR/nouveau.ko" \
  --manifest-output "$BUILD_DIR/deployment-manifest.json"
```

Only after the artifact, plan, and review are accepted, a user at the physical
desktop may run the same command with `sudo` and `--apply`:

```sh
sudo python3 validation/nouveau-deployment/finalize_install.py \
  --build-manifest /home/keivan/nouveau-vaapi-app-validation/v3-deploy-20261004-reboot-ready/build-manifest.json \
  --raw-module /home/keivan/nouveau-vaapi-app-validation/v3-deploy-20261004-reboot-ready/nouveau.ko \
  --manifest-output /home/keivan/nouveau-vaapi-app-validation/v3-deploy-20261004-reboot-ready/deployment-manifest.json \
  --apply
```

`--apply` installs the pinned `.ko.zst`, runs `depmod`, updates the pinned
initramfs, extracts every embedded Nouveau module, and writes the finalized
manifest only if installed and initramfs bytes match exactly. It never unloads
or reloads Nouveau and never reboots. Secure Boot is detected automatically;
if enabled, the command refuses to proceed without a supplied signing key and
certificate that `mokutil --test-key` reports as enrolled. Add
`--signing-key /path/to/enrolled.key --signing-cert /path/to/enrolled.der`
when required. Any nonzero result or missing finalized manifest means **do not
reboot**; preserve the logs and review the partial install state first.

The finalized manifest records the review branch/commit and fixed `main`
commit, retained build-manifest hash, plan hash, raw and installed module
hashes, srcversion, vermagic, signature identity if used, compressed module
hash, module parameters, initramfs hash and embedded module hashes, Mesa A/B
DSO hashes, every diagnostic tool hash, input hash, and software reference
manifest/container hashes.

## 3. One-command post-boot admission

From a terminal inside the logged-in local X11 desktop, do not run the script
with `sudo`. Refresh the noninteractive privilege timestamp for its one
root-only module-parameter read, then run the admission check:

```sh
sudo -v
cd /home/keivan/nouveau-hpd-ddc-dkms
PYTHONDONTWRITEBYTECODE=1 python3 validation/nouveau-deployment/admit_run.py \
  --manifest /home/keivan/nouveau-vaapi-app-validation/v3-deploy-20261004-reboot-ready/deployment-manifest.json
```

Only the exact line `RUN_ELIGIBLE=true` with exit status zero admits a test.
Otherwise it prints `RUN_ELIGIBLE=false` and reasons. It checks the running
kernel; selected installed path, compressed and uncompressed module hashes,
srcversion and vermagic; loaded srcversion and `diag_ctxsw`; initramfs path,
hash, and every embedded Nouveau module; readable current-boot kernel journal;
zero whole-boot BAR2/HOST_CPU/PTE and configured hard-stop signatures; active
local nonremote X11 session on `seat0` and a real VT; reachable `DISPLAY`; no
existing media player; Mesa A/B DSO hashes; executing admission/supervisor
identities; pinned diagnostic tools; input; build manifest; deployment plan;
and exact software NV12 reference capture.

The manifest is deliberately labeled
`FINALIZED_NOT_REBOOTED_NOT_RUNTIME_VERIFIED`. Admission rechecks the live
state and does not rewrite that immutable build/deployment record.

## 4. Isolated first-boot sequence and stop rules

Do not start a stage unless admission printed `RUN_ELIGIBLE=true` immediately
before it. After each stage, synchronize/archive the kernel journal and rerun
admission before continuing. Any BAR2/HOST_CPU/PTE, CTXSW, channel kill,
failed-idle, GPU reset, BUG, Oops, WARNING, sanitizer, lockdep, or other pinned
hard stop ends the experiment sequence for that boot. Preserve the whole-boot
journal and do not start another workload on that boot.

Find the single reviewed device's debugfs root without reading any selftest
file (reading a selftest file executes it):

```sh
sudo find /sys/kernel/debug/dri -maxdepth 2 -type f \
  \( -name selftest_safe -o -name selftest_falcon_contract \
     -o -name selftest_instmem_4k -o -name selftest_fifo_fence \) -print
```

Set `SELFTEST_DIR` to the exact directory returned for the K4200. Save every
stage output under a new boot-ID directory. Each command is individually
bounded; a timeout, missing explicit PASS result, nonzero command exit, or
hard-stop signature means stop:

```sh
set -euo pipefail
RUN_DIR=/home/keivan/nouveau-vaapi-app-validation/first-reviewed-boot-$(cat /proc/sys/kernel/random/boot_id)
mkdir -m 700 "$RUN_DIR"
SELFTEST_DIR=/sys/kernel/debug/dri/<verified-minor>
admit_now() {
  PYTHONDONTWRITEBYTECODE=1 python3 validation/nouveau-deployment/admit_run.py \
    --manifest /home/keivan/nouveau-vaapi-app-validation/v3-deploy-20261004-reboot-ready/deployment-manifest.json
}
archive_kernel() {
  local label="$1"
  sudo journalctl --sync
  sudo journalctl -k -b -o json --no-pager > "$RUN_DIR/whole-boot-kernel-${label}.jsonl"
}
admit_now

# Stage B: safe inventory.
sudo timeout --signal=TERM --kill-after=2s 30s cat "$SELFTEST_DIR/selftest_safe" \
  | tee "$RUN_DIR/v2-safe-inventory.txt"
archive_kernel after-v2
admit_now

# Stage C: GK104 Falcon contract.
sudo timeout --signal=TERM --kill-after=2s 30s cat "$SELFTEST_DIR/selftest_falcon_contract" \
  | tee "$RUN_DIR/v6-falcon-contract.txt"
archive_kernel after-v6
admit_now

# Stage D: one 4 KiB BAR2/VMA lifecycle.
sudo timeout --signal=TERM --kill-after=2s 45s cat "$SELFTEST_DIR/selftest_instmem_4k" \
  | tee "$RUN_DIR/v3-instmem-4k.txt"
archive_kernel after-v3
set +e
PYTHONDONTWRITEBYTECODE=1 python3 validation/nouveau-bar2-vma-diagnostic/correlate_v3.py \
  "$RUN_DIR/whole-boot-kernel-after-v3.jsonl" \
  > "$RUN_DIR/v3-correlation.json"
CORRELATOR_STATUS=$?
set -e
if [ "$CORRELATOR_STATUS" -ne 3 ]; then
  echo "VMA correlator found a fault or failed; stop this boot's experiment sequence"
  exit 1
fi
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d.get("fault_count") == 0 else 1)' \
  "$RUN_DIR/v3-correlation.json" || {
    echo "VMA correlation report contains fault records; stop this boot"
    exit 1
  }
admit_now
```

The correlator returns status 3 when the input contains no BAR2 fault and
status 0 when it analyzed one or more fault records; inspect its JSON `fault_count`
and per-fault outcome. A numeric VMA candidate is correlation only, not proof
that v3 caused the fault. If a fault occurs, stop immediately and do not run
v3 again, v4, or MPV on that boot. Repeat v3 only after the first v3 run is
clean and a fresh eligible boot has been admitted.

Stage F (`selftest_fifo_fence`) follows only after v3 is clean and admission
passes:

```sh
sudo timeout --signal=TERM --kill-after=2s 45s cat "$SELFTEST_DIR/selftest_fifo_fence" \
  | tee "$RUN_DIR/v4-fifo-fence.txt"
archive_kernel after-v4
admit_now
```

Repeat v3 only on a fresh boot after admission; use a new boot-ID output
directory and preserve the same stop rules. Keep the primitive tests separate
from MPV and pixel runs.

### MPV Variant A/B

Use stock `/usr/bin/mpv`; only Mesa's private Nouveau VA DSO changes. Run A
only after kernel primitive validation, on a fresh eligible boot:

```sh
python3 validation/mesa-nvif-lifetime-diagnostic/capture_nvif_lifetime_run.py \
  --variant A \
  --dso /home/keivan/nouveau-vaapi-app-validation/mesa-nvif-lifetime-ab-build-20261004-pointer-free/A/libgallium_drv_video.so \
  --deployment-manifest /home/keivan/nouveau-vaapi-app-validation/v3-deploy-20261004-reboot-ready/deployment-manifest.json \
  --output-dir /home/keivan/nouveau-vaapi-app-validation/nvif-A-$(cat /proc/sys/kernel/random/boot_id) \
  --execute
```

Analyze the generated journal delta/manifest with
`parse_nvif_lifetime_ab.py --variant A --journal ... --manifest ...`. Do not
run B unless the parser returns `A_CHAIN_PROVEN`, requiring same BSP key,
wrong DEL fd, recorded DEL result, channel teardown, same-key replacement
`-EEXIST`, ABI16 duplicate, and no earlier hard stop. A nonzero capture status
is not proof.

Only after preserving that exact A proof, run B on another fresh eligible boot
with the same command/profile, B DSO, and all three A proof files:

```sh
python3 validation/mesa-nvif-lifetime-diagnostic/capture_nvif_lifetime_run.py \
  --variant B \
  --dso /home/keivan/nouveau-vaapi-app-validation/mesa-nvif-lifetime-ab-build-20261004-pointer-free/B/libgallium_drv_video.so \
  --deployment-manifest /home/keivan/nouveau-vaapi-app-validation/v3-deploy-20261004-reboot-ready/deployment-manifest.json \
  --output-dir /home/keivan/nouveau-vaapi-app-validation/nvif-B-$(cat /proc/sys/kernel/random/boot_id) \
  --a-proof /path/to/A/proof.json \
  --a-manifest /path/to/A/manifest.json \
  --a-journal /path/to/A/journal-delta.jsonl \
  --execute
```

The parser may report only `B_CORRECTION_PROVEN` for the matched NVIF stale-key
lifecycle; it does not prove visible playback. Never run B when A was not
exactly reproduced.

### Late pixelation and FFplay export

After kernel primitive health is established, use the five-repeat raw NV12
procedure in [`../late-pixelation/README.md`](../late-pixelation/README.md) on a
separate clean boot. It compares each hardware timestamp with the pinned
software reference; no screenshot time substitutes for decoded PTS.

Run the VA export contract probe separately, with its explicit `--execute`
gate, only after the reviewed deployment is admitted and a fresh kernel
baseline is clean. The probe is not ffplay, and its result must be interpreted
against the configuration-level VA attributes it records. Do not install or
run the progressive-export candidate until the advertised capability and
surface-class contract are understood.

## Safety boundary

This package contains no player patches and no production fix. It does not
install or execute the probe by default, load a module, run a test, or reboot.
All hardware actions above are future user-controlled operations from a real
desktop after exact provenance admission.
