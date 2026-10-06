#!/usr/bin/env python3
"""Run one pinned xrdp-console trigger and stop at the first kernel hard stop."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
AMBIENT_DIR = Path(__file__).resolve().parent
ADMISSION = ROOT / "validation/nouveau-deployment/admit_run.py"
EXPORTER = AMBIENT_DIR / "export_ring.py"
HEADROOM = AMBIENT_DIR / "check_ring_headroom.py"
CORRELATOR = AMBIENT_DIR / "correlate_bar2_lifetimes.py"
PROFILE = AMBIENT_DIR / "xrdp-trigger-profile.json"
DETACHED_SUPERVISOR = ROOT / "validation/detached-mpv-supervisor"
sys.path.insert(0, str(DETACHED_SUPERVISOR))
sys.path.insert(0, str(AMBIENT_DIR))

from detached_mpv_supervisor import stop_reason_for_line  # noqa: E402
from check_ring_headroom import (  # noqa: E402
    RingHeadroomError, check_sampled_headroom, check_status,
)

KERNEL_LOG_LOSS = re.compile(
    r"/dev/kmsg buffer overrun,\s*some messages lost", re.IGNORECASE,
)
RING_FAULT = re.compile(r"NOUVEAU_DIAG_BAR2_FAULT", re.IGNORECASE)
DEFAULT_MAX_RUNTIME_SECONDS = 180
FOLLOW_READY_SECONDS = 0.15
FAULT_CLEANUP_SECONDS = 35
MINIMUM_TRIGGER_EVENT_BUDGET = 65_536
MINIMUM_RING_HEADROOM = 30_000
MINIMUM_RING_HEADROOM_SAFETY_FACTOR = 1.5
MINIMUM_RING_HEADROOM_SAFETY_MARGIN = 5_000
PCI_BDF = re.compile(r"[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]")
GPU_CHURN_MARKER = re.compile(
    r"^GPU_CHURN_FIRST_FRAME monotonic_ns=([1-9][0-9]*)$", re.MULTILINE,
)
GPU_CHURN_MARKER_PREFIX = re.compile(r"^GPU_CHURN_FIRST_FRAME\b", re.MULTILINE)
HEADROOM_STATUS = re.compile(
    r"NOUVEAU_DIAG_BAR2_RING_STATUS enabled=(\d+) capacity=(\d+) "
    r"head_before=(\d+) head_after=(\d+) "
    r"dropped_before=(\d+) dropped_after=(\d+)"
)


class ReproducerError(RuntimeError):
    """The trigger is not safely prepared or its capture cannot be trusted."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run_command(
    argv: list[str], *, timeout: float = 30, env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, check=False, capture_output=True, text=True, timeout=timeout, env=env,
    )


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReproducerError(f"expected JSON object in {path}")
    return value


def verify_pinned_tool(manifest: dict[str, Any], name: str, path: Path) -> None:
    item = manifest.get("tools", {}).get(name)
    if not isinstance(item, dict):
        raise ReproducerError(f"deployment manifest lacks pinned tool {name}")
    expected_path = Path(item.get("path", "")).resolve(strict=True)
    actual_path = path.resolve(strict=True)
    if actual_path != expected_path:
        raise ReproducerError(f"running {name} path does not match deployment manifest")
    if sha256_file(actual_path) != item.get("sha256"):
        raise ReproducerError(f"running {name} SHA-256 does not match deployment manifest")


def verify_trigger_profile(profile: dict[str, Any]) -> dict[str, Path]:
    if profile.get("schema") != 1:
        raise ReproducerError("xrdp trigger profile schema mismatch")
    workspace = Path(str(profile.get("workspace", ""))).resolve(strict=True)
    if not workspace.is_dir():
        raise ReproducerError("xrdp-console workspace is not a directory")
    head = run_command(["git", "-C", str(workspace), "rev-parse", "HEAD"])
    if head.returncode or head.stdout.strip() != profile.get("review_commit"):
        raise ReproducerError("xrdp-console review commit mismatch")
    dirty = run_command([
        "git", "-C", str(workspace), "status", "--porcelain=v1",
        "--untracked-files=all",
    ])
    if dirty.returncode or dirty.stdout.strip():
        raise ReproducerError("xrdp-console worktree is not clean")

    artifacts = profile.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise ReproducerError("xrdp trigger profile has no pinned artifacts")
    verified: dict[str, Path] = {}
    for name, item in artifacts.items():
        if not isinstance(item, dict):
            raise ReproducerError(f"invalid xrdp artifact record {name}")
        path = Path(str(item.get("path", ""))).resolve(strict=True)
        if not path.is_file() or sha256_file(path) != item.get("sha256"):
            raise ReproducerError(f"xrdp artifact path/hash mismatch: {name}")
        verified[name] = path
    return verified


