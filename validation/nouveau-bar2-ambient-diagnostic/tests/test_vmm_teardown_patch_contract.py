from __future__ import annotations

import re
import unittest
from pathlib import Path


PATCH = Path(__file__).resolve().parents[1] / "patches" / (
    "0015-drm-nouveau-correlate-bar2-vmm-pte-teardown.patch"
)


class VmmTeardownPatchContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = PATCH.read_text(encoding="utf-8")
        cls.additions = "\n".join(
            line[1:] for line in cls.text.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )

    def test_patch_scope_is_vmm_teardown_and_memory_identity_only(self):
        files = set(re.findall(r"^diff --git a/(\S+) b/\S+$", self.text, re.MULTILINE))
        self.assertEqual(files, {
            "drivers/gpu/drm/nouveau/include/nvkm/core/memory.h",
            "drivers/gpu/drm/nouveau/include/nvkm/subdev/bar.h",
            "drivers/gpu/drm/nouveau/nvkm/subdev/bar/base.c",
            "drivers/gpu/drm/nouveau/nvkm/subdev/instmem/nv50.c",
            "drivers/gpu/drm/nouveau/nvkm/subdev/mmu/vmm.c",
            "drivers/gpu/drm/nouveau/nvkm/subdev/mmu/vmm.h",
        })
        self.assertNotIn("nvkm/engine/fifo/gf100.c", files)
        self.assertNotIn("nouveau_abi16.c", files)

    def test_vmm_events_are_diagnostic_gated_and_use_stable_operation_ids(self):
        self.assertIn("#if IS_ENABLED(CONFIG_DRM_NOUVEAU_SELFTEST)", self.additions)
        self.assertIn("atomic64_inc_return(&nvkm_bar2_diag_vmm_op)", self.additions)
        self.assertIn("NOUVEAU_DIAG_BAR2_VMM seq=%llu op=%llu target_id=%llu", self.additions)
        self.assertNotIn("%p", self.additions)
        self.assertIn("char message[512];", self.additions)

    def test_pte_identity_comes_from_page_table_memory_and_unknown_stays_zero(self):
        self.assertIn("nvkm_memory_diag_id(pt->memory)", self.additions)
        self.assertRegex(self.additions, r"nvkm_memory_diag_id\(pt->memory\)\s*:\s*0")
        self.assertIn("u32 diag_ptes = ptes;", self.additions)
        self.assertIn("nvkm_vmm_diag_pte(it, NVKM_BAR2_VMM_PT_LAST_REF_BEGIN", self.additions)
        self.assertIn("nvkm_vmm_diag_pte(it, NVKM_BAR2_VMM_PT_LAST_REF_DONE", self.additions)

    def test_events_bracket_pte_chunk_flush_and_vma_release(self):
        for phase in (
            "PTE_UNMAP_BEGIN", "PTE_WRITE_DONE", "PT_LAST_REF_BEGIN",
            "PT_LAST_REF_DONE", "VMM_FLUSH_BEGIN", "VMM_FLUSH_DONE",
            "VMA_RANGE_RELEASE", "VMM_PUT_BEGIN", "VMM_PUT_DONE",
        ):
            with self.subTest(phase=phase):
                self.assertIn(f'"{phase}"', self.additions)
        pte_begin = self.text.index("NVKM_BAR2_VMM_PTE_UNMAP_BEGIN")
        pte_done = self.text.index("NVKM_BAR2_VMM_PTE_WRITE_DONE", pte_begin)
        self.assertLess(pte_begin, pte_done)
        flush_begin = self.text.index("NVKM_BAR2_VMM_FLUSH_BEGIN")
        flush_done = self.text.index("NVKM_BAR2_VMM_FLUSH_DONE", flush_begin)
        self.assertLess(flush_begin, flush_done)

    def test_threaded_diagnostic_context_reaches_existing_vmm_put_path(self):
        self.assertIn("nvkm_vmm_put_diag(vmm, &ebar, &evmm_diag);", self.additions)
        self.assertIn("nvkm_vmm_put_diag(vmm, &bar, &vmm_diag);", self.additions)
        self.assertIn("nvkm_vmm_ptes_unmap_put_diag(vmm, &page[refd]", self.additions)
        self.assertIn("nvkm_vmm_put_locked_impl(vmm, vma, diag);", self.additions)

    def test_patch_does_not_add_irq_mmio_walk_allocation_or_sleeping_lock(self):
        for forbidden in (
            "nvkm_rd32(", "nvkm_wr32(", "nvkm_vmm_walk(", "kmalloc(",
            "kzalloc(", "kvcalloc(", "GFP_KERNEL",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.additions)


if __name__ == "__main__":
    unittest.main()
