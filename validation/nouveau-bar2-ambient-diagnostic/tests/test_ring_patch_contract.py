from __future__ import annotations

import re
import unittest
from pathlib import Path


PATCH = Path(__file__).resolve().parents[1] / "patches" / (
    "0013-drm-nouveau-capture-ambient-bar2-events.patch"
)
UNWIND_PATCH = Path(__file__).resolve().parents[1] / "patches" / (
    "0014-drm-nouveau-unwind-ambient-bar2-ring-init.patch"
)


class AmbientRingPatchContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = PATCH.read_text(encoding="utf-8")
        cls.additions = "\n".join(
            line[1:] for line in cls.text.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        cls.removals = "\n".join(
            line[1:] for line in cls.text.splitlines()
            if line.startswith("-") and not line.startswith("---")
        )

    def test_patch_scope_excludes_fault_irq_and_application_sources(self):
        files = set(re.findall(r"^diff --git a/(\S+) b/\S+$", self.text, re.MULTILINE))
        self.assertEqual(files, {
            "drivers/gpu/drm/nouveau/include/nvkm/subdev/bar.h",
            "drivers/gpu/drm/nouveau/nouveau_debugfs.c",
            "drivers/gpu/drm/nouveau/nouveau_drm.c",
            "drivers/gpu/drm/nouveau/nvkm/subdev/bar/base.c",
            "drivers/gpu/drm/nouveau/nvkm/subdev/instmem/nv50.c",
        })
        self.assertNotIn("nvkm/engine/fifo/gf100.c", files)
        self.assertNotIn("mpv", self.additions.lower())
        self.assertNotIn("ffplay", self.additions.lower())

    def test_ring_is_preallocated_only_when_diagnostic_is_enabled(self):
        self.assertIn("#define NVKM_BAR2_DIAG_RING_ORDER 18", self.additions)
        self.assertIn("#define NVKM_BAR2_DIAG_RING_SIZE BIT(NVKM_BAR2_DIAG_RING_ORDER)", self.additions)
        self.assertIn("kvcalloc(NVKM_BAR2_DIAG_RING_SIZE", self.additions)
        self.assertIn("if (!nvkm_bar2_diag_enabled())", self.additions)
        init = self.text.index("nvkm_bar2_diag_ring_init(void)")
        alloc = self.text.index("kvcalloc(NVKM_BAR2_DIAG_RING_SIZE", init)
        self.assertLess(init, alloc)
        init_hunk = self.text.index("@@ -1463,0 +1465,6 @@ nouveau_drm_init")
        ring_init = self.text.index("nvkm_bar2_diag_ring_init();", init_hunk)
        self.assertLess(init_hunk, ring_init)

    def test_event_writer_reserves_fills_and_publishes_without_sleeping_work(self):
        start = self.additions.index("\nvoid\nnvkm_bar2_diag_ring_record(const char *message)")
        end = self.additions.index("\nu64\nnvkm_bar2_diag_ring_capacity(void)", start)
        body = self.additions[start:end]
        reserve = body.index("atomic64_fetch_inc(&nvkm_bar2_diag_ring_next)")
        timestamp = body.index("ktime_get_mono_fast_ns()")
        copy = body.index("strscpy(event->message")
        publish = body.index("smp_store_release(&event->published")
        self.assertLess(reserve, timestamp)
        self.assertLess(timestamp, copy)
        self.assertLess(copy, publish)
        for forbidden in (
            "kmalloc(", "kzalloc(", "kvcalloc(", "GFP_KERNEL", "mutex_lock(",
            "down(", "nvkm_rd32(", "nvkm_wr32(", "nvkm_vmm_walk(",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, body)
        self.assertIn("atomic64_inc(&nvkm_bar2_diag_ring_drop_count)", body)

    def test_snapshot_is_a_cutoff_bound_prefix_and_reports_live_footer(self):
        start = self.text.index("nouveau_bar2_diag_seq_start(struct seq_file *m")
        end = self.text.index("nouveau_bar2_diag_seq_next(", start)
        body = self.text[start:end]
        cutoff = body.index("ktime_get_mono_fast_ns()")
        head = body.index("nvkm_bar2_diag_ring_head()")
        self.assertLess(cutoff, head)
        self.assertIn("cutoff_ns=%llu", self.additions)
        self.assertIn("NOUVEAU_DIAG_BAR2_RING_END head=%llu dropped=%llu", self.additions)
        self.assertIn("NOUVEAU_DIAG_BAR2_RING_GAP index=%llu", self.additions)
        self.assertIn("smp_load_acquire(&event->published)", self.additions)

    def test_lifecycle_records_use_ring_and_have_room_for_longest_format(self):
        self.assertIn("#define NVKM_BAR2_DIAG_MESSAGE_SIZE 512", self.additions)
        self.assertIn("char message[512];", self.additions)
        self.assertGreaterEqual(self.additions.count("nvkm_bar2_diag_ring_record(message);"), 3)
        self.assertEqual(self.additions.count("NOUVEAU_DIAG_BAR2_RESET seq=%llu"), 2)
        self.assertGreaterEqual(self.removals.count("nvkm_info("), 3)

    def test_debugfs_dump_is_read_only(self):
        self.assertIn('debugfs_create_file("ambient_bar2_events", 0400', self.additions)
        self.assertIn('debugfs_create_file("ambient_bar2_status", 0400', self.additions)
        self.assertIn(".read = seq_read", self.additions)
        self.assertNotIn(".write =", self.additions)

    def test_status_probe_reports_stable_headroom_without_dumping_ring(self):
        start = self.text.index("nouveau_bar2_diag_status_show(struct seq_file *m")
        end = self.text.index("nouveau_bar2_diag_status_open(", start)
        body = self.text[start:end]
        self.assertIn("NOUVEAU_DIAG_BAR2_RING_STATUS", body)
        self.assertIn("head_before=%llu head_after=%llu", body)
        self.assertIn("dropped_before=%llu dropped_after=%llu", body)
        self.assertIn("nvkm_bar2_diag_ring_head()", body)
        self.assertNotIn("nvkm_bar2_diag_ring_read(", body)

    def test_ring_unwind_quiesces_registered_platform_producer_before_free(self):
        text = UNWIND_PATCH.read_text(encoding="utf-8")
        files = set(re.findall(r"^diff --git a/(\S+) b/\S+$", text, re.MULTILINE))
        self.assertEqual(files, {"drivers/gpu/drm/nouveau/nouveau_drm.c"})
        additions = "\n".join(
            line[1:] for line in text.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )

        self.assertIn("static bool nouveau_platform_driver_registered;", additions)
        self.assertIn(
            "platform_driver_register(&nouveau_platform_driver) == 0;",
            additions,
        )
        self.assertIn("if (nouveau_platform_driver_registered) {", additions)

        fini_start = text.index("nouveau_drm_fini(void)")
        fini_end = text.index("nouveau_drm_init(void)", fini_start)
        fini = text[fini_start:fini_end]
        unregister = fini.index("platform_driver_unregister(&nouveau_platform_driver)")
        debugfs_remove = fini.index("nouveau_module_debugfs_fini()")
        ring_free = fini.index("nvkm_bar2_diag_ring_fini()")
        self.assertLess(unregister, debugfs_remove)
        self.assertLess(debugfs_remove, ring_free)
        self.assertIn("mmu_notifier_synchronize()", fini)

        pci_failure = text.index("ret = pci_register_driver(&nouveau_drm_pci_driver);")
        failure_end = text.index("return ret;", pci_failure)
        self.assertIn("nouveau_drm_fini();", text[pci_failure:failure_end])

        exit_start = text.index("nouveau_drm_exit(void)")
        exit_end = text.index("module_init(nouveau_drm_init)", exit_start)
        exit_body = text[exit_start:exit_end]
        self.assertLess(
            exit_body.index("pci_unregister_driver(&nouveau_drm_pci_driver)"),
            exit_body.index("nouveau_drm_fini();"),
        )


if __name__ == "__main__":
    unittest.main()
