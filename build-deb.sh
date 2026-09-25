#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
NAME="nouveau-hpd-ddc"
VER="$(sed -n 's/^Version: //p' "$ROOT/debian/DEBIAN/control")"
STAGE="$ROOT/build/deb-root"
SRC="$STAGE/usr/src/$NAME-$VER"
OUT="$ROOT/build/${NAME}-dkms_${VER}_all.deb"

if [[ -z "$VER" || "$(sed -n 's/^PACKAGE_VERSION="\([^"]*\)"/\1/p' "$ROOT/dkms/dkms.conf")" != "$VER" ]]; then
    echo "ERROR: Debian and DKMS package versions do not match." >&2
    exit 2
fi

rm -rf -- "$STAGE"
mkdir -p "$SRC/patches/diagnostic" "$STAGE/DEBIAN"
cp -a "$ROOT/dkms/." "$SRC/"
cp -a "$ROOT/patches/hpd-low-ddc-probe.patch" "$SRC/patches/"
cp -a "$ROOT/patches/diagnostic/ibuf-state-snapshot.patch" "$SRC/patches/diagnostic/"
cp -a "$ROOT/patches/diagnostic/dac-powered-ddc-probe.patch" "$SRC/patches/diagnostic/"
cp -a "$ROOT/patches/diagnostic/ack-slot-sampler.patch" "$SRC/patches/diagnostic/"
cp -a "$ROOT/debian/DEBIAN/." "$STAGE/DEBIAN/"

chmod 0755 \
    "$STAGE/DEBIAN/postinst" \
    "$STAGE/DEBIAN/prerm" \
    "$SRC/dkms-build.sh" \
    "$SRC/dkms-clean.sh" \
    "$SRC/dkms-post-install.sh" \
    "$SRC/dkms-post-remove.sh"

dpkg-deb --build "$STAGE" "$OUT"
echo "Built $OUT"
