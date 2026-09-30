#!/usr/bin/env bash
set -Eeuo pipefail

NAME=nouveau-hpd-ddc
PREVIOUS_VERSION=0.1.13-diag2
VERSION=0.1.13-diag3
KERNEL=7.0.0-34-generic
BRANCH=review/gk104-vaapi-followup-20260928
BUILD_HEAD=94cc54875b1fff7a863dd483a0482c2572446046
BUILD_TREE=a51ffc06c3a059263ade1796625e9acf8bbb8686
BUILD_PROVENANCE_SHA256=11affb8a638dffc9c20c0c04f3dc8a33d0a120c34b298d3f875b09ba2ee6b5ec
INPUT_MANIFEST_SHA256=251c8912ac2038b422837f0f857770b5675b2bc12f51b54d6b305d1f18e2e542
PREVIOUS_SRCVERSION=72DEE2B4ECFF77AD3764039
PREVIOUS_MODULE_SHA256=6bad3f6bf29031d370bddc6965559f88eb4f6f2267585556f23d1fb0e8a3a464
DIAG_SRCVERSION=9F90A7EB5A9E1505E0B6708
DIAG_MODULE_SHA256=d437863bd12473c8dbba7104cf8bccdc7a2b67fafe10a9e0d5f963a168245aca
SOURCE_ARCHIVE=/usr/src/linux-source-7.0.0/linux-source-7.0.0.tar.bz2
SOURCE_ARCHIVE_SHA256=a874e1fb08d2ee695b08e0c8ce6fd2c76a4bf7ffa98882fbabd233380ef8a85a
SOURCE_PACKAGE_VERSION=7.0.0-34.34
OPTION_FILE=/etc/modprobe.d/99-nouveau-diag-bar2-once.conf
OPTION_TEXT='options nouveau diag_bar2_map=1'

HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
SCRIPT_PATH="$HERE/tools/install-bar2-map-diagnostic-v3.sh"
if [[ $EUID -ne 0 ]]; then exec sudo -- "$SCRIPT_PATH" "$@"; fi
if (($# != 0)); then echo "Usage: $0" >&2; exit 2; fi

branch=$(git -c "safe.directory=$HERE" -C "$HERE" branch --show-current)
status=$(git -c "safe.directory=$HERE" -C "$HERE" status --porcelain=v1 --untracked-files=all)
head=$(git -c "safe.directory=$HERE" -C "$HERE" rev-parse HEAD)
tree=$(git -c "safe.directory=$HERE" -C "$HERE" rev-parse 'HEAD^{tree}')
if [[ $branch != "$BRANCH" || -n $status ]] ||
   ! git -c "safe.directory=$HERE" -C "$HERE" merge-base --is-ancestor "$BUILD_HEAD" HEAD; then
    echo 'ERROR: install requires a clean review branch descended from the pinned build commit' >&2
    printf 'branch=%s\nhead=%s\n%s\n' "$branch" "$head" "$status" >&2
    exit 2
fi
[[ $(uname -r) == "$KERNEL" ]] || { echo "ERROR: expected kernel $KERNEL" >&2; exit 2; }

disk_path=$(modinfo -n nouveau)
loaded=$(cat /sys/module/nouveau/srcversion 2>/dev/null || true)
disk_src=$(modinfo -F srcversion "$disk_path")
disk_hash=$(sha256sum "$disk_path" | awk '{print $1}')
[[ $loaded == "$PREVIOUS_SRCVERSION" && $disk_src == "$PREVIOUS_SRCVERSION" &&
   $disk_hash == "$PREVIOUS_MODULE_SHA256" ]] || {
    echo 'ERROR: loaded and on-disk diag2 baseline does not match the pinned rollback state' >&2
    printf 'loaded=%s disk=%s module_sha256=%s path=%s\n' "$loaded" "$disk_src" "$disk_hash" "$disk_path" >&2
    exit 2
}
grep -q 'installed' < <(dkms status -m "$NAME" -v "$PREVIOUS_VERSION" -k "$KERNEL") || {
    echo 'ERROR: preserved diag2 is not installed' >&2; exit 2;
}
if dkms status -m "$NAME" -v "$VERSION" -k "$KERNEL" | grep -q 'installed'; then
    echo 'ERROR: diag3 is already installed; inspect it before proceeding' >&2; exit 2
fi

SOURCE_DIR="/usr/src/$NAME-$VERSION"
BUILD_DIR="/var/lib/dkms/$NAME/$VERSION/$KERNEL/$(uname -m)"
MODULE="$BUILD_DIR/module/nouveau.ko.zst"
LOG="$BUILD_DIR/log/make.log"
PROVENANCE="$SOURCE_DIR/build-provenance.txt"
MANIFEST="$SOURCE_DIR/source-inputs.sha256"
[[ -f $MODULE && -f $LOG && -f "$SOURCE_DIR/dkms.conf" && -f $PROVENANCE && -f $MANIFEST ]] || {
    echo 'ERROR: pinned diag3 build artifacts are incomplete' >&2; exit 2;
}
grep -q 'built' < <(dkms status -m "$NAME" -v "$VERSION" -k "$KERNEL") || {
    echo 'ERROR: DKMS does not report diag3 as built' >&2; exit 2;
}
[[ $(sha256sum "$PROVENANCE" | awk '{print $1}') == "$BUILD_PROVENANCE_SHA256" &&
   $(sha256sum "$MANIFEST" | awk '{print $1}') == "$INPUT_MANIFEST_SHA256" ]] || {
    echo 'ERROR: diag3 build provenance or input manifest changed' >&2; exit 2;
}
grep -Fxq "repo_branch=$BRANCH" "$PROVENANCE"
grep -Fxq "repo_head=$BUILD_HEAD" "$PROVENANCE"
grep -Fxq "repo_tree=$BUILD_TREE" "$PROVENANCE"
grep -Fxq 'repo_clean=yes' "$PROVENANCE"
grep -Fxq "kernel=$KERNEL" "$PROVENANCE"
grep -Fxq "kernel_source_archive_sha256=$SOURCE_ARCHIVE_SHA256" "$PROVENANCE"
grep -Fxq "source_input_manifest_sha256=$INPUT_MANIFEST_SHA256" "$PROVENANCE"
[[ $(dpkg-query -W -f='${Version}' linux-source-7.0.0) == "$SOURCE_PACKAGE_VERSION" &&
   $(sha256sum "$SOURCE_ARCHIVE" | awk '{print $1}') == "$SOURCE_ARCHIVE_SHA256" ]] || {
    echo 'ERROR: exact Ubuntu kernel source package/archive check failed' >&2; exit 2;
}

(cd "$HERE" && sha256sum --check "$MANIFEST") || {
    echo 'ERROR: current checkout inputs do not match the pinned diag3 build manifest' >&2; exit 2;
}
while read -r _ relative; do
    case "$relative" in
        dkms/dkms.conf)
            [[ $(grep -Fc "PACKAGE_VERSION=\"$VERSION\"" "$SOURCE_DIR/dkms.conf") -eq 1 ]] || exit 2
            sed "s/^PACKAGE_VERSION=\"$VERSION\"$/PACKAGE_VERSION=\"0.1.13\"/" \
                "$SOURCE_DIR/dkms.conf" | cmp -s - "$HERE/$relative" || exit 2
            ;;
        dkms/*) cmp -s "$HERE/$relative" "$SOURCE_DIR/${relative#dkms/}" || exit 2 ;;
        patches/*) cmp -s "$HERE/$relative" "$SOURCE_DIR/$relative" || exit 2 ;;
        tools/prepare-bar2-map-diagnostic-v3.sh) ;;
        *) echo "ERROR: unexpected build input $relative" >&2; exit 2 ;;
    esac
done < "$MANIFEST"

[[ $(modinfo -F srcversion "$MODULE") == "$DIAG_SRCVERSION" &&
   $(modinfo -F vermagic "$MODULE") == "$KERNEL "* &&
   $(sha256sum "$MODULE" | awk '{print $1}') == "$DIAG_MODULE_SHA256" ]] || {
    echo 'ERROR: built diag3 module does not match the reviewed artifact' >&2; exit 2;
}
for marker in experimental-legacy-nonstall.enabled diagnostic-vp-fence.enabled \
              diagnostic-bar2-map.enabled diagnostic-bar2-map-budget.enabled; do
    [[ -f $SOURCE_DIR/$marker ]] || { echo "ERROR: missing staged marker $marker" >&2; exit 2; }
done
params=$(modinfo -p "$MODULE")
for parameter in diag_fence_wait diag_ctxsw diag_bar2_map; do
    grep -q "^$parameter:" <<<"$params" || { echo "ERROR: missing module parameter $parameter" >&2; exit 2; }
done
strings_tmp=$(mktemp)
trap 'rm -f -- "$strings_tmp"' EXIT
zstd -dc "$MODULE" | strings -a > "$strings_tmp"
for marker in NOUVEAU_DIAG_IDLE_FENCE NOUVEAU_DIAG_CTXSW NOUVEAU_DIAG_BAR2_MAP \
              NOUVEAU_DIAG_BAR2_ACCESS 'NOUVEAU_DIAG_BAR2_MAP budget=lifecycle'; do
    grep -Fq "$marker" "$strings_tmp" || { echo "ERROR: missing module marker $marker" >&2; exit 2; }
done

for command_name in depmod dracut lsinitrd; do
    command -v "$command_name" >/dev/null 2>&1 || { echo "ERROR: missing $command_name" >&2; exit 2; }
done
INITRD="/boot/initrd.img-$KERNEL"
[[ -f $INITRD && ! -e $OPTION_FILE ]] || {
    echo 'ERROR: initramfs is missing or the one-shot option file already exists' >&2; exit 2;
}

user_name=${SUDO_USER:-root}
user_record=$(getent passwd "$user_name") || { echo "ERROR: unknown invoking user $user_name" >&2; exit 2; }
IFS=: read -r _ _ uid gid _ user_home _ <<<"$user_record"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
evidence="$user_home/.cache/nouveau-vaapi-diag3-install-$stamp"
install -d -o "$uid" -g "$gid" -m 0755 "$evidence"
available_kb=$(df -Pk "$evidence" | awk 'NR == 2 {print $4}')
initrd_kb=$(du -k "$INITRD" | awk '{print $1}')
((available_kb > initrd_kb + 500000)) || { echo 'ERROR: insufficient space for rollback evidence' >&2; exit 2; }
cp -p "$disk_path" "$evidence/preinstall-diag2.ko.zst"
cp -p "$MODULE" "$evidence/diag3.ko.zst"
cp -p "$LOG" "$evidence/build.log"
cp -p "$PROVENANCE" "$MANIFEST" "$evidence/"
cp -p "$INITRD" "$evidence/initrd.pre-diag3"
chown -R "$uid:$gid" "$evidence"
{
    printf 'install_repo_head=%s\ninstall_repo_tree=%s\n' "$head" "$tree"
    printf 'build_repo_head=%s\nbuild_repo_tree=%s\n' "$BUILD_HEAD" "$BUILD_TREE"
    printf 'preinstall_loaded_srcversion=%s\npreinstall_disk_srcversion=%s\n' "$loaded" "$disk_src"
    printf 'preinstall_disk_module_sha256=%s\n' "$disk_hash"
    printf 'diag3_srcversion=%s\ndiag3_module_sha256=%s\n' "$DIAG_SRCVERSION" "$DIAG_MODULE_SHA256"
    printf 'input_manifest_sha256=%s\n' "$INPUT_MANIFEST_SHA256"
    printf 'initrd_before_sha256=%s\n' "$(sha256sum "$evidence/initrd.pre-diag3" | awk '{print $1}')"
    printf 'install_performed=no\n'
} > "$evidence/install-metadata.txt"
chown "$uid:$gid" "$evidence/install-metadata.txt"

echo "Installing $VERSION on disk only; the loaded diag2 module will remain active."
dkms install --force -m "$NAME" -v "$VERSION" -k "$KERNEL"
depmod -a "$KERNEL"
installed_path=$(modinfo -n nouveau)
installed_srcversion=$(modinfo -F srcversion "$installed_path")
installed_hash=$(sha256sum "$installed_path" | awk '{print $1}')
[[ $installed_path == "/lib/modules/$KERNEL/updates/dkms/nouveau.ko.zst" &&
   $installed_srcversion == "$DIAG_SRCVERSION" && $installed_hash == "$DIAG_MODULE_SHA256" ]] || {
    echo 'ERROR: selected on-disk module differs from the pinned diag3 artifact' >&2; exit 2;
}
grep -q 'installed' < <(dkms status -m "$NAME" -v "$VERSION" -k "$KERNEL") || {
    echo 'ERROR: DKMS does not report diag3 as installed' >&2; exit 2;
}
[[ $(cat /sys/module/nouveau/srcversion) == "$PREVIOUS_SRCVERSION" ]] || {
    echo 'ERROR: running Nouveau changed during on-disk installation' >&2; exit 2;
}

printf '%s\n' "$OPTION_TEXT" > "$OPTION_FILE"
chmod 0644 "$OPTION_FILE"
cleanup_option() {
    if [[ -f $OPTION_FILE && $(cat "$OPTION_FILE") == "$OPTION_TEXT" ]]; then unlink "$OPTION_FILE"; fi
}
trap 'cleanup_option; rm -f -- "$strings_tmp"' EXIT
dracut --force --include "$OPTION_FILE" "$OPTION_FILE" "$INITRD" "$KERNEL"
option_in_image=$(lsinitrd -f "$OPTION_FILE" "$INITRD" 2>/dev/null || true)
image_listing=$(lsinitrd "$INITRD" 2>/dev/null)
[[ $option_in_image == "$OPTION_TEXT" ]] || { echo 'ERROR: dracut image lacks the early BAR2 option' >&2; exit 2; }
grep -Fq "usr/lib/modules/$KERNEL/updates/dkms/nouveau.ko.zst" <<<"$image_listing" || {
    echo 'ERROR: dracut image lacks the selected Nouveau module' >&2; exit 2;
}
unlink "$OPTION_FILE"
trap 'rm -f -- "$strings_tmp"' EXIT
[[ $(cat /sys/module/nouveau/srcversion) == "$PREVIOUS_SRCVERSION" ]] || {
    echo 'ERROR: running Nouveau changed while preparing dracut' >&2; exit 2;
}
{
    printf 'installed_module_path=%s\ninstalled_srcversion=%s\ninstalled_module_sha256=%s\n' \
        "$installed_path" "$installed_srcversion" "$installed_hash"
    printf 'dkms_diag3_status=%s\n' "$(dkms status -m "$NAME" -v "$VERSION" -k "$KERNEL")"
    printf 'loaded_srcversion_after_install=%s\n' "$(cat /sys/module/nouveau/srcversion)"
    printf 'initrd_with_early_option_sha256=%s\nhost_option_file_removed=yes\nreboot_required=yes\ninstall_performed=yes\n' \
        "$(sha256sum "$INITRD" | awk '{print $1}')"
} >> "$evidence/install-metadata.txt"
chown "$uid:$gid" "$evidence/install-metadata.txt"
echo "PASS: $VERSION installed on disk; one-boot dracut option staged; loaded diag2 unchanged."
echo "Evidence: $evidence"
echo "Next: sudo $HERE/tools/verify-bar2-map-diagnostic-v3.sh pre-reboot"
