#!/usr/bin/env python3
"""Fail-closed correlator for the NVIF lifetime A/B journal capture.

Input is line-delimited JSON from ``journalctl -o json`` containing both the
userspace Mesa diagnostic messages and Nouveau kernel messages. The script
only recognizes the pinned diagnostic record formats and requires matching
run manifests before reporting an A/B lifecycle pattern. It does not claim
visible playback or release readiness.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, TextIO


PREFIXES = {
    "new": "NOUVEAU_DIAG_NVIF_NEW",
    "delete": "NOUVEAU_DIAG_NVIF_DEL",
    "channel_free": "NOUVEAU_DIAG_NVIF_CHANNEL_FREE",
    "duplicate": "NOUVEAU_DIAG_NVIF_DUP",
}
REQUIRED_FIELDS = {
    "new": {
        "key", "obj", "object_token", "route_token", "parent",
        "parent_handle", "fd", "class", "ret",
    },
    "delete": {
        "key", "obj", "parent", "parent_handle", "object_handle",
        "fd", "drm_fd", "class", "ret",
    },
    "channel_free": {"channel", "fd", "ret"},
    "duplicate": {"layer", "key", "class"},
}
HEX_FIELDS = {
    "key", "obj", "object_token", "route_token", "parent",
    "parent_handle", "object_handle", "channel", "class",
}
DECIMAL_FIELDS = {"fd", "drm_fd", "ret"}
FIELD_RE = re.compile(r"([A-Za-z_][A-Za-z_0-9]*)=([^\s]+)")
ERRNO_EEXIST = -17
BSP_CLASS = 0x95B1
EXPECTED_INPUT_SHA256 = (
    "d1bab5275bcb585791fbfb15c801c1aab582256e7b7fca280c76f78a0a1c1ec2"
)
EXPECTED_KERNEL = "7.0.0-34-generic"
EXPECTED_NOUVEAU_SRCVERSION = "29C4D0E409ABB2711FC9A10"
EXPECTED_DSO_SHA256 = {
    "A": "1d8f71c5ad0884a43496cfb199e7a38eb5528441e3f24daf4dbf3f7a7c4a28f9",
    "B": "0fe4c64811e3bc77ddd00842796dbd9a8c3906599c74612ec81433ba6ebfd056",
}


@dataclass(frozen=True)
class Event:
    sequence: int
    monotonic_usec: int
    boot_id: str
    pid: int | None
    tid: int | None
    comm: str | None
    kind: str
    fields: dict[str, int | str]


@dataclass(frozen=True)
class SequenceMatch:
    key: int
    pid: int
    first_new_usec: int
    delete_usec: int
    channel_free_usec: int
    second_new_usec: int
    route_token: int
    duplicate_layer: str | None


def _int_field(record: dict[str, Any], name: str, *, required: bool) -> int | None:
    value = record.get(name)
    if value is None:
        if required:
            raise ValueError(f"journal record lacks {name}")
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"journal field {name} is not an integer: {value!r}") from exc


def _parse_number(name: str, value: str) -> int:
    if name in HEX_FIELDS:
        if value == "(nil)":
            return 0
        try:
            return int(value, 0)
        except ValueError as exc:
            raise ValueError(f"field {name} is not hexadecimal: {value!r}") from exc
    if name in DECIMAL_FIELDS:
        try:
            return int(value, 10)
        except ValueError as exc:
            raise ValueError(f"field {name} is not decimal: {value!r}") from exc
    return value  # layer


def _parse_event(record: dict[str, Any], sequence: int) -> Event | None:
    message = record.get("MESSAGE")
    if not isinstance(message, str):
        return None

    tagged = [
        (name, prefix, message.find(prefix))
        for name, prefix in PREFIXES.items()
        if prefix in message
        and (
            message.find(prefix) == 0
            or message[message.find(prefix) - 1].isspace()
        )
    ]
    if not tagged:
        return None
    if len(tagged) != 1:
        raise ValueError("journal message contains multiple NVIF diagnostic tags")
    kind, prefix, tag_position = tagged[0]

    suffix = message[tag_position + len(prefix):].strip()
    fields: dict[str, str] = {}
    for token in suffix.split():
        match = FIELD_RE.fullmatch(token)
        if match is None:
            raise ValueError(f"malformed field token in {prefix}: {token!r}")
        name, value = match.groups()
        if name in fields:
            raise ValueError(f"duplicate field {name} in diagnostic record")
        fields[name] = value

    missing = REQUIRED_FIELDS[kind] - fields.keys()
    if missing:
        raise ValueError(
            f"{prefix} record missing fields: {', '.join(sorted(missing))}"
        )
    parsed = {name: _parse_number(name, value) for name, value in fields.items()}
    if kind in {"new", "delete", "duplicate"}:
        if parsed.get("class") != BSP_CLASS:
            return None
    if kind == "duplicate":
        if parsed["layer"] not in {"abi16", "nvkm"}:
            raise ValueError(f"unknown duplicate layer: {parsed['layer']!r}")
        if parsed["layer"] == "abi16" and "channel" not in parsed:
            raise ValueError("ABI16 duplicate record lacks its channel token")

    mono = _int_field(record, "__MONOTONIC_TIMESTAMP", required=True)
    if mono is None or mono < 0:
        raise ValueError("journal record has an invalid monotonic timestamp")
    pid = _int_field(record, "_PID", required=kind != "duplicate")
    tid = _int_field(record, "TID", required=False)
    boot_id = record.get("_BOOT_ID")
    if not isinstance(boot_id, str) or not boot_id:
        raise ValueError("journal record lacks _BOOT_ID")
    comm = record.get("_COMM")
    return Event(
        sequence=sequence,
        monotonic_usec=mono,
        boot_id=boot_id,
        pid=pid,
        tid=tid,
        comm=comm if isinstance(comm, str) else None,
        kind=kind,
        fields=parsed,
    )


def read_events(stream: TextIO) -> list[Event]:
    events: list[Event] = []
    for sequence, line in enumerate(stream):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid journal JSON at line {sequence + 1}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"journal line {sequence + 1} is not an object")
        event = _parse_event(record, sequence)
        if event is not None:
            events.append(event)
    boot_ids = {event.boot_id for event in events}
    if len(boot_ids) > 1:
        raise ValueError(
            "journal capture contains diagnostic events from multiple boots"
        )
    return sorted(events, key=lambda event: (event.monotonic_usec, event.sequence))


def _between(event: Event, start: Event, end: Event) -> bool:
    event_position = (event.monotonic_usec, event.sequence)
    start_position = (start.monotonic_usec, start.sequence)
    end_position = (end.monotonic_usec, end.sequence)
    return start_position < event_position < end_position


def _matching_chains(events: list[Event], variant: str) -> list[SequenceMatch]:
    if variant not in {"A", "B"}:
        raise ValueError("variant must be A or B")
    userspace = [event for event in events if event.kind != "duplicate"]
    duplicates = [event for event in events if event.kind == "duplicate"]
    groups: dict[tuple[int, int, int], list[Event]] = {}
    for event in userspace:
        if event.kind != "new":
            continue
        if event.pid is None:
            raise ValueError("userspace NVIF record lacks a journal PID")
        key = (event.pid, int(event.fields["key"]), int(event.fields["class"]))
        groups.setdefault(key, []).append(event)

    matches: list[SequenceMatch] = []
    for (pid, key, _oclass), news in groups.items():
        news.sort(key=lambda event: (event.monotonic_usec, event.sequence))
        for first, second in zip(news, news[1:]):
            if int(first.fields["ret"]) != 0:
                continue
            if any((
                int(new.fields["obj"]) != key
                or int(new.fields["key"]) != key
                or int(new.fields["object_token"]) != key
                or int(new.fields["route_token"]) != int(new.fields["parent_handle"])
            ) for new in (first, second)):
                continue

            deletes = [
                event for event in userspace
                if event.kind == "delete"
                and event.pid == pid
                and int(event.fields["key"]) == key
                and int(event.fields["obj"]) == int(first.fields["obj"])
                and int(event.fields["parent"]) == int(first.fields["parent"])
                and _between(event, first, second)
            ]
            for deletion in deletes:
                frees = [
                    event for event in userspace
                    if event.kind == "channel_free"
                    and event.pid == pid
                    and int(event.fields["channel"])
                    == int(deletion.fields["parent_handle"])
                    and int(event.fields["fd"]) == int(deletion.fields["drm_fd"])
                    and int(event.fields["ret"]) == 0
                    and _between(event, deletion, second)
                ]
                for free in frees:
                    duplicate = next((
                        event for event in duplicates
                        if int(event.fields["key"]) == key
                        and int(event.fields["class"]) == BSP_CLASS
                        and (
                            "channel" not in event.fields
                            or int(event.fields["channel"])
                            == int(second.fields["route_token"])
                        )
                        and _between(event, free, second)
                    ), None)

                    if variant == "A":
                        accepted = (
                            int(deletion.fields["fd"]) != int(deletion.fields["drm_fd"])
                            and int(deletion.fields["ret"]) != 0
                            and int(second.fields["ret"]) == ERRNO_EEXIST
                            and duplicate is not None
                        )
                    else:
                        accepted = (
                            int(deletion.fields["fd"]) == int(deletion.fields["drm_fd"])
                            and int(deletion.fields["ret"]) == 0
                            and int(second.fields["ret"]) == 0
                            and duplicate is None
                        )
                    if accepted:
                        matches.append(SequenceMatch(
                            key=key,
                            pid=pid,
                            first_new_usec=first.monotonic_usec,
                            delete_usec=deletion.monotonic_usec,
                            channel_free_usec=free.monotonic_usec,
                            second_new_usec=second.monotonic_usec,
                            route_token=int(second.fields["route_token"]),
                            duplicate_layer=(
                                str(duplicate.fields["layer"])
                                if duplicate is not None else None
                            ),
                        ))
    return matches


def analyze(events: list[Event], variant: str) -> dict[str, Any]:
    matches = _matching_chains(events, variant)
    if matches:
        result = (
            "BASELINE_REPRODUCED_EEXIST_CHAIN" if variant == "A"
            else "CANDIDATE_LIFECYCLE_SUCCEEDED"
        )
    else:
        result = (
            "INCONCLUSIVE_BASELINE_NOT_REPRODUCED" if variant == "A"
            else "INCONCLUSIVE_CANDIDATE_SHAPE_MISMATCH"
        )
    return {
        "variant": variant,
        "result": result,
        "recognized_events": len(events),
        "matching_lifecycles": [asdict(match) for match in matches],
        "claim_boundary": (
            "Per-run sequence evidence only. The paired comparator validates run "
            "manifests; neither result establishes visible playback or release readiness."
        ),
    }


def read_manifest(path: Path, expected_variant: str) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {expected_variant} run manifest: {exc}") from exc
    required = {
        "schema", "variant", "boot_id", "input_sha256", "command_argv",
        "normalized_environment", "kernel", "nouveau_srcversion", "dso_sha256",
    }
    if not isinstance(manifest, dict) or required - manifest.keys():
        missing = (
            sorted(required - manifest.keys())
            if isinstance(manifest, dict)
            else sorted(required)
        )
        raise ValueError(
            f"{expected_variant} manifest lacks fields: {', '.join(missing)}"
        )
    if manifest["schema"] != 1 or manifest["variant"] != expected_variant:
        raise ValueError(f"{expected_variant} manifest schema/variant mismatch")
    for field in ("boot_id", "kernel", "nouveau_srcversion", "dso_sha256"):
        if not isinstance(manifest[field], str) or not manifest[field]:
            raise ValueError(f"{expected_variant} manifest has invalid {field}")
    if manifest["kernel"] != EXPECTED_KERNEL:
        raise ValueError(f"{expected_variant} manifest kernel is not the pinned build")
    if manifest["nouveau_srcversion"] != EXPECTED_NOUVEAU_SRCVERSION:
        raise ValueError(
            f"{expected_variant} manifest Nouveau srcversion is not the pinned build"
        )
    if manifest["input_sha256"] != EXPECTED_INPUT_SHA256:
        raise ValueError(
            f"{expected_variant} manifest input SHA-256 is not the pinned file"
        )
    if manifest["dso_sha256"] != EXPECTED_DSO_SHA256[expected_variant]:
        raise ValueError(
            f"{expected_variant} manifest DSO SHA-256 is not the pinned artifact"
        )
    argv = manifest["command_argv"]
    environment = manifest["normalized_environment"]
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(arg, str) for arg in argv)
    ):
        raise ValueError(
            f"{expected_variant} manifest command_argv must be a non-empty string list"
        )
    if not isinstance(environment, dict) or not environment or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in environment.items()
    ):
        raise ValueError(
            f"{expected_variant} manifest normalized_environment must be "
            "a non-empty string map"
        )
    return manifest


def compare_runs(
    events_a: list[Event],
    events_b: list[Event],
    manifest_a: dict[str, Any],
    manifest_b: dict[str, Any],
) -> dict[str, Any]:
    same_fields = (
        "input_sha256",
        "command_argv",
        "normalized_environment",
        "kernel",
        "nouveau_srcversion",
    )
    mismatched = [
        field for field in same_fields
        if manifest_a[field] != manifest_b[field]
    ]
    if mismatched:
        raise ValueError(f"A/B manifest mismatch: {', '.join(mismatched)}")
    if manifest_a["boot_id"] == manifest_b["boot_id"]:
        raise ValueError("A and B must use separate boots")

    for variant, events, manifest in (
        ("A", events_a, manifest_a),
        ("B", events_b, manifest_b),
    ):
        observed_boots = {event.boot_id for event in events}
        if observed_boots and observed_boots != {manifest["boot_id"]}:
            raise ValueError(f"{variant} journal boot ID does not match its manifest")

    result_a = analyze(events_a, "A")
    result_b = analyze(events_b, "B")
    if result_a["result"] != "BASELINE_REPRODUCED_EEXIST_CHAIN":
        outcome = "INCONCLUSIVE_BASELINE_NOT_REPRODUCED"
    elif result_b["result"] != "CANDIDATE_LIFECYCLE_SUCCEEDED":
        outcome = "INCONCLUSIVE_CANDIDATE_LIFECYCLE_NOT_CLEAN"
    else:
        outcome = "A_REPRODUCED_B_LIFECYCLE_SUCCEEDED"
    return {
        "outcome": outcome,
        "variant_a": result_a,
        "variant_b": result_b,
        "manifest_comparison": {
            "same_input_command_environment_kernel_and_srcversion": True,
            "separate_boots": True,
            "pinned_dso_hashes": True,
        },
        "claim_boundary": (
            "Matched NVIF lifecycle evidence only; this is not visible playback "
            "acceptance or release approval. Confirm manifests from observed "
            "runtime provenance."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal-a", type=Path, required=True)
    parser.add_argument("--manifest-a", type=Path, required=True)
    parser.add_argument("--journal-b", type=Path, required=True)
    parser.add_argument("--manifest-b", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        with args.journal_a.open(encoding="utf-8") as stream:
            events_a = read_events(stream)
        with args.journal_b.open(encoding="utf-8") as stream:
            events_b = read_events(stream)
        manifest_a = read_manifest(args.manifest_a, "A")
        manifest_b = read_manifest(args.manifest_b, "B")
        result = compare_runs(events_a, events_b, manifest_a, manifest_b)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
