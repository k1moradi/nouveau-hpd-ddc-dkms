#!/usr/bin/env python3
"""Merge a loss-checked Nouveau BAR2 event-ring dump with a kernel journal."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import os
import tempfile
from typing import Any


HEADER = re.compile(
    r"NOUVEAU_DIAG_BAR2_RING capacity=(\d+) head=(\d+) "
    r"captured=(\d+) dropped=(\d+) cutoff_ns=(\d+)"
)
EVENT = re.compile(
    r"NOUVEAU_DIAG_BAR2_RING_EVENT index=(\d+) mono_ns=(\d+) "
    r"message=(NOUVEAU_DIAG_BAR2_(?:MAP|RESET|VMM) .+)"
)
GAP = re.compile(r"NOUVEAU_DIAG_BAR2_RING_GAP index=(\d+)")
FOOTER = re.compile(r"NOUVEAU_DIAG_BAR2_RING_END head=(\d+) dropped=(\d+)")
REPLACED_JOURNAL_TAGS = (
    "NOUVEAU_DIAG_BAR2_MAP ",
    "NOUVEAU_DIAG_BAR2_RESET ",
    "NOUVEAU_DIAG_BAR2_VMM ",
)
KERNEL_LOG_LOSS = re.compile(
    r"/dev/kmsg buffer overrun,\s*some messages lost", re.IGNORECASE,
)


class RingCaptureError(ValueError):
    """The ring or journal cannot support a complete correlation input."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_ring(path: Path) -> tuple[dict[str, int], list[dict[str, Any]]]:
    header: dict[str, int] | None = None
    footer: dict[str, int] | None = None
    events: list[dict[str, Any]] = []
    gaps: list[int] = []

    for line_number, raw in enumerate(path.read_text(encoding="ascii").splitlines(), 1):
        if not raw:
            continue
        match = HEADER.fullmatch(raw)
        if match:
            if header is not None or events or gaps:
                raise RingCaptureError(f"line {line_number}: duplicate or misplaced ring header")
            header = {
                "capacity": int(match.group(1)),
                "head": int(match.group(2)),
                "captured": int(match.group(3)),
                "dropped": int(match.group(4)),
                "cutoff_ns": int(match.group(5)),
            }
            continue

        match = EVENT.fullmatch(raw)
        if match:
            if header is None or footer is not None:
                raise RingCaptureError(f"line {line_number}: event outside ring snapshot")
            index = int(match.group(1))
            mono_ns = int(match.group(2))
            if mono_ns <= 0:
                raise RingCaptureError(f"line {line_number}: invalid monotonic timestamp")
            events.append({"index": index, "mono_ns": mono_ns, "message": match.group(3)})
            continue

        match = GAP.fullmatch(raw)
        if match:
            gaps.append(int(match.group(1)))
            continue

        match = FOOTER.fullmatch(raw)
        if match:
            if header is None or footer is not None:
                raise RingCaptureError(f"line {line_number}: duplicate or misplaced ring footer")
            footer = {"head": int(match.group(1)), "dropped": int(match.group(2))}
            continue

        raise RingCaptureError(f"line {line_number}: unknown ring record schema")

    if header is None or footer is None:
        raise RingCaptureError("ring dump lacks a header or footer")
    if header["capacity"] <= 0:
        raise RingCaptureError("ring capacity is zero")
    if header["cutoff_ns"] <= 0:
        raise RingCaptureError("ring snapshot cutoff is invalid")
    if header["captured"] != min(header["head"], header["capacity"]):
        raise RingCaptureError("ring captured count is inconsistent with head/capacity")
    if header["head"] != header["captured"]:
        raise RingCaptureError("ring overflowed before the snapshot")
    if footer["head"] < header["head"]:
        raise RingCaptureError("ring head moved backwards while it was being dumped")
    if footer["dropped"] < header["dropped"]:
        raise RingCaptureError("ring dropped-event counter moved backwards")
    if header["dropped"] != 0:
        raise RingCaptureError("ring reports dropped events before the snapshot cutoff")
    if gaps:
        raise RingCaptureError(f"ring contains unpublished slots: {gaps[:8]}")
    if len(events) != header["captured"]:
        raise RingCaptureError("ring event count does not match captured count")
    if [event["index"] for event in events] != list(range(header["captured"])):
        raise RingCaptureError("ring indexes are missing, duplicated, or reordered")
    if any(event["mono_ns"] > header["cutoff_ns"] for event in events):
        raise RingCaptureError("captured event timestamp is newer than snapshot cutoff")

    header["end_head"] = footer["head"]
    header["end_dropped"] = footer["dropped"]
    return header, events


