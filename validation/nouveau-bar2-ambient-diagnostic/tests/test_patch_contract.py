from __future__ import annotations

import re
import unittest
from pathlib import Path


PATCH = Path(__file__).resolve().parents[1] / "patches" / (
    "0011-drm-nouveau-trace-ambient-bar2-lifetimes.patch"
)


class PatchContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = PATCH.read_text(encoding="utf-8")
        cls.additions = "\n".join(
            line[1:] for line in cls.text.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )

    def test_only_process_context_bar_and_instmem_files_change(self):
        files = re.findall(r"^diff --git a/(\S+) b/\S+$", self.text, re.MULTILINE)
        self.assertEqual(set(files), {
            "drivers/gpu/drm/nouveau/include/nvkm/subdev/bar.h",
            "drivers/gpu/drm/nouveau/nvkm/subdev/bar/base.c",
            "drivers/gpu/drm/nouveau/nvkm/subdev/instmem/nv50.c",
        })
        self.assertFalse(any("fifo/" in path or path.endswith("bar/gf100.c") for path in files))

    def test_patch_adds_no_irq_mmio_walk_lock_or_allocation(self):
        for forbidden in (
            "nvkm_rd32(", "nvkm_wr32(", "nvkm_vmm_walk(",
            "mutex_lock(", "kmalloc(", "kzalloc(", "GFP_KERNEL",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.additions)

    def test_diagnostic_identity_is_monotonic_and_not_a_pointer(self):
        self.assertEqual(self.additions.count(
            "iobj->diag_id = atomic64_inc_return(&nv50_instobj_diag_id);"
        ), 1)
        self.assertNotIn("%p", self.additions)
        self.assertIn("id=%llu", self.additions)

    def test_only_first_and_last_access_edges_are_logged(self):
        self.assertEqual(self.additions.count('"FIRST_MAP_ACTIVE"'), 1)
        self.assertEqual(self.additions.count('"LAST_MAP_RELEASE"'), 1)
        self.assertNotIn("NESTED_MAP", self.additions)

    def test_final_release_is_logged_before_accessors_are_cleared(self):
        release = self.text.index('"LAST_MAP_RELEASE"')
        clear = self.text.index("iobj->base.memory.ptrs = NULL;", release)
        self.assertLess(release, clear)
        self.assertNotIn("nv50_instobj_bar2_vma_snapshot", self.additions)

    def test_fault_irq_handler_files_are_not_modified(self):
        self.assertNotIn("nvkm/subdev/bar/gf100.c", self.text)
        self.assertNotIn("nvkm/engine/fifo/gf100.c", self.text)
        self.assertNotIn("irq", self.additions.lower())

    def test_last_release_captures_stored_vma_before_pointer_clear(self):
        self.assertIn("const bool has_bar = !!iobj->bar;", self.additions)
        self.assertIn("const bool has_map = !!iobj->map;", self.additions)
        self.assertIn("has_bar ? iobj->bar->addr : 0", self.additions)
        self.assertIn("has_bar ? iobj->bar->size : 0", self.additions)
        self.assertNotIn("nv50_instobj_bar2_vma_snapshot", self.additions)

    def test_instrumentation_is_diagnostic_config_only(self):
        self.assertGreaterEqual(
            self.text.count("#if IS_ENABLED(CONFIG_DRM_NOUVEAU_SELFTEST)"), 5
        )
        self.assertIn("module_param_named(diag_bar2_map", self.additions)


if __name__ == "__main__":
    unittest.main()
