from pathlib import Path
import hashlib
import json
import re
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "va_vp3_export_probe.c"
FFMPEG_ROOT = Path(
    "/home/keivan/.cache/vaapi-app-source-review/ffmpeg-n8.0.1"
)
FFMPEG_SOURCES = {
    "libavcodec/vaapi_decode.c":
        "dd9db7f515923d288935af0620498cea97054561bce67a7e797d3facc5764586",
    "libavutil/hwcontext_vaapi.c":
        "84e9bd59a0cc88a542d3a3692c001251e6c7c833a01390d60244b47843eacd51",
}
DEPLOYMENT_PLAN = (
    Path(__file__).resolve().parents[2]
    / "nouveau-deployment/deployment-plan.json"
)
HISTORICAL_FFPLAY_META = Path(
    "/home/keivan/nouveau-vaapi-app-validation/"
    "ffplay-interlaced-rejection-probe-v4-20261001T082058Z/meta.txt"
)
HISTORICAL_FFPLAY_GDB = HISTORICAL_FFPLAY_META.with_name("gdb-ffplay.log")
EXPECTED_INPUT_SHA256 = (
    "d1bab5275bcb585791fbfb15c801c1aab582256e7b7fca280c76f78a0a1c1ec2"
)
EXPECTED_VA_DRIVER_SHA256 = (
    "64c5508bb7167947f932c950d5e2d2c314f631fc3919de82f757c3769758b79f"
)
EXPECTED_HISTORICAL_META_SHA256 = (
    "0cb1916dba19e837ccd6b507381581e0a4994a2c54d908c54a24faa7b9885a1f"
)
EXPECTED_HISTORICAL_GDB_SHA256 = (
    "a4ae1918c5e279b0afcd69e7109e134b45c78e1aa8e10772acad83855723acf6"
)


