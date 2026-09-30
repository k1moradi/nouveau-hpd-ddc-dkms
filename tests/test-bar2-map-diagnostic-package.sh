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
grep -Fq 'records per second. This bounds logging' \
    "$repo_root/docs/BAR2-MAP-DIAGNOSTIC.md"
python3 "$repo_root/tests/test_bar2_map_rate_limit_isolation.py"
grep -Fq 'diagnostic-bar2-map-budget.enabled' \
    "$repo_root/dkms/dkms-build.sh"
grep -Fq 'gk104-bar2-map-rate-limit-isolation.patch' \
    "$repo_root/dkms/dkms-build.sh"
grep -Fq 'DIAG_VERSION=0.1.13-diag3' \
    "$repo_root/tools/prepare-bar2-map-diagnostic-v3.sh"
grep -Fq '0.1.13-diag3' "$repo_root/uninstall.sh"
grep -Fq 'PREVIOUS_DIAG_VERSION=0.1.13-diag2' \
    "$repo_root/tools/prepare-bar2-map-diagnostic-v3.sh"
grep -Fq 'EXPECTED_DIAG_SRCVERSION=9F90A7EB5A9E1505E0B6708' \
    "$repo_root/tools/prepare-bar2-map-diagnostic-v3.sh"
grep -Fq 'touch "$SRC_DIR/diagnostic-bar2-map-budget.enabled"' \
    "$repo_root/tools/prepare-bar2-map-diagnostic-v3.sh"
grep -Fq 'gk104-bar2-map-rate-limit-isolation.patch' \
    "$repo_root/tools/prepare-bar2-map-diagnostic-v3.sh"
for script in install-bar2-map-diagnostic-v3.sh \
              verify-bar2-map-diagnostic-v3.sh \
              rollback-bar2-map-diagnostic-v3.sh; do
    test -x "$repo_root/tools/$script"
    grep -Fq '0.1.13-diag3' "$repo_root/tools/$script"
done
grep -Fq 'DIAG_SRCVERSION=9F90A7EB5A9E1505E0B6708' \
    "$repo_root/tools/install-bar2-map-diagnostic-v3.sh"
grep -Fq 'DIAG_MODULE_SHA256=d437863bd12473c8dbba7104cf8bccdc7a2b67fafe10a9e0d5f963a168245aca' \
    "$repo_root/tools/install-bar2-map-diagnostic-v3.sh"
grep -Fq 'dkms install --force -m "$NAME" -v "$VERSION"' \
    "$repo_root/tools/install-bar2-map-diagnostic-v3.sh"
grep -Fq 'dracut --force --include "$OPTION_FILE"' \
    "$repo_root/tools/install-bar2-map-diagnostic-v3.sh"
grep -Fq 'clear-early-option' \
    "$repo_root/tools/verify-bar2-map-diagnostic-v3.sh"
grep -Fq 'PREVIOUS_SRCVERSION=72DEE2B4ECFF77AD3764039' \
    "$repo_root/tools/rollback-bar2-map-diagnostic-v3.sh"
grep -Fq 'PREVIOUS_VERSION=0.1.13-diag2' \
    "$repo_root/tools/install-bar2-map-diagnostic-v3.sh"
! grep -Eq '(^|[;&|[:space:]])reboot([[:space:]]|$)' \
    "$repo_root/tools/install-bar2-map-diagnostic-v3.sh"
! grep -Fq 'diagnostic-bar2-map-budget.enabled' \
    "$repo_root/tools/prepare-bar2-map-diagnostic.sh"
! grep -Eq '^dkms install([[:space:]]|$)' \
    "$repo_root/tools/prepare-bar2-map-diagnostic-v3.sh"
! grep -Eq 'update-initramfs|dracut|modprobe -r' \
    "$repo_root/tools/prepare-bar2-map-diagnostic-v3.sh"
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
