#!/usr/bin/env python3
"""Detached, fail-closed mpv runner for a future logged-in desktop session.

This file is preparation only. `start --arm` launches an actual VA-API workload;
do not use that command until a fresh desktop boot has a clean kernel baseline.
The service tails the kernel journal from a saved cursor and kills mpv's entire
process group on any BAR2/PTE or historical hard-stop signature. Once the
player stops, it passively observes the journal for 30 seconds to collect
delayed recovery/teardown records; an explicit operator stop cancels that wait.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hmac
import hashlib
import io
import json
import math
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from bar2_baseline import BAR2_HOST_CPU_PTE, matching_lines


HERE = Path(__file__).resolve().parent
INPUT = Path("/home/keivan/test_1080p.mkv")
PLUGIN = Path(
    "/home/keivan/nouveau-vaapi-app-validation/"
    "mpv-eexist-delete-fd-candidate-20261001/prefix/lib/x86_64-linux-gnu/dri/"
    "libgallium_drv_video.so"
)
LIBVA_DRIVER_ALIAS = PLUGIN.parent / "nouveau_drv_video.so"
RUN_ROOT = HERE / "detached-mpv-runs"
EXPECTED_INPUT_SHA256 = "d1bab5275bcb585791fbfb15c801c1aab582256e7b7fca280c76f78a0a1c1ec2"
MPV = Path("/usr/bin/mpv")
JOURNALCTL = Path("/usr/bin/journalctl")
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
SYSTEMD_RUN = Path("/usr/bin/systemd-run")
SYSTEMCTL = Path("/usr/bin/systemctl")
XDPYINFO = Path("/usr/bin/xdpyinfo")
LOGINCTL = Path("/usr/bin/loginctl")
MODINFO = Path("/usr/sbin/modinfo")
SUDO = Path("/usr/bin/sudo")
CAT = Path("/usr/bin/cat")
ARM_TOKEN_ENV = "NVMPV_ARM_TOKEN"
ARM_TOKEN_FILE = "arm-token"
DEPLOYMENT_MANIFEST_NAME = "deployment-manifest.json"
REQUIRED_DIAGNOSTIC_PARAMETERS = {"diag_ctxsw": "N"}
POST_STOP_OBSERVATION_SECONDS = 30.0
FIRST_SCREENSHOT_DELAY_SECONDS = 2.0
SCREENSHOT_INTERVAL_SECONDS = 10.0
DRIVER_MAP_WAIT_SECONDS = 10.0
PROCESS_GROUP_SIGINT_GRACE_SECONDS = 5.0
PROCESS_GROUP_SIGTERM_GRACE_SECONDS = 2.0
PROCESS_GROUP_SIGKILL_GRACE_SECONDS = 2.0
PROCESS_WAIT_TIMEOUT_SECONDS = 2.0
PROCESS_GROUP_STOP_MAX_SECONDS = (
    PROCESS_GROUP_SIGINT_GRACE_SECONDS
    + PROCESS_GROUP_SIGTERM_GRACE_SECONDS
    + PROCESS_GROUP_SIGKILL_GRACE_SECONDS
    + PROCESS_WAIT_TIMEOUT_SECONDS
)
FINAL_JOURNAL_QUERY_TIMEOUT_SECONDS = 8.0
SYSTEMD_STOP_MARGIN_SECONDS = 5.0
SYSTEMD_STOP_TIMEOUT_SECONDS = 30
SYSTEMCTL_STOP_COMMAND_TIMEOUT_SECONDS = 40

HARD_STOP_PATTERNS = {
    "PRIV_VIOLATION": re.compile(r"PRIV_VIOLATION", re.I),
    "CTXSW_TIMEOUT": re.compile(r"CTXSW_TIMEOUT", re.I),
    "SCHED_ERROR_0A": re.compile(r"\bSCHED_ERROR\s+0a\b", re.I),
    # The follower reads only the kernel journal. Stop on any Nouveau FIFO
    # MMU fault, even when it is not the known BAR2/PTE tuple.
    "GPU-MMU-fault": re.compile(r"\bfifo:\s*fault\b", re.I),
    "channel-killed": re.compile(r"channel .*killed", re.I),
    "failed-to-idle": re.compile(r"failed to idle", re.I),
    "PROP/RT-overrun": re.compile(r"PROP.*(?:trap|overrun)|RT_(?:WIDTH|HEIGHT)_OVERRUN", re.I),
    # This matcher consumes only journalctl -k output. A userspace SIGBUS is
    # classified from MPV's process return code instead.
    "SIGBUS": re.compile(r"SIGBUS", re.I),
    "GPU-reset/fallen-off-bus": re.compile(r"GPU reset|GPU has fallen off the bus|fallen off the bus", re.I),
    "kernel-BUG": re.compile(r"\bBUG:\s", re.I),
    "kernel-Oops": re.compile(r"\bOops:\s", re.I),
    "kernel-WARNING": re.compile(r"\bWARNING:\s+CPU:\s*\d+\b", re.I),
    "KASAN": re.compile(r"\bKASAN:\s", re.I),
    "KCSAN": re.compile(r"\bKCSAN:\s", re.I),
    "KFENCE": re.compile(r"\bKFENCE:\s", re.I),
    "UBSAN": re.compile(r"\bUBSAN:\s", re.I),
    "lockdep": re.compile(r"\blockdep\b|possible circular locking dependency", re.I),
}
PLAYER_NAMES = {"mpv", "ffplay", "vlc", "vainfo", "ffmpeg"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def journal(args: list[str], timeout: float = 8.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(JOURNALCTL), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def sync_journal() -> None:
    result = journal(["--sync"], timeout=5)
    if result.returncode:
        raise RuntimeError(f"journal sync failed: {result.stderr.strip()}")


def prove_kernel_journal_visibility() -> tuple[bool, str]:
    """Require one current-boot kernel record before claiming a clean baseline."""
    try:
        current_boot = BOOT_ID_PATH.read_text(encoding="ascii").strip().replace("-", "")
    except OSError as exc:
        return False, f"cannot read current boot ID: {exc}"
    if not current_boot:
        return False, "current boot ID is empty"

    try:
        result = journal(
            ["-k", "-b", "-n", "1", "--no-pager", "-o", "json"],
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"kernel journal visibility query failed: {exc}"
    if result.returncode:
        return False, result.stderr.strip() or "kernel journal visibility query failed"

    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        return False, f"expected one current-boot kernel journal record, got {len(lines)}"
    try:
        record = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        return False, f"kernel journal visibility record is invalid JSON: {exc}"
    if not isinstance(record, dict):
        return False, "kernel journal visibility record is not an object"
    if record.get("_TRANSPORT") != "kernel":
        return False, "visible journal record is not kernel transport"
    observed_boot = str(record.get("_BOOT_ID", "")).replace("-", "")
    if observed_boot != current_boot:
        return False, (
            f"kernel journal boot mismatch: expected {current_boot!r}, "
            f"got {observed_boot!r}"
        )
    return True, "current-boot kernel journal visibility proven"


def hard_stop_matches(lines: Iterable[str]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for line in lines:
        for label, pattern in HARD_STOP_PATTERNS.items():
            if pattern.search(line):
                found.append((label, line.rstrip("\n")))
                break
    return found


@dataclass(frozen=True)
class MpvHardwareDecodeEvidence:
    nouveau_requested: bool
    pinned_driver_path: bool
    driver_open_succeeded: bool
    h264_vaapi_requested: bool
    vaapi_pixel_format_requested: bool
    hardware_decode_active: bool
    actual_driver_mapping: bool

    @property
    def confirmed(self) -> bool:
        return all(
            (
                self.nouveau_requested,
                self.pinned_driver_path,
                self.driver_open_succeeded,
                self.h264_vaapi_requested,
                self.vaapi_pixel_format_requested,
                self.hardware_decode_active,
                self.actual_driver_mapping,
            )
        )

    def missing_evidence(self) -> list[str]:
        requirements = (
            ("nouveau-driver-request", self.nouveau_requested),
            ("pinned-driver-path", self.pinned_driver_path),
            ("driver-open-success", self.driver_open_succeeded),
            ("h264-vaapi-request", self.h264_vaapi_requested),
            ("vaapi-pixel-format", self.vaapi_pixel_format_requested),
            ("active-vaapi-copy", self.hardware_decode_active),
            ("actual-driver-mapping", self.actual_driver_mapping),
        )
        return [name for name, present in requirements if not present]


def classify_mpv_hardware_decode(
    log_text: str, *, actual_driver_mapping: bool = False
) -> MpvHardwareDecodeEvidence:
    """Require runtime log evidence, not merely the requested mpv options."""
    lowered = log_text.lower()
    return MpvHardwareDecodeEvidence(
        nouveau_requested="user environment variable requested driver 'nouveau'" in lowered,
        pinned_driver_path=str(LIBVA_DRIVER_ALIAS).lower() in lowered,
        driver_open_succeeded="va_opendriver() returns 0" in lowered,
        h264_vaapi_requested="trying hardware decoding via h264-vaapi-copy" in lowered,
        vaapi_pixel_format_requested="requesting pixfmt 'vaapi' from decoder" in lowered,
        hardware_decode_active="using hardware decoding (vaapi-copy)" in lowered,
        actual_driver_mapping=actual_driver_mapping,
    )


def current_players() -> list[str]:
    found = []
    for proc in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            argv = proc.read_bytes().split(b"\0")
        except (OSError, PermissionError):
            continue
        if argv and Path(os.fsdecode(argv[0])).name in PLAYER_NAMES:
            found.append(f"{proc.parent.name}:{os.fsdecode(argv[0])}")
    return found


def _exact_keys(value: object, expected: set[str], label: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"{label} keys must be exactly {sorted(expected)}")
    return value


def _absolute_path(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value.startswith("/"):
        raise ValueError(f"{label} must be an absolute path")
    return Path(value)


def _sha256_value(value: object, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def validate_deployment_manifest(data: object) -> dict[str, object]:
    """Validate the frozen deployment record before consulting the machine."""
    root = _exact_keys(
        data, {"schema", "kernel", "nouveau", "initramfs", "mesa", "mpv", "input"},
        "deployment manifest",
    )
    if root["schema"] != 1:
        raise ValueError("unsupported deployment manifest schema")
    if not isinstance(root["kernel"], str) or not root["kernel"]:
        raise ValueError("kernel must be a nonempty string")

    nouveau = _exact_keys(
        root["nouveau"],
        {"srcversion", "module_path", "module_sha256", "vermagic", "parameters"},
        "nouveau",
    )
    initramfs = _exact_keys(
        root["initramfs"],
        {"path", "sha256", "module_sha256"},
        "initramfs",
    )
    mesa = _exact_keys(root["mesa"], {"dso_path", "dso_sha256"}, "mesa")
    mpv = _exact_keys(root["mpv"], {"path", "sha256"}, "mpv")
    media = _exact_keys(root["input"], {"path", "sha256"}, "input")

    for section, key, label in (
        (nouveau, "module_path", "nouveau.module_path"),
        (initramfs, "path", "initramfs.path"),
        (mesa, "dso_path", "mesa.dso_path"),
        (mpv, "path", "mpv.path"),
        (media, "path", "input.path"),
    ):
        _absolute_path(section[key], label)
    for section, key, label in (
        (nouveau, "module_sha256", "nouveau.module_sha256"),
        (initramfs, "sha256", "initramfs.sha256"),
        (initramfs, "module_sha256", "initramfs.module_sha256"),
        (mesa, "dso_sha256", "mesa.dso_sha256"),
        (mpv, "sha256", "mpv.sha256"),
        (media, "sha256", "input.sha256"),
    ):
        _sha256_value(section[key], label)
    if not isinstance(nouveau["srcversion"], str) or not nouveau["srcversion"]:
        raise ValueError("nouveau.srcversion must be a nonempty string")
    if not isinstance(nouveau["vermagic"], str) or not nouveau["vermagic"]:
        raise ValueError("nouveau.vermagic must be a nonempty string")
    parameters = nouveau["parameters"]
    if not isinstance(parameters, dict) or not parameters:
        raise ValueError("nouveau.parameters must be a nonempty string map")
    for name, value in parameters.items():
        if not re.fullmatch(r"[A-Za-z0-9_]+", str(name)) or not isinstance(value, str):
            raise ValueError("nouveau.parameters must map names to string values")
    if parameters != REQUIRED_DIAGNOSTIC_PARAMETERS:
        raise ValueError(
            "nouveau.parameters must exactly pin the reviewed MPV setting "
            f"{REQUIRED_DIAGNOSTIC_PARAMETERS!r}"
        )
    return root


def load_deployment_manifest(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read deployment manifest {path}: {exc}") from exc
    return validate_deployment_manifest(data)


def _modinfo(field: str, module_path: Path | None = None) -> str:
    command = [str(MODINFO), "-F", field]
    command.append(str(module_path) if module_path is not None else "nouveau")
    result = subprocess.run(
        command, check=False, capture_output=True, text=True, timeout=5
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"modinfo {field} failed")
    return result.stdout.strip()


def _read_module_parameter(name: str) -> str:
    path = Path("/sys/module/nouveau/parameters") / name
    try:
        return path.read_text(encoding="utf-8").strip()
    except PermissionError:
        result = subprocess.run(
            [str(SUDO), "-n", str(CAT), str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode:
            raise RuntimeError(
                result.stderr.strip() or f"cannot read protected Nouveau parameter {name}"
            )
        return result.stdout.strip()


def _initramfs_module_hashes(initramfs_path: Path) -> list[tuple[str, str]]:
    unmkinitramfs = shutil.which("unmkinitramfs")
    if not unmkinitramfs:
        raise RuntimeError("unmkinitramfs is unavailable")
    with tempfile.TemporaryDirectory(prefix="nvmpv-initramfs-") as temporary:
        result = subprocess.run(
            [unmkinitramfs, str(initramfs_path), temporary],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "initramfs extraction failed")
        candidates = sorted(
            path for path in Path(temporary).rglob("nouveau.ko*")
            if path.is_file()
            and re.fullmatch(r"nouveau\.ko(?:\.(?:zst|xz|gz))?", path.name)
        )
        return [(str(path), sha256(path)) for path in candidates]


def verify_deployment_manifest(
    manifest: dict[str, object],
) -> tuple[list[str], dict[str, object]]:
    errors: list[str] = []
    observed: dict[str, object] = {}
    nouveau = manifest["nouveau"]
    initramfs = manifest["initramfs"]
    mesa = manifest["mesa"]
    mpv = manifest["mpv"]
    media = manifest["input"]
    assert isinstance(nouveau, dict) and isinstance(initramfs, dict)
    assert isinstance(mesa, dict) and isinstance(mpv, dict) and isinstance(media, dict)

    def compare(label: str, actual: object, expected: object) -> None:
        observed[label] = actual
        if actual != expected:
            errors.append(f"{label} mismatch: expected {expected!r}, got {actual!r}")

    compare("kernel", os.uname().release, manifest["kernel"])
    try:
        selected = Path(_modinfo("filename")).resolve(strict=True)
        expected_module_path = Path(str(nouveau["module_path"])).resolve(strict=True)
        compare("nouveau_module_path", str(selected), str(expected_module_path))
        compare("nouveau_module_sha256", sha256(selected), nouveau["module_sha256"])
        compare("nouveau_file_srcversion", _modinfo("srcversion", selected), nouveau["srcversion"])
        compare("nouveau_vermagic", _modinfo("vermagic", selected), nouveau["vermagic"])
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        errors.append(f"selected Nouveau module verification failed: {exc}")
    try:
        loaded_srcversion = Path("/sys/module/nouveau/srcversion").read_text().strip()
    except OSError as exc:
        loaded_srcversion = ""
        errors.append(f"cannot read loaded Nouveau srcversion: {exc}")
    compare("nouveau_loaded_srcversion", loaded_srcversion, nouveau["srcversion"])

    try:
        selected_initramfs = Path(str(initramfs["path"])).resolve(strict=True)
        compare("initramfs_path", str(selected_initramfs), str(Path(str(initramfs["path"])).resolve()))
        compare("initramfs_sha256", sha256(selected_initramfs), initramfs["sha256"])
        embedded = _initramfs_module_hashes(selected_initramfs)
        observed["initramfs_modules"] = embedded
        unique_hashes = {digest for _name, digest in embedded}
        if not embedded:
            errors.append("initramfs contains no Nouveau module")
        elif unique_hashes != {initramfs["module_sha256"]}:
            errors.append(
                "initramfs Nouveau module hash mismatch: "
                f"expected {initramfs['module_sha256']!r}, got {sorted(unique_hashes)!r}"
            )
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        errors.append(f"initramfs verification failed: {exc}")

    for label, section, path_key, hash_key, configured_path in (
        ("mesa", mesa, "dso_path", "dso_sha256", PLUGIN),
        ("mpv", mpv, "path", "sha256", MPV),
        ("input", media, "path", "sha256", INPUT),
    ):
        try:
            actual_path = configured_path.resolve(strict=True)
            expected_path = Path(str(section[path_key])).resolve(strict=True)
            compare(f"{label}_path", str(actual_path), str(expected_path))
            compare(f"{label}_sha256", sha256(actual_path), section[hash_key])
        except OSError as exc:
            errors.append(f"{label} artifact verification failed: {exc}")
    try:
        alias_path = LIBVA_DRIVER_ALIAS.resolve(strict=True)
        plugin_path = PLUGIN.resolve(strict=True)
        compare("libva_driver_alias", str(alias_path), str(plugin_path))
    except OSError as exc:
        errors.append(f"VA driver alias verification failed: {exc}")

    parameters = nouveau["parameters"]
    assert isinstance(parameters, dict)
    observed_parameters: dict[str, str] = {}
    for name, expected in parameters.items():
        try:
            actual = _read_module_parameter(name)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            errors.append(f"cannot read Nouveau parameter {name}: {exc}")
            continue
        observed_parameters[name] = actual
        if actual != expected:
            errors.append(
                f"Nouveau parameter {name} mismatch: expected {expected!r}, got {actual!r}"
            )
    observed["nouveau_parameters"] = observed_parameters
    return errors, observed


def _session_properties(text: str) -> dict[str, str]:
    return dict(
        line.split("=", 1) for line in text.splitlines() if "=" in line
    )


def visible_x11_session_errors() -> list[str]:
    errors: list[str] = []
    session_id = os.environ.get("XDG_SESSION_ID", "")
    display = os.environ.get("DISPLAY", "")
    if not session_id:
        return ["XDG_SESSION_ID is unset; launch from the logged-in desktop session"]
    if not display:
        errors.append("DISPLAY is unset; run from the logged-in X11 desktop")
    if not LOGINCTL.exists():
        return errors + ["loginctl is unavailable; cannot verify a local desktop session"]
    try:
        result = subprocess.run(
            [str(LOGINCTL), "show-session", session_id,
             "-p", "Active", "-p", "Remote", "-p", "Type",
             "-p", "Class", "-p", "Seat", "-p", "VTNr", "-p", "Display"],
            check=False, capture_output=True, text=True, timeout=4,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return errors + [f"desktop session query failed: {exc}"]
    if result.returncode:
        return errors + [f"desktop session query failed: {result.stderr.strip()}"]
    props = _session_properties(result.stdout)
    expected = {
        "Active": "yes", "Remote": "no", "Type": "x11",
        "Class": "user", "Seat": "seat0",
    }
    for name, value in expected.items():
        if props.get(name) != value:
            errors.append(f"desktop session {name} must be {value!r}, got {props.get(name)!r}")
    session_display = props.get("Display", "")
    if not session_display:
        errors.append("active login session does not report its X11 display")
    elif display and session_display != display:
        errors.append(
            f"DISPLAY {display!r} does not match active session display {session_display!r}"
        )
    try:
        if int(props.get("VTNr", "0")) <= 0:
            errors.append("active X11 session is not attached to a physical VT")
    except ValueError:
        errors.append("active X11 session has an invalid VT number")
    return errors


def child_environment() -> dict[str, str]:
    required = ("DISPLAY", "HOME", "XDG_RUNTIME_DIR")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError("required desktop environment missing: " + ", ".join(missing))
    env = {
        "DISPLAY": os.environ["DISPLAY"],
        "HOME": os.environ["HOME"],
        "XDG_RUNTIME_DIR": os.environ["XDG_RUNTIME_DIR"],
        "PATH": "/usr/bin:/bin",
        "LC_ALL": "C",
        "LIBVA_DRIVER_NAME": "nouveau",
        "LIBVA_DRIVERS_PATH": str(PLUGIN.parent),
    }
    for name in ("XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS", "PULSE_SERVER", "PIPEWIRE_REMOTE"):
        value = os.environ.get(name)
        if value:
            env[name] = value
    return env


def preflight(
    require_desktop: bool = True,
    deployment_manifest: dict[str, object] | None = None,
    require_deployment: bool = False,
) -> tuple[list[str], str, str, dict[str, object]]:
    errors: list[str] = []
    try:
        srcversion = Path("/sys/module/nouveau/srcversion").read_text().strip()
    except OSError as exc:
        srcversion = ""
        errors.append(f"cannot read loaded Nouveau srcversion: {exc}")
    deployment_observed: dict[str, object] = {}
    if deployment_manifest is not None:
        deployment_errors, deployment_observed = verify_deployment_manifest(deployment_manifest)
        errors.extend(deployment_errors)
    elif require_deployment:
        errors.append("a reviewed deployment manifest is required before starting mpv")

    if deployment_manifest is not None:
        observed_input = deployment_observed.get("input_sha256")
        if observed_input != EXPECTED_INPUT_SHA256:
            errors.append(
                f"input hash does not match the pinned workload: {observed_input!r}"
            )
    else:
        try:
            actual = sha256(INPUT)
            if actual != EXPECTED_INPUT_SHA256:
                errors.append(f"input hash mismatch: {actual}")
        except OSError as exc:
            errors.append(f"cannot hash input: {exc}")
    if require_desktop:
        errors.extend(visible_x11_session_errors())
    for label, path in (("input", INPUT), ("Mesa plugin", PLUGIN)):
        if not path.is_file():
            errors.append(f"{label} is missing or not a regular file: {path}")

    if require_desktop and os.environ.get("DISPLAY"):
        display = os.environ.get("DISPLAY", "")
        if not XDPYINFO.exists():
            errors.append("xdpyinfo is missing; cannot verify X11 connection")
        else:
            try:
                result = subprocess.run(
                    [str(XDPYINFO), "-display", display],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=4,
                )
                if result.returncode:
                    errors.append(f"X11 is unreachable: {result.stderr.strip()}")
            except (OSError, subprocess.TimeoutExpired) as exc:
                errors.append(f"X11 check failed: {exc}")

    journal_visible, journal_visibility_detail = prove_kernel_journal_visibility()
    if not journal_visible:
        errors.append(
            "cannot prove current-boot kernel journal visibility: "
            + journal_visibility_detail
        )

    players = current_players()
    if players:
        errors.append("competing media process(es): " + ", ".join(players))

    try:
        snapshot = journal(["-k", "-b", "--no-pager", "-o", "cat"], timeout=12)
    except (OSError, subprocess.TimeoutExpired) as exc:
        errors.append(f"current-boot kernel journal query failed: {exc}")
        return errors, srcversion, ""
    if snapshot.returncode:
        errors.append(f"cannot read current-boot kernel journal: {snapshot.stderr.strip()}")
        snapshot_text = ""
    else:
        snapshot_text = snapshot.stdout
        bars = matching_lines(snapshot_text.splitlines())
        if bars:
            errors.append(f"clean-boot BAR2/HOST_CPU/PTE baseline required; found {len(bars)}")
        critical = hard_stop_matches(snapshot_text.splitlines())
        if critical:
            errors.append(f"clean-boot hard-stop baseline required; found {len(critical)}")
    return errors, srcversion, snapshot_text, deployment_observed


def cursor_now() -> str:
    result = journal(["-k", "-b", "-n", "0", "--show-cursor", "--no-pager"], timeout=5)
    if result.returncode:
        raise RuntimeError(f"journal cursor query failed: {result.stderr.strip()}")
    for line in result.stdout.splitlines():
        if line.startswith("-- cursor: "):
            return line.removeprefix("-- cursor: ").strip()
    raise RuntimeError("journalctl did not provide a kernel cursor")


def journal_delta(cursor: str) -> str:
    result = journal(
        ["-k", "-b", "--after-cursor", cursor, "--no-pager", "-o", "short-iso"],
        timeout=FINAL_JOURNAL_QUERY_TIMEOUT_SECONDS,
    )
    if result.returncode:
        raise RuntimeError(f"journal delta query failed: {result.stderr.strip()}")
    return result.stdout


def process_group_members(pgid: int) -> list[tuple[int, str]]:
    members: list[tuple[int, str]] = []
    for stat_path in Path("/proc").glob("[0-9]*/stat"):
        try:
            raw = stat_path.read_text()
            tail = raw[raw.rfind(")") + 2 :].split()
            state = tail[0]
            group = int(tail[2])
            if group == pgid and state != "Z":
                members.append((int(stat_path.parent.name), state))
        except (OSError, ValueError, IndexError):
            continue
    return members


def signal_process_group(proc: subprocess.Popen[bytes], sig: int) -> None:
    # The group can outlive its leader (for example, a wrapper exits while a
    # child still holds the X connection), so signal by pgid even after the
    # Popen leader has already exited.
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        pass


def _wait_group_gone(pgid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_group_members(pgid):
            return True
        time.sleep(0.05)
    return not process_group_members(pgid)


def stop_process_group(
    proc: subprocess.Popen[bytes],
    sigint_grace: float = PROCESS_GROUP_SIGINT_GRACE_SECONDS,
    sigterm_grace: float = PROCESS_GROUP_SIGTERM_GRACE_SECONDS,
) -> None:
    pgid = proc.pid
    signal_process_group(proc, signal.SIGINT)
    if _wait_group_gone(pgid, sigint_grace):
        proc.wait(timeout=PROCESS_WAIT_TIMEOUT_SECONDS)
        return
    signal_process_group(proc, signal.SIGTERM)
    if _wait_group_gone(pgid, sigterm_grace):
        proc.wait(timeout=PROCESS_WAIT_TIMEOUT_SECONDS)
        return
    signal_process_group(proc, signal.SIGKILL)
    gone = _wait_group_gone(pgid, PROCESS_GROUP_SIGKILL_GRACE_SECONDS)
    try:
        proc.wait(timeout=PROCESS_WAIT_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"process-group leader {proc.pid} was not reaped") from exc
    survivors = process_group_members(pgid)
    if not gone or survivors:
        raise RuntimeError(
            f"process group {pgid} still has live members after SIGKILL: {survivors}"
        )


class ProcessGroupStopper:
    """Serialize repeated stop requests from the journal and service threads."""

    def __init__(
        self,
        proc: subprocess.Popen[bytes],
        *,
        sigint_grace: float = PROCESS_GROUP_SIGINT_GRACE_SECONDS,
        sigterm_grace: float = PROCESS_GROUP_SIGTERM_GRACE_SECONDS,
    ) -> None:
        self.proc = proc
        self.sigint_grace = sigint_grace
        self.sigterm_grace = sigterm_grace
        self._lock = threading.Lock()
        self._stopped = False
        self.error: str | None = None

    def stop(self) -> bool:
        with self._lock:
            if self._stopped:
                return self.error is None
            try:
                if process_group_members(self.proc.pid) or self.proc.poll() is None:
                    stop_process_group(
                        self.proc,
                        sigint_grace=self.sigint_grace,
                        sigterm_grace=self.sigterm_grace,
                    )
                else:
                    self.proc.wait(timeout=PROCESS_WAIT_TIMEOUT_SECONDS)
                survivors = process_group_members(self.proc.pid)
                if survivors:
                    raise RuntimeError(
                        f"process group {self.proc.pid} still has live members: {survivors}"
                    )
                if self.proc.poll() is None:
                    raise RuntimeError(f"process-group leader {self.proc.pid} was not reaped")
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                self.error = str(exc)
                self._stopped = True
                return False
            self._stopped = True
            return True


def stop_reason_for_line(line: str) -> str | None:
    if BAR2_HOST_CPU_PTE.search(line):
        return "BAR2/HOST_CPU/PTE"
    matches = hard_stop_matches([line])
    return matches[0][0] if matches else None


def first_stop_signature(lines: Iterable[str]) -> tuple[str, str] | None:
    """Return the first stop-worthy kernel signature in a saved journal delta."""
    for line in lines:
        reason = stop_reason_for_line(line)
        if reason:
            return reason, line.rstrip("\n")
    return None


def final_run_status(
    stopped: dict[str, str], delta: str, delta_error: str | None = None
) -> tuple[str, str | None, tuple[str, str] | None, bool]:
    """Fail closed if the final journal snapshot finds an event the follower missed."""
    reason = stopped.get("reason", "natural-exit")
    trigger_line = stopped.get("line")
    late_signature = first_stop_signature(delta.splitlines())
    failed_closed = bool(delta_error or late_signature)

    if late_signature and reason == "natural-exit":
        reason = "kernel-signature-in-final-journal-delta"
        trigger_line = late_signature[1]
    elif delta_error and reason == "natural-exit":
        reason = "post-run-journal-unavailable"

    return reason, trigger_line, late_signature, failed_closed


def mpv_run_exit_code(
    *,
    reason: str,
    child_returncode: int | None,
    final_failed_closed: bool,
    hardware_decode_confirmed: bool,
    post_window_complete: bool,
) -> int:
    if final_failed_closed:
        return 7
    if reason.startswith("external-signal-"):
        return 9
    if reason != "natural-exit":
        return 7
    if not post_window_complete:
        return 9
    if child_returncode != 0:
        return 6
    return 0 if hardware_decode_confirmed else 8


def passive_post_stop_snapshot(
    *,
    wait_for_cancel: Callable[[float], bool],
    snapshot: Callable[[], str],
    seconds: float = POST_STOP_OBSERVATION_SECONDS,
) -> tuple[str, bool]:
    """Wait passively for delayed kernel records, then snapshot the journal.

    The callback returns true if an explicit operator stop cancels the wait.
    No workload is started or resumed during this interval.
    """
    cancelled = wait_for_cancel(seconds)
    return snapshot(), not cancelled


def detached_unit_properties() -> tuple[str, ...]:
    """Return cgroup and stop properties needed to reap mpv and save evidence."""
    return (
        "--property=KillMode=control-group",
        "--property=KillSignal=SIGINT",
        f"--property=TimeoutStopSec={SYSTEMD_STOP_TIMEOUT_SECONDS}s",
        "--property=SendSIGKILL=yes",
        "--property=UMask=0077",
    )


def consume_journal_lines(lines: Iterable[str], on_trigger) -> str | None:
    """Dispatch the first stop signature from a journal stream."""
    for line in lines:
        reason = stop_reason_for_line(line)
        if reason:
            on_trigger(reason, line)
            return reason
    return None


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def create_arm_token(run_dir: Path) -> str:
    token = secrets.token_hex(32)
    token_path = run_dir / ARM_TOKEN_FILE
    fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        payload = (token + "\n").encode("ascii")
        if os.write(fd, payload) != len(payload):
            raise OSError("short write while saving one-use arm token")
        os.fsync(fd)
    finally:
        os.close(fd)
    return token


def consume_arm_token(run_dir: Path) -> None:
    supplied = os.environ.pop(ARM_TOKEN_ENV, "")
    if not supplied:
        raise RuntimeError("missing manager authorization; direct run is refused")
    token_path = run_dir / ARM_TOKEN_FILE
    try:
        expected = token_path.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise RuntimeError("missing one-use manager authorization file") from exc
    if not hmac.compare_digest(expected, supplied):
        raise RuntimeError("invalid manager authorization token")
    token_path.unlink()


def mapped_driver_status(
    pid: int, expected_path: Path, expected_sha256: str
) -> tuple[bool, str]:
    try:
        expected = expected_path.resolve(strict=True)
        expected_stat = expected.stat()
        maps_text = Path(f"/proc/{pid}/maps").read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"cannot inspect mpv driver mapping: {exc}"
    expected_dev = (os.major(expected_stat.st_dev), os.minor(expected_stat.st_dev))
    for line in maps_text.splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) != 6 or not fields[5].startswith("/"):
            continue
        mapped_name = fields[5].removesuffix(" (deleted)")
        try:
            mapped_path = Path(mapped_name).resolve(strict=True)
            major, minor = fields[3].split(":", 1)
            mapped_dev = (int(major, 16), int(minor, 16))
            mapped_inode = int(fields[4])
        except (OSError, ValueError):
            continue
        if mapped_path != expected:
            continue
        if mapped_dev != expected_dev or mapped_inode != expected_stat.st_ino:
            continue
        actual_sha256 = sha256(expected)
        if actual_sha256 != expected_sha256:
            return False, f"mapped VA DSO hash mismatch: {actual_sha256}"
        return True, f"path={expected} sha256={actual_sha256} inode={mapped_inode}"
    return False, f"pinned VA DSO is not mapped by mpv pid {pid}"


def wait_for_mapped_driver(
    pid: int,
    expected_path: Path,
    expected_sha256: str,
    *,
    timeout: float = DRIVER_MAP_WAIT_SECONDS,
    process_alive: Callable[[int], bool] | None = None,
) -> tuple[bool, str]:
    alive = process_alive or (lambda process_id: Path(f"/proc/{process_id}").exists())
    deadline = time.monotonic() + timeout
    last_detail = "mapping has not appeared"
    while time.monotonic() < deadline and alive(pid):
        matched, last_detail = mapped_driver_status(pid, expected_path, expected_sha256)
        if matched:
            return True, last_detail
        time.sleep(0.1)
    return False, last_detail


def mpv_ipc_request(
    socket_path: Path,
    command: list[object],
    timeout: float = 2.0,
) -> dict[str, object]:
    request = json.dumps({"command": command}, separators=(",", ":")).encode("utf-8") + b"\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(str(socket_path))
        client.sendall(request)
        response = bytearray()
        while b"\n" not in response:
            chunk = client.recv(4096)
            if not chunk:
                raise RuntimeError("mpv IPC closed before replying")
            response.extend(chunk)
    parsed = json.loads(bytes(response).split(b"\n", 1)[0])
    if not isinstance(parsed, dict):
        raise RuntimeError("mpv IPC returned a non-object response")
    return parsed


def mpv_screenshot_request(socket_path: Path) -> dict[str, object]:
    position = mpv_ipc_request(socket_path, ["get_property", "time-pos"])
    if position.get("error") != "success":
        raise RuntimeError(
            f"mpv time-pos query failed: {position.get('error')!r}"
        )
    raw_time = position.get("data")
    if (
        isinstance(raw_time, bool)
        or not isinstance(raw_time, (int, float))
        or not math.isfinite(float(raw_time))
    ):
        raise RuntimeError(f"mpv returned an invalid time-pos: {raw_time!r}")
    screenshot = mpv_ipc_request(socket_path, ["screenshot", "video"])
    return {
        "time_pos_response": position,
        "time_pos_seconds": float(raw_time),
        "screenshot_response": screenshot,
    }


def mpv_argv(run_dir: Path, ipc_path: Path) -> list[str]:
    screenshots = run_dir / "mpv-screenshots"
    screenshots.mkdir(parents=True, exist_ok=True, mode=0o700)
    return [
        str(MPV),
        "--no-config",
        "--force-window=yes",
        "--start=527",
        "--length=360",
        "--hwdec=vaapi-copy",
        "--hwdec-codecs=h264",
        "--vo=xv",
        "--screenshot-format=png",
        f"--screenshot-directory={screenshots}",
        f"--input-ipc-server={ipc_path}",
        "--title=K4200 VAAPI pixelation review (08:47-08:53, audio enabled)",
        str(INPUT),
    ]


def service_run(run_dir: Path) -> int:
    try:
        consume_arm_token(run_dir)
    except (OSError, RuntimeError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr, flush=True)
        return 2
    try:
        manifest_path = run_dir / DEPLOYMENT_MANIFEST_NAME
        deployment_manifest = load_deployment_manifest(manifest_path)
    except (OSError, ValueError) as exc:
        print(f"PREFLIGHT_FAIL: deployment manifest: {exc}", file=sys.stderr, flush=True)
        return 2
    run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(run_dir, 0o700)
    # Save the cursor before scanning the full boot log. The later delta check
    # catches any fault racing with that scan; the follower starts at the same
    # cursor, so there is no gap between preflight and live monitoring.
    try:
        sync_journal()
        cursor = cursor_now()
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        write_text(run_dir / "supervisor-error.txt", f"journal cursor query failed: {exc}\n")
        return 3
    errors, srcversion, snapshot, deployment_observed = preflight(
        require_desktop=True,
        deployment_manifest=deployment_manifest,
        require_deployment=True,
    )
    write_text(run_dir / "preflight-kernel.log", snapshot)
    write_text(
        run_dir / "deployment-observed.json",
        json.dumps(deployment_observed, indent=2, sort_keys=True) + "\n",
    )
    write_text(
        run_dir / "preflight.txt",
        "\n".join(
            [
                f"boot_id={Path('/proc/sys/kernel/random/boot_id').read_text().strip()}",
                f"kernel={os.uname().release}",
                f"loaded_srcversion={srcversion}",
                f"input_sha256={sha256(INPUT) if INPUT.exists() else 'missing'}",
                f"plugin_sha256={sha256(PLUGIN) if PLUGIN.exists() else 'missing'}",
                f"DISPLAY={os.environ.get('DISPLAY', '')}",
                f"baseline_BAR2_count={len(matching_lines(snapshot.splitlines()))}",
                f"PREFLIGHT={'FAIL' if errors else 'PASS'}",
                *(f"error={item}" for item in errors),
            ]
        )
        + "\n",
    )
    if errors:
        print("PREFLIGHT_FAIL", *errors, sep="\n", flush=True)
        return 2

    try:
        first_delta = journal_delta(cursor)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        write_text(run_dir / "supervisor-error.txt", str(exc) + "\n")
        return 3
    if matching_lines(first_delta.splitlines()) or hard_stop_matches(first_delta.splitlines()):
        write_text(run_dir / "kernel-delta-before-player.log", first_delta)
        write_text(run_dir / "stop-reason.txt", "kernel signature appeared during preflight\n")
        return 4

    try:
        unit = (run_dir / "unit.txt").read_text(encoding="utf-8").strip()
    except OSError:
        unit = os.environ.get("SYSTEMD_UNIT", "manual")
    ipc_path = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / f"nvmpv-{run_dir.name}.sock"
    argv = mpv_argv(run_dir, ipc_path)
    try:
        env = child_environment()
    except RuntimeError as exc:
        write_text(run_dir / "supervisor-error.txt", f"child environment refused: {exc}\n")
        return 2
    write_text(run_dir / "command-argv.json", json.dumps(argv, indent=2) + "\n")
    write_text(run_dir / "journal-cursor.txt", cursor + "\n")
    write_text(run_dir / "unit.txt", unit + "\n")

    follower = subprocess.Popen(
        [
            str(JOURNALCTL), "-k", "-b", "--follow", "--after-cursor", cursor,
            "--no-pager", "-o", "short-iso",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    time.sleep(0.15)
    if follower.poll() is not None:
        error_text = follower.stderr.read().strip() if follower.stderr else "journal follower exited"
        write_text(run_dir / "supervisor-error.txt", f"journal follower failed before mpv start: {error_text}\n")
        return 3

    stop_event = threading.Event()
    stopped: dict[str, str] = {}
    lock = threading.Lock()
    child_holder: dict[str, object] = {}

    def request_stop(reason: str, line: str = "") -> None:
        with lock:
            if "reason" in stopped:
                return
            stopped["reason"] = reason
            if line:
                stopped["line"] = line.rstrip("\n")
            stop_event.set()
            child_stopper = child_holder.get("stopper")
        if isinstance(child_stopper, ProcessGroupStopper):
            child_stopper.stop()

    def journal_reader() -> None:
        assert follower.stdout is not None
        def trigger(reason: str, line: str) -> None:
            with (run_dir / "kernel-trigger.log").open("a", encoding="utf-8") as output:
                output.write(line)
                output.flush()
            request_stop(reason, line)

        matched = consume_journal_lines(follower.stdout, trigger)
        if matched:
            return
        child = child_holder.get("child")
        if (child is None or child.poll() is None) and not stop_event.is_set():
            err = ""
            if follower.stderr is not None:
                err = follower.stderr.read().strip()
            request_stop("journal-monitor-ended", err or "journal follower exited unexpectedly")

    monitor = threading.Thread(target=journal_reader, name="kernel-journal-monitor", daemon=True)
    monitor.start()

    # The follower is live before mpv is launched. A line arriving in the
    # small launch window is buffered by journalctl and handled immediately.
    if stop_event.wait(0.15) or follower.poll() is not None:
        if not stopped:
            stderr = follower.stderr.read().strip() if follower.stderr else "journal follower exited"
            stopped["reason"] = "journal-monitor-ended"
            stopped["line"] = stderr
        follower.terminate() if follower.poll() is None else None
        try:
            follower.wait(timeout=2)
        except subprocess.TimeoutExpired:
            follower.kill()
            follower.wait(timeout=2)
        monitor.join(timeout=2)
        write_text(run_dir / "supervisor-error.txt", f"journal monitor not ready: {stopped.get('line', '')}\n")
        return 3

    log_file = (run_dir / "mpv.log").open("wb")
    try:
        child = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
    except OSError as exc:
        log_file.close()
        follower.terminate()
        follower.wait(timeout=2)
        write_text(run_dir / "supervisor-error.txt", f"mpv launch failed: {exc}\n")
        return 5
    child_stopper = ProcessGroupStopper(child)
    with lock:
        child_holder["child"] = child
        child_holder["stopper"] = child_stopper
    if stop_event.is_set():
        child_stopper.stop()

    previous_handlers: dict[int, object] = {}
    post_stop_cancel = threading.Event()

    def external_stop(signum: int, _frame: object) -> None:
        post_stop_cancel.set()
        request_stop(f"external-signal-{signal.Signals(signum).name}")

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGUSR1):
        previous_handlers[sig] = signal.signal(sig, external_stop)

    print(f"MPV_STARTED pid={child.pid} run_dir={run_dir}", flush=True)
    mesa_section = deployment_manifest["mesa"]
    assert isinstance(mesa_section, dict)
    mapped_driver, driver_map_detail = wait_for_mapped_driver(
        child.pid,
        PLUGIN,
        str(mesa_section["dso_sha256"]),
        process_alive=lambda _pid: child.poll() is None,
    )
    write_text(
        run_dir / "driver-mapping.txt",
        f"mapped={str(mapped_driver).lower()}\n{driver_map_detail}\n",
    )
    if not mapped_driver:
        request_stop("va-driver-mapping-unconfirmed", driver_map_detail)
    screenshot_dir = run_dir / "mpv-screenshots"
    screenshot_attempts = 0
    screenshot_ipc_successes = 0
    next_screenshot_at = time.monotonic() + FIRST_SCREENSHOT_DELAY_SECONDS
    try:
        while child.poll() is None and not stop_event.is_set():
            now = time.monotonic()
            if now >= next_screenshot_at:
                screenshot_attempts += 1
                record: dict[str, object] = {
                    "utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "attempt": screenshot_attempts,
                }
                try:
                    response = mpv_screenshot_request(ipc_path)
                except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
                    record["error"] = str(exc)
                    next_screenshot_at = now + FIRST_SCREENSHOT_DELAY_SECONDS
                else:
                    record["response"] = response
                    record["media_time_seconds"] = response.get("time_pos_seconds")
                    screenshot_response = response.get("screenshot_response")
                    succeeded = (
                        isinstance(screenshot_response, dict)
                        and screenshot_response.get("error") == "success"
                    )
                    record["command_succeeded"] = succeeded
                    if succeeded:
                        screenshot_ipc_successes += 1
                        next_screenshot_at = now + SCREENSHOT_INTERVAL_SECONDS
                    else:
                        next_screenshot_at = now + FIRST_SCREENSHOT_DELAY_SECONDS
                with (run_dir / "screenshot-ipc.jsonl").open("a", encoding="utf-8") as output:
                    output.write(json.dumps(record, sort_keys=True) + "\n")
                    output.flush()
            if stop_event.wait(0.25):
                break
            if follower.poll() is not None and not stop_event.is_set():
                request_stop("journal-monitor-ended", "journalctl follower exited")
                break
    finally:
        child_stopper.stop()
        stop_event.set()
        if follower.poll() is None:
            follower.terminate()
        try:
            follower.wait(timeout=2)
        except subprocess.TimeoutExpired:
            follower.kill()
            follower.wait(timeout=2)
        monitor.join(timeout=2)
        log_file.close()

    post_start_utc = dt.datetime.now(dt.timezone.utc)
    post_start_monotonic = time.monotonic()
    post_window_complete = False

    def final_journal_snapshot() -> str:
        sync_journal()
        return journal_delta(cursor)

    try:
        delta, post_window_complete = passive_post_stop_snapshot(
            wait_for_cancel=post_stop_cancel.wait,
            snapshot=final_journal_snapshot,
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        delta_error = str(exc)
        delta = f"JOURNAL_DELTA_ERROR: {delta_error}\n"
    else:
        delta_error = None
    post_end_monotonic = time.monotonic()
    post_end_utc = dt.datetime.now(dt.timezone.utc)
    for sig, handler in previous_handlers.items():
        signal.signal(sig, handler)
    write_text(
        run_dir / "post-stop-observation.txt",
        f"requested_seconds={POST_STOP_OBSERVATION_SECONDS:.1f}\n"
        f"started_utc={post_start_utc.isoformat()}\n"
        f"finished_utc={post_end_utc.isoformat()}\n"
        f"observed_seconds={post_end_monotonic - post_start_monotonic:.3f}\n"
        f"window_complete={str(post_window_complete).lower()}\n"
        f"operator_cancelled={str(post_stop_cancel.is_set()).lower()}\n",
    )
    write_text(run_dir / "kernel-delta.log", delta)
    screenshot_files = sorted(screenshot_dir.glob("*.png"))
    write_text(
        run_dir / "screenshot-summary.txt",
        f"attempts={screenshot_attempts}\n"
        f"ipc_successes={screenshot_ipc_successes}\n"
        f"png_files={len(screenshot_files)}\n"
        + ("first_file=" + str(screenshot_files[0]) + "\n" if screenshot_files else ""),
    )
    reason, trigger_line, late_signature, final_failed_closed = final_run_status(
        stopped, delta, delta_error
    )
    stop_error = child_stopper.error
    if stop_error:
        write_text(run_dir / "process-group-stop-error.txt", stop_error + "\n")
        final_failed_closed = True
    mpv_log_error = None
    try:
        mpv_log_text = (run_dir / "mpv.log").read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        mpv_log_text = ""
        mpv_log_error = str(exc)
    hardware_evidence = classify_mpv_hardware_decode(
        mpv_log_text, actual_driver_mapping=mapped_driver
    )
    hardware_status = (
        "CONFIRMED_NOUVEAU_VAAPI_H264"
        if hardware_evidence.confirmed
        else "NOT_CONFIRMED"
    )
    write_text(
        run_dir / "result.txt",
        f"run_dir={run_dir}\nmpv_pid={child.pid}\nmpv_returncode={child.returncode}\n"
        f"stop_reason={reason}\n"
        f"hardware_decode={hardware_status}\n"
        f"hardware_decode_missing={','.join(hardware_evidence.missing_evidence()) or 'none'}\n"
        f"actual_driver_mapping={str(mapped_driver).lower()}\n"
        f"post_window_complete={str(post_window_complete).lower()}\n"
        f"process_group_stopped={str(stop_error is None).lower()}\n"
        f"video_screenshots={len(screenshot_files)}\n"
        "visible_video_confirmation=USER_REQUIRED\n"
        + (f"mpv_log_read_error={mpv_log_error}\n" if mpv_log_error else "")
        + (f"trigger_line={trigger_line}\n" if trigger_line else "")
        + (
            f"final_kernel_signature={late_signature[0]}\n"
            f"final_kernel_line={late_signature[1]}\n"
            if late_signature
            else ""
        )
        + (f"final_journal_error={delta_error}\n" if delta_error else ""),
    )
    stop_error = child_stopper.error
    if stop_error:
        write_text(run_dir / "process-group-stop-error.txt", stop_error + "\n")
        final_failed_closed = True
    print(f"MPV_FINISHED returncode={child.returncode} stop_reason={reason}", flush=True)
    return mpv_run_exit_code(
        reason=reason,
        child_returncode=child.returncode,
        final_failed_closed=final_failed_closed,
        hardware_decode_confirmed=hardware_evidence.confirmed,
        post_window_complete=post_window_complete,
    )


def manager_start(run_dir: Path, deployment_manifest_path: Path) -> int:
    run_dir = run_dir.expanduser().resolve()
    deployment_manifest_path = deployment_manifest_path.expanduser().resolve()
    try:
        manifest_bytes = deployment_manifest_path.read_bytes()
        deployment_manifest = validate_deployment_manifest(json.loads(manifest_bytes))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"PREFLIGHT_FAIL deployment manifest: {exc}")
        return 2
    errors, _srcversion, _snapshot, _observed = preflight(
        require_desktop=True,
        deployment_manifest=deployment_manifest,
        require_deployment=True,
    )
    if errors:
        print("PREFLIGHT_FAIL", *errors, sep="\n")
        return 2
    run_dir.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    run_dir.mkdir(mode=0o700, exist_ok=False)
    manifest_snapshot = run_dir / DEPLOYMENT_MANIFEST_NAME
    manifest_fd = os.open(
        manifest_snapshot, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
    try:
        os.write(manifest_fd, manifest_bytes)
        os.fsync(manifest_fd)
    finally:
        os.close(manifest_fd)
    token = create_arm_token(run_dir)
    unit = "nouveau-mpv-" + run_dir.name[-18:].replace("_", "-") + ".service"
    command = [
        str(SYSTEMD_RUN), "--user", "--unit", unit, "--description",
        "Controlled Nouveau VAAPI pixelation review", "--collect", "--no-block",
        *detached_unit_properties(), "--setenv=DISPLAY=" + os.environ["DISPLAY"],
        "--setenv=SYSTEMD_UNIT=" + unit,
        "--setenv=HOME=" + str(Path.home()),
        "--setenv=XDG_RUNTIME_DIR=" + os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"),
        "--setenv=LIBVA_DRIVER_NAME=nouveau",
        "--setenv=LIBVA_DRIVERS_PATH=" + str(PLUGIN.parent),
        f"--setenv={ARM_TOKEN_ENV}={token}",
    ]
    for name in (
        "XAUTHORITY",
        "XDG_SESSION_ID",
        "DBUS_SESSION_BUS_ADDRESS",
        "PULSE_SERVER",
        "PIPEWIRE_REMOTE",
    ):
        if os.environ.get(name):
            command.append(f"--setenv={name}={os.environ[name]}")
    command += [sys.executable, str(Path(__file__).resolve()), "run", str(run_dir)]
    try:
        result = subprocess.run(
            command, check=False, text=True, capture_output=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        try:
            (run_dir / ARM_TOKEN_FILE).unlink()
        except FileNotFoundError:
            pass
        write_text(run_dir / "supervisor-error.txt", f"systemd-run failed: {exc}\n")
        print(f"REFUSED: could not start detached unit: {exc}")
        return 3
    if result.returncode:
        try:
            (run_dir / ARM_TOKEN_FILE).unlink()
        except FileNotFoundError:
            pass
        print(result.stderr.strip() or result.stdout.strip())
        return result.returncode
    write_text(run_dir / "unit.txt", unit + "\n")
    print(f"DETACHED_UNIT={unit}")
    print(f"RUN_DIR={run_dir}")
    print(f"STOP_COMMAND={Path(__file__).resolve()} stop {unit}")
    return 0


def manager_stop(unit: str) -> int:
    result = subprocess.run(
        [str(SYSTEMCTL), "--user", "stop", unit],
        check=False,
        text=True,
        capture_output=True,
        timeout=SYSTEMCTL_STOP_COMMAND_TIMEOUT_SECONDS,
    )
    if result.returncode:
        print(result.stderr.strip() or result.stdout.strip())
        return result.returncode
    print(f"STOP_REQUEST_COMPLETED={unit}; inspect run result for process-group status")
    return 0


def manager_status(unit: str) -> int:
    result = subprocess.run(
        [str(SYSTEMCTL), "--user", "--no-pager", "status", unit],
        check=False,
        text=True,
        timeout=10,
    )
    return result.returncode


def run_self_test() -> int:
    samples = [
        "engine 05 [BAR2] client 07 [HUB/HOST_CPU] reason 02 [PTE]",
        "engine 05 [BAR2] client 07 [HUB/HOST_CPU] reason 02 [PTE]",
    ]
    if len(matching_lines(samples)) != 2:
        raise AssertionError("BAR2 matcher control failed")
    if stop_reason_for_line(samples[0]) != "BAR2/HOST_CPU/PTE":
        raise AssertionError("BAR2 stop classification failed")
    if stop_reason_for_line("ordinary kernel line") is not None:
        raise AssertionError("ordinary line caused a stop")

    hard_samples = [
        ("PRIV_VIOLATION", "nouveau: PRIV_VIOLATION on channel 2"),
        ("CTXSW_TIMEOUT", "nouveau: CTXSW_TIMEOUT on channel 2"),
        ("SCHED_ERROR_0A", "nouveau: SCHED_ERROR 0a"),
        ("GPU-MMU-fault", "nouveau 0000:01:00.0: fifo: fault at 0x1000 engine 00 [GR]"),
        ("channel-killed", "nouveau: channel 2 killed"),
        ("failed-to-idle", "nouveau: failed to idle engine"),
        ("PROP/RT-overrun", "nouveau: PROP trap"),
        ("PROP/RT-overrun", "nouveau: RT_WIDTH_OVERRUN"),
        ("SIGBUS", "process received SIGBUS"),
        ("GPU-reset/fallen-off-bus", "nouveau: GPU has fallen off the bus"),
        ("kernel-BUG", "kernel: BUG: unable to handle page fault"),
        ("kernel-Oops", "kernel: Oops: 0000 [#1] PREEMPT SMP"),
        ("kernel-WARNING", "kernel: WARNING: CPU: 3 PID: 19 at example.c:7"),
        ("KASAN", "kernel: KASAN: use-after-free in example"),
        ("KCSAN", "kernel: KCSAN: data-race in example"),
        ("KFENCE", "kernel: KFENCE: use-after-free in example"),
        ("UBSAN", "kernel: UBSAN: array-index-out-of-bounds"),
        ("lockdep", "kernel: possible circular locking dependency detected"),
    ]
    for expected, sample in hard_samples:
        if stop_reason_for_line(sample) != expected:
            raise AssertionError(f"hard-stop classification failed: {expected} / {sample}")
    if stop_reason_for_line("ACPI Warning: unrelated firmware message") is not None:
        raise AssertionError("unrelated ACPI warning caused a stop")

    child_code = (
        "import signal,time; signal.signal(signal.SIGINT, signal.SIG_IGN); time.sleep(30)"
    )
    parent_code = (
        "import signal,subprocess,sys,time; "
        f"subprocess.Popen([{sys.executable!r}, '-c', {child_code!r}]); "
        "signal.signal(signal.SIGINT, lambda *_: sys.exit(0)); time.sleep(30)"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", parent_code],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    pgid = child.pid
    ready_deadline = time.monotonic() + 3.0
    members = process_group_members(pgid)
    while len(members) < 2 and time.monotonic() < ready_deadline:
        time.sleep(0.02)
        members = process_group_members(pgid)
    if len(members) < 2:
        ProcessGroupStopper(child, sigint_grace=0.3, sigterm_grace=0.5).stop()
        raise AssertionError("synthetic child did not join the supervised process group")
    seen: list[tuple[str, str]] = []
    child_stopper = ProcessGroupStopper(child, sigint_grace=0.3, sigterm_grace=0.5)

    def fake_trigger(reason: str, line: str) -> None:
        seen.append((reason, line.rstrip("\n")))
        child_stopper.stop()

    event_stream = io.StringIO(
        "unrelated kernel record\n"
        "nouveau: fifo: fault 00 [READ] at 0x433000 engine 05 [BAR2] "
        "client 07 [HUB/HOST_CPU] reason 02 [PTE]\n"
        "later record should not be consumed\n"
    )
    try:
        consumed = consume_journal_lines(event_stream, fake_trigger)
        if consumed != "BAR2/HOST_CPU/PTE" or len(seen) != 1:
            raise AssertionError("journal-line monitor did not stop on the first BAR2 event")
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and process_group_members(pgid):
            time.sleep(0.05)
        if child.poll() is None or process_group_members(pgid):
            raise AssertionError("process-group stop left a live process")
    finally:
        if child.poll() is None or process_group_members(pgid):
            child_stopper.stop()
    print(
        "SUPERVISOR_SELF_TEST_PASS matcher=pass hard-stop-cases=18 "
        "journal-trigger=pass process-group-tree=stopped"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    check = sub.add_parser("check-only", help="CPU/read-only baseline check; no VA device is opened")
    check.add_argument("--no-desktop", action="store_true", help="skip X11 checks for CPU-only inspection")
    check.add_argument("--deployment-manifest", type=Path)
    start = sub.add_parser("start", help="prepare and start detached test (requires --arm)")
    start.add_argument("--arm", action="store_true", help="explicitly authorize this one VA workload")
    start.add_argument("--run-dir", type=Path)
    start.add_argument("--deployment-manifest", type=Path, required=True)
    run = sub.add_parser("run", help=argparse.SUPPRESS)
    run.add_argument("run_dir", type=Path)
    stop = sub.add_parser("stop", help="stop one detached test via its user-systemd unit")
    stop.add_argument("unit")
    status = sub.add_parser("status", help="show one detached test's user-systemd unit")
    status.add_argument("unit")
    sub.add_parser("self-test", help="CPU-only synthetic matcher and process-tree test")
    args = parser.parse_args(argv)

    if args.action == "self-test":
        return run_self_test()
    if args.action == "check-only":
        try:
            deployment = (
                load_deployment_manifest(args.deployment_manifest)
                if args.deployment_manifest else None
            )
        except ValueError as exc:
            print(f"PREFLIGHT_FAIL deployment manifest: {exc}")
            return 2
        errors, srcversion, snapshot, observed = preflight(
            require_desktop=not args.no_desktop,
            deployment_manifest=deployment,
        )
        print(f"kernel={os.uname().release}")
        print(f"loaded_srcversion={srcversion}")
        print(f"BAR2_HOST_CPU_PTE_COUNT={len(matching_lines(snapshot.splitlines()))}")
        if observed:
            print("DEPLOYMENT_OBSERVED=" + json.dumps(observed, sort_keys=True))
        if errors:
            print("PREFLIGHT_FAIL")
            print("RUN_ELIGIBLE=false")
            for error in errors:
                print(f"error={error}")
            return 2
        if deployment is None:
            print("BASELINE_CHECK_PASS")
            print("DEPLOYMENT_VERIFIED=false")
            print("RUN_ELIGIBLE=false")
        elif args.no_desktop:
            print("DEPLOYMENT_CHECK_PASS")
            print("DEPLOYMENT_VERIFIED=true")
            print("RUN_ELIGIBLE=false")
        else:
            print("PREFLIGHT_PASS")
            print("DEPLOYMENT_VERIFIED=true")
            print("RUN_ELIGIBLE=true")
        return 0
    if args.action == "start":
        if not args.arm:
            print("REFUSED: use --arm only from a logged-in X11 desktop after fresh-boot baseline=0")
            return 2
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_dir = args.run_dir or RUN_ROOT / f"pixelation-08m47-{stamp}"
        return manager_start(run_dir, args.deployment_manifest)
    if args.action == "run":
        return service_run(args.run_dir)
    if args.action == "stop":
        return manager_stop(args.unit)
    if args.action == "status":
        return manager_status(args.unit)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
