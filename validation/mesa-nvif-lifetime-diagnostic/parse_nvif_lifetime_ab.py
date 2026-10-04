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
import hashlib
import io
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
        "key", "parent_handle", "route_token", "selected_fd",
        "drm_fd", "class", "ret",
    },
    "delete": {
        "key", "parent_handle", "object_handle", "selected_fd",
        "drm_fd", "class", "ret",
    },
    "channel_free": {"channel", "fd", "ret"},
    "duplicate": {"layer", "key", "class"},
}
HEX_FIELDS = {
    "key", "route_token", "parent_handle", "object_handle", "channel",
    "class",
}
DECIMAL_FIELDS = {"fd", "drm_fd", "selected_fd", "ret"}
FIELD_RE = re.compile(r"([A-Za-z_][A-Za-z_0-9]*)=([^\s]+)")
ERRNO_EEXIST = -17
BSP_CLASS = 0x95B1
VALID_TERMINATION_REASONS = frozenset({
    "process-exit",
    "bsp-eexist",
    "kernel-hard-stop",
    "workload-hard-stop",
    "deadline",
    "monitor-error",
})
EXPECTED_INPUT_SHA256 = (
    "d1bab5275bcb585791fbfb15c801c1aab582256e7b7fca280c76f78a0a1c1ec2"
)
EXPECTED_KERNEL = "7.0.0-34-generic"
EXPECTED_NOUVEAU_SRCVERSION = "936407678F3DA1E8515F5EC"
EXPECTED_KERNEL_SOURCE_ARCHIVE_SHA256 = (
    "a874e1fb08d2ee695b08e0c8ce6fd2c76a4bf7ffa98882fbabd233380ef8a85a"
)
EXPECTED_KERNEL_PATCH_SHA256 = {
    "0009-drm-nouveau-log-nvif-duplicate-layer.patch":
        "442b14bfb53201dc7c9e7ac8bcfd6a1f74bb94280a8d766ffc119bcf20d327fc",
    "0010-drm-nouveau-correlate-instmem-vma-selftest.patch":
        "472886814c7dd67ff2685bc50791030f24f4267f6a22be16f57ce5c9e1b4818e",
}
EXPECTED_RUNTIME_PROFILE_SHA256 = (
    "697f1192da6c8e02cd63cbcad89dc2ba7453d0dff79990b9dc170ad951693438"
)
EXPECTED_DSO_SHA256 = {
    "A": "2a23055f8fb06d67f43b3c0e046111758ecc21b3fa285516c401cbcd2f3e9b7a",
    "B": "4b334f9ee31d54ee8fc4c043889c9c554e02fb665a62ee29ff8d1af735dce152",
}
RUNTIME_PROFILE_PATH = Path(__file__).with_name("runtime-profile.json")
REQUIRED_ENVIRONMENT_KEYS = {
    "DISPLAY", "XAUTHORITY", "LIBVA_DRIVER_NAME", "LIBVA_DRIVERS_PATH",
    "HOME", "PATH", "LC_ALL", "LD_LIBRARY_PATH", "LD_PRELOAD",
    "LIBVA_TRACE", "LIBVA_MESSAGING_LEVEL", "MESA_LOADER_DRIVER_OVERRIDE",
    "MESA_DEBUG", "NOUVEAU_DEBUG", "WAYLAND_DISPLAY",
}
KERNEL_HARD_STOP_PATTERNS = {
    "CTXSW_TIMEOUT": re.compile(r"CTXSW_TIMEOUT|SCHED_ERROR\s+0a\b", re.I),
    "PRIV_VIOLATION": re.compile(r"\bPRIV[_ ]VIOLATION\b", re.I),
    "BAR2": re.compile(r"\bBAR2\b", re.I),
    "HOST_CPU": re.compile(r"\bHOST_CPU\b", re.I),
    "PTE": re.compile(r"\bPTE\b", re.I),
    "GPU-reset": re.compile(
        r"\b(?:GPU[_ -]?reset|reset(?:ting)?\s+(?:the\s+)?GPU)\b", re.I
    ),
    "failed-to-idle": re.compile(r"failed\s+to\s+idle", re.I),
    "channel-killed": re.compile(r"\bchannel\b.*\bkilled\b", re.I),
    "kernel-BUG": re.compile(r"\bBUG:", re.I),
    "kernel-Oops": re.compile(r"\bOops:", re.I),
    "kernel-WARNING": re.compile(r"\bWARNING:", re.I),
    "sanitizer": re.compile(r"\b(?:KASAN|KCSAN|KFENCE|UBSAN):", re.I),
    "lockdep": re.compile(r"\blockdep\b", re.I),
    "PROP-RT-overrun": re.compile(r"RT_(?:WIDTH|HEIGHT)_OVERRUN", re.I),
    "SIGBUS": re.compile(r"\bSIGBUS\b|\bBus error\b", re.I),
}
WORKLOAD_FATAL_PATTERNS = {
    "SIGBUS": re.compile(r"\bSIGBUS\b|\bBus error\b", re.I),
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
    delete_ret: int
    channel_free_usec: int
    second_new_usec: int
    route_token: int
    duplicate_layer: str | None


def load_runtime_profile() -> dict[str, Any]:
    try:
        profile_bytes = RUNTIME_PROFILE_PATH.read_bytes()
        profile = json.loads(profile_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read pinned runtime profile: {exc}") from exc
    if hashlib.sha256(profile_bytes).hexdigest() != EXPECTED_RUNTIME_PROFILE_SHA256:
        raise ValueError("pinned runtime profile SHA-256 mismatch")
    if not isinstance(profile, dict) or profile.get("schema") != 1:
        raise ValueError("unsupported runtime profile schema")
    if not isinstance(profile.get("argv"), list) or not profile["argv"] or not all(
        isinstance(arg, str) for arg in profile["argv"]
    ):
        raise ValueError("pinned runtime profile argv is invalid")
    if profile.get("input_sha256") != EXPECTED_INPUT_SHA256:
        raise ValueError("pinned runtime profile input hash mismatch")
    historical = profile.get("historical_capture")
    if not isinstance(historical, dict):
        raise ValueError("pinned runtime profile historical capture is invalid")
    historical_argv = historical.get("command_argv")
    if not isinstance(historical_argv, list) or not all(
        isinstance(arg, str) for arg in historical_argv
    ):
        raise ValueError("pinned historical command argv is invalid")
    if historical_argv[-1:] != [profile["argv"][-1]]:
        raise ValueError("historical command does not use the pinned input")
    try:
        args_index = historical_argv.index("--args")
    except ValueError as exc:
        raise ValueError("historical GDB command lacks --args boundary") from exc
    if historical_argv[args_index + 1:] != profile["argv"]:
        raise ValueError("historical GDB command does not contain the pinned MPV argv")
    if historical.get("failure_timestamp") != "not recorded in the saved GDB log":
        raise ValueError("historical failure-time evidence changed")
    controlled = profile.get("controlled_execution")
    if not isinstance(controlled, dict) or not isinstance(
        controlled.get("working_directory"), str
    ) or not isinstance(controlled.get("home"), str):
        raise ValueError("controlled execution profile is invalid")
    return profile


def journal_records(data: bytes) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index, raw_line in enumerate(data.splitlines(), 1):
        if not raw_line:
            continue
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid journal JSON at line {index}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"journal line {index} is not a JSON object")
        records.append(record)
    return records


def journal_messages(data: bytes) -> list[str]:
    return [
        record["MESSAGE"]
        for record in journal_records(data)
        if isinstance(record.get("MESSAGE"), str)
    ]


def journal_record_hard_stop_source(record: dict[str, Any]) -> str | None:
    """Identify kernel versus pinned-workload records from journal metadata."""
    if record.get("_TRANSPORT") == "kernel":
        return "kernel"
    if record.get("SYSLOG_IDENTIFIER") == "nouveau-nvif-lifetime":
        return "workload"
    return None


def journal_record_hard_stop_kinds(record: dict[str, Any]) -> tuple[str, ...]:
    """Classify a stop only when journal metadata identifies its source."""
    message = record.get("MESSAGE")
    if not isinstance(message, str):
        return ()

    source = journal_record_hard_stop_source(record)
    if source == "kernel":
        patterns = KERNEL_HARD_STOP_PATTERNS
    elif source == "workload":
        patterns = WORKLOAD_FATAL_PATTERNS
    else:
        return ()

    return tuple(
        name for name, pattern in patterns.items()
        if pattern.search(message)
    )


def hard_stop_records(data: bytes) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for journal_record in journal_records(data):
        message = journal_record.get("MESSAGE")
        if not isinstance(message, str):
            continue
        kinds = journal_record_hard_stop_kinds(journal_record)
        if kinds:
            records.append({
                "source": journal_record_hard_stop_source(journal_record),
                "kinds": list(kinds),
                "message": message,
            })
    return records


def is_bsp_new_eexist(message: str) -> bool:
    """Recognize the failure marker used to stop A before retry churn."""
    prefix = PREFIXES["new"]
    position = message.find(prefix)
    if position < 0:
        return False
    if position and not message[position - 1].isspace():
        return False
    fields: dict[str, str] = {}
    for token in message[position + len(prefix):].strip().split():
        match = FIELD_RE.fullmatch(token)
        if match is None:
            raise ValueError(f"malformed field token in {prefix}: {token!r}")
        name, value = match.groups()
        if name in fields:
            raise ValueError(f"duplicate field {name} in {prefix} record")
        fields[name] = value
    if not {"class", "ret"} <= fields.keys():
        raise ValueError("NVIF NEW failure record lacks class or return status")
    return (
        _parse_number("class", fields["class"]) == BSP_CLASS
        and _parse_number("ret", fields["ret"]) == ERRNO_EEXIST
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_termination_reason_sources(
    termination_reason: str,
    hard_stops: list[dict[str, Any]],
    *,
    variant: str,
) -> None:
    sources: set[str] = set()
    for index, record in enumerate(hard_stops):
        if not isinstance(record, dict):
            raise ValueError(
                f"{variant} postrun hard-stop record {index} is not an object"
            )
        source = record.get("source")
        if not isinstance(source, str) or source not in {"kernel", "workload"}:
            raise ValueError(
                f"{variant} postrun hard-stop record {index} has an invalid source"
            )
        sources.add(source)

    if termination_reason == "kernel-hard-stop" and "kernel" not in sources:
        raise ValueError(
            f"{variant} kernel-hard-stop termination lacks a kernel hard-stop record"
        )
    if termination_reason == "workload-hard-stop" and "workload" not in sources:
        raise ValueError(
            f"{variant} workload-hard-stop termination lacks a workload hard-stop record"
        )


def journal_boot_ids(data: bytes) -> set[str]:
    boot_ids: set[str] = set()
    for index, raw_line in enumerate(data.splitlines(), 1):
        if not raw_line:
            continue
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid journal JSON at line {index}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"journal line {index} is not a JSON object")
        boot_id = record.get("_BOOT_ID")
        if not isinstance(boot_id, str) or not boot_id:
            raise ValueError(f"journal line {index} lacks _BOOT_ID")
        boot_ids.add(boot_id)
    if not boot_ids:
        raise ValueError("journal capture contains no records with boot identity")
    return boot_ids


def journal_has_kernel_records(data: bytes) -> bool:
    for index, raw_line in enumerate(data.splitlines(), 1):
        if not raw_line:
            continue
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid journal JSON at line {index}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"journal line {index} is not a JSON object")
        if record.get("_TRANSPORT") == "kernel":
            return True
    return False


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
    allowed = REQUIRED_FIELDS[kind] | ({"channel"} if kind == "duplicate" else set())
    unexpected = fields.keys() - allowed
    if unexpected:
        raise ValueError(
            f"{prefix} record has unexpected fields: "
            f"{', '.join(sorted(unexpected))}"
        )
    parsed = {name: _parse_number(name, value) for name, value in fields.items()}
    if kind == "new" and parsed["selected_fd"] != parsed["drm_fd"]:
        raise ValueError("NEW selected fd must equal the canonical DRM fd")
    if kind == "new" and parsed["route_token"] != parsed["parent_handle"]:
        raise ValueError("NEW route token must equal the numeric parent handle")
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


