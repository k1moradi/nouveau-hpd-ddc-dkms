#!/usr/bin/env python3
"""Capture one pinned NVIF lifetime A/B run with fail-closed provenance.

This tool does not install anything. It refuses to launch unless the caller
passes --execute and the current boot has the pinned kernel/module, a clean
whole-boot hard-stop baseline, readable disabled CTXSW diagnostics, the pinned
input, and the selected private VA driver DSO. It captures only the journal
delta after a fresh cursor and observes the kernel passively for 30 seconds
after the workload stops.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import selectors
import shutil
import signal
import subprocess
import sys
import time
from typing import Any

import parse_nvif_lifetime_ab as correlator


ROOT = Path(__file__).resolve().parent
PROFILE_PATH = ROOT / "runtime-profile.json"
EXPECTED_KERNEL = correlator.EXPECTED_KERNEL
EXPECTED_SRCVERSION = correlator.EXPECTED_NOUVEAU_SRCVERSION
EXPECTED_PARAMS = {"diag_ctxsw": "N"}
FORBIDDEN_ENV = {
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "LIBVA_TRACE",
    "LIBVA_MESSAGING_LEVEL",
    "MESA_LOADER_DRIVER_OVERRIDE",
    "MESA_DEBUG",
    "NOUVEAU_DEBUG",
    "WAYLAND_DISPLAY",
}
UNSET = "<unset>"
ENV_SENTINELS = {
    "XAUTHORITY": "<session-xauthority>",
    "LIBVA_DRIVERS_PATH": "<variant-private-directory>",
}
ENV_SENTINELS.update({name: UNSET for name in FORBIDDEN_ENV})

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_profile(path: Path = PROFILE_PATH) -> dict[str, Any]:
    if path.resolve() != PROFILE_PATH.resolve():
        raise RuntimeError("alternate workload profile path is not accepted")
    try:
        profile = correlator.load_runtime_profile()
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    argv = profile["argv"]
    if argv[-1] != "/home/keivan/test_1080p.mkv":
        raise RuntimeError("pinned workload input path changed")
    if profile.get("input_sha256") != correlator.EXPECTED_INPUT_SHA256:
        raise RuntimeError("pinned workload input hash changed")
    if profile.get("max_runtime_seconds") != 18:
        raise RuntimeError("pinned workload deadline changed")
    if profile.get("post_stop_observation_seconds") != 30:
        raise RuntimeError("pinned post-stop observation window changed")
    if profile.get("controlled_execution", {}).get("working_directory") != "/home/keivan":
        raise RuntimeError("controlled working directory changed")
    if profile.get("controlled_execution", {}).get("home") != "/home/keivan":
        raise RuntimeError("controlled HOME changed")
    return profile


def journal_records(data: bytes) -> list[dict[str, Any]]:
    try:
        return correlator.journal_records(data)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc


def hard_stop_kinds(record: dict[str, Any]) -> tuple[str, ...]:
    return correlator.journal_record_hard_stop_kinds(record)


def journal_messages(data: bytes) -> list[str]:
    try:
        return correlator.journal_messages(data)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc


def hard_stop_records(data: bytes) -> list[dict[str, Any]]:
    try:
        return correlator.hard_stop_records(data)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc


def parse_cursor(output: str) -> str:
    cursors = [
        line.removeprefix("-- cursor: ").strip()
        for line in output.splitlines()
        if line.startswith("-- cursor: ")
    ]
    if len(cursors) != 1 or not cursors[0]:
        raise RuntimeError("journalctl did not return exactly one start cursor")
    return cursors[0]


def clean_boot_journal_boundary(expected_boot_id: str) -> tuple[str, bytes]:
    cursor = parse_cursor(run_checked(
        ["/usr/bin/journalctl", "-b", "-n", "0", "--show-cursor", "--no-pager"],
        timeout=15,
    ).decode("utf-8", "replace"))
    full_journal = run_checked(
        ["/usr/bin/journalctl", "-b", "-o", "json", "--no-pager"],
        timeout=20,
    )
    preflight_stops = hard_stop_records(full_journal)
    if preflight_stops:
        labels = sorted({label for item in preflight_stops for label in item["kinds"]})
        raise RuntimeError(
            "current-boot hard-stop baseline is contaminated: "
            + ", ".join(labels)
        )
    boot_ids = correlator.journal_boot_ids(full_journal)
    if boot_ids != {expected_boot_id}:
        raise RuntimeError("whole-boot journal snapshot does not match current boot ID")
    if not correlator.journal_has_kernel_records(full_journal):
        raise RuntimeError("whole-boot journal snapshot has no visible kernel records")
    return cursor, full_journal


def normalized_environment(
    *, display: str, home: str, xauthority: str,
) -> dict[str, str]:
    if not display or not xauthority or not home:
        raise RuntimeError("DISPLAY, HOME, and XAUTHORITY are required")
    return {
        "DISPLAY": display,
        "XAUTHORITY": ENV_SENTINELS["XAUTHORITY"],
        "LIBVA_DRIVER_NAME": "nouveau",
        "LIBVA_DRIVERS_PATH": ENV_SENTINELS["LIBVA_DRIVERS_PATH"],
        "HOME": home,
        "PATH": "/usr/bin:/bin",
        "LC_ALL": "C",
        **{name: UNSET for name in sorted(FORBIDDEN_ENV)},
    }


def execution_environment(
    *, display: str, home: str, xauthority: Path, driver_dir: Path,
) -> dict[str, str]:
    normalized_environment(display=display, home=home, xauthority=str(xauthority))
    return {
        "DISPLAY": display,
        "XAUTHORITY": str(xauthority),
        "LIBVA_DRIVER_NAME": "nouveau",
        "LIBVA_DRIVERS_PATH": str(driver_dir),
        "HOME": home,
        "PATH": "/usr/bin:/bin",
        "LC_ALL": "C",
    }


def run_checked(command: list[str], *, timeout: int = 15) -> bytes:
    try:
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"command failed to complete: {command[0]}: {exc}") from exc
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"command failed ({result.returncode}): {command[0]}: {detail}")
    return result.stdout


def read_module_parameter(name: str) -> str:
    if not name.isidentifier():
        raise RuntimeError(f"invalid Nouveau module parameter name: {name!r}")
    path = Path("/sys/module/nouveau/parameters") / name
    try:
        return path.read_text(encoding="ascii").strip()
    except PermissionError:
        sudo = shutil.which("sudo")
        if not sudo:
            raise RuntimeError(
                f"cannot read root-only Nouveau parameter {name}: sudo is unavailable"
            )
        try:
            return run_checked(
                [sudo, "-n", "/usr/bin/cat", str(path)],
                timeout=3,
            ).decode("ascii").strip()
        except (RuntimeError, UnicodeDecodeError) as exc:
            raise RuntimeError(
                f"cannot read root-only Nouveau parameter {name} with sudo -n"
            ) from exc


def require_parameter_disabled(name: str, value: str) -> str:
    if value.strip().upper() in {"N", "0", "FALSE"}:
        return "N"
    raise RuntimeError(f"{name} must be disabled; observed {value!r}")


def _modinfo_field(modinfo: str, field: str, target: str) -> str:
    try:
        output = run_checked(
            [modinfo, "-F", field, target],
            timeout=5,
        ).decode("utf-8", "replace")
    except RuntimeError as exc:
        raise RuntimeError(f"cannot read Nouveau modinfo field {field}: {exc}") from exc
    values = [line.strip() for line in output.splitlines() if line.strip()]
    if len(values) != 1:
        raise RuntimeError(
            f"modinfo returned {len(values)} values for Nouveau field {field}"
        )
    return values[0]


def installed_module_identity() -> dict[str, str]:
    """Fingerprint the module file selected by modinfo after install/sign/compress."""
    modinfo = shutil.which("modinfo")
    if not modinfo:
        raise RuntimeError("modinfo is unavailable; installed Nouveau is unverified")
    selected = _modinfo_field(modinfo, "filename", "nouveau")
    if selected in {"(builtin)", "builtin"}:
        raise RuntimeError("Nouveau is built in; expected an installed module file")
    try:
        module_path = Path(selected).resolve(strict=True)
    except OSError as exc:
        raise RuntimeError(f"selected Nouveau module file is unavailable: {exc}") from exc
    if not module_path.is_file():
        raise RuntimeError("selected Nouveau module path is not a regular file")

    srcversion = _modinfo_field(modinfo, "srcversion", str(module_path))
    vermagic = _modinfo_field(modinfo, "vermagic", str(module_path))
    if srcversion != EXPECTED_SRCVERSION:
        raise RuntimeError(f"installed Nouveau srcversion mismatch: {srcversion}")
    fields = vermagic.split()
    if not fields or fields[0] != EXPECTED_KERNEL:
        raise RuntimeError(f"installed Nouveau vermagic mismatch: {vermagic}")

    return {
        "path": str(module_path),
        "file_sha256": sha256_file(module_path),
        "srcversion": srcversion,
        "vermagic": vermagic,
    }


def observed_runtime(*, variant: str, dso: Path) -> dict[str, Any]:
    if variant not in {"A", "B"}:
        raise RuntimeError("variant must be A or B")
    dso = dso.resolve(strict=True)
    if not dso.is_file():
        raise RuntimeError(f"selected VA DSO is not a regular file: {dso}")
    dso_hash = sha256_file(dso)
    if dso_hash != correlator.EXPECTED_DSO_SHA256[variant]:
        raise RuntimeError(f"variant {variant} DSO SHA-256 is not pinned")

    profile = load_profile()
    media = Path(profile["argv"][-1])
    if not media.is_file() or sha256_file(media) != profile["input_sha256"]:
        raise RuntimeError("pinned media is missing or its SHA-256 changed")
    mpv = Path(profile["argv"][0]).resolve(strict=True)
    systemd_cat = Path("/usr/bin/systemd-cat").resolve(strict=True)
    if not mpv.is_file() or not systemd_cat.is_file():
        raise RuntimeError("pinned MPV or systemd-cat executable is unavailable")
    if platform.release() != EXPECTED_KERNEL:
        raise RuntimeError(f"unexpected kernel release: {platform.release()}")
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="ascii"
        ).strip()
        srcversion = Path("/sys/module/nouveau/srcversion").read_text(
            encoding="ascii"
        ).strip()
    except OSError as exc:
        raise RuntimeError(f"cannot read required live Nouveau provenance: {exc}") from exc
    if not boot_id:
        raise RuntimeError("current boot ID is empty")
    if srcversion != EXPECTED_SRCVERSION:
        raise RuntimeError(f"unexpected loaded Nouveau srcversion: {srcversion}")
    module_identity = installed_module_identity()
    if module_identity["srcversion"] != srcversion:
        raise RuntimeError(
            "selected installed Nouveau module does not match the loaded srcversion"
        )
    param = require_parameter_disabled(
        "diag_ctxsw",
        read_module_parameter("diag_ctxsw"),
    )

    display = os.environ.get("DISPLAY", "")
    xauthority_text = os.environ.get("XAUTHORITY", "")
    home = os.environ.get("HOME", "")
    if not display or not xauthority_text:
        raise RuntimeError("run from the logged-in X11 session with DISPLAY/XAUTHORITY")
    if display != profile["historical_capture"]["observed_environment"]["DISPLAY"]:
        raise RuntimeError(f"unexpected X11 display for this profile: {display}")
    if home != profile["controlled_execution"]["home"]:
        raise RuntimeError(f"unexpected HOME for this profile: {home}")
    xauthority = Path(xauthority_text).resolve(strict=True)
    if not xauthority.is_file():
        raise RuntimeError("XAUTHORITY does not resolve to a regular file")
    if EXPECTED_PARAMS.get("diag_ctxsw") != param:
        raise RuntimeError(f"diag_ctxsw must be disabled; observed {param!r}")
    for variable in FORBIDDEN_ENV:
        if os.environ.get(variable):
            raise RuntimeError(f"refusing contaminated environment variable {variable}")
    xdpyinfo = shutil.which("xdpyinfo")
    if not xdpyinfo:
        raise RuntimeError("xdpyinfo is unavailable; X11 display access is unverified")
    try:
        xcheck = subprocess.run(
            [xdpyinfo],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"cannot verify the active X11 display: {exc}") from exc
    if xcheck.returncode:
        detail = xcheck.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"xdpyinfo cannot access DISPLAY: {detail}")
    for name in ("mpv", "ffplay", "ffmpeg", "vlc", "firefox"):
        pgrep = shutil.which("pgrep")
        if not pgrep:
            raise RuntimeError("pgrep is unavailable; competing GPU consumers are unverified")
        try:
            check = subprocess.run(
                [pgrep, "-x", name],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"cannot check for competing process {name}: {exc}") from exc
        if check.returncode == 0:
            raise RuntimeError(f"competing GPU consumer is running: {name}")
        if check.returncode != 1:
            raise RuntimeError(f"pgrep could not verify competing process {name}")

    cursor, full_journal = clean_boot_journal_boundary(boot_id)
    profile = load_profile()
    return {
        "schema": 2,
        "variant": variant,
        "boot_id": boot_id,
        "input_sha256": profile["input_sha256"],
        "command_argv": profile["argv"],
        "normalized_environment": normalized_environment(
            display=display,
            home=home,
            xauthority=str(xauthority),
        ),
        "kernel": platform.release(),
        "nouveau_srcversion": srcversion,
        "nouveau_module_path": module_identity["path"],
        "nouveau_module_file_sha256": module_identity["file_sha256"],
        "nouveau_module_srcversion": module_identity["srcversion"],
        "nouveau_module_vermagic": module_identity["vermagic"],
        "nouveau_parameters": {"diag_ctxsw": param.upper()},
        "dso_sha256": dso_hash,
        "dso_resolved_path": str(dso),
        "mpv_sha256": sha256_file(mpv),
        "mpv_resolved_path": str(mpv),
        "systemd_cat_sha256": sha256_file(systemd_cat),
        "systemd_cat_resolved_path": str(systemd_cat),
        "working_directory": profile["controlled_execution"]["working_directory"],
        "xauthority_sha256": sha256_file(xauthority),
        "journal_start_cursor": cursor,
        "journal_boundary_proven": True,
        "preflight_journal_sha256": sha256_bytes(full_journal),
        "_preflight_journal_bytes": full_journal,
        "_xauthority_resolved_path": str(xauthority),
        "preflight_hard_stops": [],
        "postrun_hard_stops": [],
        "workload_returncode": None,
        "workload_timed_out": False,
        "journal_delta_sha256": None,
    }


def _terminate_process_group(
    proc: subprocess.Popen[str],
    *,
    sigint_grace: float = 2.0,
    sigterm_grace: float = 2.0,
    sigkill_grace: float = 2.0,
) -> None:
    pgid = proc.pid
    for sig, timeout in (
        (signal.SIGINT, sigint_grace),
        (signal.SIGTERM, sigterm_grace),
        (signal.SIGKILL, sigkill_grace),
    ):
        try:
            # The launcher can exit before a descendant. Keep signaling and
            # checking the process group even when Popen's leader has exited.
            os.killpg(pgid, sig)
        except ProcessLookupError:
            proc.wait(timeout=5)
            return

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not process_group_members(pgid):
                proc.wait(timeout=5)
                return
            time.sleep(0.05)

    if process_group_members(pgid):
        raise RuntimeError(
            f"process group {pgid} remains after SIGKILL escalation"
        )
    proc.wait(timeout=5)


def process_group_members(pgid: int) -> list[tuple[int, str]]:
    """Return non-zombie members of a process group, including after leader exit."""
    members: list[tuple[int, str]] = []
    for stat_path in Path("/proc").glob("[0-9]*/stat"):
        try:
            raw = stat_path.read_text(encoding="ascii")
            tail = raw[raw.rfind(")") + 2 :].split()
            state = tail[0]
            process_group = int(tail[2])
            if process_group == pgid and state != "Z":
                members.append((int(stat_path.parent.name), state))
        except (OSError, ValueError, IndexError):
            continue
    return members


def _consume_follower(selector: selectors.BaseSelector, fd: int) -> list[bytes]:
    if not selector.select(timeout=0):
        return []
    try:
        data = os.read(fd, 65536)
    except BlockingIOError:
        return []
    return data.splitlines(keepends=True)


def _journal_follow_command(cursor: str) -> list[str]:
    return [
        "/usr/bin/journalctl", "-b", f"--after-cursor={cursor}",
        "-f", "-o", "json", "--no-pager",
    ]


def _export_delta(cursor: str) -> bytes:
    return run_checked(
        [
            "/usr/bin/journalctl", "-b", f"--after-cursor={cursor}",
            "-o", "json", "--no-pager",
        ],
        timeout=20,
    )


def execute_capture(
    *, variant: str, dso: Path, output_dir: Path,
    runtime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile = load_profile()
    runtime = runtime or observed_runtime(variant=variant, dso=dso)
    if runtime["variant"] != variant:
        raise RuntimeError("observed runtime variant mismatch")
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise RuntimeError(f"refusing to overwrite capture directory: {output_dir}")
    systemd_cat = Path("/usr/bin/systemd-cat")
    mpv = Path(profile["argv"][0])
    if not systemd_cat.is_file() or not mpv.is_file():
        raise RuntimeError("systemd-cat or pinned mpv executable is missing")

    output_dir.mkdir(parents=True)
    preflight_journal = runtime.pop("_preflight_journal_bytes", None)
    if not isinstance(preflight_journal, bytes):
        raise RuntimeError("observed runtime lacks the full-boot preflight journal")
    (output_dir / "journal-preflight.jsonl").write_bytes(preflight_journal)
    driver_dir = output_dir / "driver"
    driver_dir.mkdir()
    alias = driver_dir / "nouveau_drv_video.so"
    alias.symlink_to(Path(runtime["dso_resolved_path"]))
    if alias.resolve(strict=True) != Path(runtime["dso_resolved_path"]):
        raise RuntimeError("private VA driver alias resolves to an unexpected file")

    env = execution_environment(
        display=runtime["normalized_environment"]["DISPLAY"],
        home=runtime["normalized_environment"]["HOME"],
        xauthority=Path(runtime.pop("_xauthority_resolved_path")).resolve(strict=True),
        driver_dir=driver_dir.resolve(),
    )
    launcher = [
        str(systemd_cat), "--identifier=nouveau-nvif-lifetime", "--",
        *profile["argv"],
    ]
    manifest = dict(runtime)
    manifest["launcher_argv"] = launcher
    manifest["dso_alias_path"] = str(alias)
    manifest["preflight_journal_path"] = "journal-preflight.jsonl"
    manifest["journal_delta_sha256"] = None
    manifest["workload_returncode"] = None
    manifest["workload_timed_out"] = False
    manifest["journal_monitor_error"] = None
    manifest["termination_reason"] = "process-exit"
    (output_dir / "manifest.pending.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    follower = subprocess.Popen(
        _journal_follow_command(runtime["journal_start_cursor"]),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=False,
        start_new_session=True,
        bufsize=0,
    )
    if follower.stdout is None:
        raise RuntimeError("journal follower has no stdout pipe")
    selector = selectors.DefaultSelector()
    fd = follower.stdout.fileno()
    os.set_blocking(fd, False)
    selector.register(fd, selectors.EVENT_READ)
    time.sleep(0.1)
    if follower.poll() is not None:
        selector.close()
        raise RuntimeError("journal follower exited before workload launch")
    pending = bytearray()
    hard_stops: list[dict[str, Any]] = []
    monitor_error: str | None = None
    started = time.monotonic()
    stop_deadline: float | None = None
    process: subprocess.Popen[str] | None = None
    timed_out = False
    termination_reason = "process-exit"
    try:
        process = subprocess.Popen(
            launcher,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            cwd=profile["controlled_execution"]["working_directory"],
            start_new_session=True,
            text=True,
        )
        while True:
            now = time.monotonic()
            for chunk in _consume_follower(selector, fd):
                pending.extend(chunk)
                while b"\n" in pending:
                    line, _, remainder = pending.partition(b"\n")
                    pending = bytearray(remainder)
                    line += b"\n"
                    try:
                        records = journal_records(line)
                    except RuntimeError as exc:
                        monitor_error = str(exc)
                        _terminate_process_group(process)
                        termination_reason = "monitor-error"
                        if stop_deadline is None:
                            stop_deadline = (
                                time.monotonic()
                                + profile["post_stop_observation_seconds"]
                            )
                        break
                    for record in records:
                        message = record.get("MESSAGE")
                        if not isinstance(message, str):
                            continue
                        kinds = hard_stop_kinds(record)
                        if kinds:
                            hard_stops.append({
                                "source": correlator.journal_record_hard_stop_source(record),
                                "kinds": list(kinds),
                                "message": message,
                            })
                        try:
                            eexist = correlator.is_bsp_new_eexist(message)
                        except ValueError as exc:
                            monitor_error = str(exc)
                            _terminate_process_group(process)
                            termination_reason = "monitor-error"
                            if stop_deadline is None:
                                stop_deadline = (
                                    time.monotonic()
                                    + profile["post_stop_observation_seconds"]
                                )
                            break
                        if eexist:
                            termination_reason = "bsp-eexist"
                            _terminate_process_group(process)
                            if stop_deadline is None:
                                stop_deadline = (
                                    time.monotonic()
                                    + profile["post_stop_observation_seconds"]
                                )
                            break
                    if hard_stops:
                        _terminate_process_group(process)
                        primary_source = (
                            "kernel"
                            if any(item["source"] == "kernel" for item in hard_stops)
                            else "workload"
                        )
                        termination_reason = f"{primary_source}-hard-stop"
                        if stop_deadline is None:
                            stop_deadline = (
                                time.monotonic()
                                + profile["post_stop_observation_seconds"]
                            )
                        break
                    if monitor_error or termination_reason == "bsp-eexist":
                        break
            if monitor_error:
                break
            if process.poll() is None and now - started >= profile["max_runtime_seconds"]:
                timed_out = True
                termination_reason = "deadline"
                _terminate_process_group(process)
                stop_deadline = (
                    time.monotonic()
                    + profile["post_stop_observation_seconds"]
                )
            elif process.poll() is not None and stop_deadline is None:
                stop_deadline = (
                    time.monotonic()
                    + profile["post_stop_observation_seconds"]
                )
            if stop_deadline is not None and now >= stop_deadline:
                break
            if follower.poll() is not None:
                monitor_error = "journal follower exited before observation completed"
                termination_reason = "monitor-error"
                if process.poll() is None:
                    _terminate_process_group(process)
                break
            time.sleep(0.05)
        if process.poll() is None:
            _terminate_process_group(process)
        returncode = process.wait(timeout=5)
        if monitor_error:
            passive_until = stop_deadline or (
                time.monotonic() + profile["post_stop_observation_seconds"]
            )
            while time.monotonic() < passive_until:
                time.sleep(0.05)
    finally:
        selector.close()
        if follower.poll() is None:
            follower.terminate()
            try:
                follower.wait(timeout=3)
            except subprocess.TimeoutExpired:
                follower.kill()
                follower.wait(timeout=3)
        if process is not None and process.poll() is None:
            _terminate_process_group(process)

    delta = _export_delta(runtime["journal_start_cursor"])
    (output_dir / "journal-delta.jsonl").write_bytes(delta)
    all_stops = hard_stop_records(delta)
    for item in hard_stops:
        if item not in all_stops:
            all_stops.append(item)
    manifest.update({
        "journal_delta_sha256": sha256_bytes(delta),
        "postrun_hard_stops": all_stops,
        "workload_returncode": returncode,
        "workload_timed_out": timed_out,
        "termination_reason": termination_reason,
        "journal_monitor_error": monitor_error,
        "journal_boundary_proven": not bool(monitor_error),
    })
    if any(record.get("source") == "kernel" for record in all_stops):
        manifest["outcome"] = "KERNEL_FAILURE"
    elif all_stops:
        manifest["outcome"] = "WORKLOAD_FAILURE"
    elif monitor_error:
        manifest["outcome"] = "JOURNAL_BOUNDARY_UNPROVEN"
    elif timed_out:
        manifest["outcome"] = "WORKLOAD_TIMEOUT"
    elif termination_reason == "bsp-eexist":
        manifest["outcome"] = "STOPPED_AT_BSP_EEXIST"
    elif returncode:
        manifest["outcome"] = "WORKLOAD_NONZERO_EXIT"
    else:
        manifest["outcome"] = "CAPTURED_NO_KERNEL_HARD_STOP"
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "manifest.pending.json").unlink(missing_ok=True)
    if monitor_error:
        raise RuntimeError(f"capture is incomplete: {monitor_error}")
    return manifest


def capture_exit_status(result: dict[str, Any], *, variant: str) -> int:
    """Return a nonzero status when a capture is unsafe or inconclusive.

    A zero status means the run was captured without a kernel hard stop and
    without an unexplained workload failure. It does not mean the A/B
    hypothesis was proven; the pair correlator owns that decision.
    """
    if variant not in {"A", "B"}:
        raise ValueError("variant must be A or B")
    outcome = result.get("outcome")
    if result.get("postrun_hard_stops") or outcome == "KERNEL_FAILURE":
        return 4
    if outcome == "WORKLOAD_FAILURE":
        return 4
    if (
        result.get("workload_timed_out")
        or not result.get("journal_boundary_proven")
        or result.get("journal_monitor_error")
        or outcome == "JOURNAL_BOUNDARY_UNPROVEN"
        or outcome == "WORKLOAD_TIMEOUT"
    ):
        return 3
    if outcome == "WORKLOAD_NONZERO_EXIT":
        return 4
    if outcome == "STOPPED_AT_BSP_EEXIST":
        return 0
    if outcome == "CAPTURED_NO_KERNEL_HARD_STOP":
        # A is useful only if it reproduces the expected failure. A clean A
        # run makes the later B comparison inconclusive.
        return 3 if variant == "A" else 0
    return 4


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=("A", "B"), required=True)
    parser.add_argument("--dso", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="required to launch the pinned mpv command after fail-closed preflight",
    )
    args = parser.parse_args(argv)
    if not args.execute:
        print("REFUSED: --execute is required; no workload was launched", file=sys.stderr)
        return 2
    try:
        runtime = observed_runtime(variant=args.variant, dso=args.dso)
        result = execute_capture(
            variant=args.variant,
            dso=args.dso,
            output_dir=args.output_dir,
            runtime=runtime,
        )
    except (OSError, RuntimeError, KeyError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return capture_exit_status(result, variant=args.variant)


if __name__ == "__main__":
    raise SystemExit(main())
