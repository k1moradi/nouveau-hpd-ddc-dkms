#!/usr/bin/env python3
"""Offline numeric correlation for ambient Nouveau BAR2 instmem lifetimes.

Whole-boot correlation supports exactly one Nouveau module-load epoch. A
sequence reset after a reload is rejected rather than merged.
"""

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
    "bar2_va", "bar2_len", "vma_state", "cache_state", "access",
    "map_source", "map_reset_gen", "current_reset_gen", "refs", "pid",
    "comm", "rc",
}
RESET_FIELDS = {"seq", "gen", "stage"}
MAP_STAGES = {
    "ALLOCATED", "MAP_BEGIN", "MAP_VMA_RESERVED", "MAP_VMM_MAP_OK",
    "MAP_READY", "MAP_FAILED", "MAP_DISCARDED", "MAP_ROLLBACK_BEGIN",
    "MAP_ROLLBACK_DONE", "KMAP_ACTIVE", "KMAP_LAST_RELEASE",
    "BOOT_MAP_PINNED", "VMA_EVICTING", "VMA_EVICTED",
    "OBJECT_DESTROYING", "OBJECT_IOUNMAP_BEGIN", "OBJECT_IOUNMAP_DONE",
    "OBJECT_VMM_PUT_BEGIN", "OBJECT_VMM_PUT_DONE", "OBJECT_VMM_PUT_SKIPPED",
    "OBJECT_DESTROYED",
}
DESTROY_PHASE_NEXT = {
    "OBJECT_DESTROYING": {"OBJECT_IOUNMAP_BEGIN"},
    "OBJECT_IOUNMAP_BEGIN": {"OBJECT_IOUNMAP_DONE"},
    "OBJECT_IOUNMAP_DONE": {"OBJECT_VMM_PUT_BEGIN", "OBJECT_VMM_PUT_SKIPPED"},
    "OBJECT_VMM_PUT_BEGIN": {"OBJECT_VMM_PUT_DONE"},
    "OBJECT_VMM_PUT_DONE": set(),
    "OBJECT_VMM_PUT_SKIPPED": set(),
}
DESTROY_PHASE_STAGES = set(DESTROY_PHASE_NEXT) - {"OBJECT_DESTROYING"}
VMA_STATES = {
    "not_established", "establishing", "resident", "evicted", "destroyed", "unknown",
}
CACHE_STATES = {"none", "active", "lru", "pinned"}
ACCESS_STATES = {"none", "bar0", "bar2", "unknown"}
MAP_SOURCES = {"none", "new", "cached", "unknown"}

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
    state = values["vma_state"]
    if state not in VMA_STATES:
        raise CorrelationInputError(f"line {line}: unknown VMA state {state}")
    if length and start > U64_MAX - length:
        raise CorrelationInputError(f"line {line}: BAR2 range overflows")
    if not length and start:
        raise CorrelationInputError(f"line {line}: zero-length BAR2 range has nonzero start")
    if state in {"resident", "evicted", "destroyed"} and not length:
        raise CorrelationInputError(f"line {line}: {state} VMA requires a nonzero range")
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
    vma_state: str
    cache_state: str
    access: str
    map_source: str
    map_reset_gen: int | None
    current_reset_gen: int
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
    generation: int
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
                if record.get("_TRANSPORT") != "kernel":
                    raise CorrelationInputError(f"line {line_number}: BAR2 map record is not kernel transport")
                if message.count(MAP_TAG) != 1:
                    raise CorrelationInputError(f"line {line_number}: repeated BAR2 map tag")
                fields = _fields(message.split(MAP_TAG, 1)[1], MAP_FIELDS, line_number, "BAR2 map")
                seq = _uint(fields["seq"], "sequence", line_number, maximum=U64_MAX)
                allocation_id = _uint(fields["id"], "allocation id", line_number)
                attempt = _uint(fields["attempt"], "attempt id", line_number)
                mem_addr = _uint(fields["mem_addr"], "memory address", line_number, base=0)
                mem_len = _uint(fields["mem_len"], "memory length", line_number, base=0)
                bar2_va, bar2_len, vma_state = _range(fields, line_number)
                cache_state = fields["cache_state"]
                map_source = fields["map_source"]
                map_reset_gen = (
                    None if fields["map_reset_gen"] == "none" else
                    _uint(fields["map_reset_gen"], "mapping reset generation", line_number)
                )
                current_reset_gen = _uint(
                    fields["current_reset_gen"], "current reset generation", line_number
                )
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
                if cache_state not in CACHE_STATES:
                    raise CorrelationInputError(f"line {line_number}: invalid cache state")
                if map_source not in MAP_SOURCES:
                    raise CorrelationInputError(f"line {line_number}: invalid map source")
                if map_reset_gen is not None and map_reset_gen > current_reset_gen:
                    raise CorrelationInputError(
                        f"line {line_number}: mapping reset generation is newer than current generation"
                    )
                if not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", fields["comm"]):
                    raise CorrelationInputError(f"line {line_number}: malformed comm field")
                if seq in diagnostic_sequences:
                    raise CorrelationInputError(f"line {line_number}: duplicate diagnostic sequence")
                diagnostic_sequences.add(seq)
                map_events.append(MapEvent(
                    line_number, boot, mono, seq, allocation_id, fields["stage"], attempt,
                    mem_addr, mem_len, bar2_va, bar2_len, vma_state, cache_state,
                    fields["access"], map_source, map_reset_gen, current_reset_gen,
                    refs, pid, fields["comm"], rc,
                ))

            if RESET_TAG in message:
                if record.get("_TRANSPORT") != "kernel":
                    raise CorrelationInputError(f"line {line_number}: BAR2 reset record is not kernel transport")
                if message.count(RESET_TAG) != 1:
                    raise CorrelationInputError(f"line {line_number}: repeated BAR2 reset tag")
                fields = _fields(message.split(RESET_TAG, 1)[1], RESET_FIELDS, line_number, "BAR2 reset")
                seq = _uint(fields["seq"], "sequence", line_number)
                generation = _uint(fields["gen"], "reset generation", line_number)
                if seq == 0 or generation == 0 or fields["stage"] not in {"BEGIN", "END"}:
                    raise CorrelationInputError(f"line {line_number}: invalid BAR2 reset record")
                if seq in diagnostic_sequences:
                    raise CorrelationInputError(f"line {line_number}: duplicate diagnostic sequence")
                diagnostic_sequences.add(seq)
                reset_events.append(ResetEvent(line_number, boot, mono, seq, generation, fields["stage"]))

            if FAULT_TAG in message:
                if record.get("_TRANSPORT") != "kernel":
                    raise CorrelationInputError(f"line {line_number}: raw BAR2 fault is not kernel transport")
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
    sequences = sorted([event.seq for event in [*maps, *resets]])
    if len(set(sequences)) != len(sequences):
        raise CorrelationInputError("duplicate ambient diagnostic sequence")
    if sequences and sequences != list(range(1, sequences[-1] + 1)):
        raise CorrelationInputError("ambient diagnostic sequence gap; lifecycle log is incomplete")


