# Ambient BAR2 diagnostic deployment

This deployment is for one passive ambient BAR2 first-fault capture. It is not
an application or selftest boot. Do not start media, VA, debugfs selftests, or
other controlled GPU work while the reviewed kernel has reproduced
BAR2/HOST_CPU/PTE faults before workloads.

The retained 0011 enabled module is:

```text
SHA-256:   2a2f5b9098696fea53617e9534965851b91b7ef4dcd3152b3dc2ab62baf6ab86
srcversion: 76FD3C4C33BEDD3450C720B
vermagic:   7.0.0-34-generic SMP preempt mod_unload modversions
```

It was built from the retained 0011 source overlay with a full W=1 module
link. The full disabled link was also completed and its new ambient diagnostic
markers and `diag_bar2_map` parameter are absent. The build manifest records
the retained build evidence and keeps enabled and disabled module hashes
separate. The currently running module remains the earlier `936407678F3DA1E8515F5EC`
build until a later user-controlled reboot; this document does not authorize a
reboot.

## Finalize the retained artifact

The new immutable deployment root is:

```text
/home/keivan/nouveau-vaapi-app-validation/ambient-bar2-0011-deploy-20261004
```

The exact plan, build manifest, and retained raw module live there. First run
the finalizer without `--apply` to validate all pinned hashes. The authorized
apply command installs the compressed module, runs `depmod`, installs the
exact early-load option, rebuilds the initramfs if either module bytes or
options are missing, extracts and verifies both, and writes the finalized
manifest. It does not load/reload Nouveau or reboot.

```sh
cd /home/keivan/nouveau-hpd-ddc-dkms
DEPLOY=/home/keivan/nouveau-vaapi-app-validation/ambient-bar2-0011-deploy-20261004
PYTHONDONTWRITEBYTECODE=1 python3 validation/nouveau-deployment/finalize_install.py \
  --plan "$DEPLOY/deployment-plan.json" \
  --build-manifest "$DEPLOY/build-manifest.json" \
  --raw-module "$DEPLOY/nouveau.ko" \
  --manifest-output "$DEPLOY/deployment-manifest.json"
```

Only after the read-only plan succeeds, apply from the logged-in desktop:

```sh
sudo --preserve-env=DISPLAY,XAUTHORITY,XDG_SESSION_ID,XDG_SESSION_TYPE,XDG_RUNTIME_DIR \
  env PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 \
  /home/keivan/nouveau-hpd-ddc-dkms/validation/nouveau-deployment/finalize_install.py \
  --plan /home/keivan/nouveau-vaapi-app-validation/ambient-bar2-0011-deploy-20261004/deployment-plan.json \
  --build-manifest /home/keivan/nouveau-vaapi-app-validation/ambient-bar2-0011-deploy-20261004/build-manifest.json \
  --raw-module /home/keivan/nouveau-vaapi-app-validation/ambient-bar2-0011-deploy-20261004/nouveau.ko \
  --manifest-output /home/keivan/nouveau-vaapi-app-validation/ambient-bar2-0011-deploy-20261004/deployment-manifest.json \
  --apply
```

The only active Nouveau module option after finalization must be:

```text
options nouveau diag_bar2_map=Y diag_ctxsw=N
```

The finalizer preserves the previous modprobe file in the deployment root,
comments out that old file, and pins the new file's exact bytes in the
manifest. It verifies the option file appears in the initramfs and that the
effective Nouveau options extracted from the initramfs are exactly the pinned
parameter set. Do not manually change either file, the installed module, or
the initramfs after finalization.

The manifest status must remain:

```text
FINALIZED_NOT_REBOOTED_NOT_RUNTIME_VERIFIED
```

## Admission and first-fault capture

After a future reboot, do not read selftest debugfs files and do not launch a
player or VA probe. If no BAR2/PTE fault has occurred, run ordinary admission
from the real local desktop:

```sh
cd /home/keivan/nouveau-hpd-ddc-dkms
sudo --preserve-env=DISPLAY,XAUTHORITY,XDG_SESSION_ID,XDG_SESSION_TYPE,XDG_RUNTIME_DIR \
  env PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 validation/nouveau-deployment/admit_run.py \
  --manifest /home/keivan/nouveau-vaapi-app-validation/ambient-bar2-0011-deploy-20261004/deployment-manifest.json
```

Only `RUN_ELIGIBLE=true` proves the pre-fault baseline gates passed. It still
authorizes only passive observation for this deployment. Keep the normal X11
desktop operating without deliberately triggering unrelated activity. Observe
for a bounded 30-minute window, which covers the earliest previously recorded
fault time of about 867 seconds. A clean window is only a clean bounded
observation, not a fix.

If a BAR2/HOST_CPU/PTE fault appears at any time, stop. Run no more GPU work.
Preserve the whole system and kernel journals, then run the distinct archival
gate. That gate must print `RUN_ELIGIBLE=false`; it can print
`AMBIENT_FAULT_CAPTURE_ELIGIBLE=true` only for exact module/initramfs/options
provenance, a parsed fault stream with one module-load epoch, and the explicit
operator assertion that Nouveau was not unloaded/reloaded. The assertion is
not machine proof; the sequence parser independently rejects a repeated
diagnostic sequence.

```sh
set -euo pipefail
DEPLOY=/home/keivan/nouveau-vaapi-app-validation/ambient-bar2-0011-deploy-20261004
RUN_DIR=/home/keivan/nouveau-vaapi-app-validation/ambient-first-fault-$(cat /proc/sys/kernel/random/boot_id)
mkdir -m 700 "$RUN_DIR"
sudo journalctl --sync
sudo journalctl -k -b -o json --no-pager > "$RUN_DIR/whole-boot-kernel.jsonl"
sudo journalctl -b -o json --no-pager > "$RUN_DIR/whole-boot-system.jsonl"
sha256sum "$RUN_DIR/whole-boot-kernel.jsonl" "$RUN_DIR/whole-boot-system.jsonl" \
  > "$RUN_DIR/SHA256SUMS"

PYTHONDONTWRITEBYTECODE=1 python3 \
  /home/keivan/nouveau-hpd-ddc-dkms/validation/nouveau-bar2-ambient-diagnostic/correlate_bar2_lifetimes.py \
  "$RUN_DIR/whole-boot-kernel.jsonl" > "$RUN_DIR/bar2-correlation.json"

sudo --preserve-env=DISPLAY,XAUTHORITY,XDG_SESSION_ID,XDG_SESSION_TYPE,XDG_RUNTIME_DIR \
  env PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 \
  /home/keivan/nouveau-hpd-ddc-dkms/validation/nouveau-deployment/admit_run.py \
  --manifest "$DEPLOY/deployment-manifest.json" \
  --ambient-fault-capture --confirm-no-nouveau-reload \
  | tee "$RUN_DIR/ambient-capture-admission.txt"
```

The archival gate never admits another workload. Keep the original journal
files unchanged. The ambient correlator accepts exactly one boot and one
Nouveau module-load epoch; it reports numeric range correlation only, never
object ownership or causation. `KMAP_LAST_RELEASE` is a CPU map-reference
transition to zero, not VMA eviction. A VMA is evicted only at its separately
logged teardown event.

## Stop boundary and deferred work

The first ambient BAR2/PTE fault is the primary event. After it, do not run
v2, v3, v4, MPV A/B, ffplay, VA export, or late-pixel capture on that boot.
Analyze the first fault's mapping, kmap reference, VMA residency, mapping reset
generation, and lifecycle sequence before considering any follow-up. Keep
`main` on HOLD and do not convert diagnostic correlation into a functional
fix without source and runtime evidence.
