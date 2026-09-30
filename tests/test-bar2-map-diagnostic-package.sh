#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
version=0.1.13-diag2

for script in prepare-bar2-map-diagnostic.sh install-bar2-map-diagnostic.sh \
              verify-bar2-map-diagnostic.sh; do
    grep -Fq "$version" "$repo_root/tools/$script"
done
grep -Fq "$version" "$repo_root/uninstall.sh"
grep -Fq 'touch "$SRC_DIR/diagnostic-bar2-map.enabled"' \
    "$repo_root/tools/prepare-bar2-map-diagnostic.sh"
grep -Fq 'EXPECTED_BASE_SRCVERSION=57AE1B168D50DB546CD87A1' \
    "$repo_root/tools/prepare-bar2-map-diagnostic.sh"
grep -Fq 'EXPECTED_PREVIOUS_SRCVERSION=B19B8AAE48467545E652509' \
    "$repo_root/tools/prepare-bar2-map-diagnostic.sh"
grep -Fq 'EXPECTED_DIAG_SRCVERSION=72DEE2B4ECFF77AD3764039' \
    "$repo_root/tools/prepare-bar2-map-diagnostic.sh"
grep -Fq 'EXPECTED_REVIEW_BRANCH=review/gk104-vaapi-followup-20260928' \
    "$repo_root/tools/prepare-bar2-map-diagnostic.sh"
grep -Fq 'status --porcelain=v1 --untracked-files=all' \
    "$repo_root/tools/prepare-bar2-map-diagnostic.sh"
grep -Fq 'repo_tree=' "$repo_root/tools/prepare-bar2-map-diagnostic.sh"
grep -Fq 'kernel_source_archive_sha256=' \
    "$repo_root/tools/prepare-bar2-map-diagnostic.sh"
grep -Fq 'source-inputs.sha256' \
    "$repo_root/tools/prepare-bar2-map-diagnostic.sh"
grep -Fq 'status --porcelain=v1 --untracked-files=all' \
    "$repo_root/tools/install-bar2-map-diagnostic.sh"
grep -Fq 'cmp -s "$input_manifest" "$source_inputs_current"' \
    "$repo_root/tools/install-bar2-map-diagnostic.sh"
grep -Fq 'cmp -s "$HERE/$input_path" "$staged_path"' \
    "$repo_root/tools/install-bar2-map-diagnostic.sh"
grep -Fq 'EXPECTED_REVIEW_BRANCH=review/gk104-vaapi-followup-20260928' \
    "$repo_root/tools/install-bar2-map-diagnostic.sh"
! grep -Eq '^dkms install([[:space:]]|$)' \
    "$repo_root/tools/prepare-bar2-map-diagnostic.sh"
grep -Fq 'dkms install --force -m "$NAME" -v "$DIAG_VERSION"' \
    "$repo_root/tools/install-bar2-map-diagnostic.sh"
grep -Fq 'one-boot dracut modprobe option to enable diag_bar2_map' \
    "$repo_root/tools/install-bar2-map-diagnostic.sh"
grep -Fq 'diag_bar2_map=1' "$repo_root/tools/verify-bar2-map-diagnostic.sh"
grep -Fq 'dracut-initramfs' "$repo_root/tools/verify-bar2-map-diagnostic.sh"
grep -Fq '99-nouveau-diag-bar2-once.conf' "$repo_root/tools/verify-bar2-map-diagnostic.sh"
grep -Fq 'dkms install --force -m "$NAME" -v "$VERSION"' \
    "$repo_root/tools/rollback-bar2-map-diagnostic.sh"
grep -Fq 'eight combined mapping/access records per second' \
    "$repo_root/docs/BAR2-MAP-DIAGNOSTIC.md"
grep -Fq 'd841244e592f171adebdbeaa556c97cb2e1bf639fceba81b0a829c9720b1119f' \
    "$repo_root/docs/BAR2-MAP-DIAGNOSTIC.md"
grep -Fq 'serialized-teardown native-surface Mesa candidate' \
    "$repo_root/docs/BAR2-MAP-DIAGNOSTIC.md"
grep -Fq 'dracut --force' "$repo_root/docs/BAR2-MAP-DIAGNOSTIC.md"
grep -Fq 'options nouveau diag_bar2_map=1' \
    "$repo_root/docs/BAR2-MAP-DIAGNOSTIC.md"
grep -Fq 'lsinitrd -f' "$repo_root/docs/BAR2-MAP-DIAGNOSTIC.md"
! grep -Fq 'At the GRUB menu' "$repo_root/docs/BAR2-MAP-DIAGNOSTIC.md"

echo 'PASS: BAR2 diagnostic uses a unique DKMS version, validates srcversions, and separates build/install/rollback.'
