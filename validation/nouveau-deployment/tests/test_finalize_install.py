import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("finalize_install", ROOT / "finalize_install.py")
assert SPEC is not None and SPEC.loader is not None
FINALIZER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FINALIZER)


class ManifestWriteTests(unittest.TestCase):
    def test_write_all_handles_one_complete_write(self):
        written = []

        def write(_fd, view):
            payload = bytes(view)
            written.append(payload)
            return len(payload)

        with patch.object(FINALIZER.os, "write", side_effect=write) as mocked:
            FINALIZER.write_all(123, b"complete payload")

        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(b"".join(written), b"complete payload")

    def test_write_all_retries_short_writes_without_losing_bytes(self):
        saved = bytearray()

        def write(_fd, view):
            part = bytes(view[:3])
            saved.extend(part)
            return len(part)

        with patch.object(FINALIZER.os, "write", side_effect=write):
            FINALIZER.write_all(123, b"manifest bytes across writes")
        self.assertEqual(bytes(saved), b"manifest bytes across writes")

    def test_write_all_fails_on_zero_or_negative_write(self):
        for result in (0, -1):
            with self.subTest(result=result):
                with patch.object(FINALIZER.os, "write", return_value=result):
                    with self.assertRaisesRegex(OSError, "short or invalid write"):
                        FINALIZER.write_all(123, b"payload")

    def test_atomic_saved_manifest_bytes_equal_original(self):
        with tempfile.TemporaryDirectory(prefix="deployment-manifest-test-") as temporary:
            path = Path(temporary) / "deployment.json"
            payload = json.dumps({"schema": 2, "value": "exact"}).encode() + b"\n"
            FINALIZER.atomic_write(path, payload)
            self.assertEqual(path.read_bytes(), payload)
            self.assertEqual(json.loads(path.read_text()), {"schema": 2, "value": "exact"})


