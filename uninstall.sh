#!/bin/bash
set -euo pipefail
NAME=nouveau-hpd-ddc
VERSIONS=(0.1.0 0.1.1 0.1.2 0.1.3 0.1.4 0.1.5 0.1.6)
if [ "$EUID" -ne 0 ]; then
    exec sudo "$0" "$@"
fi

for ver in "${VERSIONS[@]}"; do
    if dkms status -m "$NAME" -v "$ver" 2>/dev/null | grep -q .; then
        dkms remove -m "$NAME" -v "$ver" --all
    fi
    rm -rf "/usr/src/$NAME-$ver" "/var/lib/dkms/$NAME/$ver"
done
find /tmp -maxdepth 1 -type d \
    \( -name 'nouveau-hpd-ddc.*' -o -name 'nouveau-hpd-test-*' \) \
    -exec rm -rf -- {} + 2>/dev/null || true

for modules_dir in /lib/modules/*; do
    [ -d "$modules_dir" ] || continue
    depmod -a "${modules_dir##*/}" || true
done
if command -v update-initramfs >/dev/null 2>&1; then
    update-initramfs -u -k all || true
fi

echo "Removed all known $NAME revisions; stock Ubuntu nouveau will be used after reboot."
