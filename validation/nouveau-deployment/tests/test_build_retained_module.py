import importlib.util
from difflib import unified_diff
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "validation/nouveau-deployment/build_retained_module.py"
SPEC = importlib.util.spec_from_file_location("build_retained_module", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class PatchInputTests(unittest.TestCase):
    def test_marker_sets_match_the_ambient_patch_contract(self):
        self.assertNotIn("NOUVEAU_DIAG_BAR2_CONFIG", BUILDER.REQUIRED_MARKERS)
        self.assertNotIn("diag_bar2_map_kmap_start", BUILDER.DISABLED_MARKERS)
        for marker in (
            "NOUVEAU_DIAG_BAR2_MAP",
            "NOUVEAU_DIAG_BAR2_RESET",
            "NOUVEAU_DIAG_BAR2_RING_EVENT",
            "NOUVEAU_DIAG_BAR2_RING_STATUS",
            "OBJECT_IOUNMAP_BEGIN",
            "OBJECT_VMM_PUT_BEGIN",
            "diag_bar2_map",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, BUILDER.REQUIRED_MARKERS)
                self.assertIn(marker, BUILDER.DISABLED_MARKERS)

    def test_builder_pins_patches_from_their_actual_separate_directories(self):
        nvif = (
            ROOT / "validation/nouveau-nvif-duplicate-diagnostic/patches/"
            "0009-drm-nouveau-log-nvif-duplicate-layer.patch"
        )
        vma = (
            ROOT / "validation/nouveau-bar2-vma-diagnostic/patches/"
            "0010-drm-nouveau-correlate-instmem-vma-selftest.patch"
        )
        ambient = (
            ROOT / "validation/nouveau-bar2-ambient-diagnostic/patches/"
            "0011-drm-nouveau-trace-ambient-bar2-lifetimes.patch"
        )
        teardown = (
            ROOT / "validation/nouveau-bar2-ambient-diagnostic/patches/"
            "0012-drm-nouveau-mark-bar2-teardown-phases.patch"
        )
        ring = (
            ROOT / "validation/nouveau-bar2-ambient-diagnostic/patches/"
            "0013-drm-nouveau-capture-ambient-bar2-events.patch"
        )

        paths = BUILDER.verify_patch_inputs(nvif, vma, ambient, teardown, ring)

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
            remaining = [
                ROOT / "validation/nouveau-bar2-ambient-diagnostic/patches/"
                / name
                for name in list(BUILDER.PATCHES)[2:]
            ]

            with self.assertRaisesRegex(RuntimeError, "unexpected hash for 0009"):
                BUILDER.verify_patch_inputs(wrong, vma, *remaining)

    def test_patch_paths_support_traditional_unified_headers(self):
        patch = (
            ROOT / "validation/nouveau-nvif-duplicate-diagnostic/patches/"
            "0009-drm-nouveau-log-nvif-duplicate-layer.patch"
        )

        self.assertEqual(
            BUILDER.patch_file_paths(patch),
            {
                "drivers/gpu/drm/nouveau/nouveau_abi16.c",
                "drivers/gpu/drm/nouveau/nvkm/core/ioctl.c",
            },
        )

    def test_stack_verification_reverses_overlapping_patches_sequentially(self):
        with tempfile.TemporaryDirectory(prefix="patch-stack-verify-test-") as temporary:
            root = Path(temporary)
            relative = "drivers/example.c"
            source_file = root / relative
            source_file.parent.mkdir(parents=True)
            source_file.write_text("before\nlatest\nafter\n", encoding="utf-8")

            def make_patch(name: str, old: str, new: str) -> Path:
                patch_path = root / name
                lines = list(unified_diff(
                    ["before\n", old + "\n", "after\n"],
                    ["before\n", new + "\n", "after\n"],
                    fromfile=f"a/{relative}", tofile=f"b/{relative}",
                ))
                patch_path.write_text(
                    f"diff --git a/{relative} b/{relative}\n" +
                    "".join(lines),
                    encoding="utf-8",
                )
                return patch_path

            first = make_patch("0010.patch", "before", "middle")
            second = make_patch("0011.patch", "middle", "latest")
            logs = root / "logs"
            logs.mkdir()

            BUILDER.verify_patch_stack(
                root, {"0010.patch": first, "0011.patch": second}, logs,
            )

            self.assertEqual(
                source_file.read_text(encoding="utf-8"),
                "before\nlatest\nafter\n",
            )
            self.assertTrue((logs / "verify-0011.patch.log").is_file())
            self.assertTrue((logs / "verify-0010.patch.log").is_file())


class KernelHeaderReleaseTests(unittest.TestCase):
    def test_uses_distro_uts_release_not_upstream_make_kernelrelease(self):
        with tempfile.TemporaryDirectory(prefix="kernel-release-pin-") as temporary:
            headers = Path(temporary)
            config = headers / "include/config/kernel.release"
            uts = headers / "include/generated/utsrelease.h"
            config.parent.mkdir(parents=True)
            uts.parent.mkdir(parents=True)
            config.write_text("7.0.0-34-generic\n", encoding="ascii")
            uts.write_text(
                '#define UTS_RELEASE "7.0.0-34-generic"\n',
                encoding="ascii",
            )
            completed = type("Completed", (), {"stdout": "7.0.14\n"})()
            with patch.object(BUILDER.subprocess, "run", return_value=completed):
                self.assertEqual(
                    BUILDER.configured_kernel_release(headers),
                    ("7.0.0-34-generic", "7.0.14"),
                )

    def test_rejects_disagreement_between_kernel_release_and_uts_header(self):
        with tempfile.TemporaryDirectory(prefix="kernel-release-mismatch-") as temporary:
            headers = Path(temporary)
            config = headers / "include/config/kernel.release"
            uts = headers / "include/generated/utsrelease.h"
            config.parent.mkdir(parents=True)
            uts.parent.mkdir(parents=True)
            config.write_text("7.0.0-34-generic\n", encoding="ascii")
            uts.write_text('#define UTS_RELEASE "7.0.14"\n', encoding="ascii")
            with self.assertRaisesRegex(RuntimeError, "configured kernel release mismatch"):
                BUILDER.configured_kernel_release(headers)


if __name__ == "__main__":
    unittest.main()
