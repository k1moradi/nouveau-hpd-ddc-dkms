#!/usr/bin/env python3
"""CPU-only contract tests for the read-only v3 BAR2 VMA diagnostic patch."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[3]
PATCH = (
    REPOSITORY
    / "validation/nouveau-bar2-vma-diagnostic/patches/"
    / "0010-drm-nouveau-correlate-instmem-vma-selftest.patch"
)
BASE_ROOT_TEXT = os.environ.get("NOUVEAU_VMA_BASE_ROOT", "")
BASE_ROOT = Path(BASE_ROOT_TEXT).resolve() if BASE_ROOT_TEXT else None
PATCHED_FILES = (
    "drivers/gpu/drm/nouveau/include/nvkm/core/memory.h",
    "drivers/gpu/drm/nouveau/nouveau_debugfs.c",
    "drivers/gpu/drm/nouveau/nvkm/engine/fifo/gf100.c",
    "drivers/gpu/drm/nouveau/nvkm/subdev/instmem/nv50.c",
)
BASE_SHA256 = {
    "drivers/gpu/drm/nouveau/include/nvkm/core/memory.h":
        "dbf6854812e3a1154f429b7a0d2e9ed628f9e34b4942c090d9953f760631c76c",
    "drivers/gpu/drm/nouveau/nouveau_debugfs.c":
        "5f9d6fbb928f72784757db688c8be6ee823d6485095ac4eea1b1074e0c0a8e6c",
    "drivers/gpu/drm/nouveau/nvkm/engine/fifo/gf100.c":
        "1887dd37924288d9a641ac7ea6279e02f2436d42f79943eeba5b37ce28074b95",
    "drivers/gpu/drm/nouveau/nvkm/subdev/instmem/nv50.c":
        "19060f6a69bfae03044443d310cbe15806f161f2bf1c0f5b24e72f704796894e",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def function_body(source: str, name: str) -> str:
    match = re.search(r"\b" + re.escape(name) + r"\s*\(", source)
    if not match:
        raise AssertionError(f"function not found: {name}")
    opening = source.index("{", match.end())
    depth = 0
    state = "code"
    index = opening
    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if state == "line_comment":
            if char == "\n":
                state = "code"
        elif state == "block_comment":
            if char == "*" and following == "/":
                state = "code"
                index += 1
        elif state in {"string", "char"}:
            terminator = '"' if state == "string" else "'"
            if char == "\\":
                index += 1
            elif char == terminator:
                state = "code"
        elif char == "/" and following == "/":
            state = "line_comment"
            index += 1
        elif char == "/" and following == "*":
            state = "block_comment"
            index += 1
        elif char == '"':
            state = "string"
        elif char == "'":
            state = "char"
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[opening:index + 1]
        index += 1
    raise AssertionError(f"unterminated function: {name}")


class Bar2VmaPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if BASE_ROOT is None:
            raise RuntimeError("set NOUVEAU_VMA_BASE_ROOT to the pinned v1-v8 source tree")
        if not PATCH.is_file():
            raise RuntimeError(f"missing diagnostic patch: {PATCH}")
        cls.temporary = tempfile.TemporaryDirectory(prefix="nouveau-vma-contract-")
        cls.root = Path(cls.temporary.name)
        for relative in PATCHED_FILES:
            source = BASE_ROOT / relative
            if not source.is_file():
                raise RuntimeError(f"missing pinned baseline source: {source}")
            actual = sha256(source)
            if actual != BASE_SHA256[relative]:
                raise RuntimeError(
                    f"baseline source hash mismatch for {relative}: {actual}"
                )
            target = cls.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

        subprocess.run(["git", "init", "-q"], cwd=cls.root, check=True)
        subprocess.run(
            ["git", "apply", "--check", str(PATCH)],
            cwd=cls.root,
            check=True,
        )
        subprocess.run(["git", "apply", str(PATCH)], cwd=cls.root, check=True)
        cls.memory = (cls.root / PATCHED_FILES[0]).read_text(encoding="utf-8")
        cls.debugfs = (cls.root / PATCHED_FILES[1]).read_text(encoding="utf-8")
        cls.fifo = (cls.root / PATCHED_FILES[2]).read_text(encoding="utf-8")
        cls.instmem = (cls.root / PATCHED_FILES[3]).read_text(encoding="utf-8")
        cls.fifo_base = (BASE_ROOT / PATCHED_FILES[2]).read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_patch_touches_only_process_context_and_fault_decode_files(self) -> None:
        paths = re.findall(r"^diff --git a/(\S+) b/(\S+)$", PATCH.read_text(), re.M)
        self.assertEqual({left for left, _right in paths}, set(PATCHED_FILES))
        self.assertTrue(all(left == right for left, right in paths))

    def test_memory_accessor_is_diagnostic_build_only(self) -> None:
        self.assertRegex(
            self.memory,
            r"#if IS_ENABLED\(CONFIG_DRM_NOUVEAU_SELFTEST\)\s+"
            r"int \(\*bar2_vma_snapshot\)",
        )
        self.assertIn(
            "nvkm_memory_bar2_vma_snapshot(struct nvkm_memory *memory,",
            self.memory,
        )
        self.assertIn("#if IS_ENABLED(CONFIG_DRM_NOUVEAU_SELFTEST)", self.memory)

    def test_snapshot_requires_a_pinned_active_map_and_only_reads_vma(self) -> None:
        body = function_body(self.instmem, "nv50_instobj_bar2_vma_snapshot")
        self.assertIn("if (!addr || !size)", body)
        lock = body.index("mutex_lock(&imem->mutex)")
        refcheck = body.index("refcount_read(&iobj->maps) > 0")
        unlock = body.index("mutex_unlock(&imem->mutex)")
        self.assertLess(lock, refcheck)
        self.assertLess(refcheck, unlock)
        self.assertIn("iobj->bar->addr", body)
        self.assertIn("iobj->bar->size", body)
        for forbidden in (
            "nv50_instobj_acquire(",
            "nv50_instobj_release(",
            "nvkm_memory_bar2(",
            "nvkm_vmm_get(",
            "nvkm_vmm_put(",
            "ioremap",
            "nvkm_rd32(",
        ):
            self.assertNotIn(forbidden, body)

    def test_debugfs_helper_avoids_kernel_current_macro_identifier(self) -> None:
        body = function_body(
            self.debugfs,
            "nouveau_debugfs_selftest_instmem_log_vma",
        )
        self.assertRegex(body, r"\bbool current_vma = false;")
        self.assertNotRegex(body, r"\bbool current =")

    def test_vma_logs_use_only_current_or_explicitly_last_observed_ranges(self) -> None:
        logger = function_body(
            self.debugfs,
            "nouveau_debugfs_selftest_instmem_log_vma",
        )
        self.assertIn("nvkm_memory_bar2_vma_snapshot(", logger)
        self.assertIn('"current"', logger)
        self.assertIn('"last_observed"', logger)
        self.assertIn('"unavailable"', logger)
        for forbidden in ("nvkm_kmap(", "nvkm_memory_bar2(", "nvkm_rd32(", "nvkm_ro32("):
            self.assertNotIn(forbidden, logger)

    def test_final_map_release_is_never_followed_by_snapshot_query(self) -> None:
        body = function_body(
            self.debugfs,
            "nouveau_debugfs_selftest_instmem_show",
        )
        reacquire = body.index("reacquired_map = nvkm_kmap(memory)")
        final_done = body.rfind("nvkm_done(memory);", 0, reacquire)
        released_log = body.index('"released"', final_done, reacquire)
        released_call = body.rfind(
            "nouveau_debugfs_selftest_instmem_log_vma(", final_done, released_log
        )
        between = body[final_done:reacquire]
        self.assertGreaterEqual(released_log, released_call)
        self.assertIn("false, &vma", between)
        self.assertNotIn("true, &vma", between)
        self.assertNotIn("nvkm_memory_bar2_vma_snapshot", between)

        cleanup = body[body.index("out_memory:"):body.index("out_runtime_pm:")]
        self.assertNotIn("true, &vma", cleanup)
        self.assertIn('"destroying"', cleanup)
        self.assertIn('"destroyed"', cleanup)
        destroyed = cleanup.index('"destroyed"')
        self.assertIn("NULL, false, &vma", cleanup[destroyed:])

    def test_v3_stage_order_and_test_id_are_recorded(self) -> None:
        body = function_body(
            self.debugfs,
            "nouveau_debugfs_selftest_instmem_show",
        )
        self.assertIn("atomic64_inc_return(&nouveau_selftest_instmem_test_id)", body)
        self.assertIn("id=%llu stage=start", body)
        stages = (
            '"allocated"',
            '"kmap_acquired"',
            '"nested_acquired"',
            '"nested_ref_remaining"',
            '"released"',
            '"reacquired"',
            '"verified"',
            '"destroying"',
            '"destroyed"',
        )
        positions = [body.index(stage) for stage in stages]
        self.assertEqual(positions, sorted(positions))

    def test_fault_record_precedes_recovery_without_extra_mmio(self) -> None:
        body = function_body(self.fifo, "gf100_fifo_intr_mmu_fault_unit")
        base_body = function_body(self.fifo_base, "gf100_fifo_intr_mmu_fault_unit")
        pattern = r"\bnvkm_rd32\s*\("
        self.assertEqual(len(re.findall(pattern, base_body)), 4)
        self.assertEqual(len(re.findall(pattern, body)), 4)
        log = body.index("NOUVEAU_DIAG_BAR2_FAULT")
        recovery = body.index("nvkm_fifo_fault(fifo, &info)")
        self.assertLess(log, recovery)
        self.assertIn("if (unit == 0x05)", body)
        for field in ("unit", "inst", "valo", "vahi", "type"):
            self.assertIn(field, body[log:recovery])
        for forbidden in (
            "mutex_lock(",
            "mutex_unlock(",
            "kmalloc(",
            "msleep(",
            "schedule(",
            "nvkm_vmm_",
            "nvkm_chan_get_inst(",
        ):
            self.assertNotIn(forbidden, body[log:recovery])


if __name__ == "__main__":
    unittest.main(verbosity=2)
