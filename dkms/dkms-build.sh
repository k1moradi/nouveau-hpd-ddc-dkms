#!/bin/bash
set -Eeuo pipefail

kernelver="${1:-${kernelver:-}}"
if [ -z "$kernelver" ]; then
    echo "ERROR: DKMS did not supply kernel version" >&2
    exit 2
fi

kdir="/lib/modules/$kernelver/build"
if [ ! -d "$kdir" ]; then
    echo "ERROR: kernel headers/build tree missing: $kdir" >&2
    echo "Install linux-headers-$kernelver and retry DKMS." >&2
    exit 2
fi

config="/boot/config-$kernelver"
if [ -r "$config" ] && ! grep -q '^CONFIG_DRM_NOUVEAU=m$' "$config"; then
    echo "ERROR: $kernelver does not build Nouveau as a module; DKMS cannot override it safely." >&2
    exit 2
fi

base="${kernelver%%-*}"
srcpkg="linux-source-$base"
out="$PWD/output"
rm -rf "$out"
mkdir -p "$out"

# Use tmpfs for the temporary source/object tree when /tmp is tmpfs.  The tree
# is deleted on every normal exit and on HUP/INT/TERM so RAM/swap is released.
tmp_parent="${NOUVEAU_DKMS_TMPDIR:-/tmp}"
mkdir -p "$tmp_parent"
work=$(mktemp -d -p "$tmp_parent" "nouveau-hpd-ddc.${kernelver}.XXXXXX")
cleanup() {
    rc=$?
    rm -rf -- "$work" 2>/dev/null || true
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

desired_ver=""
for p in \
    "linux-modules-$kernelver" \
    "linux-headers-$kernelver" \
    "linux-image-unsigned-$kernelver" \
    "linux-image-$kernelver"; do
    if v=$(dpkg-query -W -f='${Version}' "$p" 2>/dev/null); then
        desired_ver="$v"
        break
    fi
done

if [ -z "$desired_ver" ]; then
    echo "ERROR: cannot determine Ubuntu package version for $kernelver" >&2
    exit 2
fi

plain_ver="${desired_ver%%+*}"
echo "nouveau-hpd-ddc: kernel=$kernelver source-package=$srcpkg desired-version=$desired_ver"
echo "nouveau-hpd-ddc: temporary build tree: $work (auto-cleaned on exit)"

src_tar=""
installed_ver=$(dpkg-query -W -f='${Version}' "$srcpkg" 2>/dev/null || true)
if [ "$installed_ver" = "$desired_ver" ] || [ "$installed_ver" = "$plain_ver" ]; then
    src_tar=$(find /usr/src -maxdepth 3 -type f \
        \( -name "$srcpkg.tar.bz2" -o -name "$srcpkg.tar.xz" -o -name "$srcpkg.tar.gz" -o -name "$srcpkg.tar.zst" \) \
        -print -quit)
fi

# Future ABI fallback: retrieve the exact Ubuntu source binary package without
# installing it globally, extract it into our temporary directory, and remove
# it automatically when the DKMS build exits.
if [ -z "$src_tar" ]; then
    echo "nouveau-hpd-ddc: exact installed source archive not found; downloading $srcpkg=$desired_ver"
    mkdir -p "$work/download" "$work/source-pkg"
    (
        cd "$work/download"
        apt-get download "$srcpkg=$desired_ver"
    )
    deb=$(find "$work/download" -maxdepth 1 -type f -name "${srcpkg}_*.deb" -print -quit)
    if [ -z "${deb:-}" ]; then
        echo "ERROR: apt downloaded no $srcpkg package for $desired_ver" >&2
        exit 2
    fi
    dpkg-deb -x "$deb" "$work/source-pkg"
    src_tar=$(find "$work/source-pkg/usr/src" -maxdepth 3 -type f \
        \( -name "$srcpkg.tar.bz2" -o -name "$srcpkg.tar.xz" -o -name "$srcpkg.tar.gz" -o -name "$srcpkg.tar.zst" \) \
        -print -quit)
fi

if [ -z "${src_tar:-}" ] || [ ! -f "$src_tar" ]; then
    echo "ERROR: Ubuntu kernel source archive for $kernelver not found" >&2
    exit 2
fi

echo "nouveau-hpd-ddc: source archive: $src_tar"
echo "nouveau-hpd-ddc: extracting only drivers/gpu/drm/nouveau"

subtree="$srcpkg/drivers/gpu/drm/nouveau"
case "$src_tar" in
    *.tar.bz2)
        if command -v lbzip2 >/dev/null 2>&1; then
            lbzip2 -dc "$src_tar" | tar -x -C "$work" "$subtree"
        else
            bzip2 -dc "$src_tar" | tar -x -C "$work" "$subtree"
        fi
        ;;
    *.tar.xz)  xz -dc "$src_tar"   | tar -x -C "$work" "$subtree" ;;
    *.tar.gz)  gzip -dc "$src_tar" | tar -x -C "$work" "$subtree" ;;
    *.tar.zst) zstd -dc "$src_tar" | tar -x -C "$work" "$subtree" ;;
    *)
        echo "ERROR: unsupported source archive format: $src_tar" >&2
        exit 2
        ;;
