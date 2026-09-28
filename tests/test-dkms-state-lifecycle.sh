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
