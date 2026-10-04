#!/usr/bin/env python3
"""Offline numeric correlation for ambient Nouveau BAR2 instmem lifetimes."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

U64_MAX = (1 << 64) - 1
MAP_TAG = "NOUVEAU_DIAG_BAR2_MAP "
RESET_TAG = "NOUVEAU_DIAG_BAR2_RESET "
FAULT_TAG = "NOUVEAU_DIAG_BAR2_FAULT "
MAP_FIELDS = {
    "seq", "id", "stage", "attempt", "vmm", "mem_addr", "mem_len",
    "bar2_va", "bar2_len", "range", "access", "refs", "pid", "comm", "rc",
}
RESET_FIELDS = {"seq", "reset_id", "stage"}
MAP_STAGES = {
    "ALLOCATED", "MAP_BEGIN", "MAP_VMA_RESERVED", "MAP_PTE_READY",
    "MAP_READY", "MAP_FAILED", "MAP_DISCARDED", "MAP_ROLLBACK_BEGIN",
    "MAP_ROLLBACK_DONE", "FIRST_MAP_ACTIVE", "LAST_MAP_RELEASE",
    "BOOT_MAP_PINNED", "EVICTING", "EVICTED", "DESTROYING", "DESTROYED",
}
RANGE_STATES = {"none", "reserved", "current", "cached", "released", "unknown"}
ACCESS_STATES = {"none", "bar0", "bar2", "unknown"}

RAW_FAULT_FIELDS = {"unit", "inst", "valo", "vahi", "type"}
DECODED_FAULT_RE = re.compile(
    r"fifo: fault (?P<access>[0-9a-fA-F]{2}) \[(?P<access_name>[^]]*)\] "
    r"at (?P<va>[0-9a-fA-F]{16}) engine 05 \[BAR2\] "
    r"client 07 \[HUB/HOST_CPU\] reason 02 \[PTE\] "
    r"on channel (?P<channel>-?\d+) "
    r"\[(?P<inst>[0-9a-fA-F]+) (?P<channel_name>[^]]+)\]"
)


class CorrelationInputError(ValueError):
    """The journal or diagnostic records violate the pinned schema."""


class _DuplicateJsonKey(ValueError):
    pass


def _object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _boot_id(value: Any, line: int) -> str:
    if not isinstance(value, str):
        raise CorrelationInputError(f"line {line}: _BOOT_ID must be a string")
    normalized = value.replace("-", "").lower()
    if not re.fullmatch(r"[0-9a-f]{32}", normalized):
        raise CorrelationInputError(f"line {line}: malformed _BOOT_ID")
    return normalized


def _uint(value: str, field: str, line: int, *, base: int = 10, maximum: int = U64_MAX) -> int:
    try:
        number = int(value, base)
    except (TypeError, ValueError) as exc:
        raise CorrelationInputError(f"line {line}: invalid {field}") from exc
    if number < 0 or number > maximum:
        raise CorrelationInputError(f"line {line}: {field} out of range")
    return number


def _signed(value: str, field: str, line: int, low: int, high: int) -> int:
    try:
        number = int(value, 10)
    except (TypeError, ValueError) as exc:
        raise CorrelationInputError(f"line {line}: invalid {field}") from exc
    if number < low or number > high:
        raise CorrelationInputError(f"line {line}: {field} out of range")
    return number


def _fields(text: str, expected: set[str], line: int, kind: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for item in text.split():
        if item.count("=") != 1:
            raise CorrelationInputError(f"line {line}: malformed {kind} field")
        key, value = item.split("=", 1)
        if not key or not value:
            raise CorrelationInputError(f"line {line}: empty {kind} field")
        if key in fields:
            raise CorrelationInputError(f"line {line}: duplicate {kind} field {key}")
        fields[key] = value
    missing = expected - fields.keys()
    extra = fields.keys() - expected
    if missing or extra:
        raise CorrelationInputError(
            f"line {line}: {kind} fields mismatch missing={sorted(missing)} extra={sorted(extra)}"
        )
    return fields


def _range(values: dict[str, str], line: int) -> tuple[int, int, str]:
    start = _uint(values["bar2_va"], "bar2_va", line, base=0)
    length = _uint(values["bar2_len"], "bar2_len", line, base=0)
    state = values["range"]
    if state not in RANGE_STATES:
        raise CorrelationInputError(f"line {line}: unknown range state {state}")
    if state == "none":
        if start or length:
            raise CorrelationInputError(f"line {line}: range=none requires zero va and length")
    elif state == "unknown":
        if length and start > U64_MAX - length:
            raise CorrelationInputError(f"line {line}: unknown BAR2 range overflows")
    else:
        if length == 0:
            raise CorrelationInputError(f"line {line}: BAR2 range length must be nonzero")
        if start > U64_MAX - length:
            raise CorrelationInputError(f"line {line}: BAR2 range overflows")
    return start, length, state


@dataclass(frozen=True)
class JournalRow:
    line: int
    boot_id: str
    mono_usec: int
    real_usec: int | None
    message: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class MapEvent:
    line: int
    boot_id: str
    mono_usec: int
    seq: int
    allocation_id: int
    stage: str
    attempt: int
    mem_addr: int
    mem_len: int
    bar2_va: int
    bar2_len: int
    range_state: str
    access: str
    refs: int
    pid: int
    comm: str
    rc: int

    def contains(self, address: int) -> bool:
        return self.bar2_len > 0 and self.bar2_va <= address < self.bar2_va + self.bar2_len


@dataclass(frozen=True)
class ResetEvent:
    line: int
    boot_id: str
    mono_usec: int
    seq: int
    reset_id: int
    stage: str


@dataclass(frozen=True)
class FaultEvent:
    raw_line: int
    decoded_line: int
    boot_id: str
    raw_mono_usec: int
    mono_usec: int
    raw_real_usec: int | None
    real_usec: int | None
    unit: int
    inst_register: int
    valo: int
    vahi: int
    fault_type: int
    va: int
    inst: int
    access: str
    engine: str
    client: str
    reason: str
    channel: int
    channel_name: str

    def as_json(self) -> dict[str, Any]:
        return {
            "raw_journal_line": self.raw_line,
            "decoded_journal_line": self.decoded_line,
            "raw_monotonic_usec": self.raw_mono_usec,
            "fault_monotonic_usec": self.mono_usec,
            "raw_realtime_usec": self.raw_real_usec,
            "fault_realtime_usec": self.real_usec,
            "raw_tuple": {
                "unit": f"0x{self.unit:02x}",
                "inst_register": f"0x{self.inst_register:08x}",
                "valo": f"0x{self.valo:08x}",
                "vahi": f"0x{self.vahi:08x}",
                "type": f"0x{self.fault_type:08x}",
            },
            "fault_va": f"0x{self.va:x}",
            "inst": f"0x{self.inst:010x}",
            "access": self.access,
            "engine": self.engine,
            "client": self.client,
            "reason": self.reason,
            "channel": self.channel,
            "channel_name": self.channel_name,
        }


@dataclass
class ParsedInput:
    boot_id: str
    rows: list[JournalRow]
    maps: list[MapEvent]
    resets: list[ResetEvent]
    faults: list[FaultEvent]


def parse_journal(path: Path) -> ParsedInput:
    rows: list[JournalRow] = []
    map_events: list[MapEvent] = []
    reset_events: list[ResetEvent] = []
    raw_faults: list[tuple[JournalRow, tuple[int, int, int, int, int]]] = []
    decoded_faults: list[tuple[JournalRow, re.Match[str]]] = []
    boots: set[str] = set()
    diagnostic_sequences: set[int] = set()

    try:
        stream = path.open("r", encoding="utf-8")
    except OSError as exc:
        raise CorrelationInputError(f"cannot open journal: {exc}") from exc

    with stream:
        for line_number, text in enumerate(stream, 1):
            if not text.strip():
                raise CorrelationInputError(f"line {line_number}: blank JSONL record")
            try:
                record = json.loads(text, object_pairs_hook=_object_no_duplicates)
            except _DuplicateJsonKey as exc:
                raise CorrelationInputError(f"line {line_number}: duplicate JSON key {exc}") from exc
            except json.JSONDecodeError as exc:
                raise CorrelationInputError(f"line {line_number}: malformed JSON: {exc}") from exc
            if not isinstance(record, dict):
                raise CorrelationInputError(f"line {line_number}: journal record is not an object")
            if "__MONOTONIC_TIMESTAMP" not in record:
                raise CorrelationInputError(
                    f"line {line_number}: missing __MONOTONIC_TIMESTAMP; refusing realtime fallback"
                )
            mono_text = record["__MONOTONIC_TIMESTAMP"]
            if not isinstance(mono_text, (str, int)) or isinstance(mono_text, bool):
                raise CorrelationInputError(f"line {line_number}: invalid monotonic timestamp")
            mono = _uint(str(mono_text), "monotonic timestamp", line_number)
            if "_BOOT_ID" not in record:
                raise CorrelationInputError(f"line {line_number}: missing _BOOT_ID")
            boot = _boot_id(record["_BOOT_ID"], line_number)
            boots.add(boot)
            if len(boots) > 1:
                raise CorrelationInputError("mixed boot IDs are not accepted")
            real: int | None = None
            if "__REALTIME_TIMESTAMP" in record:
                raw_real = record["__REALTIME_TIMESTAMP"]
                if not isinstance(raw_real, (str, int)) or isinstance(raw_real, bool):
                    raise CorrelationInputError(f"line {line_number}: invalid realtime timestamp")
                real = _uint(str(raw_real), "realtime timestamp", line_number)
            message = record.get("MESSAGE", "")
            if not isinstance(message, str):
                raise CorrelationInputError(f"line {line_number}: MESSAGE must be a string")
            metadata = {
                key: record[key]
                for key in (
                    "_TRANSPORT", "_BOOT_ID", "_PID", "_TID", "_COMM", "_EXE",
                    "_SYSTEMD_UNIT", "SYSTEMD_UNIT", "USER_UNIT", "SYSLOG_IDENTIFIER", "MESSAGE",
                    "__MONOTONIC_TIMESTAMP", "__REALTIME_TIMESTAMP",
                )
                if key in record
            }
            row = JournalRow(line_number, boot, mono, real, message, metadata)
            rows.append(row)

            if MAP_TAG in message:
                if message.count(MAP_TAG) != 1:
                    raise CorrelationInputError(f"line {line_number}: repeated BAR2 map tag")
                fields = _fields(message.split(MAP_TAG, 1)[1], MAP_FIELDS, line_number, "BAR2 map")
                seq = _uint(fields["seq"], "sequence", line_number, maximum=U64_MAX)
                allocation_id = _uint(fields["id"], "allocation id", line_number)
                attempt = _uint(fields["attempt"], "attempt id", line_number)
                mem_addr = _uint(fields["mem_addr"], "memory address", line_number, base=0)
                mem_len = _uint(fields["mem_len"], "memory length", line_number, base=0)
                bar2_va, bar2_len, range_state = _range(fields, line_number)
                refs = _uint(fields["refs"], "map refs", line_number, maximum=2**31 - 1)
                pid = _uint(fields["pid"], "pid", line_number, maximum=2**31 - 1)
                rc = _signed(fields["rc"], "lookup/map result", line_number, -4095, 4095)
                if fields["vmm"] != "BAR2":
                    raise CorrelationInputError(f"line {line_number}: VMM must be BAR2")
                if allocation_id == 0 or mem_len == 0:
                    raise CorrelationInputError(f"line {line_number}: allocation ID and memory length must be nonzero")
                if mem_addr > U64_MAX - mem_len:
                    raise CorrelationInputError(f"line {line_number}: backing memory range overflows")
                if seq == 0:
                    raise CorrelationInputError(f"line {line_number}: sequence must be nonzero")
                if fields["stage"] not in MAP_STAGES:
                    raise CorrelationInputError(f"line {line_number}: unknown BAR2 map stage")
                if fields["access"] not in ACCESS_STATES:
                    raise CorrelationInputError(f"line {line_number}: invalid access mode")
                if not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", fields["comm"]):
                    raise CorrelationInputError(f"line {line_number}: malformed comm field")
                if seq in diagnostic_sequences:
                    raise CorrelationInputError(f"line {line_number}: duplicate diagnostic sequence")
                diagnostic_sequences.add(seq)
                map_events.append(MapEvent(
                    line_number, boot, mono, seq, allocation_id, fields["stage"], attempt,
                    mem_addr, mem_len, bar2_va, bar2_len, range_state, fields["access"],
                    refs, pid, fields["comm"], rc,
                ))

            if RESET_TAG in message:
                if message.count(RESET_TAG) != 1:
                    raise CorrelationInputError(f"line {line_number}: repeated BAR2 reset tag")
                fields = _fields(message.split(RESET_TAG, 1)[1], RESET_FIELDS, line_number, "BAR2 reset")
                seq = _uint(fields["seq"], "sequence", line_number)
                reset_id = _uint(fields["reset_id"], "reset ID", line_number)
                if seq == 0 or reset_id == 0 or fields["stage"] not in {"BEGIN", "END"}:
                    raise CorrelationInputError(f"line {line_number}: invalid BAR2 reset record")
                if seq in diagnostic_sequences:
                    raise CorrelationInputError(f"line {line_number}: duplicate diagnostic sequence")
                diagnostic_sequences.add(seq)
                reset_events.append(ResetEvent(line_number, boot, mono, seq, reset_id, fields["stage"]))

            if FAULT_TAG in message:
                if message.count(FAULT_TAG) != 1:
                    raise CorrelationInputError(f"line {line_number}: repeated BAR2 fault tag")
                fields = _fields(
                    message.split(FAULT_TAG, 1)[1], RAW_FAULT_FIELDS,
                    line_number, "raw BAR2 fault",
                )
                values = tuple(
                    _uint(fields[name], name, line_number, base=16, maximum=0xFFFFFFFF)
                    for name in ("unit", "inst", "valo", "vahi", "type")
                )
                unit, inst_reg, valo, vahi, fault_type = values
                if unit == 0x05 and ((fault_type & 0x1f) == 0x02) and (((fault_type >> 8) & 0x1f) == 0x07) and (fault_type & 0x40):
                    raw_faults.append((row, values))

            match = DECODED_FAULT_RE.search(message)
            if match:
                decoded_faults.append((row, match))

    if not rows:
        raise CorrelationInputError("empty journal")
    if len(boots) != 1:
        raise CorrelationInputError("journal must contain exactly one boot ID")
    if not map_events and any(MAP_TAG in row.message for row in rows):
        raise CorrelationInputError("BAR2 map marker parsing failed")

    faults: list[FaultEvent] = []
    used_raw: set[int] = set()
    for row, match in decoded_faults:
        access_code = int(match.group("access"), 16)
        access_name = match.group("access_name")
        va = int(match.group("va"), 16)
        inst = int(match.group("inst"), 16)
        channel = int(match.group("channel"), 10)
        channel_name = match.group("channel_name")
        candidates = []
        for raw_row, values in raw_faults:
            if raw_row.line in used_raw or raw_row.mono_usec > row.mono_usec:
                continue
            if row.mono_usec - raw_row.mono_usec > 10_000:
                continue
            unit, inst_reg, valo, vahi, fault_type = values
            raw_va = (vahi << 32) | valo
            raw_inst = inst_reg << 12
            if (raw_va == va and raw_inst == inst and unit == 0x05 and
                    ((fault_type >> 7) & 1) == access_code and
                    ((fault_type >> 6) & 1) == 1 and
                    ((fault_type >> 8) & 0x1f) == 0x07 and
                    (fault_type & 0x1f) == 0x02):
                candidates.append((raw_row, values))
        if len(candidates) != 1:
            raise CorrelationInputError(
                f"line {row.line}: expected one matching raw BAR2 tuple, found {len(candidates)}"
            )
        raw_row, values = candidates[0]
        used_raw.add(raw_row.line)
        unit, inst_reg, valo, vahi, fault_type = values
        faults.append(FaultEvent(
            raw_row.line, row.line, row.boot_id, raw_row.mono_usec, row.mono_usec,
            raw_row.real_usec, row.real_usec, unit, inst_reg, valo, vahi, fault_type,
            va, inst, access_name, "BAR2", "HUB/HOST_CPU", "PTE", channel, channel_name,
        ))
    if len(used_raw) != len(raw_faults):
        missing = sorted(set(row.line for row, _ in raw_faults) - used_raw)
        raise CorrelationInputError(f"unmatched raw BAR2 fault records at lines {missing[:8]}")

    _validate_global_sequences(map_events, reset_events)
    _validate_attempts(map_events)
    _validate_map_records(map_events)
    _validate_reset_records(reset_events)
    return ParsedInput(next(iter(boots)), rows, map_events, reset_events, faults)


def _validate_global_sequences(maps: list[MapEvent], resets: list[ResetEvent]) -> None:
    records = sorted([*maps, *resets], key=lambda item: item.seq)
    for previous, current in zip(records, records[1:]):
        if current.seq <= previous.seq:
            raise CorrelationInputError("diagnostic event sequence is not strictly increasing")
        if current.mono_usec < previous.mono_usec:
            raise CorrelationInputError(
                "diagnostic sequence order conflicts with journal monotonic time"
            )


def _validate_attempts(events: list[MapEvent]) -> None:
    by_attempt: dict[int, list[MapEvent]] = {}
    attempt_owner: dict[int, int] = {}
    for event in events:
        if event.stage not in {
            "MAP_BEGIN", "MAP_VMA_RESERVED", "MAP_PTE_READY", "MAP_READY",
            "MAP_FAILED", "MAP_DISCARDED", "MAP_ROLLBACK_BEGIN", "MAP_ROLLBACK_DONE",
        }:
            if event.attempt != 0:
                raise CorrelationInputError(f"line {event.line}: lifecycle event has a map attempt ID")
            continue
        if event.attempt == 0:
            raise CorrelationInputError(f"line {event.line}: map attempt ID must be nonzero")
        owner = attempt_owner.setdefault(event.attempt, event.allocation_id)
        if owner != event.allocation_id:
            raise CorrelationInputError(
                f"line {event.line}: map attempt ID {event.attempt} reused across allocations"
            )
        by_attempt.setdefault(event.attempt, []).append(event)
    for attempt, sequence in by_attempt.items():
        sequence.sort(key=lambda item: item.seq)
        if sequence[0].stage != "MAP_BEGIN":
            raise CorrelationInputError(f"line {sequence[0].line}: map attempt {attempt} has no MAP_BEGIN")
        stages = [event.stage for event in sequence]
        if len({event.allocation_id for event in sequence}) != 1:
            raise CorrelationInputError(f"map attempt {attempt} spans allocations")
        known_ranges = {(event.bar2_va, event.bar2_len) for event in sequence if event.bar2_len}
        if len(known_ranges) > 1:
            raise CorrelationInputError(f"map attempt {attempt} changes its BAR2 range")
        legal = {
            ("MAP_BEGIN", "MAP_FAILED"),
            ("MAP_BEGIN", "MAP_VMA_RESERVED", "MAP_FAILED", "MAP_ROLLBACK_BEGIN", "MAP_ROLLBACK_DONE"),
            ("MAP_BEGIN", "MAP_VMA_RESERVED", "MAP_PTE_READY", "MAP_FAILED", "MAP_ROLLBACK_BEGIN", "MAP_ROLLBACK_DONE"),
            ("MAP_BEGIN", "MAP_VMA_RESERVED", "MAP_PTE_READY", "MAP_DISCARDED", "MAP_ROLLBACK_BEGIN", "MAP_ROLLBACK_DONE"),
            ("MAP_BEGIN", "MAP_VMA_RESERVED", "MAP_PTE_READY", "MAP_READY"),
        }
        if tuple(stages) not in legal:
            # A still-open attempt is valid only when the supplied journal ends mid-transition.
            prefixes = [candidate[:len(stages)] for candidate in legal]
            if not any(tuple(stages) == prefix for prefix in prefixes):
                raise CorrelationInputError(f"line {sequence[-1].line}: impossible map-attempt stages {stages}")


def _validate_map_records(events: list[MapEvent]) -> None:
    by_id: dict[int, list[MapEvent]] = {}
    for event in events:
        by_id.setdefault(event.allocation_id, []).append(event)

    for allocation_id, records in by_id.items():
        records.sort(key=lambda item: item.seq)
        if records[0].stage != "ALLOCATED":
            raise CorrelationInputError(
                f"line {records[0].line}: allocation {allocation_id} does not begin with ALLOCATED"
            )
        if sum(event.stage == "ALLOCATED" for event in records) != 1:
            raise CorrelationInputError(f"allocation {allocation_id} has duplicate allocation records")
        if sum(event.stage == "DESTROYED" for event in records) > 1:
            raise CorrelationInputError(f"allocation {allocation_id} has duplicate DESTROYED records")

        allocated = records[0]
        if allocated.attempt or allocated.range_state != "none" or allocated.refs != 0:
            raise CorrelationInputError(f"line {allocated.line}: invalid ALLOCATED state")
        current_range: tuple[int, int] | None = None
        active = False
        destroying = False
        destroyed = False
        previous_time = allocated.mono_usec
        for event in records[1:]:
            if event.mono_usec < previous_time:
                raise CorrelationInputError(
                    f"line {event.line}: per-allocation sequence conflicts with monotonic timestamps"
                )
            previous_time = event.mono_usec
            if destroyed:
                raise CorrelationInputError(f"line {event.line}: map event occurs after DESTROYED")
            if event.stage == "ALLOCATED":
                raise CorrelationInputError(f"line {event.line}: duplicate ALLOCATED")
            if event.stage == "MAP_BEGIN":
                if destroying or event.refs != 0 or event.range_state != "none":
                    raise CorrelationInputError(f"line {event.line}: invalid MAP_BEGIN state")
            elif event.stage == "MAP_VMA_RESERVED":
                if event.range_state != "reserved" or event.refs != 0:
                    raise CorrelationInputError(f"line {event.line}: invalid VMA reservation state")
            elif event.stage == "MAP_PTE_READY":
                if event.range_state != "reserved" or event.refs != 0:
                    raise CorrelationInputError(f"line {event.line}: invalid PTE-ready state")
            elif event.stage == "MAP_READY":
                if event.range_state != "current" or event.refs != 0 or current_range is not None:
                    raise CorrelationInputError(f"line {event.line}: invalid MAP_READY lifecycle")
                current_range = (event.bar2_va, event.bar2_len)
            elif event.stage == "MAP_FAILED":
                if event.range_state not in {"none", "reserved"} or event.refs != 0:
                    raise CorrelationInputError(f"line {event.line}: invalid MAP_FAILED state")
            elif event.stage == "MAP_DISCARDED":
                if event.range_state != "reserved" or event.refs != 0:
                    raise CorrelationInputError(f"line {event.line}: invalid discarded-map state")
            elif event.stage in {"MAP_ROLLBACK_BEGIN", "MAP_ROLLBACK_DONE"}:
                if event.refs != 0 or event.range_state not in {"reserved", "released"}:
                    raise CorrelationInputError(f"line {event.line}: invalid map rollback state")
            elif event.stage == "FIRST_MAP_ACTIVE":
                if active or event.refs != 1 or event.access not in {"bar0", "bar2"}:
                    raise CorrelationInputError(f"line {event.line}: impossible 0-to-1 map transition")
                if event.access == "bar2":
                    if event.range_state != "current" or current_range != (event.bar2_va, event.bar2_len):
                        raise CorrelationInputError(f"line {event.line}: active BAR2 range is not the current VMA")
                elif event.range_state not in {"none", "cached", "current", "unknown"}:
                    raise CorrelationInputError(f"line {event.line}: BAR0 access has invalid range state")
                active = True
            elif event.stage == "LAST_MAP_RELEASE":
                if not active or event.refs != 0:
                    raise CorrelationInputError(f"line {event.line}: impossible 1-to-0 map transition")
                if event.range_state == "cached":
                    if current_range != (event.bar2_va, event.bar2_len):
                        raise CorrelationInputError(f"line {event.line}: cached range differs from current VMA")
                elif event.range_state not in {"none", "unknown"}:
                    raise CorrelationInputError(f"line {event.line}: invalid last-release range state")
                active = False
            elif event.stage == "BOOT_MAP_PINNED":
                if active or event.refs != 0 or event.range_state != "current":
                    raise CorrelationInputError(f"line {event.line}: invalid boot-pinned map state")
                if current_range != (event.bar2_va, event.bar2_len):
                    raise CorrelationInputError(f"line {event.line}: boot-pinned range differs from current VMA")
            elif event.stage == "EVICTING":
                if active or event.refs != 0 or event.range_state != "current":
                    raise CorrelationInputError(f"line {event.line}: eviction with active refs or no current range")
                if current_range != (event.bar2_va, event.bar2_len):
                    raise CorrelationInputError(f"line {event.line}: evicted range differs from current VMA")
            elif event.stage == "EVICTED":
                if active or event.refs != 0 or event.range_state != "released":
                    raise CorrelationInputError(f"line {event.line}: invalid EVICTED state")
                if current_range != (event.bar2_va, event.bar2_len):
                    raise CorrelationInputError(f"line {event.line}: EVICTED range differs from current VMA")
                current_range = None
            elif event.stage == "DESTROYING":
                if event.refs != 0 or active:
                    raise CorrelationInputError(f"line {event.line}: destroy with active map refs")
                if event.range_state == "current":
                    if current_range != (event.bar2_va, event.bar2_len):
                        raise CorrelationInputError(f"line {event.line}: destroy range differs from current VMA")
                elif event.range_state != "none":
                    raise CorrelationInputError(f"line {event.line}: invalid DESTROYING range state")
                destroying = True
            elif event.stage == "DESTROYED":
                if not destroying:
                    raise CorrelationInputError(f"line {event.line}: DESTROYED without DESTROYING")
                if event.refs != 0:
                    raise CorrelationInputError(f"line {event.line}: destroyed with active map refs")
                if event.range_state == "released":
                    if current_range != (event.bar2_va, event.bar2_len):
                        raise CorrelationInputError(f"line {event.line}: DESTROYED range differs from current VMA")
                    current_range = None
                elif event.range_state == "unknown":
                    pass
                elif event.range_state == "none":
                    if current_range is not None:
                        raise CorrelationInputError(f"line {event.line}: live VMA missing from DESTROYED record")
                else:
                    raise CorrelationInputError(f"line {event.line}: invalid DESTROYED range state")
                destroyed = True


def _validate_reset_records(events: list[ResetEvent]) -> None:
    by_id: dict[int, list[ResetEvent]] = {}
    for event in events:
        by_id.setdefault(event.reset_id, []).append(event)
    for reset_id, records in by_id.items():
        records.sort(key=lambda item: item.seq)
        stages = [item.stage for item in records]
        if stages not in (["BEGIN", "END"], ["BEGIN"]):
            raise CorrelationInputError(f"BAR2 reset {reset_id} has invalid sequence {stages}")
        if len(records) == 2 and records[1].mono_usec < records[0].mono_usec:
            raise CorrelationInputError(f"BAR2 reset {reset_id} has reversed timestamps")


def _map_transition_windows(parsed: ParsedInput) -> list[dict[str, Any]]:
    by_attempt: dict[tuple[int, int], list[MapEvent]] = {}
    by_id: dict[int, list[MapEvent]] = {}
    for event in parsed.maps:
        by_id.setdefault(event.allocation_id, []).append(event)
        if event.attempt:
            by_attempt.setdefault((event.allocation_id, event.attempt), []).append(event)
    windows: list[dict[str, Any]] = []
    for (allocation_id, attempt), events in by_attempt.items():
        events.sort(key=lambda item: item.seq)
        start = events[0]
        terminal = None
        for event in events[1:]:
            if event.stage in {"MAP_READY", "MAP_ROLLBACK_DONE"}:
                terminal = event
                break
            if event.stage == "MAP_FAILED" and event.bar2_len == 0:
                terminal = event
                break
        end = terminal
        start_range = next((event for event in events if event.bar2_len), None)
        windows.append({
            "kind": "map_setup",
            "allocation_id": allocation_id,
            "attempt": attempt,
            "start_usec": start.mono_usec,
            "end_usec": end.mono_usec if end else None,
            "start_line": start.line,
            "end_line": end.line if end else None,
            "va": start_range.bar2_va if start_range else None,
            "length": start_range.bar2_len if start_range else None,
            "open": end is None,
        })
    for allocation_id, events in by_id.items():
        events.sort(key=lambda item: item.seq)
        active_bar2: MapEvent | None = None
        ready: MapEvent | None = None
        for index, event in enumerate(events):
            if event.stage == "MAP_READY":
                ready = event
            elif event.stage == "FIRST_MAP_ACTIVE":
                if ready is not None and ready.bar2_va == event.bar2_va and ready.bar2_len == event.bar2_len:
                    windows.append({
                        "kind": "activation",
                        "allocation_id": allocation_id,
                        "attempt": 0,
                        "start_usec": ready.mono_usec,
                        "end_usec": event.mono_usec,
                        "start_line": ready.line,
                        "end_line": event.line,
                        "va": event.bar2_va,
                        "length": event.bar2_len,
                        "open": False,
                    })
                ready = None
                if event.access == "bar2" and event.range_state == "current" and event.bar2_len:
                    active_bar2 = event
                else:
                    active_bar2 = None
                windows.append({
                    "kind": "map_ref_activation_edge",
                    "allocation_id": allocation_id,
                    "attempt": 0,
                    "start_usec": event.mono_usec,
                    "end_usec": event.mono_usec,
                    "start_line": event.line,
                    "end_line": event.line,
                    "va": event.bar2_va if active_bar2 else None,
                    "length": event.bar2_len if active_bar2 else None,
                    "open": False,
                })
            elif event.stage == "LAST_MAP_RELEASE":
                if active_bar2 is not None and event.bar2_len:
                    windows.append({
                        "kind": "map_ref_release_edge",
                        "allocation_id": allocation_id,
                        "attempt": 0,
                        "start_usec": event.mono_usec,
                        "end_usec": event.mono_usec,
                        "start_line": event.line,
                        "end_line": event.line,
                        "va": event.bar2_va,
                        "length": event.bar2_len,
                        "open": False,
                    })
                active_bar2 = None
            elif event.stage in {"EVICTED", "DESTROYED"}:
                active_bar2 = None
            if event.stage not in {"EVICTING", "DESTROYING"}:
                continue
            closing_stage = "EVICTED" if event.stage == "EVICTING" else "DESTROYED"
            closing = next((item for item in events[index + 1:] if item.stage == closing_stage), None)
            windows.append({
                "kind": "eviction" if event.stage == "EVICTING" else "destruction",
                "allocation_id": allocation_id,
                "attempt": 0,
                "start_usec": event.mono_usec,
                "end_usec": closing.mono_usec if closing else None,
                "start_line": event.line,
                "end_line": closing.line if closing else None,
                "va": event.bar2_va if event.bar2_len else None,
                "length": event.bar2_len if event.bar2_len else None,
                "open": closing is None,
            })
    for reset_id, records in _reset_groups(parsed.resets).items():
        begin = next(item for item in records if item.stage == "BEGIN")
        end = next((item for item in records if item.stage == "END"), None)
        windows.append({
            "kind": "bar2_reset",
            "reset_id": reset_id,
            "allocation_id": None,
            "attempt": 0,
            "start_usec": begin.mono_usec,
            "end_usec": end.mono_usec if end else None,
            "start_line": begin.line,
            "end_line": end.line if end else None,
            "va": None,
            "length": None,
            "open": end is None,
        })
    return windows


def _reset_groups(events: list[ResetEvent]) -> dict[int, list[ResetEvent]]:
    result: dict[int, list[ResetEvent]] = {}
    for event in events:
        result.setdefault(event.reset_id, []).append(event)
    for records in result.values():
        records.sort(key=lambda item: item.seq)
    return result


def correlate(parsed: ParsedInput) -> dict[str, Any]:
    windows = _map_transition_windows(parsed)
    maps_by_id: dict[int, list[MapEvent]] = {}
    for event in parsed.maps:
        maps_by_id.setdefault(event.allocation_id, []).append(event)
    for events in maps_by_id.values():
        events.sort(key=lambda item: item.seq)

    reset_groups = _reset_groups(parsed.resets)
    fault_results = []
    for fault in parsed.faults:
        interval_start = fault.raw_mono_usec
        interval_end = fault.mono_usec
        active_candidates: list[dict[str, Any]] = []
        released_ranges: list[dict[str, Any]] = []
        lifecycle_unknown: list[dict[str, Any]] = []
        for allocation_id, events in maps_by_id.items():
            active_event: MapEvent | None = None
            last_release: MapEvent | None = None
            terminal_state: MapEvent | None = None
            destroying_event: MapEvent | None = None
            for event in events:
                if event.mono_usec > interval_end:
                    break
                if event.stage == "FIRST_MAP_ACTIVE":
                    active_event = event
                    last_release = None
                    terminal_state = event
                elif event.stage == "LAST_MAP_RELEASE":
                    active_event = None
                    last_release = event
                    terminal_state = event
                elif event.stage in {"EVICTED", "DESTROYED"}:
                    active_event = None
                    last_release = event
                    terminal_state = event
                elif event.stage == "BOOT_MAP_PINNED":
                    terminal_state = event
                elif event.stage == "MAP_READY":
                    terminal_state = event
                elif event.stage == "DESTROYING":
                    destroying_event = event
                    terminal_state = event

            if active_event and active_event.access == "bar2" and active_event.range_state == "current" and active_event.contains(fault.va):
                release = next((event for event in events if event.stage == "LAST_MAP_RELEASE" and event.seq > active_event.seq), None)
                if release is None or interval_end < release.mono_usec:
                    if (interval_start <= active_event.mono_usec <= interval_end or
                            (release and interval_start <= release.mono_usec <= interval_end)):
                        lifecycle_unknown.append({"allocation_id": allocation_id, "reason": "fault shares transition timestamp"})
                    else:
                        spanning_resets = []
                        for reset_id, reset_events in reset_groups.items():
                            begin = next(item for item in reset_events if item.stage == "BEGIN")
                            end = next((item for item in reset_events if item.stage == "END"), None)
                            if (active_event.mono_usec < begin.mono_usec < interval_start and
                                    end is not None and end.mono_usec < interval_start and
                                    (release is None or end.mono_usec < release.mono_usec)):
                                spanning_resets.append(reset_id)
                        active_candidates.append({
                            "allocation_id": allocation_id,
                            "stage": active_event.stage,
                            "bar2_va": f"0x{active_event.bar2_va:x}",
                            "bar2_len": f"0x{active_event.bar2_len:x}",
                            "active_since_monotonic_usec": active_event.mono_usec,
                            "last_release_monotonic_usec": release.mono_usec if release else None,
                            "map_refs_at_transition": active_event.refs,
                            "pid": active_event.pid,
                            "comm": active_event.comm,
                            "numeric_address_correlation_only": True,
                            "causal_ownership_proven": False,
                            "active_mapping_spanned_completed_bar2_reset_ids": spanning_resets,
                        })

            if terminal_state and terminal_state.bar2_len and terminal_state.contains(fault.va):
                if terminal_state.stage == "DESTROYING":
                    lifecycle_unknown.append({
                        "allocation_id": allocation_id,
                        "stage": terminal_state.stage,
                        "bar2_va": f"0x{terminal_state.bar2_va:x}",
                        "bar2_len": f"0x{terminal_state.bar2_len:x}",
                    })
                elif terminal_state.stage == "DESTROYED" and terminal_state.range_state == "unknown":
                    lifecycle_unknown.append({
                        "allocation_id": allocation_id,
                        "stage": terminal_state.stage,
                        "reason": "destroyed range could not be verified",
                        "bar2_va": f"0x{terminal_state.bar2_va:x}",
                        "bar2_len": f"0x{terminal_state.bar2_len:x}",
                    })
                elif (active_event is None and last_release and
                      last_release.seq == terminal_state.seq and
                      last_release.mono_usec < interval_start):
                    vma_teardown_confirmed = last_release.stage in {"EVICTED", "DESTROYED"}
                    released_ranges.append({
                        "allocation_id": allocation_id,
                        "release_stage": last_release.stage,
                        "release_monotonic_usec": last_release.mono_usec,
                        "release_to_fault_usec": interval_start - last_release.mono_usec,
                        "last_observed_range_state": last_release.range_state,
                        "bar2_va": f"0x{last_release.bar2_va:x}",
                        "bar2_len": f"0x{last_release.bar2_len:x}",
                        "map_ref_active_at_fault": False,
                        "vma_teardown_confirmed": vma_teardown_confirmed,
                        "bar2_vma_state_at_record": (
                            "cached_in_lru" if last_release.stage == "LAST_MAP_RELEASE" else
                            "released" if vma_teardown_confirmed else "unknown"
                        ),
                    })
                elif terminal_state.stage in {"MAP_READY", "BOOT_MAP_PINNED"}:
                    lifecycle_unknown.append({
                        "allocation_id": allocation_id,
                        "stage": terminal_state.stage,
                        "reason": "range exists without a logged active access reference",
                        "bar2_va": f"0x{terminal_state.bar2_va:x}",
                        "bar2_len": f"0x{terminal_state.bar2_len:x}",
                    })

        relevant_windows = []
        for window in windows:
            start = window["start_usec"]
            end = window["end_usec"]
            in_time = start <= interval_end and (end is None or interval_start <= end)
            if not in_time:
                continue
            if window["kind"] == "bar2_reset":
                relevant_windows.append(window)
            elif window["va"] is None or (
                window["length"] and window["va"] <= fault.va < window["va"] + window["length"]
            ):
                relevant_windows.append(window)

        if relevant_windows:
            outcome = "INCONCLUSIVE_MAPPING_TRANSITION"
        elif lifecycle_unknown:
            outcome = "INCONCLUSIVE_MAPPING_LIFECYCLE"
        elif len(active_candidates) == 1:
            outcome = "ONE_NUMERIC_ACTIVE_BAR2_MAPPING_CANDIDATE"
        elif len(active_candidates) > 1:
            outcome = "MULTIPLE_NUMERIC_ACTIVE_BAR2_MAPPING_CANDIDATES"
        elif released_ranges:
            outcome = "RECENTLY_RELEASED_BAR2_RANGE"
        else:
            outcome = "NO_OBSERVED_BAR2_MAPPING_MATCH"

        fault_results.append({
            "fault": fault.as_json(),
            "correlation_observation_window_usec": [interval_start, interval_end],
            "fault_event_time_exact": False,
            "outcome": outcome,
            "numeric_address_correlation_only": True,
            "causal_ownership_proven": False,
            "active_candidates": active_candidates,
            "recently_released_ranges": released_ranges,
            "incomplete_or_unreferenced_ranges": lifecycle_unknown,
            "transition_windows": relevant_windows,
        })

    return {
        "schema_version": 1,
        "result_kind": "AMBIENT_BAR2_LIFETIME_CORRELATION_ONLY",
        "boot_id": parsed.boot_id,
        "input_completeness_proven": False,
        "pointer_identity_used": False,
        "memory_address_used_for_fault_matching": False,
        "fault_count": len(parsed.faults),
        "mapping_event_count": len(parsed.maps),
        "reset_event_count": len(parsed.resets),
        "allocation_id_count": len({event.allocation_id for event in parsed.maps}),
        "outcomes": {name: sum(item["outcome"] == name for item in fault_results) for name in (
            "ONE_NUMERIC_ACTIVE_BAR2_MAPPING_CANDIDATE",
            "MULTIPLE_NUMERIC_ACTIVE_BAR2_MAPPING_CANDIDATES",
            "INCONCLUSIVE_MAPPING_TRANSITION",
            "RECENTLY_RELEASED_BAR2_RANGE",
            "INCONCLUSIVE_MAPPING_LIFECYCLE",
            "NO_OBSERVED_BAR2_MAPPING_MATCH",
        )},
        "faults": fault_results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("journal", type=Path, help="whole-boot journalctl -o json JSONL")
    parser.add_argument("-o", "--output", type=Path, help="write JSON report to this path")
    args = parser.parse_args(argv)
    try:
        result = correlate(parse_journal(args.journal))
        serialized = json.dumps(result, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.write_text(serialized, encoding="utf-8")
        else:
            sys.stdout.write(serialized)
    except (CorrelationInputError, OSError) as exc:
        print(f"CORRELATION_INPUT_ERROR={exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