def benchmark_argv(
    workspace: Path, display: str, python: Path, workload: dict[str, Any],
    direct_module: Path,
) -> list[str]:
    script = workspace / "tools/benchmark/xrdp_console_bench.py"
    expected = {
        "backend": "direct-x11",
        "transport": "rdp",
        "mode": "graphics-under-churn",
        "network_mode": "localhost",
        "direct_graphics_transport": "gfx-planar",
        "fps": 30,
        "duration_seconds": 20,
        "repetitions": 1,
        "width": 1024,
        "height": 640,
        "passive_baseline_seconds": 10,
    }
    for key, value in expected.items():
        if workload.get(key) != value:
            raise ReproducerError(
                f"xrdp trigger profile {key}={workload.get(key)!r}; "
                f"expected {value!r}"
            )
    argv = [
        str(python), "-B", str(script),
        "--backend", str(workload["backend"]),
        "--transport", str(workload["transport"]),
        "--mode", str(workload["mode"]),
        "--network-mode", str(workload["network_mode"]),
        "--direct-graphics-transport",
        str(workload["direct_graphics_transport"]),
        "--direct-module", str(direct_module.resolve(strict=True)),
        "--fps", str(workload["fps"]),
        "--duration", str(workload["duration_seconds"]),
        "--repetitions", str(workload["repetitions"]),
        "--display", display,
        "--width", str(workload["width"]),
        "--height", str(workload["height"]),
    ]
    return argv


def first_gpu_churn_frame(payload: str) -> int | None:
    prefixes = GPU_CHURN_MARKER_PREFIX.findall(payload)
    matches = GPU_CHURN_MARKER.findall(payload)
    if not prefixes:
        return None
    if len(prefixes) != 1 or len(matches) != 1:
        raise ReproducerError("benchmark has a malformed or duplicate GPU churn marker")
    return int(matches[0])


def validate_trigger_event_budget(workload: dict[str, Any]) -> int:
    value = workload.get("expected_trigger_events")
    if type(value) is not int or value < MINIMUM_TRIGGER_EVENT_BUDGET:
        raise ReproducerError(
            "trigger profile expected_trigger_events must be an integer "
            f">= {MINIMUM_TRIGGER_EVENT_BUDGET}"
        )
    return value


def validate_headroom_policy(
    workload: dict[str, Any],
) -> tuple[int, int, float, int]:
    expected_trigger_events = validate_trigger_event_budget(workload)

    minimum_ring_headroom = workload.get("minimum_ring_headroom")
    if (
        type(minimum_ring_headroom) is not int
        or minimum_ring_headroom < MINIMUM_RING_HEADROOM
    ):
        raise ReproducerError(
            "trigger profile minimum_ring_headroom must be an integer "
            f">= {MINIMUM_RING_HEADROOM}"
        )

    safety_factor_value = workload.get("ring_headroom_safety_factor")
    if (
        isinstance(safety_factor_value, bool)
        or not isinstance(safety_factor_value, (int, float))
    ):
        raise ReproducerError(
            "trigger profile ring_headroom_safety_factor must be numeric"
        )
    try:
        ring_headroom_safety_factor = float(safety_factor_value)
    except (OverflowError, ValueError) as exc:
        raise ReproducerError(
            "trigger profile ring_headroom_safety_factor must be finite"
        ) from exc
    if (
        not math.isfinite(ring_headroom_safety_factor)
        or ring_headroom_safety_factor < MINIMUM_RING_HEADROOM_SAFETY_FACTOR
    ):
        raise ReproducerError(
            "trigger profile ring_headroom_safety_factor must be "
            f">= {MINIMUM_RING_HEADROOM_SAFETY_FACTOR}"
        )

    ring_headroom_safety_margin = workload.get("ring_headroom_safety_margin")
    if (
        type(ring_headroom_safety_margin) is not int
        or ring_headroom_safety_margin < MINIMUM_RING_HEADROOM_SAFETY_MARGIN
    ):
        raise ReproducerError(
            "trigger profile ring_headroom_safety_margin must be an integer "
            f">= {MINIMUM_RING_HEADROOM_SAFETY_MARGIN}"
        )

    return (
        expected_trigger_events,
        minimum_ring_headroom,
        ring_headroom_safety_factor,
        ring_headroom_safety_margin,
    )


def kernel_record_stop_reason(line: str, boot_id: str) -> str | None:
    try:
        record = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ReproducerError("journal follower emitted invalid JSON") from exc
    if not isinstance(record, dict):
        raise ReproducerError("journal follower emitted a non-object record")
    if record.get("_TRANSPORT") != "kernel":
        raise ReproducerError("journal follower emitted a non-kernel record")
    observed_boot = str(record.get("_BOOT_ID", "")).replace("-", "").lower()
    if observed_boot != boot_id.replace("-", "").lower():
        raise ReproducerError("journal follower changed boot ID")
    message = record.get("MESSAGE", "")
    if not isinstance(message, str):
        raise ReproducerError("journal record MESSAGE is not a string")
    if KERNEL_LOG_LOSS.search(message):
        return "KERNEL_JOURNAL_LOSS"
    if RING_FAULT.search(message):
        return "NOUVEAU_DIAGNOSTIC_BAR2_FAULT"
    return stop_reason_for_line(message)


