#!/usr/bin/env python3
"""CPU-only checks for detached mpv monitoring and final classification."""

from __future__ import annotations

import hashlib
import json
import io
import os
import signal
import copy
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import detached_mpv_supervisor as supervisor
from detached_mpv_supervisor import (
    ARM_TOKEN_ENV,
    FINAL_JOURNAL_QUERY_TIMEOUT_SECONDS,
    LIBVA_DRIVER_ALIAS,
    POST_STOP_OBSERVATION_SECONDS,
    ProcessGroupStopper,
    PROCESS_GROUP_STOP_MAX_SECONDS,
    SYSTEMCTL_STOP_COMMAND_TIMEOUT_SECONDS,
    SYSTEMD_STOP_MARGIN_SECONDS,
    SYSTEMD_STOP_TIMEOUT_SECONDS,
    classify_mpv_hardware_decode,
    child_environment,
    consume_arm_token,
    create_arm_token,
    detached_unit_properties,
    final_run_status,
    mpv_screenshot_request,
    mpv_run_exit_code,
    result_status_fields,
    mapped_driver_status,
    passive_post_stop_snapshot,
    prove_kernel_journal_visibility,
    first_stop_signature,
    _read_module_parameter,
    process_group_members,
    stop_reason_for_line,
    sync_journal,
    validate_deployment_manifest,
    verify_deployment_manifest,
    visible_x11_session_errors,
)


class FinalJournalClassificationTests(unittest.TestCase):
    def test_clean_natural_exit_remains_clean(self) -> None:
        result = final_run_status({}, "ordinary kernel line\n")
        self.assertEqual(result, ("natural-exit", None, None, False))

    def test_late_bar2_fault_overrides_natural_exit(self) -> None:
        line = (
            "nouveau: fifo: fault 00 [READ] at 0x433000 engine 05 [BAR2] "
            "client 07 [HUB/HOST_CPU] reason 02 [PTE]"
        )
        result = final_run_status({}, line + "\n")
        self.assertEqual(
            result,
            ("kernel-signature-in-final-journal-delta", line, ("BAR2/HOST_CPU/PTE", line), True),
        )

    def test_late_hard_stop_overrides_natural_exit(self) -> None:
        line = "nouveau: CTXSW_TIMEOUT on channel 2"
        reason, trigger, signature, failed = final_run_status({}, line + "\n")
        self.assertEqual(reason, "kernel-signature-in-final-journal-delta")
        self.assertEqual(trigger, line)
        self.assertEqual(signature, ("CTXSW_TIMEOUT", line))
        self.assertTrue(failed)


class KernelJournalVisibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="nvmpv-boot-id-")
        self.boot_id_path = Path(self.temporary.name) / "boot_id"
        self.boot_id = "03f7b214-ea03-4be8-af92-f6a073942e9d"
        self.boot_id_path.write_text(self.boot_id + "\n", encoding="ascii")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def query(self, stdout: str, returncode: int = 0, stderr: str = ""):
        return subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=stdout, stderr=stderr
        )

    def test_current_boot_kernel_record_proves_visibility(self) -> None:
        record = {
            "_TRANSPORT": "kernel",
            "_BOOT_ID": self.boot_id.replace("-", ""),
            "MESSAGE": "Linux version ...",
        }
        with (
            patch.object(supervisor, "BOOT_ID_PATH", self.boot_id_path),
            patch.object(
                supervisor,
                "journal",
                return_value=self.query(json.dumps(record) + "\n"),
            ) as query,
        ):
            self.assertEqual(
                prove_kernel_journal_visibility(),
                (True, "current-boot kernel journal visibility proven"),
            )
        query.assert_called_once_with(
            ["-k", "-b", "-n", "1", "--no-pager", "-o", "json"],
            timeout=5,
        )

    def test_empty_journal_is_not_a_clean_baseline(self) -> None:
        with (
            patch.object(supervisor, "BOOT_ID_PATH", self.boot_id_path),
            patch.object(supervisor, "journal", return_value=self.query("")),
        ):
            visible, detail = prove_kernel_journal_visibility()
        self.assertFalse(visible)
        self.assertIn("got 0", detail)

    def test_inaccessible_journal_is_not_a_clean_baseline(self) -> None:
        with (
            patch.object(supervisor, "BOOT_ID_PATH", self.boot_id_path),
            patch.object(
                supervisor,
                "journal",
                return_value=self.query("", returncode=1, stderr="permission denied"),
            ),
        ):
            visible, detail = prove_kernel_journal_visibility()
        self.assertFalse(visible)
        self.assertEqual(detail, "permission denied")

    def test_wrong_boot_and_non_kernel_transport_are_rejected(self) -> None:
        cases = (
            ({"_TRANSPORT": "kernel", "_BOOT_ID": "a" * 32}, "boot mismatch"),
            (
                {
                    "_TRANSPORT": "syslog",
                    "_BOOT_ID": self.boot_id.replace("-", ""),
                },
                "not kernel transport",
            ),
        )
        for record, expected in cases:
            with self.subTest(expected=expected):
                with (
                    patch.object(supervisor, "BOOT_ID_PATH", self.boot_id_path),
                    patch.object(
                        supervisor,
                        "journal",
                        return_value=self.query(json.dumps(record) + "\n"),
                    ),
                ):
                    visible, detail = prove_kernel_journal_visibility()
                self.assertFalse(visible)
                self.assertIn(expected, detail)

    def test_invalid_json_and_boot_id_read_fail_closed(self) -> None:
        with (
            patch.object(supervisor, "BOOT_ID_PATH", self.boot_id_path),
            patch.object(supervisor, "journal", return_value=self.query("not-json\n")),
        ):
            visible, detail = prove_kernel_journal_visibility()
        self.assertFalse(visible)
        self.assertIn("invalid JSON", detail)

        with patch.object(supervisor, "BOOT_ID_PATH", Path("/missing/boot-id")):
            visible, detail = prove_kernel_journal_visibility()
        self.assertFalse(visible)
        self.assertIn("cannot read current boot ID", detail)

    def test_preflight_rejects_unproven_kernel_journal_visibility(self) -> None:
        completed = self.query("")
        original_read_text = Path.read_text

        def fake_read_text(path: Path, *args, **kwargs) -> str:
            if str(path) == "/sys/module/nouveau/srcversion":
                return "test-srcversion\n"
            return original_read_text(path, *args, **kwargs)

        with (
            patch.object(supervisor, "prove_kernel_journal_visibility", return_value=(False, "empty")),
            patch.object(supervisor, "current_players", return_value=[]),
            patch.object(supervisor, "journal", return_value=completed),
            patch.object(supervisor, "sha256", return_value=supervisor.EXPECTED_INPUT_SHA256),
            patch.object(Path, "is_file", return_value=True),
            patch.object(Path, "read_text", new=fake_read_text),
        ):
            errors, _srcversion, _snapshot, _observed = supervisor.preflight(
                require_desktop=False
            )
        self.assertTrue(
            any("cannot prove current-boot kernel journal visibility: empty" in item
                for item in errors)
        )


class PreflightJournalSnapshotFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_read_text = Path.read_text

    def preflight_patches(self, journal_failure):
        original_read_text = self.original_read_text

        def fake_read_text(path: Path, *args, **kwargs) -> str:
            if str(path) == "/sys/module/nouveau/srcversion":
                return "test-srcversion\n"
            return original_read_text(path, *args, **kwargs)

        stack = ExitStack()
        for patcher in (
            patch.object(
                supervisor,
                "prove_kernel_journal_visibility",
                return_value=(True, "current-boot journal visible"),
            ),
            patch.object(supervisor, "current_players", return_value=[]),
            patch.object(supervisor, "journal", side_effect=journal_failure),
            patch.object(
                supervisor,
                "sha256",
                return_value=supervisor.EXPECTED_INPUT_SHA256,
            ),
            patch.object(Path, "is_file", return_value=True),
            patch.object(Path, "read_text", new=fake_read_text),
        ):
            stack.enter_context(patcher)
        return stack

    def test_snapshot_oserror_and_timeout_preserve_four_value_contract(self) -> None:
        failures = (
            OSError("journal unavailable"),
            subprocess.TimeoutExpired(cmd=["journalctl"], timeout=12),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with self.preflight_patches(failure):
                    result = supervisor.preflight(require_desktop=False)

                self.assertEqual(len(result), 4)
                errors, srcversion, snapshot, observed = result
                self.assertEqual(srcversion, "test-srcversion")
                self.assertEqual(snapshot, "")
                self.assertEqual(observed, {})
                self.assertTrue(
                    any("current-boot kernel journal query failed" in error
                        for error in errors)
                )

    def test_check_only_snapshot_timeout_is_structured_failure(self) -> None:
        failure = subprocess.TimeoutExpired(cmd=["journalctl"], timeout=12)
        with self.preflight_patches(failure):
            with (
                patch("sys.stdout", new_callable=io.StringIO) as output,
                patch("sys.stderr", new_callable=io.StringIO) as error_output,
            ):
                status = supervisor.main(["check-only", "--no-desktop"])

        self.assertEqual(status, 2)
        self.assertIn("PREFLIGHT_FAIL", output.getvalue())
        self.assertIn("RUN_ELIGIBLE=false", output.getvalue())
        self.assertIn("current-boot kernel journal query failed", output.getvalue())
        self.assertNotIn("Traceback", error_output.getvalue())


class CheckOnlyEligibilityOutputTests(unittest.TestCase):
    def run_main(self, argv: list[str], deployment=None):
        observed = {"deployment": "verified"} if deployment is not None else {}
        with (
            patch.object(
                supervisor,
                "preflight",
                return_value=([], "src", "", observed),
            ),
            patch.object(
                supervisor,
                "load_deployment_manifest",
                return_value=deployment,
            ),
            patch("sys.stdout", new_callable=io.StringIO) as output,
        ):
            status = supervisor.main(argv)
            return status, output.getvalue()

    def test_unpinned_check_only_is_baseline_only(self) -> None:
        status, output = self.run_main(["check-only", "--no-desktop"])
        self.assertEqual(status, 0)
        self.assertIn("BASELINE_CHECK_PASS", output)
        self.assertIn("DEPLOYMENT_VERIFIED=false", output)
        self.assertIn("RUN_ELIGIBLE=false", output)
        self.assertNotIn("PREFLIGHT_PASS", output)

    def test_manifest_and_desktop_check_are_required_for_eligibility(self) -> None:
        status, output = self.run_main(
            ["check-only", "--deployment-manifest", "/tmp/reviewed.json"],
            deployment={"schema": 1},
        )
        self.assertEqual(status, 0)
        self.assertIn("PREFLIGHT_PASS", output)
        self.assertIn("DEPLOYMENT_VERIFIED=true", output)
        self.assertIn("RUN_ELIGIBLE=true", output)

        status, output = self.run_main(
            ["check-only", "--no-desktop", "--deployment-manifest", "/tmp/reviewed.json"],
            deployment={"schema": 1},
        )
        self.assertEqual(status, 0)
        self.assertIn("DEPLOYMENT_CHECK_PASS", output)
        self.assertIn("RUN_ELIGIBLE=false", output)

    def test_journal_sync_failure_is_not_ignored(self) -> None:
        failure = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="journal unavailable"
        )
        with patch.object(supervisor, "journal", return_value=failure):
            with self.assertRaisesRegex(RuntimeError, "journal sync failed"):
                sync_journal()

    def test_unavailable_final_journal_fails_closed(self) -> None:
        reason, trigger, signature, failed = final_run_status({}, "", "journalctl failed")
        self.assertEqual(reason, "post-run-journal-unavailable")
        self.assertIsNone(trigger)
        self.assertIsNone(signature)
        self.assertTrue(failed)


