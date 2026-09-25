#!/bin/bash
set -Eeuo pipefail
NAME=nouveau-hpd-ddc
VER=0.1.6
SRC_DIR="/usr/src/$NAME-$VER"
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
diag_ibuf=0
diag_dac_ddc=0

for arg in "$@"; do
    case "$arg" in
        --diag-ibuf) diag_ibuf=1 ;;
        --diag-dac-ddc) diag_dac_ddc=1 ;;
        *)
            echo "Usage: $0 [--diag-ibuf] [--diag-dac-ddc]" >&2
            exit 2
            ;;
    esac
done

if [ "$EUID" -ne 0 ]; then
    exec sudo "$0" "$@"
fi

for source_file in \
    dkms/dkms.conf \
    dkms/dkms-build.sh \
    patches/hpd-low-ddc-probe.patch \
    patches/diagnostic/ibuf-state-snapshot.patch \
    patches/diagnostic/dac-powered-ddc-probe.patch; do
    if [ ! -f "$HERE/$source_file" ]; then
        echo "ERROR: canonical project source is missing: $HERE/$source_file" >&2
        exit 2
    fi
done

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
for oldver in 0.1.0 0.1.1 0.1.2 0.1.3 0.1.4 0.1.5; do
    if dkms status -m "$NAME" -v "$oldver" 2>/dev/null | grep -q .; then
        echo "Removing older DKMS revision $NAME/$oldver"
        dkms remove -m "$NAME" -v "$oldver" --all
    fi
    rm -rf "/usr/src/$NAME-$oldver"
    rm -rf "/var/lib/dkms/$NAME/$oldver"
done

for oldver in 0.1.0 0.1.1 0.1.2 0.1.3 0.1.4 0.1.5; do
    if dkms status -m "$NAME" -v "$oldver" 2>/dev/null | grep -q . \
        || [ -e "/usr/src/$NAME-$oldver" ] \
        || [ -e "/var/lib/dkms/$NAME/$oldver" ]; then
        echo "ERROR: older revision $NAME/$oldver remains after cleanup" >&2
        exit 2
    fi
done

if dkms status -m "$NAME" -v "$VER" 2>/dev/null | grep -q .; then
    echo "Removing existing DKMS revision $NAME/$VER before reinstall"
    dkms remove -m "$NAME" -v "$VER" --all
fi
rm -rf "/var/lib/dkms/$NAME/$VER"
rm -rf "$SRC_DIR"
mkdir -p "$SRC_DIR"
cp -a "$HERE/dkms/." "$SRC_DIR/"
mkdir -p "$SRC_DIR/patches/diagnostic"
cp -a "$HERE/patches/hpd-low-ddc-probe.patch" "$SRC_DIR/patches/"
cp -a "$HERE/patches/diagnostic/ibuf-state-snapshot.patch" "$SRC_DIR/patches/diagnostic/"
cp -a "$HERE/patches/diagnostic/dac-powered-ddc-probe.patch" "$SRC_DIR/patches/diagnostic/"
if [ "$diag_ibuf" -eq 1 ]; then
    touch "$SRC_DIR/diagnostic-ibuf.enabled"
    echo "Enabling read-only IBUF state diagnostics for this DKMS build."
fi
if [ "$diag_dac_ddc" -eq 1 ]; then
    touch "$SRC_DIR/diagnostic-dac-ddc.enabled"
    echo "Enabling DAC-powered DDC diagnostics for this DKMS build."
fi

dkms add -m "$NAME" -v "$VER"
dkms build -m "$NAME" -v "$VER" -k "$k"
dkms install -m "$NAME" -v "$VER" -k "$k"

# Refresh every installed kernel so stale copies of earlier DKMS revisions are
# removed from old initramfs images as well as the running kernel's image.
for modules_dir in /lib/modules/*; do
    [ -d "$modules_dir" ] || continue
    depmod -a "${modules_dir##*/}"
done
update-initramfs -u -k all

# Explicit cleanup here plus EXIT trap: tmpfs RAM is released before returning.
cleanup_tmp

echo
echo "Installed $NAME/$VER for $k."
echo "Preferred module: $(modinfo -n nouveau 2>/dev/null || true)"
echo "Temporary Nouveau build trees in /tmp have been removed."
echo "Reboot, then verify EDID/modes."
