from __future__ import annotations

import re
import unittest
from pathlib import Path


PATCH = Path(__file__).resolve().parents[1] / "patches" / (
    "0012-drm-nouveau-mark-bar2-teardown-phases.patch"
)


class TeardownPhasePatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = PATCH.read_text(encoding="utf-8")
        cls.additions = "\n".join(
            line[1:] for line in cls.text.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )

    def test_patch_changes_only_nv50_instmem_destructor_source(self):
        files = re.findall(r"^diff --git a/(\S+) b/\S+$", self.text, re.MULTILINE)
        self.assertEqual(files, [
            "drivers/gpu/drm/nouveau/nvkm/subdev/instmem/nv50.c",
        ])

    def test_dtor_markers_bracket_iounmap_then_vmm_put(self):
        sequence = (
            '"OBJECT_IOUNMAP_BEGIN"',
            "iounmap(map);",
            '"OBJECT_IOUNMAP_DONE"',
            '"OBJECT_VMM_PUT_BEGIN"',
            "nvkm_vmm_put(vmm, &bar);",
            '"OBJECT_VMM_PUT_DONE"',
        )
        positions = [self.text.index(item) for item in sequence]
        self.assertEqual(positions, sorted(positions))

    def test_markers_reuse_pre_teardown_range_snapshot(self):
        self.assertGreaterEqual(self.additions.count("bar2_va, bar2_len"), 5)
        self.assertIn('"resident", "none"', self.additions)
        self.assertIn('"destroyed", "none"', self.additions)
        self.assertNotIn("nvkm_memory_bar2_vma_snapshot(", self.additions)

    def test_new_calls_are_process_context_and_have_no_irq_work(self):
        for forbidden in (
            "nvkm_rd32(", "nvkm_wr32(", "nvkm_vmm_walk(",
            "kmalloc(", "kzalloc(", "GFP_KERNEL", "mutex_lock(",
            "mutex_unlock(", "down(", "down_read(", "down_write(",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.additions)

    def test_all_stage_strings_are_diagnostic_config_gated(self):
        for stage in (
            "OBJECT_IOUNMAP_BEGIN", "OBJECT_IOUNMAP_DONE",
            "OBJECT_VMM_PUT_BEGIN", "OBJECT_VMM_PUT_DONE",
            "OBJECT_VMM_PUT_SKIPPED",
        ):
            with self.subTest(stage=stage):
                self.assertIn(f'"{stage}"', self.additions)
        self.assertGreaterEqual(
            self.additions.count("#if IS_ENABLED(CONFIG_DRM_NOUVEAU_SELFTEST)"),
            3,
        )


if __name__ == "__main__":
    unittest.main()
