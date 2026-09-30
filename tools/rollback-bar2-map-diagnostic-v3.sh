#!/usr/bin/env bash
set -Eeuo pipefail

NAME=nouveau-hpd-ddc
VERSION=0.1.13-diag2
DIAG_VERSION=0.1.13-diag3
KERNEL=7.0.0-34-generic
PREVIOUS_SRCVERSION=72DEE2B4ECFF77AD3764039
PREVIOUS_MODULE_SHA256=6bad3f6bf29031d370bddc6965559f88eb4f6f2267585556f23d1fb0e8a3a464
DIAG_SRCVERSION=9F90A7EB5A9E1505E0B6708
OPTION_FILE=/etc/modprobe.d/99-nouveau-diag-bar2-once.conf
OPTION_TEXT='options nouveau diag_bar2_map=1'
SCRIPT_PATH=$(readlink -f -- "${BASH_SOURCE[0]}")
if [[ $EUID -ne 0 ]]; then exec sudo -- "$SCRIPT_PATH" "$@"; fi
if (($# != 0)); then echo "Usage: $0" >&2; exit 2; fi
[[ $(uname -r) == "$KERNEL" ]] || { echo "ERROR: expected kernel $KERNEL" >&2; exit 2; }
diag2_module="/var/lib/dkms/$NAME/$VERSION/$KERNEL/$(uname -m)/module/nouveau.ko.zst"
[[ -f $diag2_module && $(modinfo -F srcversion "$diag2_module") == "$PREVIOUS_SRCVERSION" &&
   $(sha256sum "$diag2_module" | awk '{print $1}') == "$PREVIOUS_MODULE_SHA256" ]] || {
    echo 'ERROR: preserved diag2 module does not match the reviewed rollback target' >&2; exit 2;
}
loaded_before=$(cat /sys/module/nouveau/srcversion 2>/dev/null || true)
[[ $loaded_before == "$PREVIOUS_SRCVERSION" || $loaded_before == "$DIAG_SRCVERSION" ]] || {
    echo "ERROR: unexpected loaded Nouveau srcversion $loaded_before" >&2; exit 2;
}
if [[ -e $OPTION_FILE ]]; then
    [[ $(cat "$OPTION_FILE") == "$OPTION_TEXT" ]] || {
        echo "ERROR: preserving unrelated configuration at $OPTION_FILE" >&2; exit 2;
    }
    unlink "$OPTION_FILE"
fi
initrd="/boot/initrd.img-$KERNEL"
[[ -f $initrd ]] || { echo "ERROR: missing dracut image $initrd" >&2; exit 2; }

echo "Restoring $VERSION on disk; the currently loaded module will not be reloaded."
dkms install --force -m "$NAME" -v "$VERSION" -k "$KERNEL"
depmod -a "$KERNEL"
dracut --force "$initrd" "$KERNEL"
module_path=$(modinfo -n nouveau)
module_src=$(modinfo -F srcversion "$module_path")
module_hash=$(sha256sum "$module_path" | awk '{print $1}')
[[ $module_src == "$PREVIOUS_SRCVERSION" && $module_hash == "$PREVIOUS_MODULE_SHA256" ]] || {
    echo 'ERROR: on-disk module is not the pinned diag2 rollback target' >&2; exit 2;
}
listing=$(lsinitrd "$initrd" 2>/dev/null)
grep -Fq "usr/lib/modules/$KERNEL/updates/dkms/nouveau.ko.zst" <<<"$listing" || {
    echo 'ERROR: regenerated dracut image lacks Nouveau' >&2; exit 2;
}
option_in_image=$(lsinitrd -f "$OPTION_FILE" "$initrd" 2>/dev/null || true)
[[ -z $option_in_image ]] || { echo 'ERROR: one-boot BAR2 option remains in initramfs' >&2; exit 2; }
loaded_after=$(cat /sys/module/nouveau/srcversion 2>/dev/null || true)
[[ $loaded_after == "$loaded_before" ]] || { echo 'ERROR: rollback changed the running module' >&2; exit 2; }
printf 'restored_module_path=%s\nrestored_srcversion=%s\nrestored_module_sha256=%s\nloaded_srcversion=%s\n' \
    "$module_path" "$module_src" "$module_hash" "$loaded_after"
if [[ $loaded_after == "$DIAG_SRCVERSION" ]]; then
    echo 'PASS: diag2 is restored on disk; reboot is required before it is loaded.'
else
    echo 'PASS: diag2 is restored on disk and remains loaded.'
fi
