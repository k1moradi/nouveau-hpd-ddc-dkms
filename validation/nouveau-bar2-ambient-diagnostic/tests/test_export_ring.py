from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "validation/nouveau-bar2-ambient-diagnostic/export_ring.py"
SPEC = importlib.util.spec_from_file_location("export_bar2_ring", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
EXPORTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORTER)

BOOT = "112233445566778899aabbccddeeff00"


def ring_text(
    *,
    capacity: int = 8,
    head: int = 2,
    captured: int = 2,
    dropped: int = 0,
    footer_head: int | None = None,
    cutoff_ns: int = 7_000_000,
    events: tuple[tuple[int, int, str], ...] | None = None,
) -> str:
    if events is None:
        events = (
            (0, 5_000_000,
             "NOUVEAU_DIAG_BAR2_MAP seq=1 id=7 stage=MAP_READY"),
            (1, 6_000_000,
             "NOUVEAU_DIAG_BAR2_RESET seq=2 gen=1 stage=BEGIN"),
        )
    footer_head = head if footer_head is None else footer_head
    lines = [
        f"NOUVEAU_DIAG_BAR2_RING capacity={capacity} head={head} captured={captured} "
        f"dropped={dropped} cutoff_ns={cutoff_ns}"
    ]
    lines.extend(
        f"NOUVEAU_DIAG_BAR2_RING_EVENT index={index} mono_ns={mono_ns} message={message}"
        for index, mono_ns, message in events
    )
    lines.append(
        f"NOUVEAU_DIAG_BAR2_RING_END head={footer_head} dropped={dropped}"
    )
    return "\n".join(lines) + "\n"


def journal_row(mono_usec: int = 4_000, boot: str = BOOT, **extra: object) -> str:
    record: dict[str, object] = {
        "_BOOT_ID": boot,
        "_TRANSPORT": "kernel",
        "__MONOTONIC_TIMESTAMP": str(mono_usec),
        "MESSAGE": "kernel baseline row",
    }
    record.update(extra)
    return json.dumps(record)


