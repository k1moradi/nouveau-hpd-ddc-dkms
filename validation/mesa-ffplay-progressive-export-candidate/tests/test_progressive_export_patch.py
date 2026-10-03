from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "patches/0001-va-export-interlaced-surface-through-progressive-staging.patch"
BOUNDED_WAIT_PATCH = ROOT / "patches/0002-use-bounded-nouveau-fence-poll.patch"
EXPECTED_PATCH_SHA256 = (
    "e8e7611f591c237bf4c2d5c993b93625af1d5610a3277b6698c1c501d5858560"
)
EXPECTED_BOUNDED_WAIT_PATCH_SHA256 = (
    "6865f60a222043ddd2e31f5c8cfad84d4bd3c7dff588988a39eda03281d5d0e1"
)
EXPECTED_BASE_SHA256 = (
    "f66d404ad556926a37caffa5d120a04a74b4a36af63e322ce578b6840e8ffcc0"
)
EXPECTED_CANDIDATE_SHA256 = (
    "ca7e3b5fbd1f0c915d0efb6143ee620a883a17507d2a90442ef2b512b516c3da"
)
EXPECTED_BOUNDED_CANDIDATE_SHA256 = (
    "dc84e79326ee5e546f296279e65eb9dc468080833de875b82391fba09c26706c"
)
SOURCE = os.environ.get("MESA_SURFACE_C_SOURCE")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@unittest.skipUnless(SOURCE, "set MESA_SURFACE_C_SOURCE to pinned baseline surface.c")
class ProgressiveExportPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.baseline = Path(SOURCE).resolve()
        if not cls.baseline.is_file():
            raise RuntimeError(f"baseline surface.c does not exist: {cls.baseline}")
        if sha256(cls.baseline) != EXPECTED_BASE_SHA256:
            raise RuntimeError("baseline surface.c SHA-256 mismatch")
        if sha256(PATCH) != EXPECTED_PATCH_SHA256:
            raise RuntimeError("progressive-export patch SHA-256 mismatch")
        if sha256(BOUNDED_WAIT_PATCH) != EXPECTED_BOUNDED_WAIT_PATCH_SHA256:
            raise RuntimeError("bounded-fence-wait patch SHA-256 mismatch")
        if shutil.which("patch") is None:
            raise RuntimeError("GNU patch is required for strict patch validation")

    def apply_patch(self, root: Path) -> Path:
        relative = Path("src/gallium/frontends/va/surface.c")
        destination = root / relative
        destination.parent.mkdir(parents=True)
        shutil.copyfile(self.baseline, destination)
        for patch_file, expected_hash in (
            (PATCH, EXPECTED_CANDIDATE_SHA256),
            (BOUNDED_WAIT_PATCH, EXPECTED_BOUNDED_CANDIDATE_SHA256),
        ):
            result = subprocess.run(
                [
                    "patch", "--fuzz=0", "--batch", "--forward", "-p1",
                    "-i", str(patch_file),
                ],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(sha256(destination), expected_hash)
        return destination

    def test_patch_applies_zero_fuzz_and_reproduces_pinned_candidate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mesa-progressive-export-") as temporary:
            candidate = self.apply_patch(Path(temporary))
            self.assertEqual(candidate.name, "surface.c")

    def test_staging_is_weaved_synchronized_and_exported_as_progressive(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mesa-progressive-export-") as temporary:
            source = self.apply_patch(Path(temporary)).read_text(encoding="utf-8")

        helper_start = source.index("vlVaCreateProgressiveExportBuffer(")
        helper_end = source.index("\n}\n#endif", helper_start)
        helper = source[helper_start:helper_end]
        for required in (
            "src->interlaced",
            "src->buffer_format != PIPE_FORMAT_NV12",
            "PIPE_BIND_PROTECTED",
            "VL_COMPOSITOR_WEAVE",
            "drv->compositor.shaders_initialized",
            "drv->pipe->flush(drv->pipe, &fence, 0)",
            "vlVaFenceFinishBounded(screen, fence, timeout_ns)",
            "*out_buffer = buffer",
            "buffer->destroy(buffer)",
        ):
            with self.subTest(required=required):
                self.assertIn(required, helper)

        wait_start = source.index("vlVaFenceFinishBounded(")
        wait_end = source.index("\n}\n", wait_start)
        bounded_wait = source[wait_start:wait_end]
        self.assertIn(
            "screen->fence_finish(screen, NULL, fence, 0)",
            bounded_wait,
        )
        self.assertIn("os_time_get_nano()", bounded_wait)
        self.assertIn("os_time_nanosleep_until(wake_ns)", bounded_wait)
        self.assertIn("if (now_ns >= deadline_ns)", bounded_wait)
        self.assertNotIn(
            "screen->fence_finish(screen, NULL, fence, timeout_ns)",
            source,
        )

        export_start = source.index("vlVaExportSurfaceHandle(")
        export = source[export_start:]
        weave = export.index("vlVaCreateProgressiveExportBuffer(drv, surf->buffer")
        progressive = export.index("staging_surface.templat.interlaced = false")
        substitute = export.index("surf = &staging_surface")
        export_surfaces = export.index(
            "surfaces = surf->buffer->get_surfaces(surf->buffer)"
        )
        self.assertLess(weave, progressive)
        self.assertLess(progressive, substitute)
        self.assertLess(substitute, export_surfaces)
        self.assertEqual(export.count("staging_buffer->destroy(staging_buffer)"), 2)

    def test_windows_interlaced_rejection_and_failure_cleanup_remain(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mesa-progressive-export-") as temporary:
            source = self.apply_patch(Path(temporary)).read_text(encoding="utf-8")

        export_start = source.index("vlVaExportSurfaceHandle(")
        export = source[export_start:]
        branch_start = export.index("if (surf->buffer->interlaced)")
        branch_end = export.index("\n   surfaces = surf->buffer->get_surfaces", branch_start)
        interlaced_branch = export[branch_start:branch_end]
        self.assertIn("#ifndef _WIN32", interlaced_branch)
        self.assertIn("#else", interlaced_branch)
        self.assertIn("return VA_STATUS_ERROR_INVALID_SURFACE", interlaced_branch)
        self.assertIn("for (i = 0; i < desc->num_objects; i++)", export)
        self.assertIn("close(desc->objects[i].fd)", export)
        self.assertEqual(export.count("staging_buffer->destroy(staging_buffer)"), 2)


if __name__ == "__main__":
    unittest.main()
