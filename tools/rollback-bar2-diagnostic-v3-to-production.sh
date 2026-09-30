#!/usr/bin/env bash
set -Eeuo pipefail

NAME=nouveau-hpd-ddc
PRODUCTION_VERSION=0.1.13
DIAGNOSTIC_VERSION=0.1.13-diag3
KERNEL=7.0.0-34-generic
PRODUCTION_SRCVERSION=57AE1B168D50DB546CD87A1
PRODUCTION_MODULE_SHA256=0cdb057ff3bddcefa88828f2910f5328f56f438f77e623a3bd1c5c33dbbc765c
DIAGNOSTIC_SRCVERSION=9F90A7EB5A9E1505E0B6708
DIAGNOSTIC_MODULE_SHA256=d437863bd12473c8dbba7104cf8bccdc7a2b67fafe10a9e0d5f963a168245aca
OPTION_FILE=/etc/modprobe.d/99-nouveau-diag-bar2-once.conf
INITRD="/boot/initrd.img-$KERNEL"
SCRIPT_PATH=$(readlink -f -- "${BASH_SOURCE[0]}")
CHECK_ONLY=0

if [[ $EUID -ne 0 ]]; then
    exec sudo -- "$SCRIPT_PATH" "$@"
fi
if (($# == 1)) && [[ $1 == --check-only ]]; then
    CHECK_ONLY=1
elif (($# != 0)); then
    echo "Usage: $0 [--check-only]" >&2
    exit 2
fi
[[ $(uname -r) == "$KERNEL" ]] || {
    echo "ERROR: expected kernel $KERNEL" >&2
    exit 2
}
command -v dracut >/dev/null || {
    echo 'ERROR: dracut is required to regenerate this host initramfs' >&2
    exit 2
}
[[ -f $INITRD ]] || {
    echo "ERROR: missing initramfs $INITRD" >&2
    exit 2
}

module_path=$(modinfo -n nouveau)
module_srcversion=$(modinfo -F srcversion "$module_path")
module_sha256=$(sha256sum "$module_path" | awk '{print $1}')
loaded_srcversion=$(cat /sys/module/nouveau/srcversion 2>/dev/null || true)
printf 'kernel=%s\nselected_module=%s\nselected_srcversion=%s\nselected_sha256=%s\nloaded_srcversion=%s\n' \
    "$KERNEL" "$module_path" "$module_srcversion" "$module_sha256" "$loaded_srcversion"

[[ $module_path == "/lib/modules/$KERNEL/updates/dkms/nouveau.ko.zst" &&
   $module_srcversion == "$DIAGNOSTIC_SRCVERSION" &&
   $module_sha256 == "$DIAGNOSTIC_MODULE_SHA256" &&
   $loaded_srcversion == "$DIAGNOSTIC_SRCVERSION" ]] || {
    echo 'ERROR: current selected and loaded modules are not the pinned diag3 artifact' >&2
    exit 2
}

production_module="/var/lib/dkms/$NAME/$PRODUCTION_VERSION/$KERNEL/$(uname -m)/module/nouveau.ko.zst"
[[ -f $production_module &&
   $(modinfo -F srcversion "$production_module") == "$PRODUCTION_SRCVERSION" &&
   $(sha256sum "$production_module" | awk '{print $1}') == "$PRODUCTION_MODULE_SHA256" ]] || {
    echo 'ERROR: preserved production .13 module does not match the reviewed artifact' >&2
    exit 2
}

if [[ -e $OPTION_FILE ]]; then
    echo "ERROR: preserving unexpected one-shot modprobe file $OPTION_FILE" >&2
    exit 2
fi
production_status=$(dkms status -m "$NAME" -v "$PRODUCTION_VERSION" -k "$KERNEL")
grep -q 'built' <<<"$production_status" || {
    echo 'ERROR: production .13 must already be built before rollback' >&2
    exit 2
}

if [[ $CHECK_ONLY -eq 1 ]]; then
    printf 'production_module=%s\nproduction_srcversion=%s\nproduction_sha256=%s\n' \
        "$production_module" "$PRODUCTION_SRCVERSION" "$PRODUCTION_MODULE_SHA256"
    printf 'diagnostic_module=%s\ndiagnostic_srcversion=%s\n' \
        "$module_path" "$loaded_srcversion"
    echo 'PASS: rollback preconditions verified; no package, initramfs, or module state changed.'
    exit 0
fi

echo "Installing the preserved production $PRODUCTION_VERSION module on disk; no module reload is performed."
dkms install --force -m "$NAME" -v "$PRODUCTION_VERSION" -k "$KERNEL"
depmod -a "$KERNEL"
dracut --force "$INITRD" "$KERNEL"

restored_path=$(modinfo -n nouveau)
restored_srcversion=$(modinfo -F srcversion "$restored_path")
restored_sha256=$(sha256sum "$restored_path" | awk '{print $1}')
[[ $restored_path == "/lib/modules/$KERNEL/updates/dkms/nouveau.ko.zst" &&
   $restored_srcversion == "$PRODUCTION_SRCVERSION" &&
   $restored_sha256 == "$PRODUCTION_MODULE_SHA256" ]] || {
    echo 'ERROR: selected on-disk module is not the pinned production .13 artifact' >&2
    exit 2
}

parameters=$(modinfo -p "$restored_path")
for parameter in diag_fence_wait diag_ctxsw diag_bar2_map; do
    if grep -q "^$parameter:" <<<"$parameters"; then
        echo "ERROR: diagnostic parameter $parameter remains in production module" >&2
        exit 2
    fi
done

status_after=$(dkms status -m "$NAME" -v "$PRODUCTION_VERSION" -k "$KERNEL")
grep -q 'installed' <<<"$status_after" || {
    echo 'ERROR: DKMS does not report production .13 installed' >&2
    exit 2
}
initrd_listing=$(lsinitrd "$INITRD" 2>/dev/null)
grep -Fq "usr/lib/modules/$KERNEL/updates/dkms/nouveau.ko.zst" <<<"$initrd_listing" || {
    echo 'ERROR: dracut image does not contain the selected Nouveau module' >&2
    exit 2
}
initrd_option=$(lsinitrd -f "$OPTION_FILE" "$INITRD" 2>/dev/null || true)
[[ -z $initrd_option ]] || {
    echo 'ERROR: future boots would re-enable the one-shot BAR2 option' >&2
    exit 2
}
initrd_module_sha256=$(lsinitrd -f "usr/lib/modules/$KERNEL/updates/dkms/nouveau.ko.zst" "$INITRD" 2>/dev/null | sha256sum | awk '{print $1}')
[[ $initrd_module_sha256 == "$PRODUCTION_MODULE_SHA256" ]] || {
    echo 'ERROR: dracut image does not contain the pinned production module bytes' >&2
    exit 2
}

loaded_after=$(cat /sys/module/nouveau/srcversion 2>/dev/null || true)
[[ $loaded_after == "$DIAGNOSTIC_SRCVERSION" ]] || {
    echo 'ERROR: rollback unexpectedly changed the currently loaded module' >&2
    exit 2
}
printf 'restored_module_path=%s\nrestored_srcversion=%s\nrestored_sha256=%s\n' \
    "$restored_path" "$restored_srcversion" "$restored_sha256"
printf 'loaded_srcversion=%s\ndkms_status=%s\n' "$loaded_after" "$status_after"
echo 'PASS: production .13 is selected on disk and in dracut; diag3 remains loaded until reboot.'
