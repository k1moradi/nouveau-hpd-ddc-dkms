#!/bin/bash
set -Eeuo pipefail
NAME=nouveau-hpd-ddc
VER=0.1.7
SRC_DIR="/usr/src/$NAME-$VER"
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
diag_ibuf=0
diag_dac_ddc=0
diag_ack_slot=0
diag_firmware_edid=0
diag_d014_sense=0
diag_pnvio_hw_ddc=0

for arg in "$@"; do
    case "$arg" in
        --diag-ibuf) diag_ibuf=1 ;;
        --diag-dac-ddc) diag_dac_ddc=1 ;;
        --diag-ack-slot) diag_ack_slot=1; diag_dac_ddc=1 ;;
        --diag-firmware-edid) diag_firmware_edid=1 ;;
        --diag-d014-sense) diag_d014_sense=1; diag_ack_slot=1 ;;
        --diag-pnvio-hw-ddc) diag_pnvio_hw_ddc=1 ;;
        *)
            echo "Usage: $0 [--diag-ibuf] [--diag-dac-ddc] [--diag-ack-slot] [--diag-firmware-edid] [--diag-d014-sense] [--diag-pnvio-hw-ddc]" >&2
            exit 2
            ;;
    esac
done

if [ "$diag_pnvio_hw_ddc" -eq 1 ] &&
   [ "$((diag_ibuf + diag_dac_ddc + diag_ack_slot + diag_firmware_edid + diag_d014_sense))" -ne 0 ]; then
    echo "ERROR: --diag-pnvio-hw-ddc must run alone so the GOP-backed transfer has an unambiguous result." >&2
    exit 2
fi

if [ "$EUID" -ne 0 ]; then
    exec sudo "$0" "$@"
fi

for source_file in \
    dkms/dkms.conf \
    dkms/dkms-build.sh \
    patches/hpd-low-ddc-probe.patch \
    patches/diagnostic/ibuf-state-snapshot.patch \
    patches/diagnostic/dac-powered-ddc-probe.patch \
    patches/diagnostic/ack-slot-sampler.patch \
    patches/diagnostic/d014-init-snapshot.patch \
    patches/diagnostic/pnvio-d014-sense-matrix.patch \
    patches/diagnostic/firmware-edid-snapshot.patch \
    patches/diagnostic/gk104-pnvio-hw-ddc.patch \
    docs/GK104-PNVIO-HW-I2C-TRANSCRIPT.md; do
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

# A normal install clears the temporary internal-I2C module option. The
# diagnostic option is staged again only after its DKMS build succeeds.
rm -f /etc/modprobe.d/99-nouveau-i2c-test.conf
rm -f /etc/dracut.conf.d/61-nouveau-i2c-test.conf

k=$(uname -r)
base="${k%%-*}"

echo "Installing build/runtime prerequisites..."
apt-get update
apt-get install -y \
    dkms build-essential gcc patch python3 binutils bzip2 lbzip2 xz-utils zstd \
    "linux-headers-$k" \
    "linux-source-$base"

# Clean up failed/older test revisions before installing this revision.
for oldver in 0.1.0 0.1.1 0.1.2 0.1.3 0.1.4 0.1.5 0.1.6; do
    if dkms status -m "$NAME" -v "$oldver" 2>/dev/null | grep -q .; then
        echo "Removing older DKMS revision $NAME/$oldver"
        dkms remove -m "$NAME" -v "$oldver" --all
    fi
    rm -rf "/usr/src/$NAME-$oldver"
    rm -rf "/var/lib/dkms/$NAME/$oldver"
done

for oldver in 0.1.0 0.1.1 0.1.2 0.1.3 0.1.4 0.1.5 0.1.6; do
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
cp -a "$HERE/patches/diagnostic/ack-slot-sampler.patch" "$SRC_DIR/patches/diagnostic/"
cp -a "$HERE/patches/diagnostic/d014-init-snapshot.patch" "$SRC_DIR/patches/diagnostic/"
cp -a "$HERE/patches/diagnostic/pnvio-d014-sense-matrix.patch" "$SRC_DIR/patches/diagnostic/"
cp -a "$HERE/patches/diagnostic/firmware-edid-snapshot.patch" "$SRC_DIR/patches/diagnostic/"
cp -a "$HERE/patches/diagnostic/gk104-pnvio-hw-ddc.patch" "$SRC_DIR/patches/diagnostic/"
mkdir -p "$SRC_DIR/docs"
cp -a "$HERE/docs/GK104-PNVIO-HW-I2C-TRANSCRIPT.md" "$SRC_DIR/docs/"
if [ "$diag_ibuf" -eq 1 ]; then
    touch "$SRC_DIR/diagnostic-ibuf.enabled"
    echo "Enabling read-only IBUF state diagnostics for this DKMS build."
