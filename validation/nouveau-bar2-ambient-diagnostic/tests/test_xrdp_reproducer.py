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
    def test_admission_runs_unprivileged_invocation_via_sudo_with_session_identity(self):
        session_environment = {
            "XAUTHORITY": "/run/sddm/xauth_test",
            "XDG_SESSION_ID": "4",
            "XDG_RUNTIME_DIR": "/run/user/1000",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
        }
        with (
            mock.patch.object(RUNNER.os, "geteuid", return_value=1000),
            mock.patch.dict(RUNNER.os.environ, session_environment, clear=True),
        ):
            argv = RUNNER.admission_command(Path("/tmp/manifest.json"), ":0")

        self.assertEqual(argv[:3], ["sudo", "-n", "env"])
        self.assertIn("DISPLAY=:0", argv)
        self.assertIn("PYTHONDONTWRITEBYTECODE=1", argv)
        for name, value in session_environment.items():
            self.assertIn(f"{name}={value}", argv)
        self.assertEqual(
            argv[-5:],
            [
                sys.executable, "-B", str(RUNNER.ADMISSION),
                "--manifest", "/tmp/manifest.json",
            ],
        )

    def test_admission_running_as_root_does_not_nest_sudo(self):
        with mock.patch.object(RUNNER.os, "geteuid", return_value=0):
            argv = RUNNER.admission_command(Path("/tmp/manifest.json"), ":0")

        self.assertEqual(
            argv,
            [
                sys.executable, "-B", str(RUNNER.ADMISSION),
                "--manifest", "/tmp/manifest.json",
            ],
        )

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
        direct_module = Path(profile["artifacts"]["direct_module"]["path"])
        argv = RUNNER.benchmark_argv(
            Path(profile["workspace"]), ":0", python, workload, direct_module,
        )
        self.assertEqual(argv[0:2], [str(python), "-B"])
        self.assertEqual(argv[argv.index("--backend") + 1], "direct-x11")
        self.assertIn("--mode", argv)
        self.assertEqual(argv[argv.index("--mode") + 1], "graphics-under-churn")
        self.assertEqual(
            argv[argv.index("--direct-graphics-transport") + 1], "gfx-planar",
        )
        self.assertEqual(
            argv[argv.index("--direct-module") + 1],
            str(direct_module.resolve(strict=True)),
        )
        self.assertEqual(argv[argv.index("--duration") + 1], "20")
        self.assertEqual(argv[argv.index("--repetitions") + 1], "1")
        self.assertIn("--gl-readback-32x32", argv)
        self.assertIs(workload["gl_readback_32x32"], True)
        self.assertFalse(any(
            name in " ".join(argv).lower()
            for name in ("mpv", "ffmpeg", "ffplay", "vainfo")
        ))
        self.assertNotIn("--no-chansrv", argv)
        self.assertNotIn("--enable-gfx-for-vnc", argv)
        self.assertNotIn("--pipeline", argv)

    def test_profile_requires_explicit_trigger_event_budget(self):
        profile = RUNNER.load_json(RUNNER.PROFILE)
        self.assertEqual(profile["workload"]["expected_trigger_events"], 219_478)
        for invalid in (None, 1, 219_477, -1, "219478", True):
            workload = dict(profile["workload"])
            if invalid is None:
                workload.pop("expected_trigger_events")
            else:
                workload["expected_trigger_events"] = invalid
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                RUNNER.ReproducerError, "expected_trigger_events.*integer"
            ):
                RUNNER.validate_trigger_event_budget(workload)
        self.assertEqual(
            RUNNER.validate_trigger_event_budget(
                {"expected_trigger_events": 219_478}
            ),
            219_478,
        )

    def test_headroom_policy_rejects_weakened_or_coerced_profile_values(self):
        profile = RUNNER.load_json(RUNNER.PROFILE)
        original = dict(profile["workload"])
        self.assertEqual(
            RUNNER.validate_headroom_policy(original),
            (219_478, 30_000, 1.5, 5_000),
        )
        weakened = (
            ("minimum_ring_headroom", 0),
            ("minimum_ring_headroom", 29_999),
            ("minimum_ring_headroom", "30000"),
            ("ring_headroom_safety_factor", 1.0),
            ("ring_headroom_safety_factor", True),
            ("ring_headroom_safety_factor", "1.5"),
            ("ring_headroom_safety_factor", float("nan")),
            ("ring_headroom_safety_factor", float("inf")),
            ("ring_headroom_safety_margin", 4_999),
            ("ring_headroom_safety_margin", 5_000.0),
            ("ring_headroom_safety_margin", True),
        )
        for field, value in weakened:
            workload = dict(original)
            workload[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(
                RUNNER.ReproducerError
            ):
                RUNNER.validate_headroom_policy(workload)

    def test_profile_cannot_silently_expand_or_change_trigger(self):
        profile = RUNNER.load_json(RUNNER.PROFILE)
        workload = dict(profile["workload"])
        workload["duration_seconds"] = 600
        with self.assertRaisesRegex(RUNNER.ReproducerError, "duration_seconds"):
            RUNNER.benchmark_argv(
                Path(profile["workspace"]), ":0",
                Path(profile["artifacts"]["python"]["path"]), workload,
                Path(profile["artifacts"]["direct_module"]["path"]),
            )

    def test_profile_requires_the_pinned_ten_second_passive_baseline(self):
        profile = RUNNER.load_json(RUNNER.PROFILE)
        workload = dict(profile["workload"])
        workload["passive_baseline_seconds"] = 0
        with self.assertRaisesRegex(RUNNER.ReproducerError, "passive_baseline_seconds"):
            RUNNER.benchmark_argv(
                Path(profile["workspace"]), ":0",
                Path(profile["artifacts"]["python"]["path"]), workload,
                Path(profile["artifacts"]["direct_module"]["path"]),
            )

    def test_first_gpu_churn_frame_requires_one_valid_completed_frame_marker(self):
        self.assertEqual(
            RUNNER.first_gpu_churn_frame(
                "startup\nGPU_CHURN_FIRST_FRAME monotonic_ns=123456789\n"
            ),
            123456789,
        )
        self.assertIsNone(RUNNER.first_gpu_churn_frame("benchmark failed before GL\n"))
        for invalid in (
            "GPU_CHURN_FIRST_FRAME monotonic_ns=0\n",
            "GPU_CHURN_FIRST_FRAME monotonic_ns=12x\n",
            "GPU_CHURN_FIRST_FRAME monotonic_ns=1\nGPU_CHURN_FIRST_FRAME monotonic_ns=2\n",
        ):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                RUNNER.ReproducerError, "malformed or duplicate",
            ):
                RUNNER.first_gpu_churn_frame(invalid)

    def test_first_gpu_frame_can_require_checksumming_gl_back_readback(self):
        marker = (
            "GPU_CHURN_FIRST_FRAME monotonic_ns=123456789 "
            "gl_readback=32x32 checksum=3948571\n"
        )
        self.assertEqual(
            RUNNER.first_gpu_churn_frame(marker, require_readback=True),
            123456789,
        )
        self.assertEqual(RUNNER.first_gpu_readback_checksum(marker), 3948571)
        with self.assertRaisesRegex(RUNNER.ReproducerError, "lacks GL readback"):
            RUNNER.first_gpu_churn_frame(
                "GPU_CHURN_FIRST_FRAME monotonic_ns=123456789\n",
                require_readback=True,
            )
        with self.assertRaisesRegex(RUNNER.ReproducerError, "unexpectedly reported"):
            RUNNER.first_gpu_churn_frame(marker)
        with self.assertRaisesRegex(RUNNER.ReproducerError, "invalid readback checksum"):
            RUNNER.first_gpu_churn_frame(
                "GPU_CHURN_FIRST_FRAME monotonic_ns=123456789 "
                "gl_readback=32x32 checksum=4294967296\n",
                require_readback=True,
            )

    def test_sample_is_never_valid_without_a_completed_gpu_frame(self):
        self.assertFalse(RUNNER.experimental_sample_validity(
            first_frame_ns=None,
            evidence_complete=True,
            monitor_failure=False,
            trigger_completed=True,
            kernel_stop_observed=False,
        ))
        self.assertTrue(RUNNER.experimental_sample_validity(
            first_frame_ns=123,
            evidence_complete=True,
            monitor_failure=False,
            trigger_completed=True,
            kernel_stop_observed=False,
        ))
        self.assertTrue(RUNNER.experimental_sample_validity(
            first_frame_ns=123,
            evidence_complete=True,
            monitor_failure=False,
            trigger_completed=False,
            kernel_stop_observed=True,
        ))
        self.assertFalse(RUNNER.experimental_sample_validity(
            first_frame_ns=123,
            evidence_complete=False,
            monitor_failure=False,
            trigger_completed=True,
            kernel_stop_observed=False,
        ))
        self.assertFalse(RUNNER.experimental_sample_validity(
            first_frame_ns=123,
            evidence_complete=True,
            monitor_failure=True,
            trigger_completed=True,
            kernel_stop_observed=False,
        ))

    def test_journal_delta_returns_first_stop_without_discarding_rows(self):
        payload = journal_line(
            "nouveau: fifo: fault 00 [READ] at 0x46f000 engine 05 [BAR2] "
            "client 07 [HUB/HOST_CPU] reason 02 [PTE]"
        ).decode("ascii").strip()
        record = json.loads(payload)
        record["__CURSOR"] = "cursor-2"
        with mock.patch.object(
            RUNNER, "run_command",
            return_value=subprocess.CompletedProcess(
                [], 0, json.dumps(record) + "\n", "",
            ),
        ):
            lines, cursor, reason = RUNNER.journal_records_after("cursor-1", BOOT)

        self.assertEqual(len(lines), 1)
        self.assertEqual(cursor, "cursor-2")
        self.assertEqual(reason, "BAR2/HOST_CPU/PTE")

    def test_passive_baseline_stops_and_saves_first_kernel_fault(self):
        line = journal_line("NOUVEAU_DIAG_BAR2_FAULT seq=9 va=0x46f000").decode("ascii")
        with tempfile.TemporaryDirectory() as temporary:
            delta_path = Path(temporary) / "pretrigger.jsonl"
            with (
                mock.patch.object(
                    RUNNER, "journal_records_after",
                    return_value=([line.strip()], "cursor-9", "NOUVEAU_DIAGNOSTIC_BAR2_FAULT"),
                ),
                mock.patch.object(RUNNER.time, "monotonic", return_value=1.0),
            ):
                cursor, reason = RUNNER.wait_passive_baseline(
                    10, "cursor-8", BOOT, delta_path,
                )

            self.assertEqual(cursor, "cursor-9")
            self.assertEqual(reason, "NOUVEAU_DIAGNOSTIC_BAR2_FAULT")
            self.assertEqual(delta_path.read_text(encoding="utf-8"), line)

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
