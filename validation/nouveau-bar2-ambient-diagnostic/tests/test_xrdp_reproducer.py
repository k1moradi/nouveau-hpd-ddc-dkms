from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


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
    def _ring_tree(self, root: Path) -> tuple[Path, Path]:
        drm_root = root / "sys/class/drm"
        debugfs_root = root / "sys/kernel/debug/dri"
        device = root / "sys/devices/pci0000:00/0000:01:00.0"
        device.mkdir(parents=True)
        card = drm_root / "card0"
        card.mkdir(parents=True)
        (card / "device").symlink_to(device)
        (card / "dev").write_text("226:0\n", encoding="ascii")

        for minor in ("0", "128", "0000:01:00.0"):
            node = debugfs_root / minor
            node.mkdir(parents=True)
            (node / "ambient_bar2_status").write_text("status\n", encoding="ascii")
            (node / "ambient_bar2_events").write_text("events\n", encoding="ascii")
        return drm_root, debugfs_root

    def test_ring_locator_selects_pinned_gpu_primary_minor_with_alias_nodes(self):
        with tempfile.TemporaryDirectory() as temporary:
            drm_root, debugfs_root = self._ring_tree(Path(temporary))

            with (
                mock.patch.object(RUNNER.os, "geteuid", return_value=1000),
                mock.patch.object(
                    RUNNER,
                    "run_command",
                    return_value=subprocess.CompletedProcess([], 0, "", ""),
                ) as run_command,
            ):
                status, events = RUNNER.find_debugfs_ring_files(
                    "0000:01:00.0",
                    drm_class_root=drm_root,
                    debugfs_root=debugfs_root,
                )

            self.assertEqual(status, debugfs_root / "0/ambient_bar2_status")
            self.assertEqual(events, debugfs_root / "0/ambient_bar2_events")
            self.assertEqual(run_command.call_count, 2)
            run_command.assert_any_call(
                ["sudo", "-n", "test", "-f", str(status)], timeout=10,
            )
            run_command.assert_any_call(
                ["sudo", "-n", "test", "-f", str(events)], timeout=10,
            )

    def test_ring_locator_rejects_malformed_or_unmatched_pci_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            drm_root, debugfs_root = self._ring_tree(Path(temporary))

            with self.assertRaisesRegex(RUNNER.ReproducerError, "malformed"):
                RUNNER.find_debugfs_ring_files(
                    "not-a-bdf", drm_class_root=drm_root,
                    debugfs_root=debugfs_root,
                )
            with self.assertRaisesRegex(RUNNER.ReproducerError, "found 0"):
                RUNNER.find_debugfs_ring_files(
                    "0000:02:00.0", drm_class_root=drm_root,
                    debugfs_root=debugfs_root,
                )

    def test_ring_locator_rejects_ambiguous_primary_cards(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            drm_root, debugfs_root = self._ring_tree(root)
            device = root / "sys/devices/pci0000:00/0000:01:00.0"
            card1 = drm_root / "card1"
            card1.mkdir()
            (card1 / "device").symlink_to(device)
            (card1 / "dev").write_text("226:1\n", encoding="ascii")

            with self.assertRaisesRegex(RUNNER.ReproducerError, "found 2"):
                RUNNER.find_debugfs_ring_files(
                    "0000:01:00.0",
                    drm_class_root=drm_root,
                    debugfs_root=debugfs_root,
                )

    def test_ring_locator_rejects_missing_primary_ring_node(self):
        with tempfile.TemporaryDirectory() as temporary:
            drm_root, debugfs_root = self._ring_tree(Path(temporary))

            def reject_status(argv: list[str], *, timeout: float = 30):
                code = 1 if argv[-1].endswith("ambient_bar2_status") else 0
                return subprocess.CompletedProcess(argv, code, "", "permission denied")

            with (
                mock.patch.object(RUNNER.os, "geteuid", return_value=1000),
                mock.patch.object(RUNNER, "run_command", side_effect=reject_status),
                self.assertRaisesRegex(RUNNER.ReproducerError, "cannot verify required"),
            ):
                RUNNER.find_debugfs_ring_files(
                    "0000:01:00.0",
                    drm_class_root=drm_root,
                    debugfs_root=debugfs_root,
                )

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
