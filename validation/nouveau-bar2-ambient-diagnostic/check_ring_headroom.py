#!/usr/bin/env python3
"""Fail closed unless the ambient BAR2 ring has enough trigger headroom."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
from typing import Any


STATUS = re.compile(
    r"NOUVEAU_DIAG_BAR2_RING_STATUS enabled=(\d+) capacity=(\d+) "
    r"head_before=(\d+) head_after=(\d+) "
    r"dropped_before=(\d+) dropped_after=(\d+)"
)


class RingHeadroomError(ValueError):
    """The ring status is malformed or cannot safely admit the workload."""


def is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def check_status(payload: str, minimum_free: int = 30_000) -> dict[str, Any]:
    if type(minimum_free) is not int or minimum_free < 0:
        raise RingHeadroomError(
            "minimum free capacity must be a non-negative integer"
        )
    lines = [line.strip() for line in payload.splitlines() if line.strip()]
    if len(lines) != 1:
        raise RingHeadroomError("ring status must contain exactly one nonempty line")
    match = STATUS.fullmatch(lines[0])
    if match is None:
        raise RingHeadroomError("ring status has an unknown or malformed schema")
    enabled, capacity, head_before, head_after, dropped_before, dropped_after = (
        int(value) for value in match.groups()
    )
    if enabled != 1:
        raise RingHeadroomError("ambient BAR2 ring diagnostic is not enabled")
    if capacity <= 0:
        raise RingHeadroomError("ring capacity is zero")
    if head_before != head_after or dropped_before != dropped_after:
        raise RingHeadroomError("ring changed while the pre-trigger status was sampled")
    if head_after > capacity:
        raise RingHeadroomError("ring head exceeds capacity")
    if dropped_after != 0:
        raise RingHeadroomError("ring has already dropped events")
    free = capacity - head_after
    result = {
        "enabled": True,
        "capacity": capacity,
        "head": head_after,
        "dropped": dropped_after,
        "free": free,
        "minimum_free": minimum_free,
        "trigger_eligible": free >= minimum_free,
    }
    if not result["trigger_eligible"]:
        raise RingHeadroomError(
            f"ring has {free} free records; need at least {minimum_free}"
        )
    return result


def check_sampled_headroom(
    samples: list[tuple[float, str]],
    *,
    projection_seconds: float,
    expected_trigger_events: int,
    minimum_free: int = 30_000,
    safety_factor: float = 1.5,
    safety_margin: int = 5_000,
) -> dict[str, Any]:
    """Reserve trigger records plus projected ambient growth before admission."""
    if type(minimum_free) is not int or minimum_free < 0:
        raise RingHeadroomError(
            "minimum free capacity must be a non-negative integer"
        )
    if type(expected_trigger_events) is not int or expected_trigger_events <= 0:
        raise RingHeadroomError(
            "expected trigger event budget must be a positive integer"
        )
    if len(samples) < 3:
        raise RingHeadroomError("at least three ring headroom samples are required")
    if (
        not is_finite_number(projection_seconds)
        or projection_seconds <= 0
    ):
        raise RingHeadroomError("headroom projection horizon must be positive")
    if (
        not is_finite_number(safety_factor)
        or safety_factor < 1
    ):
        raise RingHeadroomError("headroom safety factor must be at least one")
    if type(safety_margin) is not int or safety_margin < 0:
        raise RingHeadroomError(
            "headroom safety margin must be a non-negative integer"
        )

    parsed = [
        (timestamp, check_status(payload, minimum_free=0))
        for timestamp, payload in samples
    ]
    capacity = parsed[0][1]["capacity"]
    rates: list[float] = []
    for (time_a, status_a), (time_b, status_b) in zip(parsed, parsed[1:]):
        if not is_finite_number(time_a) or not is_finite_number(time_b):
            raise RingHeadroomError("headroom sample timestamps must be finite numbers")
        elapsed = time_b - time_a
        if elapsed <= 0:
            raise RingHeadroomError("headroom sample interval must be positive")
        if status_b["capacity"] != capacity:
            raise RingHeadroomError("ring capacity changed between headroom samples")
        event_delta = status_b["head"] - status_a["head"]
        if event_delta < 0:
            raise RingHeadroomError("ring head decreased between headroom samples")
        rates.append(event_delta / elapsed)

    first_time, first = parsed[0]
    last_time, latest = parsed[-1]
    rate = max(rates, default=0.0)
    rate_reserve = math.ceil(rate * projection_seconds * safety_factor)
    required_free = max(
        minimum_free,
        rate_reserve + expected_trigger_events + safety_margin,
    )
    free = latest["free"]
    result = {
        **latest,
        "first_head": first["head"],
        "last_head": latest["head"],
        "sample_count": len(parsed),
        "sample_window_seconds": last_time - first_time,
        "interval_event_rates": rates,
        "new_events_during_sample": latest["head"] - first["head"],
        "estimated_events_per_second": rate,
        "projection_seconds": projection_seconds,
        "safety_factor": safety_factor,
        "rate_reserve": rate_reserve,
        "expected_trigger_events": expected_trigger_events,
        "safety_margin": safety_margin,
        "required_free": required_free,
    }
    result["trigger_eligible"] = free >= required_free
    if not result["trigger_eligible"]:
        raise RingHeadroomError(
            f"ring has {free} free records; projected requirement is {required_free}"
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status-file", type=Path, required=True)
    parser.add_argument("--minimum-free", type=int, default=30_000)
    args = parser.parse_args()

    try:
        result = check_status(
            args.status_file.read_text(encoding="ascii"), args.minimum_free,
        )
    except (OSError, RingHeadroomError) as exc:
        print(json.dumps({"trigger_eligible": False, "reason": str(exc)}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
