#!/bin/bash
set -euo pipefail
kernelver="${1:-${kernelver:-}}"
[ -n "$kernelver" ] || exit 0
if command -v depmod >/dev/null 2>&1; then
    depmod -a "$kernelver" || true
fi
if command -v update-initramfs >/dev/null 2>&1; then
    update-initramfs -u -k "$kernelver" || true
elif command -v dracut >/dev/null 2>&1; then
    dracut -f "/boot/initrd.img-$kernelver" "$kernelver" || true
fi
