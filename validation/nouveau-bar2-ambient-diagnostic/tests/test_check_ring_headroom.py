from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "check_ring_headroom.py"
SPEC = importlib.util.spec_from_file_location("check_bar2_ring_headroom", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


def status(
    *,
    enabled: int = 1,
    capacity: int = 65_536,
    head_before: int = 10_000,
    head_after: int = 10_000,
    dropped_before: int = 0,
    dropped_after: int = 0,
) -> str:
    return (
        "NOUVEAU_DIAG_BAR2_RING_STATUS "
        f"enabled={enabled} capacity={capacity} "
        f"head_before={head_before} head_after={head_after} "
        f"dropped_before={dropped_before} dropped_after={dropped_after}\n"
    )


class RingHeadroomTests(unittest.TestCase):
    def test_admits_stable_ring_with_at_least_minimum_headroom(self):
        result = CHECKER.check_status(status())
        self.assertTrue(result["trigger_eligible"])
        self.assertEqual(result["free"], 55_536)

    def test_rejects_ring_below_minimum_headroom(self):
        with self.assertRaisesRegex(CHECKER.RingHeadroomError, "need at least"):
            CHECKER.check_status(status(head_before=35_537, head_after=35_537))

    def test_rejects_disabled_ring(self):
        with self.assertRaisesRegex(CHECKER.RingHeadroomError, "not enabled"):
            CHECKER.check_status(status(enabled=0))

    def test_rejects_existing_event_loss(self):
        with self.assertRaisesRegex(CHECKER.RingHeadroomError, "already dropped"):
            CHECKER.check_status(status(dropped_before=1, dropped_after=1))

    def test_rejects_changing_head_or_drop_count(self):
        cases = (
            status(head_before=1, head_after=2),
            status(dropped_before=0, dropped_after=1),
        )
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaisesRegex(
                CHECKER.RingHeadroomError, "changed while"
            ):
                CHECKER.check_status(payload)

    def test_rejects_head_over_capacity(self):
        with self.assertRaisesRegex(CHECKER.RingHeadroomError, "exceeds capacity"):
            CHECKER.check_status(status(capacity=8, head_before=9, head_after=9))

    def test_rejects_unknown_duplicate_and_multiline_status(self):
        malformed = (
            status().replace("capacity=65536", "capacity=65536 extra=1"),
            status() + status(),
            "NOUVEAU_DIAG_BAR2_RING_STATUS enabled=1 capacity=8 head=0 dropped=0\n",
        )
        for payload in malformed:
            with self.subTest(payload=payload), self.assertRaisesRegex(
                CHECKER.RingHeadroomError, "exactly one|unknown or malformed"
            ):
                CHECKER.check_status(payload)

    def test_rejects_negative_minimum(self):
        with self.assertRaisesRegex(CHECKER.RingHeadroomError, "cannot be negative"):
            CHECKER.check_status(status(), minimum_free=-1)


if __name__ == "__main__":
    unittest.main()
