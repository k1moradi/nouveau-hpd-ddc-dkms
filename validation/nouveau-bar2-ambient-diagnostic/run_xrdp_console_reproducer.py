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
from check_ring_headroom import RingHeadroomError, check_sampled_headroom  # noqa: E402

KERNEL_LOG_LOSS = re.compile(
    r"/dev/kmsg buffer overrun,\s*some messages lost", re.IGNORECASE,
)
RING_FAULT = re.compile(r"NOUVEAU_DIAG_BAR2_FAULT", re.IGNORECASE)
DEFAULT_MAX_RUNTIME_SECONDS = 180
FOLLOW_READY_SECONDS = 0.15
FAULT_CLEANUP_SECONDS = 35


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
) -> list[str]:
    script = workspace / "tools/benchmark/xrdp_console_bench.py"
    expected = {
        "backend": "vnc",
        "transport": "rdp",
        "mode": "graphics-under-churn",
        "network_mode": "localhost",
        "pipeline": "gfx",
        "fps": 30,
        "duration_seconds": 20,
        "repetitions": 1,
        "width": 1024,
        "height": 640,
        "enable_gfx_for_vnc": True,
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
        "--pipeline", str(workload["pipeline"]),
        "--fps", str(workload["fps"]),
        "--duration", str(workload["duration_seconds"]),
        "--repetitions", str(workload["repetitions"]),
        "--display", display,
        "--width", str(workload["width"]),
        "--height", str(workload["height"]),
    ]
    if workload["enable_gfx_for_vnc"]:
        argv.append("--enable-gfx-for-vnc")
    return argv


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


def journal_records_after(cursor: str, boot_id: str) -> tuple[list[str], str]:
    result = run_command([
        "journalctl", "-k", "-b", "--after-cursor", cursor,
        "--no-pager", "-o", "json",
    ], timeout=30)
    if result.returncode:
        raise ReproducerError(f"cannot inspect pre-trigger kernel journal: {result.stderr.strip()}")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    latest = cursor
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
        if reason:
            raise ReproducerError(f"kernel hard stop appeared during preflight: {reason}")
    return lines, latest


def find_debugfs_ring_files() -> tuple[Path, Path]:
    status_candidates = sorted(Path("/sys/kernel/debug/dri").glob("*/ambient_bar2_status"))
    if len(status_candidates) != 1:
        raise ReproducerError(
            f"expected one Nouveau BAR2 ring status node, found {len(status_candidates)}"
        )
    status = status_candidates[0]
    events = status.with_name("ambient_bar2_events")
    if not events.is_file():
        raise ReproducerError("ambient BAR2 event ring node is missing")
    return status, events


