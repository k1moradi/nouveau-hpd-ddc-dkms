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

    def test_only_bar2_reset_and_nv50_instmem_sources_change(self):
        files = re.findall(r"^diff --git a/(\S+) b/\S+$", self.text, re.MULTILINE)
        self.assertEqual(set(files), {
            "drivers/gpu/drm/nouveau/include/nvkm/subdev/bar.h",
            "drivers/gpu/drm/nouveau/nvkm/subdev/bar/base.c",
            "drivers/gpu/drm/nouveau/nvkm/subdev/instmem/nv50.c",
        })
        self.assertNotIn("nvkm/engine/fifo/gf100.c", files)
        self.assertNotIn("nvkm/subdev/bar/gf100.c", files)

    def test_irq_additions_have_no_mmio_walk_allocation_or_sleeping_lock(self):
        for forbidden in (
            "nvkm_rd32(", "nvkm_wr32(", "nvkm_vmm_walk(",
            "kmalloc(", "kzalloc(", "GFP_KERNEL", "mutex_lock(",
            "mutex_unlock(", "down(", "down_read(", "down_write(",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.additions)

    def test_reset_generation_is_atomic_and_marked_around_existing_reset(self):
        self.assertIn("static atomic64_t nvkm_bar2_diag_gen = ATOMIC64_INIT(0);", self.additions)
        self.assertIn("return atomic64_inc_return(&nvkm_bar2_diag_gen);", self.additions)
        begin = self.text.index('"NOUVEAU_DIAG_BAR2_RESET seq=%llu gen=%llu stage=BEGIN')
        reset = self.text.index("bar->func->bar2.init(bar);", begin)
        end = self.text.index('"NOUVEAU_DIAG_BAR2_RESET seq=%llu gen=%llu stage=END', reset)
        wait = self.text.index("bar->func->bar2.wait(bar);", reset)
        self.assertLess(begin, reset)
        self.assertLess(wait, end)

    def test_default_off_read_only_load_parameter_controls_new_logging(self):
        self.assertIn("static bool nvkm_diag_bar2_map;", self.additions)
        self.assertIn("module_param_named(diag_bar2_map, nvkm_diag_bar2_map, bool, 0400);", self.additions)
        self.assertIn("return READ_ONCE(nvkm_diag_bar2_map);", self.additions)

    def test_allocation_ids_are_boot_local_monotonic_and_pointer_free(self):
        self.assertEqual(self.additions.count(
            "iobj->diag_id = atomic64_inc_return(&nv50_instobj_diag_id);"
        ), 1)
        self.assertIn("static atomic64_t nv50_instobj_diag_id = ATOMIC64_INIT(0);", self.additions)
        self.assertIn("id=%llu", self.additions)
        self.assertNotIn("%p", self.additions)
        self.assertIn("if (nvkm_bar2_diag_enabled()) {", self.additions)

    def test_cpu_reference_release_and_vma_eviction_are_separate_events(self):
        self.assertIn('"KMAP_ACTIVE"', self.additions)
        self.assertIn('"KMAP_LAST_RELEASE"', self.additions)
        self.assertIn('"VMA_EVICTING"', self.additions)
        self.assertIn('"VMA_EVICTED"', self.additions)
        self.assertNotIn('"UNMAPPED"', self.additions)
        self.assertNotIn('"RELEASED_VMA"', self.additions)
        release = self.text.index('"KMAP_LAST_RELEASE"')
        accessor_clear = self.text.index("iobj->base.memory.ptrs = NULL;", release)
        self.assertLess(release, accessor_clear)

    def test_true_eviction_brackets_iounmap_and_vmm_release(self):
        eviction_begin = self.text.index('"VMA_EVICTING"')
        remove_lru = self.text.index("list_del_init(&eobj->lru);", eviction_begin)
        iounmap = self.text.index("iounmap(emap);", remove_lru)
        vmm_put = self.text.index("nvkm_vmm_put(vmm, &ebar);", iounmap)
        eviction_done = self.text.index('"VMA_EVICTED"', vmm_put)
        self.assertLess(eviction_begin, remove_lru)
        self.assertLess(remove_lru, iounmap)
        self.assertLess(iounmap, vmm_put)
        self.assertLess(vmm_put, eviction_done)
        self.assertNotIn("nv50_instobj_bar2_vma_snapshot", self.additions)

    def test_map_source_and_reset_generations_are_logged_at_real_source_edges(self):
        self.assertIn('"MAP_VMM_MAP_OK"', self.additions)
        self.assertIn('"MAP_READY"', self.additions)
        self.assertIn('map_source=%s ', self.additions)
        self.assertIn('"new" : "cached"', self.additions)
        map_call = self.text.index("ret = nvkm_memory_map(memory, 0, vmm, bar, NULL, 0);")
        map_ok = self.text.index('"MAP_VMM_MAP_OK"', map_call)
        ioremap = self.text.index("iobj->map = ioremap_wc(", map_ok)
        self.assertLess(map_call, map_ok)
        self.assertLess(map_ok, ioremap)
        self.assertIn("if (!ret && nvkm_bar2_diag_enabled()) {", self.additions)
        self.assertIn("map_reset_gen = nvkm_bar2_diag_reset_generation();", self.additions)
        diagnostic_gate = self.text.index("if (!ret && nvkm_bar2_diag_enabled()) {", map_call)
        generation_read = self.text.index("map_reset_gen = nvkm_bar2_diag_reset_generation();", diagnostic_gate)
        map_ok = self.text.index('"MAP_VMM_MAP_OK"', generation_read)
        self.assertLess(map_call, diagnostic_gate)
        self.assertLess(diagnostic_gate, generation_read)
        self.assertLess(generation_read, map_ok)
        self.assertIn("iobj->diag_map_reset_gen = map_reset_gen;", self.additions)
        self.assertIn('"map_reset_gen=%llu "', self.additions)
        self.assertIn('"current_reset_gen=%llu refs=%d pid=%d comm=%s rc=%d"', self.additions)

    def test_allocation_id_is_assigned_only_after_object_construction(self):
        wrap = self.text.index("nv50_instobj_wrap(struct nvkm_instmem *base,")
        ref = self.text.index("iobj->ram = nvkm_memory_ref(memory);", wrap)
        identity = self.text.index("iobj->diag_id = atomic64_inc_return", ref)
        success = self.text.index("return 0;", identity)
        self.assertLess(ref, identity)
        self.assertLess(identity, success)

    def test_destroy_records_preserve_final_resident_range_before_release(self):
        begin = self.text.index('"OBJECT_DESTROYING"')
        remove_lru = self.text.index("list_del(&iobj->lru);", begin)
        iounmap = self.text.index("iounmap(map);", remove_lru)
        vmm_put = self.text.index("nvkm_vmm_put(vmm, &bar);", iounmap)
        done = self.text.index('"OBJECT_DESTROYED"', vmm_put)
        self.assertLess(begin, remove_lru)
        self.assertLess(remove_lru, iounmap)
        self.assertLess(iounmap, vmm_put)
        self.assertLess(vmm_put, done)

    def test_new_diagnostic_fields_and_strings_are_config_gated(self):
        self.assertGreaterEqual(
            self.text.count("#if IS_ENABLED(CONFIG_DRM_NOUVEAU_SELFTEST)"), 8
        )
        self.assertIn("u64 diag_map_reset_gen;", self.additions)
        self.assertIn("bool diag_map_reset_valid;", self.additions)
        self.assertIn('"NOUVEAU_DIAG_BAR2_MAP seq=%llu', self.additions)
        self.assertIn('"NOUVEAU_DIAG_BAR2_RESET seq=%llu', self.additions)


if __name__ == "__main__":
    unittest.main()