def _validate_attempts(events: list[MapEvent]) -> None:
    attempt_stages = {
        "MAP_BEGIN", "MAP_VMA_RESERVED", "MAP_VMM_MAP_OK", "MAP_READY",
        "MAP_FAILED", "MAP_DISCARDED", "MAP_ROLLBACK_BEGIN", "MAP_ROLLBACK_DONE",
    }
    paths = (
        ("MAP_BEGIN", "MAP_FAILED"),
        ("MAP_BEGIN", "MAP_VMA_RESERVED", "MAP_FAILED",
         "MAP_ROLLBACK_BEGIN", "MAP_ROLLBACK_DONE"),
        ("MAP_BEGIN", "MAP_VMA_RESERVED", "MAP_VMM_MAP_OK", "MAP_FAILED",
         "MAP_ROLLBACK_BEGIN", "MAP_ROLLBACK_DONE"),
        ("MAP_BEGIN", "MAP_VMA_RESERVED", "MAP_VMM_MAP_OK", "MAP_DISCARDED",
         "MAP_ROLLBACK_BEGIN", "MAP_ROLLBACK_DONE"),
        ("MAP_BEGIN", "MAP_VMA_RESERVED", "MAP_VMM_MAP_OK", "MAP_READY"),
    )
    by_attempt: dict[int, list[MapEvent]] = {}
    owners: dict[int, int] = {}
    for event in sorted(events, key=lambda item: (item.mono_usec, item.line)):
        if event.stage not in attempt_stages:
            if event.attempt:
                raise CorrelationInputError(
                    f"line {event.line}: non-attempt event has an attempt ID"
                )
            continue
        if not event.attempt:
            raise CorrelationInputError(
                f"line {event.line}: mapping attempt has a zero attempt ID"
            )
        owner = owners.setdefault(event.attempt, event.allocation_id)
        if owner != event.allocation_id:
            raise CorrelationInputError(
                f"line {event.line}: mapping attempt ID reused by another allocation"
            )
        by_attempt.setdefault(event.attempt, []).append(event)

    for attempt, records in by_attempt.items():
        records.sort(key=lambda event: (event.mono_usec, event.line))
        stages = tuple(event.stage for event in records)
        if not any(stages == path[:len(stages)] for path in paths):
            raise CorrelationInputError(
                f"line {records[-1].line}: invalid map attempt {attempt} sequence {stages}"
            )
        if len(records) > 1 and len({event.allocation_id for event in records}) != 1:
            raise CorrelationInputError(f"mapping attempt {attempt} spans allocations")

        established_gen: int | None = None
        for event in records:
            if event.stage in {"MAP_BEGIN", "MAP_VMA_RESERVED"}:
                if event.map_reset_gen is not None:
                    raise CorrelationInputError(
                        f"line {event.line}: mapping generation logged before VMM map success"
                    )
            elif event.stage == "MAP_VMM_MAP_OK":
                if event.map_reset_gen is None:
                    raise CorrelationInputError(
                        f"line {event.line}: successful VMM map lacks its reset generation"
                    )
                established_gen = event.map_reset_gen
            elif event.stage == "MAP_READY":
                if established_gen is None or event.map_reset_gen != established_gen:
                    raise CorrelationInputError(
                        f"line {event.line}: ready VMA changed its VMM establishment generation"
                    )
            elif event.stage in {"MAP_FAILED", "MAP_DISCARDED", "MAP_ROLLBACK_BEGIN"}:
                if event.map_reset_gen != established_gen:
                    raise CorrelationInputError(
                        f"line {event.line}: failed/rolled-back map changed its attempt generation"
                    )
            elif event.stage == "MAP_ROLLBACK_DONE" and event.map_reset_gen is not None:
                raise CorrelationInputError(
                    f"line {event.line}: rolled-back VMA still reports a resident generation"
                )


@dataclass
class MappingState:
    allocation_id: int
    allocated: bool = False
    destroyed: bool = False
    destroying: bool = False
    destroy_phase: str | None = None
    kmap_refs_nonzero: bool = False
    kmap_transition_refs: int = 0
    last_kmap_ref_stage: str | None = None
    last_kmap_ref_count: int | None = None
    last_kmap_ref_mono_usec: int | None = None
    vma_state: str = "not_established"
    cache_state: str = "none"
    bar2_va: int | None = None
    bar2_len: int | None = None
    map_reset_gen: int | None = None
    current_reset_gen: int = 0
    last_map_source: str = "none"
    last_access: str = "none"
    reused_from_cache_after_reset: bool = False
    pending_evict: bool = False
    pending_destroy: bool = False
    last_inactive: dict[str, Any] | None = None
    incomplete_reason: str | None = None


def _same_range(state: MappingState, event: MapEvent) -> bool:
    return (
        state.bar2_va == event.bar2_va
        and state.bar2_len == event.bar2_len
        and state.bar2_len is not None
    )


