#!/bin/bash
set -euo pipefail
NAME=nouveau-hpd-ddc
VER=0.1.0
if [ "$EUID" -ne 0 ]; then
    exec sudo "$0" "$@"
fi

kernels=$(dkms status -m "$NAME" -v "$VER" 2>/dev/null | sed -n 's/.*\/\([^,]*\),.*/\1/p' | sort -u || true)
dkms remove -m "$NAME" -v "$VER" --all || true
rm -rf "/usr/src/$NAME-$VER"

for k in $kernels "$(uname -r)"; do
    [ -d "/lib/modules/$k" ] || continue
    depmod -a "$k" || true
    if command -v update-initramfs >/dev/null 2>&1; then
        update-initramfs -u -k "$k" || true
    fi
done

echo "Removed $NAME/$VER; stock Ubuntu nouveau will be used after reboot."