esac

srcdir="$work/$srcpkg"
target="$srcdir/drivers/gpu/drm/nouveau/nvkm/engine/disp/outp.c"
if [ ! -f "$target" ]; then
    echo "ERROR: Nouveau source layout changed; refusing an unsafe patch" >&2
    exit 2
fi

if python3 - "$target" <<'PY'
import pathlib, re, sys
p = pathlib.Path(sys.argv[1])
s = p.read_text()
pat = re.compile(r"if \(outp->info\.type == DCB_OUTPUT_DP\)\s*\n\s*return 0;\s*\n\s*return -EINVAL;", re.M)
sys.exit(0 if pat.search(s) else 1)
PY
then
    echo "nouveau-hpd-ddc: source already contains the non-DP UNKNOWN fallback; no patch needed"
else
    echo "nouveau-hpd-ddc: applying HPD-low/DDC fallback patch"
    if ! patch -d "$srcdir" -p1 --forward --batch < "$PWD/patches/hpd-low-ddc-probe.patch"; then
        echo "ERROR: patch did not apply cleanly. Nouveau changed; refusing to guess." >&2
        exit 2
    fi
fi

# Optional read-only PNVIO input-buffer snapshot for the K4200 DDC diagnosis.
# The installer creates this marker only when invoked with --diag-ibuf.
if [ -f "$PWD/diagnostic-ibuf.enabled" ]; then
    echo "nouveau-hpd-ddc: applying read-only IBUF state diagnostic"
    if ! patch -d "$srcdir" -p1 --forward --batch < "$PWD/patches/diagnostic/ibuf-state-snapshot.patch"; then
        echo "ERROR: IBUF diagnostic patch did not apply cleanly; refusing to guess." >&2
        exit 2
    fi
else
    echo "nouveau-hpd-ddc: IBUF state diagnostic is disabled"
fi

# Optional probe of the output's VBIOS-selected DDC bus while analog DAC power
# is active.  This marker is created only by install.sh --diag-dac-ddc.
if [ -f "$PWD/diagnostic-dac-ddc.enabled" ]; then
    echo "nouveau-hpd-ddc: applying DAC-powered DDC diagnostic"
    if ! patch -d "$srcdir" -p1 --forward --batch < "$PWD/patches/diagnostic/dac-powered-ddc-probe.patch"; then
        echo "ERROR: DAC-powered DDC diagnostic patch did not apply cleanly; refusing to guess." >&2
        exit 2
    fi
else
    echo "nouveau-hpd-ddc: DAC-powered DDC diagnostic is disabled"
fi

# Optional ACK-slot sampler for physical GF119 PNVIO port 0. The installer
# also enables the DAC-powered probe so each diagnostic boot makes known 0x50
# address transactions. This patch adds only MMIO reads at the ACK decision.
if [ -f "$PWD/diagnostic-ack-slot.enabled" ]; then
    echo "nouveau-hpd-ddc: applying read-only ACK-slot sampler"
    if ! patch -d "$srcdir" -p1 --forward --batch < "$PWD/patches/diagnostic/ack-slot-sampler.patch"; then
        echo "ERROR: ACK-slot sampler patch did not apply cleanly; refusing to guess." >&2
        exit 2
    fi
else
    echo "nouveau-hpd-ddc: ACK-slot sampler is disabled"
fi

# Optional read-only sample of each normal bus-0 line-drive state during an
# existing I2C address-0x50 transfer.  This flag also enables the ACK sampler
# and internal bit-bang path, but does not add a DAC-powered test transfer.
if [ -f "$PWD/diagnostic-d014-sense.enabled" ]; then
    if [ ! -f "$PWD/diagnostic-ibuf.enabled" ]; then
        echo "nouveau-hpd-ddc: applying read-only D014 init snapshot"
        if ! patch -d "$srcdir" -p1 --forward --batch < "$PWD/patches/diagnostic/d014-init-snapshot.patch"; then
            echo "ERROR: D014 init snapshot patch did not apply cleanly; refusing to guess." >&2
            exit 2
        fi
    fi
    echo "nouveau-hpd-ddc: applying bounded read-only PNVIO D014 drive/sense sampler"
    if ! patch -d "$srcdir" -p1 --forward --batch < "$PWD/patches/diagnostic/pnvio-d014-sense-matrix.patch"; then
        echo "ERROR: PNVIO D014 sense matrix patch did not apply cleanly; refusing to guess." >&2
        exit 2
    fi
