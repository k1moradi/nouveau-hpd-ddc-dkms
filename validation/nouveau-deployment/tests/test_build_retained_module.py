import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "validation/nouveau-deployment/build_retained_module.py"
SPEC = importlib.util.spec_from_file_location("build_retained_module", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class PatchInputTests(unittest.TestCase):
    def test_builder_pins_patches_from_their_actual_separate_directories(self):
        nvif = (
            ROOT / "validation/nouveau-nvif-duplicate-diagnostic/patches/"
            "0009-drm-nouveau-log-nvif-duplicate-layer.patch"
        )
        vma = (
            ROOT / "validation/nouveau-bar2-vma-diagnostic/patches/"
            "0010-drm-nouveau-correlate-instmem-vma-selftest.patch"
        )

        paths = BUILDER.verify_patch_inputs(nvif, vma)

        self.assertEqual(paths["0009-drm-nouveau-log-nvif-duplicate-layer.patch"], nvif)
        self.assertEqual(paths["0010-drm-nouveau-correlate-instmem-vma-selftest.patch"], vma)
        self.assertEqual(
            {name: BUILDER.sha256_file(path) for name, path in paths.items()},
            BUILDER.PATCHES,
        )

    def test_builder_rejects_wrong_patch_bytes_before_build(self):
        with tempfile.TemporaryDirectory(prefix="deployment-patch-pin-") as temporary:
            wrong = Path(temporary) / "0009-drm-nouveau-log-nvif-duplicate-layer.patch"
            wrong.write_text("not the reviewed patch\n", encoding="utf-8")
            vma = (
                ROOT / "validation/nouveau-bar2-vma-diagnostic/patches/"
                "0010-drm-nouveau-correlate-instmem-vma-selftest.patch"
            )

            with self.assertRaisesRegex(RuntimeError, "unexpected hash for 0009"):
                BUILDER.verify_patch_inputs(wrong, vma)


if __name__ == "__main__":
    unittest.main()