def read_root_debugfs(path: Path) -> str:
    result = run_command(["sudo", "-n", "cat", str(path)], timeout=10)
    if result.returncode:
        raise ReproducerError(f"cannot read {path}: {result.stderr.strip()}")
    return result.stdout


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
    workload = profile.get("workload")
    if not isinstance(workload, dict) or workload.get("stop_on_first_kernel_hard_stop") is not True:
        raise ReproducerError("trigger profile does not require stop-on-first-hard-stop")

    display = os.environ.get("DISPLAY", "")
    if not display:
        raise ReproducerError("DISPLAY is empty; run from the admitted local X11 desktop")

    cursor_result = run_command([
        "journalctl", "-k", "-b", "-n", "0", "--no-pager", "-o", "json",
        "--show-cursor",
    ])
    if cursor_result.returncode:
        raise ReproducerError(f"cannot obtain pre-trigger journal cursor: {cursor_result.stderr.strip()}")
    cursor = parse_cursor(cursor_result.stdout)
    (run_dir / "starting-journal-cursor.txt").write_text(cursor + "\n", encoding="ascii")

    admission = run_command([
        sys.executable, str(ADMISSION), "--manifest", str(manifest_path),
    ], timeout=180)
    (run_dir / "admission.log").write_text(
        admission.stdout + admission.stderr, encoding="utf-8",
    )
    admission_lines = set(admission.stdout.splitlines())
    if admission.returncode or "RUN_ELIGIBLE=true" not in admission_lines:
        raise ReproducerError("post-boot admission did not return RUN_ELIGIBLE=true")
    boot_line = next(
        (line for line in admission.stdout.splitlines() if line.startswith("BOOT_ID=")),
        None,
    )
    if boot_line is None:
        raise ReproducerError("admission output lacks BOOT_ID")
    boot_id = boot_line.split("=", 1)[1]

    pretrigger_records, latest_cursor = journal_records_after(cursor, boot_id)
    (run_dir / "pretrigger-journal-delta.jsonl").write_text(
        "".join(line + "\n" for line in pretrigger_records), encoding="utf-8",
    )
    status_path, events_path = find_debugfs_ring_files()
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
            minimum_free=int(workload.get("minimum_ring_headroom", 30_000)),
            safety_factor=float(workload.get("ring_headroom_safety_factor", 1.5)),
            safety_margin=int(workload.get("ring_headroom_safety_margin", 5_000)),
        )
    except RingHeadroomError as exc:
        raise ReproducerError(f"refusing xrdp trigger: {exc}") from exc
    (run_dir / "ring-headroom.json").write_text(
        json.dumps(headroom, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )

    workspace = Path(str(profile["workspace"])).resolve(strict=True)
    argv = benchmark_argv(workspace, display, python, workload)
    env = os.environ.copy()
    env.update({
        "XRDP_CONSOLE_WORKSPACE": str(workspace),
        "XRDP_CONSOLE_BUILD_DIR": str(workspace / "build-direct-console"),
        "XRDP_CONSOLE_HELPER_DIR": str(workspace / "build-direct-console/bin"),
        "XRDP_CONSOLE_XRDP": str(artifacts["xrdp"]),
        "XRDP_CONSOLE_X11VNC": str(artifacts["x11vnc"]),
        "XRDP_CONSOLE_FREERDP": str(artifacts["freerdp"]),
        "XRDP_CONSOLE_CHANSRV": str(artifacts["xrdp_chansrv"]),
        "XRDP_CONSOLE_RESULTS": str(run_dir / "xrdp-console-results"),
    })
    invocation = {
        "boot_id": boot_id,
        "display": display,
        "argv": argv,
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
            error_path = run_dir / "journal-follower.stderr"
            raise ReproducerError(
                "kernel journal follower exited before trigger: "
                + error_path.read_text(encoding="utf-8", errors="replace")
            )

        # Catch a stop event that arrived while the ring capacity was checked.
        early_reason, early_monitor_failed = read_live_journal(
            follower, selector, run_dir / "journal-follow.jsonl", boot_id,
            follower_buffer,
        )
        if early_monitor_failed or early_reason:
            raise ReproducerError(
                f"refusing trigger because journal monitoring is not clean: "
                f"reason={early_reason or 'follower failed'}"
            )

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

    if process is None:
        raise ReproducerError("xrdp benchmark did not start")

    ring_path = run_dir / "ambient-bar2-ring.txt"
    capture_root_debugfs(events_path, ring_path)
    kernel_journal, all_journal = archive_journals(run_dir)
    export_path = run_dir / "ring-merged-kernel.jsonl"
    export = run_command([
        sys.executable, str(EXPORTER), "--ring", str(ring_path),
        "--kernel-journal", str(kernel_journal), "--output", str(export_path),
    ], timeout=180)
    (run_dir / "ring-export.log").write_text(
        export.stdout + export.stderr, encoding="utf-8",
    )
    correlation_rc = None
    if export.returncode == 0:
        correlation = run_command([
            sys.executable, str(CORRELATOR), str(export_path),
        ], timeout=180)
        (run_dir / "ambient-correlation.json").write_text(
            correlation.stdout + correlation.stderr, encoding="utf-8",
        )
        correlation_rc = correlation.returncode

    if stop_reason is None:
        for line in kernel_journal.read_text(encoding="utf-8").splitlines():
            reason = kernel_record_stop_reason(line, boot_id)
            if reason:
                stop_reason = reason
                monitor_failure = True
                break

    result = {
        "boot_id": boot_id,
        "admission": "RUN_ELIGIBLE=true",
        "experiment": "pinned xrdp-console graphics-under-churn VNC/RDP reproducer",
        "stop_reason": stop_reason,
        "benchmark_exit_status": benchmark_rc,
        "benchmark_cleanup_timeout": cleanup_timeout,
        "journal_monitor_failure": monitor_failure,
        "ring_export_exit_status": export.returncode,
        "correlator_exit_status": correlation_rc,
        "artifacts": {
            path.name: file_sha256(path)
            for path in (ring_path, kernel_journal, all_journal, export_path)
            if path.is_file()
        },
    }
    (run_dir / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    print(json.dumps(result, sort_keys=True))

    if stop_reason:
        return 4
    if benchmark_rc != 0 or export.returncode != 0 or correlation_rc != 0:
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