class HardStopSignatureTests(unittest.TestCase):
    def test_scheduler_mmu_and_kernel_fatal_signatures_stop(self) -> None:
        cases = {
            "SCHED_ERROR_0A": "nouveau: SCHED_ERROR 0a",
            "GPU-MMU-fault": "nouveau 0000:01:00.0: fifo: fault 00 [READ] at 0x1000 engine 00 [GR]",
            "kernel-BUG": "kernel: BUG: unable to handle page fault",
            "kernel-Oops": "kernel: Oops: 0000 [#1] PREEMPT SMP",
            "kernel-WARNING": "kernel: WARNING: CPU: 3 PID: 19 at example.c:7",
            "KASAN": "kernel: KASAN: use-after-free in example",
            "KCSAN": "kernel: KCSAN: data-race in example",
            "KFENCE": "kernel: KFENCE: use-after-free in example",
            "UBSAN": "kernel: UBSAN: array-index-out-of-bounds",
            "lockdep": "kernel: possible circular locking dependency detected",
        }
        for expected, line in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(stop_reason_for_line(line), expected)

    def test_first_signature_wins_and_unrelated_acpi_warning_is_ignored(self) -> None:
        lines = [
            "nouveau: SCHED_ERROR 0a",
            "nouveau: fifo: fault at 0x1000",
        ]
        self.assertEqual(first_stop_signature(lines), ("SCHED_ERROR_0A", lines[0]))
        self.assertIsNone(stop_reason_for_line("ACPI Warning: unrelated firmware message"))