def _validate_map_records(events: list[MapEvent]) -> None:
    by_id: dict[int, list[MapEvent]] = {}
    for event in events:
        by_id.setdefault(event.allocation_id, []).append(event)

    for allocation_id, records in by_id.items():
        records.sort(key=lambda event: (event.mono_usec, event.line))
        if records[0].stage != "ALLOCATED":
            raise CorrelationInputError(
                f"line {records[0].line}: allocation {allocation_id} lacks its first ALLOCATED record"
            )
        if sum(event.stage == "ALLOCATED" for event in records) != 1:
            raise CorrelationInputError(f"allocation ID {allocation_id} is reused")
        identities = {(event.mem_addr, event.mem_len) for event in records}
        if len(identities) != 1:
            raise CorrelationInputError(f"allocation ID {allocation_id} changes backing metadata")

        state = MappingState(allocation_id)
        previous_current_gen = 0
        pending_eviction: MapEvent | None = None
        pending_destroy: MapEvent | None = None
        for event in records:
            if event.current_reset_gen < previous_current_gen:
                raise CorrelationInputError(
                    f"line {event.line}: current BAR2 reset generation decreased"
                )
            previous_current_gen = event.current_reset_gen
            if state.destroyed:
                raise CorrelationInputError(
                    f"line {event.line}: lifecycle continues after OBJECT_DESTROYED"
                )
            if state.destroying and event.stage not in DESTROY_PHASE_STAGES | {"OBJECT_DESTROYED"}:
                raise CorrelationInputError(
                    f"line {event.line}: invalid event during object destruction"
                )
            if event.map_reset_gen is not None and event.map_reset_gen > event.current_reset_gen:
                raise CorrelationInputError(
                    f"line {event.line}: mapping reset generation exceeds current generation"
                )
            if event.stage == "ALLOCATED":
                if state.allocated or event is not records[0]:
                    raise CorrelationInputError(f"line {event.line}: duplicate or misplaced ALLOCATED")
                if (
                    event.vma_state != "not_established"
                    or event.cache_state != "none"
                    or event.access != "none"
                    or event.map_source != "none"
                    or event.map_reset_gen is not None
                    or event.refs != 0
                    or event.bar2_len != 0
                ):
                    raise CorrelationInputError(f"line {event.line}: invalid ALLOCATED state")
                state.allocated = True
                continue

            if event.stage in {"MAP_BEGIN", "MAP_VMA_RESERVED", "MAP_VMM_MAP_OK",
                               "MAP_READY", "MAP_FAILED", "MAP_DISCARDED",
                               "MAP_ROLLBACK_BEGIN", "MAP_ROLLBACK_DONE"}:
                if state.destroying:
                    raise CorrelationInputError(
                        f"line {event.line}: mapping setup during object destruction"
                    )
                if event.cache_state != "none" or event.access != "none" or event.refs != 0:
                    raise CorrelationInputError(f"line {event.line}: invalid map-attempt reference state")
                if event.stage == "MAP_BEGIN" and state.kmap_refs_nonzero:
                    raise CorrelationInputError(
                        f"line {event.line}: new map attempt began while kmap refs were active"
                    )
                if event.stage == "MAP_READY":
                    if (
                        state.kmap_refs_nonzero
                        or state.vma_state == "resident"
                        or event.vma_state != "resident"
                        or not event.bar2_len
                        or event.map_reset_gen is None
                        or event.map_source != "new"
                    ):
                        raise CorrelationInputError(f"line {event.line}: invalid new VMA establishment")
                    state.vma_state = "resident"
                    state.bar2_va = event.bar2_va
                    state.bar2_len = event.bar2_len
                    state.map_reset_gen = event.map_reset_gen
                    state.cache_state = "none"
                    state.last_map_source = "new"
                    state.last_access = "none"
                    state.reused_from_cache_after_reset = False
                    state.incomplete_reason = None
                elif event.stage == "MAP_VMA_RESERVED":
                    if event.vma_state not in {"establishing", "not_established"}:
                        raise CorrelationInputError(
                            f"line {event.line}: invalid VMA setup transition state"
                        )
                    if event.map_reset_gen is not None:
                        raise CorrelationInputError(
                            f"line {event.line}: VMA reservation precedes the mapped generation"
                        )
                elif event.stage == "MAP_VMM_MAP_OK":
                    if event.vma_state != "establishing" or event.map_reset_gen is None:
                        raise CorrelationInputError(
                            f"line {event.line}: successful VMM mapping lacks its generation"
                        )
                elif event.stage in {"MAP_FAILED", "MAP_DISCARDED", "MAP_ROLLBACK_BEGIN"}:
                    if event.vma_state not in {"establishing", "not_established"}:
                        raise CorrelationInputError(
                            f"line {event.line}: invalid failed map transition state"
                        )
                elif event.stage in {"MAP_BEGIN", "MAP_ROLLBACK_DONE"}:
                    if event.vma_state != "not_established" or event.map_reset_gen is not None:
                        raise CorrelationInputError(
                            f"line {event.line}: invalid map begin/rollback completion state"
                        )
                continue

            if event.stage == "KMAP_ACTIVE":
                if state.kmap_refs_nonzero or event.refs != 1 or event.cache_state != "active":
                    raise CorrelationInputError(f"line {event.line}: invalid zero-to-nonzero kmap edge")
                if event.access == "bar2":
                    if (
                        event.vma_state != "resident"
                        or not event.bar2_len
                        or event.map_reset_gen is None
                        or event.map_source not in {"new", "cached"}
                    ):
                        raise CorrelationInputError(
                            f"line {event.line}: BAR2 kmap edge lacks a resident VMA snapshot"
                        )
                    if state.vma_state == "resident" and not _same_range(state, event):
                        raise CorrelationInputError(
                            f"line {event.line}: kmap range differs from resident allocation VMA"
                        )
                    if state.vma_state != "resident" or not _same_range(state, event):
                        raise CorrelationInputError(
                            f"line {event.line}: kmap reuse has no previously established resident VMA"
                        )
                    if event.map_source == "new" and (
                        state.last_map_source != "new" or state.cache_state != "none"
                    ):
                        raise CorrelationInputError(
                            f"line {event.line}: new kmap edge has no preceding MAP_READY event"
                        )
                    if event.map_source == "cached" and state.cache_state not in {"lru", "pinned"}:
                        raise CorrelationInputError(
                            f"line {event.line}: cached kmap reuse did not start from a cached resident VMA"
                        )
                    state.vma_state = "resident"
                    state.bar2_va = event.bar2_va
                    state.bar2_len = event.bar2_len
                    if state.map_reset_gen is None:
                        state.map_reset_gen = event.map_reset_gen
                    elif state.map_reset_gen != event.map_reset_gen:
                        raise CorrelationInputError(
                            f"line {event.line}: cached kmap changed VMA establishment generation"
                        )
                elif event.access == "bar0":
                    if event.map_source not in {"none", "unknown"}:
                        raise CorrelationInputError(
                            f"line {event.line}: BAR0 access claims a BAR2 map source"
                        )
                else:
                    raise CorrelationInputError(f"line {event.line}: kmap edge has no access mode")
                if event.vma_state == "unknown":
                    state.incomplete_reason = "kmap edge has inconsistent VMA/ioremap state"
                state.kmap_refs_nonzero = True
                state.kmap_transition_refs = event.refs
                state.last_kmap_ref_stage = event.stage
                state.last_kmap_ref_count = event.refs
                state.last_kmap_ref_mono_usec = event.mono_usec
                state.cache_state = event.cache_state
                state.last_map_source = event.map_source
                state.last_access = event.access
                state.current_reset_gen = event.current_reset_gen
                if (
                    event.map_source == "cached"
                    and event.map_reset_gen is not None
                    and event.current_reset_gen > event.map_reset_gen
                ):
                    state.reused_from_cache_after_reset = True
                continue

            if event.stage == "KMAP_LAST_RELEASE":
                if not state.kmap_refs_nonzero or event.refs != 0:
                    raise CorrelationInputError(f"line {event.line}: invalid nonzero-to-zero kmap edge")
                if event.vma_state == "resident":
                    if not _same_range(state, event) or event.map_reset_gen != state.map_reset_gen:
                        raise CorrelationInputError(
                            f"line {event.line}: last kmap release changed resident VMA identity"
                        )
                    if event.cache_state not in {"lru", "pinned"}:
                        raise CorrelationInputError(
                            f"line {event.line}: zero-ref resident VMA is not classified as cached/pinned"
                        )
                elif event.vma_state not in {"not_established", "unknown"}:
                    raise CorrelationInputError(f"line {event.line}: invalid last kmap release VMA state")
                state.kmap_refs_nonzero = False
                state.last_kmap_ref_stage = event.stage
                state.last_kmap_ref_count = event.refs
                state.last_kmap_ref_mono_usec = event.mono_usec
                state.cache_state = event.cache_state
                state.vma_state = event.vma_state
                if event.bar2_len:
                    state.bar2_va = event.bar2_va
                    state.bar2_len = event.bar2_len
                state.current_reset_gen = event.current_reset_gen
                continue

            if event.stage == "BOOT_MAP_PINNED":
                if (
                    state.kmap_refs_nonzero
                    or event.refs != 0
                    or event.vma_state != "resident"
                    or event.cache_state != "pinned"
                    or event.map_source not in {"new", "cached"}
                    or event.map_reset_gen is None
                    or not event.bar2_len
                ):
                    raise CorrelationInputError(f"line {event.line}: invalid boot-pinned VMA record")
                if state.vma_state == "resident" and not _same_range(state, event):
                    raise CorrelationInputError(f"line {event.line}: pinned VMA changed range")
                state.vma_state = "resident"
                state.cache_state = "pinned"
                state.bar2_va = event.bar2_va
                state.bar2_len = event.bar2_len
                state.map_reset_gen = event.map_reset_gen
                state.last_map_source = event.map_source
                state.current_reset_gen = event.current_reset_gen
                continue

            if event.stage == "VMA_EVICTING":
                if (
                    state.kmap_refs_nonzero
                    or event.refs != 0
                    or event.vma_state != "resident"
                    or event.cache_state != "lru"
                    or not event.bar2_len
                ):
                    raise CorrelationInputError(f"line {event.line}: invalid VMA eviction start")
                if state.vma_state != "resident" or not _same_range(state, event):
                    raise CorrelationInputError(f"line {event.line}: eviction does not match resident VMA")
                if event.map_reset_gen != state.map_reset_gen:
                    raise CorrelationInputError(f"line {event.line}: eviction changed map reset generation")
                pending_eviction = event
                state.pending_evict = True
                state.current_reset_gen = event.current_reset_gen
                continue

            if event.stage == "VMA_EVICTED":
                if (
                    pending_eviction is None
                    or event.refs != 0
                    or event.vma_state != "evicted"
                    or event.cache_state != "none"
                    or not _same_range(state, event)
                    or event.map_reset_gen != state.map_reset_gen
                ):
                    raise CorrelationInputError(f"line {event.line}: VMA eviction completion lacks matching start")
                state.last_inactive = {
                    "allocation_id": allocation_id,
                    "terminal_stage": "VMA_EVICTED",
                    "bar2_va": event.bar2_va,
                    "bar2_len": event.bar2_len,
                    "map_reset_gen": event.map_reset_gen,
                    "mono_usec": event.mono_usec,
                    "line": event.line,
                }
                state.vma_state = "evicted"
                state.cache_state = "none"
                state.last_map_source = "none"
                state.bar2_va = event.bar2_va
                state.bar2_len = event.bar2_len
                state.map_reset_gen = event.map_reset_gen
                state.pending_evict = False
                pending_eviction = None
                state.current_reset_gen = event.current_reset_gen
                continue

            if event.stage == "OBJECT_DESTROYING":
                if state.destroying:
                    raise CorrelationInputError(f"line {event.line}: invalid object destruction start")
                if event.refs > 0:
                    state.incomplete_reason = "object destruction began with active kmap refs"
                elif state.kmap_refs_nonzero:
                    raise CorrelationInputError(
                        f"line {event.line}: destruction refcount contradicts the active kmap edge"
                    )
                if event.vma_state == "resident":
                    if state.vma_state != "resident" or not _same_range(state, event):
                        raise CorrelationInputError(
                            f"line {event.line}: destruction range differs from resident VMA"
                        )
                elif event.vma_state == "not_established":
                    if state.vma_state == "resident":
                        raise CorrelationInputError(
                            f"line {event.line}: object destruction omitted its resident VMA"
                        )
                elif event.vma_state != "unknown":
                    raise CorrelationInputError(f"line {event.line}: invalid destruction VMA state")
                state.destroying = True
                state.pending_destroy = True
                state.destroy_phase = "OBJECT_DESTROYING"
                state.current_reset_gen = event.current_reset_gen
                pending_destroy = event
                continue

            if event.stage in DESTROY_PHASE_STAGES:
                if not state.destroying or pending_destroy is None:
                    raise CorrelationInputError(
                        f"line {event.line}: teardown phase without object destruction"
                    )
                if event.stage not in DESTROY_PHASE_NEXT.get(state.destroy_phase or "", set()):
                    raise CorrelationInputError(
                        f"line {event.line}: invalid object teardown phase order"
                    )
                if (
                    event.refs != pending_destroy.refs
                    or event.attempt != 0
                    or event.cache_state != "none"
                    or event.access != "none"
                    or event.map_source != "none"
                    or event.map_reset_gen != pending_destroy.map_reset_gen
                    or event.bar2_va != pending_destroy.bar2_va
                    or event.bar2_len != pending_destroy.bar2_len
                ):
                    raise CorrelationInputError(
                        f"line {event.line}: teardown phase changed object snapshot"
                    )
                if event.stage in {
                    "OBJECT_IOUNMAP_BEGIN", "OBJECT_IOUNMAP_DONE", "OBJECT_VMM_PUT_BEGIN",
                }:
                    if event.vma_state != pending_destroy.vma_state or event.rc != 0:
                        raise CorrelationInputError(
                            f"line {event.line}: invalid pre-release teardown phase state"
                        )
                elif event.stage == "OBJECT_VMM_PUT_DONE":
                    if event.vma_state != "destroyed" or event.rc != 0:
                        raise CorrelationInputError(
                            f"line {event.line}: invalid completed VMM release state"
                        )
                    state.vma_state = "destroyed"
                    state.cache_state = "none"
                    state.map_reset_gen = event.map_reset_gen
                    state.last_inactive = {
                        "allocation_id": allocation_id,
                        "terminal_stage": event.stage,
                        "bar2_va": event.bar2_va,
                        "bar2_len": event.bar2_len,
                        "map_reset_gen": event.map_reset_gen,
                        "mono_usec": event.mono_usec,
                        "line": event.line,
                    }
                elif event.stage == "OBJECT_VMM_PUT_SKIPPED":
                    if event.vma_state != "unknown" or event.rc != -19:
                        raise CorrelationInputError(
                            f"line {event.line}: invalid skipped VMM release state"
                        )
                    state.vma_state = "unknown"
                    state.incomplete_reason = "object destruction could not release VMM range"
                state.destroy_phase = event.stage
                state.current_reset_gen = event.current_reset_gen
                continue

            if event.stage == "OBJECT_DESTROYED":
                if not state.destroying or pending_destroy is None:
                    raise CorrelationInputError(f"line {event.line}: object destroyed without valid start")
                if state.destroy_phase not in {
                    "OBJECT_DESTROYING", "OBJECT_VMM_PUT_DONE", "OBJECT_VMM_PUT_SKIPPED",
                }:
                    raise CorrelationInputError(
                        f"line {event.line}: object destroyed before teardown phases completed"
                    )
                if event.refs != pending_destroy.refs:
                    raise CorrelationInputError(
                        f"line {event.line}: object destruction refcount changed without a kmap edge"
                    )
                if event.vma_state == "destroyed":
                    if (
                        pending_destroy.vma_state != "resident"
                        or not _same_range(state, event)
                        or event.map_reset_gen != state.map_reset_gen
                    ):
                        raise CorrelationInputError(
                            f"line {event.line}: destroyed VMA lacks a matching resident range"
                        )
                    state.last_inactive = {
                        "allocation_id": allocation_id,
                        "terminal_stage": "OBJECT_DESTROYED",
                        "bar2_va": event.bar2_va,
                        "bar2_len": event.bar2_len,
                        "map_reset_gen": event.map_reset_gen,
                        "mono_usec": event.mono_usec,
                        "line": event.line,
                    }
                elif event.vma_state == "not_established":
                    if pending_destroy.vma_state == "resident":
                        raise CorrelationInputError(
                            f"line {event.line}: destroyed record lost a resident VMA range"
                        )
                elif event.vma_state == "unknown":
                    state.incomplete_reason = "object destruction could not confirm VMA release"
                else:
                    raise CorrelationInputError(f"line {event.line}: invalid destroyed VMA state")
                state.vma_state = event.vma_state
                state.cache_state = "none"
                if event.bar2_len:
                    state.bar2_va = event.bar2_va
                    state.bar2_len = event.bar2_len
                state.map_reset_gen = event.map_reset_gen
                state.destroyed = True
                state.destroying = False
                state.pending_destroy = False
                state.destroy_phase = None
                state.current_reset_gen = event.current_reset_gen
                if event.refs:
                    state.incomplete_reason = "object was destroyed with active kmap refs"
                continue

            raise CorrelationInputError(f"line {event.line}: unhandled mapping stage {event.stage}")

        if pending_eviction or pending_destroy or any(
            attempt_event.allocation_id == allocation_id
            and attempt_event.stage in {"MAP_BEGIN", "MAP_VMA_RESERVED", "MAP_VMM_MAP_OK",
                                        "MAP_FAILED", "MAP_DISCARDED", "MAP_ROLLBACK_BEGIN"}
            for attempt_event in events
        ):
            # Open-at-EOF transitions are preserved and classified as incomplete by
            # the per-fault state reconstruction if they intersect a fault.
            pass