def load_journal(
    path: Path, cutoff_ns: int,
) -> tuple[str, list[dict[str, Any]], int]:
    records: list[dict[str, Any]] = []
    boot_ids: set[str] = set()
    records_seen = 0
    cutoff_usec = cutoff_ns // 1000
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw:
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RingCaptureError(f"journal line {line_number} is not JSON") from exc
        if not isinstance(record, dict):
            raise RingCaptureError(f"journal line {line_number} is not an object")
        boot_id = record.get("_BOOT_ID")
        mono_usec = record.get("__MONOTONIC_TIMESTAMP")
        if not isinstance(boot_id, str) or not boot_id:
            raise RingCaptureError(f"journal line {line_number} lacks _BOOT_ID")
        if mono_usec is None:
            raise RingCaptureError(f"journal line {line_number} lacks monotonic timestamp")
        try:
            if int(mono_usec) < 0:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise RingCaptureError(f"journal line {line_number} has invalid monotonic timestamp") from exc
        if record.get("_TRANSPORT") != "kernel":
            raise RingCaptureError(f"journal line {line_number} is not kernel transport")
        message = record.get("MESSAGE", "")
        if not isinstance(message, str):
            raise RingCaptureError(f"journal line {line_number} has a non-string MESSAGE")
        if int(mono_usec) <= cutoff_usec and KERNEL_LOG_LOSS.search(message):
            raise RingCaptureError("kernel journal reports /dev/kmsg message loss before snapshot cutoff")
        if isinstance(message, str) and any(tag in message for tag in REPLACED_JOURNAL_TAGS):
            raise RingCaptureError("journal contains ambient map/reset printk records replaced by the ring")
        boot_ids.add(boot_id)
        records_seen += 1
        if int(mono_usec) <= cutoff_usec:
            records.append(record)

    if not records:
        raise RingCaptureError("kernel journal is empty")
    if len(boot_ids) != 1:
        raise RingCaptureError("kernel journal must contain exactly one boot ID")
    return next(iter(boot_ids)), records, records_seen


def merged_records(
    ring_header: dict[str, int],
    ring_events: list[dict[str, Any]],
    boot_id: str,
    journal_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged = list(journal_records)
    for event in ring_events:
        merged.append({
            "_BOOT_ID": boot_id,
            "_TRANSPORT": "kernel",
            "__MONOTONIC_TIMESTAMP": str(event["mono_ns"] // 1000),
            "NOUVEAU_DIAG_RING_MONO_NS": str(event["mono_ns"]),
            "SYSLOG_IDENTIFIER": "nouveau-bar2-event-ring",
            "MESSAGE": event["message"],
        })
    merged.sort(key=lambda row: int(row["__MONOTONIC_TIMESTAMP"]))
    return merged


def write_atomically(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def export_ring(ring_path: Path, journal_path: Path, output_path: Path) -> dict[str, Any]:
    header, events = parse_ring(ring_path)
    boot_id, journal_records, journal_records_seen = load_journal(
        journal_path, header["cutoff_ns"],
    )
    rows = merged_records(header, events, boot_id, journal_records)
    output_payload = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    ).encode("utf-8")
    write_atomically(output_path, output_payload)

    metadata = {
        "schema": 1,
        "transport": "preallocated-nouveau-kernel-ring",
        "boot_id": boot_id,
        "ring_capacity": header["capacity"],
        "ring_head": header["head"],
        "ring_end_head": header["end_head"],
        "ring_appends_during_snapshot": header["end_head"] - header["head"],
        "ring_cutoff_ns": header["cutoff_ns"],
        "ring_captured": header["captured"],
        "ring_dropped_at_snapshot": header["dropped"],
        "ring_dropped_after_snapshot": header["end_dropped"] - header["dropped"],
        "ring_loss_proven_through_cutoff": header["dropped"] == 0,
        "ring_sha256": sha256_file(ring_path),
        "kernel_journal_sha256": sha256_file(journal_path),
        "kernel_journal_records_seen": journal_records_seen,
        "kernel_journal_records_through_cutoff": len(journal_records),
        "kernel_journal_cutoff_usec": header["cutoff_ns"] // 1000,
        "merged_journal_sha256": hashlib.sha256(output_payload).hexdigest(),
        "merged_record_count": len(rows),
    }
    metadata_path = output_path.with_suffix(output_path.suffix + ".capture.json")
    write_atomically(
        metadata_path,
        (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ring", type=Path, required=True)
    parser.add_argument("--kernel-journal", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    try:
        metadata = export_ring(args.ring, args.kernel_journal, args.output)
    except (OSError, RingCaptureError) as exc:
        parser.error(str(exc))
    print(json.dumps(metadata, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
