#!/usr/bin/env python3
"""Correlate saved BAR2 fault/VMA journal records by numeric address only.

Input is current-boot journal JSONL (for example, `journalctl -k -b -o json`).
The report is diagnostic correlation, never proof of object identity or cause.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import sys
from typing import Iterable


VMA_MARKER = "NOUVEAU_DIAG_V3_VMA "
FAULT_MARKER = "NOUVEAU_DIAG_BAR2_FAULT "
FIELD = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)")
ACTIVE_STAGES = {
    "kmap_acquired",
    "nested_acquired",
    "nested_ref_remaining",
    "reacquired",
    "verified",
}
RELEASE_STAGES = {"released", "destroying", "destroyed"}
CONTIGUOUS_ACTIVE_TRANSITIONS = {
    ("kmap_acquired", "nested_acquired"),
    ("nested_acquired", "nested_ref_remaining"),
    ("reacquired", "verified"),
}
U64_LIMIT = 1 << 64


class CorrelationInputError(ValueError):
    """The journal does not satisfy the pinned diagnostic record contract."""


@dataclass(frozen=True)
class VmaEvent:
    boot_id: str
    monotonic_usec: int
    sequence: int
    test_id: int
    stage: str
    source: str
    start: int | None
    size: int | None
    lookup_rc: int


@dataclass(frozen=True)
class FaultEvent:
    boot_id: str
    monotonic_usec: int
    sequence: int
    unit: int
    instance: int
    address: int
    fault_type: int


@dataclass(frozen=True)
class Segment:
    test_id: int
    start: int | None
    size: int | None
    start_usec: int
    end_usec: int | None
    kind: str
    stage_start: str
    stage_end: str | None


def _number(value: str, label: str, *, base: int = 10) -> int:
    try:
        result = int(value, base)
    except ValueError as exc:
        raise CorrelationInputError(f"invalid {label}: {value!r}") from exc
    if result < 0:
        raise CorrelationInputError(f"negative {label}: {value!r}")
    return result


def _signed_decimal(value: str, label: str) -> int:
    try:
        return int(value, 10)
    except ValueError as exc:
        raise CorrelationInputError(f"invalid {label}: {value!r}") from exc


def _required_journal_metadata(record: dict[str, object], line_no: int) -> tuple[str, int]:
    transport = record.get("_TRANSPORT")
    boot_id = record.get("_BOOT_ID")
    timestamp = record.get("__MONOTONIC_TIMESTAMP")
    if transport != "kernel":
        raise CorrelationInputError(
            f"line {line_no}: relevant diagnostic record is not kernel transport"
        )
    if not isinstance(boot_id, str) or not boot_id:
        raise CorrelationInputError(f"line {line_no}: relevant record has no _BOOT_ID")
    if not isinstance(timestamp, (str, int)) or not str(timestamp).isdigit():
        raise CorrelationInputError(
            f"line {line_no}: relevant record has no monotonic timestamp"
        )
    normalized_boot = boot_id.replace("-", "").lower()
    if not re.fullmatch(r"[0-9a-f]{32}", normalized_boot):
        raise CorrelationInputError(f"line {line_no}: malformed _BOOT_ID")
    return normalized_boot, int(timestamp)


def _fields(message: str, marker: str, line_no: int) -> dict[str, str]:
    payload = message.split(marker, 1)[1].strip()
    fields: dict[str, str] = {}
    for token in payload.split():
        match = FIELD.fullmatch(token)
        if not match:
            raise CorrelationInputError(
                f"line {line_no}: malformed diagnostic field {token!r}"
            )
        key, value = match.groups()
        if key in fields:
            raise CorrelationInputError(
                f"line {line_no}: duplicate diagnostic field {key!r}"
            )
        fields[key] = value
    return fields


def parse_records(lines: Iterable[str]) -> tuple[list[VmaEvent], list[FaultEvent]]:
    vmas: list[VmaEvent] = []
    faults: list[FaultEvent] = []
    sequence = 0
    for line_no, raw in enumerate(lines, 1):
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CorrelationInputError(f"line {line_no}: invalid journal JSON: {exc}") from exc
        if not isinstance(record, dict):
            raise CorrelationInputError(f"line {line_no}: journal JSON is not an object")
        message = record.get("MESSAGE")
        if not isinstance(message, str):
            continue
        marker = VMA_MARKER if VMA_MARKER in message else (
            FAULT_MARKER if FAULT_MARKER in message else None
        )
        if marker is None:
            continue
        boot_id, timestamp = _required_journal_metadata(record, line_no)
        fields = _fields(message, marker, line_no)
        sequence += 1

        if marker == VMA_MARKER:
            required = {"vmm", "id", "stage", "src", "va", "len", "rc"}
            missing = required - fields.keys()
            if missing:
                raise CorrelationInputError(
                    f"line {line_no}: VMA record missing {sorted(missing)}"
                )
            if fields["vmm"] != "BAR2":
                raise CorrelationInputError(
                    f"line {line_no}: unexpected VMA domain {fields['vmm']!r}"
                )
            source = fields["src"]
            if source not in {"current", "last_observed", "unavailable"}:
                raise CorrelationInputError(
                    f"line {line_no}: unknown VMA source {source!r}"
                )
            stage = fields["stage"]
            known_stages = ACTIVE_STAGES | RELEASE_STAGES | {"allocated"}
            if stage not in known_stages:
                raise CorrelationInputError(
                    f"line {line_no}: unknown VMA stage {stage!r}"
                )
            start = _number(fields["va"], "VMA start", base=16)
            size = _number(fields["len"], "VMA size", base=16)
            lookup_rc = _signed_decimal(fields["rc"], "VMA lookup result")
            if source == "current":
                if stage not in ACTIVE_STAGES:
                    raise CorrelationInputError(
                        f"line {line_no}: current VMA has non-mapped stage "
                        f"{stage!r}"
                    )
                if lookup_rc != 0 or size == 0:
                    raise CorrelationInputError(
                        f"line {line_no}: current VMA requires rc=0 and len>0"
                    )
                if start + size > U64_LIMIT:
                    raise CorrelationInputError(
                        f"line {line_no}: VMA range overflows 64-bit address space"
                    )
            else:
                # A cached/unavailable record is never treated as a current mapping.
                start = None
                size = None
            vmas.append(
                VmaEvent(
                    boot_id=boot_id,
                    monotonic_usec=timestamp,
                    sequence=sequence,
                    test_id=_number(fields["id"], "test ID"),
                    stage=stage,
                    source=source,
                    start=start,
                    size=size,
                    lookup_rc=lookup_rc,
                )
            )
        else:
            required = {"unit", "inst", "valo", "vahi", "type"}
            missing = required - fields.keys()
            if missing:
                raise CorrelationInputError(
                    f"line {line_no}: BAR2 fault record missing {sorted(missing)}"
                )
            unit = _number(fields["unit"], "fault unit", base=16)
            if unit != 0x05:
                raise CorrelationInputError(
                    f"line {line_no}: BAR2 fault marker has non-BAR2 unit {unit:#x}"
                )
            valo = _number(fields["valo"], "fault VALO", base=16)
            vahi = _number(fields["vahi"], "fault VAHI", base=16)
            if valo >= (1 << 32) or vahi >= (1 << 32):
                raise CorrelationInputError(f"line {line_no}: fault VA register exceeds 32 bits")
            faults.append(
                FaultEvent(
                    boot_id=boot_id,
                    monotonic_usec=timestamp,
                    sequence=sequence,
                    unit=unit,
                    instance=_number(fields["inst"], "fault instance", base=16) << 12,
                    address=(vahi << 32) | valo,
                    fault_type=_number(fields["type"], "fault type", base=16),
                )
            )
    return vmas, faults


def _segments(events: list[VmaEvent]) -> tuple[list[Segment], dict[int, bool]]:
    by_test: dict[int, list[VmaEvent]] = {}
    for event in events:
        by_test.setdefault(event.test_id, []).append(event)

    segments: list[Segment] = []
    destroyed: dict[int, bool] = {}
    for test_id, test_events in by_test.items():
        test_events.sort(key=lambda event: (event.monotonic_usec, event.sequence))
        current: VmaEvent | None = None
        unavailable_since: VmaEvent | None = None
        ended = False
        for event in test_events:
            if event.stage in ACTIVE_STAGES:
                if event.source == "current":
                    if unavailable_since is not None:
                        segments.append(
                            Segment(
                                test_id=test_id,
                                start=None,
                                size=None,
                                start_usec=unavailable_since.monotonic_usec,
                                end_usec=event.monotonic_usec,
                                kind="unavailable",
                                stage_start=unavailable_since.stage,
                                stage_end=event.stage,
                            )
                        )
                        unavailable_since = None
                    if current is not None:
                        if (current.stage, event.stage) not in CONTIGUOUS_ACTIVE_TRANSITIONS:
                            # In particular, nested_ref_remaining -> reacquired
                            # must have a logged fully-released boundary between
                            # them; otherwise the unpinned interval is unknown.
                            segments.append(
                                Segment(
                                    test_id=test_id,
                                    start=None,
                                    size=None,
                                    start_usec=current.monotonic_usec,
                                    end_usec=event.monotonic_usec,
                                    kind="lifecycle_gap",
                                    stage_start=current.stage,
                                    stage_end=event.stage,
                                )
                            )
                            current = event
                            ended = False
                            continue
                        if (current.start, current.size) != (event.start, event.size):
                            # A map reference should keep the BAR2 VMA stable.
                            # If observed endpoints disagree, do not assign the
                            # intervening period to either numeric range.
                            segments.append(
                                Segment(
                                    test_id=test_id,
                                    start=None,
                                    size=None,
                                    start_usec=current.monotonic_usec,
                                    end_usec=event.monotonic_usec,
                                    kind="vma_changed",
                                    stage_start=current.stage,
                                    stage_end=event.stage,
                                )
                            )
                            current = event
                            ended = False
                            continue
                        segments.append(
                            Segment(
                                test_id=test_id,
                                start=current.start,
                                size=current.size,
                                start_usec=current.monotonic_usec,
                                end_usec=event.monotonic_usec,
                                kind="active",
                                stage_start=current.stage,
                                stage_end=event.stage,
                            )
                        )
                    current = event
                    ended = False
                else:
                    if current is not None:
                        segments.append(
                            Segment(
                                test_id=test_id,
                                start=current.start,
                                size=current.size,
                                start_usec=current.monotonic_usec,
                                end_usec=event.monotonic_usec,
                                kind="active",
                                stage_start=current.stage,
                                stage_end=event.stage,
                            )
                        )
                        current = None
                    if unavailable_since is not None:
                        segments.append(
                            Segment(
                                test_id=test_id,
                                start=None,
                                size=None,
                                start_usec=unavailable_since.monotonic_usec,
                                end_usec=event.monotonic_usec,
                                kind="unavailable",
                                stage_start=unavailable_since.stage,
                                stage_end=event.stage,
                            )
                        )
                    # This is an active-map lifecycle stage, but the snapshot
                    # did not prove a current range. Never extend an older
                    # cached address through this interval.
                    unavailable_since = event
                    ended = False
            elif event.stage in RELEASE_STAGES:
                if current is not None:
                    segments.append(
                        Segment(
                            test_id=test_id,
                            start=current.start,
                            size=current.size,
                            start_usec=current.monotonic_usec,
                            end_usec=event.monotonic_usec,
                            kind="release_transition",
                            stage_start=current.stage,
                            stage_end=event.stage,
                        )
                    )
                    current = None
                if unavailable_since is not None:
                    segments.append(
                        Segment(
                            test_id=test_id,
                            start=None,
                            size=None,
                            start_usec=unavailable_since.monotonic_usec,
                            end_usec=event.monotonic_usec,
                            kind="unavailable",
                            stage_start=unavailable_since.stage,
                            stage_end=event.stage,
                        )
                    )
                    unavailable_since = None
                if event.stage == "destroyed":
                    ended = True
        if current is not None:
            segments.append(
                Segment(
                    test_id=test_id,
                    start=current.start,
                    size=current.size,
                    start_usec=current.monotonic_usec,
                    end_usec=None,
                    kind="open",
                    stage_start=current.stage,
                    stage_end=None,
                )
            )
            ended = False
        elif unavailable_since is not None:
            segments.append(
                Segment(
                    test_id=test_id,
                    start=None,
                    size=None,
                    start_usec=unavailable_since.monotonic_usec,
                    end_usec=None,
                    kind="unavailable_open",
                    stage_start=unavailable_since.stage,
                    stage_end=None,
                )
            )
            ended = False
        destroyed[test_id] = ended
    return segments, destroyed


def correlate(lines: Iterable[str]) -> dict[str, object]:
    vmas, faults = parse_records(lines)
    relevant = [*vmas, *faults]
    boots = {item.boot_id for item in relevant}
    if len(boots) > 1:
        return {
            "outcome": "INCONCLUSIVE_MIXED_BOOT_IDS",
            "boot_ids": sorted(boots),
            "numeric_address_correlation_only": True,
            "pointer_identity_used": False,
            "fault_count": len(faults),
            "vma_record_count": len(vmas),
            "faults": [],
        }
    if not relevant:
        return {
            "outcome": "INCONCLUSIVE_NO_DIAGNOSTIC_RECORDS",
            "numeric_address_correlation_only": True,
            "faults": [],
        }

    segments, destroyed = _segments(vmas)
    results: list[dict[str, object]] = []
    for fault in sorted(faults, key=lambda event: (event.monotonic_usec, event.sequence)):
        matching: list[Segment] = []
        transition: list[Segment] = []
        open_segments: list[Segment] = []
        unavailable: list[Segment] = []
        changed: list[Segment] = []
        boundary = False
        for segment in segments:
            end = segment.end_usec
            in_time = fault.monotonic_usec > segment.start_usec and (
                end is None or fault.monotonic_usec < end
            )
            if segment.kind in {
                "unavailable", "unavailable_open", "vma_changed", "lifecycle_gap"
            }:
                if fault.monotonic_usec == segment.start_usec or (
                    end is not None and fault.monotonic_usec == end
                ):
                    boundary = True
                elif in_time:
                    if segment.kind == "vma_changed":
                        changed.append(segment)
                    elif segment.kind == "lifecycle_gap":
                        changed.append(segment)
                    else:
                        unavailable.append(segment)
                continue
            if segment.start is None or segment.size is None:
                continue
            if not (segment.start <= fault.address < segment.start + segment.size):
                continue
            if fault.monotonic_usec == segment.start_usec or (
                end is not None and fault.monotonic_usec == end
            ):
                boundary = True
                continue
            if not in_time:
                continue
            if segment.kind == "active":
                matching.append(segment)
            elif segment.kind == "open":
                open_segments.append(segment)
            else:
                transition.append(segment)

        if boundary:
            outcome = "INCONCLUSIVE_EQUAL_TIMESTAMP_ORDER"
            candidates: list[dict[str, object]] = []
        elif open_segments:
            outcome = "INCONCLUSIVE_TEST_LIFECYCLE_INCOMPLETE"
            candidates = [asdict(item) for item in open_segments]
        elif unavailable:
            if any(not destroyed.get(item.test_id, False) for item in unavailable):
                outcome = "INCONCLUSIVE_TEST_LIFECYCLE_INCOMPLETE"
            else:
                outcome = "INCONCLUSIVE_ACTIVE_VMA_UNAVAILABLE"
            candidates = [asdict(item) for item in unavailable]
        elif changed:
            if any(item.kind == "lifecycle_gap" for item in changed):
                outcome = "INCONCLUSIVE_VMA_LIFECYCLE_GAP"
            else:
                outcome = "INCONCLUSIVE_VMA_CHANGED_DURING_ACTIVE_MAP"
            candidates = [asdict(item) for item in changed]
        elif transition:
            outcome = "INCONCLUSIVE_RELEASE_TRANSITION_WINDOW"
            candidates = [asdict(item) for item in transition]
        elif len(matching) > 1:
            outcome = "AMBIGUOUS_MULTIPLE_NUMERIC_VMA_CANDIDATES"
            candidates = [asdict(item) for item in matching]
        elif len(matching) == 1:
            item = matching[0]
            if not destroyed.get(item.test_id, False):
                outcome = "INCONCLUSIVE_TEST_LIFECYCLE_INCOMPLETE"
                candidates = [asdict(item)]
            else:
                outcome = "ONE_NUMERIC_ACTIVE_VMA_CANDIDATE"
                candidates = [asdict(item)]
        else:
            outcome = "NO_ACTIVE_V3_VMA_MATCH"
            candidates = []

        results.append(
            {
                "monotonic_usec": fault.monotonic_usec,
                "fault_address": f"0x{fault.address:x}",
                "fault_instance_address": f"0x{fault.instance:x}",
                "fault_type": f"0x{fault.fault_type:x}",
                "outcome": outcome,
                "candidates": candidates,
            }
        )

    return {
        "outcome": "CORRELATION_REPORT_ONLY",
        "boot_id": next(iter(boots)),
        "numeric_address_correlation_only": True,
        "pointer_identity_used": False,
        "fault_count": len(faults),
        "vma_record_count": len(vmas),
        "faults": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("journal_jsonl", type=Path)
    args = parser.parse_args(argv)
    try:
        with args.journal_jsonl.open(encoding="utf-8") as journal_file:
            result = correlate(journal_file)
    except (OSError, UnicodeError, CorrelationInputError) as exc:
        print(f"VMA_CORRELATION_ERROR {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    if result.get("outcome") == "INCONCLUSIVE_MIXED_BOOT_IDS":
        return 4
    return 0 if int(result.get("fault_count", 0)) > 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