else
    echo "nouveau-hpd-ddc: PNVIO D014 sense matrix is disabled"
fi

# Optional read-only snapshot of firmware-transferred EDID for the primary
# display GPU. This diagnostic logs only the retained base block and does not
# attach it to the connector or change mode detection.
if [ -f "$PWD/diagnostic-firmware-edid.enabled" ]; then
    echo "nouveau-hpd-ddc: applying firmware EDID snapshot diagnostic"
    if ! patch -d "$srcdir" -p1 --forward --batch < "$PWD/patches/diagnostic/firmware-edid-snapshot.patch"; then
        echo "ERROR: firmware EDID diagnostic patch did not apply cleanly; refusing to guess." >&2
        exit 2
    fi
else
    echo "nouveau-hpd-ddc: firmware EDID snapshot diagnostic is disabled"
fi

# Force the GNU C compiler even on systems where Clang is the interactive
# default.  Remove inherited LLVM/Kbuild tool-selection variables first so a
# shell/profile setting such as LLVM=1 cannot silently switch this DKMS build
# back to Clang.  NOUVEAU_DKMS_CC remains an explicit expert override.
cc="${NOUVEAU_DKMS_CC:-gcc}"
if ! command -v "$cc" >/dev/null 2>&1; then
    echo "ERROR: requested C compiler '$cc' is not installed" >&2
    exit 2
fi
cc_path=$(command -v "$cc")
cc_version=$($cc --version 2>/dev/null | head -n 1 || true)
echo "nouveau-hpd-ddc: compiler: $cc_path${cc_version:+ ($cc_version)}"

# Use every online logical CPU by default.  This is what matters for make -j
# throughput (rather than physical-core count).  An explicit positive integer
# NOUVEAU_DKMS_JOBS overrides autodetection when a low-memory system needs it.
cpus=$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)
case "$cpus" in
    ''|*[!0-9]*|0) cpus=1 ;;
esac

if [ -n "${NOUVEAU_DKMS_JOBS:-}" ]; then
    jobs="$NOUVEAU_DKMS_JOBS"
    case "$jobs" in
        ''|*[!0-9]*|0)
            echo "ERROR: NOUVEAU_DKMS_JOBS must be a positive integer (got '$jobs')" >&2
            exit 2
            ;;
    esac
else
    jobs="$cpus"
fi

echo "nouveau-hpd-ddc: online logical CPUs: $cpus; make jobs: $jobs"
echo "nouveau-hpd-ddc: building only Nouveau against $kdir with GNU GCC"
internal_i2c_cflags=()
if [ -f "$PWD/diagnostic-ack-slot.enabled" ]; then
    # The Ubuntu headers leave Nouveau's existing internal bit-bang transfer
    # implementation behind this compile-time guard. Enable it only for the
    # ACK-slot diagnostic; NvI2C=1 selects it at runtime for that boot.
    internal_i2c_cflags+=("KCFLAGS=-DCONFIG_NOUVEAU_I2C_INTERNAL")
    echo "nouveau-hpd-ddc: compiling internal I2C transfer support for ACK-slot diagnostic"
fi
env -u LLVM -u LLVM_IAS -u CC -u HOSTCC \
    make -C "$kdir" \
        M="$srcdir/drivers/gpu/drm/nouveau" \
        CC="$cc_path" \
        HOSTCC="$cc_path" \
        "${internal_i2c_cflags[@]}" \
        -j"$jobs" \
        modules

module="$srcdir/drivers/gpu/drm/nouveau/nouveau.ko"
if [ ! -f "$module" ]; then
    echo "ERROR: build completed without $module" >&2
    exit 2
fi

cp -f "$module" "$out/nouveau.ko"
vermagic=$(modinfo -F vermagic "$out/nouveau.ko" 2>/dev/null || true)
case "$vermagic" in
    "$kernelver"*) ;;
    *)
        echo "ERROR: built module vermagic '$vermagic' does not match '$kernelver'" >&2
        exit 2
        ;;
esac

echo "nouveau-hpd-ddc: built $out/nouveau.ko"
echo "nouveau-hpd-ddc: temporary tree will now be removed from $tmp_parent"
