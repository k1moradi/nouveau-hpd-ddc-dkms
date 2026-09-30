#!/usr/bin/env bash
set -Eeuo pipefail

NAME=nouveau-hpd-ddc
VERSION=0.1.13
KERNEL=7.0.0-34-generic
EXPECTED_SRCVERSION=57AE1B168D50DB546CD87A1
EXPECTED_MODULE_SHA256=0cdb057ff3bddcefa88828f2910f5328f56f438f77e623a3bd1c5c33dbbc765c

kernel=$(uname -r)
[[ $kernel == "$KERNEL" ]] || {
    echo "FAIL: expected kernel $KERNEL, found $kernel" >&2
    exit 1
}
module_path=$(modinfo -n nouveau)
disk_srcversion=$(modinfo -F srcversion "$module_path")
disk_sha256=$(sha256sum "$module_path" | awk '{print $1}')
loaded_srcversion=$(cat /sys/module/nouveau/srcversion 2>/dev/null || true)
dkms_status=$(dkms status -m "$NAME" -v "$VERSION" -k "$KERNEL")
printf 'kernel=%s\nmodule_path=%s\ndisk_srcversion=%s\ndisk_sha256=%s\nloaded_srcversion=%s\ndkms_status=%s\n' \
    "$kernel" "$module_path" "$disk_srcversion" "$disk_sha256" \
    "$loaded_srcversion" "$dkms_status"

[[ $module_path == "/lib/modules/$KERNEL/updates/dkms/nouveau.ko.zst" &&
   $disk_srcversion == "$EXPECTED_SRCVERSION" &&
   $disk_sha256 == "$EXPECTED_MODULE_SHA256" &&
   $loaded_srcversion == "$EXPECTED_SRCVERSION" ]] || {
    echo 'FAIL: selected and loaded Nouveau are not the preserved production .13 module' >&2
    exit 1
}
grep -q 'installed' <<<"$dkms_status" || {
    echo 'FAIL: DKMS does not report production .13 installed' >&2
    exit 1
}

parameters=$(modinfo -p "$module_path")
for parameter in diag_fence_wait diag_ctxsw diag_bar2_map; do
    if grep -q "^$parameter:" <<<"$parameters"; then
        echo "FAIL: diagnostic parameter $parameter exists in the selected module" >&2
        exit 1
    fi
    if [[ -e "/sys/module/nouveau/parameters/$parameter" ]]; then
        echo "FAIL: loaded module exposes diagnostic parameter $parameter" >&2
        exit 1
    fi
done

echo 'PASS: preserved production Nouveau .13 is loaded; diagnostic probes are absent.'
