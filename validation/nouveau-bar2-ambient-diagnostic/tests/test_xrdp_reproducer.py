from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import selectors
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "validation/nouveau-bar2-ambient-diagnostic/run_xrdp_console_reproducer.py"
SPEC = importlib.util.spec_from_file_location("xrdp_bar2_reproducer", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)

BOOT = "112233445566778899aabbccddeeff00"


def journal_line(message: str, *, boot: str = BOOT, transport: str = "kernel") -> bytes:
    return (
        json.dumps({
            "_BOOT_ID": boot,
            "_TRANSPORT": transport,
            "MESSAGE": message,
        }, separators=(",", ":")) + "\n"
    ).encode("ascii")


class XrdpReproducerTests(unittest.TestCase):
    def test_profile_builds_the_pinned_bounded_xrdp_trigger_only(self):
        profile = RUNNER.load_json(RUNNER.PROFILE)
        workload = profile["workload"]
        python = Path(profile["artifacts"]["python"]["path"])
        argv = RUNNER.benchmark_argv(
            Path(profile["workspace"]), ":0", python, workload,
        )
        self.assertEqual(argv[0:2], [str(python), "-B"])
        self.assertIn("--mode", argv)
        self.assertEqual(argv[argv.index("--mode") + 1], "graphics-under-churn")
        self.assertEqual(argv[argv.index("--duration") + 1], "20")
        self.assertEqual(argv[argv.index("--repetitions") + 1], "1")
        self.assertIn("--enable-gfx-for-vnc", argv)
        self.assertFalse(any(
            name in " ".join(argv).lower()
            for name in ("mpv", "ffmpeg", "ffplay", "vainfo")
        ))

    def test_profile_cannot_silently_expand_or_change_trigger(self):
        profile = RUNNER.load_json(RUNNER.PROFILE)
        workload = dict(profile["workload"])
        workload["duration_seconds"] = 600
        with self.assertRaisesRegex(RUNNER.ReproducerError, "duration_seconds"):
            RUNNER.benchmark_argv(
                Path(profile["workspace"]), ":0",
                Path(profile["artifacts"]["python"]["path"]), workload,
            )

    def test_kernel_record_detects_fault_ring_fault_and_log_loss(self):
        cases = (
            ("nouveau: fifo: fault 00 [READ] at 0x46f000 engine 05 [BAR2] "
             "client 07 [HUB/HOST_CPU] reason 02 [PTE]", "BAR2/HOST_CPU/PTE"),
            ("NOUVEAU_DIAG_BAR2_FAULT seq=9 va=0x46f000", "NOUVEAU_DIAGNOSTIC_BAR2_FAULT"),
            ("/dev/kmsg buffer overrun, some messages lost", "KERNEL_JOURNAL_LOSS"),
        )
        for message, expected in cases:
            with self.subTest(message=message):
                self.assertEqual(
                    RUNNER.kernel_record_stop_reason(
                        journal_line(message).decode("ascii"), BOOT,
                    ),
                    expected,
                )

    def test_kernel_record_rejects_wrong_transport_or_boot(self):
        with self.assertRaisesRegex(RUNNER.ReproducerError, "non-kernel"):
            RUNNER.kernel_record_stop_reason(
                journal_line("ordinary", transport="syslog").decode("ascii"), BOOT,
            )
        with self.assertRaisesRegex(RUNNER.ReproducerError, "boot ID"):
            RUNNER.kernel_record_stop_reason(
                journal_line("ordinary", boot="ffeeddccbbaa99887766554433221100").decode("ascii"),
                BOOT,
            )
        self.assertIsNone(RUNNER.kernel_record_stop_reason(
            journal_line("ordinary Nouveau informational record").decode("ascii"), BOOT,
        ))

    def test_follower_preserves_partial_line_after_complete_record(self):
        first = journal_line("ordinary first row")
        second = journal_line("ordinary second row")
        split = second.index(b"\"MESSAGE\"") + 12
        read_fd, write_fd = os.pipe()
        reader = os.fdopen(read_fd, "rb", buffering=0)

        class Follower:
            stdout = reader

            @staticmethod
            def poll() -> None:
                return None

        selector = selectors.DefaultSelector()
        buffer = bytearray()
        try:
            os.write(write_fd, first + second[:split])
            with tempfile.TemporaryDirectory() as temporary:
                saved = Path(temporary) / "follow.jsonl"
                reason, failed = RUNNER.read_live_journal(
                    Follower(), selector, saved, BOOT, buffer,
                )
                self.assertIsNone(reason)
                self.assertFalse(failed)
                self.assertEqual(buffer, second[:split])

                os.write(write_fd, second[split:])
                reason, failed = RUNNER.read_live_journal(
                    Follower(), selector, saved, BOOT, buffer,
                )
                self.assertIsNone(reason)
                self.assertFalse(failed)
                self.assertEqual(saved.read_bytes(), first + second)
                self.assertEqual(buffer, bytearray())
        finally:
            selector.close()
            reader.close()
            os.close(write_fd)


if __name__ == "__main__":
    unittest.main()
