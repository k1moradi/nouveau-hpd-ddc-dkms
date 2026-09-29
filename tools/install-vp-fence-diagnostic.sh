#!/usr/bin/env bash
set -Eeuo pipefail

NAME=nouveau-hpd-ddc
BASE_VERSION=0.1.13
DIAG_VERSION=0.1.13-diag1
EXPECTED_KERNEL=7.0.0-34-generic
EXPECTED_BASE_SRCVERSION=57AE1B168D50DB546CD87A1
EXPECTED_DIAG_SRCVERSION=B19B8AAE48467545E652509
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
SCRIPT_PATH="$HERE/tools/install-vp-fence-diagnostic.sh"

if [[ $EUID -ne 0 ]]; then
    exec sudo -- "$SCRIPT_PATH" "$@"
fi
if (($# != 0)); then
    echo "Usage: $0" >&2
    exit 2
fi

kernel=$(uname -r)
if [[ $kernel != "$EXPECTED_KERNEL" ]]; then
    echo "ERROR: diagnostic was prepared for $EXPECTED_KERNEL; running kernel is $kernel" >&2
    exit 2
fi
if [[ $(cat /sys/module/nouveau/srcversion 2>/dev/null || true) != "$EXPECTED_BASE_SRCVERSION" ]]; then
    echo "ERROR: loaded Nouveau is not the expected untouched $BASE_VERSION baseline" >&2
    exit 2
fi
if [[ $(modinfo -F srcversion nouveau 2>/dev/null || true) != "$EXPECTED_BASE_SRCVERSION" ]]; then
    echo "ERROR: on-disk Nouveau is not the expected $BASE_VERSION baseline" >&2
    exit 2
fi

for command_name in dkms gcc patch python3 modinfo strings zstd depmod update-initramfs; do
    command -v "$command_name" >/dev/null 2>&1 || {
        echo "ERROR: required command is missing: $command_name" >&2
        exit 2
    }
done
[[ -d "/lib/modules/$kernel/build" ]] || {
    echo "ERROR: kernel headers are missing for $kernel" >&2
    exit 2
}
[[ -f /usr/src/linux-source-7.0.0/linux-source-7.0.0.tar.bz2 || \
   -f /usr/src/linux-source-7.0.0/linux-source-7.0.0.tar.xz || \
   -f /usr/src/linux-source-7.0.0/linux-source-7.0.0.tar.zst ]] || {
    echo "ERROR: exact Ubuntu Linux 7.0 source archive is missing" >&2
    exit 2
}

SRC_DIR="/usr/src/$NAME-$DIAG_VERSION"
DKMS_VERSION_DIR="/var/lib/dkms/$NAME/$DIAG_VERSION"
if [[ -e $SRC_DIR || -e $DKMS_VERSION_DIR ]]; then
    echo "ERROR: $DIAG_VERSION already exists; inspect it before retrying" >&2
    exit 2
fi
if ! dkms status -m "$NAME" -v "$BASE_VERSION" -k "$kernel" | grep -q 'installed'; then
    echo "ERROR: preserved baseline $NAME/$BASE_VERSION is not installed for $kernel" >&2
    exit 2
fi

for required in \
    dkms/dkms.conf \
    dkms/dkms-build.sh \
    dkms/dkms-clean.sh \
    dkms/dkms-post-install.sh \
    dkms/dkms-post-remove.sh \
    dkms/nonstall-state.sh \
    dkms/check-gk104-video-context.py \
    dkms/check-legacy-fifo-nonstall.py \
    patches/hpd-low-ddc-probe.patch \
    patches/video/gk104-legacy-video-context-nonpriv.patch \
    patches/video/legacy-fifo-nonstall-event-index.patch \
    patches/diagnostic/gk104-vp-idle-fence-ctxsw-trace.patch; do
    [[ -f $HERE/$required ]] || {
        echo "ERROR: required source is missing: $HERE/$required" >&2
        exit 2
    }
done

user_name=${SUDO_USER:-root}
user_record=$(getent passwd "$user_name") || {
    echo "ERROR: cannot resolve invoking user $user_name" >&2
    exit 2
}
IFS=: read -r _ _ user_uid user_gid _ user_home _ <<<"$user_record"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
evidence_dir="$user_home/.cache/nouveau-vp-idle-fence-diag-install-$stamp"
install -d -o "$user_uid" -g "$user_gid" -m 0755 "$evidence_dir"

installed_module=$(modinfo -n nouveau)
old_built_module="/var/lib/dkms/$NAME/$BASE_VERSION/$kernel/$(uname -m)/module/nouveau.ko.zst"
[[ -f $installed_module && -f $old_built_module ]] || {
    echo "ERROR: cannot locate installed module or preserved $BASE_VERSION DKMS build" >&2
    exit 2
}
cp -p "$installed_module" "$evidence_dir/preinstall-nouveau.ko.zst"
cp -p "$old_built_module" "$evidence_dir/nouveau-0.1.13.ko.zst"
chown "$user_uid:$user_gid" "$evidence_dir"/*.ko.zst

{
    printf 'repo_head=%s\n' "$(git -C "$HERE" rev-parse HEAD)"
    printf 'kernel=%s\n' "$kernel"
    printf 'base_version=%s\n' "$BASE_VERSION"
    printf 'diagnostic_version=%s\n' "$DIAG_VERSION"
    printf 'baseline_loaded_srcversion=%s\n' "$EXPECTED_BASE_SRCVERSION"
    printf 'preinstall_module_path=%s\n' "$installed_module"
    printf 'preinstall_module_srcversion=%s\n' "$(modinfo -F srcversion "$installed_module")"
    printf 'preinstall_module_sha256=%s\n' "$(sha256sum "$installed_module" | awk '{print $1}')"
    printf 'preserved_0.1.13_module_sha256=%s\n' "$(sha256sum "$old_built_module" | awk '{print $1}')"
    printf 'diagnostic_patch_sha256=%s\n' "$(sha256sum "$HERE/patches/diagnostic/gk104-vp-idle-fence-ctxsw-trace.patch" | awk '{print $1}')"
} > "$evidence_dir/install-metadata.txt"
chown "$user_uid:$user_gid" "$evidence_dir/install-metadata.txt"

mkdir -p "$SRC_DIR/patches/video" "$SRC_DIR/patches/diagnostic"
cp -a "$HERE/dkms/." "$SRC_DIR/"
cp -a "$HERE/patches/hpd-low-ddc-probe.patch" "$SRC_DIR/patches/"
cp -a "$HERE/patches/video/gk104-legacy-video-context-nonpriv.patch" "$SRC_DIR/patches/video/"
cp -a "$HERE/patches/video/legacy-fifo-nonstall-event-index.patch" "$SRC_DIR/patches/video/"
cp -a "$HERE/patches/diagnostic/gk104-vp-idle-fence-ctxsw-trace.patch" "$SRC_DIR/patches/diagnostic/"
touch "$SRC_DIR/diagnostic-vp-fence.enabled"
# Preserve both already-installed video fixes. The active .13 build includes
# the legacy nonstall patch, so the diagnostic must carry its marker too.
touch "$SRC_DIR/experimental-legacy-nonstall.enabled"

version_count=$(grep -Fc 'PACKAGE_VERSION="0.1.13"' "$SRC_DIR/dkms.conf")
if [[ $version_count -ne 1 ]]; then
    echo "ERROR: copied dkms.conf does not contain exactly one baseline version" >&2
    exit 2
fi
sed -i 's/^PACKAGE_VERSION="0\.1\.13"$/PACKAGE_VERSION="0.1.13-diag1"/' "$SRC_DIR/dkms.conf"
if ! grep -Fxq "PACKAGE_VERSION=\"$DIAG_VERSION\"" "$SRC_DIR/dkms.conf"; then
    echo "ERROR: diagnostic DKMS version substitution failed" >&2
    exit 2
fi

echo "Adding isolated DKMS source $NAME/$DIAG_VERSION"
dkms add -m "$NAME" -v "$DIAG_VERSION"
jobs=${NOUVEAU_DKMS_JOBS:-2}
case $jobs in
    ''|*[!0-9]*|0)
        echo "ERROR: NOUVEAU_DKMS_JOBS must be a positive integer" >&2
        exit 2
        ;;
esac
echo "Building $NAME/$DIAG_VERSION for $kernel with $jobs jobs"
NOUVEAU_DKMS_JOBS="$jobs" dkms build -m "$NAME" -v "$DIAG_VERSION" -k "$kernel"

arch=$(uname -m)
if [[ $arch == x86_64 ]]; then
    dkms_arch=x86_64
else
    dkms_arch=$arch
fi
build_module="/var/lib/dkms/$NAME/$DIAG_VERSION/$kernel/$dkms_arch/module/nouveau.ko.zst"
build_log="/var/lib/dkms/$NAME/$DIAG_VERSION/$kernel/$dkms_arch/log/make.log"
[[ -f $build_module && -f $build_log ]] || {
    echo "ERROR: DKMS build did not leave the expected module and log" >&2
    exit 2
}

vermagic=$(modinfo -F vermagic "$build_module")
srcversion=$(modinfo -F srcversion "$build_module")
params=$(modinfo -p "$build_module")
[[ $vermagic == "$kernel "* ]] || {
    echo "ERROR: diagnostic module vermagic does not match $kernel: $vermagic" >&2
    exit 2
}
[[ $srcversion == "$EXPECTED_DIAG_SRCVERSION" ]] || {
    echo "ERROR: diagnostic module srcversion mismatch: expected $EXPECTED_DIAG_SRCVERSION, found $srcversion" >&2
    exit 2
}
grep -q '^diag_fence_wait:' <<<"$params" || {
    echo "ERROR: diag_fence_wait module parameter is missing" >&2
    exit 2
}
grep -q '^diag_ctxsw:' <<<"$params" || {
    echo "ERROR: diag_ctxsw module parameter is missing" >&2
    exit 2
}
strings_file="$evidence_dir/nouveau-module.strings"
zstd -dc "$build_module" | strings -a > "$strings_file"
if ! grep -Fq 'NOUVEAU_DIAG_IDLE_FENCE' "$strings_file"; then
    echo "ERROR: idle-fence diagnostic marker is missing from the built module" >&2
    exit 2
fi
if ! grep -Fq 'NOUVEAU_DIAG_CTXSW' "$strings_file"; then
    echo "ERROR: CTXSW diagnostic marker is missing from the built module" >&2
    exit 2
fi
chown "$user_uid:$user_gid" "$strings_file"

cp -p "$build_module" "$evidence_dir/nouveau-diag1.ko.zst"
cp -p "$build_log" "$evidence_dir/dkms-make.log"
chown "$user_uid:$user_gid" "$evidence_dir/nouveau-diag1.ko.zst" "$evidence_dir/dkms-make.log"
{
    printf 'built_module_path=%s\n' "$build_module"
    printf 'built_module_sha256=%s\n' "$(sha256sum "$build_module" | awk '{print $1}')"
    printf 'build_log_sha256=%s\n' "$(sha256sum "$build_log" | awk '{print $1}')"
    printf 'vermagic=%s\n' "$vermagic"
    printf 'srcversion=%s\n' "$srcversion"
    printf 'diag_fence_wait=present default=false (module parameter)\n'
    printf 'diag_ctxsw=present default=false (module parameter)\n'
    printf 'dkms_build_jobs=%s\n' "$jobs"
} >> "$evidence_dir/install-metadata.txt"
chown "$user_uid:$user_gid" "$evidence_dir/install-metadata.txt"

echo "Installing the already-built diagnostic module; no module reload is requested."
dkms install -m "$NAME" -v "$DIAG_VERSION" -k "$kernel"
depmod -a "$kernel"
update-initramfs -u -k "$kernel"

installed_after=$(modinfo -n nouveau)
installed_srcversion=$(modinfo -F srcversion "$installed_after")
if [[ $installed_srcversion != "$EXPECTED_DIAG_SRCVERSION" ]]; then
    echo "ERROR: installed module has unexpected srcversion $installed_srcversion" >&2
    exit 2
fi
if ! dkms status -m "$NAME" -v "$DIAG_VERSION" -k "$kernel" | grep -q 'installed'; then
    echo "ERROR: DKMS does not report $DIAG_VERSION installed for $kernel" >&2
    exit 2
fi
if [[ ! -f "/usr/src/$NAME-$BASE_VERSION/dkms.conf" || ! -f $old_built_module ]]; then
    echo "ERROR: preserved $BASE_VERSION source/build was unexpectedly removed" >&2
    exit 2
fi

{
    printf 'installed_module_path=%s\n' "$installed_after"
    printf 'installed_module_srcversion=%s\n' "$installed_srcversion"
    printf 'installed_module_sha256=%s\n' "$(sha256sum "$installed_after" | awk '{print $1}')"
    printf 'installed_dkms_status=%s\n' "$(dkms status -m "$NAME" -v "$DIAG_VERSION" -k "$kernel")"
    printf 'baseline_dkms_status=%s\n' "$(dkms status -m "$NAME" -v "$BASE_VERSION" -k "$kernel")"
    printf 'reboot_required=yes\n'
} >> "$evidence_dir/install-metadata.txt"
chown "$user_uid:$user_gid" "$evidence_dir/install-metadata.txt"

echo "Installed $NAME/$DIAG_VERSION for $kernel; running module remains unchanged until reboot."
echo "Evidence and rollback module: $evidence_dir"
echo "Verify before reboot: sudo $HERE/tools/verify-vp-fence-diagnostic.sh pre-reboot"
echo "To restore the existing $BASE_VERSION build: sudo $HERE/tools/rollback-vp-fence-diagnostic.sh"