class FinalManifestTests(unittest.TestCase):
    def test_validate_inputs_pins_review_tools_mesa_input_and_software_reference(self):
        with tempfile.TemporaryDirectory(prefix="deployment-inputs-test-") as temporary:
            root = Path(temporary)
            raw_module = root / "nouveau.ko"
            raw_module.write_bytes(b"raw diagnostic module")
            input_path = root / "input.mkv"
            input_path.write_bytes(b"pinned input")
            dso_a = root / "A.so"
            dso_b = root / "B.so"
            dso_a.write_bytes(b"mesa A")
            dso_b.write_bytes(b"mesa B")
            software_container = root / "software.nut"
            software_container.write_bytes(b"software frames")
            reference_path = root / "software-manifest.json"
            reference = {
                "schema": 1,
                "mode": "software",
                "input_sha256": FINALIZER.sha256_file(input_path),
                "output": software_container.name,
                "output_sha256": FINALIZER.sha256_file(software_container),
            }
            reference_path.write_text(json.dumps(reference), encoding="utf-8")
            tools = {}
            required_tools = (
                "supervisor", "bar2_correlator", "nvif_capture", "nvif_parser",
                "nvif_profile", "pixel_capture", "va_export_probe",
                "va_export_probe_source", "va_export_probe_build_manifest",
                "va_export_driver_dso",
                "mesa_ab_manifest", "mesa_nvif_diagnostic_patch",
                "admission_check", "deployment_finalizer", "retained_module_builder",
            )
            for name in required_tools:
                path = root / f"{name}.bin"
                path.write_text(name, encoding="ascii")
                tools[name] = {
                    "path": str(path),
                    "sha256": FINALIZER.sha256_file(path),
                }
            plan = {
                "schema": 1,
                "expected_review_branch": "review/gk104-vaapi-selftest-v8-20261002",
                "expected_main_commit": "7b44b1c1e282eac7c54c1cfa8c758118cd66312c",
                "kernel_release": "7.0.0-34-generic",
                "kernel_source_archive_sha256": "a" * 64,
                "patch_sha256": {"patch-9": "b" * 64},
                "expected_srcversion": "936407678F3DA1E8515F5EC",
                "expected_vermagic": "7.0.0-34-generic SMP preempt mod_unload modversions",
                "module_install_path": "/lib/modules/7.0.0-34-generic/updates/dkms/nouveau.ko.zst",
                "initramfs_path": "/boot/initrd.img-7.0.0-34-generic",
                "module_parameters": {"diag_ctxsw": "N"},
                "mesa_variants": {
                    "A": {"path": str(dso_a), "sha256": FINALIZER.sha256_file(dso_a)},
                    "B": {"path": str(dso_b), "sha256": FINALIZER.sha256_file(dso_b)},
                },
                "tools": tools,
                "input": {"path": str(input_path), "sha256": FINALIZER.sha256_file(input_path)},
                "software_reference": {
                    "manifest_path": str(reference_path),
                    "manifest_sha256": FINALIZER.sha256_file(reference_path),
                    "container_path": str(software_container),
                    "container_sha256": FINALIZER.sha256_file(software_container),
                },
            }
            build = {
                "status": "CLEAN_ENABLED_BUILD_RETAINED_NOT_INSTALLED",
                "review_branch": plan["expected_review_branch"],
                "review_commit": "c" * 40,
                "main_commit": plan["expected_main_commit"],
                "kernel_release": plan["kernel_release"],
                "kernel_source_archive_sha256": plan["kernel_source_archive_sha256"],
                "patch_sha256": plan["patch_sha256"],
                "srcversion": plan["expected_srcversion"],
                "vermagic": plan["expected_vermagic"],
                "raw_module": str(raw_module),
                "raw_module_sha256": FINALIZER.sha256_file(raw_module),
            }
            values = FINALIZER.validate_inputs(build, plan, raw_module)
            self.assertEqual(values["review_commit"], "c" * 40)
            tools["supervisor"]["sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "pinned tool path/hash mismatch"):
                FINALIZER.validate_inputs(build, plan, raw_module)

    def test_final_manifest_requires_exact_embedded_module_bytes(self):
        with tempfile.TemporaryDirectory(prefix="manifest-finalize-test-") as temporary:
            root = Path(temporary)
            artifacts = {}
            for name in ("a.so", "b.so", "supervisor.py", "correlator.py", "input.mkv"):
                path = root / name
                path.write_bytes(name.encode("ascii"))
                artifacts[name] = str(path)
            plan = {
                "module_install_path": "/lib/modules/7.0.0-34-generic/updates/dkms/nouveau.ko.zst",
                "module_parameters": {"diag_ctxsw": "N"},
                "mesa_variants": {
                    "A": {"path": artifacts["a.so"], "sha256": "a" * 64},
                    "B": {"path": artifacts["b.so"], "sha256": "b" * 64},
                },
                "tools": {
                    "supervisor": {"path": artifacts["supervisor.py"]},
                    "bar2_correlator": {"path": artifacts["correlator.py"]},
                },
                "input": {"path": artifacts["input.mkv"], "sha256": "e" * 64},
                "software_reference": {
                    "manifest_path": artifacts["input.mkv"],
                    "manifest_sha256": "1" * 64,
                    "container_path": artifacts["input.mkv"],
                    "container_sha256": "2" * 64,
                },
            }
            build = {
                "review_commit": "f" * 40,
                "review_branch": "review/gk104-vaapi-selftest-v8-20261002",
                "main_commit": "7b44b1c1e282eac7c54c1cfa8c758118cd66312c",
                "kernel_release": "7.0.0-34-generic",
                "kernel_source_archive_sha256": "1" * 64,
                "source_overlay_tree": {"source_tree_sha256": "2" * 64},
                "patch_sha256": {
                    "0009-drm-nouveau-log-nvif-duplicate-layer.patch": "3" * 64,
                    "0010-drm-nouveau-correlate-instmem-vma-selftest.patch": "4" * 64,
                },
                "raw_module_sha256": "5" * 64,
                "srcversion": "936407678F3DA1E8515F5EC",
                "vermagic": "7.0.0-34-generic SMP preempt mod_unload modversions",
            }
            kwargs = {
                "build": build,
                "build_manifest_path": Path("/tmp/build-manifest.json"),
                "build_manifest_sha256": "9" * 64,
                "deployment_plan_path": Path("/tmp/deployment-plan.json"),
                "deployment_plan_sha256": "a" * 64,
                "observed_module_path": Path(plan["module_install_path"]),
                "installed_uncompressed_sha256": "6" * 64,
                "compressed_sha256": "7" * 64,
                "initramfs_path": Path("/boot/initrd.img-7.0.0-34-generic"),
                "initramfs_sha256": "8" * 64,
                "embedded": [{"path_in_initramfs": "main/nouveau.ko.zst", "uncompressed_sha256": "6" * 64}],
                "plan": plan,
                "signed": False,
                "certificate_sha256": None,
            }
            result = FINALIZER.build_final_manifest(**kwargs)
            self.assertEqual(result["nouveau"]["raw_module_sha256"], "5" * 64)
            self.assertEqual(result["nouveau"]["installed_uncompressed_sha256"], "6" * 64)
            self.assertEqual(result["initramfs"]["embedded_uncompressed_sha256"], "6" * 64)
            self.assertEqual(result["status"], "FINALIZED_NOT_REBOOTED_NOT_RUNTIME_VERIFIED")
            self.assertEqual(result["review_branch"], build["review_branch"])
            self.assertEqual(result["main_commit"], build["main_commit"])
            self.assertEqual(result["deployment_plan"]["sha256"], "a" * 64)

            kwargs["embedded"] = [{
                "path_in_initramfs": "main/nouveau.ko.zst",
                "uncompressed_sha256": "9" * 64,
            }]
            with self.assertRaisesRegex(RuntimeError, "do not exactly match"):
                FINALIZER.build_final_manifest(**kwargs)


if __name__ == "__main__":
    unittest.main()
