#!/usr/bin/env python3
"""CPU-only checks for detached mpv monitoring and final classification."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from detached_mpv_supervisor import (
    FINAL_JOURNAL_QUERY_TIMEOUT_SECONDS,
    LIBVA_DRIVER_ALIAS,
    POST_STOP_OBSERVATION_SECONDS,
    ProcessGroupStopper,
    PROCESS_GROUP_STOP_MAX_SECONDS,
    SYSTEMCTL_STOP_COMMAND_TIMEOUT_SECONDS,
    SYSTEMD_STOP_MARGIN_SECONDS,
    SYSTEMD_STOP_TIMEOUT_SECONDS,
    classify_mpv_hardware_decode,
    detached_unit_properties,
    final_run_status,
    mpv_ipc_request,
    mpv_run_exit_code,
    passive_post_stop_snapshot,
    first_stop_signature,
    process_group_members,
    stop_reason_for_line,
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

    def test_unavailable_final_journal_fails_closed(self) -> None:
        reason, trigger, signature, failed = final_run_status({}, "", "journalctl failed")
        self.assertEqual(reason, "post-run-journal-unavailable")
        self.assertIsNone(trigger)
        self.assertIsNone(signature)
        self.assertTrue(failed)


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
        evidence = classify_mpv_hardware_decode(saved_log)

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
        self.assertEqual(evidence.missing_evidence(), ["active-vaapi-copy"])

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
        self.assertEqual(evidence.missing_evidence(), ["pinned-driver-path"])

    def test_zero_exit_requires_positive_hardware_evidence(self) -> None:
        self.assertEqual(
            mpv_run_exit_code(
                reason="natural-exit",
                child_returncode=0,
                final_failed_closed=False,
                hardware_decode_confirmed=True,
            ),
            0,
        )
        self.assertEqual(
            mpv_run_exit_code(
                reason="natural-exit",
                child_returncode=0,
                final_failed_closed=False,
                hardware_decode_confirmed=False,
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
            ),
            7,
        )
        self.assertEqual(
            mpv_run_exit_code(
                reason="natural-exit",
                child_returncode=1,
                final_failed_closed=False,
                hardware_decode_confirmed=True,
            ),
            6,
        )


class MpvIpcTests(unittest.TestCase):
    def test_screenshot_command_uses_json_ipc_and_reads_reply(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mpv-ipc-test-") as temporary:
            socket_path = Path(temporary) / "mpv.sock"
            received: list[dict[str, object]] = []
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(str(socket_path))
            server.listen(1)

            def respond() -> None:
                connection, _ = server.accept()
                with connection:
                    request = bytearray()
                    while b"\n" not in request:
                        request.extend(connection.recv(4096))
                    received.append(json.loads(bytes(request).split(b"\n", 1)[0]))
                    connection.sendall(b'{"request_id":1,"error":"success"}\n')

            thread = threading.Thread(target=respond)
            thread.start()
            try:
                reply = mpv_ipc_request(socket_path, ["screenshot", "video"])
            finally:
                thread.join(timeout=2)
                server.close()

        self.assertFalse(thread.is_alive())
        self.assertEqual(received, [{"command": ["screenshot", "video"]}])
        self.assertEqual(reply, {"request_id": 1, "error": "success"})

if __name__ == "__main__":
    unittest.main(verbosity=2)