def _validate_reset_records(events: list[ResetEvent]) -> None:
    ordered = sorted(events, key=lambda event: (event.mono_usec, event.line))
    generation = 0
    pending: int | None = None
    for event in ordered:
        if event.stage == "BEGIN":
            if pending is not None:
                raise CorrelationInputError(
                    f"line {event.line}: overlapping BAR2 reset intervals are unsupported"
                )
            if event.generation != generation + 1:
                raise CorrelationInputError(
                    f"line {event.line}: BAR2 reset generation is not monotonic"
                )
            generation = event.generation
            pending = generation
        elif event.stage == "END":
            if event.generation != pending:
                raise CorrelationInputError(
                    f"line {event.line}: BAR2 reset end has no matching begin"
                )
            pending = None


def _reset_groups(events: list[ResetEvent]) -> dict[int, list[ResetEvent]]:
    grouped: dict[int, list[ResetEvent]] = {}
    for event in events:
        grouped.setdefault(event.generation, []).append(event)
    for records in grouped.values():
        records.sort(key=lambda event: event.seq)
    return grouped


def _before_fault(event: MapEvent | ResetEvent, fault: FaultEvent) -> bool:
    if event.mono_usec != fault.raw_mono_usec:
        return event.mono_usec < fault.raw_mono_usec
    return event.line < fault.raw_line


