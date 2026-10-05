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


class DeploymentTemporaryDirectoryTests(unittest.TestCase):
    def test_large_initramfs_workspace_uses_var_tmp(self):
        with FINALIZER.deployment_temporary_directory() as temporary:
            self.assertEqual(
                Path(temporary).parent.resolve(),
                Path("/var/tmp").resolve(),
            )


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


class ReviewHeadPinTests(unittest.TestCase):
    def test_apply_review_head_must_match_plan(self):
        plan = {
            "expected_review_branch": "review/gk104-vaapi-selftest-v8-20261002",
            "expected_main_commit": "7b44b1c1e282eac7c54c1cfa8c758118cd66312c",
            "expected_review_head": "1" * 40,
        }
        with patch.object(
            FINALIZER,
            "command_output",
            side_effect=[
                plan["expected_review_branch"],
                plan["expected_main_commit"],
                "",
                plan["expected_review_head"],
            ],
        ):
            self.assertEqual(FINALIZER.current_review_head(plan), "1" * 40)

        with patch.object(
            FINALIZER,
            "command_output",
            side_effect=[
                plan["expected_review_branch"],
                plan["expected_main_commit"],
                "",
                "2" * 40,
            ],
        ):
            with self.assertRaisesRegex(RuntimeError, "review HEAD does not match"):
                FINALIZER.current_review_head(plan)


class ModuleCompressionTests(unittest.TestCase):
    def test_zstd_module_compress_decompress_round_trip(self):
        with tempfile.TemporaryDirectory(prefix="module-compression-test-") as temporary:
            root = Path(temporary)
            source = root / "nouveau.ko"
            compressed = root / "nouveau.ko.zst"
            restored = root / "restored-nouveau.ko"
            payload = (b"Nouveau diagnostic module fixture\0" * 4096)
            source.write_bytes(payload)

            FINALIZER.compress_module(source, compressed)
            FINALIZER.decompress_module(compressed, restored)

            self.assertEqual(restored.read_bytes(), payload)

    def test_large_module_decompression_has_a_bounded_five_minute_timeout(self):
        with tempfile.TemporaryDirectory(prefix="module-decompression-timeout-test-") as temporary:
            root = Path(temporary)
            compressed = root / "nouveau.ko.zst"
            compressed.write_bytes(b"compressed module fixture")
            with patch.object(FINALIZER.subprocess, "run") as run:
                FINALIZER.decompress_module(compressed, root / "restored.ko")
            self.assertEqual(run.call_args.kwargs["timeout"], 300)


