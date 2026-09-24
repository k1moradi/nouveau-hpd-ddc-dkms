#!/bin/bash
set -euo pipefail
rm -rf output .work
# Remove only stale temporary trees created by this project.  Never clear /tmp.
find /tmp -maxdepth 1 -type d -name 'nouveau-hpd-ddc.*' -exec rm -rf -- {} + 2>/dev/null || true
