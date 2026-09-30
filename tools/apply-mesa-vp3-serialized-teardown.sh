#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    printf 'Usage: %s <Mesa 26.0.8 source tree with native-clear and VP3 diagnostics applied>\n' "$0" >&2
    exit 2
fi

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
source_dir=$(cd -- "$1" && pwd -P)
cd -- "$source_dir"

vp3_source=src/gallium/drivers/nouveau/nouveau_vp3_video.c
if ! grep -Fq 'phase=channel-identity' "$vp3_source" ||
   ! grep -Fq 'NOUVEAU_DIAG_VA_SURFACE mono_ns=' "$vp3_source"; then
    echo 'expected timestamped VP3 channel diagnostics are missing; apply the diagnostic patch set first' >&2
    exit 2
fi

patch --dry-run --batch --fuzz=0 --forward -p1 \
    < "$repo_dir/patches/mesa/nouveau-vp3-serialize-channel-teardown.patch"
patch --batch --fuzz=0 --forward -p1 \
    < "$repo_dir/patches/mesa/nouveau-vp3-serialize-channel-teardown.patch"
python3 "$repo_dir/tests/test-mesa-vp3-serialized-teardown.py" "$source_dir"
