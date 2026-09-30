#!/usr/bin/env bash
set -Eeuo pipefail

NAME=nouveau-hpd-ddc
VERSION=0.1.13-diag3
PREVIOUS_SRCVERSION=72DEE2B4ECFF77AD3764039
EXPECTED_SRCVERSION=9F90A7EB5A9E1505E0B6708
EXPECTED_MODULE_SHA256=d437863bd12473c8dbba7104cf8bccdc7a2b67fafe10a9e0d5f963a168245aca
EXPECTED_KERNEL=7.0.0-34-generic
OPTION_FILE=/etc/modprobe.d/99-nouveau-diag-bar2-once.conf
OPTION_TEXT='options nouveau diag_bar2_map=1'
mode=${1:-post-reboot}
SCRIPT_PATH=$(readlink -f -- "${BASH_SOURCE[0]}")
if [[ $EUID -ne 0 ]]; then exec sudo -- "$SCRIPT_PATH" "$@"; fi
if [[ $mode != pre-reboot && $mode != post-reboot && $mode != clear-early-option ]]; then
    echo "Usage: $0 [pre-reboot|post-reboot|clear-early-option]" >&2
    exit 2
fi

kernel=$(uname -r)
[[ $kernel == "$EXPECTED_KERNEL" ]] || { echo "FAIL: expected kernel $EXPECTED_KERNEL" >&2; exit 1; }
module_path=$(modinfo -n nouveau)
disk_src=$(modinfo -F srcversion "$module_path")
disk_hash=$(sha256sum "$module_path" | awk '{print $1}')
status=$(dkms status -m "$NAME" -v "$VERSION" -k "$kernel")
printf 'kernel=%s\nmodule_path=%s\ndisk_srcversion=%s\ndisk_sha256=%s\ndkms_status=%s\n' \
    "$kernel" "$module_path" "$disk_src" "$disk_hash" "$status"
[[ $module_path == "/lib/modules/$kernel/updates/dkms/nouveau.ko.zst" &&
   $disk_src == "$EXPECTED_SRCVERSION" && $disk_hash == "$EXPECTED_MODULE_SHA256" ]] || {
    echo 'FAIL: on-disk module is not the pinned diag3 artifact' >&2; exit 1;
}
grep -q 'installed' <<<"$status" || { echo 'FAIL: DKMS does not report diag3 installed' >&2; exit 1; }
params=$(modinfo -p "$module_path")
for parameter in diag_fence_wait diag_ctxsw diag_bar2_map; do
    grep -q "^$parameter:" <<<"$params" || { echo "FAIL: missing module parameter $parameter" >&2; exit 1; }
done

initrd="/boot/initrd.img-$kernel"
[[ -f $initrd ]] || { echo "FAIL: missing dracut image $initrd" >&2; exit 1; }
image_listing=$(lsinitrd "$initrd" 2>/dev/null)
grep -Fq "usr/lib/modules/$kernel/updates/dkms/nouveau.ko.zst" <<<"$image_listing" || {
    echo 'FAIL: dracut image does not contain the selected Nouveau module' >&2; exit 1;
}
loaded_src=$(cat /sys/module/nouveau/srcversion 2>/dev/null || true)
printf 'loaded_srcversion=%s\n' "${loaded_src:-unavailable}"

parameter_value() { cat "/sys/module/nouveau/parameters/$1"; }
check_fence_ctxsw_off() {
    for parameter in diag_fence_wait diag_ctxsw; do
        value=$(parameter_value "$parameter")
        printf '%s=%s\n' "$parameter" "$value"
        [[ $value == 0 || $value == N ]] || {
            echo "FAIL: $parameter must remain off until explicitly enabled" >&2; exit 1;
        }
    done
}

if [[ $mode == pre-reboot ]]; then
    [[ $loaded_src == "$PREVIOUS_SRCVERSION" ]] || {
        echo 'FAIL: diag2 should remain loaded before reboot' >&2; exit 1;
    }
    option_in_image=$(lsinitrd -f "$OPTION_FILE" "$initrd" 2>/dev/null || true)
    [[ $option_in_image == "$OPTION_TEXT" && ! -e $OPTION_FILE ]] || {
        echo 'FAIL: dracut one-boot BAR2 setting is absent or still present on the host' >&2; exit 1;
    }
    echo 'PASS: diag3 is on disk; diag2 remains loaded; one-boot BAR2 tracing is staged.'
    exit 0
fi

[[ $loaded_src == "$EXPECTED_SRCVERSION" ]] || { echo 'FAIL: diag3 is not loaded' >&2; exit 1; }
bar2=$(parameter_value diag_bar2_map)
printf 'diag_bar2_map=%s\n' "$bar2"
[[ $bar2 == 1 || $bar2 == Y ]] || { echo 'FAIL: diag_bar2_map is not enabled' >&2; exit 1; }
check_fence_ctxsw_off

if [[ $mode == post-reboot ]]; then
    option_in_image=$(lsinitrd -f "$OPTION_FILE" "$initrd" 2>/dev/null || true)
    [[ $option_in_image == "$OPTION_TEXT" && ! -e $OPTION_FILE ]] || {
        echo 'FAIL: boot did not preserve the expected dracut one-boot option' >&2; exit 1;
    }
    echo 'PASS: diag3 loaded with BAR2 mapping tracing enabled; other probes are off.'
    exit 0
fi

[[ ! -e $OPTION_FILE ]] || { echo 'FAIL: preserving existing host modprobe configuration' >&2; exit 1; }
if tr ' ' '\n' < /proc/cmdline | grep -Fxq 'nouveau.diag_bar2_map=1'; then
    echo 'FAIL: remove any manual kernel argument before clearing the dracut option' >&2
    exit 1
fi
dracut --force "$initrd" "$kernel"
option_in_image=$(lsinitrd -f "$OPTION_FILE" "$initrd" 2>/dev/null || true)
[[ -z $option_in_image ]] || { echo 'FAIL: initramfs still contains the one-boot setting' >&2; exit 1; }
image_listing=$(lsinitrd "$initrd" 2>/dev/null)
grep -Fq "usr/lib/modules/$kernel/updates/dkms/nouveau.ko.zst" <<<"$image_listing" || {
    echo 'FAIL: regenerated initramfs lacks Nouveau' >&2; exit 1;
}
[[ $(cat /sys/module/nouveau/srcversion) == "$EXPECTED_SRCVERSION" &&
   $(parameter_value diag_bar2_map) == "$bar2" ]] || {
    echo 'FAIL: initramfs cleanup changed the running module or parameter' >&2; exit 1;
}
echo 'PASS: future boots will not auto-enable BAR2 tracing; current diag3 remains active.'
