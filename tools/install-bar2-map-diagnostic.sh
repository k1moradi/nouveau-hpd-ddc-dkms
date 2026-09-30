#!/usr/bin/env bash
set -Eeuo pipefail

NAME=nouveau-hpd-ddc
PREVIOUS_DIAG_VERSION=0.1.13-diag1
DIAG_VERSION=0.1.13-diag2
BASE_VERSION=0.1.13
EXPECTED_KERNEL=7.0.0-34-generic
EXPECTED_REVIEW_BRANCH=review/gk104-vaapi-followup-20260928
EXPECTED_SOURCE_PACKAGE_VERSION=7.0.0-34.34
SOURCE_ARCHIVE=/usr/src/linux-source-7.0.0/linux-source-7.0.0.tar.bz2
EXPECTED_SOURCE_ARCHIVE_SHA256=a874e1fb08d2ee695b08e0c8ce6fd2c76a4bf7ffa98882fbabd233380ef8a85a
EXPECTED_PREVIOUS_SRCVERSION=B19B8AAE48467545E652509
EXPECTED_BASE_SRCVERSION=57AE1B168D50DB546CD87A1
EXPECTED_DIAG_SRCVERSION=72DEE2B4ECFF77AD3764039
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
SCRIPT_PATH="$HERE/tools/install-bar2-map-diagnostic.sh"

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
    echo "ERROR: installation requires a clean $EXPECTED_REVIEW_BRANCH checkout" >&2
    printf 'branch=%s\n' "$git_branch" >&2
    if [[ -n $git_status ]]; then
        printf '%s\n' "$git_status" >&2
    fi
    exit 2
fi
repo_head=$(git -c "safe.directory=$HERE" -C "$HERE" rev-parse HEAD)
repo_tree=$(git -c "safe.directory=$HERE" -C "$HERE" rev-parse 'HEAD^{tree}')

kernel=$(uname -r)
[[ $kernel == "$EXPECTED_KERNEL" ]] || {
    echo "ERROR: expected $EXPECTED_KERNEL, running $kernel" >&2
    exit 2
}
loaded_before=$(cat /sys/module/nouveau/srcversion 2>/dev/null || true)
disk_path=$(modinfo -n nouveau 2>/dev/null || true)
disk_srcversion=$(modinfo -F srcversion "$disk_path" 2>/dev/null || true)
[[ $loaded_before == "$EXPECTED_PREVIOUS_SRCVERSION" &&
   $disk_srcversion == "$EXPECTED_PREVIOUS_SRCVERSION" ]] || {
    echo "ERROR: expected loaded and on-disk $PREVIOUS_DIAG_VERSION before install" >&2
    exit 2
}

build_module="/var/lib/dkms/$NAME/$DIAG_VERSION/$kernel/$(uname -m)/module/nouveau.ko.zst"
build_log="/var/lib/dkms/$NAME/$DIAG_VERSION/$kernel/$(uname -m)/log/make.log"
source_dir="/usr/src/$NAME-$DIAG_VERSION"
[[ -f $build_module && -f $build_log && -f "$source_dir/dkms.conf" &&
   -f "$source_dir/build-provenance.txt" && -f "$source_dir/source-inputs.sha256" ]] || {
    echo "ERROR: prepared $DIAG_VERSION DKMS build is incomplete; run prepare-bar2-map-diagnostic.sh first" >&2
    exit 2
}
source_package_version=$(dpkg-query -W -f='${Version}' linux-source-7.0.0 2>/dev/null || true)
source_archive_sha256=$(sha256sum "$SOURCE_ARCHIVE" 2>/dev/null | awk '{print $1}')
if [[ $source_package_version != "$EXPECTED_SOURCE_PACKAGE_VERSION" ||
      $source_archive_sha256 != "$EXPECTED_SOURCE_ARCHIVE_SHA256" ]]; then
    echo "ERROR: Ubuntu Linux source package/archive differs from the reviewed build input" >&2
    exit 2
