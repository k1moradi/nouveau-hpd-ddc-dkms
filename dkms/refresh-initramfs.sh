#!/bin/bash
set -Eeuo pipefail

usage() {
    echo "Usage: $0 KERNEL_VERSION|all|others" >&2
    exit 2
}

mode="${1:-}"
if [[ $# -ne 1 || -z "$mode" ]]; then
    usage
fi
if [[ "$mode" != all && "$mode" != others && "$mode" == *'/'* ]]; then
    usage
fi

modules_root="${NOUVEAU_MODULES_DIR:-/lib/modules}"
boot_root="${NOUVEAU_BOOT_DIR:-/boot}"
dracut_conf="${NOUVEAU_DRACUT_CONF:-/etc/dracut.conf}"
dracut_conf_dir="${NOUVEAU_DRACUT_CONF_DIR:-/etc/dracut.conf.d}"
initramfs_tools_conf="${NOUVEAU_INITRAMFS_TOOLS_CONFIG:-/etc/initramfs-tools/initramfs.conf}"

tool="${NOUVEAU_INITRAMFS_TOOL:-auto}"
if [[ "$tool" == auto ]]; then
    if command -v dracut >/dev/null 2>&1 && \
        { [[ -e "$dracut_conf" ]] || [[ -d "$dracut_conf_dir" ]]; } && \
        [[ ! -e "$initramfs_tools_conf" ]]; then
        tool=dracut
    elif command -v update-initramfs >/dev/null 2>&1; then
        tool=update-initramfs
    elif command -v dracut >/dev/null 2>&1; then
        tool=dracut
    else
        echo "ERROR: neither dracut nor update-initramfs is available" >&2
        exit 2
    fi
fi

if [[ "$tool" != dracut && "$tool" != update-initramfs ]]; then
    echo "ERROR: unsupported initramfs tool '$tool'" >&2
    exit 2
fi

refresh_kernel() {
    local kernel="$1"
    if [[ ! -d "$modules_root/$kernel" ]]; then
        echo "ERROR: kernel module directory is missing: $modules_root/$kernel" >&2
        return 2
    fi

    if [[ "$tool" == dracut ]]; then
        command -v dracut >/dev/null 2>&1 || {
            echo "ERROR: dracut was selected but is unavailable" >&2
            return 2
        }
        dracut --force "$boot_root/initrd.img-$kernel" "$kernel"
    else
        command -v update-initramfs >/dev/null 2>&1 || {
            echo "ERROR: update-initramfs was selected but is unavailable" >&2
            return 2
        }
        update-initramfs -u -k "$kernel"
    fi
}

if [[ "$mode" != all ]]; then
    if [[ "$mode" != others ]]; then
        refresh_kernel "$mode"
        exit $?
    fi
fi

running_kernel="${NOUVEAU_RUNNING_KERNEL:-$(uname -r)}"
found=0

if [[ "$mode" == all && "$tool" == update-initramfs ]]; then
    update-initramfs -u -k all
    exit $?
fi

for modules_dir in "$modules_root"/*; do
    [[ -d "$modules_dir" ]] || continue
    kernel="${modules_dir##*/}"
    [[ -e "$boot_root/initrd.img-$kernel" ]] || continue
    if [[ "$mode" == others && "$kernel" == "$running_kernel" ]]; then
        continue
    fi
    found=1
    refresh_kernel "$kernel"
done

if [[ "$found" -eq 0 ]]; then
    if [[ "$mode" == others ]]; then
        exit 0
    fi
    echo "ERROR: no installed kernel initrd images were found for $tool refresh" >&2
    exit 2
fi