class VaVp3ExportProbeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SOURCE.read_text(encoding="utf-8")

    def test_queries_matching_h264_high_vld_config(self) -> None:
        self.assertIn("vaQueryConfigProfiles", self.source)
        self.assertIn("VAProfileH264High", self.source)
        self.assertIn("VAEntrypointVLD", self.source)
        self.assertIn("vaCreateConfig", self.source)
        self.assertIn("vaQuerySurfaceAttributes", self.source)
        self.assertIn(
            "vaCreateConfig(display, VAProfileH264High, VAEntrypointVLD,\n"
            "                           NULL, 0, config_out)",
            self.source,
        )

    def test_configuration_and_export_match_pinned_ffmpeg_source(self) -> None:
        sources = {}
        for relative, expected_hash in FFMPEG_SOURCES.items():
            path = FFMPEG_ROOT / relative
            payload = path.read_bytes()
            self.assertEqual(hashlib.sha256(payload).hexdigest(), expected_hash)
            sources[relative] = payload.decode("utf-8")

        decoder = sources["libavcodec/vaapi_decode.c"]
        create_config = re.search(
            r"vas\s*=\s*vaCreateConfig\(hwctx->display,\s*"
            r"matched_va_profile,\s*VAEntrypointVLD,\s*NULL,\s*0,\s*va_config\)",
            decoder,
        )
        self.assertIsNotNone(create_config)

        export_source = sources["libavutil/hwcontext_vaapi.c"]
        self.assertIn("export_flags = VA_EXPORT_SURFACE_SEPARATE_LAYERS;", export_source)
        self.assertIn("export_flags |= VA_EXPORT_SURFACE_READ_ONLY;", export_source)
        self.assertIn("vaSyncSurface(hwctx->display, surface_id)", export_source)
        self.assertIn(
            "vaExportSurfaceHandle(hwctx->display, surface_id,\n"
            "                                VA_SURFACE_ATTRIB_MEM_TYPE_DRM_PRIME_2,\n"
            "                                export_flags, &va_desc)",
            export_source,
        )

    def test_sync_precedes_export_of_actual_decoded_surface(self) -> None:
        sync_at = self.source.index("vaSyncSurface(display, surface)")
        export_at = self.source.index("vaExportSurfaceHandle(display, surface")
        self.assertLess(sync_at, export_at)
        self.assertIn("frame->data[3]", self.source)

    def test_pins_and_verifies_the_selected_va_driver_dso(self) -> None:
        self.assertIn(EXPECTED_VA_DRIVER_SHA256, self.source)
        self.assertIn('setenv("LIBVA_DRIVER_NAME", "nouveau", 1)', self.source)
        self.assertIn('setenv("LIBVA_DRIVERS_PATH", driver_dir, 1)', self.source)
        self.assertIn('fopen("/proc/self/maps", "r")', self.source)
        self.assertIn("verify_va_driver_mapped(expected_driver_path)", self.source)

        plan = json.loads(DEPLOYMENT_PLAN.read_text(encoding="utf-8"))
        driver = plan["tools"]["va_export_driver_dso"]
        self.assertEqual(driver["path"],
                         "/usr/lib/x86_64-linux-gnu/libgallium-26.0.8-1ubuntu0.3.so")
        self.assertEqual(driver["sha256"], EXPECTED_VA_DRIVER_SHA256)

    def test_retained_probe_binary_and_runtime_driver_match_build_manifest(self) -> None:
        manifest = json.loads(
            (SOURCE.parent / "build-manifest.json").read_text(encoding="utf-8")
        )
        source_sha = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
        binary = Path(manifest["binary"]["path"])
        build_log = Path(manifest["binary"]["build_log_path"])
        driver = Path(manifest["runtime_driver"]["resolved_path"])
        plan = json.loads(DEPLOYMENT_PLAN.read_text(encoding="utf-8"))
        self.assertEqual(manifest["source"]["sha256"], source_sha)
        self.assertEqual(
            hashlib.sha256(binary.read_bytes()).hexdigest(),
            manifest["binary"]["sha256"],
        )
        self.assertEqual(
            hashlib.sha256(build_log.read_bytes()).hexdigest(),
            manifest["binary"]["build_log_sha256"],
        )
        self.assertEqual(
            hashlib.sha256(driver.read_bytes()).hexdigest(),
            manifest["runtime_driver"]["sha256"],
        )
        self.assertEqual(plan["tools"]["va_export_probe"]["path"], str(binary))
        self.assertEqual(
            plan["tools"]["va_export_probe"]["sha256"],
            manifest["binary"]["sha256"],
        )
        self.assertEqual(
            plan["tools"]["va_export_probe_build_manifest"]["sha256"],
            hashlib.sha256(
                (SOURCE.parent / "build-manifest.json").read_bytes()
            ).hexdigest(),
        )
        self.assertFalse(manifest["runtime_executed"])

    def test_historical_ffplay_capture_and_probe_use_identical_input(self) -> None:
        metadata = HISTORICAL_FFPLAY_META.read_text(encoding="utf-8")
        historical_log = HISTORICAL_FFPLAY_GDB.read_text(encoding="utf-8")
        manifest = json.loads(
            (SOURCE.parent / "build-manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            hashlib.sha256(HISTORICAL_FFPLAY_META.read_bytes()).hexdigest(),
            EXPECTED_HISTORICAL_META_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(HISTORICAL_FFPLAY_GDB.read_bytes()).hexdigest(),
            EXPECTED_HISTORICAL_GDB_SHA256,
        )
        self.assertEqual(
            manifest["historical_failure"]["meta_sha256"],
            EXPECTED_HISTORICAL_META_SHA256,
        )
        self.assertEqual(
            manifest["historical_failure"]["gdb_log_sha256"],
            EXPECTED_HISTORICAL_GDB_SHA256,
        )
        self.assertIn(
            f"input={EXPECTED_INPUT_SHA256}  /home/keivan/test_1080p.mkv",
            metadata,
        )
        self.assertIn("Video: h264 (High)", historical_log)
        self.assertIn("interlaced=1 status=6", historical_log)
        self.assertEqual(
            manifest["historical_failure"]["input_sha256"],
            EXPECTED_INPUT_SHA256,
        )
        self.assertIn(EXPECTED_INPUT_SHA256, self.source)

    def test_uses_same_drm_prime_2_export_flags_as_ffmpeg_mapping(self) -> None:
        self.assertIn("VA_SURFACE_ATTRIB_MEM_TYPE_DRM_PRIME_2", self.source)
        self.assertIn("VA_EXPORT_SURFACE_READ_ONLY", self.source)
        self.assertIn("VA_EXPORT_SURFACE_SEPARATE_LAYERS", self.source)

    def test_records_surface_capabilities_and_descriptor_geometry(self) -> None:
        self.assertIn("VASurfaceAttribMemoryType", self.source)
        self.assertIn("VASurfaceAttribPixelFormat", self.source)
        self.assertIn("VASurfaceAttribUsageHint", self.source)
        self.assertIn("descriptor.width", self.source)
        self.assertIn("descriptor.height", self.source)
        self.assertIn("descriptor.layers[layer]", self.source)

    def test_probe_does_not_spawn_or_modify_player_processes(self) -> None:
        self.assertNotIn("system(", self.source)
        self.assertNotIn("popen(", self.source)
        self.assertNotIn("execv(", self.source)
        self.assertNotIn("fork(", self.source)

    def test_probe_requires_explicit_execute_and_pinned_input_hash(self) -> None:
        self.assertIn('argc != 6 || strcmp(argv[1], "--execute")', self.source)
        self.assertIn(
            EXPECTED_INPUT_SHA256,
            self.source,
        )
        self.assertLess(
            self.source.index("verify_input_sha256(argv[2])"),
            self.source.index("decode_first_vaapi_surface(argv[2], argv[3], resolved_driver)"),
        )
        self.assertLess(
            self.source.index("prepare_va_driver(argv[4], argv[5], resolved_driver)"),
            self.source.index("decode_first_vaapi_surface(argv[2], argv[3], resolved_driver)"),
        )


if __name__ == "__main__":
    unittest.main()
