#!/bin/bash
set -euo pipefail
NAME=nouveau-hpd-ddc
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VERSIONS=(0.1.0 0.1.1 0.1.2 0.1.3 0.1.4 0.1.5 0.1.6 0.1.13 0.1.14)
if [ "$EUID" -ne 0 ]; then
    exec sudo "$0" "$@"
fi

rm -f /etc/modprobe.d/99-nouveau-i2c-test.conf
rm -f /etc/dracut.conf.d/61-nouveau-i2c-test.conf

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
if [ -x "$ROOT/dkms/refresh-initramfs.sh" ]; then
    "$ROOT/dkms/refresh-initramfs.sh" all || true
elif command -v update-initramfs >/dev/null 2>&1; then
    update-initramfs -u -k all || true
elif command -v dracut >/dev/null 2>&1; then
    for modules_dir in /lib/modules/*; do
        [ -d "$modules_dir" ] || continue
        kernel="${modules_dir##*/}"
        [ -e "/boot/initrd.img-$kernel" ] || continue
        dracut -f "/boot/initrd.img-$kernel" "$kernel" || true
    done
fi

echo "Removed all known $NAME revisions; stock Ubuntu nouveau will be used after reboot."