def _matching_chains(
    events: list[Event],
    variant: str,
    *,
    duplicate_layers: set[str] | None = None,
) -> list[SequenceMatch]:
    if variant not in {"A", "B"}:
        raise ValueError("variant must be A or B")
    if duplicate_layers is None:
        duplicate_layers = {"abi16"} if variant == "A" else {"abi16", "nvkm"}
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
                int(new.fields["key"]) != key
                or int(new.fields["route_token"])
                != int(new.fields["parent_handle"])
            ) for new in (first, second)):
                continue

            deletes = [
                event for event in userspace
                if event.kind == "delete"
                and event.pid == pid
                and int(event.fields["key"]) == key
                and int(event.fields["class"]) == BSP_CLASS
                and int(event.fields["parent_handle"])
                == int(first.fields["parent_handle"])
                and _between(event, first, second)
            ]
            for deletion in deletes:
                canonical_fd = int(deletion.fields["drm_fd"])
                if (
                    int(first.fields["drm_fd"]) != canonical_fd
                    or int(second.fields["drm_fd"]) != canonical_fd
                ):
                    continue
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
                        and str(event.fields["layer"]) in duplicate_layers
                        and (
                            "channel" not in event.fields
                            or int(event.fields["channel"])
                            == int(second.fields["route_token"])
                        )
                        and _between(event, free, second)
                    ), None)

                    if variant == "A":
                        accepted = (
                            int(deletion.fields["selected_fd"])
                            != int(deletion.fields["drm_fd"])
                            and int(deletion.fields["ret"]) <= 0
                            and int(second.fields["ret"]) == ERRNO_EEXIST
                            and duplicate is not None
                        )
                    else:
                        accepted = (
                            int(deletion.fields["selected_fd"])
                            == int(deletion.fields["drm_fd"])
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
                            delete_ret=int(deletion.fields["ret"]),
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
    weak_nvkm_matches: list[SequenceMatch] = []
    if variant == "A" and not matches:
        weak_nvkm_matches = _matching_chains(
            events,
            variant,
            duplicate_layers={"nvkm"},
        )
    if matches:
        if variant == "A":
            result = (
                "BASELINE_REPRODUCED_EEXIST_AFTER_ZERO_RETURN_DEL"
                if all(match.delete_ret == 0 for match in matches)
                else "BASELINE_REPRODUCED_EEXIST_CHAIN"
            )
        else:
            result = "CANDIDATE_LIFECYCLE_SUCCEEDED"
    elif weak_nvkm_matches:
        result = "INCONCLUSIVE_NVKM_DUPLICATE_IDENTITY_WEAK"
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
        "weak_nvkm_lifecycles": [asdict(match) for match in weak_nvkm_matches],
        "claim_boundary": (
            "Per-run sequence evidence only. The paired comparator validates run "
            "manifests; neither result establishes visible playback or release readiness."
        ),
    }


