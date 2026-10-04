from pathlib import Path
import hashlib
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
        self.assertIn('strcmp(argv[1], "--execute")', self.source)
        self.assertIn(
            "d1bab5275bcb585791fbfb15c801c1aab582256e7b7fca280c76f78a0a1c1ec2",
            self.source,
        )
        self.assertLess(
            self.source.index("verify_input_sha256(argv[2])"),
            self.source.index("decode_first_vaapi_surface(argv[2], argv[3])"),
        )


if __name__ == "__main__":
    unittest.main()
