#!/usr/bin/env python3
"""Check the follow-up patch keeps BAR2 lifecycle/access budgets separate."""

from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "patches/diagnostic/gk104-bar2-map-rate-limit-isolation.patch"
BASE_PATCH = ROOT / "patches/diagnostic/gk104-bar2-instmem-map-trace.patch"
BUILDER = ROOT / "dkms/dkms-build.sh"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FAIL: {message}")


patch_text = PATCH.read_text()
base_patch_text = BASE_PATCH.read_text()
builder_text = BUILDER.read_text()

require(
    "+static DEFINE_RATELIMIT_STATE(nv50_instobj_diag_map_rs, HZ, 8);"
    in patch_text,
    "follow-up must add its own bounded map-lifecycle ratelimit",
)
require(
    "-\tif (!pages || !__ratelimit(&nv50_instobj_diag_access_rs))\n"
    "+\tif (!pages || !__ratelimit(&nv50_instobj_diag_map_rs))"
    in patch_text,
    "map lifecycle must switch from the access budget to its own budget",
)
require(
    "nv50_instobj_diag_access_rs" in base_patch_text,
    "base BAR2 diagnostic must retain the separate access budget",
)
require(
    '+\t\t "NOUVEAU_DIAG_BAR2_MAP budget=lifecycle stage=%s pages=0x%x object=%p "'
    in patch_text,
    "map records must identify the lifecycle budget in the built module/log",
)
base_patch_at = builder_text.index(
    '"$PWD/patches/diagnostic/gk104-bar2-instmem-map-trace.patch"'
)
budget_marker_at = builder_text.index(
    '"$PWD/diagnostic-bar2-map-budget.enabled"'
)
budget_patch_at = builder_text.index(
    '"$PWD/patches/diagnostic/gk104-bar2-map-rate-limit-isolation.patch"'
)
require(
    base_patch_at < budget_marker_at < budget_patch_at,
    "the optional follow-up must apply after the base BAR2 diagnostic",
)
require(
    "patch --fuzz=0" in builder_text[budget_marker_at:budget_patch_at],
    "the follow-up must use strict zero-fuzz application",
)

if len(sys.argv) > 2:
    raise SystemExit(f"Usage: {sys.argv[0]} [diag2-patched-nv50.c]")

if len(sys.argv) == 2:
    source = Path(sys.argv[1]).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="nouveau-bar2-budget-test-") as tmp:
        tree = Path(tmp)
        relative = Path("drivers/gpu/drm/nouveau/nvkm/subdev/instmem/nv50.c")
        target = tree / relative
        target.parent.mkdir(parents=True)
        shutil.copyfile(source, target)
        subprocess.run(
            ["patch", "--fuzz=0", "--dry-run", "-p1", "-d", str(tree)],
            input=PATCH.read_text(),
            text=True,
            check=True,
        )
        subprocess.run(
            ["patch", "--fuzz=0", "-p1", "-d", str(tree)],
            input=PATCH.read_text(),
            text=True,
            check=True,
        )
        result = target.read_text()

    map_match = re.search(
        r"static void\s+nv50_instobj_diag_map\(.*?\n}\n", result, re.S
    )
    access_match = re.search(
        r"static void\s+nv50_instobj_diag_access\(.*?\n}\n", result, re.S
    )
    require(map_match is not None, "patched NV50 source has no map diagnostic")
    require(access_match is not None, "patched NV50 source has no access diagnostic")
    require(
        "__ratelimit(&nv50_instobj_diag_map_rs)" in map_match.group(0),
        "map lifecycle records do not use the independent map budget",
    )
    require(
        "nv50_instobj_diag_access_rs" not in map_match.group(0),
        "map lifecycle still consumes the access budget",
    )
    require(
        "__ratelimit(&nv50_instobj_diag_access_rs)" in access_match.group(0),
        "access records no longer use the bounded access budget",
    )
    require(
        "nv50_instobj_diag_map_rs" not in access_match.group(0),
        "access records consume the lifecycle budget",
    )

print("PASS: BAR2 lifecycle and access diagnostics have separate bounded budgets.")