fi
provenance_file="$source_dir/build-provenance.txt"
input_manifest="$source_dir/source-inputs.sha256"
require_provenance_value() {
    local key=$1 expected=$2
    grep -Fxq "$key=$expected" "$provenance_file" || {
        echo "ERROR: prepared source provenance does not match $key" >&2
        exit 2
    }
}
require_provenance_value repo_branch "$EXPECTED_REVIEW_BRANCH"
require_provenance_value repo_head "$repo_head"
require_provenance_value repo_tree "$repo_tree"
require_provenance_value repo_clean yes
require_provenance_value kernel "$kernel"
require_provenance_value kernel_source_package linux-source-7.0.0
require_provenance_value kernel_source_package_version "$source_package_version"
require_provenance_value kernel_source_archive "$SOURCE_ARCHIVE"
require_provenance_value kernel_source_archive_sha256 "$source_archive_sha256"
prepare_script_sha256=$(sha256sum "$HERE/tools/prepare-bar2-map-diagnostic.sh" | awk '{print $1}')
require_provenance_value prepare_script_sha256 "$prepare_script_sha256"

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
source_inputs_current=$(mktemp)
staged_config_current=$(mktemp)
strings_file=$(mktemp)
trap 'rm -f -- "$source_inputs_current" "$staged_config_current" "$strings_file"' EXIT
(
    cd "$HERE"
    printf '%s\0' "${provenance_inputs[@]}" | sort -zu | xargs -0 sha256sum
) > "$source_inputs_current"
current_input_manifest_sha256=$(sha256sum "$source_inputs_current" | awk '{print $1}')
require_provenance_value source_input_manifest_sha256 "$current_input_manifest_sha256"
if ! cmp -s "$input_manifest" "$source_inputs_current"; then
    echo "ERROR: prepared DKMS source inputs differ from the clean review tree" >&2
    exit 2
