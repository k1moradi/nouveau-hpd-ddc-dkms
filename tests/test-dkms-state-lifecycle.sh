#!/bin/bash
set -Eeuo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
. "$repo_root/dkms/nonstall-state.sh"

tmp=$(mktemp -d "${TMPDIR:-/tmp}/nouveau-dkms-state-test.XXXXXX")
cleanup() {
    rm -rf -- "$tmp"
}
trap cleanup EXIT HUP INT TERM

source_dir="$tmp/usr/src/nouveau-hpd-ddc-0.1.13"
dkms_version_dir="$tmp/var/lib/dkms/nouveau-hpd-ddc/0.1.13"
build_dir="$dkms_version_dir/build"
kernelver=7.0.0-34-generic
mkdir -p "$source_dir" "$build_dir"
ln -s "$source_dir" "$dkms_version_dir/source"

# Mirror DKMS 3.2.2: copy the persistent source to the build tree, write the
# in-progress status there through its sibling source symlink, then remove it.
touch "$source_dir/dkms.conf"
cp -a "$source_dir/." "$build_dir/"
test -d "$build_dir"
persistent_source=$(nouveau_nonstall_source_from_build "$build_dir")
test "$persistent_source" = "$source_dir"
state_file=$(nouveau_nonstall_state_file "$persistent_source" "$kernelver")
nouveau_nonstall_write_state "$state_file" build-incomplete
test "$(nouveau_nonstall_read_state "$source_dir" "$kernelver")" = build-incomplete

# Successful build completion changes the state atomically in persistent
# source storage, after which DKMS may delete its temporary build directory.
nouveau_nonstall_write_state "$state_file" patched
rm -rf -- "$build_dir"
test ! -e "$build_dir"
test "$(nouveau_nonstall_read_state "$source_dir" "$kernelver")" = patched

# A later build failure before the final-state write must remain incomplete.
mkdir -p "$build_dir"
cp -a "$source_dir/." "$build_dir/"
nouveau_nonstall_write_state "$state_file" build-incomplete
rm -rf -- "$build_dir"
test "$(nouveau_nonstall_read_state "$source_dir" "$kernelver")" = build-incomplete

printf 'PASS: DKMS state survives build-tree removal; failed build stays incomplete\n'

# Guard the real build script's ordering as well as the state helper's
# persistence behavior. Moving final-state publication before any output or
# vermagic check must make this test fail.
python3 - "$repo_root/dkms/dkms-build.sh" <<'PY'
from pathlib import Path
import sys

build = Path(sys.argv[1]).read_text()
markers = {
    "incomplete state": 'nouveau_nonstall_write_state "$nonstall_state_file" build-incomplete',
    "module build": 'make -C "$kdir"',
    "module path": 'module="$srcdir/drivers/gpu/drm/nouveau/nouveau.ko"',
    "module existence check": 'if [ ! -f "$module" ]; then',
    "output copy": 'cp -f "$module" "$out/nouveau.ko"',
    "vermagic lookup": 'vermagic=$(modinfo -F vermagic "$out/nouveau.ko" 2>/dev/null || true)',
    "vermagic validation": 'case "$vermagic" in',
    "final state": 'if ! nouveau_nonstall_write_state "$nonstall_state_file" "$nonstall_build_state"; then',
}
positions = {}
for name, marker in markers.items():
    assert build.count(marker) == 1, f"expected exactly one {name} marker"
    positions[name] = build.index(marker)

case_end = build.index("\nesac", positions["vermagic validation"]) + len("\nesac")
ordered = [
    positions["incomplete state"],
    positions["module build"],
    positions["module path"],
    positions["module existence check"],
    positions["output copy"],
    positions["vermagic lookup"],
    positions["vermagic validation"],
    case_end,
    positions["final state"],
]
assert ordered == sorted(ordered), "nonstall build-state writes/checks are out of order"
print("PASS: DKMS build script keeps final state after module, copy, and vermagic checks")
PY
