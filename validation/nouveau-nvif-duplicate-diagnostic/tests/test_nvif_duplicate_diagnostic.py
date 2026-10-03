import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "patches/0009-drm-nouveau-log-nvif-duplicate-layer.patch"
SOURCE_ROOT = Path(os.environ.get("NOUVEAU_KERNEL_SOURCE_ROOT", ""))
ABI16_PATH = SOURCE_ROOT / "drivers/gpu/drm/nouveau/nouveau_abi16.c"
NVKM_IOCTL_PATH = SOURCE_ROOT / "drivers/gpu/drm/nouveau/nvkm/core/ioctl.c"
ABI16_SHA256 = "3d4c81dd87c8e426b7e948eb448a49075d1df54ab22c68b8e181a0b88bd28fec"
NVKM_IOCTL_SHA256 = "2a18013840cee2298edadd19171b083cfed2bb7227a13303ac9afad34a107e18"


@unittest.skipUnless(
    ABI16_PATH.is_file() and NVKM_IOCTL_PATH.is_file(),
    "set NOUVEAU_KERNEL_SOURCE_ROOT to the pinned v1-v8 kernel source",
)
class NvifDuplicateDiagnosticTests(unittest.TestCase):
    def test_pinned_source_hashes(self) -> None:
        self.assertEqual(
            hashlib.sha256(ABI16_PATH.read_bytes()).hexdigest(),
            ABI16_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(NVKM_IOCTL_PATH.read_bytes()).hexdigest(),
            NVKM_IOCTL_SHA256,
        )

    def test_patch_applies_with_zero_fuzz(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nouveau-nvif-dup-") as temp:
            root = Path(temp)
            abi16_target = root / ABI16_PATH.relative_to(SOURCE_ROOT)
            nvkm_target = root / NVKM_IOCTL_PATH.relative_to(SOURCE_ROOT)
            abi16_target.parent.mkdir(parents=True)
            nvkm_target.parent.mkdir(parents=True)
            shutil.copyfile(ABI16_PATH, abi16_target)
            shutil.copyfile(NVKM_IOCTL_PATH, nvkm_target)

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

            abi16_candidate = abi16_target.read_text(encoding="utf-8")
            nvkm_candidate = nvkm_target.read_text(encoding="utf-8")

        self.assertIn("NOUVEAU_DIAG_NVIF_DUP layer=abi16", abi16_candidate)
        self.assertIn("NOUVEAU_DIAG_NVIF_DUP layer=nvkm", nvkm_candidate)
        self.assertLess(
            abi16_candidate.index("if (ret == -EEXIST)"),
            abi16_candidate.index("return ret;", abi16_candidate.index("if (ret == -EEXIST)")),
        )
        self.assertLess(
            nvkm_candidate.index("pr_info(\"NOUVEAU_DIAG_NVIF_DUP layer=nvkm"),
            nvkm_candidate.index("ret = -EEXIST;", nvkm_candidate.index("pr_info(\"NOUVEAU_DIAG_NVIF_DUP layer=nvkm")),
        )
        self.assertEqual(abi16_candidate.count("#if IS_ENABLED(CONFIG_DRM_NOUVEAU_SELFTEST)"), 1)
        self.assertEqual(nvkm_candidate.count("#if IS_ENABLED(CONFIG_DRM_NOUVEAU_SELFTEST)"), 1)


if __name__ == "__main__":
    unittest.main()
