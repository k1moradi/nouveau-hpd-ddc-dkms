#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
diag_version=0.1.13-diag1

grep -Fxq 'PACKAGE_VERSION="0.1.13"' "$repo_root/dkms/dkms.conf"
grep -Fq "DIAG_VERSION=$diag_version" "$repo_root/tools/install-vp-fence-diagnostic.sh"
grep -Fq "VERSION=$diag_version" "$repo_root/tools/verify-vp-fence-diagnostic.sh"
grep -Fq "DIAG_VERSION=$diag_version" "$repo_root/tools/rollback-vp-fence-diagnostic.sh"
grep -Fq "$diag_version" "$repo_root/uninstall.sh"
grep -Fq 'diagnostic-vp-fence.enabled' "$repo_root/dkms/dkms-build.sh"
grep -Fq 'patch --fuzz=0' "$repo_root/dkms/dkms-build.sh"
grep -Fq 'gk104-vp-idle-fence-ctxsw-trace.patch' "$repo_root/tools/install-vp-fence-diagnostic.sh"
grep -Fq 'diag_fence_wait' "$repo_root/docs/VP-FENCE-DIAGNOSTIC-DKMS.md"

if grep -Fq 'gk104-vp-idle-fence-ctxsw-trace.patch' "$repo_root/install.sh"; then
    echo 'FAIL: normal installer must not stage the opt-in diagnostic' >&2
    exit 1
fi

tmp=$(mktemp -d "${TMPDIR:-/tmp}/vp-fence-dkms-version-test.XXXXXX")
trap 'rm -rf -- "$tmp"' EXIT
cp "$repo_root/dkms/dkms.conf" "$tmp/dkms.conf"
count=$(grep -Fc 'PACKAGE_VERSION="0.1.13"' "$tmp/dkms.conf")
[[ $count -eq 1 ]]
sed -i 's/^PACKAGE_VERSION="0\.1\.13"$/PACKAGE_VERSION="0.1.13-diag1"/' "$tmp/dkms.conf"
grep -Fxq "PACKAGE_VERSION=\"$diag_version\"" "$tmp/dkms.conf"

for script in \
    "$repo_root/tools/install-vp-fence-diagnostic.sh" \
    "$repo_root/tools/verify-vp-fence-diagnostic.sh" \
    "$repo_root/tools/rollback-vp-fence-diagnostic.sh"; do
    bash -n "$script"
done

echo 'PASS: diagnostic DKMS version is isolated from the normal .13 installer and has a matching rollback/verification path.'
