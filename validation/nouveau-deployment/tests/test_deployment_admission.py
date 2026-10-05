import importlib.util
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("admit_run", ROOT / "admit_run.py")
assert SPEC is not None and SPEC.loader is not None
ADMISSION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ADMISSION)


def fixtures():
    manifest = {
        "schema": 2,
        "status": "FINALIZED_NOT_REBOOTED_NOT_RUNTIME_VERIFIED",
        "build_manifest": {"path": "/repo/build.json", "sha256": "4" * 64},
        "deployment_plan": {"path": "/repo/plan.json", "sha256": "5" * 64},
        "kernel": "7.0.0-34-generic",
        "nouveau": {
            "module_path": "/lib/modules/7.0.0-34-generic/updates/dkms/nouveau.ko.zst",
            "raw_module_sha256": "a" * 64,
            "installed_uncompressed_sha256": "b" * 64,
            "installed_compressed_sha256": "c" * 64,
            "srcversion": "936407678F3DA1E8515F5EC",
            "vermagic": "7.0.0-34-generic SMP preempt mod_unload modversions",
            "parameters": {"diag_ctxsw": "N"},
        },
        "initramfs": {
            "path": "/boot/initrd.img-7.0.0-34-generic",
            "sha256": "d" * 64,
            "embedded_uncompressed_sha256": "b" * 64,
        },
        "mesa": {"variants": {
            "A": {"path": "/private/A.so", "sha256": "e" * 64},
            "B": {"path": "/private/B.so", "sha256": "f" * 64},
        }},
        "tools": {
            "supervisor": {"path": "/repo/supervisor.py", "sha256": "1" * 64},
            "bar2_correlator": {"path": "/repo/correlator.py", "sha256": "2" * 64},
        },
        "input": {"path": "/media/test.mkv", "sha256": "3" * 64},
        "software_reference": {
            "manifest_path": "/media/software-manifest.json",
            "manifest_sha256": "6" * 64,
            "container_path": "/media/software.nut",
            "container_sha256": "7" * 64,
        },
    }
    snapshot = {
        "kernel_release": "7.0.0-34-generic",
        "boot_id": "11223344-5566-7788-99aa-bbccddeeff00",
        "loaded_srcversion": "936407678F3DA1E8515F5EC",
        "selected_module_path": "/lib/modules/7.0.0-34-generic/updates/dkms/nouveau.ko.zst",
        "installed_compressed_sha256": "c" * 64,
        "installed_uncompressed_sha256": "b" * 64,
        "installed_srcversion": "936407678F3DA1E8515F5EC",
        "vermagic": "7.0.0-34-generic SMP preempt mod_unload modversions",
        "diag_ctxsw": "N",
        "initramfs_path": "/boot/initrd.img-7.0.0-34-generic",
        "initramfs_sha256": "d" * 64,
        "embedded_nouveau_sha256": "b" * 64,
        "journal_visible": True,
        "journal_current_boot": True,
        "journal_detail": "current-boot kernel journal verified",
        "bar2_pte_count": 0,
        "hard_stops": [],
        "desktop_errors": [],
        "players": [],
        "mesa_hashes": {"A": "e" * 64, "B": "f" * 64},
        "tool_hashes": {"supervisor": "1" * 64, "bar2_correlator": "2" * 64},
        "input_sha256": "3" * 64,
        "build_manifest_sha256": "4" * 64,
        "deployment_plan_sha256": "5" * 64,
        "software_reference_manifest_sha256": "6" * 64,
        "software_reference_container_sha256": "7" * 64,
    }
    return manifest, snapshot


