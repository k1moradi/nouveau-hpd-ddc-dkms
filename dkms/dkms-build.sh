#!/bin/bash
set -euo pipefail

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
work="$PWD/.work/$kernelver"
out="$PWD/output"
rm -rf "$work" "$out"
mkdir -p "$work" "$out"

# Prefer a kernel binary package built from Ubuntu's 'linux' source package.
# linux-modules and linux-headers avoid the +N suffix sometimes used by
# linux-signed image packages.
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

# A signed image may carry a packaging-only +N suffix not present on
# linux-source. Strip only that final +suffix as a fallback candidate.
plain_ver="${desired_ver%%+*}"

echo "nouveau-hpd-ddc: kernel=$kernelver source-package=$srcpkg desired-version=$desired_ver"

find_installed_tarball() {
    local installed=""
    installed=$(dpkg-query -W -f='${Version}' "$srcpkg" 2>/dev/null || true)
    if [ "$installed" = "$desired_ver" ] || [ "$installed" = "$plain_ver" ]; then
        find /usr/src -maxdepth 1 -type f \
            \( -name "$srcpkg.tar.bz2" -o -name "$srcpkg.tar.xz" -o -name "$srcpkg.tar.gz" \) \
            -print -quit
    fi
}

src_tar=$(find_installed_tarball || true)
if [ -z "$src_tar" ]; then
    echo "nouveau-hpd-ddc: exact installed source tarball not found; downloading matching Ubuntu source binary package"
    mkdir -p "$work/download"
    (
        cd "$work/download"
        if ! apt-get download "$srcpkg=$desired_ver"; then
            if [ "$plain_ver" != "$desired_ver" ]; then
                apt-get download "$srcpkg=$plain_ver"
            else
                exit 1
            fi
        fi
    )
    deb=$(find "$work/download" -maxdepth 1 -type f -name "${srcpkg}_*.deb" -print -quit)
    if [ -z "$deb" ]; then
        echo "ERROR: failed to download $srcpkg matching $desired_ver" >&2
        exit 2
    fi
    mkdir -p "$work/pkg"
    dpkg-deb -x "$deb" "$work/pkg"
    src_tar=$(find "$work/pkg/usr/src" -maxdepth 1 -type f -name "$srcpkg.tar.*" -print -quit)
fi

if [ -z "${src_tar:-}" ] || [ ! -f "$src_tar" ]; then
    echo "ERROR: Ubuntu kernel source tarball for $kernelver not found" >&2
    exit 2
fi

echo "nouveau-hpd-ddc: source tarball: $src_tar"
tar -xf "$src_tar" -C "$work"
srcdir=$(find "$work" -mindepth 1 -maxdepth 1 -type d -name 'linux-source-*' -print -quit)
if [ -z "$srcdir" ]; then
    echo "ERROR: could not locate extracted Ubuntu kernel source" >&2
    exit 2
fi

target="$srcdir/drivers/gpu/drm/nouveau/nvkm/engine/disp/outp.c"
if [ ! -f "$target" ]; then
    echo "ERROR: Nouveau source layout changed; refusing an unsafe patch" >&2
    exit 2
fi

# If Ubuntu/upstream already has the intended behavior, do not re-patch it.
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
    if ! patch -d "$srcdir" -p1 --forward --batch < "$PWD/fix-nouveau-hpd-ddc.patch"; then
        echo "ERROR: patch did not apply cleanly. Nouveau changed; refusing to guess." >&2
        exit 2
    fi
fi

# Build only the Nouveau external module against the exact target Ubuntu
# kernel headers. The complete Nouveau source comes from Ubuntu's source
# package, while symbol versions/config come from /lib/modules/$kernelver/build.
nproc_count=$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)
make -C "$kdir" \
    M="$srcdir/drivers/gpu/drm/nouveau" \
    -j"$nproc_count" \
    modules

module="$srcdir/drivers/gpu/drm/nouveau/nouveau.ko"
if [ ! -f "$module" ]; then
    echo "ERROR: build completed without nouveau.ko" >&2
    exit 2
fi

cp -f "$module" "$out/nouveau.ko"

# Sanity: vermagic must name the target kernel release.
vermagic=$(modinfo -F vermagic "$out/nouveau.ko" 2>/dev/null || true)
case "$vermagic" in
    "$kernelver"*) ;;
    *)
        echo "ERROR: built module vermagic '$vermagic' does not match '$kernelver'" >&2
        exit 2
        ;;
esac

echo "nouveau-hpd-ddc: built $out/nouveau.ko"
