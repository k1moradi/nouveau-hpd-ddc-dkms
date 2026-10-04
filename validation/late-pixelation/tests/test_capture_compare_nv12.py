import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "capture_compare_nv12.py"
SPEC = importlib.util.spec_from_file_location("capture_compare_nv12", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
CAPTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CAPTURE)


class Nv12CaptureComparisonTests(unittest.TestCase):
    def test_software_comparison_reference_is_hash_pinned(self) -> None:
        with tempfile.TemporaryDirectory(prefix="late-nv12-reference-") as temporary:
            root = Path(temporary)
            ffmpeg = root / "ffmpeg"
            ffmpeg.write_bytes(b"pinned fake executable")
            ffprobe = root / "ffprobe"
            ffprobe.write_bytes(b"pinned fake ffprobe")
            container = root / "software.nut"
            container.write_bytes(b"exact reviewed reference")
            manifest = {
                "schema": 1,
                "mode": "software",
                "status": "CAPTURED_RAW_FRAMES_NOT_VISUAL_ACCEPTANCE",
                "input_sha256": CAPTURE.EXPECTED_INPUT_SHA256,
                "target_pts": list(CAPTURE.TARGET_PTS),
                "captured_pts": list(CAPTURE.TARGET_PTS),
                "input_seek_start_seconds": CAPTURE.INPUT_SEEK_START_SECONDS,
                "input_decode_duration_seconds": CAPTURE.INPUT_DECODE_DURATION_SECONDS,
                "output_time_base": CAPTURE.OUTPUT_TIME_BASE,
                "output": container.name,
                "output_sha256": CAPTURE.sha256_file(container),
                "ffmpeg": str(ffmpeg),
                "ffmpeg_sha256": CAPTURE.sha256_file(ffmpeg),
                "ffprobe": str(ffprobe),
                "ffprobe_sha256": CAPTURE.sha256_file(ffprobe),
            }
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            with (
                patch.object(
                    CAPTURE,
                    "SOFTWARE_REFERENCE_CONTAINER_SHA256",
                    CAPTURE.sha256_file(container),
                ),
                patch.object(
                    CAPTURE,
                    "SOFTWARE_REFERENCE_MANIFEST_SHA256",
                    CAPTURE.sha256_file(manifest_path),
                ),
            ):
                self.assertEqual(
                    CAPTURE.verify_software_reference(container),
                    manifest,
                )
                container.write_bytes(b"different reference")
                with self.assertRaisesRegex(RuntimeError, "capture hash mismatch"):
                    CAPTURE.verify_software_reference(container)

    def test_capture_argv_keeps_hardware_and_software_paths_separate(self) -> None:
        common = {
            "ffmpeg": "/usr/bin/ffmpeg",
            "input_path": Path("/tmp/input.mkv"),
            "output_path": Path("/tmp/capture.nut"),
        }
        hardware = CAPTURE.capture_argv(mode="hardware", **common)
        software = CAPTURE.capture_argv(mode="software", **common)

        self.assertIn("-hwaccel_output_format", hardware)
        self.assertLess(hardware.index("-ss"), hardware.index("-i"))
        self.assertLess(software.index("-ss"), software.index("-i"))
        self.assertIn("-t", hardware)
        self.assertIn("-frames:v", hardware)
        self.assertEqual(hardware[hardware.index("-frames:v") + 1], "5")
        self.assertEqual(
            hardware[hardware.index("-enc_time_base") + 1],
            CAPTURE.OUTPUT_TIME_BASE,
        )
        hardware_filter = hardware[hardware.index("-vf") + 1]
        software_filter = software[software.index("-vf") + 1]
        self.assertIn("hwdownload,format=nv12", hardware_filter)
        self.assertNotIn("-hwaccel", software)
        self.assertIn("format=nv12", software_filter)
        self.assertIn("select='", hardware_filter)
        self.assertIn("select='", software_filter)
        self.assertIn("between(t,530.011000,530.031000)", hardware_filter)

    def test_frame_metrics_reports_exact_y_uv_differences(self) -> None:
        # 4x2 NV12: eight Y bytes followed by one 4-byte interleaved UV row.
        software = bytes([1, 2, 3, 4, 5, 6, 7, 8, 20, 21, 22, 23])
        hardware = bytes([1, 2, 4, 4, 5, 6, 7, 8, 20, 21, 24, 23])

        result = CAPTURE.frame_metrics(hardware, software, 4, 2)

        self.assertEqual(result["y_changed"], 1)
        self.assertEqual(result["uv_changed"], 1)
        self.assertEqual(result["max_y_delta"], 1)
        self.assertEqual(result["max_uv_delta"], 2)
        self.assertAlmostEqual(result["y_rmse"], (1 / 8) ** 0.5)
        self.assertAlmostEqual(result["uv_rmse"], (4 / 4) ** 0.5)
        self.assertEqual(result["difference_bbox"]["y"], [2, 0, 2, 0])
        self.assertEqual(result["difference_bbox"]["uv_bytes"], [2, 0, 2, 0])

    def test_identical_frames_have_zero_deltas_and_no_bbox(self) -> None:
        frame = bytes(range(12))
        result = CAPTURE.frame_metrics(frame, frame, 4, 2)

        self.assertEqual(result["y_changed"], 0)
        self.assertEqual(result["uv_changed"], 0)
        self.assertEqual(result["max_y_delta"], 0)
        self.assertEqual(result["max_uv_delta"], 0)
        self.assertEqual(result["difference_bbox"], {"y": None, "uv_bytes": None})

    def test_target_matching_requires_close_pts_and_distinct_nearest_frames(self) -> None:
        hw = [{"index": 0, "pts": 530.0212}]
        sw = [{"index": 0, "pts": 530.0210}]
        self.assertEqual(CAPTURE.match_target(530.021, hw, sw), (0, 0))

        with self.assertRaisesRegex(ValueError, "requested PTS"):
            CAPTURE.match_target(
                530.021,
                [{"index": 0, "pts": 530.025}],
                [{"index": 0, "pts": 530.021}],
            )

    def test_nv12_geometry_and_lengths_are_validated(self) -> None:
        with self.assertRaisesRegex(ValueError, "even"):
            CAPTURE.frame_metrics(bytes(18), bytes(18), 3, 4)
        with self.assertRaisesRegex(ValueError, "byte count"):
            CAPTURE.frame_metrics(bytes(11), bytes(11), 4, 2)

    def test_capture_manifest_pins_mode_input_targets_and_output(self) -> None:
        with tempfile.TemporaryDirectory(prefix="late-nv12-manifest-") as temporary:
            root = Path(temporary)
            ffmpeg = root / "ffmpeg"
            ffmpeg.write_bytes(b"pinned fake executable")
            ffprobe = root / "ffprobe"
            ffprobe.write_bytes(b"pinned fake ffprobe")
            container = root / "hardware.nut"
            container.write_bytes(b"captured bytes")
            manifest = {
                "schema": 1,
                "mode": "hardware",
                "status": "CAPTURED_RAW_FRAMES_NOT_VISUAL_ACCEPTANCE",
                "input_sha256": CAPTURE.EXPECTED_INPUT_SHA256,
                "target_pts": list(CAPTURE.TARGET_PTS),
                "captured_pts": list(CAPTURE.TARGET_PTS),
                "input_seek_start_seconds": CAPTURE.INPUT_SEEK_START_SECONDS,
                "input_decode_duration_seconds": CAPTURE.INPUT_DECODE_DURATION_SECONDS,
                "output_time_base": CAPTURE.OUTPUT_TIME_BASE,
                "output": container.name,
                "output_sha256": CAPTURE.sha256_file(container),
                "ffmpeg": str(ffmpeg),
                "ffmpeg_sha256": CAPTURE.sha256_file(ffmpeg),
                "ffprobe": str(ffprobe),
                "ffprobe_sha256": CAPTURE.sha256_file(ffprobe),
            }
            (root / "manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )

            self.assertEqual(
                CAPTURE.verify_capture_manifest(container, "hardware"),
                manifest,
            )
            manifest["mode"] = "software"
            (root / "manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            with self.assertRaisesRegex(RuntimeError, "mode mismatch"):
                CAPTURE.verify_capture_manifest(container, "hardware")

    def test_capture_requires_exact_input_identity_and_never_overwrites(self) -> None:
        with tempfile.TemporaryDirectory(prefix="late-nv12-test-") as temporary:
            root = Path(temporary)
            input_file = root / "input.mkv"
            input_file.write_bytes(b"not the pinned input")
            with self.assertRaisesRegex(RuntimeError, "input SHA-256 mismatch"):
                CAPTURE.run_capture(
                    mode="software",
                    input_path=input_file,
                    output_dir=root / "run",
                    ffmpeg="/usr/bin/ffmpeg",
                    device="/dev/dri/renderD128",
                )
            existing = root / "existing"
            existing.mkdir()
            with self.assertRaisesRegex(FileExistsError, "existing capture"):
                CAPTURE.run_capture(
                    mode="software",
                    input_path=input_file,
                    output_dir=existing,
                    ffmpeg="/usr/bin/ffmpeg",
                    device="/dev/dri/renderD128",
                )


if __name__ == "__main__":
    unittest.main()