class RingParserTests(unittest.TestCase):
    def parse(self, payload: str) -> tuple[dict[str, int], list[dict[str, object]]]:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ring.txt"
            path.write_text(payload, encoding="ascii")
            return EXPORTER.parse_ring(path)

    def test_complete_stable_ring_is_accepted(self):
        header, events = self.parse(ring_text())
        self.assertEqual(header, {
            "capacity": 8, "head": 2, "captured": 2, "dropped": 0,
            "cutoff_ns": 7_000_000, "end_head": 2, "end_dropped": 0,
        })
        self.assertEqual([event["index"] for event in events], [0, 1])

    def test_reported_drop_is_rejected(self):
        with self.assertRaisesRegex(EXPORTER.RingCaptureError, "dropped events"):
            self.parse(ring_text(dropped=1))

    def test_overflowed_head_is_rejected(self):
        payload = ring_text(head=9, captured=8, dropped=1, events=tuple(
            (index, index + 1, "NOUVEAU_DIAG_BAR2_MAP seq=1 id=7 stage=MAP_READY")
            for index in range(8)
        ))
        with self.assertRaisesRegex(EXPORTER.RingCaptureError, "overflowed"):
            self.parse(payload)

    def test_append_during_dump_preserves_a_complete_prefix_snapshot(self):
        header, events = self.parse(ring_text(footer_head=3))
        self.assertEqual(header["head"], 2)
        self.assertEqual(header["end_head"], 3)
        self.assertEqual([event["index"] for event in events], [0, 1])

    def test_ring_head_moving_backwards_is_rejected(self):
        with self.assertRaisesRegex(EXPORTER.RingCaptureError, "moved backwards"):
            self.parse(ring_text(footer_head=1))

    def test_overflow_after_snapshot_does_not_invalidate_captured_prefix(self):
        payload = ring_text().replace(
            "NOUVEAU_DIAG_BAR2_RING_END head=2 dropped=0",
            "NOUVEAU_DIAG_BAR2_RING_END head=9 dropped=1",
        )
        header, events = self.parse(payload)
        self.assertEqual(header["head"], 2)
        self.assertEqual(header["end_head"], 9)
        self.assertEqual(header["end_dropped"], 1)
        self.assertEqual([event["index"] for event in events], [0, 1])

    def test_drop_before_snapshot_is_rejected(self):
        with self.assertRaisesRegex(EXPORTER.RingCaptureError, "before the snapshot cutoff"):
            self.parse(ring_text(dropped=1))

    def test_drop_counter_cannot_move_backwards_during_dump(self):
        payload = ring_text(dropped=1).replace(
            "NOUVEAU_DIAG_BAR2_RING_END head=2 dropped=1",
            "NOUVEAU_DIAG_BAR2_RING_END head=2 dropped=0",
        )
        with self.assertRaisesRegex(EXPORTER.RingCaptureError, "counter moved backwards"):
            self.parse(payload)

    def test_unpublished_slot_is_rejected(self):
        payload = ring_text().replace(
            "NOUVEAU_DIAG_BAR2_RING_EVENT index=1",
            "NOUVEAU_DIAG_BAR2_RING_GAP index=1\nNOUVEAU_DIAG_BAR2_RING_EVENT index=1",
        )
        with self.assertRaisesRegex(EXPORTER.RingCaptureError, "unpublished slots"):
            self.parse(payload)

    def test_duplicate_reordered_or_missing_indexes_are_rejected(self):
        events = (
            (1, 5_000_000, "NOUVEAU_DIAG_BAR2_MAP seq=1 id=7 stage=MAP_READY"),
            (1, 6_000_000, "NOUVEAU_DIAG_BAR2_RESET seq=2 gen=1 stage=BEGIN"),
        )
        with self.assertRaisesRegex(EXPORTER.RingCaptureError, "missing, duplicated, or reordered"):
            self.parse(ring_text(events=events))

    def test_unknown_ring_schema_is_rejected(self):
        with self.assertRaisesRegex(EXPORTER.RingCaptureError, "unknown ring record"):
            self.parse(ring_text().replace("NOUVEAU_DIAG_BAR2_RING_END", "UNKNOWN_END"))

    def test_event_newer_than_snapshot_cutoff_is_rejected(self):
        events = (
            (0, 8_000_000, "NOUVEAU_DIAG_BAR2_MAP seq=1 id=7 stage=MAP_READY"),
            (1, 6_000_000, "NOUVEAU_DIAG_BAR2_RESET seq=2 gen=1 stage=BEGIN"),
        )
        with self.assertRaisesRegex(EXPORTER.RingCaptureError, "newer than snapshot cutoff"):
            self.parse(ring_text(events=events))

    def test_prior_boot_event_volume_is_preserved_without_gaps(self):
        event_count = 36_215
        events = tuple(
            (
                index,
                (index + 1) * 1_000,
                f"NOUVEAU_DIAG_BAR2_MAP seq={index + 1} id=7 stage=MAP_READY",
            )
            for index in range(event_count)
        )
        header, parsed = self.parse(ring_text(
            capacity=65_536,
            head=event_count,
            captured=event_count,
            footer_head=event_count + 3,
            cutoff_ns=40_000_000,
            events=events,
        ))
        self.assertEqual(header["end_head"] - header["head"], 3)
        self.assertEqual(len(parsed), event_count)
        self.assertEqual(parsed[-1]["index"], event_count - 1)