def _state_at_fault(events: list[MapEvent], fault: FaultEvent) -> MappingState:
    state = MappingState(events[0].allocation_id)
    for event in sorted(events, key=lambda item: (item.mono_usec, item.line)):
        if not _before_fault(event, fault):
            break
        state.current_reset_gen = max(state.current_reset_gen, event.current_reset_gen)
        if event.stage == "ALLOCATED":
            state.allocated = True
        elif event.stage == "MAP_READY":
            state.vma_state = "resident"
            state.cache_state = "none"
            state.bar2_va = event.bar2_va
            state.bar2_len = event.bar2_len
            state.map_reset_gen = event.map_reset_gen
            state.last_map_source = "new"
            state.last_access = "none"
            state.reused_from_cache_after_reset = False
            state.incomplete_reason = "VMA established before first kmap reference edge"
        elif event.stage in {"MAP_BEGIN", "MAP_VMA_RESERVED", "MAP_VMM_MAP_OK",
                             "MAP_FAILED", "MAP_DISCARDED", "MAP_ROLLBACK_BEGIN"}:
            # Attempt state is reconstructed independently. A concurrent losing
            # attempt must not overwrite an already resident VMA snapshot.
            pass
        elif event.stage == "MAP_ROLLBACK_DONE":
            state.incomplete_reason = None
        elif event.stage == "KMAP_ACTIVE":
            state.kmap_refs_nonzero = True
            state.kmap_transition_refs = event.refs
            state.last_kmap_ref_stage = event.stage
            state.last_kmap_ref_count = event.refs
            state.last_kmap_ref_mono_usec = event.mono_usec
            state.vma_state = event.vma_state
            state.cache_state = event.cache_state
            if event.bar2_len:
                state.bar2_va = event.bar2_va
                state.bar2_len = event.bar2_len
            state.map_reset_gen = event.map_reset_gen
            state.last_map_source = event.map_source
            state.last_access = event.access
            state.incomplete_reason = None
            if (
                event.map_source == "cached"
                and event.map_reset_gen is not None
                and event.current_reset_gen > event.map_reset_gen
            ):
                state.reused_from_cache_after_reset = True
        elif event.stage == "KMAP_LAST_RELEASE":
            state.kmap_refs_nonzero = False
            state.kmap_transition_refs = 0
            state.last_kmap_ref_stage = event.stage
            state.last_kmap_ref_count = event.refs
            state.last_kmap_ref_mono_usec = event.mono_usec
            state.vma_state = event.vma_state
            state.cache_state = event.cache_state
            if event.bar2_len:
                state.bar2_va = event.bar2_va
                state.bar2_len = event.bar2_len
            state.map_reset_gen = event.map_reset_gen
        elif event.stage == "BOOT_MAP_PINNED":
            state.kmap_refs_nonzero = False
            state.vma_state = event.vma_state
            state.cache_state = event.cache_state
            state.bar2_va = event.bar2_va
            state.bar2_len = event.bar2_len
            state.map_reset_gen = event.map_reset_gen
            state.last_map_source = event.map_source
            state.incomplete_reason = None
            if (
                event.map_source == "cached"
                and event.map_reset_gen is not None
                and event.current_reset_gen > event.map_reset_gen
            ):
                state.reused_from_cache_after_reset = True
        elif event.stage == "VMA_EVICTING":
            state.pending_evict = True
            state.incomplete_reason = "VMA eviction is in progress"
            state.bar2_va = event.bar2_va
            state.bar2_len = event.bar2_len
        elif event.stage == "VMA_EVICTED":
            state.pending_evict = False
            state.vma_state = "evicted"
            state.cache_state = "none"
            state.bar2_va = event.bar2_va
            state.bar2_len = event.bar2_len
            state.map_reset_gen = event.map_reset_gen
            state.last_inactive = {
                "allocation_id": event.allocation_id,
                "terminal_stage": "VMA_EVICTED",
                "bar2_va": event.bar2_va,
                "bar2_len": event.bar2_len,
                "map_reset_gen": event.map_reset_gen,
                "mono_usec": event.mono_usec,
                "line": event.line,
            }
            state.incomplete_reason = None
        elif event.stage == "OBJECT_DESTROYING":
            state.destroying = True
            state.pending_destroy = True
            state.destroy_phase = event.stage
            state.incomplete_reason = "object destruction is in progress"
            if event.bar2_len:
                state.bar2_va = event.bar2_va
                state.bar2_len = event.bar2_len
        elif event.stage in DESTROY_PHASE_STAGES:
            state.destroy_phase = event.stage
            state.incomplete_reason = "object destruction is in progress"
            if event.bar2_len:
                state.bar2_va = event.bar2_va
                state.bar2_len = event.bar2_len
            if event.stage == "OBJECT_VMM_PUT_DONE":
                state.vma_state = "destroyed"
                state.cache_state = "none"
                state.map_reset_gen = event.map_reset_gen
                state.last_inactive = {
                    "allocation_id": event.allocation_id,
                    "terminal_stage": event.stage,
                    "bar2_va": event.bar2_va,
                    "bar2_len": event.bar2_len,
                    "map_reset_gen": event.map_reset_gen,
                    "mono_usec": event.mono_usec,
                    "line": event.line,
                }
            elif event.stage == "OBJECT_VMM_PUT_SKIPPED":
                state.vma_state = "unknown"
                state.incomplete_reason = "object destruction could not release VMM range"
        elif event.stage == "OBJECT_DESTROYED":
            state.destroying = False
            state.pending_destroy = False
            state.destroyed = True
            state.destroy_phase = None
            state.vma_state = event.vma_state
            state.cache_state = "none"
            if event.bar2_len:
                state.bar2_va = event.bar2_va
                state.bar2_len = event.bar2_len
            state.map_reset_gen = event.map_reset_gen
            if event.vma_state == "destroyed":
                state.last_inactive = {
                    "allocation_id": event.allocation_id,
                    "terminal_stage": "OBJECT_DESTROYED",
                    "bar2_va": event.bar2_va,
                    "bar2_len": event.bar2_len,
                    "map_reset_gen": event.map_reset_gen,
                    "mono_usec": event.mono_usec,
                    "line": event.line,
                }
                state.incomplete_reason = (
                    "object was destroyed with active kmap refs"
                    if event.refs else None
                )
            elif event.vma_state == "not_established":
                state.incomplete_reason = None
            else:
                state.incomplete_reason = "object destruction did not confirm VMA release"
    return state


