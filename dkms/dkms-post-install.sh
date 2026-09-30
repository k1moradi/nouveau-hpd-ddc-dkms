#!/bin/bash
set -euo pipefail
kernelver="${1:-${kernelver:-}}"
[ -n "$kernelver" ] || exit 0
if command -v depmod >/dev/null 2>&1; then
    depmod -a "$kernelver" || true
fi
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
"$script_dir/refresh-initramfs.sh" "$kernelver"
