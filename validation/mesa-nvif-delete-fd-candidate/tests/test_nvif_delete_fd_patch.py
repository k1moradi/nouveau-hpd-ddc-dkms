from difflib import SequenceMatcher
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


PACKAGE = Path(__file__).resolve().parents[1]
PATCH = PACKAGE / "patches/0001-nouveau-subchan-del-use-drm-fd.patch"
SOURCE = Path(os.environ.get("MESA_NOUVEAU_C_SOURCE", ""))
SOURCE_SHA256 = "2140bca6de1666e4517ebabf97a63b417db43bfbc575198d7835d40ad92dc57f"
PATCH_SHA256 = "6ff3fdf5b3a38f07246830c78acf46cf222dd3ec2114a87a03526d5f8721df2c"
RELATIVE_SOURCE = Path("src/gallium/winsys/nouveau/drm/nouveau.c")
OLD_CALL = (
    "drmCommandWrite(obj->parent->handle, DRM_NOUVEAU_NVIF, "
    "&args, sizeof(args));"
)
NEW_CALL = (
    "drmCommandWrite(nouveau_drm(obj->parent)->fd, DRM_NOUVEAU_NVIF, "
    "&args, sizeof(args));"
)


@unittest.skipUnless(
    SOURCE.is_file(),
    "set MESA_NOUVEAU_C_SOURCE to the pinned pristine Mesa nouveau.c",
)
class NvifDeleteFdPatchTests(unittest.TestCase):
    def test_pinned_source_identity(self) -> None:
        self.assertEqual(
            hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            SOURCE_SHA256,
        )

    def test_patch_identity(self) -> None:
        self.assertEqual(
            hashlib.sha256(PATCH.read_bytes()).hexdigest(),
            PATCH_SHA256,
        )

    def test_zero_fuzz_patch_changes_only_del_fd_argument(self) -> None:
        original = SOURCE.read_text(encoding="utf-8")
        self.assertEqual(original.count(OLD_CALL), 1)
        self.assertEqual(original.count(NEW_CALL), 0)

        with tempfile.TemporaryDirectory(prefix="mesa-nvif-del-fd-") as temp:
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

            candidate = target.read_text(encoding="utf-8")

        self.assertEqual(candidate.count(OLD_CALL), 0)
        self.assertEqual(candidate.count(NEW_CALL), 1)
        changes = [
            opcode
            for opcode in SequenceMatcher(
                a=original.splitlines(),
                b=candidate.splitlines(),
            ).get_opcodes()
            if opcode[0] != "equal"
        ]
        self.assertEqual(len(changes), 1)
        tag, old_start, old_end, new_start, new_end = changes[0]
        self.assertEqual(tag, "replace")
        self.assertEqual(old_end - old_start, 1)
        self.assertEqual(new_end - new_start, 1)


if __name__ == "__main__":
    unittest.main()