fi
while IFS= read -r input_path; do
    case "$input_path" in
        dkms/dkms.conf)
            [[ $(grep -Fc "PACKAGE_VERSION=\"$DIAG_VERSION\"" "$source_dir/dkms.conf") -eq 1 &&
               $(grep -Fc 'PACKAGE_VERSION="0.1.13"' "$source_dir/dkms.conf" || true) -eq 0 ]] || {
                echo "ERROR: staged DKMS configuration has an unexpected package version" >&2
                exit 2
            }
            sed "s/^PACKAGE_VERSION=\"$DIAG_VERSION\"$/PACKAGE_VERSION=\"0.1.13\"/" \
                "$source_dir/dkms.conf" > "$staged_config_current"
            cmp -s "$HERE/$input_path" "$staged_config_current" || {
                echo "ERROR: staged DKMS configuration differs beyond its package version" >&2
                exit 2
            }
            ;;
        dkms/*)
            staged_path="$source_dir/${input_path#dkms/}"
            cmp -s "$HERE/$input_path" "$staged_path" || {
                echo "ERROR: staged DKMS input differs from the review tree: $input_path" >&2
                exit 2
            }
            ;;
        patches/*)
            staged_path="$source_dir/$input_path"
            cmp -s "$HERE/$input_path" "$staged_path" || {
                echo "ERROR: staged patch differs from the review tree: $input_path" >&2
                exit 2
            }
            ;;
        tools/prepare-bar2-map-diagnostic.sh)
            ;;
        *)
            echo "ERROR: unrecognized provenance input: $input_path" >&2
            exit 2
            ;;
    esac
done < <(awk '{print $2}' "$input_manifest")
[[ $(modinfo -F vermagic "$build_module") == "$kernel "* ]] || {
    echo "ERROR: prepared module vermagic does not match $kernel" >&2
    exit 2
}
[[ $(modinfo -F srcversion "$build_module") == "$EXPECTED_DIAG_SRCVERSION" ]] || {
    echo "ERROR: prepared module srcversion is not the reviewed diagnostic build" >&2
    exit 2
}
[[ $(sha256sum "$source_dir/patches/diagnostic/gk104-bar2-instmem-map-trace.patch" | awk '{print $1}') == \
   $(sha256sum "$HERE/patches/diagnostic/gk104-bar2-instmem-map-trace.patch" | awk '{print $1}') ]] || {
    echo "ERROR: prepared DKMS source patch differs from the review branch" >&2
    exit 2
}
params=$(modinfo -p "$build_module")
for parameter in diag_fence_wait diag_ctxsw diag_bar2_map; do
    grep -q "^$parameter:" <<<"$params" || {
        echo "ERROR: prepared module is missing $parameter" >&2
        exit 2
    }
done
zstd -dc "$build_module" | strings -a > "$strings_file"
for marker in NOUVEAU_DIAG_IDLE_FENCE NOUVEAU_DIAG_CTXSW NOUVEAU_DIAG_BAR2_MAP NOUVEAU_DIAG_BAR2_ACCESS; do
    grep -Fq "$marker" "$strings_file" || {
        echo "ERROR: prepared module is missing trace marker $marker" >&2
        exit 2
    }
done

if ! dkms status -m "$NAME" -v "$PREVIOUS_DIAG_VERSION" -k "$kernel" | grep -q 'installed'; then
    echo "ERROR: preserved $PREVIOUS_DIAG_VERSION is not installed" >&2
    exit 2
fi
base_module="/var/lib/dkms/$NAME/$BASE_VERSION/$kernel/$(uname -m)/module/nouveau.ko.zst"
previous_module="/var/lib/dkms/$NAME/$PREVIOUS_DIAG_VERSION/$kernel/$(uname -m)/module/nouveau.ko.zst"
[[ -f $base_module && -f $previous_module ]] || {
    echo "ERROR: preserved baseline/diag1 module artifact is missing" >&2
    exit 2
}
[[ $(modinfo -F srcversion "$base_module") == "$EXPECTED_BASE_SRCVERSION" ]] || {
    echo "ERROR: preserved .13 baseline srcversion does not match" >&2
    exit 2
}
[[ $(modinfo -F srcversion "$previous_module") == "$EXPECTED_PREVIOUS_SRCVERSION" ]] || {
    echo "ERROR: preserved diag1 srcversion does not match" >&2
    exit 2
}
user_name=${SUDO_USER:-root}
user_record=$(getent passwd "$user_name") || {
    echo "ERROR: cannot resolve invoking user $user_name" >&2
    exit 2
}
IFS=: read -r _ _ user_uid user_gid _ user_home _ <<<"$user_record"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
evidence_dir="$user_home/.cache/nouveau-bar2-map-diag-install-$stamp"
install -d -o "$user_uid" -g "$user_gid" -m 0755 "$evidence_dir"
cp -p "$disk_path" "$evidence_dir/preinstall-nouveau.ko.zst"
cp -p "$build_module" "$evidence_dir/nouveau-diag2.ko.zst"
cp -p "$build_log" "$evidence_dir/dkms-make.log"
chown "$user_uid:$user_gid" "$evidence_dir"/*
{
    printf 'repo_head=%s\n' "$repo_head"
    printf 'repo_tree=%s\n' "$repo_tree"
    printf 'repo_clean=yes\n'
    printf 'build_provenance_sha256=%s\n' "$(sha256sum "$provenance_file" | awk '{print $1}')"
    printf 'source_input_manifest_sha256=%s\n' "$current_input_manifest_sha256"
    printf 'kernel=%s\n' "$kernel"
    printf 'previous_loaded_srcversion=%s\n' "$loaded_before"
    printf 'prepared_srcversion=%s\n' "$(modinfo -F srcversion "$build_module")"
    printf 'prepared_module_sha256=%s\n' "$(sha256sum "$build_module" | awk '{print $1}')"
    printf 'build_log_sha256=%s\n' "$(sha256sum "$build_log" | awk '{print $1}')"
    printf 'bar2_patch_sha256=%s\n' "$(sha256sum "$HERE/patches/diagnostic/gk104-bar2-instmem-map-trace.patch" | awk '{print $1}')"
} > "$evidence_dir/install-metadata.txt"
chown "$user_uid:$user_gid" "$evidence_dir/install-metadata.txt"

echo "Installing $NAME/$DIAG_VERSION on disk; the loaded module will not be reloaded."
dkms install --force -m "$NAME" -v "$DIAG_VERSION" -k "$kernel"
depmod -a "$kernel"
update-initramfs -u -k "$kernel"

installed_path=$(modinfo -n nouveau)
installed_srcversion=$(modinfo -F srcversion "$installed_path")
[[ $installed_srcversion == "$EXPECTED_DIAG_SRCVERSION" ]] || {
    echo "ERROR: installed module has unexpected srcversion $installed_srcversion" >&2
    exit 2
}
dkms status -m "$NAME" -v "$DIAG_VERSION" -k "$kernel" | grep -q 'installed' || {
    echo "ERROR: DKMS does not report $DIAG_VERSION installed" >&2
    exit 2
}
[[ $(cat /sys/module/nouveau/srcversion) == "$EXPECTED_PREVIOUS_SRCVERSION" ]] || {
    echo "ERROR: running Nouveau changed during on-disk install" >&2
    exit 2
}
{
    printf 'installed_module_path=%s\n' "$installed_path"
    printf 'installed_module_srcversion=%s\n' "$installed_srcversion"
    printf 'installed_module_sha256=%s\n' "$(sha256sum "$installed_path" | awk '{print $1}')"
    printf 'dkms_status=%s\n' "$(dkms status -m "$NAME" -v "$DIAG_VERSION" -k "$kernel")"
    printf 'running_srcversion_after_install=%s\n' "$(cat /sys/module/nouveau/srcversion)"
    printf 'reboot_required=yes\n'
} >> "$evidence_dir/install-metadata.txt"
chown "$user_uid:$user_gid" "$evidence_dir/install-metadata.txt"

echo "Installed on disk only. On the next boot, add the one-time kernel argument nouveau.diag_bar2_map=1."
echo "Evidence: $evidence_dir"
echo "Verify before reboot: sudo $HERE/tools/verify-bar2-map-diagnostic.sh pre-reboot"
echo "Rollback on disk: sudo $HERE/tools/rollback-bar2-map-diagnostic.sh"
