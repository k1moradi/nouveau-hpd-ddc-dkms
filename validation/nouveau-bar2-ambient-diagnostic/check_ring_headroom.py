#!/usr/bin/env python3
"""Fail closed unless the ambient BAR2 ring has enough trigger headroom."""

from __future__ import annotations

import argparse
import json
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


def check_status(payload: str, minimum_free: int = 30_000) -> dict[str, Any]:
    if minimum_free < 0:
        raise RingHeadroomError("minimum free capacity cannot be negative")
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
