#!/bin/bash
set -euo pipefail
NAME=nouveau-hpd-ddc
VER=0.1.0
SRC_DIR="/usr/src/$NAME-$VER"
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

if [ "$EUID" -ne 0 ]; then
    exec sudo "$0" "$@"
fi

k=$(uname -r)
base="${k%%-*}"

echo "Installing build/runtime prerequisites..."
apt-get update
apt-get install -y \
    dkms build-essential patch python3 binutils bzip2 xz-utils zstd \
    "linux-headers-$k" \
    linux-source

# Remove only the temporary diagnostics created during this investigation.
rm -f /etc/modprobe.d/99-nouveau-i2c-test.conf
rm -f /etc/dracut.conf.d/61-nouveau-i2c-test.conf

if dkms status -m "$NAME" -v "$VER" 2>/dev/null | grep -q .; then
    dkms remove -m "$NAME" -v "$VER" --all || true
fi
rm -rf "$SRC_DIR"
mkdir -p "$SRC_DIR"
cp -a "$HERE/dkms/." "$SRC_DIR/"

dkms add -m "$NAME" -v "$VER"
dkms build -m "$NAME" -v "$VER" -k "$k"
dkms install -m "$NAME" -v "$VER" -k "$k"

# POST_INSTALL normally handles this; repeat harmlessly so Dracut definitely
# sees the DKMS override on the portable installation.
depmod -a "$k"
update-initramfs -u -k "$k"

echo
echo "Installed $NAME/$VER for $k."
echo "Preferred module: $(modinfo -n nouveau 2>/dev/null || true)"
echo "Reboot, then verify EDID/modes."