def _run_hard_stops(
    manifest: dict[str, Any], external: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for source in (
        manifest["preflight_hard_stops"],
        manifest["postrun_hard_stops"],
        external or [],
    ):
        for record in source:
            if record not in records:
                records.append(record)
    return records


def _journal_boundary_valid(manifest: dict[str, Any]) -> bool:
    return bool(manifest["journal_boundary_proven"]) and not manifest[
        "journal_monitor_error"
    ]


def classify_a_run(
    events: list[Event],
    manifest: dict[str, Any],
    hard_stops: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    evidence = analyze(events, "A")
    stops = _run_hard_stops(manifest, hard_stops)
    clean = (
        _journal_boundary_valid(manifest)
        and not stops
        and not manifest["workload_timed_out"]
    )
    chain = evidence["result"] in {
        "BASELINE_REPRODUCED_EEXIST_CHAIN",
        "BASELINE_REPRODUCED_EEXIST_AFTER_ZERO_RETURN_DEL",
    }
    if (
        clean
        and chain
        and manifest["termination_reason"] == "bsp-eexist"
        and manifest["workload_returncode"] != 0
    ):
        outcome = "A_CHAIN_PROVEN"
    elif (
        clean
        and not chain
        and manifest["termination_reason"] == "process-exit"
        and manifest["workload_returncode"] == 0
    ):
        outcome = "A_CHAIN_NOT_REPRODUCED"
    else:
        outcome = "A_INCONCLUSIVE"

    return {
        "outcome": outcome,
        "evidence": evidence,
        "hard_stop_records": stops,
        "run_valid": clean,
        "claim_boundary": (
            "A_CHAIN_PROVEN is an observed key/fd/result/order chain in the "
            "Nouveau diagnostics. It does not prove visible playback or a fix."
        ),
    }


SINGLE_A_IDENTITY_FIELDS = (
    "boot_id", "input_sha256", "command_argv", "normalized_environment",
    "deployment_manifest_path", "deployment_manifest_sha256",
    "nvif_capture_sha256", "nvif_parser_sha256",
    "kernel", "runtime_profile_sha256", "nouveau_srcversion",
    "nouveau_module_path", "nouveau_module_file_sha256",
    "nouveau_module_srcversion", "nouveau_module_vermagic",
    "nouveau_parameters", "mpv_sha256", "mpv_resolved_path",
    "systemd_cat_sha256", "systemd_cat_resolved_path", "working_directory",
    "xauthority_sha256", "dso_sha256",
)


def single_a_report(
    events: list[Event],
    manifest: dict[str, Any],
    manifest_bytes: bytes,
    journal_bytes: bytes,
    hard_stops: list[dict[str, Any]],
) -> dict[str, Any]:
    result = classify_a_run(events, manifest, hard_stops)
    result["schema"] = 1
    result["variant"] = "A"
    result["manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
    result["identity"] = {
        name: manifest[name] for name in SINGLE_A_IDENTITY_FIELDS
    }
    result["journal_delta_sha256"] = hashlib.sha256(journal_bytes).hexdigest()
    return result


def validate_a_proof_files(
    *, proof_path: Path, manifest_path: Path, journal_path: Path,
) -> dict[str, Any]:
    """Recompute and verify the exact A-chain proof before allowing Variant B."""
    try:
        proof_bytes = proof_path.read_bytes()
        proof = json.loads(proof_bytes)
        manifest_bytes = manifest_path.read_bytes()
        manifest = read_manifest(manifest_path, "A")
        journal_bytes = journal_path.read_bytes()
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load Variant A proof artifacts: {exc}") from exc

    if not isinstance(proof, dict):
        raise ValueError("Variant A proof is not a JSON object")
    if hashlib.sha256(journal_bytes).hexdigest() != manifest[
        "journal_delta_sha256"
    ]:
        raise ValueError("Variant A journal delta SHA-256 does not match manifest")
    if journal_boot_ids(journal_bytes) != {manifest["boot_id"]}:
        raise ValueError("Variant A journal boot ID does not match manifest")
    try:
        events = read_events(io.StringIO(journal_bytes.decode("utf-8")))
    except UnicodeDecodeError as exc:
        raise ValueError("Variant A journal is not valid UTF-8") from exc
    hard_stops = hard_stop_records(journal_bytes)
    if hard_stops != manifest["postrun_hard_stops"]:
        raise ValueError("Variant A hard-stop records do not match journal")

    expected = single_a_report(
        events, manifest, manifest_bytes, journal_bytes, hard_stops
    )
    if expected["outcome"] != "A_CHAIN_PROVEN":
        raise ValueError(
            f"Variant A artifacts do not prove the required chain: {expected['outcome']}"
        )
    if proof != expected:
        raise ValueError(
            "Variant A proof does not exactly match recomputed manifest/journal evidence"
        )
    return {
        "proof_sha256": hashlib.sha256(proof_bytes).hexdigest(),
        "manifest_sha256": expected["manifest_sha256"],
        "journal_delta_sha256": expected["journal_delta_sha256"],
        "identity": expected["identity"],
    }

def classify_b_run(
    events: list[Event],
    manifest: dict[str, Any],
    *,
    a_chain_proven: bool,
    hard_stops: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    evidence = analyze(events, "B")
    stops = _run_hard_stops(manifest, hard_stops)
    abi16_duplicates = [
        event for event in events
        if event.kind == "duplicate"
        and str(event.fields["layer"]) == "abi16"
    ]
    clean = (
        _journal_boundary_valid(manifest)
        and not stops
        and not manifest["workload_timed_out"]
        and manifest["termination_reason"] == "process-exit"
        and manifest["workload_returncode"] == 0
    )
    if not a_chain_proven:
        outcome = "A_INCONCLUSIVE"
    elif (
        clean
        and not abi16_duplicates
        and evidence["result"] == "CANDIDATE_LIFECYCLE_SUCCEEDED"
    ):
        outcome = "B_CORRECTION_PROVEN"
    elif clean:
        outcome = "B_CORRECTION_FAILED"
    else:
        outcome = "A_INCONCLUSIVE"

    return {
        "outcome": outcome,
        "evidence": evidence,
        "hard_stop_records": stops,
        "abi16_duplicate_records": [asdict(event) for event in abi16_duplicates],
        "run_valid": clean,
        "a_chain_proven": a_chain_proven,
        "claim_boundary": (
            "B_CORRECTION_PROVEN means only that the matched same-key NVIF "
            "lifecycle changed as predicted. It does not establish visible "
            "playback acceptance. B_CORRECTION_FAILED means B did not meet "
            "those proof criteria; inspect run_valid before inferring why."
        ),
    }


def read_manifest(path: Path, expected_variant: str) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {expected_variant} run manifest: {exc}") from exc
    required = {
        "schema", "variant", "boot_id", "input_sha256", "command_argv",
        "launcher_argv", "normalized_environment", "kernel",
        "runtime_profile_sha256", "nouveau_srcversion", "nouveau_parameters", "nouveau_module_path",
        "nouveau_module_file_sha256", "nouveau_module_srcversion",
        "nouveau_module_vermagic", "deployment_manifest_path",
        "deployment_manifest_sha256", "nvif_capture_sha256",
        "nvif_parser_sha256", "dso_sha256",
        "dso_resolved_path", "dso_alias_path", "mpv_sha256",
        "mpv_resolved_path", "systemd_cat_sha256",
        "systemd_cat_resolved_path", "journal_start_cursor",
        "working_directory", "xauthority_sha256", "termination_reason",
        "journal_boundary_proven", "preflight_journal_path",
        "preflight_journal_sha256",
        "preflight_hard_stops", "journal_delta_sha256", "postrun_hard_stops",
        "workload_returncode", "workload_timed_out", "journal_monitor_error",
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
    if manifest["schema"] != 2 or manifest["variant"] != expected_variant:
        raise ValueError(f"{expected_variant} manifest schema/variant mismatch")
    for field in (
        "boot_id", "kernel", "runtime_profile_sha256", "nouveau_srcversion", "nouveau_module_path",
        "nouveau_module_srcversion", "nouveau_module_vermagic", "dso_sha256",
    ):
        if not isinstance(manifest[field], str) or not manifest[field]:
            raise ValueError(f"{expected_variant} manifest has invalid {field}")
    if manifest["kernel"] != EXPECTED_KERNEL:
        raise ValueError(f"{expected_variant} manifest kernel is not the pinned build")
    if manifest["nouveau_srcversion"] != EXPECTED_NOUVEAU_SRCVERSION:
        raise ValueError(
            f"{expected_variant} manifest Nouveau srcversion is not the pinned build"
        )
    if manifest["nouveau_module_srcversion"] != manifest["nouveau_srcversion"]:
        raise ValueError(
            f"{expected_variant} selected Nouveau module srcversion does not "
            "match the loaded module"
        )
    module_vermagic = manifest["nouveau_module_vermagic"].split()
    if not module_vermagic or module_vermagic[0] != EXPECTED_KERNEL:
        raise ValueError(f"{expected_variant} selected Nouveau module vermagic is not pinned")
    module_path = Path(manifest["nouveau_module_path"])
    if not module_path.is_absolute():
        raise ValueError(f"{expected_variant} selected Nouveau module path must be absolute")
    if not isinstance(manifest["nouveau_module_file_sha256"], str) or not re.fullmatch(
        r"[0-9a-f]{64}", manifest["nouveau_module_file_sha256"]
    ):
        raise ValueError(f"{expected_variant} selected Nouveau module hash is invalid")
    try:
        resolved_module_path = module_path.resolve(strict=True)
        if resolved_module_path != module_path:
            raise ValueError(
                f"{expected_variant} selected Nouveau module path is not canonical"
            )
        if not module_path.is_file() or _sha256_file(module_path) != manifest[
            "nouveau_module_file_sha256"
        ]:
            raise ValueError(
                f"{expected_variant} selected Nouveau module file hash changed"
            )
    except OSError as exc:
        raise ValueError(
            f"{expected_variant} selected Nouveau module file is unavailable: {exc}"
        ) from exc
    profile = load_runtime_profile()
    if manifest["runtime_profile_sha256"] != EXPECTED_RUNTIME_PROFILE_SHA256:
        raise ValueError(f"{expected_variant} runtime profile SHA-256 is not pinned")
    if manifest["input_sha256"] != profile["input_sha256"]:
        raise ValueError(
            f"{expected_variant} manifest input SHA-256 is not the pinned file"
        )
    media = Path(profile["argv"][-1])
    try:
        if not media.is_file() or _sha256_file(media) != profile["input_sha256"]:
            raise ValueError("pinned media file is missing or has changed")
    except OSError as exc:
        raise ValueError(f"cannot verify pinned media: {exc}") from exc
    if manifest["dso_sha256"] != EXPECTED_DSO_SHA256[expected_variant]:
        raise ValueError(
            f"{expected_variant} manifest DSO SHA-256 is not the pinned artifact"
        )
    argv = manifest["command_argv"]
    launcher_argv = manifest["launcher_argv"]
    environment = manifest["normalized_environment"]
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(arg, str) for arg in argv)
    ):
        raise ValueError(
            f"{expected_variant} manifest command_argv must be a non-empty string list"
        )
    if argv != profile["argv"]:
        raise ValueError(f"{expected_variant} command does not match the pinned workload")
    expected_launcher = [
        "/usr/bin/systemd-cat", "--identifier=nouveau-nvif-lifetime", "--", *argv,
    ]
    if launcher_argv != expected_launcher:
        raise ValueError(f"{expected_variant} launcher command is not pinned")
    if not isinstance(environment, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in environment.items()
    ):
        raise ValueError(
            f"{expected_variant} manifest normalized_environment must be "
            "a string map"
        )
    if set(environment) != REQUIRED_ENVIRONMENT_KEYS:
        raise ValueError(f"{expected_variant} environment keys do not match the schema")
    fixed_environment = {
        "DISPLAY": profile["historical_capture"]["observed_environment"]["DISPLAY"],
        "XAUTHORITY": "<session-xauthority>",
        "LIBVA_DRIVER_NAME": "nouveau",
        "LIBVA_DRIVERS_PATH": "<variant-private-directory>",
        "HOME": profile["controlled_execution"]["home"],
        "PATH": "/usr/bin:/bin",
        "LC_ALL": "C",
        "LD_LIBRARY_PATH": "<unset>",
        "LD_PRELOAD": "<unset>",
        "LIBVA_TRACE": "<unset>",
        "LIBVA_MESSAGING_LEVEL": "<unset>",
        "MESA_LOADER_DRIVER_OVERRIDE": "<unset>",
        "MESA_DEBUG": "<unset>",
        "NOUVEAU_DEBUG": "<unset>",
        "WAYLAND_DISPLAY": "<unset>",
    }
    for key, expected_value in fixed_environment.items():
        if environment.get(key) != expected_value:
            raise ValueError(
                f"{expected_variant} normalized environment {key} is not pinned"
            )
    if not environment.get("DISPLAY") or not environment.get("HOME"):
        raise ValueError(f"{expected_variant} environment lacks DISPLAY or HOME")

    parameters = manifest["nouveau_parameters"]
    if parameters != {"diag_ctxsw": "N"}:
        raise ValueError(f"{expected_variant} Nouveau diagnostic parameters are not pinned")
    for name in (
        "dso_resolved_path", "dso_alias_path", "mpv_resolved_path",
        "systemd_cat_resolved_path", "journal_start_cursor",
        "working_directory", "termination_reason",
    ):
        if not isinstance(manifest[name], str) or not manifest[name]:
            raise ValueError(f"{expected_variant} manifest has invalid {name}")
    if manifest["working_directory"] != profile["controlled_execution"]["working_directory"]:
        raise ValueError(f"{expected_variant} working directory is not pinned")
    if manifest["termination_reason"] not in VALID_TERMINATION_REASONS:
        raise ValueError(f"{expected_variant} termination reason is invalid")
    if not isinstance(manifest["xauthority_sha256"], str) or not re.fullmatch(
        r"[0-9a-f]{64}", manifest["xauthority_sha256"]
    ):
        raise ValueError(f"{expected_variant} XAUTHORITY hash is invalid")
    for name in ("mpv_sha256", "systemd_cat_sha256"):
        if not isinstance(manifest[name], str) or not re.fullmatch(
            r"[0-9a-f]{64}", manifest[name]
        ):
            raise ValueError(f"{expected_variant} {name} is invalid")
    for name in ("mpv_resolved_path", "systemd_cat_resolved_path"):
        if not Path(manifest[name]).is_absolute():
            raise ValueError(f"{expected_variant} {name} must be absolute")
    alias = Path(manifest["dso_alias_path"])
    resolved = Path(manifest["dso_resolved_path"])
    try:
        if not alias.is_symlink() or alias.resolve(strict=True) != resolved.resolve(strict=True):
            raise ValueError(f"{expected_variant} VA driver alias does not resolve to its DSO")
        if _sha256_file(resolved) != EXPECTED_DSO_SHA256[expected_variant]:
            raise ValueError(f"{expected_variant} resolved VA DSO hash changed")
    except OSError as exc:
        raise ValueError(f"{expected_variant} VA driver path is unavailable: {exc}") from exc
    if not isinstance(manifest["journal_boundary_proven"], bool):
        raise ValueError(f"{expected_variant} journal boundary field is invalid")
    if not isinstance(manifest["journal_delta_sha256"], str) or not re.fullmatch(
        r"[0-9a-f]{64}", manifest["journal_delta_sha256"]
    ):
        raise ValueError(f"{expected_variant} journal delta hash is invalid")
    if manifest["preflight_journal_path"] != "journal-preflight.jsonl":
        raise ValueError(f"{expected_variant} preflight journal path is not pinned")
    if not isinstance(manifest["preflight_journal_sha256"], str) or not re.fullmatch(
        r"[0-9a-f]{64}", manifest["preflight_journal_sha256"]
    ):
        raise ValueError(f"{expected_variant} preflight journal hash is invalid")
    if not isinstance(manifest["preflight_hard_stops"], list):
        raise ValueError(f"{expected_variant} preflight hard-stop field is invalid")
    preflight_path = path.parent / manifest["preflight_journal_path"]
    try:
        preflight_journal = preflight_path.read_bytes()
    except OSError as exc:
        raise ValueError(f"{expected_variant} preflight journal is unavailable: {exc}") from exc
    if hashlib.sha256(preflight_journal).hexdigest() != manifest["preflight_journal_sha256"]:
        raise ValueError(f"{expected_variant} preflight journal SHA-256 mismatch")
    if journal_boot_ids(preflight_journal) != {manifest["boot_id"]}:
        raise ValueError(f"{expected_variant} preflight journal boot ID mismatch")
    if not journal_has_kernel_records(preflight_journal):
        raise ValueError(f"{expected_variant} preflight journal lacks kernel records")
    observed_preflight_stops = hard_stop_records(preflight_journal)
    if observed_preflight_stops != manifest["preflight_hard_stops"]:
        raise ValueError(f"{expected_variant} preflight hard-stop records mismatch")
    if not isinstance(manifest["postrun_hard_stops"], list):
        raise ValueError(f"{expected_variant} postrun hard-stop field is invalid")
    _validate_termination_reason_sources(
        manifest["termination_reason"],
        manifest["postrun_hard_stops"],
        variant=expected_variant,
    )
    if type(manifest["workload_returncode"]) is not int:
        raise ValueError(f"{expected_variant} workload return code is invalid")
    if not isinstance(manifest["workload_timed_out"], bool):
        raise ValueError(f"{expected_variant} workload timeout field is invalid")
    if manifest["journal_monitor_error"] is not None and not isinstance(
        manifest["journal_monitor_error"], str
    ):
        raise ValueError(f"{expected_variant} journal monitor error is invalid")
    _validate_deployment_provenance(manifest, expected_variant)
    return manifest


def _validate_deployment_provenance(
    run: dict[str, Any], variant: str,
) -> None:
    """Bind a run to the exact finalized module/DSO/tools deployment record."""
    for name in (
        "deployment_manifest_sha256",
        "nvif_capture_sha256",
        "nvif_parser_sha256",
    ):
        value = run.get(name)
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError(f"{variant} manifest has invalid {name}")

    raw_path = run.get("deployment_manifest_path")
    if not isinstance(raw_path, str) or not Path(raw_path).is_absolute():
        raise ValueError(f"{variant} deployment manifest path must be absolute")
    path = Path(raw_path)
    try:
        resolved = path.resolve(strict=True)
        payload = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"{variant} deployment manifest is unavailable: {exc}") from exc
    if resolved != path:
        raise ValueError(f"{variant} deployment manifest path is not canonical")
    if hashlib.sha256(payload).hexdigest() != run["deployment_manifest_sha256"]:
        raise ValueError(f"{variant} deployment manifest SHA-256 mismatch")
    try:
        deployment = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{variant} deployment manifest is invalid JSON") from exc
    if not isinstance(deployment, dict) or deployment.get("schema") != 2:
        raise ValueError(f"{variant} deployment manifest schema is unsupported")
    if deployment.get("status") != "FINALIZED_NOT_REBOOTED_NOT_RUNTIME_VERIFIED":
        raise ValueError(f"{variant} deployment manifest is not finalized")
    if deployment.get("review_branch") != "review/gk104-vaapi-selftest-v8-20261002":
        raise ValueError(f"{variant} deployment manifest review branch is unexpected")
    if deployment.get("main_commit") != "7b44b1c1e282eac7c54c1cfa8c758118cd66312c":
        raise ValueError(f"{variant} deployment manifest main commit is unexpected")
    if deployment.get("kernel") != run["kernel"]:
        raise ValueError(f"{variant} deployment manifest kernel mismatch")
    review_commit = deployment.get("review_commit")
    if not isinstance(review_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", review_commit):
        raise ValueError(f"{variant} deployment manifest review commit is invalid")

    source = deployment.get("source")
    if not isinstance(source, dict):
        raise ValueError(f"{variant} deployment manifest lacks source pins")
    if source.get("kernel_source_archive_sha256") != EXPECTED_KERNEL_SOURCE_ARCHIVE_SHA256:
        raise ValueError(f"{variant} deployment kernel source hash is not pinned")
    if source.get("patch_sha256") != EXPECTED_KERNEL_PATCH_SHA256:
        raise ValueError(f"{variant} deployment kernel patch hashes are not pinned")

    module = deployment.get("nouveau")
    initramfs = deployment.get("initramfs")
    if not isinstance(module, dict) or not isinstance(initramfs, dict):
        raise ValueError(f"{variant} deployment manifest lacks module/initramfs records")
    module_identity = {
        "module_path": run["nouveau_module_path"],
        "installed_compressed_sha256": run["nouveau_module_file_sha256"],
        "srcversion": run["nouveau_module_srcversion"],
        "vermagic": run["nouveau_module_vermagic"],
    }
    for field, expected in module_identity.items():
        if module.get(field) != expected:
            raise ValueError(f"{variant} deployment Nouveau {field} mismatch")
    if module.get("parameters") != run["nouveau_parameters"]:
        raise ValueError(f"{variant} deployment Nouveau parameters mismatch")
    initramfs_path = initramfs.get("path")
    initramfs_sha = initramfs.get("sha256")
    if initramfs_path != "/boot/initrd.img-7.0.0-34-generic":
        raise ValueError(f"{variant} deployment initramfs path is not pinned")
    if not isinstance(initramfs_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", initramfs_sha):
        raise ValueError(f"{variant} deployment initramfs hash is invalid")

    for record_name in ("build_manifest", "deployment_plan"):
        record = deployment.get(record_name)
        if not isinstance(record, dict):
            raise ValueError(f"{variant} deployment lacks {record_name}")
        record_path_text = record.get("path")
        record_hash = record.get("sha256")
        if not isinstance(record_path_text, str) or not Path(record_path_text).is_absolute():
            raise ValueError(f"{variant} deployment {record_name} path is invalid")
        if not isinstance(record_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", record_hash):
            raise ValueError(f"{variant} deployment {record_name} hash is invalid")
        record_path = Path(record_path_text)
        try:
            if (
                record_path.resolve(strict=True) != record_path
                or not record_path.is_file()
                or _sha256_file(record_path) != record_hash
            ):
                raise ValueError(f"{variant} deployment {record_name} hash mismatch")
        except OSError as exc:
            raise ValueError(f"{variant} deployment {record_name} is unavailable: {exc}") from exc
        try:
            record_json = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"{variant} deployment {record_name} is invalid JSON") from exc
        if not isinstance(record_json, dict):
            raise ValueError(f"{variant} deployment {record_name} is not an object")
        if record_name == "build_manifest":
            if (
                record_json.get("status") != "CLEAN_ENABLED_BUILD_RETAINED_NOT_INSTALLED"
                or record_json.get("review_commit") != review_commit
                or record_json.get("review_branch") != deployment["review_branch"]
                or record_json.get("main_commit") != deployment["main_commit"]
                or record_json.get("kernel_release") != deployment["kernel"]
                or record_json.get("kernel_source_archive_sha256")
                != EXPECTED_KERNEL_SOURCE_ARCHIVE_SHA256
                or record_json.get("patch_sha256") != EXPECTED_KERNEL_PATCH_SHA256
                or record_json.get("srcversion") != module.get("srcversion")
                or record_json.get("vermagic") != module.get("vermagic")
                or record_json.get("raw_module_sha256") != module.get("raw_module_sha256")
            ):
                raise ValueError(f"{variant} deployment build manifest identity mismatch")
        elif (
            record_json.get("schema") != 1
            or record_json.get("expected_review_branch") != deployment["review_branch"]
            or record_json.get("expected_main_commit") != deployment["main_commit"]
            or record_json.get("kernel_release") != deployment["kernel"]
            or record_json.get("kernel_source_archive_sha256")
            != EXPECTED_KERNEL_SOURCE_ARCHIVE_SHA256
            or record_json.get("patch_sha256") != EXPECTED_KERNEL_PATCH_SHA256
            or record_json.get("expected_srcversion") != module.get("srcversion")
            or record_json.get("expected_vermagic") != module.get("vermagic")
            or record_json.get("module_install_path") != module.get("module_path")
            or record_json.get("module_parameters") != module.get("parameters")
        ):
            raise ValueError(f"{variant} deployment plan identity mismatch")

    input_record = deployment.get("input")
    profile = load_runtime_profile()
    if (
        not isinstance(input_record, dict)
        or input_record.get("path") != str(Path(profile["argv"][-1]).resolve())
        or input_record.get("sha256") != run["input_sha256"]
        or run["input_sha256"] != EXPECTED_INPUT_SHA256
    ):
        raise ValueError(f"{variant} deployment input provenance mismatch")

    mesa = deployment.get("mesa")
    variants = mesa.get("variants") if isinstance(mesa, dict) else None
    mesa_record = variants.get(variant) if isinstance(variants, dict) else None
    if not isinstance(mesa_record, dict):
        raise ValueError(f"{variant} deployment manifest lacks Mesa variant")
    if mesa_record.get("path") != run["dso_resolved_path"]:
        raise ValueError(f"{variant} deployment Mesa path mismatch")
    if mesa_record.get("sha256") != run["dso_sha256"]:
        raise ValueError(f"{variant} deployment Mesa DSO hash mismatch")
    plan_variants = json.loads(
        Path(deployment["deployment_plan"]["path"]).read_text(encoding="utf-8")
    ).get("mesa_variants")
    if not isinstance(plan_variants, dict) or plan_variants.get(variant) != mesa_record:
        raise ValueError(f"{variant} deployment plan Mesa variant mismatch")

    tools = deployment.get("tools")
    if not isinstance(tools, dict):
        raise ValueError(f"{variant} deployment manifest lacks tool provenance")
    expected_tools = {
        "nvif_capture": (
            Path(__file__).with_name("capture_nvif_lifetime_run.py"),
            run["nvif_capture_sha256"],
        ),
        "nvif_parser": (Path(__file__), run["nvif_parser_sha256"]),
    }
    for name, (expected_path, expected_hash) in expected_tools.items():
        record = tools.get(name)
        if not isinstance(record, dict):
            raise ValueError(f"{variant} deployment manifest lacks {name}")
        tool_path_text = record.get("path")
        if not isinstance(tool_path_text, str) or not Path(tool_path_text).is_absolute():
            raise ValueError(f"{variant} deployment {name} path is invalid")
        tool_path = Path(tool_path_text)
        try:
            actual_path = expected_path.resolve(strict=True)
            if (
                tool_path.resolve(strict=True) != actual_path
                or record.get("sha256") != expected_hash
                or _sha256_file(actual_path) != expected_hash
            ):
                raise ValueError(f"{variant} deployment {name} provenance mismatch")
        except OSError as exc:
            raise ValueError(f"{variant} deployment {name} is unavailable: {exc}") from exc
    plan_tools = json.loads(
        Path(deployment["deployment_plan"]["path"]).read_text(encoding="utf-8")
    ).get("tools")
    if not isinstance(plan_tools, dict):
        raise ValueError(f"{variant} deployment plan lacks tools")
    for name in ("nvif_capture", "nvif_parser"):
        if plan_tools.get(name) != tools.get(name):
            raise ValueError(f"{variant} deployment plan {name} mismatch")


def compare_runs(
    events_a: list[Event],
    events_b: list[Event],
    manifest_a: dict[str, Any],
    manifest_b: dict[str, Any],
    hard_stops_a: list[dict[str, Any]] | None = None,
    hard_stops_b: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    same_fields = (
        "input_sha256",
        "command_argv",
        "normalized_environment",
        "kernel",
        "runtime_profile_sha256",
        "nouveau_srcversion",
        "nouveau_module_path",
        "nouveau_module_file_sha256",
        "nouveau_module_srcversion",
        "nouveau_module_vermagic",
        "nouveau_parameters",
        "launcher_argv",
        "working_directory",
        "mpv_sha256",
        "mpv_resolved_path",
        "systemd_cat_sha256",
        "systemd_cat_resolved_path",
        "deployment_manifest_path",
        "deployment_manifest_sha256",
        "nvif_capture_sha256",
        "nvif_parser_sha256",
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

    hard_stops_a = hard_stops_a or []
    hard_stops_b = hard_stops_b or []
    result_a = classify_a_run(events_a, manifest_a, hard_stops_a)
    a_proven = result_a["outcome"] == "A_CHAIN_PROVEN"
    result_b = classify_b_run(
        events_b,
        manifest_b,
        a_chain_proven=a_proven,
        hard_stops=hard_stops_b,
    )
    if result_a["outcome"] != "A_CHAIN_PROVEN":
        outcome = result_a["outcome"]
    else:
        outcome = result_b["outcome"]
    return {
        "outcome": outcome,
        "variant_a": result_a,
        "variant_b": result_b,
        "manifest_comparison": {
            "same_input_command_environment_kernel_and_srcversion": True,
            "same_installed_nouveau_module_file_and_metadata": True,
            "same_nouveau_parameters": True,
            "separate_boots": True,
            "pinned_dso_hashes": True,
            "same_deployment_manifest_and_nvif_tools": True,
            "journal_deltas_hash_verified": True,
        },
        "hard_stop_records": {
            "A": result_a["hard_stop_records"],
            "B": result_b["hard_stop_records"],
        },
        "claim_boundary": (
            "Only the listed A/B causal outcomes are emitted. Pointer identity "
            "is not used; the numeric NVIF object key and channel handles are "
            "compared. No outcome proves visible playback."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=("A",))
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--journal-a", type=Path)
    parser.add_argument("--manifest-a", type=Path)
    parser.add_argument("--journal-b", type=Path)
    parser.add_argument("--manifest-b", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.variant == "A":
            if not args.journal or not args.manifest or any((
                args.journal_a, args.manifest_a, args.journal_b, args.manifest_b
            )):
                raise ValueError(
                    "single-run A analysis requires only --journal and --manifest"
                )
            manifest_bytes = args.manifest.read_bytes()
            manifest_a = read_manifest(args.manifest, "A")
            journal_a = args.journal.read_bytes()
            if hashlib.sha256(journal_a).hexdigest() != manifest_a[
                "journal_delta_sha256"
            ]:
                raise ValueError("A journal delta SHA-256 does not match its manifest")
            if journal_boot_ids(journal_a) != {manifest_a["boot_id"]}:
                raise ValueError("A journal delta boot ID does not match its manifest")
            events_a = read_events(io.StringIO(journal_a.decode("utf-8")))
            stops_a = hard_stop_records(journal_a)
            if stops_a != manifest_a["postrun_hard_stops"]:
                raise ValueError("A postrun hard-stop records do not match its journal")
            result = single_a_report(
                events_a, manifest_a, manifest_bytes, journal_a, stops_a
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result["outcome"] == "A_CHAIN_PROVEN" else 3

        if any((
            not args.journal_a,
            not args.manifest_a,
            not args.journal_b,
            not args.manifest_b,
            args.journal,
            args.manifest,
        )):
            raise ValueError(
                "paired comparison requires --journal-a/--manifest-a and "
                "--journal-b/--manifest-b"
            )
        assert args.journal_a and args.manifest_a and args.journal_b and args.manifest_b
        manifest_a = read_manifest(args.manifest_a, "A")
        manifest_b = read_manifest(args.manifest_b, "B")
        journal_a = args.journal_a.read_bytes()
        journal_b = args.journal_b.read_bytes()
        if hashlib.sha256(journal_a).hexdigest() != manifest_a["journal_delta_sha256"]:
            raise ValueError("A journal delta SHA-256 does not match its manifest")
        if hashlib.sha256(journal_b).hexdigest() != manifest_b["journal_delta_sha256"]:
            raise ValueError("B journal delta SHA-256 does not match its manifest")
        if journal_boot_ids(journal_a) != {manifest_a["boot_id"]}:
            raise ValueError("A journal delta boot ID does not match its manifest")
        if journal_boot_ids(journal_b) != {manifest_b["boot_id"]}:
            raise ValueError("B journal delta boot ID does not match its manifest")
        events_a = read_events(io.StringIO(journal_a.decode("utf-8")))
        events_b = read_events(io.StringIO(journal_b.decode("utf-8")))
        if hard_stop_records(journal_a) != manifest_a["postrun_hard_stops"]:
            raise ValueError("A postrun hard-stop records do not match its journal delta")
        if hard_stop_records(journal_b) != manifest_b["postrun_hard_stops"]:
            raise ValueError("B postrun hard-stop records do not match its journal delta")
        result = compare_runs(
            events_a,
            events_b,
            manifest_a,
            manifest_b,
            hard_stop_records(journal_a),
            hard_stop_records(journal_b),
        )
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["outcome"] == "B_CORRECTION_PROVEN":
        return 0
    if result["outcome"] in {"A_CHAIN_NOT_REPRODUCED", "A_INCONCLUSIVE"}:
        return 3
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