class AdmissionTests(unittest.TestCase):
    def test_running_admission_and_supervisor_are_hash_pinned(self):
        manifest, _snapshot = fixtures()
        with tempfile.TemporaryDirectory(prefix="admission-tools-test-") as temporary:
            root = Path(temporary)
            admission = root / "admit_run.py"
            supervisor = root / "detached_mpv_supervisor.py"
            admission.write_text("admission source\n", encoding="utf-8")
            supervisor.write_text("supervisor source\n", encoding="utf-8")
            manifest["tools"]["admission_check"] = {
                "path": str(admission),
                "sha256": hashlib.sha256(admission.read_bytes()).hexdigest(),
            }
            manifest["tools"]["supervisor"] = {
                "path": str(supervisor),
                "sha256": hashlib.sha256(supervisor.read_bytes()).hexdigest(),
            }
            ADMISSION.verify_execution_identity(
                manifest,
                {"admission_check": admission, "supervisor": supervisor},
            )
            supervisor.write_text("changed supervisor\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "supervisor SHA-256 mismatch"):
                ADMISSION.verify_execution_identity(
                    manifest,
                    {"admission_check": admission, "supervisor": supervisor},
                )

    def test_running_admission_path_must_match_manifest(self):
        manifest, _snapshot = fixtures()
        with tempfile.TemporaryDirectory(prefix="admission-path-test-") as temporary:
            root = Path(temporary)
            admission = root / "admit_run.py"
            supervisor = root / "detached_mpv_supervisor.py"
            wrong_admission = root / "stale_admit_run.py"
            for path in (admission, supervisor, wrong_admission):
                path.write_text(path.name, encoding="utf-8")
            manifest["tools"]["admission_check"] = {
                "path": str(admission),
                "sha256": hashlib.sha256(admission.read_bytes()).hexdigest(),
            }
            manifest["tools"]["supervisor"] = {
                "path": str(supervisor),
                "sha256": hashlib.sha256(supervisor.read_bytes()).hexdigest(),
            }
            with self.assertRaisesRegex(ValueError, "admission_check path mismatch"):
                ADMISSION.verify_execution_identity(
                    manifest,
                    {"admission_check": wrong_admission, "supervisor": supervisor},
                )

    def test_journal_parser_requires_current_kernel_transport(self):
        line = lambda transport, boot: json.dumps({
            "_TRANSPORT": transport,
            "_BOOT_ID": boot,
            "MESSAGE": "kernel record",
        })
        visible, same_boot, count, _messages, _detail = ADMISSION.parse_journal_json(
            line("kernel", "112233445566778899aabbccddeeff00"),
            "112233445566778899aabbccddeeff00",
        )
        self.assertTrue(visible)
        self.assertTrue(same_boot)
        self.assertEqual(count, 0)

        visible, same_boot, _count, _messages, _detail = ADMISSION.parse_journal_json(
            line("syslog", "112233445566778899aabbccddeeff00"),
            "112233445566778899aabbccddeeff00",
        )
        self.assertFalse(visible)
        self.assertFalse(same_boot)

        visible, same_boot, _count, _messages, detail = ADMISSION.parse_journal_json(
            line("kernel", "ffeeddccbbaa99887766554433221100"),
            "112233445566778899aabbccddeeff00",
        )
        self.assertTrue(visible)
        self.assertFalse(same_boot)
        self.assertIn("different boot ID", detail)

    def test_exact_snapshot_is_eligible(self):
        manifest, snapshot = fixtures()
        self.assertEqual(ADMISSION.evaluate_snapshot(manifest, snapshot), [])

    def test_ambient_fault_archive_does_not_admit_workloads(self):
        manifest, snapshot = fixtures()
        manifest["nouveau"]["parameters"] = {
            "diag_bar2_map": "Y",
            "diag_ctxsw": "N",
        }
        options_entries = [{
            "path_in_initramfs": "main/etc/modprobe.d/99-nouveau-ambient-bar2-0011.conf",
            "sha256": "8" * 64,
        }]
        manifest["module_options"] = {
            "path": "/etc/modprobe.d/99-nouveau-ambient-bar2-0011.conf",
            "content_sha256": "9" * 64,
            "embedded_initramfs_entries": options_entries,
        }
        manifest["tools"]["ambient_correlator"] = {
            "path": "/repo/ambient-correlator.py",
            "sha256": "a" * 64,
        }
        snapshot["module_parameters"] = {"diag_bar2_map": "Y", "diag_ctxsw": "N"}
        snapshot["module_options_file_sha256"] = "9" * 64
        snapshot["embedded_module_options"] = options_entries
        snapshot["tool_hashes"]["ambient_correlator"] = "a" * 64
        snapshot["bar2_pte_count"] = 1
        snapshot["hard_stops"] = ["BAR2/HOST_CPU/PTE"]
        snapshot["ambient_fault_correlation_parseable"] = True
        snapshot["ambient_fault_correlation_report"] = {
            "fault_count": 1,
            "module_load_epoch_policy": "single_nouveau_module_instance_per_boot",
            "diagnostic_sequence_gaps_rejected": True,
        }
        self.assertEqual(
            ADMISSION.evaluate_ambient_capture(
                manifest, snapshot, confirmed_no_reload=True
            ),
            [],
        )
        run_reasons = ADMISSION.evaluate_snapshot(manifest, snapshot)
        self.assertTrue(any("BAR2/HOST_CPU/PTE count" in reason for reason in run_reasons))
        self.assertIn("current-boot hard-stop signatures are present", run_reasons)
        capture_reasons = ADMISSION.evaluate_ambient_capture(
            manifest, snapshot, confirmed_no_reload=False
        )
        self.assertIn(
            "operator has not confirmed that Nouveau was not unloaded or reloaded",
            capture_reasons,
        )

    def test_ambient_capture_rejects_wrong_parameter_or_epoch_policy(self):
        manifest, snapshot = fixtures()
        manifest["nouveau"]["parameters"] = {
            "diag_bar2_map": "Y",
            "diag_ctxsw": "N",
        }
        manifest["module_options"] = {
            "content_sha256": "9" * 64,
            "embedded_initramfs_entries": [],
        }
        snapshot["module_parameters"] = {"diag_bar2_map": "N", "diag_ctxsw": "N"}
        snapshot["module_options_file_sha256"] = "9" * 64
        snapshot["embedded_module_options"] = []
        snapshot["bar2_pte_count"] = 1
        snapshot["ambient_fault_correlation_parseable"] = True
        snapshot["ambient_fault_correlation_report"] = {
            "fault_count": 1,
            "module_load_epoch_policy": "multiple_or_unknown",
            "diagnostic_sequence_gaps_rejected": False,
        }
        reasons = ADMISSION.evaluate_ambient_capture(
            manifest, snapshot, confirmed_no_reload=True
        )
        self.assertIn("loaded diag_bar2_map parameter mismatch", reasons)
        self.assertIn("ambient correlation does not use the single-module-epoch policy", reasons)
        self.assertIn("ambient diagnostic sequence was not validated", reasons)

    def test_ambient_parser_rejects_wrong_boot_and_unparseable_journal(self):
        with tempfile.TemporaryDirectory(prefix="ambient-parser-admission-test-") as temporary:
            path = Path(temporary) / "correlator.py"
            path.write_text("# pinned correlator fixture\n", encoding="ascii")
            manifest = {"tools": {"ambient_correlator": {
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }}}
            report = {
                "boot_id": "112233445566778899aabbccddeeff00",
                "fault_count": 1,
                "module_load_epoch_policy": "single_nouveau_module_instance_per_boot",
                "diagnostic_sequence_gaps_rejected": True,
            }

            def fake_command(_argv, timeout=20):
                return subprocess.CompletedProcess([], 0, json.dumps(report), "")

            with patch.object(ADMISSION, "command", side_effect=fake_command):
                okay, parsed, _detail = ADMISSION.parse_ambient_journal(
                    "journal fixture", manifest,
                    "11223344-5566-7788-99aa-bbccddeeff00",
                )
            self.assertTrue(okay)
            self.assertEqual(parsed["fault_count"], 1)

            report["boot_id"] = "ffeeddccbbaa99887766554433221100"
            with patch.object(ADMISSION, "command", side_effect=fake_command):
                okay, _parsed, detail = ADMISSION.parse_ambient_journal(
                    "journal fixture", manifest,
                    "11223344-5566-7788-99aa-bbccddeeff00",
                )
            self.assertFalse(okay)
            self.assertIn("boot ID mismatch", detail)

    def test_wrong_module_hash_or_selected_path_refuses(self):
        manifest, snapshot = fixtures()
        snapshot["installed_compressed_sha256"] = "0" * 64
        snapshot["selected_module_path"] = "/lib/modules/old/nouveau.ko.zst"
        reasons = ADMISSION.evaluate_snapshot(manifest, snapshot)
        self.assertIn("installed compressed module hash mismatch", reasons)
        self.assertIn("modinfo-selected module path mismatch", reasons)

    def test_wrong_loaded_or_installed_srcversion_refuses(self):
        manifest, snapshot = fixtures()
        snapshot["loaded_srcversion"] = "OLD"
        snapshot["installed_srcversion"] = "OLD"
        reasons = ADMISSION.evaluate_snapshot(manifest, snapshot)
        self.assertIn("loaded Nouveau srcversion mismatch", reasons)
        self.assertIn("selected installed module srcversion mismatch", reasons)

    def test_wrong_initramfs_or_embedded_module_refuses(self):
        manifest, snapshot = fixtures()
        snapshot["initramfs_sha256"] = "0" * 64
        snapshot["embedded_nouveau_sha256"] = "0" * 64
        reasons = ADMISSION.evaluate_snapshot(manifest, snapshot)
        self.assertIn("initramfs hash mismatch", reasons)
        self.assertIn("initramfs embedded Nouveau hash mismatch", reasons)

    def test_missing_or_wrong_boot_journal_visibility_refuses(self):
        manifest, snapshot = fixtures()
        snapshot["journal_visible"] = False
        snapshot["journal_current_boot"] = False
        reasons = ADMISSION.evaluate_snapshot(manifest, snapshot)
        self.assertIn("current-boot kernel journal visibility not proven", reasons)
        self.assertIn("kernel journal boot identity not proven", reasons)

    def test_bar2_fault_and_hard_stop_refuse(self):
        manifest, snapshot = fixtures()
        snapshot["bar2_pte_count"] = 1
        snapshot["hard_stops"] = ["CTXSW_TIMEOUT"]
        reasons = ADMISSION.evaluate_snapshot(manifest, snapshot)
        self.assertIn("BAR2/HOST_CPU/PTE count is 1", reasons)
        self.assertIn("current-boot hard-stop signatures are present", reasons)

    def test_missing_desktop_or_display_refuses(self):
        manifest, snapshot = fixtures()
        snapshot["desktop_errors"] = ["DISPLAY is missing"]
        self.assertIn(
            "DISPLAY is missing",
            ADMISSION.evaluate_snapshot(manifest, snapshot),
        )

    def test_wrong_dso_or_tool_hash_refuses(self):
        manifest, snapshot = fixtures()
        snapshot["mesa_hashes"]["A"] = "0" * 64
        snapshot["tool_hashes"]["supervisor"] = "0" * 64
        reasons = ADMISSION.evaluate_snapshot(manifest, snapshot)
        self.assertIn("Mesa A/B DSO path/hash set mismatch", reasons)
        self.assertIn("supervisor/correlator hash set mismatch", reasons)

    def test_wrong_input_param_or_competing_player_refuses(self):
        manifest, snapshot = fixtures()
        snapshot["input_sha256"] = "0" * 64
        snapshot["diag_ctxsw"] = "Y"
        snapshot["players"] = ["123:mpv"]
        reasons = ADMISSION.evaluate_snapshot(manifest, snapshot)
        self.assertIn("media input hash mismatch", reasons)
        self.assertIn("loaded diag_ctxsw parameter mismatch", reasons)
        self.assertIn("media/player process is already running", reasons)

    def test_changed_build_plan_or_software_reference_refuses(self):
        manifest, snapshot = fixtures()
        snapshot["build_manifest_sha256"] = "0" * 64
        snapshot["deployment_plan_sha256"] = "0" * 64
        snapshot["software_reference_manifest_sha256"] = "0" * 64
        snapshot["software_reference_container_sha256"] = "0" * 64
        reasons = ADMISSION.evaluate_snapshot(manifest, snapshot)
        self.assertIn("retained Nouveau build-manifest hash mismatch", reasons)
        self.assertIn("deployment plan hash mismatch", reasons)
        self.assertIn("software NV12 reference manifest hash mismatch", reasons)
        self.assertIn("software NV12 reference container hash mismatch", reasons)

    def test_desktop_gate_requires_current_users_real_local_x11_vt(self):
        env = {"XDG_SESSION_ID": "4", "DISPLAY": ":0"}
        session = (
            "Active=yes\nRemote=no\nType=x11\nClass=user\nSeat=seat0\n"
            f"User={os.getuid()}\nVTNr=2\nDisplay=:0\n"
        )

        def fake_command(argv, timeout=20):
            if argv[0] == "loginctl":
                return subprocess.CompletedProcess(argv, 0, session, "")
            return subprocess.CompletedProcess(argv, 0, "X11 reachable", "")

        with patch.object(ADMISSION, "command", side_effect=fake_command):
            self.assertEqual(ADMISSION.session_errors(env), [])

        wrong_session = session.replace(
            f"User={os.getuid()}", "User=0"
        ).replace("VTNr=2", "VTNr=0")

        def wrong_command(argv, timeout=20):
            if argv[0] == "loginctl":
                return subprocess.CompletedProcess(argv, 0, wrong_session, "")
            return subprocess.CompletedProcess(argv, 0, "X11 reachable", "")

        with patch.object(ADMISSION, "command", side_effect=wrong_command):
            errors = ADMISSION.session_errors(env)
        self.assertIn("desktop session belongs to a different Unix user", errors)
        self.assertIn("desktop session is not attached to a real VT", errors)

    def test_sudo_admission_checks_the_invoking_desktop_users_uid(self):
        env = {
            "XDG_SESSION_ID": "7",
            "DISPLAY": ":0",
            "SUDO_UID": "1000",
        }
        session = (
            "Active=yes\nRemote=no\nType=x11\nClass=user\nSeat=seat0\n"
            "User=1000\nVTNr=2\nDisplay=:0\n"
        )

        def fake_command(argv, timeout=20):
            if argv[0] == "loginctl":
                return subprocess.CompletedProcess(argv, 0, session, "")
            return subprocess.CompletedProcess(argv, 0, "X11 reachable", "")

        with (
            patch.object(ADMISSION.os, "geteuid", return_value=0),
            patch.object(ADMISSION, "command", side_effect=fake_command),
        ):
            self.assertEqual(ADMISSION.session_errors(env), [])

    def test_root_admission_without_sudo_uid_fails_closed(self):
        env = {"XDG_SESSION_ID": "7", "DISPLAY": ":0"}
        session = (
            "Active=yes\nRemote=no\nType=x11\nClass=user\nSeat=seat0\n"
            "User=1000\nVTNr=2\nDisplay=:0\n"
        )

        def fake_command(argv, timeout=20):
            if argv[0] == "loginctl":
                return subprocess.CompletedProcess(argv, 0, session, "")
            return subprocess.CompletedProcess(argv, 0, "X11 reachable", "")

        with (
            patch.object(ADMISSION.os, "geteuid", return_value=0),
            patch.object(ADMISSION, "command", side_effect=fake_command),
        ):
            errors = ADMISSION.session_errors(env)
        self.assertIn(
            "root admission requires a non-root SUDO_UID from the desktop user",
            errors,
        )

    def test_root_admission_with_root_sudo_uid_fails_closed(self):
        env = {
            "XDG_SESSION_ID": "7",
            "DISPLAY": ":0",
            "SUDO_UID": "0",
        }
        session = (
            "Active=yes\nRemote=no\nType=x11\nClass=user\nSeat=seat0\n"
            "User=0\nVTNr=2\nDisplay=:0\n"
        )

        def fake_command(argv, timeout=20):
            if argv[0] == "loginctl":
                return subprocess.CompletedProcess(argv, 0, session, "")
            return subprocess.CompletedProcess(argv, 0, "X11 reachable", "")

        with (
            patch.object(ADMISSION.os, "geteuid", return_value=0),
            patch.object(ADMISSION, "command", side_effect=fake_command),
        ):
            errors = ADMISSION.session_errors(env)

        self.assertIn(
            "root admission requires a non-root SUDO_UID from the desktop user",
            errors,
        )


if __name__ == "__main__":
    unittest.main()
