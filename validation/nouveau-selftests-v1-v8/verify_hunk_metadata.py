#!/usr/bin/env python3
"""Verify Ubuntu patch adaptations alter only unified-diff hunk headers."""

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parent
ORIGINAL = ROOT / "patches" / "original-v1-v6"
ADAPTED = ROOT / "patches" / "ubuntu-7.0.0-34"
HUNK_HEADER = re.compile(rb"^@@ -[^\n]*$", re.MULTILINE)
PAIRS = (
    (
        "0002-drm-nouveau-add-safe-live-hardware-selftest.patch",
        "0002-drm-nouveau-add-safe-live-hardware-selftest.patch",
    ),
    (
        "0003-drm-nouveau-add-instmem-bar2-lifetime-selftest.patch",
        "0003-drm-nouveau-add-instmem-bar2-lifetime-selftest.patch",
    ),
    (
        "0006-drm-nouveau-add-gk104-falcon-contract-selftest.patch",
        "0006-drm-nouveau-add-gk104-falcon-contract-selftest.patch",
    ),
)


def without_hunk_headers(path: Path) -> bytes:
    return HUNK_HEADER.sub(b"@@", path.read_bytes())


def main() -> int:
    failed = False
    for original_name, adapted_name in PAIRS:
        original = ORIGINAL / original_name
        adapted = ADAPTED / adapted_name
        matches = without_hunk_headers(original) == without_hunk_headers(adapted)
        print(f"{original_name}: {'PASS' if matches else 'FAIL'} hunk-headers-only")
        failed |= not matches
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
