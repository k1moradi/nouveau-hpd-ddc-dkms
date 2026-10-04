import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


PACKAGE = Path(__file__).resolve().parents[1]
PATCH = PACKAGE / "patches/0001-nouveau-nvif-bsp-lifetime-diagnostic.patch"
SOURCE = Path(os.environ.get("MESA_NOUVEAU_C_SOURCE", ""))
SOURCE_SHA256 = "2140bca6de1666e4517ebabf97a63b417db43bfbc575198d7835d40ad92dc57f"
PATCH_SHA256 = "23e952f8c4d61c0a2ae556029bf45e21e1c718fc23570c7abccaa640c349a81a"
RELATIVE_SOURCE = Path("src/gallium/winsys/nouveau/drm/nouveau.c")


@unittest.skipUnless(
    SOURCE.is_file(),
    "set MESA_NOUVEAU_C_SOURCE to pristine pinned Mesa 26.0.8 nouveau.c",
)
class NvifLifetimeDiagnosticTests(unittest.TestCase):
    def patched_source(self) -> str:
        self.assertEqual(
            hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            SOURCE_SHA256,
        )

        with tempfile.TemporaryDirectory(prefix="mesa-nvif-lifetime-") as temp:
            root = Path(temp)
            target = root / RELATIVE_SOURCE
            target.parent.mkdir(parents=True)
            shutil.copyfile(SOURCE, target)

            result = subprocess.run(
                [
                    "patch",
                    "--fuzz=0",
                    "--batch",
                    "--forward",
                    "-p1",
                    "-i",
                    str(PATCH),
                ],
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                result.returncode,
                0,
                result.stdout + result.stderr,
            )
            return target.read_text(encoding="utf-8")

    def test_patch_identity(self) -> None:
        self.assertEqual(
            hashlib.sha256(PATCH.read_bytes()).hexdigest(),
            PATCH_SHA256,
        )

    def test_zero_fuzz_patch_applies_to_pinned_source(self) -> None:
        self.assertIn(
            "NOUVEAU_DIAG_NVIF_BSP_CLASS 0x95b1",
            self.patched_source(),
        )

    def test_new_log_uses_sent_key_and_records_result(self) -> None:
        source = self.patched_source()
        self.assertIn(
            "NOUVEAU_DIAG_NVIF_NEW key=0x%016",
            source,
        )
        self.assertIn("(uint64_t)args.new.object", source)
        self.assertIn("(uint64_t)args.ioctl.token", source)
        self.assertIn("(uint64_t)parent->handle", source)
        self.assertIn("selected_fd=%d drm_fd=%d", source)
        self.assertIn("drm->fd, drm->fd, oclass, ret", source)
        self.assertNotIn("obj=%p", source)
        self.assertNotIn("parent=%p", source)

    def test_del_variants_share_the_actual_ioctl_and_log(self) -> None:
        source = self.patched_source()
        self.assertIn(
            "#ifdef NOUVEAU_DIAG_NVIF_CORRECT_DEL_FD\n"
            "   const int delete_fd = drm->fd;\n"
            "#else\n"
            "   const int delete_fd = obj->parent->handle;\n"
            "#endif",
            source,
        )
        self.assertIn(
            "const int ret = drmCommandWrite(delete_fd, DRM_NOUVEAU_NVIF, "
            "&args, sizeof(args));",
            source,
        )
        self.assertIn("(uint64_t)args.ioctl.object", source)
        self.assertIn("selected_fd=%d drm_fd=%d", source)
        self.assertIn("delete_fd, drm->fd, obj->oclass, ret", source)

    def test_diagnostic_disabled_path_preserves_original_fd_calls(self) -> None:
        source = self.patched_source()
        self.assertIn(
            "#else\n"
            "   return drmCommandWrite(drm->fd, DRM_NOUVEAU_NVIF, "
            "&args, sizeof(args));\n"
            "#endif",
            source,
        )
        self.assertIn(
            "#else\n"
            "   drmCommandWrite(obj->parent->handle, DRM_NOUVEAU_NVIF, "
            "&args, sizeof(args));\n"
            "#endif",
            source,
        )

    def test_channel_free_records_channel_fd_and_return(self) -> None:
        source = self.patched_source()
        self.assertIn("NOUVEAU_DIAG_NVIF_CHANNEL_FREE channel=0x%016", source)
        self.assertIn("(uint64_t)req.channel, drm->fd, ret", source)

    def test_candidate_fd_requires_diagnostic_logging(self) -> None:
        source = self.patched_source()
        self.assertIn(
            "#if defined(NOUVEAU_DIAG_NVIF_CORRECT_DEL_FD)",
            source,
        )
        self.assertIn(
            "the NVIF DEL fd candidate requires lifetime diagnostics",
            source,
        )


if __name__ == "__main__":
    unittest.main()
