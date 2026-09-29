#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    printf 'Usage: %s <Mesa 26.0.8 source tree after diagnostic patch>\n' "$0" >&2
    exit 2
fi

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
source_dir=$(cd -- "$1" && pwd -P)
cd -- "$source_dir"

sha256sum --check --strict - <<'HASHES'
37a37b121a10ac02c346e1833ac43a2a613473cb5a7eb11f3676f24382769fb2  src/gallium/drivers/nouveau/nvc0/nvc0_resource.c
16ca564524b529401a12fb890862035a6fdbf3ba78f667a65152c098b6ef83fe  src/gallium/drivers/nouveau/nvc0/nvc0_miptree.c
e58cbcc06ac919cec228b41a3c53d545dfd360455a76536b1dc4bc6986b1114a  src/gallium/drivers/nouveau/nv50/nv50_resource.c
38e0a3299539a38213471f00cb0bc9a028206fd895e376f81a39203038df4a87  src/gallium/drivers/nouveau/nv50/nv50_miptree.c
4b0853045568ef81ff724cb675ec6d094933c98d2b2a3a40542b810e79589418  src/gallium/frontends/va/surface.c
HASHES

patch --dry-run --fuzz=0 --forward -p1 \
    < "$repo_dir/patches/mesa/nvc0-create-surface-for-va-clear.patch"
patch --batch --fuzz=0 --forward -p1 \
    < "$repo_dir/patches/mesa/nvc0-create-surface-for-va-clear.patch"
python3 "$repo_dir/tests/test-mesa-va-clear-surface.py" "$source_dir"
