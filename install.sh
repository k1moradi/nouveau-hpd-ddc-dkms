#!/bin/bash
set -Eeuo pipefail
NAME=nouveau-hpd-ddc
VER=0.1.5
SRC_DIR="/usr/src/$NAME-$VER"
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

if [ "$EUID" -ne 0 ]; then
    exec sudo "$0" "$@"
fi

# Clean only temporary trees created during this Nouveau investigation.  This
# is intentionally narrow: it never clears arbitrary /tmp content.
cleanup_tmp() {
    find /tmp -maxdepth 1 -type d \
        \( -name 'nouveau-hpd-ddc.*' -o -name 'nouveau-hpd-test-*' \) \
        -exec rm -rf -- {} + 2>/dev/null || true
}
trap cleanup_tmp EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# Free RAM/swap from any completed/interrupted manual test before starting.
cleanup_tmp

k=$(uname -r)
base="${k%%-*}"

echo "Installing build/runtime prerequisites..."
apt-get update
apt-get install -y \
    dkms build-essential gcc patch python3 binutils bzip2 lbzip2 xz-utils zstd \
    "linux-headers-$k" \
    "linux-source-$base"

# Remove only the temporary diagnostics created during this investigation.
rm -f /etc/modprobe.d/99-nouveau-i2c-test.conf
rm -f /etc/dracut.conf.d/61-nouveau-i2c-test.conf

# Clean up failed/older test revisions before installing this revision.
for oldver in 0.1.0 0.1.1 0.1.2 0.1.3 0.1.4; do
    if dkms status -m "$NAME" -v "$oldver" 2>/dev/null | grep -q .; then
        dkms remove -m "$NAME" -v "$oldver" --all || true
    fi
    rm -rf "/usr/src/$NAME-$oldver"
done

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

# Explicit cleanup here plus EXIT trap: tmpfs RAM is released before returning.
cleanup_tmp

echo
echo "Installed $NAME/$VER for $k."
echo "Preferred module: $(modinfo -n nouveau 2>/dev/null || true)"
echo "Temporary Nouveau build trees in /tmp have been removed."
echo "Reboot, then verify EDID/modes."