def _reset_state_at_fault(resets: list[ResetEvent], fault: FaultEvent) -> tuple[int, bool]:
    generation = 0
    begun: set[int] = set()
    ended: set[int] = set()
    for event in sorted(resets, key=lambda item: (item.mono_usec, item.line)):
        if not _before_fault(event, fault):
            continue
        if event.stage == "BEGIN":
            generation = max(generation, event.generation)
            begun.add(event.generation)
        else:
            ended.add(event.generation)
    return generation, bool(begun - ended)


def _transition_records(parsed: ParsedInput, fault: FaultEvent) -> list[dict[str, Any]]:
    transitions: list[dict[str, Any]] = []
    before = [event for event in parsed.maps if _before_fault(event, fault)]
    attempts: dict[tuple[int, int], list[MapEvent]] = {}
    by_id: dict[int, list[MapEvent]] = {}
    all_by_id: dict[int, list[MapEvent]] = {}
    for event in parsed.maps:
        all_by_id.setdefault(event.allocation_id, []).append(event)
    for records in all_by_id.values():
        records.sort(key=lambda event: (event.mono_usec, event.line))
    attempt_stages = {
        "MAP_BEGIN", "MAP_VMA_RESERVED", "MAP_VMM_MAP_OK", "MAP_READY",
        "MAP_FAILED", "MAP_DISCARDED", "MAP_ROLLBACK_BEGIN", "MAP_ROLLBACK_DONE",
    }
    for event in before:
        by_id.setdefault(event.allocation_id, []).append(event)
        if event.attempt and event.stage in attempt_stages:
            attempts.setdefault((event.allocation_id, event.attempt), []).append(event)
    for (allocation_id, attempt), records in attempts.items():
        records.sort(key=lambda event: (event.mono_usec, event.line))
        terminal = records[-1].stage in {"MAP_READY", "MAP_ROLLBACK_DONE"}
        terminal = terminal or (
            records[-1].stage == "MAP_FAILED" and records[-1].bar2_len == 0
        )
        if terminal:
            continue
        ranged = next((event for event in reversed(records) if event.bar2_len), None)
        if ranged and ranged.bar2_va <= fault.va < ranged.bar2_va + ranged.bar2_len:
            transitions.append({
                "kind": "map_setup",
                "allocation_id": allocation_id,
                "attempt": attempt,
                "start_usec": records[0].mono_usec,
                "va": f"0x{ranged.bar2_va:x}",
                "length": f"0x{ranged.bar2_len:x}",
                "event_line": records[0].line,
            })
        elif not ranged:
            transitions.append({
                "kind": "map_setup_address_not_yet_known",
                "allocation_id": allocation_id,
                "attempt": attempt,
                "start_usec": records[0].mono_usec,
                "va": None,
                "length": None,
                "event_line": records[0].line,
            })
    for allocation_id, records in by_id.items():
        records.sort(key=lambda event: (event.mono_usec, event.line))
        if not records:
            continue
        last = records[-1]
        if (
            last.stage == "VMA_EVICTING" or
            last.stage in {"OBJECT_DESTROYING"} | DESTROY_PHASE_STAGES
        ) and last.bar2_len:
            if last.bar2_va <= fault.va < last.bar2_va + last.bar2_len:
                next_event = next((
                    event for event in all_by_id.get(allocation_id, [])
                    if not _before_fault(event, fault)
                ), None)
                transitions.append({
                    "kind": "vma_eviction" if last.stage == "VMA_EVICTING" else "object_destruction",
                    "phase": last.stage,
                    "next_stage": next_event.stage if next_event else None,
                    "allocation_id": allocation_id,
                    "attempt": 0,
                    "start_usec": last.mono_usec,
                    "end_usec": next_event.mono_usec if next_event else None,
                    "va": f"0x{last.bar2_va:x}",
                    "length": f"0x{last.bar2_len:x}",
                    "event_line": last.line,
                })
    return transitions


