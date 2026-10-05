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
    capacity: int = 131_072,
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
        self.assertEqual(result["free"], 121_072)

    def test_rejects_ring_below_minimum_headroom(self):
        with self.assertRaisesRegex(CHECKER.RingHeadroomError, "need at least"):
            CHECKER.check_status(status(head_before=101_073, head_after=101_073))

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
            status().replace("capacity=131072", "capacity=131072 extra=1"),
            status() + status(),
            "NOUVEAU_DIAG_BAR2_RING_STATUS enabled=1 capacity=8 head=0 dropped=0\n",
        )
        for payload in malformed:
            with self.subTest(payload=payload), self.assertRaisesRegex(
                CHECKER.RingHeadroomError, "exactly one|unknown or malformed"
            ):
                CHECKER.check_status(payload)

    def test_rejects_negative_minimum(self):
        with self.assertRaisesRegex(CHECKER.RingHeadroomError, "non-negative integer"):
            CHECKER.check_status(status(), minimum_free=-1)

    def test_rejects_non_integer_minimum_free_values(self):
        for minimum_free in (True, 1.5, "30000"):
            with self.subTest(minimum_free=minimum_free), self.assertRaisesRegex(
                CHECKER.RingHeadroomError, "non-negative integer"
            ):
                CHECKER.check_status(status(), minimum_free=minimum_free)

    def test_sampled_headroom_projects_observed_ambient_rate(self):
        result = CHECKER.check_sampled_headroom(
            [
                (0.0, status(head_before=10_000, head_after=10_000)),
                (1.0, status(head_before=10_020, head_after=10_020)),
                (2.0, status(head_before=10_120, head_after=10_120)),
            ],
            projection_seconds=60,
            expected_trigger_events=2_000,
            minimum_free=1_000,
            safety_margin=50,
        )
        self.assertEqual(result["estimated_events_per_second"], 100)
        self.assertEqual(result["interval_event_rates"], [20, 100])
        self.assertEqual(result["rate_reserve"], 9_000)
        self.assertEqual(result["required_free"], 11_050)
        self.assertEqual(result["expected_trigger_events"], 2_000)
        self.assertTrue(result["trigger_eligible"])

    def test_sampled_headroom_rejects_when_rate_will_consume_remaining_capacity(self):
        with self.assertRaisesRegex(
            CHECKER.RingHeadroomError, "projected requirement",
        ):
            CHECKER.check_sampled_headroom(
                [
                    (0.0, status(head_before=35_000, head_after=35_000)),
                    (1.0, status(head_before=35_500, head_after=35_500)),
                    (2.0, status(head_before=36_000, head_after=36_000)),
                ],
                projection_seconds=60,
                expected_trigger_events=60_000,
                minimum_free=1_000,
                safety_margin=0,
            )

    def test_sampled_headroom_rejects_dropped_events_or_invalid_clock(self):
        with self.assertRaisesRegex(CHECKER.RingHeadroomError, "already dropped"):
            CHECKER.check_sampled_headroom(
                [
                    (0.0, status()),
                    (1.0, status(dropped_before=1, dropped_after=1)),
                    (2.0, status()),
                ],
                projection_seconds=60,
                expected_trigger_events=100,
            )
        with self.assertRaisesRegex(CHECKER.RingHeadroomError, "interval"):
            CHECKER.check_sampled_headroom(
                [(0.0, status()), (0.0, status()), (1.0, status())],
                projection_seconds=60,
                expected_trigger_events=100,
            )

    def test_startup_and_shutdown_budget_can_make_projection_fail_closed(self):
        samples = [
            (0.0, status(capacity=65_536, head_before=30_000, head_after=30_000)),
            (1.0, status(capacity=65_536, head_before=30_250, head_after=30_250)),
            (2.0, status(capacity=65_536, head_before=30_500, head_after=30_500)),
        ]
        short = CHECKER.check_sampled_headroom(
            samples,
            projection_seconds=80,
            expected_trigger_events=1_000,
            minimum_free=30_000,
            safety_margin=0,
        )
        self.assertTrue(short["trigger_eligible"])
        with self.assertRaisesRegex(CHECKER.RingHeadroomError, "projected requirement"):
            CHECKER.check_sampled_headroom(
                samples,
                projection_seconds=100,
                expected_trigger_events=1_000,
                minimum_free=30_000,
                safety_margin=0,
            )

    def test_explicit_trigger_budget_is_included_in_required_capacity(self):
        samples = [
            (0.0, status(head_before=10_000, head_after=10_000)),
            (1.0, status(head_before=10_010, head_after=10_010)),
            (2.0, status(head_before=10_020, head_after=10_020)),
        ]
        with self.assertRaisesRegex(CHECKER.RingHeadroomError, "projected requirement"):
            CHECKER.check_sampled_headroom(
                samples,
                projection_seconds=10,
                expected_trigger_events=125_000,
                minimum_free=30_000,
                safety_factor=1.0,
                safety_margin=0,
            )

    def test_rejects_invalid_expected_trigger_budget(self):
        samples = [
            (0.0, status()),
            (1.0, status()),
            (2.0, status()),
        ]
        for budget in (0, -1, 1.5, True):
            with self.subTest(budget=budget), self.assertRaisesRegex(
                CHECKER.RingHeadroomError, "positive integer"
            ):
                CHECKER.check_sampled_headroom(
                    samples,
                    projection_seconds=10,
                    expected_trigger_events=budget,
                )

    def test_rejects_non_integer_ring_budgets(self):
        samples = [
            (0.0, status()),
            (1.0, status()),
            (2.0, status()),
        ]
        for minimum_free in (True, 1.5, "30000"):
            with self.subTest(minimum_free=minimum_free), self.assertRaisesRegex(
                CHECKER.RingHeadroomError, "minimum free capacity.*integer"
            ):
                CHECKER.check_sampled_headroom(
                    samples,
                    projection_seconds=10,
                    expected_trigger_events=100,
                    minimum_free=minimum_free,
                )
        for safety_margin in (True, 1.5, "5000"):
            with self.subTest(safety_margin=safety_margin), self.assertRaisesRegex(
                CHECKER.RingHeadroomError, "safety margin.*integer"
            ):
                CHECKER.check_sampled_headroom(
                    samples,
                    projection_seconds=10,
                    expected_trigger_events=100,
                    safety_margin=safety_margin,
                )

    def test_rejects_boolean_or_string_projection_policies(self):
        samples = [
            (0.0, status()),
            (1.0, status()),
            (2.0, status()),
        ]
        for projection_seconds in (True, "10", float("nan"), float("inf")):
            with self.subTest(projection_seconds=projection_seconds), self.assertRaisesRegex(
                CHECKER.RingHeadroomError, "projection horizon"
            ):
                CHECKER.check_sampled_headroom(
                    samples,
                    projection_seconds=projection_seconds,
                    expected_trigger_events=100,
                )
        for safety_factor in (True, "1.5", float("nan"), float("inf")):
            with self.subTest(safety_factor=safety_factor), self.assertRaisesRegex(
                CHECKER.RingHeadroomError, "safety factor"
            ):
                CHECKER.check_sampled_headroom(
                    samples,
                    projection_seconds=10,
                    expected_trigger_events=100,
                    safety_factor=safety_factor,
                )

    def test_required_free_boundary_is_inclusive(self):
        admitted_samples = [
            (0.0, status(capacity=1_000, head_before=900, head_after=900)),
            (1.0, status(capacity=1_000, head_before=900, head_after=900)),
            (2.0, status(capacity=1_000, head_before=900, head_after=900)),
        ]
        result = CHECKER.check_sampled_headroom(
            admitted_samples,
            projection_seconds=10,
            expected_trigger_events=100,
            minimum_free=0,
            safety_factor=1.0,
            safety_margin=0,
        )
        self.assertEqual(result["free"], 100)
        self.assertEqual(result["required_free"], 100)

        refused_samples = [
            (0.0, status(capacity=1_000, head_before=901, head_after=901)),
            (1.0, status(capacity=1_000, head_before=901, head_after=901)),
            (2.0, status(capacity=1_000, head_before=901, head_after=901)),
        ]
        with self.assertRaisesRegex(
            CHECKER.RingHeadroomError, "projected requirement"
        ):
            CHECKER.check_sampled_headroom(
                refused_samples,
                projection_seconds=10,
                expected_trigger_events=100,
                minimum_free=0,
                safety_factor=1.0,
                safety_margin=0,
            )


if __name__ == "__main__":
    unittest.main()