class EarlyModuleOptionsTests(unittest.TestCase):
    def test_module_options_parser_accepts_exact_ambient_parameters(self):
        self.assertEqual(
            FINALIZER.parse_module_options(
                "options nouveau diag_bar2_map=Y diag_ctxsw=N\n"
            ),
            {"diag_bar2_map": "Y", "diag_ctxsw": "N"},
        )

    def test_module_options_parser_rejects_duplicate_or_extra_directives(self):
        for payload in (
            "options nouveau diag_bar2_map=Y diag_bar2_map=N\n",
            "options nouveau diag_bar2_map=Y\noptions nouveau diag_ctxsw=N\n",
            "options nouveau diag_bar2_map=Y stale_option=N\n",
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    parsed = FINALIZER.parse_module_options(payload)
                    if parsed != {"diag_bar2_map": "Y", "diag_ctxsw": "N"}:
                        raise ValueError("module option set mismatch")

    def test_ambient_module_options_are_exact_and_hash_pinned(self):
        params = {"diag_bar2_map": "Y", "diag_ctxsw": "N"}
        content = "options nouveau diag_bar2_map=Y diag_ctxsw=N\n"
        options = {
            "path": "/etc/modprobe.d/99-nouveau-ambient-bar2-0011.conf",
            "content": content,
            "sha256": FINALIZER.sha256_file_from_bytes(content.encode("ascii")),
        }
        retired = {
            "path": "/etc/modprobe.d/99-nouveau-diag4-v3.conf",
            "sha256": "9" * 64,
            "backup_path": "/tmp/retired-options.original",
        }
        FINALIZER.validate_module_options(params, options, retired)

        options["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "content hash mismatch"):
            FINALIZER.validate_module_options(params, options, retired)

    def test_dracut_config_pins_the_exact_early_options_file(self):
        options = {
            "path": "/etc/modprobe.d/99-nouveau-ambient-bar2-0011.conf",
        }
        content = FINALIZER.expected_dracut_config_content(options)
        config = {
            "path": "/etc/dracut.conf.d/99-nouveau-ambient-bar2-0011.conf",
            "content": content,
            "sha256": FINALIZER.sha256_file_from_bytes(content.encode("ascii")),
        }
        FINALIZER.validate_dracut_config(options, config)

        config["content"] = 'install_items+=" /etc/modprobe.d/other.conf "\n'
        with self.assertRaisesRegex(ValueError, "does not install the exact"):
            FINALIZER.validate_dracut_config(options, config)

    def test_apply_dracut_config_is_exact_and_resumable(self):
        with tempfile.TemporaryDirectory(prefix="dracut-options-test-") as temporary:
            root = Path(temporary)
            path = root / "99-nouveau-ambient-bar2-0011.conf"
            content = 'install_items+=" /etc/modprobe.d/99-nouveau-ambient-bar2-0011.conf "\n'
            plan = {
                "dracut_config": {
                    "path": str(path),
                    "content": content,
                    "sha256": FINALIZER.sha256_file_from_bytes(content.encode("ascii")),
                }
            }
            first = FINALIZER.apply_dracut_config(plan)
            second = FINALIZER.apply_dracut_config(plan)
            self.assertEqual(path.read_text(encoding="ascii"), content)
            self.assertEqual(first, second)

            path.write_text('install_items+=" /etc/modprobe.d/unexpected.conf "\n')
            with self.assertRaisesRegex(RuntimeError, "different bytes"):
                FINALIZER.apply_dracut_config(plan)

    def test_apply_ambient_options_archives_old_file_and_leaves_one_active_config(self):
        with tempfile.TemporaryDirectory(prefix="apply-ambient-options-test-") as temporary:
            root = Path(temporary)
            retired = root / "99-old-nouveau.conf"
            target = root / "99-nouveau-ambient-bar2-0011.conf"
            backup = root / "retired-options.original"
            original = b"options nouveau diag_bar2_map=Y obsolete=N diag_ctxsw=N\n"
            content = "options nouveau diag_bar2_map=Y diag_ctxsw=N\n"
            retired.write_bytes(original)
            plan = {
                "module_parameters": {"diag_bar2_map": "Y", "diag_ctxsw": "N"},
                "module_options": {
                    "path": str(target),
                    "content": content,
                    "sha256": FINALIZER.sha256_file_from_bytes(content.encode("ascii")),
                },
                "retired_module_options": {
                    "path": str(retired),
                    "sha256": FINALIZER.sha256_file_from_bytes(original),
                    "backup_path": str(backup),
                },
            }
            manifest = root / "deployment-manifest.json"
            result = FINALIZER.apply_module_options(
                plan, manifest, config_directories=[root]
            )
            self.assertEqual(backup.read_bytes(), original)
            self.assertEqual(target.read_text(), content)
            self.assertTrue(retired.read_text().startswith("# Superseded"))
            self.assertEqual(
                FINALIZER.active_nouveau_option_files([root]),
                {target.resolve()},
            )
            self.assertEqual(result["content_sha256"], plan["module_options"]["sha256"])
            resumed = FINALIZER.apply_module_options(
                plan, manifest, config_directories=[root]
            )
            self.assertEqual(resumed, result)

    def test_apply_ambient_options_resumes_after_backup_and_retire_step(self):
        with tempfile.TemporaryDirectory(prefix="resume-ambient-options-test-") as temporary:
            root = Path(temporary)
            retired = root / "99-old-nouveau.conf"
            target = root / "99-nouveau-ambient-bar2-0011.conf"
            backup = root / "retired-options.original"
            original = b"options nouveau diag_bar2_map=Y obsolete=N diag_ctxsw=N\n"
            content = "options nouveau diag_bar2_map=Y diag_ctxsw=N\n"
            retired.write_bytes(b"# Superseded by the ambient BAR2 diagnostic deployment.\n")
            backup.write_bytes(original)
            plan = {
                "module_parameters": {"diag_bar2_map": "Y", "diag_ctxsw": "N"},
                "module_options": {
                    "path": str(target),
                    "content": content,
                    "sha256": FINALIZER.sha256_file_from_bytes(content.encode("ascii")),
                },
                "retired_module_options": {
                    "path": str(retired),
                    "sha256": FINALIZER.sha256_file_from_bytes(original),
                    "backup_path": str(backup),
                },
            }
            result = FINALIZER.apply_module_options(
                plan, root / "deployment-manifest.json", config_directories=[root]
            )
            self.assertEqual(target.read_text(encoding="ascii"), content)
            self.assertEqual(backup.read_bytes(), original)
            self.assertEqual(
                result["superseded"]["original_sha256"],
                plan["retired_module_options"]["sha256"],
            )

    def test_preflight_rejects_competing_options_without_mutation(self):
        with tempfile.TemporaryDirectory(prefix="preflight-ambient-options-test-") as temporary:
            root = Path(temporary)
            retired = root / "99-old-nouveau.conf"
            competing = root / "10-other-nouveau.conf"
            target = root / "99-nouveau-ambient-bar2-0011.conf"
            backup = root / "retired-options.original"
            original = b"options nouveau diag_bar2_map=Y obsolete=N diag_ctxsw=N\n"
            retired.write_bytes(original)
            competing.write_text("options nouveau modeset=1\n", encoding="ascii")
            content = "options nouveau diag_bar2_map=Y diag_ctxsw=N\n"
            plan = {
                "module_parameters": {"diag_bar2_map": "Y", "diag_ctxsw": "N"},
                "module_options": {
                    "path": str(target),
                    "content": content,
                    "sha256": FINALIZER.sha256_file_from_bytes(content.encode("ascii")),
                },
                "retired_module_options": {
                    "path": str(retired),
                    "sha256": FINALIZER.sha256_file_from_bytes(original),
                    "backup_path": str(backup),
                },
            }
            with self.assertRaisesRegex(RuntimeError, "additional Nouveau modprobe"):
                FINALIZER.preflight_module_options(
                    plan, root / "deployment-manifest.json", config_directories=[root]
                )
            self.assertEqual(retired.read_bytes(), original)
            self.assertFalse(target.exists())
            self.assertFalse(backup.exists())

    def test_preflight_ignores_unrelated_multiline_modprobe_directive(self):
        with tempfile.TemporaryDirectory(prefix="unrelated-modprobe-test-") as temporary:
            root = Path(temporary)
            retired = root / "99-old-nouveau.conf"
            unrelated = root / "iwlwifi.conf"
            target = root / "99-nouveau-ambient-bar2-0011.conf"
            backup = root / "retired-options.original"
            original = b"options nouveau diag_bar2_map=Y obsolete=N diag_ctxsw=N\n"
            retired.write_bytes(original)
            unrelated.write_text(
                """remove iwlwifi \\
(/sbin/lsmod | grep -o -e ^iwlmvm) \\
&& /sbin/modprobe -r mac80211
""",
                encoding="ascii",
            )
            content = "options nouveau diag_bar2_map=Y diag_ctxsw=N\n"
            plan = {
                "module_parameters": {"diag_bar2_map": "Y", "diag_ctxsw": "N"},
                "module_options": {
                    "path": str(target),
                    "content": content,
                    "sha256": FINALIZER.sha256_file_from_bytes(content.encode("ascii")),
                },
                "retired_module_options": {
                    "path": str(retired),
                    "sha256": FINALIZER.sha256_file_from_bytes(original),
                    "backup_path": str(backup),
                },
            }
            state = FINALIZER.preflight_module_options(
                plan, root / "deployment-manifest.json", config_directories=[root]
            )
            self.assertEqual(state["original_sha"], plan["retired_module_options"]["sha256"])

    def test_embedded_module_options_require_exact_bytes_and_parameters(self):
        with tempfile.TemporaryDirectory(prefix="embedded-options-test-") as temporary:
            root = Path(temporary)
            initramfs = root / "initrd.img"
            initramfs.write_bytes(b"initramfs fixture")
            payload = b"options nouveau diag_bar2_map=Y diag_ctxsw=N\n"
            expected = FINALIZER.sha256_file_from_bytes(payload)

            def fake_unmkinitramfs(argv, **_kwargs):
                extract = Path(argv[2])
                config = extract / "main/etc/modprobe.d/99-nouveau-ambient-bar2-0011.conf"
                config.parent.mkdir(parents=True)
                config.write_bytes(payload)
                unrelated = extract / "main/etc/modprobe.d/iwlwifi.conf"
                unrelated.write_text(
                    """remove iwlwifi \\
(/sbin/lsmod | grep -o -e ^iwlmvm) \\
&& /sbin/modprobe -r mac80211
""",
                    encoding="ascii",
                )

            with patch.object(FINALIZER.subprocess, "run", side_effect=fake_unmkinitramfs):
                records = FINALIZER.embedded_module_options(
                    initramfs,
                    Path("/etc/modprobe.d/99-nouveau-ambient-bar2-0011.conf"),
                    expected,
                    {"diag_bar2_map": "Y", "diag_ctxsw": "N"},
                    root / "extract-good",
                )
                self.assertEqual(len(records or []), 1)
                self.assertIsNone(FINALIZER.embedded_module_options(
                    initramfs,
                    Path("/etc/modprobe.d/99-nouveau-ambient-bar2-0011.conf"),
                    "0" * 64,
                    {"diag_bar2_map": "Y", "diag_ctxsw": "N"},
                    root / "extract-bad-hash",
                ))
                self.assertIsNone(FINALIZER.embedded_module_options(
                    initramfs,
                    Path("/etc/modprobe.d/99-nouveau-ambient-bar2-0011.conf"),
                    expected,
                    {"diag_ctxsw": "N"},
                    root / "extract-bad-params",
                ))


class ExistingInitramfsTests(unittest.TestCase):
    def test_embedded_module_extraction_creates_nested_temp_root(self):
        with tempfile.TemporaryDirectory(prefix="initramfs-extract-test-") as temporary:
            root = Path(temporary)
            initramfs = root / "initrd.img"
            initramfs.write_bytes(b"initramfs fixture")
            temporary_root = root / "new-parent" / "check"
            payload = b"embedded module bytes"

            def fake_unmkinitramfs(argv, **_kwargs):
                extract = Path(argv[2])
                module = extract / "main" / "nouveau.ko"
                module.parent.mkdir(parents=True)
                module.write_bytes(payload)

            with patch.object(
                FINALIZER.subprocess,
                "run",
                side_effect=fake_unmkinitramfs,
            ):
                records = FINALIZER.embedded_module_hashes(
                    initramfs, temporary_root
                )

            self.assertEqual(
                records,
                [{
                    "path_in_initramfs": "main/nouveau.ko",
                    "uncompressed_sha256": FINALIZER.hashlib.sha256(payload).hexdigest(),
                }],
            )

    def test_matching_embedded_module_is_reused(self):
        with tempfile.TemporaryDirectory(prefix="initramfs-match-test-") as temporary:
            root = Path(temporary)
            initramfs = root / "initrd.img"
            initramfs.write_bytes(b"initramfs fixture")
            expected = "a" * 64
            records = [{"path_in_initramfs": "main/nouveau.ko.zst", "uncompressed_sha256": expected}]
            with patch.object(FINALIZER, "embedded_module_hashes", return_value=records):
                result = FINALIZER.matching_embedded_modules(
                    initramfs, expected, root / "extract"
                )
            self.assertEqual(result, records)

    def test_missing_or_mismatched_embedded_module_requires_rebuild(self):
        with tempfile.TemporaryDirectory(prefix="initramfs-mismatch-test-") as temporary:
            root = Path(temporary)
            initramfs = root / "initrd.img"
            initramfs.write_bytes(b"initramfs fixture")
            mismatch = [{
                "path_in_initramfs": "main/nouveau.ko.zst",
                "uncompressed_sha256": "b" * 64,
            }]
            with patch.object(FINALIZER, "embedded_module_hashes", return_value=mismatch):
                result = FINALIZER.matching_embedded_modules(
                    initramfs, "a" * 64, root / "extract"
                )
            self.assertIsNone(result)
            self.assertIsNone(
                FINALIZER.matching_embedded_modules(
                    root / "missing-initrd.img", "a" * 64, root / "missing-check"
                )
            )


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
                "embedded_options": None,
                "installed_options": None,
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

            option_content = "options nouveau diag_bar2_map=Y diag_ctxsw=N\n"
            option_hash = FINALIZER.sha256_file_from_bytes(option_content.encode("ascii"))
            build["patch_sha256"]["0011-drm-nouveau-trace-ambient-bar2-lifetimes.patch"] = "d" * 64
            plan["review_head"] = "e" * 40
            plan["module_parameters"] = {"diag_bar2_map": "Y", "diag_ctxsw": "N"}
            plan["module_options"] = {
                "path": "/etc/modprobe.d/99-nouveau-ambient-bar2-0011.conf",
                "content": option_content,
                "sha256": option_hash,
            }
            dracut_content = FINALIZER.expected_dracut_config_content(
                plan["module_options"]
            )
            dracut_hash = FINALIZER.sha256_file_from_bytes(
                dracut_content.encode("ascii")
            )
            plan["dracut_config"] = {
                "path": "/etc/dracut.conf.d/99-nouveau-ambient-bar2-0011.conf",
                "content": dracut_content,
                "sha256": dracut_hash,
            }
            kwargs["installed_dracut_config"] = {
                "path": plan["dracut_config"]["path"],
                "content_sha256": dracut_hash,
            }
            kwargs["embedded_options"] = [{
                "path_in_initramfs": "main/etc/modprobe.d/99-nouveau-ambient-bar2-0011.conf",
                "sha256": option_hash,
            }]
            kwargs["installed_options"] = {
                "path": plan["module_options"]["path"],
                "content_sha256": option_hash,
                "parameters": plan["module_parameters"],
                "superseded": {"original_sha256": "c" * 64},
            }
            ambient_result = FINALIZER.build_final_manifest(**kwargs)
            self.assertEqual(ambient_result["review_head"], "e" * 40)
            self.assertEqual(
                ambient_result["driver_source_head"], build["review_commit"]
            )
            self.assertEqual(
                ambient_result["source"]["patch_0011_sha256"], "d" * 64
            )
            self.assertEqual(
                ambient_result["module_options"]["content_sha256"], option_hash
            )
            self.assertEqual(
                ambient_result["initramfs"]["embedded_module_options"],
                kwargs["embedded_options"],
            )
            self.assertEqual(
                ambient_result["dracut_config"]["content_sha256"],
                dracut_hash,
            )

            kwargs["embedded"] = [{
                "path_in_initramfs": "main/nouveau.ko.zst",
                "uncompressed_sha256": "9" * 64,
            }]
            with self.assertRaisesRegex(RuntimeError, "do not exactly match"):
                FINALIZER.build_final_manifest(**kwargs)


if __name__ == "__main__":
    unittest.main()
