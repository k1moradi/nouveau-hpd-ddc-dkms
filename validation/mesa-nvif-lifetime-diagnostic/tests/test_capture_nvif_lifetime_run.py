from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import capture_nvif_lifetime_run as capture
import parse_nvif_lifetime_ab as correlator


class NvifLifetimeCaptureTests(unittest.TestCase):
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
                "fifo fault engine 05 [BAR2] client 07 [HOST_CPU] reason 02 [PTE]"
            ),
            ("BAR2", "HOST_CPU", "PTE"),
        )
        self.assertIn(
            "CTXSW_TIMEOUT",
            capture.hard_stop_kinds("fifo: SCHED_ERROR 0a [CTXSW_TIMEOUT]"),
        )
        self.assertIn(
            "failed-to-idle",
            capture.hard_stop_kinds("Xorg failed to idle channel 9"),
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
            b'"_BOOT_ID":"boot-a"}\n'
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
                capture.observed_runtime(variant="A", dso=dso)
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


if __name__ == "__main__":
    unittest.main()
