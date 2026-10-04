from __future__ import annotations

import contextlib
import hashlib
import io
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import capture_nvif_lifetime_run as capture
import parse_nvif_lifetime_ab as correlator


class NvifLifetimeCaptureTests(unittest.TestCase):
    def test_capture_and_parser_share_termination_reason_contract(self) -> None:
        self.assertEqual(
            capture.VALID_TERMINATION_REASONS,
            correlator.VALID_TERMINATION_REASONS,
        )
        self.assertEqual(
            capture.hard_stop_termination_reason(
                [{"source": "workload", "kinds": ["SIGBUS"]}]
            ),
            "workload-hard-stop",
        )
        self.assertEqual(
            capture.hard_stop_termination_reason(
                [
                    {"source": "workload", "kinds": ["SIGBUS"]},
                    {"source": "kernel", "kinds": ["PTE"]},
                ]
            ),
            "kernel-hard-stop",
        )
        with self.assertRaisesRegex(ValueError, "at least one signature"):
            capture.hard_stop_termination_reason([])
        with self.assertRaisesRegex(ValueError, "unsupported source"):
            capture.hard_stop_termination_reason([{"source": "unknown"}])

    def test_profile_pins_saved_stage4_command_and_limits(self) -> None:
        profile = capture.load_profile()
        self.assertEqual(profile["input_sha256"], correlator.EXPECTED_INPUT_SHA256)
        self.assertEqual(profile["max_runtime_seconds"], 18)
        self.assertEqual(profile["post_stop_observation_seconds"], 30)
        self.assertEqual(
            profile["controlled_execution"]["working_directory"],
            "/home/keivan",
        )
        self.assertEqual(profile["controlled_execution"]["home"], "/home/keivan")

    def test_hard_stop_signatures_cover_bar2_and_ctxsw(self) -> None:
        self.assertEqual(
            capture.hard_stop_kinds(
                {
                    "MESSAGE": "fifo fault engine 05 [BAR2] client 07 [HOST_CPU] reason 02 [PTE]",
                    "_TRANSPORT": "kernel",
                }
            ),
            ("BAR2", "HOST_CPU", "PTE"),
        )
        self.assertIn(
            "CTXSW_TIMEOUT",
            capture.hard_stop_kinds({
                "MESSAGE": "fifo: SCHED_ERROR 0a [CTXSW_TIMEOUT]",
                "_TRANSPORT": "kernel",
            }),
        )
        self.assertIn(
            "failed-to-idle",
            capture.hard_stop_kinds({
                "MESSAGE": "Xorg failed to idle channel 9",
                "_TRANSPORT": "kernel",
            }),
        )

    def test_hard_stop_signatures_cover_privilege_bus_and_gpu_reset(self) -> None:
        cases = (
            ("fifo: PRIV_VIOLATION on channel 4", "PRIV_VIOLATION"),
            ("mpv terminated by SIGBUS", "SIGBUS"),
            ("nouveau: resetting GPU after engine failure", "GPU-reset"),
        )
        for message, expected in cases:
            with self.subTest(message=message):
                if expected == "SIGBUS":
                    record = {
                        "MESSAGE": message,
                        "SYSLOG_IDENTIFIER": "nouveau-nvif-lifetime",
                        "_TRANSPORT": "stdout",
                    }
                else:
                    record = {"MESSAGE": message, "_TRANSPORT": "kernel"}
                self.assertIn(expected, capture.hard_stop_kinds(record))

    def test_untrusted_user_text_does_not_look_like_a_kernel_hard_stop(self) -> None:
        for message in (
            "WARNING: diagnostic explanation mentions PTE",
            "user supplied text: BAR2 HOST_CPU PTE",
        ):
            with self.subTest(message=message):
                self.assertEqual(
                    capture.hard_stop_kinds({
                        "MESSAGE": message,
                        "_TRANSPORT": "journal",
                        "SYSLOG_IDENTIFIER": "unrelated-process",
                    }),
                    (),
                )

    def test_cursor_requires_one_nonempty_journal_cursor(self) -> None:
        self.assertEqual(capture.parse_cursor("-- cursor: cursor-123\n"), "cursor-123")
        for output in ("", "-- cursor: \n", "-- cursor: a\n-- cursor: b\n"):
            with self.subTest(output=output):
                with self.assertRaisesRegex(RuntimeError, "exactly one"):
                    capture.parse_cursor(output)

    def test_journal_cursor_precedes_whole_boot_preflight_snapshot(self) -> None:
        full_boot = (
            b'{"MESSAGE":"nouveau: initialized","_BOOT_ID":"boot-a",'
            b'"_TRANSPORT":"kernel"}\n'
        )
        with mock.patch.object(
            capture,
            "run_checked",
            side_effect=[b"-- cursor: cursor-123\n", full_boot],
        ) as run:
            cursor, snapshot = capture.clean_boot_journal_boundary("boot-a")

        self.assertEqual(cursor, "cursor-123")
        self.assertEqual(snapshot, full_boot)
        self.assertIn("-n", run.call_args_list[0].args[0])
        self.assertIn("-o", run.call_args_list[1].args[0])

    def test_clean_boot_preflight_rejects_existing_hard_stop(self) -> None:
        cursor = b"-- cursor: cursor-123\n"
        full_boot = (
            b'{"MESSAGE":"nouveau fifo fault [BAR2] [HOST_CPU] [PTE]",'
            b'"_BOOT_ID":"boot-a","_TRANSPORT":"kernel"}\n'
        )
        with mock.patch.object(
            capture,
            "run_checked",
            side_effect=[cursor, full_boot],
        ):
            with self.assertRaisesRegex(RuntimeError, "baseline is contaminated"):
                capture.clean_boot_journal_boundary("boot-a")

    def test_preflight_rejects_journal_from_another_boot(self) -> None:
        full_boot = (
            b'{"MESSAGE":"nouveau: initialized","_BOOT_ID":"boot-b"}\n'
        )
        with mock.patch.object(
            capture,
            "run_checked",
            side_effect=[b"-- cursor: cursor-123\n", full_boot],
        ):
            with self.assertRaisesRegex(RuntimeError, "does not match current boot ID"):
                capture.clean_boot_journal_boundary("boot-a")

    def test_preflight_requires_visible_kernel_journal_records(self) -> None:
        full_boot = (
            b'{"MESSAGE":"user message only","_BOOT_ID":"boot-a",'
            b'"_TRANSPORT":"syslog"}\n'
        )
        with mock.patch.object(
            capture,
            "run_checked",
            side_effect=[b"-- cursor: cursor-123\n", full_boot],
        ):
            with self.assertRaisesRegex(RuntimeError, "has no visible kernel records"):
                capture.clean_boot_journal_boundary("boot-a")

    def test_unrelated_user_warning_or_pte_does_not_contaminate_clean_preflight(self) -> None:
        cursor = b"-- cursor: cursor-123\n"
        full_boot = (
            b'{"MESSAGE":"nouveau: initialized","_BOOT_ID":"boot-a",'
            b'"_TRANSPORT":"kernel"}\n'
            b'{"MESSAGE":"user note: WARNING BAR2 HOST_CPU PTE",'
            b'"_BOOT_ID":"boot-a","_TRANSPORT":"journal",'
            b'"SYSLOG_IDENTIFIER":"unrelated-process"}\n'
        )
        with mock.patch.object(
            capture,
            "run_checked",
            side_effect=[cursor, full_boot],
        ):
            observed_cursor, snapshot = capture.clean_boot_journal_boundary("boot-a")

        self.assertEqual(observed_cursor, "cursor-123")
        self.assertEqual(snapshot, full_boot)

    def test_normalized_environment_has_exact_a_b_schema(self) -> None:
        observed = capture.normalized_environment(
            display=":0",
            home="/home/keivan",
            xauthority="/tmp/session-auth",
        )
        self.assertEqual(set(observed), correlator.REQUIRED_ENVIRONMENT_KEYS)
        self.assertEqual(observed["XAUTHORITY"], "<session-xauthority>")
        self.assertEqual(observed["LIBVA_DRIVERS_PATH"], "<variant-private-directory>")
        self.assertEqual(observed["LD_PRELOAD"], "<unset>")
        self.assertEqual(observed["DISPLAY"], ":0")

    def test_execution_environment_does_not_inherit_debug_overrides(self) -> None:
        env = capture.execution_environment(
            display=":0",
            home="/home/keivan",
            xauthority=Path("/tmp/session-auth"),
            driver_dir=Path("/tmp/run/driver"),
        )
        self.assertEqual(
            set(env),
            {
                "DISPLAY", "XAUTHORITY", "LIBVA_DRIVER_NAME",
                "LIBVA_DRIVERS_PATH", "HOME", "PATH", "LC_ALL",
            },
        )
        self.assertEqual(env["LIBVA_DRIVER_NAME"], "nouveau")
        self.assertNotIn("LD_PRELOAD", env)
        self.assertNotIn("MESA_LOADER_DRIVER_OVERRIDE", env)

    def test_root_only_module_parameter_uses_noninteractive_read_only_sudo(self) -> None:
        with (
            mock.patch.object(
                capture.Path,
                "read_text",
                side_effect=PermissionError("root-only sysfs parameter"),
            ),
            mock.patch.object(capture.shutil, "which", return_value="/usr/bin/sudo"),
            mock.patch.object(capture, "run_checked", return_value=b"N\n") as run,
        ):
            value = capture.read_module_parameter("diag_ctxsw")

        self.assertEqual(value, "N")
        run.assert_called_once_with(
            [
                "/usr/bin/sudo", "-n", "/usr/bin/cat",
                "/sys/module/nouveau/parameters/diag_ctxsw",
            ],
            timeout=3,
        )

    def test_disabled_boolean_parameter_is_canonicalized(self) -> None:
        for value in ("N", "0", "FALSE", "false\n"):
            with self.subTest(value=value):
                self.assertEqual(
                    capture.require_parameter_disabled("diag_ctxsw", value),
                    "N",
                )
        with self.assertRaisesRegex(RuntimeError, "must be disabled"):
            capture.require_parameter_disabled("diag_ctxsw", "Y")

    def test_installed_module_identity_fingerprints_selected_compressed_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            module = Path(temporary) / "nouveau.ko.zst"
            module.write_bytes(b"signed compressed module bytes")
            expected_hash = hashlib.sha256(module.read_bytes()).hexdigest()

            def fake_modinfo(command: list[str], *, timeout: int) -> bytes:
                self.assertEqual(command[0], "/usr/sbin/modinfo")
                self.assertEqual(timeout, 5)
                if command[2] == "filename":
                    return (str(module) + "\n").encode()
                if command[2] == "srcversion":
                    return (capture.EXPECTED_SRCVERSION + "\n").encode()
                if command[2] == "vermagic":
                    return (capture.EXPECTED_KERNEL + " SMP preempt modversions\n").encode()
                self.fail(f"unexpected modinfo field: {command[2]}")

            with (
                mock.patch.object(capture.shutil, "which", return_value="/usr/sbin/modinfo"),
                mock.patch.object(capture, "run_checked", side_effect=fake_modinfo),
            ):
                identity = capture.installed_module_identity()

        self.assertEqual(identity["path"], str(module.resolve()))
        self.assertEqual(identity["file_sha256"], expected_hash)
        self.assertEqual(identity["srcversion"], capture.EXPECTED_SRCVERSION)

    def test_installed_module_identity_rejects_wrong_srcversion_or_vermagic(self) -> None:
        for field, value, expected_error in (
            ("srcversion", "wrong-srcversion", "srcversion mismatch"),
            ("vermagic", "6.9.0 wrong-kernel", "vermagic mismatch"),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                module = Path(temporary) / "nouveau.ko.zst"
                module.write_bytes(b"module")

                def fake_modinfo(command: list[str], *, timeout: int) -> bytes:
                    if command[2] == "filename":
                        return (str(module) + "\n").encode()
                    if command[2] == field:
                        return (value + "\n").encode()
                    if command[2] == "srcversion":
                        return (capture.EXPECTED_SRCVERSION + "\n").encode()
                    if command[2] == "vermagic":
                        return (capture.EXPECTED_KERNEL + " SMP\n").encode()
                    raise AssertionError(f"unexpected modinfo field: {command[2]}")

                with (
                    mock.patch.object(capture.shutil, "which", return_value="/usr/sbin/modinfo"),
                    mock.patch.object(capture, "run_checked", side_effect=fake_modinfo),
                ):
                    with self.assertRaisesRegex(RuntimeError, expected_error):
                        capture.installed_module_identity()

    def test_installed_module_identity_rejects_missing_selected_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing-nouveau.ko.zst"

            def fake_modinfo(command: list[str], *, timeout: int) -> bytes:
                if command[2] == "filename":
                    return (str(missing) + "\n").encode()
                self.fail("modinfo metadata should not be queried for a missing module")

            with (
                mock.patch.object(capture.shutil, "which", return_value="/usr/sbin/modinfo"),
                mock.patch.object(capture, "run_checked", side_effect=fake_modinfo),
            ):
                with self.assertRaisesRegex(RuntimeError, "selected Nouveau module file is unavailable"):
                    capture.installed_module_identity()

    def test_bsp_eexist_marker_is_early_stop_trigger(self) -> None:
        line = (
            "NOUVEAU_DIAG_NVIF_NEW key=0x500 obj=0x500 object_token=0x500 "
            "route_token=0x3 parent=0x23 parent_handle=0x3 fd=9 "
            "class=0x000095b1 ret=-17"
        )
        self.assertTrue(correlator.is_bsp_new_eexist(line))
        self.assertFalse(correlator.is_bsp_new_eexist(line.replace("ret=-17", "ret=0")))
        with self.assertRaisesRegex(ValueError, "lacks class or return"):
            correlator.is_bsp_new_eexist("NOUVEAU_DIAG_NVIF_NEW key=0x500")

    def test_cli_refuses_to_launch_without_explicit_execute_flag(self) -> None:
        output = io.StringIO()
        with (
            mock.patch.object(capture, "observed_runtime") as observe,
            contextlib.redirect_stderr(output),
        ):
            status = capture.main([
                "--variant", "A",
                "--dso", "/tmp/unused.so",
                "--output-dir", "/tmp/unused-capture",
            ])
        self.assertEqual(status, 2)
        self.assertIn("--execute is required", output.getvalue())
        observe.assert_not_called()

    def test_variant_b_cli_requires_a_recomputed_a_proof_before_preflight(self) -> None:
        output = io.StringIO()
        with (
            mock.patch.object(capture, "observed_runtime") as observe,
            contextlib.redirect_stderr(output),
        ):
            status = capture.main([
                "--variant", "B",
                "--dso", "/tmp/unused.so",
                "--output-dir", "/tmp/unused-capture",
                "--execute",
            ])
        self.assertEqual(status, 2)
        self.assertIn("requires --a-proof, --a-manifest, and --a-journal", output.getvalue())
        observe.assert_not_called()

    def test_variant_b_direct_capture_requires_proof_before_creating_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary) / "capture-B"
            with self.assertRaisesRegex(RuntimeError, "requires a verified exact"):
                capture.execute_capture(
                    variant="B",
                    dso=Path("/tmp/not-used.so"),
                    output_dir=output_dir,
                    runtime={"variant": "B"},
                )
            self.assertFalse(output_dir.exists())

    def test_variant_b_proof_must_match_same_kernel_module_and_workload(self) -> None:
        fields = (
            "input_sha256", "command_argv", "normalized_environment", "kernel",
            "runtime_profile_sha256", "nouveau_srcversion", "nouveau_module_path",
            "nouveau_module_file_sha256", "nouveau_module_srcversion",
            "nouveau_module_vermagic", "nouveau_parameters", "mpv_sha256",
            "mpv_resolved_path", "systemd_cat_sha256", "systemd_cat_resolved_path",
            "working_directory", "deployment_manifest_path",
            "deployment_manifest_sha256", "nvif_capture_sha256",
            "nvif_parser_sha256",
        )
        a_identity = {
            field: f"pinned-{field}" for field in fields
        }
        a_identity.update({
            "boot_id": "boot-a",
            "dso_sha256": correlator.EXPECTED_DSO_SHA256["A"],
        })
        runtime = {field: a_identity[field] for field in fields}
        runtime.update({
            "boot_id": "boot-b",
            "dso_sha256": correlator.EXPECTED_DSO_SHA256["B"],
        })
        capture.validate_a_proof_matches_b_runtime(
            {"identity": a_identity}, runtime
        )
        runtime["nouveau_module_file_sha256"] = "different-module"
        with self.assertRaisesRegex(RuntimeError, "nouveau_module_file_sha256"):
            capture.validate_a_proof_matches_b_runtime(
                {"identity": a_identity}, runtime
            )

    def test_variant_b_proof_must_match_deployment_and_diagnostic_tool_hashes(self) -> None:
        fields = (
            "input_sha256", "command_argv", "normalized_environment", "kernel",
            "runtime_profile_sha256", "nouveau_srcversion", "nouveau_module_path",
            "nouveau_module_file_sha256", "nouveau_module_srcversion",
            "nouveau_module_vermagic", "nouveau_parameters", "mpv_sha256",
            "mpv_resolved_path", "systemd_cat_sha256", "systemd_cat_resolved_path",
            "working_directory", "deployment_manifest_path",
            "deployment_manifest_sha256", "nvif_capture_sha256",
            "nvif_parser_sha256",
        )
        a_identity = {field: f"pinned-{field}" for field in fields}
        a_identity.update({
            "boot_id": "boot-a",
            "dso_sha256": correlator.EXPECTED_DSO_SHA256["A"],
        })
        runtime = {field: a_identity[field] for field in fields}
        runtime.update({
            "boot_id": "boot-b",
            "dso_sha256": correlator.EXPECTED_DSO_SHA256["B"],
        })

        for field in (
            "deployment_manifest_sha256",
            "nvif_capture_sha256",
            "nvif_parser_sha256",
        ):
            with self.subTest(field=field):
                runtime[field] = f"different-{field}"
                with self.assertRaisesRegex(RuntimeError, field):
                    capture.validate_a_proof_matches_b_runtime(
                        {"identity": a_identity}, runtime
                    )
                runtime[field] = a_identity[field]

    def test_capture_cli_exit_status_fails_closed_for_unexpected_outcomes(self) -> None:
        cases = (
            ("A clean run without the expected failure is inconclusive", "A", {
                "outcome": "CAPTURED_NO_KERNEL_HARD_STOP",
                "journal_boundary_proven": True,
                "workload_timed_out": False,
                "postrun_hard_stops": [],
            }, 3),
            ("A expected EEXIST stop is captured", "A", {
                "outcome": "STOPPED_AT_BSP_EEXIST",
                "journal_boundary_proven": True,
                "workload_timed_out": False,
                "postrun_hard_stops": [],
            }, 0),
            ("B clean run is available for pair correlation", "B", {
                "outcome": "CAPTURED_NO_KERNEL_HARD_STOP",
                "journal_boundary_proven": True,
                "workload_timed_out": False,
                "postrun_hard_stops": [],
            }, 0),
            ("unexplained workload failure is nonzero", "B", {
                "outcome": "WORKLOAD_NONZERO_EXIT",
                "journal_boundary_proven": True,
                "workload_timed_out": False,
                "postrun_hard_stops": [],
            }, 4),
            ("kernel failure takes precedence", "B", {
                "outcome": "KERNEL_FAILURE",
                "journal_boundary_proven": True,
                "workload_timed_out": False,
                "postrun_hard_stops": [{"source": "kernel", "kinds": ["PTE"]}],
            }, 4),
            ("tagged workload fatal is nonzero", "B", {
                "outcome": "WORKLOAD_FAILURE",
                "journal_boundary_proven": True,
                "workload_timed_out": False,
                "postrun_hard_stops": [{"source": "workload", "kinds": ["SIGBUS"]}],
            }, 4),
            ("timeout is inconclusive", "A", {
                "outcome": "WORKLOAD_TIMEOUT",
                "journal_boundary_proven": True,
                "workload_timed_out": True,
                "postrun_hard_stops": [],
            }, 3),
        )
        for name, variant, result, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(
                    capture.capture_exit_status(result, variant=variant),
                    expected,
                )

    def test_capture_cli_exit_status_rejects_unknown_outcome(self) -> None:
        self.assertEqual(
            capture.capture_exit_status(
                {
                    "outcome": "UNRECOGNIZED",
                    "journal_boundary_proven": True,
                    "workload_timed_out": False,
                    "postrun_hard_stops": [],
                },
                variant="A",
            ),
            4,
        )

    def test_wrong_kernel_is_rejected_before_sysfs_or_journal_probe(self) -> None:
        dso = Path(
            "/home/keivan/nouveau-vaapi-app-validation/"
            "mesa-nvif-lifetime-ab-build-20261003T082047Z/A/libgallium_drv_video.so"
        )
        if not dso.is_file():
            self.skipTest("saved linked A DSO is unavailable in this checkout")
        def fake_hash(path: Path) -> str:
            if path.resolve() == dso.resolve():
                return correlator.EXPECTED_DSO_SHA256["A"]
            return correlator.EXPECTED_INPUT_SHA256

        with (
            mock.patch.object(capture, "sha256_file", side_effect=fake_hash),
            mock.patch.object(capture.platform, "release", return_value="wrong-kernel"),
            mock.patch.object(capture, "run_checked") as run_checked,
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpected kernel"):
                capture.observed_runtime(
                    variant="A",
                    dso=dso,
                    deployment_manifest=Path("/nonexistent/deployment-manifest.json"),
                )
        run_checked.assert_not_called()

    def test_existing_output_directory_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "existing"
            output.mkdir()
            with self.assertRaisesRegex(RuntimeError, "refusing to overwrite"):
                capture.execute_capture(
                    variant="A",
                    dso=Path("/tmp/unused.so"),
                    output_dir=output,
                    runtime={"variant": "A"},
                )

    def test_process_group_cleanup_reaps_child_after_leader_exits(self) -> None:
        child_code = (
            "import signal,time; "
            "signal.signal(signal.SIGINT, signal.SIG_IGN); "
            "time.sleep(30)"
        )
        parent_code = (
            "import subprocess,sys; "
            f"subprocess.Popen([{sys.executable!r}, '-c', {child_code!r}], "
            "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
            "stderr=subprocess.DEVNULL)"
        )
        leader = subprocess.Popen(
            [sys.executable, "-c", parent_code],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            text=True,
        )
        pgid = leader.pid
        try:
            leader.wait(timeout=3)
            deadline = time.monotonic() + 3
            members = capture.process_group_members(pgid)
            while not members and time.monotonic() < deadline:
                time.sleep(0.02)
                members = capture.process_group_members(pgid)
            self.assertTrue(members, "synthetic child did not remain in leader's process group")

            capture._terminate_process_group(
                leader,
                sigint_grace=0.2,
                sigterm_grace=0.5,
                sigkill_grace=0.5,
            )
            self.assertEqual(capture.process_group_members(pgid), [])
        finally:
            if capture.process_group_members(pgid):
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                leader.wait(timeout=3)
            except subprocess.TimeoutExpired:
                leader.kill()
                leader.wait(timeout=3)


if __name__ == "__main__":
    unittest.main()