def correlate(parsed: ParsedInput) -> dict[str, Any]:
    maps_by_id: dict[int, list[MapEvent]] = {}
    for event in parsed.maps:
        maps_by_id.setdefault(event.allocation_id, []).append(event)
    for records in maps_by_id.values():
        records.sort(key=lambda event: (event.mono_usec, event.line))

    faults = sorted(parsed.faults, key=lambda event: (event.raw_mono_usec, event.raw_line))
    fault_results: list[dict[str, Any]] = []
    reset_begins = [event for event in parsed.resets if event.stage == "BEGIN"]
    previous_fault: FaultEvent | None = None
    for ordinal, fault in enumerate(faults, 1):
        current_gen, reset_in_progress = _reset_state_at_fault(parsed.resets, fault)
        active_candidates: list[dict[str, Any]] = []
        zero_ref_candidates: list[dict[str, Any]] = []
        resident_candidates: list[dict[str, Any]] = []
        incomplete: list[dict[str, Any]] = []
        recently_evicted: list[dict[str, Any]] = []
        transitions = _transition_records(parsed, fault)

        for allocation_id, records in maps_by_id.items():
            state = _state_at_fault(records, fault)
            if state.current_reset_gen > current_gen and state.bar2_len and state.bar2_va is not None:
                if state.bar2_va <= fault.va < state.bar2_va + state.bar2_len:
                    incomplete.append({
                        "allocation_id": allocation_id,
                        "reason": "mapping observed a reset generation before its BEGIN record",
                        "vma_state": state.vma_state,
                        "bar2_va": f"0x{state.bar2_va:x}",
                        "bar2_len": f"0x{state.bar2_len:x}",
                    })
            if state.incomplete_reason and state.bar2_len and state.bar2_va is not None:
                if state.bar2_va <= fault.va < state.bar2_va + state.bar2_len:
                    incomplete.append({
                        "allocation_id": allocation_id,
                        "reason": state.incomplete_reason,
                        "vma_state": state.vma_state,
                        "bar2_va": f"0x{state.bar2_va:x}",
                        "bar2_len": f"0x{state.bar2_len:x}",
                    })
            if state.pending_evict or state.pending_destroy:
                if state.bar2_len and state.bar2_va is not None and state.bar2_va <= fault.va < state.bar2_va + state.bar2_len:
                    incomplete.append({
                        "allocation_id": allocation_id,
                        "reason": state.incomplete_reason or "mapping transition in progress",
                        "vma_state": state.vma_state,
                        "bar2_va": f"0x{state.bar2_va:x}",
                        "bar2_len": f"0x{state.bar2_len:x}",
                    })

            if (
                state.vma_state == "resident"
                and state.bar2_va is not None
                and state.bar2_len
                and state.bar2_va <= fault.va < state.bar2_va + state.bar2_len
            ):
                if state.incomplete_reason:
                    continue
                if state.kmap_refs_nonzero:
                    outcome = (
                        "ONE_RESET_SPANNING_ACTIVE_VMA_CANDIDATE"
                        if state.map_reset_gen is not None
                        and current_gen > state.map_reset_gen
                        else "ONE_ACTIVE_KMAP_RESIDENT_VMA_CANDIDATE"
                    )
                elif state.cache_state == "lru":
                    outcome = (
                        "ONE_RESET_SPANNING_CACHED_VMA_CANDIDATE"
                        if state.map_reset_gen is not None
                        and current_gen > state.map_reset_gen
                        else "ONE_ZERO_REF_CACHED_VMA_CANDIDATE"
                    )
                elif state.cache_state == "pinned":
                    outcome = "ONE_ZERO_REF_RESIDENT_VMA_CANDIDATE"
                else:
                    incomplete.append({
                        "allocation_id": allocation_id,
                        "reason": "resident VMA has no logged kmap/cache state",
                        "vma_state": state.vma_state,
                        "bar2_va": f"0x{state.bar2_va:x}",
                        "bar2_len": f"0x{state.bar2_len:x}",
                    })
                    continue
                candidate = {
                    "allocation_id": allocation_id,
                    "outcome": outcome,
                    "kmap_ref_state_at_fault": (
                        "NONZERO" if state.kmap_refs_nonzero else "ZERO"
                    ),
                    "exact_kmap_refcount_at_fault": (
                        None if state.kmap_refs_nonzero else 0
                    ),
                    "last_kmap_ref_edge": (
                        {
                            "stage": state.last_kmap_ref_stage,
                            "refcount_at_edge": state.last_kmap_ref_count,
                            "monotonic_usec": state.last_kmap_ref_mono_usec,
                        }
                        if state.last_kmap_ref_stage is not None else None
                    ),
                    "vma_state": state.vma_state,
                    "cache_state": state.cache_state,
                    "bar2_va": f"0x{state.bar2_va:x}",
                    "bar2_len": f"0x{state.bar2_len:x}",
                    "map_reset_gen": state.map_reset_gen,
                    "current_reset_gen_at_fault": current_gen,
                    "last_map_source": state.last_map_source,
                    "last_access": state.last_access,
                    "mapping_spans_reset": (
                        state.map_reset_gen is not None
                        and current_gen > state.map_reset_gen
                    ),
                    "reused_from_cache_after_reset": state.reused_from_cache_after_reset,
                    "numeric_address_correlation_only": True,
                    "causal_ownership_proven": False,
                }
                resident_candidates.append(candidate)
                if state.kmap_refs_nonzero:
                    active_candidates.append(candidate)
                else:
                    zero_ref_candidates.append(candidate)

            if (
                state.last_inactive
                and not (
                    state.vma_state == "resident"
                    and state.bar2_va is not None
                    and state.bar2_len is not None
                    and state.bar2_va <= fault.va < state.bar2_va + state.bar2_len
                )
                and state.last_inactive["bar2_va"] <= fault.va
                < state.last_inactive["bar2_va"] + state.last_inactive["bar2_len"]
            ):
                recently_evicted.append({
                    **state.last_inactive,
                    "release_to_fault_usec": fault.raw_mono_usec - state.last_inactive["mono_usec"],
                    "active_at_fault": False,
                    "causal_ownership_proven": False,
                })

        resets_before = [
            event for event in reset_begins if _before_fault(event, fault)
        ]
        post_fault_resets = [
            event for event in reset_begins
            if fault.raw_mono_usec < event.mono_usec <= fault.mono_usec
        ]
        resets_since_previous = 0
        delta_previous = None
        if previous_fault is not None:
            delta_previous = fault.raw_mono_usec - previous_fault.raw_mono_usec
            resets_since_previous = sum(
                event.generation > 0
                and (event.mono_usec, event.line) > (previous_fault.raw_mono_usec, previous_fault.raw_line)
                and _before_fault(event, fault)
                for event in reset_begins
            )

        transition_reasons = {
            "VMA established before first kmap reference edge",
            "mapping establishment transition overlaps fault time",
            "VMA eviction is in progress",
            "object destruction is in progress",
            "mapping observed a reset generation before its BEGIN record",
        }
        has_transition_incomplete = any(
            item["reason"] in transition_reasons for item in incomplete
        )
        if reset_in_progress or transitions or has_transition_incomplete:
            outcome = "INCONCLUSIVE_VMA_TRANSITION"
        elif incomplete:
            outcome = "INCOMPLETE_MAPPING_LIFECYCLE"
        elif len(resident_candidates) > 1:
            outcome = "MULTIPLE_RESIDENT_VMA_CANDIDATES"
        elif len(resident_candidates) == 1:
            outcome = resident_candidates[0]["outcome"]
        elif recently_evicted:
            outcome = "RECENTLY_EVICTED_VMA_RANGE"
        else:
            outcome = "NO_OBSERVED_BAR2_MAPPING_MATCH"

        fault_results.append({
            "fault_ordinal": ordinal,
            "time_since_previous_fault_usec": delta_previous,
            "bar2_resets_before_fault": len(resets_before),
            "bar2_reset_generation_at_fault": current_gen,
            "bar2_resets_since_previous_fault": resets_since_previous,
            "reset_in_progress_at_fault": reset_in_progress,
            "post_fault_recovery_reset_generations": [event.generation for event in post_fault_resets],
            "fault": fault.as_json(),
            "outcome": outcome,
            "numeric_address_correlation_only": True,
            "causal_ownership_proven": False,
            "active_candidates": active_candidates,
            "zero_ref_candidates": zero_ref_candidates,
            "resident_candidates": resident_candidates,
            "recently_evicted_ranges": recently_evicted,
            "incomplete_lifecycles": incomplete,
            "transition_records": transitions,
        })
        previous_fault = fault

    outcomes = sorted({item["outcome"] for item in fault_results})
    return {
        "schema_version": 2,
        "result_kind": "AMBIENT_BAR2_LIFETIME_CORRELATION_ONLY",
        "boot_id": parsed.boot_id,
        "input_completeness_proven": False,
        "diagnostic_sequence_gaps_rejected": True,
        "module_load_epoch_policy": "single_nouveau_module_instance_per_boot",
        "pointer_identity_used": False,
        "memory_address_used_for_fault_matching": False,
        "fault_count": len(parsed.faults),
        "mapping_event_count": len(parsed.maps),
        "reset_event_count": len(parsed.resets),
        "allocation_id_count": len({event.allocation_id for event in parsed.maps}),
        "outcomes": {name: sum(item["outcome"] == name for item in fault_results)
                     for name in outcomes},
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
