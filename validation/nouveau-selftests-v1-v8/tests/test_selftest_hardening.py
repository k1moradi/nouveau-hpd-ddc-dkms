import os
from pathlib import Path
import re
import unittest

SOURCE_PATH = Path(os.environ.get("NOUVEAU_SELFTEST_SOURCE", ""))
SOURCE = SOURCE_PATH.read_text() if SOURCE_PATH.is_file() else ""


def function_body(name: str) -> str:
    marker = name + "("
    start = SOURCE.index(marker)
    opening = SOURCE.index("{", start)
    depth = 0
    in_string = False
    escaped = False
    for index in range(opening, len(SOURCE)):
        char = SOURCE[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return SOURCE[opening:index + 1]
    raise AssertionError(f"unterminated function body: {name}")


@unittest.skipUnless(
    SOURCE_PATH.is_file(),
    "set NOUVEAU_SELFTEST_SOURCE to the candidate nouveau_debugfs.c",
)
class SelftestHardeningTests(unittest.TestCase):
    def test_falcon_base_validation_precedes_mmio_reads(self) -> None:
        body = function_body("nouveau_selftest_falcon_snapshot")
        check = body.index("if (falcon->addr != desc->base)")
        first_read = body.index("nvkm_rd32(")
        self.assertLess(check, first_read)
        self.assertIn("base = falcon->addr;", body)
        self.assertNotRegex(body, r"nvkm_rd32\(device,\s*desc->base")

    def test_safe_selftest_populates_runlist_cache_first(self) -> None:
        body = function_body("nouveau_debugfs_selftest_safe_show")
        initialize = body.index("nvif_fifo_runlist(")
        inspect = body.index("device->runlists <= 0 || !device->runlist")
        self.assertLess(initialize, inspect)

    def test_fifo_selftest_populates_runlist_cache_before_inspection(self) -> None:
        body = function_body("nouveau_debugfs_selftest_fifo_show")
        get_pm = body.index("pm_runtime_get_sync(")
        query = body.index("runm = nvif_fifo_runlist(")
        check_mask = body.index("if (!runm)")
        create_channel = body.index("nouveau_channel_new(")
        self.assertLess(get_pm, query)
        self.assertLess(query, check_mask)
        self.assertLess(check_mask, create_channel)
        self.assertNotIn("device->runlists", body)

    def test_safe_selftest_holds_runtime_pm_for_lazy_runlist_query(self) -> None:
        body = function_body("nouveau_debugfs_selftest_safe_show")
        get_pm = body.index("pm_runtime_get_sync(")
        query = body.index("nvif_fifo_runlist(")
        inspect = body.index("device->runlists <= 0 || !device->runlist")
        put_pm = body.index("pm_runtime_put_autosuspend(", query)
        unlock = body.index("mutex_unlock(&nouveau_selftest_lock)")
        self.assertLess(get_pm, query)
        self.assertLess(query, inspect)
        self.assertLess(inspect, put_pm)
        self.assertLess(put_pm, unlock)
        pm_failure = re.search(
            r"if \(ret < 0 && ret != -EACCES\) \{(.*?)\n\t\}",
            body,
            re.DOTALL,
        )
        self.assertIsNotNone(pm_failure)
        failure_body = pm_failure.group(1)
        self.assertLess(
            failure_body.index("pm_runtime_put_autosuspend("),
            failure_body.index("goto done;"),
        )

    def test_fifo_releases_runtime_pm_and_global_gate_after_lazy_query(self) -> None:
        body = function_body("nouveau_debugfs_selftest_fifo_show")
        query = body.index("runm = nvif_fifo_runlist(")
        failed_mask = body.index("if (!runm)")
        failed_path = body[failed_mask:body.index("ret = nouveau_channel_new(", failed_mask)]
        self.assertLess(query, failed_mask)
        self.assertIn("goto out_runtime_pm;", failed_path)
        self.assertIn("pm_runtime_put_autosuspend(drm->dev->dev);", body)
        pm_failure = re.search(
            r"if \(ret < 0 && ret != -EACCES\) \{(.*?)\n\t\}",
            body,
            re.DOTALL,
        )
        self.assertIsNotNone(pm_failure)
        failure_body = pm_failure.group(1)
        self.assertLess(
            failure_body.index("pm_runtime_put_autosuspend("),
            failure_body.index("goto out_summary;"),
        )
        self.assertLess(
            body.index("pm_runtime_put_autosuspend(drm->dev->dev);"),
            body.index("mutex_unlock(&nouveau_selftest_lock)"),
        )

    def test_one_shared_selftest_mutex_is_defined(self) -> None:
        definitions = re.findall(r"static DEFINE_MUTEX\(([^)]+)\);", SOURCE)
        self.assertEqual(definitions, ["nouveau_selftest_lock"])
        self.assertNotRegex(SOURCE, r"nouveau_selftest_(?:instmem|fifo|falcon)_lock")

    def test_all_selftest_suites_use_shared_trylock_and_unlock(self) -> None:
        names = (
            "nouveau_debugfs_selftest_safe_show",
            "nouveau_debugfs_selftest_instmem_show",
            "nouveau_debugfs_selftest_fifo_show",
            "nouveau_debugfs_selftest_falcon_show",
        )
        for name in names:
            with self.subTest(name=name):
                body = function_body(name)
                self.assertIn("mutex_trylock(&nouveau_selftest_lock)", body)
                self.assertIn("mutex_unlock(&nouveau_selftest_lock)", body)
                self.assertIn("selftest_busy", body)

    def test_fixed_size_device_strings_are_bounded(self) -> None:
        self.assertIn('chip=\\"%.15s\\" name=\\"%.63s\\"', SOURCE)
        info_header = (SOURCE_PATH.parent / "include/nvif/cl0080.h").read_text()
        self.assertRegex(info_header, r"char\s+chip\[16\];")
        self.assertRegex(info_header, r"char\s+name\[64\];")


if __name__ == "__main__":
    unittest.main()