class RingExportTests(unittest.TestCase):
    def test_exports_loss_checked_ring_records_as_kernel_journal_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ring = root / "ring.txt"
            journal = root / "kernel.jsonl"
            output = root / "merged.jsonl"
            ring.write_text(ring_text(), encoding="ascii")
            journal.write_text(journal_row() + "\n", encoding="utf-8")

            metadata = EXPORTER.export_ring(ring, journal, output)
            rows = [json.loads(line) for line in output.read_text().splitlines()]

        self.assertEqual(metadata["ring_dropped_at_snapshot"], 0)
        self.assertEqual(metadata["ring_dropped_after_snapshot"], 0)
        self.assertTrue(metadata["ring_loss_proven_through_cutoff"])
        self.assertEqual(metadata["ring_appends_during_snapshot"], 0)
        self.assertEqual(metadata["boot_id"], BOOT)
        self.assertEqual(len(rows), 3)
        ring_rows = [row for row in rows if "nouveau-bar2-event-ring" in row.get("SYSLOG_IDENTIFIER", "")]
        self.assertEqual(len(ring_rows), 2)
        self.assertEqual(ring_rows[0]["__MONOTONIC_TIMESTAMP"], "5000")
        self.assertEqual(ring_rows[1]["MESSAGE"],
                         "NOUVEAU_DIAG_BAR2_RESET seq=2 gen=1 stage=BEGIN")
        self.assertEqual(ring_rows[1]["NOUVEAU_DIAG_RING_MONO_NS"], "6000000")

    def test_export_marks_a_consistent_prefix_if_ring_appends_during_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ring = root / "ring.txt"
            journal = root / "kernel.jsonl"
            output = root / "merged.jsonl"
            ring.write_text(ring_text(footer_head=3), encoding="ascii")
            journal.write_text(journal_row() + "\n", encoding="utf-8")

            metadata = EXPORTER.export_ring(ring, journal, output)

        self.assertTrue(metadata["ring_loss_proven_through_cutoff"])
        self.assertEqual(metadata["ring_head"], 2)
        self.assertEqual(metadata["ring_end_head"], 3)
        self.assertEqual(metadata["ring_appends_during_snapshot"], 1)

    def test_mixed_boot_journal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ring = root / "ring.txt"
            journal = root / "kernel.jsonl"
            output = root / "merged.jsonl"
            ring.write_text(ring_text(), encoding="ascii")
            journal.write_text(
                journal_row() + "\n"
                + journal_row(boot="ffeeddccbbaa99887766554433221100") + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(EXPORTER.RingCaptureError, "exactly one boot ID"):
                EXPORTER.export_ring(ring, journal, output)

    def test_journal_without_monotonic_timestamp_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ring = root / "ring.txt"
            journal = root / "kernel.jsonl"
            output = root / "merged.jsonl"
            ring.write_text(ring_text(), encoding="ascii")
            bad = json.dumps({"_BOOT_ID": BOOT, "_TRANSPORT": "kernel", "MESSAGE": "x"})
            journal.write_text(bad + "\n", encoding="utf-8")
            with self.assertRaisesRegex(EXPORTER.RingCaptureError, "lacks monotonic timestamp"):
                EXPORTER.export_ring(ring, journal, output)

    def test_old_printk_map_records_are_not_merged_with_ring(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ring = root / "ring.txt"
            journal = root / "kernel.jsonl"
            output = root / "merged.jsonl"
            ring.write_text(ring_text(), encoding="ascii")
            journal.write_text(journal_row(
                MESSAGE="NOUVEAU_DIAG_BAR2_MAP seq=9 id=4 stage=MAP_READY"
            ) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(EXPORTER.RingCaptureError, "replaced by the ring"):
                EXPORTER.export_ring(ring, journal, output)

    def test_kernel_message_loss_before_cutoff_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ring = root / "ring.txt"
            journal = root / "kernel.jsonl"
            output = root / "merged.jsonl"
            ring.write_text(ring_text(), encoding="ascii")
            journal.write_text(journal_row(
                mono_usec=4_500,
                MESSAGE="/dev/kmsg buffer overrun, some messages lost",
            ) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(EXPORTER.RingCaptureError, "message loss"):
                EXPORTER.export_ring(ring, journal, output)

    def test_records_after_snapshot_cutoff_are_not_merged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ring = root / "ring.txt"
            journal = root / "kernel.jsonl"
            output = root / "merged.jsonl"
            ring.write_text(ring_text(), encoding="ascii")
            journal.write_text(
                journal_row(mono_usec=4_000) + "\n"
                + journal_row(
                    mono_usec=8_000,
                    MESSAGE="later record after cutoff",
                ) + "\n",
                encoding="utf-8",
            )

            metadata = EXPORTER.export_ring(ring, journal, output)
            rows = [json.loads(line) for line in output.read_text().splitlines()]

        self.assertEqual(metadata["kernel_journal_records_seen"], 2)
        self.assertEqual(metadata["kernel_journal_records_through_cutoff"], 1)
        self.assertFalse(any(row.get("MESSAGE") == "later record after cutoff" for row in rows))


if __name__ == "__main__":
    unittest.main()
