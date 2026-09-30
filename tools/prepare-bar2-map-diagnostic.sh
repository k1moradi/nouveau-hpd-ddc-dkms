#!/usr/bin/env bash
set -Eeuo pipefail

NAME=nouveau-hpd-ddc
BASE_VERSION=0.1.13
PREVIOUS_DIAG_VERSION=0.1.13-diag1
DIAG_VERSION=0.1.13-diag2
EXPECTED_KERNEL=7.0.0-34-generic
EXPECTED_REVIEW_BRANCH=review/gk104-vaapi-followup-20260928
EXPECTED_SOURCE_PACKAGE_VERSION=7.0.0-34.34
SOURCE_ARCHIVE=/usr/src/linux-source-7.0.0/linux-source-7.0.0.tar.bz2
EXPECTED_SOURCE_ARCHIVE_SHA256=a874e1fb08d2ee695b08e0c8ce6fd2c76a4bf7ffa98882fbabd233380ef8a85a
EXPECTED_PREVIOUS_SRCVERSION=B19B8AAE48467545E652509
EXPECTED_BASE_SRCVERSION=57AE1B168D50DB546CD87A1
EXPECTED_DIAG_SRCVERSION=72DEE2B4ECFF77AD3764039
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
SCRIPT_PATH="$HERE/tools/prepare-bar2-map-diagnostic.sh"

if [[ $EUID -ne 0 ]]; then
    exec sudo -- "$SCRIPT_PATH" "$@"