fi
if [ "$diag_dac_ddc" -eq 1 ]; then
    touch "$SRC_DIR/diagnostic-dac-ddc.enabled"
    echo "Enabling DAC-powered DDC diagnostics for this DKMS build."
fi
if [ "$diag_ack_slot" -eq 1 ]; then
    touch "$SRC_DIR/diagnostic-ack-slot.enabled"
    echo "Enabling read-only DDC ACK-slot sampling for physical PNVIO port 0."
fi
if [ "$diag_d014_sense" -eq 1 ]; then
    touch "$SRC_DIR/diagnostic-d014-sense.enabled"
    echo "Enabling bounded read-only D014 drive/sense sampling during normal 0x50 transfers."
fi
if [ "$diag_firmware_edid" -eq 1 ]; then
    touch "$SRC_DIR/diagnostic-firmware-edid.enabled"
    echo "Enabling read-only firmware EDID snapshot diagnostics for DVI-I."
fi
if [ "$diag_pnvio_hw_ddc" -eq 1 ]; then
    touch "$SRC_DIR/diagnostic-pnvio-hw-ddc.enabled"
    echo "Enabling the GK104 PNVIO hardware DDC diagnostic."
fi

dkms add -m "$NAME" -v "$VER"
dkms build -m "$NAME" -v "$VER" -k "$k"
dkms install -m "$NAME" -v "$VER" -k "$k"

if [ "$diag_pnvio_hw_ddc" -eq 1 ]; then
    install -d -m 0755 /etc/modprobe.d
    printf '%s\n' 'options nouveau config=NvI2CHw=1' \
        > /etc/modprobe.d/99-nouveau-i2c-test.conf
    chmod 0644 /etc/modprobe.d/99-nouveau-i2c-test.conf
    install -d -m 0755 /etc/dracut.conf.d
    printf '%s\n' 'install_items+=" /etc/modprobe.d/99-nouveau-i2c-test.conf "' \
        > /etc/dracut.conf.d/61-nouveau-i2c-test.conf
    chmod 0644 /etc/dracut.conf.d/61-nouveau-i2c-test.conf
    echo "Staged config=NvI2CHw=1 and included it in the diagnostic initramfs."
fi

if [ "$diag_ack_slot" -eq 1 ]; then
    install -d -m 0755 /etc/modprobe.d
    printf '%s\n' 'options nouveau config=NvI2C=1' \
        > /etc/modprobe.d/99-nouveau-i2c-test.conf
    chmod 0644 /etc/modprobe.d/99-nouveau-i2c-test.conf
    install -d -m 0755 /etc/dracut.conf.d
    printf '%s\n' 'install_items+=" /etc/modprobe.d/99-nouveau-i2c-test.conf "' \
        > /etc/dracut.conf.d/61-nouveau-i2c-test.conf
    chmod 0644 /etc/dracut.conf.d/61-nouveau-i2c-test.conf
    echo "Staged config=NvI2C=1 and included it in the diagnostic initramfs."
fi

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
if [ "$diag_pnvio_hw_ddc" -eq 1 ]; then
    echo "Keep the monitor connected for one diagnostic reboot, then run ./verify-after-reboot.sh."
    echo "This stages NvI2CHw=1 only; it does not force NvI2C=1."
    echo "After saving the result, run install.sh without diagnostic flags to remove the temporary option."
elif [ "$diag_ack_slot" -eq 1 ]; then
    echo "Keep the monitor connected for the diagnostic reboot, then run ./verify-after-reboot.sh."
    echo "After saving the samples, run install.sh without diagnostic flags to remove NvI2C=1."
else
    echo "Reboot, then verify EDID/modes."
fi
if [ "$diag_firmware_edid" -eq 1 ]; then
    echo "The verifier will also show the read-only firmware EDID base-block snapshot."
fi
if [ "$diag_d014_sense" -eq 1 ]; then
    echo "The matrix samples only address-0x50 traffic already requested by Nouveau; it adds no DDC transaction."
fi