class ProcessGroupStopperTests(unittest.TestCase):
    def test_concurrent_stop_requests_reap_the_process_group_once(self) -> None:
        child_code = (
            "import signal,time; signal.signal(signal.SIGINT, signal.SIG_IGN); time.sleep(30)"
        )
        parent_code = (
            "import signal,subprocess,sys,time; "
            f"subprocess.Popen([{sys.executable!r}, '-c', {child_code!r}]); "
            "signal.signal(signal.SIGINT, lambda *_: sys.exit(0)); time.sleep(30)"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", parent_code],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        stopper = ProcessGroupStopper(proc, sigint_grace=0.3, sigterm_grace=0.5)
        pgid = proc.pid
        failures: list[BaseException] = []
        barrier = threading.Barrier(3)

        def request_stop() -> None:
            try:
                barrier.wait(timeout=2)
                stopper.stop()
            except BaseException as exc:
                failures.append(exc)

        threads = [threading.Thread(target=request_stop) for _ in range(2)]
        try:
            deadline = time.monotonic() + 3
            while len(process_group_members(pgid)) < 2 and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertGreaterEqual(len(process_group_members(pgid)), 2)
            for thread in threads:
                thread.start()
            barrier.wait(timeout=2)
            for thread in threads:
                thread.join(timeout=5)
            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(failures, [])
            self.assertIsNotNone(proc.poll())
            self.assertEqual(process_group_members(pgid), [])
        finally:
            stopper.stop()

    def test_existing_stop_reason_is_preserved_but_late_fault_fails_run(self) -> None:
        line = "nouveau: fifo: fault 00 [READ] at 0x433000 engine 05 [BAR2] client 07 [HUB/HOST_CPU] reason 02 [PTE]"
        reason, trigger, signature, failed = final_run_status(
            {"reason": "external-signal-SIGINT"}, line + "\n"
        )
        self.assertEqual(reason, "external-signal-SIGINT")
        self.assertIsNone(trigger)
        self.assertEqual(signature, ("BAR2/HOST_CPU/PTE", line))
        self.assertTrue(failed)

    def test_sigkill_survivor_is_reported_as_stop_failure(self) -> None:
        proc = MagicMock()
        proc.pid = 99999999
        proc.poll.return_value = 0
        stopper = ProcessGroupStopper(proc)
        with (
            patch.object(supervisor, "process_group_members", return_value=[(99, "D")]),
            patch.object(supervisor, "signal_process_group"),
            patch.object(supervisor, "_wait_group_gone", return_value=False),
        ):
            self.assertFalse(stopper.stop())
        self.assertIn("still has live members after SIGKILL", stopper.error or "")


class ArmTokenTests(unittest.TestCase):
    def test_direct_run_without_token_refuses_before_journal_cursor(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nvmpv-no-arm-") as temporary:
            with patch.dict(os.environ, {}, clear=True):
                with patch.object(supervisor, "cursor_now") as cursor:
                    self.assertEqual(supervisor.service_run(Path(temporary)), 2)
                    cursor.assert_not_called()

    def test_wrong_token_refuses_before_journal_cursor(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nvmpv-bad-arm-") as temporary:
            run_dir = Path(temporary)
            token = create_arm_token(run_dir)
            self.assertNotEqual(token, "wrong-token")
            with patch.dict(os.environ, {ARM_TOKEN_ENV: "wrong-token"}, clear=True):
                with patch.object(supervisor, "cursor_now") as cursor:
                    self.assertEqual(supervisor.service_run(run_dir), 2)
                    cursor.assert_not_called()
            self.assertTrue((run_dir / "arm-token").exists())

    def test_valid_token_is_consumed_once_and_replay_is_refused(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nvmpv-good-arm-") as temporary:
            run_dir = Path(temporary)
            token = create_arm_token(run_dir)
            with patch.dict(os.environ, {ARM_TOKEN_ENV: token}, clear=True):
                consume_arm_token(run_dir)
                self.assertNotIn(ARM_TOKEN_ENV, os.environ)
            self.assertFalse((run_dir / "arm-token").exists())
            with patch.dict(os.environ, {ARM_TOKEN_ENV: token}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "missing one-use"):
                    consume_arm_token(run_dir)


class DeploymentManifestTests(unittest.TestCase):
    @staticmethod
    def valid_manifest() -> dict[str, object]:
        digest = "a" * 64
        return {
            "schema": 1,
            "kernel": "7.0.0-34-generic",
            "nouveau": {
                "srcversion": "reviewed-srcversion",
                "module_path": "/lib/modules/7.0.0-34-generic/updates/dkms/nouveau.ko.zst",
                "module_sha256": digest,
                "vermagic": "7.0.0-34-generic SMP preempt mod_unload modversions",
                "parameters": {"diag_ctxsw": "N"},
            },
            "initramfs": {
                "path": "/boot/initrd.img-7.0.0-34-generic",
                "sha256": digest,
                "module_sha256": digest,
            },
            "mesa": {"dso_path": "/opt/review/libgallium_drv_video.so", "dso_sha256": digest},
            "mpv": {"path": "/usr/bin/mpv", "sha256": digest},
            "input": {"path": "/home/user/test.mkv", "sha256": digest},
        }

    def test_strict_deployment_manifest_is_accepted(self) -> None:
        manifest = validate_deployment_manifest(self.valid_manifest())
        self.assertEqual(manifest["schema"], 1)

    def test_manifest_rejects_missing_or_unexpected_fields(self) -> None:
        manifest = self.valid_manifest()
        del manifest["initramfs"]
        with self.assertRaisesRegex(ValueError, "keys must be exactly"):
            validate_deployment_manifest(manifest)
        manifest = self.valid_manifest()
        manifest["unexpected"] = True
        with self.assertRaisesRegex(ValueError, "keys must be exactly"):
            validate_deployment_manifest(manifest)

    def test_manifest_rejects_relative_path_and_bad_digest(self) -> None:
        manifest = self.valid_manifest()
        manifest["nouveau"]["module_path"] = "relative/nouveau.ko"
        with self.assertRaisesRegex(ValueError, "absolute path"):
            validate_deployment_manifest(manifest)
        manifest = self.valid_manifest()
        manifest["mesa"]["dso_sha256"] = "bad"
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            validate_deployment_manifest(manifest)

    def test_manifest_requires_exact_disabled_ctxsw_parameter(self) -> None:
        manifest = self.valid_manifest()
        manifest["nouveau"]["parameters"] = {}
        with self.assertRaisesRegex(ValueError, "must be a nonempty string map"):
            validate_deployment_manifest(manifest)

        manifest = self.valid_manifest()
        manifest["nouveau"]["parameters"] = {"diag_bar2_map": "N"}
        with self.assertRaisesRegex(ValueError, "exactly pin"):
            validate_deployment_manifest(manifest)

        manifest = self.valid_manifest()
        manifest["nouveau"]["parameters"] = {"diag_ctxsw": "Y"}
        with self.assertRaisesRegex(ValueError, "exactly pin"):
            validate_deployment_manifest(manifest)

    def test_runtime_verifier_checks_module_initrd_driver_and_workload_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nvmpv-deployment-") as temporary:
            root = Path(temporary)
            module = root / "nouveau.ko.zst"
            initrd_module = root / "embedded" / "nouveau.ko.zst"
            initrd_module.parent.mkdir()
            initramfs = root / "initrd.img"
            mesa = root / "libgallium_drv_video.so"
            alias = root / "nouveau_drv_video.so"
            mpv = root / "mpv"
            media = root / "test.mkv"
            for path, payload in (
                (module, b"module bytes"),
                (initrd_module, b"module bytes"),
                (initramfs, b"initramfs bytes"),
                (mesa, b"mesa bytes"),
                (mpv, b"mpv bytes"),
                (media, b"media bytes"),
            ):
                path.write_bytes(payload)
            alias.symlink_to(mesa)
            digest = supervisor.sha256
            srcversion = "reviewed-srcversion"
            loaded_srcversion = [srcversion]
            vermagic = "7.0.0-34-generic SMP preempt mod_unload modversions"
            manifest = {
                "schema": 1,
                "kernel": "7.0.0-34-generic",
                "nouveau": {
                    "srcversion": srcversion,
                    "module_path": str(module),
                    "module_sha256": digest(module),
                    "vermagic": vermagic,
                    "parameters": {"diag_ctxsw": "N"},
                },
                "initramfs": {
                    "path": str(initramfs),
                    "sha256": digest(initramfs),
                    "module_sha256": digest(initrd_module),
                },
                "mesa": {"dso_path": str(mesa), "dso_sha256": digest(mesa)},
                "mpv": {"path": str(mpv), "sha256": digest(mpv)},
                "input": {"path": str(media), "sha256": digest(media)},
            }

            original_read_text = Path.read_text

            def fake_read_text(path: Path, *args, **kwargs) -> str:
                if str(path) == "/sys/module/nouveau/srcversion":
                    return loaded_srcversion[0] + "\n"
                if str(path) == "/sys/module/nouveau/parameters/diag_ctxsw":
                    return "N\n"
                return original_read_text(path, *args, **kwargs)

            def modinfo(field: str, module_path: Path | None = None) -> str:
                if field == "filename":
                    return str(module)
                if field == "srcversion":
                    return srcversion
                if field == "vermagic":
                    return vermagic
                raise AssertionError(field)

            with (
                patch.object(supervisor, "PLUGIN", mesa),
                patch.object(supervisor, "LIBVA_DRIVER_ALIAS", alias),
                patch.object(supervisor, "MPV", mpv),
                patch.object(supervisor, "INPUT", media),
                patch.object(supervisor, "_modinfo", side_effect=modinfo),
                patch.object(
                    supervisor,
                    "_initramfs_module_hashes",
                    return_value=[(str(initrd_module), digest(initrd_module))],
                ),
                patch.object(supervisor.os, "uname", return_value=type("Uname", (), {"release": "7.0.0-34-generic"})()),
                patch.object(Path, "read_text", new=fake_read_text),
            ):
                errors, observed = verify_deployment_manifest(
                    validate_deployment_manifest(manifest)
                )
                self.assertEqual(errors, [])
                self.assertEqual(observed["nouveau_loaded_srcversion"], srcversion)
                self.assertEqual(observed["initramfs_sha256"], digest(initramfs))

                bad_hash = copy.deepcopy(manifest)
                bad_hash["nouveau"]["module_sha256"] = "f" * 64
                errors, _ = verify_deployment_manifest(
                    validate_deployment_manifest(bad_hash)
                )
                self.assertTrue(any("nouveau_module_sha256 mismatch" in item for item in errors))

                bad_vermagic = copy.deepcopy(manifest)
                bad_vermagic["nouveau"]["vermagic"] = "wrong-kernel"
                errors, _ = verify_deployment_manifest(
                    validate_deployment_manifest(bad_vermagic)
                )
                self.assertTrue(any("nouveau_vermagic mismatch" in item for item in errors))

                loaded_srcversion[0] = "different-loaded-build"
                errors, _ = verify_deployment_manifest(
                    validate_deployment_manifest(manifest)
                )
                self.assertTrue(
                    any("nouveau_loaded_srcversion mismatch" in item for item in errors)
                )


class ManifestSnapshotWriteTests(unittest.TestCase):
    payload = b'{"schema":1,"review":"pinned manifest"}\n'

    def test_manifest_snapshot_completes_a_single_full_write(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="nvmpv-manifest-write-"
        ) as temporary:
            target = Path(temporary) / "deployment-manifest.json"
            real_write = os.write
            calls: list[bytes] = []

            def full_write(fd: int, view: memoryview) -> int:
                calls.append(bytes(view))
                return real_write(fd, view)

            with patch.object(supervisor.os, "write", side_effect=full_write):
                supervisor.write_manifest_snapshot(target, self.payload)

            self.assertEqual(calls, [self.payload])
            self.assertEqual(target.read_bytes(), self.payload)

    def test_manifest_snapshot_retries_multiple_short_writes(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="nvmpv-manifest-short-write-"
        ) as temporary:
            target = Path(temporary) / "deployment-manifest.json"
            real_write = os.write
            calls: list[bytes] = []

            def short_write(fd: int, view: memoryview) -> int:
                chunk = bytes(view[:7])
                calls.append(chunk)
                return real_write(fd, chunk)

            with patch.object(supervisor.os, "write", side_effect=short_write):
                supervisor.write_manifest_snapshot(target, self.payload)

            self.assertGreater(len(calls), 1)
            self.assertEqual(b"".join(calls), self.payload)
            self.assertEqual(target.read_bytes(), self.payload)

    def test_manifest_snapshot_rejects_zero_write_and_removes_partial_file(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="nvmpv-manifest-zero-write-"
        ) as temporary:
            target = Path(temporary) / "deployment-manifest.json"
            with patch.object(supervisor.os, "write", return_value=0):
                with self.assertRaisesRegex(OSError, "short or invalid write"):
                    supervisor.write_manifest_snapshot(target, self.payload)
            self.assertFalse(target.exists())

    def test_manifest_snapshot_rejects_negative_write_and_removes_partial_file(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="nvmpv-manifest-negative-write-"
        ) as temporary:
            target = Path(temporary) / "deployment-manifest.json"
            with patch.object(supervisor.os, "write", return_value=-1):
                with self.assertRaisesRegex(OSError, "short or invalid write"):
                    supervisor.write_manifest_snapshot(target, self.payload)
            self.assertFalse(target.exists())


class ProtectedModuleParameterTests(unittest.TestCase):
    def test_root_only_parameter_uses_noninteractive_read_only_cat(self) -> None:
        parameter = Path("/sys/module/nouveau/parameters/diag_ctxsw")
        original_read_text = Path.read_text

        def permission_denied(path: Path, *args, **kwargs) -> str:
            if path == parameter:
                raise PermissionError("root-owned sysfs parameter")
            return original_read_text(path, *args, **kwargs)

        result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="N\n", stderr=""
        )
        with (
            patch.object(Path, "read_text", new=permission_denied),
            patch.object(supervisor.subprocess, "run", return_value=result) as run,
        ):
            self.assertEqual(_read_module_parameter("diag_ctxsw"), "N")
        run.assert_called_once_with(
            ["/usr/bin/sudo", "-n", "/usr/bin/cat", str(parameter)],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )

    def test_root_only_parameter_fails_when_noninteractive_access_is_denied(self) -> None:
        parameter = Path("/sys/module/nouveau/parameters/diag_ctxsw")
        original_read_text = Path.read_text

        def permission_denied(path: Path, *args, **kwargs) -> str:
            if path == parameter:
                raise PermissionError("root-owned sysfs parameter")
            return original_read_text(path, *args, **kwargs)

        result = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="a password is required"
        )
        with (
            patch.object(Path, "read_text", new=permission_denied),
            patch.object(supervisor.subprocess, "run", return_value=result),
        ):
            with self.assertRaisesRegex(RuntimeError, "password is required"):
                _read_module_parameter("diag_ctxsw")


class DesktopSessionTests(unittest.TestCase):
    def test_visible_session_requires_active_local_x11_user_on_seat0(self) -> None:
        valid_output = (
            "Active=yes\nRemote=no\nType=x11\nClass=user\n"
            "Seat=seat0\nVTNr=2\nDisplay=:0\n"
        )
        fake_loginctl = Path("/usr/bin/loginctl")
        result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=valid_output, stderr=""
        )
        with (
            patch.object(supervisor, "LOGINCTL", fake_loginctl),
            patch.object(supervisor.subprocess, "run", return_value=result),
            patch.dict(os.environ, {"XDG_SESSION_ID": "7", "DISPLAY": ":0"}, clear=True),
        ):
            self.assertEqual(visible_x11_session_errors(), [])

        remote_result = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=valid_output.replace("Remote=no", "Remote=yes"), stderr="",
        )
        with (
            patch.object(supervisor, "LOGINCTL", fake_loginctl),
            patch.object(supervisor.subprocess, "run", return_value=remote_result),
            patch.dict(os.environ, {"XDG_SESSION_ID": "7", "DISPLAY": ":0"}, clear=True),
        ):
            self.assertTrue(any("Remote must be 'no'" in item for item in visible_x11_session_errors()))

        with (
            patch.object(supervisor, "LOGINCTL", fake_loginctl),
            patch.object(supervisor.subprocess, "run", return_value=result),
            patch.dict(os.environ, {"XDG_SESSION_ID": "7", "DISPLAY": ":99"}, clear=True),
        ):
            self.assertTrue(any("does not match active session display" in item for item in visible_x11_session_errors()))