fi
if (($# != 0)); then
    echo "Usage: $0" >&2
    exit 2
fi

git_branch=$(git -c "safe.directory=$HERE" -C "$HERE" branch --show-current)
git_status=$(git -c "safe.directory=$HERE" -C "$HERE" status --porcelain=v1 --untracked-files=all)
if [[ $git_branch != "$EXPECTED_REVIEW_BRANCH" || -n $git_status ]]; then
    echo "ERROR: diagnostic build requires a clean $EXPECTED_REVIEW_BRANCH checkout" >&2
    printf 'branch=%s\n' "$git_branch" >&2
    if [[ -n $git_status ]]; then
        printf '%s\n' "$git_status" >&2
    fi
    exit 2
fi
repo_head=$(git -c "safe.directory=$HERE" -C "$HERE" rev-parse HEAD)
repo_tree=$(git -c "safe.directory=$HERE" -C "$HERE" rev-parse 'HEAD^{tree}')

kernel=$(uname -r)
if [[ $kernel != "$EXPECTED_KERNEL" ]]; then
    echo "ERROR: diagnostic is prepared for $EXPECTED_KERNEL; running kernel is $kernel" >&2
    exit 2
fi
loaded_srcversion=$(cat /sys/module/nouveau/srcversion 2>/dev/null || true)
disk_module=$(modinfo -n nouveau 2>/dev/null || true)
disk_srcversion=$(modinfo -F srcversion "$disk_module" 2>/dev/null || true)
if [[ $loaded_srcversion != "$EXPECTED_PREVIOUS_SRCVERSION" ||
      $disk_srcversion != "$EXPECTED_PREVIOUS_SRCVERSION" ]]; then
    echo "ERROR: expected loaded and on-disk $PREVIOUS_DIAG_VERSION srcversion $EXPECTED_PREVIOUS_SRCVERSION" >&2
    echo "loaded=$loaded_srcversion disk=$disk_srcversion path=$disk_module" >&2
    exit 2
fi

for command_name in dkms gcc patch python3 modinfo strings zstd; do
    command -v "$command_name" >/dev/null 2>&1 || {
        echo "ERROR: required command is missing: $command_name" >&2
        exit 2
    }
done
[[ -d "/lib/modules/$kernel/build" ]] || {
    echo "ERROR: kernel headers are missing for $kernel" >&2
    exit 2
}
source_package_version=$(dpkg-query -W -f='${Version}' linux-source-7.0.0 2>/dev/null || true)
source_archive_sha256=$(sha256sum "$SOURCE_ARCHIVE" 2>/dev/null | awk '{print $1}')
if [[ $source_package_version != "$EXPECTED_SOURCE_PACKAGE_VERSION" ||
      $source_archive_sha256 != "$EXPECTED_SOURCE_ARCHIVE_SHA256" ]]; then
    echo "ERROR: Ubuntu Linux source package/archive does not match the reviewed input" >&2
    printf 'package_version=%s\narchive=%s\narchive_sha256=%s\n' \
        "$source_package_version" "$SOURCE_ARCHIVE" "$source_archive_sha256" >&2
    exit 2
fi

SRC_DIR="/usr/src/$NAME-$DIAG_VERSION"
DKMS_VERSION_DIR="/var/lib/dkms/$NAME/$DIAG_VERSION"
if [[ -e $SRC_DIR || -e $DKMS_VERSION_DIR ]]; then
    echo "ERROR: $DIAG_VERSION already exists; inspect it before retrying" >&2
    exit 2
fi
if ! dkms status -m "$NAME" -v "$PREVIOUS_DIAG_VERSION" -k "$kernel" | grep -q 'installed'; then
    echo "ERROR: preserved $NAME/$PREVIOUS_DIAG_VERSION is not installed for $kernel" >&2
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
    patches/diagnostic/gk104-vp-idle-fence-ctxsw-trace.patch \
    patches/diagnostic/gk104-bar2-instmem-map-trace.patch; do
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
evidence_dir="$user_home/.cache/nouveau-bar2-map-diag-build-$stamp"
install -d -o "$user_uid" -g "$user_gid" -m 0755 "$evidence_dir"

provenance_inputs=()
while IFS= read -r -d '' input_path; do
    provenance_inputs+=("${input_path#"$HERE/"}")
done < <(find "$HERE/dkms" -type f -print0)
provenance_inputs+=(
    tools/prepare-bar2-map-diagnostic.sh
    patches/hpd-low-ddc-probe.patch
    patches/video/gk104-legacy-video-context-nonpriv.patch
    patches/video/legacy-fifo-nonstall-event-index.patch
    patches/diagnostic/gk104-vp-idle-fence-ctxsw-trace.patch
    patches/diagnostic/gk104-bar2-instmem-map-trace.patch
)
input_manifest_tmp="$evidence_dir/source-inputs.sha256"
(
    cd "$HERE"
    printf '%s\0' "${provenance_inputs[@]}" | sort -zu | xargs -0 sha256sum
) > "$input_manifest_tmp"
input_manifest_sha256=$(sha256sum "$input_manifest_tmp" | awk '{print $1}')
prepare_script_sha256=$(sha256sum "$SCRIPT_PATH" | awk '{print $1}')

prior_module="/var/lib/dkms/$NAME/$PREVIOUS_DIAG_VERSION/$kernel/$(uname -m)/module/nouveau.ko.zst"
base_module="/var/lib/dkms/$NAME/$BASE_VERSION/$kernel/$(uname -m)/module/nouveau.ko.zst"
[[ -f $disk_module && -f $prior_module && -f $base_module ]] || {
    echo "ERROR: installed or preserved prior module artifact is missing" >&2
    exit 2
}
[[ $(modinfo -F srcversion "$prior_module") == "$EXPECTED_PREVIOUS_SRCVERSION" ]] || {
    echo "ERROR: preserved $PREVIOUS_DIAG_VERSION artifact has unexpected srcversion" >&2
    exit 2
}
[[ $(modinfo -F srcversion "$base_module") == "$EXPECTED_BASE_SRCVERSION" ]] || {
    echo "ERROR: preserved $BASE_VERSION artifact has unexpected srcversion" >&2
    exit 2
}
cp -p "$disk_module" "$evidence_dir/preinstall-nouveau.ko.zst"
cp -p "$prior_module" "$evidence_dir/nouveau-diag1.ko.zst"
cp -p "$base_module" "$evidence_dir/nouveau-0.1.13.ko.zst"
chown "$user_uid:$user_gid" "$evidence_dir"/*.ko.zst

{
    printf 'repo_branch=%s\n' "$git_branch"
    printf 'repo_head=%s\n' "$repo_head"
    printf 'repo_tree=%s\n' "$repo_tree"
    printf 'repo_clean=yes\n'
    printf 'kernel=%s\n' "$kernel"
    printf 'kernel_source_package=linux-source-7.0.0\n'
    printf 'kernel_source_package_version=%s\n' "$source_package_version"
    printf 'kernel_source_archive=%s\n' "$SOURCE_ARCHIVE"
    printf 'kernel_source_archive_sha256=%s\n' "$source_archive_sha256"
    printf 'prepare_script_sha256=%s\n' "$prepare_script_sha256"
    printf 'source_input_manifest_sha256=%s\n' "$input_manifest_sha256"
    printf 'previous_diagnostic_version=%s\n' "$PREVIOUS_DIAG_VERSION"
    printf 'diagnostic_version=%s\n' "$DIAG_VERSION"
    printf 'loaded_srcversion=%s\n' "$loaded_srcversion"
    printf 'disk_module_path=%s\n' "$disk_module"
    printf 'disk_module_sha256=%s\n' "$(sha256sum "$disk_module" | awk '{print $1}')"
    printf 'diag1_module_sha256=%s\n' "$(sha256sum "$prior_module" | awk '{print $1}')"
    printf 'base_module_sha256=%s\n' "$(sha256sum "$base_module" | awk '{print $1}')"
    printf 'bar2_patch_sha256=%s\n' "$(sha256sum "$HERE/patches/diagnostic/gk104-bar2-instmem-map-trace.patch" | awk '{print $1}')"
} > "$evidence_dir/build-metadata.txt"
chown "$user_uid:$user_gid" "$evidence_dir/build-metadata.txt"

mkdir -p "$SRC_DIR/patches/video" "$SRC_DIR/patches/diagnostic"
cp -a "$HERE/dkms/." "$SRC_DIR/"
cp -a "$HERE/patches/hpd-low-ddc-probe.patch" "$SRC_DIR/patches/"
cp -a "$HERE/patches/video/gk104-legacy-video-context-nonpriv.patch" "$SRC_DIR/patches/video/"
cp -a "$HERE/patches/video/legacy-fifo-nonstall-event-index.patch" "$SRC_DIR/patches/video/"
cp -a "$HERE/patches/diagnostic/gk104-vp-idle-fence-ctxsw-trace.patch" "$SRC_DIR/patches/diagnostic/"
cp -a "$HERE/patches/diagnostic/gk104-bar2-instmem-map-trace.patch" "$SRC_DIR/patches/diagnostic/"
cp -p "$input_manifest_tmp" "$SRC_DIR/source-inputs.sha256"
{
    printf 'repo_branch=%s\n' "$git_branch"
    printf 'repo_head=%s\n' "$repo_head"
    printf 'repo_tree=%s\n' "$repo_tree"
    printf 'repo_clean=yes\n'
    printf 'kernel=%s\n' "$kernel"
    printf 'kernel_source_package=linux-source-7.0.0\n'
    printf 'kernel_source_package_version=%s\n' "$source_package_version"
    printf 'kernel_source_archive=%s\n' "$SOURCE_ARCHIVE"
    printf 'kernel_source_archive_sha256=%s\n' "$source_archive_sha256"
    printf 'prepare_script_sha256=%s\n' "$prepare_script_sha256"
    printf 'source_input_manifest_sha256=%s\n' "$input_manifest_sha256"
} > "$SRC_DIR/build-provenance.txt"
touch "$SRC_DIR/experimental-legacy-nonstall.enabled"
touch "$SRC_DIR/diagnostic-vp-fence.enabled"
touch "$SRC_DIR/diagnostic-bar2-map.enabled"

version_count=$(grep -Fc 'PACKAGE_VERSION="0.1.13"' "$SRC_DIR/dkms.conf")
if [[ $version_count -ne 1 ]]; then
    echo "ERROR: copied dkms.conf does not contain exactly one baseline version" >&2
    exit 2
fi
sed -i 's/^PACKAGE_VERSION="0\.1\.13"$/PACKAGE_VERSION="0.1.13-diag2"/' "$SRC_DIR/dkms.conf"
grep -Fxq "PACKAGE_VERSION=\"$DIAG_VERSION\"" "$SRC_DIR/dkms.conf" || {
    echo "ERROR: diagnostic DKMS version substitution failed" >&2
    exit 2
}

echo "Adding isolated DKMS source $NAME/$DIAG_VERSION"
dkms add -m "$NAME" -v "$DIAG_VERSION"
jobs=${NOUVEAU_DKMS_JOBS:-1}
case $jobs in
    ''|*[!0-9]*|0)
        echo "ERROR: NOUVEAU_DKMS_JOBS must be a positive integer" >&2
        exit 2
        ;;
esac
echo "Building $NAME/$DIAG_VERSION for $kernel with $jobs job(s)"
NOUVEAU_DKMS_JOBS="$jobs" dkms build -m "$NAME" -v "$DIAG_VERSION" -k "$kernel"

arch=$(uname -m)
build_module="/var/lib/dkms/$NAME/$DIAG_VERSION/$kernel/$arch/module/nouveau.ko.zst"
build_log="/var/lib/dkms/$NAME/$DIAG_VERSION/$kernel/$arch/log/make.log"
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
for parameter in diag_fence_wait diag_ctxsw diag_bar2_map; do
    grep -q "^$parameter:" <<<"$params" || {
        echo "ERROR: $parameter module parameter is missing" >&2
        exit 2
    }
done

strings_file="$evidence_dir/nouveau-module.strings"
zstd -dc "$build_module" | strings -a > "$strings_file"
for marker in NOUVEAU_DIAG_IDLE_FENCE NOUVEAU_DIAG_CTXSW NOUVEAU_DIAG_BAR2_MAP NOUVEAU_DIAG_BAR2_ACCESS; do
    grep -Fq "$marker" "$strings_file" || {
        echo "ERROR: expected module marker is missing: $marker" >&2
        exit 2
    }
done
chown "$user_uid:$user_gid" "$strings_file"

cp -p "$build_module" "$evidence_dir/nouveau-diag2.ko.zst"
cp -p "$build_log" "$evidence_dir/dkms-make.log"
chown "$user_uid:$user_gid" "$evidence_dir/nouveau-diag2.ko.zst" "$evidence_dir/dkms-make.log"
{
    printf 'built_module_path=%s\n' "$build_module"
    printf 'built_module_sha256=%s\n' "$(sha256sum "$build_module" | awk '{print $1}')"
    printf 'build_log_sha256=%s\n' "$(sha256sum "$build_log" | awk '{print $1}')"
    printf 'vermagic=%s\n' "$vermagic"
    printf 'srcversion=%s\n' "$srcversion"
    printf 'diag_fence_wait=present default=false\n'
    printf 'diag_ctxsw=present default=false\n'
    printf 'diag_bar2_map=present default=false until module option is staged\n'
    printf 'dkms_build_jobs=%s\n' "$jobs"
    printf 'installed=no\n'
    printf 'build_provenance_sha256=%s\n' "$(sha256sum "$SRC_DIR/build-provenance.txt" | awk '{print $1}')"
    printf 'source_input_manifest_sha256=%s\n' "$input_manifest_sha256"
} >> "$evidence_dir/build-metadata.txt"
chown "$user_uid:$user_gid" "$evidence_dir/build-metadata.txt" "$input_manifest_tmp"

echo "Prepared and verified $NAME/$DIAG_VERSION; it was not installed."
echo "Build artifact and logs: $evidence_dir"
echo "DKMS status: $(dkms status -m "$NAME" -v "$DIAG_VERSION" -k "$kernel")"
