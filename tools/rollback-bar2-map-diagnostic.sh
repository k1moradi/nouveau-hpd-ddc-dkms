#!/usr/bin/env bash
set -Eeuo pipefail

NAME=nouveau-hpd-ddc
VERSION=0.1.13-diag1
EXPECTED_KERNEL=7.0.0-34-generic
EXPECTED_SRCVERSION=B19B8AAE48467545E652509
SCRIPT_PATH=$(readlink -f -- "${BASH_SOURCE[0]}")

if [[ $EUID -ne 0 ]]; then
    exec sudo -- "$SCRIPT_PATH" "$@"
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
module="/var/lib/dkms/$NAME/$VERSION/$kernel/$(uname -m)/module/nouveau.ko.zst"
[[ -f $module ]] || {
    echo "ERROR: preserved $VERSION module is missing: $module" >&2
    exit 2
}
[[ $(modinfo -F srcversion "$module") == "$EXPECTED_SRCVERSION" ]] || {
    echo "ERROR: preserved $VERSION module has an unexpected srcversion" >&2
    exit 2
}

echo "Restoring DKMS $NAME/$VERSION on disk; this does not reload Nouveau."
dkms install --force -m "$NAME" -v "$VERSION" -k "$kernel"
depmod -a "$kernel"
update-initramfs -u -k "$kernel"
module_path=$(modinfo -n nouveau)
srcversion=$(modinfo -F srcversion "$module_path")
[[ $srcversion == "$EXPECTED_SRCVERSION" ]] || {
    echo "ERROR: rollback selected unexpected srcversion $srcversion" >&2
    exit 2
}
printf 'restored_module_path=%s\nrestored_srcversion=%s\n' "$module_path" "$srcversion"
printf 'loaded_srcversion=%s\n' "$(cat /sys/module/nouveau/srcversion 2>/dev/null || true)"
echo "Rollback is on disk only; reboot without the one-time nouveau.diag_bar2_map=1 argument."