class ChildEnvironmentTests(unittest.TestCase):
    def test_child_environment_drops_loader_and_debug_overrides(self) -> None:
        parent = {
            "DISPLAY": ":0",
            "HOME": "/home/user",
            "XDG_RUNTIME_DIR": "/run/user/1000",
            "LD_PRELOAD": "/tmp/inject.so",
            "LD_LIBRARY_PATH": "/tmp/lib",
            "LIBVA_TRACE": "/tmp/trace",
            "LIBVA_MESSAGING_LEVEL": "2",
            "MESA_DEBUG": "1",
            "MESA_LOADER_DRIVER_OVERRIDE": "llvmpipe",
            "NOUVEAU_DEBUG": "all",
            "WAYLAND_DISPLAY": "wayland-0",
            "DRI_PRIME": "1",
        }
        with patch.dict(os.environ, parent, clear=True):
            env = child_environment()
        self.assertEqual(env["DISPLAY"], ":0")
        self.assertEqual(env["LIBVA_DRIVER_NAME"], "nouveau")
        self.assertEqual(
            set(env),
            {
                "DISPLAY", "HOME", "XDG_RUNTIME_DIR", "PATH", "LC_ALL",
                "LIBVA_DRIVER_NAME", "LIBVA_DRIVERS_PATH",
            },
        )
        for name in (
            "LD_PRELOAD", "LD_LIBRARY_PATH", "LIBVA_TRACE",
            "LIBVA_MESSAGING_LEVEL", "MESA_DEBUG", "MESA_LOADER_DRIVER_OVERRIDE",
            "NOUVEAU_DEBUG", "WAYLAND_DISPLAY", "DRI_PRIME",
        ):
            self.assertNotIn(name, env)