def parse_cursor(payload: str) -> str:
    cursors = [
        line.removeprefix("-- cursor: ")
        for line in payload.splitlines()
        if line.startswith("-- cursor: ")
    ]
    if not cursors or not cursors[-1]:
        raise ReproducerError("journalctl did not provide a kernel cursor")
    return cursors[-1]


def journal_records_after(
    cursor: str, boot_id: str,
) -> tuple[list[str], str, str | None]:
    result = run_command([
        "journalctl", "-k", "-b", "--after-cursor", cursor,
        "--no-pager", "-o", "json",
    ], timeout=30)
    if result.returncode:
        raise ReproducerError(f"cannot inspect pre-trigger kernel journal: {result.stderr.strip()}")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    latest = cursor
    first_stop_reason: str | None = None
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReproducerError("pre-trigger journal delta contains invalid JSON") from exc
        if not isinstance(record, dict):
            raise ReproducerError("pre-trigger journal delta contains a non-object")
        if record.get("_TRANSPORT") != "kernel":
            raise ReproducerError("pre-trigger journal delta is not kernel transport")
        if str(record.get("_BOOT_ID", "")).replace("-", "").lower() != boot_id.replace("-", "").lower():
            raise ReproducerError("pre-trigger journal delta changed boot ID")
        latest_cursor = record.get("__CURSOR")
        if isinstance(latest_cursor, str) and latest_cursor:
            latest = latest_cursor
        reason = kernel_record_stop_reason(line, boot_id)
        if reason and first_stop_reason is None:
            first_stop_reason = reason
    return lines, latest, first_stop_reason


def append_journal_delta(path: Path, lines: list[str]) -> None:
    if not lines:
        return
    with path.open("a", encoding="utf-8") as stream:
        for line in lines:
            stream.write(line + "\n")
        stream.flush()


def wait_passive_baseline(
    seconds: float, cursor: str, boot_id: str, delta_path: Path,
    *, poll_seconds: float = 0.5,
) -> tuple[str, str | None]:
    if (
        isinstance(seconds, bool) or not isinstance(seconds, (int, float))
        or not math.isfinite(seconds) or seconds != 10
    ):
        raise ReproducerError("passive baseline must remain exactly 10 seconds")
    deadline = time.monotonic() + seconds
    while True:
        lines, cursor, reason = journal_records_after(cursor, boot_id)
        append_journal_delta(delta_path, lines)
        if reason:
            return cursor, reason
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return cursor, None
        time.sleep(min(poll_seconds, remaining))


def capture_and_correlate(
    run_dir: Path, status_path: Path, events_path: Path,
) -> dict[str, Any]:
    ring_path = run_dir / "ambient-bar2-ring.txt"
    capture_root_debugfs(events_path, ring_path)

    status_path_out = run_dir / "ring-status-at-stop.txt"
    status_text = read_root_debugfs(status_path)
    status_path_out.write_text(status_text, encoding="ascii")
    try:
        status_record: dict[str, int] | None = parse_ring_status(status_text)
    except ReproducerError:
        status_record = None

    kernel_journal, all_journal = archive_journals(run_dir)
    export_path = run_dir / "ring-merged-kernel.jsonl"
    export = run_command([
        sys.executable, str(EXPORTER), "--ring", str(ring_path),
        "--kernel-journal", str(kernel_journal), "--output", str(export_path),
    ], timeout=180)
    (run_dir / "ring-export.log").write_text(
        export.stdout + export.stderr, encoding="utf-8",
    )
    metadata_path = export_path.with_suffix(export_path.suffix + ".capture.json")
    capture_metadata: dict[str, Any] | None = None
    if metadata_path.is_file():
        capture_metadata = load_json(metadata_path)

    correlation_rc: int | None = None
    fault_count: int | None = None
    if export.returncode == 0:
        correlation_path = run_dir / "ambient-correlation.json"
        correlation = run_command([
            sys.executable, str(CORRELATOR), str(export_path),
            "--output", str(correlation_path),
        ], timeout=180)
        (run_dir / "ambient-correlation.log").write_text(
            correlation.stdout + correlation.stderr, encoding="utf-8",
        )
        correlation_rc = correlation.returncode
        if correlation_rc == 0 and correlation_path.is_file():
            fault_count = load_json(correlation_path).get("fault_count")

    paths = (
        status_path_out, ring_path, kernel_journal, all_journal, export_path,
        metadata_path, run_dir / "ambient-correlation.json",
    )
    return {
        "ring_export_exit_status": export.returncode,
        "correlator_exit_status": correlation_rc,
        "ring_status": status_record,
        "capture_metadata": capture_metadata,
        "fault_count": fault_count,
        "artifact_sha256": {
            path.name: file_sha256(path) for path in paths if path.is_file()
        },
    }


