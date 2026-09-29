#!/usr/bin/env bash
set -Eeuo pipefail

NAME=nouveau-hpd-ddc
BASE_VERSION=0.1.13
DIAG_VERSION=0.1.13-diag1
EXPECTED_KERNEL=7.0.0-34-generic
EXPECTED_BASE_SRCVERSION=57AE1B168D50DB546CD87A1

if [[ $EUID -ne 0 ]]; then
    exec sudo -- "$(readlink -f -- "$0")" "$@"
fi
if (($# != 0)); then
    echo "Usage: $0" >&2
    exit 2
fi
kernel=$(uname -r)
[[ $kernel == "$EXPECTED_KERNEL" ]] || {
    echo "ERROR: rollback was prepared for $EXPECTED_KERNEL; running kernel is $kernel" >&2
    exit 2
}
baseline_module="/var/lib/dkms/$NAME/$BASE_VERSION/$kernel/$(uname -m)/module/nouveau.ko.zst"
[[ -f $baseline_module ]] || {
    echo "ERROR: preserved baseline module is missing: $baseline_module" >&2
    exit 2
}
[[ $(modinfo -F srcversion "$baseline_module") == "$EXPECTED_BASE_SRCVERSION" ]] || {
    echo "ERROR: preserved baseline module srcversion does not match the reviewed build" >&2
    exit 2
}

echo "Restoring DKMS $NAME/$BASE_VERSION for $kernel; this does not reload Nouveau."
dkms install --force -m "$NAME" -v "$BASE_VERSION" -k "$kernel"
depmod -a "$kernel"
update-initramfs -u -k "$kernel"
module_path=$(modinfo -n nouveau)
srcversion=$(modinfo -F srcversion "$module_path")
[[ $srcversion == "$EXPECTED_BASE_SRCVERSION" ]] || {
    echo "ERROR: rollback selected unexpected srcversion $srcversion" >&2
    exit 2
}
echo "Restored on-disk $BASE_VERSION module at $module_path; reboot to load it."