class MappedDriverTests(unittest.TestCase):
    def test_expected_inode_and_hash_must_be_mapped_by_mpv(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nvmpv-map-test-") as temporary:
            dso = Path(temporary) / "libgallium_drv_video.so"
            dso.write_bytes(b"pinned DSO")
            info = dso.stat()
            dev = f"{os.major(info.st_dev):x}:{os.minor(info.st_dev):x}"
            maps = (
                f"7f000000-7f001000 r-xp 00000000 {dev} {info.st_ino} "
                f"{dso}\n"
            )
            original_read_text = Path.read_text

            def fake_read_text(path: Path, *args, **kwargs) -> str:
                if str(path) == "/proc/12345/maps":
                    return maps
                return original_read_text(path, *args, **kwargs)

            with patch.object(Path, "read_text", new=fake_read_text):
                matched, detail = mapped_driver_status(
                    12345, dso, supervisor.sha256(dso)
                )
                self.assertTrue(matched, detail)
                self.assertIn(f"inode={info.st_ino}", detail)
                self.assertFalse(
                    mapped_driver_status(12345, dso, "0" * 64)[0]
                )

class PassivePostStopObservationTests(unittest.TestCase):
    def test_delayed_pte_is_included_after_passive_window(self) -> None:
        lines = ["CTXSW_TIMEOUT"]
        waited: list[float] = []

        def fake_wait(seconds: float) -> bool:
            waited.append(seconds)
            lines.append(
                "nouveau: fifo: fault 00 [READ] at 0x5a9000 engine 05 [BAR2] "
                "client 07 [HUB/HOST_CPU] reason 02 [PTE]"
            )
            return False

        snapshot, complete = passive_post_stop_snapshot(
            wait_for_cancel=fake_wait,
            snapshot=lambda: "\n".join(lines),
        )

        self.assertEqual(waited, [POST_STOP_OBSERVATION_SECONDS])
        self.assertTrue(complete)
        self.assertIn("CTXSW_TIMEOUT", snapshot)
        self.assertIn("[BAR2]", snapshot)

    def test_operator_cancel_still_takes_a_final_snapshot(self) -> None:
        waited: list[float] = []

        def cancelled_wait(seconds: float) -> bool:
            waited.append(seconds)
            return True

        snapshot, complete = passive_post_stop_snapshot(
            wait_for_cancel=cancelled_wait,
            snapshot=lambda: "latest journal state",
        )

        self.assertEqual(waited, [POST_STOP_OBSERVATION_SECONDS])
        self.assertFalse(complete)
        self.assertEqual(snapshot, "latest journal state")


class DetachedUnitStopPolicyTests(unittest.TestCase):
    def test_systemd_allows_cleanup_and_final_journal_capture(self) -> None:
        properties = detached_unit_properties()
        self.assertIn("--property=KillMode=control-group", properties)
        self.assertIn("--property=KillSignal=SIGINT", properties)
        self.assertIn("--property=SendSIGKILL=yes", properties)
        self.assertIn("--property=UMask=0077", properties)
        self.assertIn(
            f"--property=TimeoutStopSec={SYSTEMD_STOP_TIMEOUT_SECONDS}s",
            properties,
        )
        self.assertGreaterEqual(
            SYSTEMD_STOP_TIMEOUT_SECONDS,
            PROCESS_GROUP_STOP_MAX_SECONDS
            + FINAL_JOURNAL_QUERY_TIMEOUT_SECONDS
            + SYSTEMD_STOP_MARGIN_SECONDS,
        )
        self.assertGreater(
            SYSTEMCTL_STOP_COMMAND_TIMEOUT_SECONDS,
            SYSTEMD_STOP_TIMEOUT_SECONDS,
        )


class MpvHardwareDecodeEvidenceTests(unittest.TestCase):
    def test_saved_positive_log_excerpt_confirms_nouveau_vaapi_h264(self) -> None:
        saved_log = Path(__file__).with_name("mpv-vaapi-positive-fixture.log").read_text(
            encoding="utf-8", errors="replace"
        )
        evidence = classify_mpv_hardware_decode(saved_log, actual_driver_mapping=True)

        self.assertTrue(evidence.confirmed, evidence.missing_evidence())
        self.assertEqual(evidence.missing_evidence(), [])

    def test_requested_options_without_active_decoder_are_not_confirmed(self) -> None:
        log = f"""--hwdec=vaapi-copy
[vaapi] libva: User environment variable requested driver 'nouveau'
[vaapi] libva: Trying to open {LIBVA_DRIVER_ALIAS}
[vaapi] libva: va_openDriver() returns 0
[vd] Trying hardware decoding via h264-vaapi-copy.
[vd] Requesting pixfmt 'vaapi' from decoder.
"""
        evidence = classify_mpv_hardware_decode(log)

        self.assertFalse(evidence.confirmed)
        self.assertEqual(
            evidence.missing_evidence(),
            ["active-vaapi-copy", "actual-driver-mapping"],
        )

    def test_active_vaapi_with_unpinned_driver_path_is_not_confirmed(self) -> None:
        log = f"""[vaapi] libva: User environment variable requested driver 'nouveau'
[vaapi] libva: Trying to open /wrong/location/nouveau_drv_video.so
[vaapi] libva: va_openDriver() returns 0
[vd] Trying hardware decoding via h264-vaapi-copy.
[vd] Requesting pixfmt 'vaapi' from decoder.
[vd] Using hardware decoding (vaapi-copy).
"""
        evidence = classify_mpv_hardware_decode(log)

        self.assertFalse(evidence.confirmed)
        self.assertEqual(
            evidence.missing_evidence(),
            ["pinned-driver-path", "actual-driver-mapping"],
        )

    def test_zero_exit_requires_positive_hardware_evidence(self) -> None:
        self.assertEqual(
            mpv_run_exit_code(
                reason="natural-exit",
                child_returncode=0,
                final_failed_closed=False,
                hardware_decode_confirmed=True,
                post_window_complete=True,
            ),
            0,
        )
        self.assertEqual(
            mpv_run_exit_code(
                reason="natural-exit",
                child_returncode=0,
                final_failed_closed=False,
                hardware_decode_confirmed=False,
                post_window_complete=True,
            ),
            8,
        )

    def test_kernel_or_player_failure_stays_nonzero(self) -> None:
        self.assertEqual(
            mpv_run_exit_code(
                reason="CTXSW_TIMEOUT",
                child_returncode=0,
                final_failed_closed=False,
                hardware_decode_confirmed=True,
                post_window_complete=True,
            ),
            7,
        )
        self.assertEqual(
            mpv_run_exit_code(
                reason="natural-exit",
                child_returncode=1,
                final_failed_closed=False,
                hardware_decode_confirmed=True,
                post_window_complete=True,
            ),
            6,
        )

    def test_operator_stop_or_incomplete_tail_can_never_succeed(self) -> None:
        self.assertEqual(
            mpv_run_exit_code(
                reason="external-signal-SIGINT",
                child_returncode=-signal.SIGINT,
                final_failed_closed=False,
                hardware_decode_confirmed=True,
                post_window_complete=False,
            ),
            9,
        )
        self.assertEqual(
            mpv_run_exit_code(
                reason="natural-exit",
                child_returncode=0,
                final_failed_closed=False,
                hardware_decode_confirmed=True,
                post_window_complete=False,
            ),
            9,
        )
        self.assertEqual(
            mpv_run_exit_code(
                reason="external-signal-SIGINT",
                child_returncode=0,
                final_failed_closed=False,
                hardware_decode_confirmed=True,
                post_window_complete=True,
            ),
            9,
        )

    def test_screenshot_evidence_is_separate_from_decode_completion(self) -> None:
        decode_only = result_status_fields(
            run_exit_code=0,
            screenshot_file_successes=0,
        )
        self.assertEqual(decode_only["decode_run_complete"], "true")
        self.assertEqual(decode_only["screenshot_evidence_complete"], "false")
        self.assertEqual(decode_only["visible_video_confirmation"], "USER_REQUIRED")

        screenshot_only = result_status_fields(
            run_exit_code=6,
            screenshot_file_successes=1,
        )
        self.assertEqual(screenshot_only["decode_run_complete"], "false")
        self.assertEqual(screenshot_only["screenshot_evidence_complete"], "true")
        self.assertEqual(screenshot_only["visible_video_confirmation"], "USER_REQUIRED")


class MpvIpcTests(unittest.TestCase):
    def test_mpv_argv_prepares_directory_for_screenshot_ipc(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mpv-ipc-test-") as temporary:
            run_dir = Path(temporary) / "run"
            run_dir.mkdir()
            screenshot_dir = run_dir / "mpv-screenshots"
            self.assertFalse(screenshot_dir.exists())
            screenshot_path = screenshot_dir / "capture-0001.png"
            socket_path = Path(temporary) / "mpv.sock"
            argv = supervisor.mpv_argv(run_dir, socket_path)
            self.assertTrue(screenshot_dir.is_dir())
            self.assertEqual(screenshot_dir.stat().st_mode & 0o777, 0o700)
            self.assertIn(f"--screenshot-directory={screenshot_dir}", argv)
            received: list[dict[str, object]] = []
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(str(socket_path))
            server.listen(3)

            def respond() -> None:
                for request_id in (1, 2, 3):
                    connection, _ = server.accept()
                    with connection:
                        request = bytearray()
                        while b"\n" not in request:
                            request.extend(connection.recv(4096))
                        parsed = json.loads(bytes(request).split(b"\n", 1)[0])
                        received.append(parsed)
                        if parsed["command"][0] == "get_property":
                            response = {
                                "request_id": request_id,
                                "error": "success",
                                "data": 530.083 if request_id == 1 else 530.125,
                            }
                        else:
                            Path(parsed["command"][1]).write_bytes(
                                b"synthetic png payload"
                            )
                            response = {"request_id": request_id, "error": "success"}
                        connection.sendall(
                            json.dumps(response).encode("utf-8") + b"\n"
                        )

            thread = threading.Thread(target=respond)
            thread.start()
            try:
                reply = mpv_screenshot_request(socket_path, screenshot_path)
            finally:
                thread.join(timeout=2)
                server.close()

        self.assertFalse(thread.is_alive())
        self.assertEqual(
            received,
            [
                {"command": ["get_property", "time-pos"]},
                {
                    "command": [
                        "screenshot-to-file",
                        str(screenshot_path),
                        "video",
                    ]
                },
                {"command": ["get_property", "time-pos"]},
            ],
        )
        self.assertEqual(reply["media_time_before_seconds"], 530.083)
        self.assertEqual(reply["media_time_after_seconds"], 530.125)
        self.assertEqual(
            reply["screenshot_response"],
            {"request_id": 2, "error": "success"},
        )
        self.assertEqual(reply["screenshot_path"], str(screenshot_path))
        self.assertTrue(reply["screenshot_file_exists"])
        self.assertEqual(
            reply["screenshot_sha256"],
            hashlib.sha256(b"synthetic png payload").hexdigest(),
        )

    def test_screenshot_capture_survives_failed_after_timestamp_query(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mpv-ipc-after-time-") as temporary:
            screenshot_path = Path(temporary) / "capture-0001.png"
            replies = [
                {"error": "success", "data": 530.0},
                {"error": "success"},
                {"error": "property unavailable"},
            ]

            def fake_ipc(_socket_path: Path, command: list[object]) -> dict[str, object]:
                if command[0] == "screenshot-to-file":
                    screenshot_path.write_bytes(b"captured")
                return replies.pop(0)

            with patch.object(supervisor, "mpv_ipc_request", side_effect=fake_ipc):
                result = mpv_screenshot_request(Path("/unused"), screenshot_path)

        self.assertEqual(result["media_time_before_seconds"], 530.0)
        self.assertIsNone(result["media_time_after_seconds"])
        self.assertIn("time-pos query failed", result["time_pos_after_error"])
        self.assertTrue(result["screenshot_file_exists"])
        self.assertEqual(
            result["screenshot_sha256"], hashlib.sha256(b"captured").hexdigest()
        )

    def test_after_timestamp_is_queried_after_file_stabilizes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mpv-ipc-order-") as temporary:
            screenshot_path = Path(temporary) / "capture-0001.png"
            events: list[str] = []
            replies = iter((530.0, 530.125))
            real_wait = supervisor._wait_for_screenshot_file

            def fake_ipc(_socket_path: Path, command: list[object]) -> dict[str, object]:
                if command[0] == "get_property":
                    if not events:
                        events.append("time-before")
                    else:
                        events.append("time-after")
                    return {"error": "success", "data": next(replies)}
                events.append("screenshot")
                screenshot_path.write_bytes(b"captured")
                return {"error": "success"}

            def wait_for_file(path: Path) -> tuple[bool, str | None, str | None]:
                result = real_wait(path)
                events.append("file-stable")
                return result

            with (
                patch.object(supervisor, "mpv_ipc_request", side_effect=fake_ipc),
                patch.object(
                    supervisor,
                    "_wait_for_screenshot_file",
                    side_effect=wait_for_file,
                ),
            ):
                result = mpv_screenshot_request(Path("/unused"), screenshot_path)

        self.assertEqual(
            events,
            ["time-before", "screenshot", "file-stable", "time-after"],
        )
        self.assertTrue(result["screenshot_file_exists"])

    def test_successful_ipc_without_output_file_is_not_a_saved_capture(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mpv-ipc-missing-file-") as temporary:
            screenshot_path = Path(temporary) / "capture-0001.png"
            replies = iter(
                (
                    {"error": "success", "data": 530.0},
                    {"error": "success"},
                    {"error": "success", "data": 530.125},
                )
            )
            with (
                patch.object(
                    supervisor,
                    "SCREENSHOT_FILE_WAIT_SECONDS",
                    0.0,
                ),
                patch.object(
                    supervisor,
                    "mpv_ipc_request",
                    side_effect=lambda _socket, _command: next(replies),
                ),
            ):
                result = mpv_screenshot_request(Path("/unused"), screenshot_path)

        self.assertEqual(result["screenshot_response"]["error"], "success")
        self.assertFalse(result["screenshot_file_exists"])
        self.assertIsNone(result["screenshot_sha256"])
        self.assertIn("timed out waiting", result["screenshot_file_error"])


    def test_screenshot_requires_a_valid_media_timestamp(self) -> None:
        with patch.object(
            supervisor,
            "mpv_ipc_request",
            return_value={"error": "success", "data": "not-a-number"},
        ) as ipc:
            with self.assertRaisesRegex(RuntimeError, "invalid time-pos"):
                mpv_screenshot_request(
                    Path("/unused"), Path("/tmp/unused-capture.png")
                )
        ipc.assert_called_once_with(Path("/unused"), ["get_property", "time-pos"])

    def test_screenshot_path_must_be_absolute_png(self) -> None:
        with self.assertRaisesRegex(ValueError, "absolute and end in .png"):
            mpv_screenshot_request(Path("/unused"), Path("relative.jpg"))

    def test_screenshot_refuses_to_overwrite_existing_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mpv-ipc-existing-") as temporary:
            screenshot_path = Path(temporary) / "capture-0001.png"
            screenshot_path.write_bytes(b"old capture")
            with patch.object(supervisor, "mpv_ipc_request") as ipc:
                with self.assertRaisesRegex(FileExistsError, "already exists"):
                    mpv_screenshot_request(Path("/unused"), screenshot_path)
            ipc.assert_not_called()

if __name__ == "__main__":
    unittest.main(verbosity=2)