def parse_ring_status(payload: str) -> dict[str, int]:
    match = HEADROOM_STATUS.fullmatch(payload.strip())
    if match is None:
        raise ReproducerError("BAR2 ring status has an unknown or malformed schema")
    enabled, capacity, head_before, head_after, dropped_before, dropped_after = (
        int(value) for value in match.groups()
    )
    if head_before != head_after or dropped_before != dropped_after:
        raise ReproducerError("BAR2 ring status changed during its read")
    return {
        "enabled": enabled,
        "capacity": capacity,
        "head": head_after,
        "dropped": dropped_after,
    }


def write_result(run_dir: Path, result: dict[str, Any]) -> None:
    (run_dir / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    print(json.dumps(result, sort_keys=True))


def admission_reports_hard_stop(payload: str) -> bool:
    for name in ("BAR2_HOST_CPU_PTE_COUNT", "HARD_STOP_COUNT"):
        match = re.search(rf"^{name}=([0-9]+)$", payload, re.MULTILINE)
        if match and int(match.group(1)) > 0:
            return True
    return False


def experimental_sample_validity(
    *,
    first_frame_ns: int | None,
    evidence_complete: bool,
    monitor_failure: bool,
    trigger_completed: bool,
    kernel_stop_observed: bool,
) -> bool:
    return (
        first_frame_ns is not None
        and evidence_complete
        and not monitor_failure
        and (trigger_completed or kernel_stop_observed)
    )


def finish_pretrigger_stop(
    run_dir: Path,
    status_path: Path,
    events_path: Path,
    boot_id: str,
    reason: str,
    admission: str,
) -> int:
    capture: dict[str, Any] | None = None
    capture_error: str | None = None
    try:
        capture = capture_and_correlate(run_dir, status_path, events_path)
    except (OSError, ReproducerError, subprocess.SubprocessError) as exc:
        capture_error = str(exc)
    result = {
        "boot_id": boot_id,
        "admission": admission,
        "experiment": "pre-trigger stop; no xrdp/GL workload started",
        "pretrigger_stop_reason": reason,
        "STIMULUS_STARTED": False,
        "TRIGGER_COMPLETED": False,
        "EXPERIMENTAL_SAMPLE_VALID": False,
        "capture": capture,
        "capture_error": capture_error,
    }
    write_result(run_dir, result)
    if capture is None:
        return 5
    complete = (
        capture["ring_export_exit_status"] == 0
        and capture["correlator_exit_status"] == 0
        and isinstance(capture.get("capture_metadata"), dict)
        and capture["capture_metadata"].get("ring_loss_proven_through_cutoff") is True
    )
    return 4 if complete else 5


def find_debugfs_ring_files(
    expected_bdf: str,
    *,
    drm_class_root: Path = Path("/sys/class/drm"),
    debugfs_root: Path = Path("/sys/kernel/debug/dri"),
) -> tuple[Path, Path]:
    if PCI_BDF.fullmatch(expected_bdf) is None:
        raise ReproducerError("pinned Nouveau PCI BDF is malformed")

    primary_minors: list[int] = []
    for card in sorted(drm_class_root.glob("card*")):
        if re.fullmatch(r"card[0-9]+", card.name) is None:
            continue
        try:
            device_bdf = (card / "device").resolve(strict=True).name
            dev_text = (card / "dev").read_text(encoding="ascii").strip()
        except OSError:
            continue
        if device_bdf.lower() != expected_bdf.lower():
            continue
        dev_match = re.fullmatch(r"([0-9]+):([0-9]+)", dev_text)
        if dev_match is None or int(dev_match.group(1)) != 226:
            raise ReproducerError(f"invalid DRM primary device number for {card}")
        primary_minors.append(int(dev_match.group(2)))

    if len(primary_minors) != 1:
        raise ReproducerError(
            f"expected one primary DRM card for {expected_bdf}, found {len(primary_minors)}"
        )

    status = debugfs_root / str(primary_minors[0]) / "ambient_bar2_status"
    events = status.with_name("ambient_bar2_events")
    for path in (status, events):
        if os.geteuid() == 0:
            exists = path.is_file()
        else:
            result = run_command(["sudo", "-n", "test", "-f", str(path)], timeout=10)
            exists = result.returncode == 0
        if not exists:
            raise ReproducerError(
                f"cannot verify required Nouveau BAR2 ring node {path} as root"
            )
    return status, events


def read_root_debugfs(path: Path) -> str:
    result = run_command(["sudo", "-n", "cat", str(path)], timeout=10)
    if result.returncode:
        raise ReproducerError(f"cannot read {path}: {result.stderr.strip()}")
    return result.stdout


def admission_command(manifest_path: Path, display: str) -> list[str]:
    """Run protected provenance checks as root, keeping the benchmark unprivileged."""
    checker = [
        sys.executable, "-B", str(ADMISSION), "--manifest", str(manifest_path),
    ]
    if os.geteuid() == 0:
        return checker

    environment = {
        "DISPLAY": display,
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for name in (
        "XAUTHORITY", "XDG_SESSION_ID", "XDG_RUNTIME_DIR",
        "DBUS_SESSION_BUS_ADDRESS",
    ):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return [
        "sudo", "-n", "env",
        *(f"{name}={value}" for name, value in sorted(environment.items())),
        *checker,
    ]


def capture_root_debugfs(path: Path, output: Path, *, timeout: float = 180) -> None:
    with output.open("wb") as stream:
        result = subprocess.run(
            ["sudo", "-n", "cat", str(path)],
            check=False, stdout=stream, stderr=subprocess.PIPE, timeout=timeout,
        )
    if result.returncode:
        raise ReproducerError(
            f"cannot archive {path}: {result.stderr.decode(errors='replace').strip()}"
        )


def terminate_benchmark(process: subprocess.Popen[bytes], *, interrupt: bool) -> tuple[int, bool]:
    if process.poll() is not None:
        return process.returncode or 0, False
    if interrupt:
        process.send_signal(signal.SIGINT)
    try:
        return process.wait(timeout=FAULT_CLEANUP_SECONDS), False
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            return process.wait(timeout=5), True
        except subprocess.TimeoutExpired:
            process.kill()
            return process.wait(timeout=5), True


def read_live_journal(
    follower: subprocess.Popen[bytes], selector: selectors.BaseSelector,
    stream_path: Path, boot_id: str, buffer: bytearray,
) -> tuple[str | None, bool]:
    if follower.stdout is None:
        raise ReproducerError("journal follower has no output stream")
    fd = follower.stdout.fileno()
    reason: str | None = None
    monitor_failed = False
    with stream_path.open("ab") as saved:
        if follower.poll() is None:
            try:
                selector.get_key(follower.stdout)
            except KeyError:
                selector.register(follower.stdout, selectors.EVENT_READ)
        ready = selector.select(FOLLOW_READY_SECONDS)
        if follower.poll() is not None and not ready:
            monitor_failed = True
        for _key, _events in ready:
            chunk = os.read(fd, 65536)
            if not chunk:
                monitor_failed = True
                continue
            buffer.extend(chunk)
            while b"\n" in buffer:
                line, _, remainder = buffer.partition(b"\n")
                buffer[:] = remainder
                complete = bytes(line) + b"\n"
                saved.write(complete)
                saved.flush()
                if reason is None:
                    reason = kernel_record_stop_reason(
                        complete.decode("utf-8", errors="strict"), boot_id,
                    )
    return reason, monitor_failed


def archive_journals(run_dir: Path) -> tuple[Path, Path]:
    sync = run_command(["journalctl", "--sync"], timeout=10)
    if sync.returncode:
        raise ReproducerError(f"journal sync failed: {sync.stderr.strip()}")
    outputs = []
    for name, args in (
        ("whole-boot-kernel.jsonl", ["-k", "-b"]),
        ("whole-boot-all.jsonl", ["-b"]),
    ):
        path = run_dir / name
        with path.open("wb") as stream:
            result = subprocess.run(
                ["journalctl", *args, "-o", "json", "--no-pager"],
                check=False, stdout=stream, stderr=subprocess.PIPE, timeout=60,
            )
        if result.returncode:
            raise ReproducerError(
                f"journal archive failed: {result.stderr.decode(errors='replace').strip()}"
            )
        outputs.append(path)
    return outputs[0], outputs[1]


def file_sha256(path: Path) -> str:
    return sha256_file(path)


def execute(args: argparse.Namespace) -> int:
    run_dir = args.run_dir.resolve()
    if run_dir.exists():
        raise ReproducerError(f"run directory already exists: {run_dir}")
    run_dir.mkdir(parents=True, mode=0o700)

    manifest_path = args.manifest.resolve(strict=True)
    manifest = load_json(manifest_path)
    tool_map = {
        "xrdp_bar2_reproducer": Path(__file__),
        "xrdp_trigger_profile": PROFILE,
        "ambient_ring_exporter": EXPORTER,
        "ambient_ring_headroom": HEADROOM,
        "ambient_correlator": CORRELATOR,
    }
    for name, path in tool_map.items():
        verify_pinned_tool(manifest, name, path)

    profile = load_json(PROFILE)
    artifacts = verify_trigger_profile(profile)
    python = artifacts.get("python")
    if python is None or python != Path(sys.executable).resolve(strict=True):
        raise ReproducerError(
            "running Python interpreter does not match the pinned xrdp profile"
        )
    direct_module = artifacts.get("direct_module")
    if direct_module is None:
        raise ReproducerError("xrdp profile lacks the pinned direct-X11 module")
    workload = profile.get("workload")
    if not isinstance(workload, dict) or workload.get("stop_on_first_kernel_hard_stop") is not True:
        raise ReproducerError("trigger profile does not require stop-on-first-hard-stop")
    (
        expected_trigger_events,
        minimum_ring_headroom,
        ring_headroom_safety_factor,
        ring_headroom_safety_margin,
    ) = validate_headroom_policy(workload)

    display = os.environ.get("DISPLAY", "")
    if not display:
        raise ReproducerError("DISPLAY is empty; run from the admitted local X11 desktop")

    gpu = manifest.get("gpu")
    expected_bdf = gpu.get("pci_bdf") if isinstance(gpu, dict) else None
    if not isinstance(expected_bdf, str):
        raise ReproducerError("deployment manifest lacks the pinned Nouveau PCI BDF")
    status_path, events_path = find_debugfs_ring_files(expected_bdf)

    cursor_result = run_command([
        "journalctl", "-k", "-b", "-n", "0", "--no-pager", "-o", "json",
        "--show-cursor",
    ])
    if cursor_result.returncode:
        raise ReproducerError(f"cannot obtain pre-trigger journal cursor: {cursor_result.stderr.strip()}")
    cursor = parse_cursor(cursor_result.stdout)
    (run_dir / "starting-journal-cursor.txt").write_text(cursor + "\n", encoding="ascii")

    admission = run_command(
        admission_command(manifest_path, display), timeout=180,
    )
    admission_text = admission.stdout + admission.stderr
    (run_dir / "admission.log").write_text(
        admission_text, encoding="utf-8",
    )
    admission_lines = set(admission.stdout.splitlines())
    system_boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
        encoding="ascii",
    ).strip()
    if admission.returncode or "RUN_ELIGIBLE=true" not in admission_lines:
        try:
            records, _latest, delta_reason = journal_records_after(cursor, system_boot_id)
            delta_path = run_dir / "pretrigger-journal-delta.jsonl"
            delta_path.write_text(
                "".join(line + "\n" for line in records), encoding="utf-8",
            )
        except ReproducerError as exc:
            delta_reason = None
            (run_dir / "pretrigger-journal-error.txt").write_text(
                str(exc) + "\n", encoding="utf-8",
            )
        if delta_reason or admission_reports_hard_stop(admission_text):
            return finish_pretrigger_stop(
                run_dir, status_path, events_path, system_boot_id,
                delta_reason or "ADMISSION_REPORTED_PREEXISTING_HARD_STOP",
                "RUN_ELIGIBLE=false",
            )
        result = {
            "boot_id": system_boot_id,
            "admission": "RUN_ELIGIBLE=false",
            "admission_exit_status": admission.returncode,
            "experiment": "admission rejected; no xrdp/GL workload started",
            "STIMULUS_STARTED": False,
            "TRIGGER_COMPLETED": False,
            "EXPERIMENTAL_SAMPLE_VALID": False,
        }
        write_result(run_dir, result)
        return 2
    boot_line = next(
        (line for line in admission.stdout.splitlines() if line.startswith("BOOT_ID=")),
        None,
    )
    if boot_line is None:
        raise ReproducerError("admission output lacks BOOT_ID")
    boot_id = boot_line.split("=", 1)[1]
    if boot_id != system_boot_id:
        raise ReproducerError("admission BOOT_ID differs from the current boot ID")

    delta_path = run_dir / "pretrigger-journal-delta.jsonl"
    delta_path.write_text("", encoding="utf-8")
    pretrigger_records, latest_cursor, pretrigger_reason = journal_records_after(
        cursor, boot_id,
    )
    append_journal_delta(delta_path, pretrigger_records)
    if pretrigger_reason:
        return finish_pretrigger_stop(
            run_dir, status_path, events_path, boot_id, pretrigger_reason,
            "RUN_ELIGIBLE=true; pre-trigger hard stop observed",
        )

    baseline_seconds = workload.get("passive_baseline_seconds")
    latest_cursor, baseline_reason = wait_passive_baseline(
        baseline_seconds, latest_cursor, boot_id, delta_path,
    )
    if baseline_reason:
        return finish_pretrigger_stop(
            run_dir, status_path, events_path, boot_id, baseline_reason,
            "RUN_ELIGIBLE=true; passive baseline hard stop observed",
        )

    try:
        sample_delay = float(workload.get("ring_headroom_sample_seconds", 1.0))
    except (TypeError, ValueError) as exc:
        raise ReproducerError("ring headroom sample interval is not numeric") from exc
    if not math.isfinite(sample_delay) or sample_delay <= 0 or sample_delay > 5:
        raise ReproducerError("ring headroom sample interval must be in (0, 5] seconds")
    samples: list[tuple[float, str]] = []
    for sample_index in range(3):
        if sample_index:
            time.sleep(sample_delay)
        status_sample = read_root_debugfs(status_path)
        samples.append((time.monotonic(), status_sample))
        sample_records, latest_cursor, sample_reason = journal_records_after(
            latest_cursor, boot_id,
        )
        append_journal_delta(delta_path, sample_records)
        if sample_reason:
            return finish_pretrigger_stop(
                run_dir, status_path, events_path, boot_id, sample_reason,
                "RUN_ELIGIBLE=true; headroom-sampling hard stop observed",
            )
    try:
        projection_seconds = (
            float(workload.get("startup_budget_seconds", 20.0))
            + float(workload["duration_seconds"])
            + float(workload.get("post_trigger_capture_seconds", FAULT_CLEANUP_SECONDS))
            + float(workload.get("shutdown_budget_seconds", 10.0))
        )
        headroom = check_sampled_headroom(
            samples,
            projection_seconds=projection_seconds,
            expected_trigger_events=expected_trigger_events,
            minimum_free=minimum_ring_headroom,
            safety_factor=ring_headroom_safety_factor,
            safety_margin=ring_headroom_safety_margin,
        )
    except RingHeadroomError as exc:
        result = {
            "boot_id": boot_id,
            "admission": "RUN_ELIGIBLE=true",
            "experiment": "ring headroom refused trigger; no xrdp/GL workload started",
            "ring_headroom_error": str(exc),
            "STIMULUS_STARTED": False,
            "TRIGGER_COMPLETED": False,
            "EXPERIMENTAL_SAMPLE_VALID": False,
        }
        write_result(run_dir, result)
        return 2
    (run_dir / "ring-headroom.json").write_text(
        json.dumps(headroom, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )

    workspace = Path(str(profile["workspace"])).resolve(strict=True)
    argv = benchmark_argv(workspace, display, python, workload, direct_module)
    env = os.environ.copy()
    env.update({
        "XRDP_CONSOLE_WORKSPACE": str(workspace),
        "XRDP_CONSOLE_BUILD_DIR": str(workspace / "build-direct-console"),
        "XRDP_CONSOLE_HELPER_DIR": str(workspace / "build-direct-console/bin"),
        "XRDP_CONSOLE_XRDP": str(artifacts["xrdp"]),
        "XRDP_CONSOLE_MODULE": str(direct_module),
        "XRDP_CONSOLE_FREERDP": str(artifacts["freerdp"]),
        "XRDP_CONSOLE_RESULTS": str(run_dir / "xrdp-console-results"),
    })
    invocation = {
        "boot_id": boot_id,
        "display": display,
        "bar2_ring_status_path": str(status_path),
        "bar2_ring_events_path": str(events_path),
        "argv": argv,
        "backend": "direct-x11",
        "direct_graphics_transport": "gfx-planar",
        "xrdp_profile": PROFILE.name,
        "xrdp_profile_sha256": sha256_file(PROFILE),
        "xrdp_workspace_commit": profile["review_commit"],
        "artifact_sha256": {
            name: sha256_file(path) for name, path in artifacts.items()
        },
        "ring_headroom": headroom,
    }
    (run_dir / "invocation.json").write_text(
        json.dumps(invocation, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )

    selector = selectors.DefaultSelector()
    follower_buffer = bytearray()
    follower_stderr = (run_dir / "journal-follower.stderr").open("wb")
    follower: subprocess.Popen[bytes] | None = None
    process: subprocess.Popen[bytes] | None = None
    stop_reason: str | None = None
    monitor_failure = False
    benchmark_rc = 0
    cleanup_timeout = False
    trigger_start_head: int | None = None
    try:
        follower = subprocess.Popen(
            [
                "journalctl", "-k", "-b", "--follow", "--after-cursor", latest_cursor,
                "--no-pager", "-o", "json",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=follower_stderr,
            bufsize=0,
            start_new_session=True,
        )
        time.sleep(FOLLOW_READY_SECONDS)
        if follower.poll() is not None:
            stop_reason = "JOURNAL_FOLLOWER_FAILED"
            monitor_failure = True

        # Catch a stop event that arrived while the ring capacity was checked.
        if stop_reason is None and follower is not None:
            early_reason, early_monitor_failed = read_live_journal(
                follower, selector, run_dir / "journal-follow.jsonl", boot_id,
                follower_buffer,
            )
            if early_monitor_failed or early_reason:
                stop_reason = early_reason or "JOURNAL_FOLLOWER_FAILED"
                monitor_failure = early_monitor_failed or early_reason is None

        if stop_reason is None:
            try:
                start_status = check_status(
                    read_root_debugfs(status_path),
                    minimum_free=headroom["required_free"],
                )
                trigger_start_head = start_status["head"]
            except RingHeadroomError as exc:
                stop_reason = f"PRETRIGGER_RING_REFUSAL: {exc}"

        if stop_reason is None:
            with (run_dir / "benchmark.log").open("wb") as benchmark_log:
                process = subprocess.Popen(
                    argv,
                    cwd=workspace,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=benchmark_log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                deadline = time.monotonic() + DEFAULT_MAX_RUNTIME_SECONDS
                while process.poll() is None:
                    if time.monotonic() >= deadline:
                        stop_reason = "TRIGGER_WALL_TIMEOUT"
                        break
                    reason, failed = read_live_journal(
                        follower, selector, run_dir / "journal-follow.jsonl", boot_id,
                        follower_buffer,
                    )
                    if failed:
                        stop_reason = "JOURNAL_FOLLOWER_FAILED"
                        monitor_failure = True
                        break
                    if reason:
                        stop_reason = reason
                        break
                benchmark_rc, cleanup_timeout = terminate_benchmark(
                    process, interrupt=stop_reason is not None,
                )
    finally:
        if process is not None and process.poll() is None:
            benchmark_rc, _cleanup_failed = terminate_benchmark(process, interrupt=True)
        if follower is not None and follower.poll() is None:
            follower.terminate()
            try:
                follower.wait(timeout=5)
            except subprocess.TimeoutExpired:
                follower.kill()
                follower.wait(timeout=5)
        follower_stderr.close()
        selector.close()

    if process is None and stop_reason is None:
        stop_reason = "TRIGGER_NOT_STARTED"
    benchmark_log_path = run_dir / "benchmark.log"
    first_frame_ns = None
    if benchmark_log_path.is_file():
        first_frame_ns = first_gpu_churn_frame(
            benchmark_log_path.read_text(encoding="utf-8", errors="replace"),
        )
    capture = capture_and_correlate(run_dir, status_path, events_path)
    capture_metadata = capture.get("capture_metadata")
    ring_end_head = (
        capture_metadata.get("ring_head")
        if isinstance(capture_metadata, dict) else None
    )
    ring_event_delta = None
    if (
        isinstance(trigger_start_head, int)
        and isinstance(ring_end_head, int)
        and ring_end_head >= trigger_start_head
    ):
        ring_event_delta = ring_end_head - trigger_start_head

    if stop_reason is None:
        kernel_journal = run_dir / "whole-boot-kernel.jsonl"
        for line in kernel_journal.read_text(encoding="utf-8").splitlines():
            reason = kernel_record_stop_reason(line, boot_id)
            if reason:
                stop_reason = reason
                monitor_failure = True
                break

    evidence_complete = (
        capture["ring_export_exit_status"] == 0
        and capture["correlator_exit_status"] == 0
        and isinstance(capture_metadata, dict)
        and capture_metadata.get("ring_loss_proven_through_cutoff") is True
        and capture_metadata.get("ring_dropped_at_snapshot") == 0
    )
    trigger_completed = (
        process is not None and benchmark_rc == 0 and stop_reason is None
    )
    kernel_stop_observed = stop_reason is not None and stop_reason not in {
        "TRIGGER_WALL_TIMEOUT", "JOURNAL_FOLLOWER_FAILED",
        "KERNEL_JOURNAL_LOSS", "TRIGGER_NOT_STARTED",
    } and not stop_reason.startswith("PRETRIGGER_RING_REFUSAL:")
    stimulus_started = first_frame_ns is not None
    sample_valid = experimental_sample_validity(
        first_frame_ns=first_frame_ns,
        evidence_complete=evidence_complete,
        monitor_failure=monitor_failure,
        trigger_completed=trigger_completed,
        kernel_stop_observed=kernel_stop_observed,
    )

    result = {
        "boot_id": boot_id,
        "admission": "RUN_ELIGIBLE=true",
        "experiment": "pinned xrdp-console direct-X11 GFX Planar graphics-under-churn",
        "stop_reason": stop_reason,
        "benchmark_exit_status": benchmark_rc,
        "benchmark_cleanup_timeout": cleanup_timeout,
        "journal_monitor_failure": monitor_failure,
        "STIMULUS_STARTED": stimulus_started,
        "first_gpu_churn_frame_monotonic_ns": first_frame_ns,
        "TRIGGER_COMPLETED": trigger_completed,
        "EXPERIMENTAL_SAMPLE_VALID": sample_valid,
        "kernel_stop_observed_after_stimulus_start": (
            stimulus_started and kernel_stop_observed
        ),
        "trigger_start_ring_head": trigger_start_head,
        "snapshot_ring_head": ring_end_head,
        "ring_event_delta_through_snapshot": ring_event_delta,
        "evidence_capture_complete_through_ring_cutoff": evidence_complete,
        "capture": capture,
    }
    write_result(run_dir, result)

    if stop_reason:
        return 4
    if not trigger_completed or not evidence_complete:
        return 5
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        return execute(args)
    except (OSError, ValueError, KeyError, ReproducerError, subprocess.SubprocessError) as exc:
        print(f"XRDP_BAR2_REPRODUCER_ERROR={exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
