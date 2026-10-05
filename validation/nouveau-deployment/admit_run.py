#!/usr/bin/env python3
"""Read-only post-boot admission check for one reviewed Nouveau experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from finalize_install import decompress_module, embedded_module_hashes, sha256_file
from finalize_install import embedded_module_options

ROOT = Path(__file__).resolve().parents[2]
PCI_BDF = re.compile(r"[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]")
sys.path.insert(0, str(ROOT / "validation/detached-mpv-supervisor"))
from bar2_baseline import matching_lines  # noqa: E402
from detached_mpv_supervisor import (  # noqa: E402
    HARD_STOP_PATTERNS,
    current_players,
)
import detached_mpv_supervisor as supervisor_module  # noqa: E402


def command(argv: list[str], *, timeout: float = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def bound_nouveau_pci_functions(
    driver_root: Path = Path("/sys/bus/pci/drivers/nouveau"),
) -> list[str]:
    try:
        entries = tuple(driver_root.iterdir())
    except OSError:
        return []
    return sorted(entry.name for entry in entries if PCI_BDF.fullmatch(entry.name))


def load_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema") != 2:
        raise ValueError("deployment manifest must be schema 2 object")
    if manifest.get("status") != "FINALIZED_NOT_REBOOTED_NOT_RUNTIME_VERIFIED":
        raise ValueError("deployment manifest is not a finalized pre-reboot record")
    required = {
        "kernel", "nouveau", "initramfs", "mesa", "tools", "input",
        "build_manifest", "deployment_plan", "software_reference",
    }
    if not required <= manifest.keys():
        raise ValueError(f"deployment manifest lacks {sorted(required - manifest.keys())}")
    if "diag_bar2_map" in manifest["nouveau"].get("parameters", {}):
        gpu = manifest.get("gpu")
        if not isinstance(gpu, dict) or not PCI_BDF.fullmatch(
            str(gpu.get("pci_bdf", ""))
        ):
            raise ValueError("ambient manifest lacks a valid pinned GPU PCI BDF")
        if not isinstance(manifest.get("module_options"), dict):
            raise ValueError("ambient manifest lacks verified early-load module options")
        for name in (
            "ambient_correlator", "ambient_ring_exporter", "ambient_ring_headroom",
            "xrdp_bar2_reproducer", "xrdp_trigger_profile",
        ):
            if not isinstance(manifest.get("tools", {}).get(name), dict):
                raise ValueError(f"ambient manifest lacks pinned {name}")
    return manifest


def gpu_identity_reasons(
    manifest: dict[str, Any], snapshot: dict[str, Any],
) -> list[str]:
    if "diag_bar2_map" not in manifest["nouveau"].get("parameters", {}):
        return []
    gpu = manifest.get("gpu")
    expected_bdf = gpu.get("pci_bdf") if isinstance(gpu, dict) else None
    expected_devices = [expected_bdf] if isinstance(expected_bdf, str) else None
    devices = snapshot.get("bound_nouveau_pci_functions")
    if devices == expected_devices:
        return []
    return [f"Nouveau PCI device set mismatch: {devices!r} != {expected_devices!r}"]


def verify_execution_identity(
    manifest: dict[str, Any],
    running_paths: dict[str, Path] | None = None,
) -> None:
    if running_paths is None:
        running_paths = {
            "admission_check": Path(__file__).resolve(strict=True),
            "supervisor": Path(supervisor_module.__file__).resolve(strict=True),
        }
    for name, running_path in running_paths.items():
        item = manifest["tools"].get(name)
        if not isinstance(item, dict):
            raise ValueError(f"manifest lacks executing tool {name}")
        expected_path = Path(item["path"]).resolve(strict=True)
        actual_path = running_path.resolve(strict=True)
        if actual_path != expected_path:
            raise ValueError(
                f"running {name} path mismatch: {actual_path} != {expected_path}"
            )
        if sha256_file(actual_path) != item.get("sha256"):
            raise ValueError(f"running {name} SHA-256 mismatch")
    if "diag_bar2_map" in manifest["nouveau"].get("parameters", {}):
        for name in (
            "ambient_correlator", "ambient_ring_exporter", "ambient_ring_headroom",
            "xrdp_bar2_reproducer", "xrdp_trigger_profile",
        ):
            item = manifest["tools"][name]
            path = Path(item["path"]).resolve(strict=True)
            if not path.is_file() or sha256_file(path) != item.get("sha256"):
                raise ValueError(f"pinned ambient tool path/hash mismatch: {name}")


def read_loaded_module_parameter(name: str) -> str:
    path = Path("/sys/module/nouveau/parameters") / name
    if os.geteuid() == 0:
        result = command(["/usr/bin/cat", str(path)], timeout=5)
    else:
        result = command(["sudo", "-n", "/usr/bin/cat", str(path)], timeout=5)
    return result.stdout.strip() if result.returncode == 0 else "MISSING"


def parse_ambient_journal(
    payload: str,
    manifest: dict[str, Any],
    boot_id: str,
) -> tuple[bool, dict[str, Any] | None, str]:
    item = manifest["tools"].get("ambient_correlator")
    if not isinstance(item, dict):
        return False, None, "ambient correlator is not pinned"
    path = Path(item.get("path", ""))
    if not path.is_file() or sha256_file(path) != item.get("sha256"):
        return False, None, "ambient correlator identity mismatch"
    try:
        with tempfile.TemporaryDirectory(prefix="nouveau-ambient-admission-") as temporary:
            journal = Path(temporary) / "kernel.jsonl"
            journal.write_text(payload, encoding="utf-8")
            result = command([sys.executable, str(path), str(journal)], timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, None, f"ambient correlator execution failed: {exc}"
    if result.returncode:
        return False, None, result.stderr.strip() or "ambient correlator rejected journal"
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False, None, "ambient correlator output is invalid JSON"
    normalized_boot = boot_id.replace("-", "").lower()
    if report.get("boot_id") != normalized_boot:
        return False, report, "ambient report boot ID mismatch"
    if report.get("module_load_epoch_policy") != "single_nouveau_module_instance_per_boot":
        return False, report, "ambient report lacks the single-module-epoch policy"
    if not report.get("diagnostic_sequence_gaps_rejected"):
        return False, report, "ambient report does not reject sequence gaps"
    if report.get("fault_count", 0) <= 0:
        return False, report, "ambient diagnostic report contains no decoded faults"
    return True, report, "ambient journal parsed with one nonrepeating diagnostic sequence"


def session_errors(env: dict[str, str] | None = None) -> list[str]:
    env = os.environ if env is None else env
    errors: list[str] = []
    expected_uid = os.getuid()
    if os.geteuid() == 0:
        sudo_uid = env.get("SUDO_UID", "")
        if not sudo_uid.isdecimal() or int(sudo_uid) == 0:
            errors.append(
                "root admission requires a non-root SUDO_UID from the desktop user"
            )
            expected_uid = None
        else:
            expected_uid = int(sudo_uid)
    session_id = env.get("XDG_SESSION_ID")
    display = env.get("DISPLAY")
    if not session_id:
        errors.append("XDG_SESSION_ID is missing")
    else:
        result = command([
            "loginctl", "show-session", session_id,
            "-p", "Active", "-p", "Remote", "-p", "Type",
            "-p", "Class", "-p", "Seat", "-p", "Display",
            "-p", "User", "-p", "VTNr",
        ], timeout=5)
        if result.returncode:
            errors.append("loginctl cannot inspect the current desktop session")
        else:
            props = dict(
                line.split("=", 1)
                for line in result.stdout.splitlines()
                if "=" in line
            )
            required = {
                "Active": "yes",
                "Remote": "no",
                "Type": "x11",
                "Class": "user",
                "Seat": "seat0",
            }
            for key, value in required.items():
                if props.get(key) != value:
                    errors.append(f"desktop session {key}={props.get(key)!r}, expected {value!r}")
            if expected_uid is not None and props.get("User") != str(expected_uid):
                errors.append("desktop session belongs to a different Unix user")
            try:
                if int(props.get("VTNr", "0")) <= 0:
                    errors.append("desktop session is not attached to a real VT")
            except ValueError:
                errors.append("desktop session VT number is invalid")
            if props.get("Display") and display and props["Display"] != display:
                errors.append("DISPLAY does not match the active login session")
    if not display:
        errors.append("DISPLAY is missing")
    else:
        xdpyinfo = shutil.which("xdpyinfo")
        if not xdpyinfo:
            errors.append("xdpyinfo is unavailable")
        else:
            result = command([xdpyinfo, "-display", display], timeout=5)
            if result.returncode:
                errors.append("xdpyinfo cannot reach DISPLAY")
    return errors


def parse_journal_json(payload: str, boot_id: str) -> tuple[bool, bool, int, list[str], str]:
    rows = [line for line in payload.splitlines() if line.strip()]
    messages: list[str] = []
    if not rows:
        return False, False, 0, [], "current-boot kernel journal is empty"
    for index, row in enumerate(rows):
        try:
            record = json.loads(row)
        except json.JSONDecodeError:
            return False, False, 0, [], f"invalid journal JSON at record {index}"
        if not isinstance(record, dict):
            return False, False, 0, [], f"journal record {index} is not an object"
        if record.get("_TRANSPORT") != "kernel":
            return False, False, 0, [], f"journal record {index} is not kernel transport"
        observed_boot = str(record.get("_BOOT_ID", "")).replace("-", "")
        if observed_boot != boot_id:
            return True, False, 0, [], f"journal record {index} has a different boot ID"
        message = record.get("MESSAGE", "")
        if not isinstance(message, str):
            message = str(message)
        messages.append(message)
    return True, True, len(matching_lines(messages)), messages, "current-boot kernel journal verified"


def collect_snapshot(manifest: dict[str, Any]) -> dict[str, Any]:
    kernel = subprocess.check_output(["uname", "-r"], text=True).strip()
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except OSError:
        boot_id = ""

    module = manifest["nouveau"]
    initramfs_info = manifest["initramfs"]
    selected_result = command(["modinfo", "-n", "nouveau"])
    selected_path = Path(selected_result.stdout.strip()) if selected_result.returncode == 0 else Path("/")
    installed_compressed_sha = ""
    installed_uncompressed_sha = ""
    installed_srcversion = ""
    vermagic = ""
    if selected_result.returncode == 0 and selected_path.is_file():
        installed_compressed_sha = sha256_file(selected_path)
        with tempfile.TemporaryDirectory(prefix="nouveau-admission-module-") as temporary:
            decompressed = Path(temporary) / "nouveau.ko"
            decompress_module(selected_path, decompressed)
            installed_uncompressed_sha = sha256_file(decompressed)
        src = command(["modinfo", "-F", "srcversion", str(selected_path)])
        vm = command(["modinfo", "-F", "vermagic", str(selected_path)])
        installed_srcversion = src.stdout.strip() if src.returncode == 0 else ""
        vermagic = vm.stdout.strip() if vm.returncode == 0 else ""

    loaded_srcversion = ""
    loaded_parameters: dict[str, str] = {}
    try:
        loaded_srcversion = Path("/sys/module/nouveau/srcversion").read_text().strip()
    except OSError:
        pass
    for name in module.get("parameters", {}):
        loaded_parameters[name] = read_loaded_module_parameter(name)

    initramfs_path = Path(initramfs_info["path"])
    initramfs_sha = sha256_file(initramfs_path) if initramfs_path.is_file() else ""
    embedded_sha = ""
    embedded_records: list[dict[str, str]] = []
    if initramfs_path.is_file():
        with tempfile.TemporaryDirectory(prefix="nouveau-admission-initramfs-") as temporary:
            try:
                embedded_records = embedded_module_hashes(initramfs_path, Path(temporary))
                hashes = {item["uncompressed_sha256"] for item in embedded_records}
                embedded_sha = next(iter(hashes)) if len(hashes) == 1 else "AMBIGUOUS"
            except (OSError, RuntimeError, subprocess.SubprocessError):
                embedded_sha = "UNAVAILABLE"

    module_options = manifest.get("module_options")
    module_options_sha = ""
    embedded_options: list[dict[str, str]] | None = None
    if isinstance(module_options, dict):
        options_path = Path(module_options["path"])
        if options_path.is_file():
            module_options_sha = sha256_file(options_path)
        if initramfs_path.is_file():
            with tempfile.TemporaryDirectory(prefix="nouveau-admission-options-") as temporary:
                embedded_options = embedded_module_options(
                    initramfs_path,
                    options_path,
                    module_options["content_sha256"],
                    module["parameters"],
                    Path(temporary),
                )

    visible = False
    current_boot_records_match = False
    bar2_count = 0
    hard_stops: list[str] = []
    journal_detail = ""
    ambient_parseable = False
    ambient_report: dict[str, Any] | None = None
    ambient_parse_detail = "not required"
    kernel_journal_payload = ""
    try:
        sync = command(["journalctl", "--sync"], timeout=8)
        probe = command(
            ["journalctl", "-k", "-b", "-n", "1", "--no-pager", "-o", "json"],
            timeout=8,
        )
        full = command(["journalctl", "-k", "-b", "--no-pager", "-o", "json"], timeout=30)
        if sync.returncode or probe.returncode or full.returncode:
            journal_detail = "journal synchronization or query failed"
        else:
            probe_ok, probe_boot_ok, _count, _messages, probe_detail = parse_journal_json(
                probe.stdout, boot_id.replace("-", "")
            )
            visible = probe_ok and probe_boot_ok
            full_ok, full_boot_ok, bar2_count, messages, journal_detail = parse_journal_json(
                full.stdout, boot_id.replace("-", "")
            )
            kernel_journal_payload = full.stdout
            current_boot_records_match = full_ok and full_boot_ok
            if visible and not probe_detail:
                journal_detail = probe_detail
            for message in messages:
                for label, pattern in HARD_STOP_PATTERNS.items():
                    if pattern.search(message):
                        hard_stops.append(f"{label}: {message}")
                        break
    except (OSError, subprocess.SubprocessError) as exc:
        journal_detail = f"journal query raised: {exc}"

    if "diag_bar2_map" in module.get("parameters", {}) and bar2_count:
        ambient_parseable, ambient_report, ambient_parse_detail = parse_ambient_journal(
            kernel_journal_payload, manifest, boot_id
        )

    mesa_hashes: dict[str, str] = {}
    for variant, item in manifest["mesa"]["variants"].items():
        path = Path(item["path"])
        mesa_hashes[variant] = sha256_file(path) if path.is_file() else "MISSING"
    tools: dict[str, str] = {}
    for name, item in manifest["tools"].items():
        path = Path(item["path"])
        tools[name] = sha256_file(path) if path.is_file() else "MISSING"
    input_info = manifest["input"]
    input_path = Path(input_info["path"])
    input_sha = sha256_file(input_path) if input_path.is_file() else "MISSING"
    build_manifest = manifest["build_manifest"]
    build_manifest_path = Path(build_manifest["path"])
    build_manifest_sha = (
        sha256_file(build_manifest_path) if build_manifest_path.is_file() else "MISSING"
    )
    deployment_plan = manifest["deployment_plan"]
    deployment_plan_path = Path(deployment_plan["path"])
    deployment_plan_sha = (
        sha256_file(deployment_plan_path) if deployment_plan_path.is_file() else "MISSING"
    )
    reference = manifest["software_reference"]
    reference_manifest_path = Path(reference["manifest_path"])
    reference_container_path = Path(reference["container_path"])
    reference_manifest_sha = (
        sha256_file(reference_manifest_path)
        if reference_manifest_path.is_file() else "MISSING"
    )
    reference_container_sha = (
        sha256_file(reference_container_path)
        if reference_container_path.is_file() else "MISSING"
    )

    return {
        "kernel_release": kernel,
        "boot_id": boot_id,
        "bound_nouveau_pci_functions": bound_nouveau_pci_functions(),
        "loaded_srcversion": loaded_srcversion,
        "selected_module_path": str(selected_path.resolve()) if selected_path.exists() else "MISSING",
        "installed_compressed_sha256": installed_compressed_sha,
        "installed_uncompressed_sha256": installed_uncompressed_sha,
        "installed_srcversion": installed_srcversion,
        "vermagic": vermagic,
        "diag_ctxsw": loaded_parameters.get("diag_ctxsw", ""),
        "module_parameters": loaded_parameters,
        "module_options_file_sha256": module_options_sha,
        "embedded_module_options": embedded_options,
        "initramfs_path": str(initramfs_path.resolve()) if initramfs_path.exists() else "MISSING",
        "initramfs_sha256": initramfs_sha,
        "embedded_nouveau_sha256": embedded_sha,
        "embedded_module_records": embedded_records,
        "journal_visible": visible,
        "journal_current_boot": current_boot_records_match,
        "journal_detail": journal_detail,
        "bar2_pte_count": bar2_count,
        "ambient_fault_correlation_parseable": ambient_parseable,
        "ambient_fault_correlation_report": ambient_report,
        "ambient_fault_correlation_detail": ambient_parse_detail,
        "hard_stops": hard_stops,
        "desktop_errors": session_errors(),
        "players": current_players(),
        "mesa_hashes": mesa_hashes,
        "tool_hashes": tools,
        "input_sha256": input_sha,
        "build_manifest_sha256": build_manifest_sha,
        "deployment_plan_sha256": deployment_plan_sha,
        "software_reference_manifest_sha256": reference_manifest_sha,
        "software_reference_container_sha256": reference_container_sha,
    }


def evaluate_snapshot(manifest: dict[str, Any], snapshot: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    module = manifest["nouveau"]
    initramfs = manifest["initramfs"]
    reasons.extend(gpu_identity_reasons(manifest, snapshot))
    if snapshot.get("kernel_release") != manifest["kernel"]:
        reasons.append("running kernel mismatch")
    if snapshot.get("loaded_srcversion") != module["srcversion"]:
        reasons.append("loaded Nouveau srcversion mismatch")
    if snapshot.get("installed_srcversion") != module["srcversion"]:
        reasons.append("selected installed module srcversion mismatch")
    if snapshot.get("selected_module_path") != module["module_path"]:
        reasons.append("modinfo-selected module path mismatch")
    if snapshot.get("installed_compressed_sha256") != module["installed_compressed_sha256"]:
        reasons.append("installed compressed module hash mismatch")
    if snapshot.get("installed_uncompressed_sha256") != module["installed_uncompressed_sha256"]:
        reasons.append("installed module bytes hash mismatch")
    if snapshot.get("vermagic") != module["vermagic"]:
        reasons.append("installed module vermagic mismatch")
    for name, expected in module["parameters"].items():
        observed = snapshot.get("module_parameters", {}).get(name)
        if observed is None and name == "diag_ctxsw":
            observed = snapshot.get("diag_ctxsw")
        if observed != expected:
            reasons.append(f"loaded {name} parameter mismatch")
    if snapshot.get("diag_ctxsw") != module["parameters"].get("diag_ctxsw"):
        reasons.append("loaded diag_ctxsw parameter mismatch")
    if snapshot.get("initramfs_path") != initramfs["path"]:
        reasons.append("initramfs path mismatch")
    if snapshot.get("initramfs_sha256") != initramfs["sha256"]:
        reasons.append("initramfs hash mismatch")
    if snapshot.get("embedded_nouveau_sha256") != initramfs["embedded_uncompressed_sha256"]:
        reasons.append("initramfs embedded Nouveau hash mismatch")
    options = manifest.get("module_options")
    if options is not None:
        if snapshot.get("module_options_file_sha256") != options.get("content_sha256"):
            reasons.append("installed early-load Nouveau options file hash mismatch")
        if snapshot.get("embedded_module_options") != options.get("embedded_initramfs_entries"):
            reasons.append("initramfs early-load Nouveau options mismatch")
    if not snapshot.get("journal_visible"):
        reasons.append("current-boot kernel journal visibility not proven")
    if not snapshot.get("journal_current_boot"):
        reasons.append("kernel journal boot identity not proven")
    if snapshot.get("bar2_pte_count") != 0:
        reasons.append(f"BAR2/HOST_CPU/PTE count is {snapshot.get('bar2_pte_count')!r}")
    if snapshot.get("hard_stops"):
        reasons.append("current-boot hard-stop signatures are present")
    if snapshot.get("desktop_errors"):
        reasons.extend(str(item) for item in snapshot["desktop_errors"])
    if snapshot.get("players"):
        reasons.append("media/player process is already running")
    expected_mesa = {
        variant: item["sha256"]
        for variant, item in manifest["mesa"]["variants"].items()
    }
    if snapshot.get("mesa_hashes") != expected_mesa:
        reasons.append("Mesa A/B DSO path/hash set mismatch")
    expected_tools = {
        name: item["sha256"] for name, item in manifest["tools"].items()
    }
    if snapshot.get("tool_hashes") != expected_tools:
        reasons.append("supervisor/correlator hash set mismatch")
    if snapshot.get("input_sha256") != manifest["input"]["sha256"]:
        reasons.append("media input hash mismatch")
    if snapshot.get("build_manifest_sha256") != manifest["build_manifest"]["sha256"]:
        reasons.append("retained Nouveau build-manifest hash mismatch")
    if snapshot.get("deployment_plan_sha256") != manifest["deployment_plan"]["sha256"]:
        reasons.append("deployment plan hash mismatch")
    reference = manifest["software_reference"]
    if snapshot.get("software_reference_manifest_sha256") != reference["manifest_sha256"]:
        reasons.append("software NV12 reference manifest hash mismatch")
    if snapshot.get("software_reference_container_sha256") != reference["container_sha256"]:
        reasons.append("software NV12 reference container hash mismatch")
    return reasons


def evaluate_ambient_capture(
    manifest: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    confirmed_no_reload: bool,
) -> list[str]:
    """Validate read-only evidence capture without admitting any workload."""
    reasons: list[str] = []
    module = manifest["nouveau"]
    initramfs = manifest["initramfs"]
    reasons.extend(gpu_identity_reasons(manifest, snapshot))
    options = manifest.get("module_options")
    if options is None or module.get("parameters", {}).get("diag_bar2_map") != "Y":
        reasons.append("manifest does not pin diag_bar2_map=Y")
    if snapshot.get("kernel_release") != manifest.get("kernel"):
        reasons.append("running kernel mismatch")
    if snapshot.get("loaded_srcversion") != module.get("srcversion"):
        reasons.append("loaded Nouveau srcversion mismatch")
    if snapshot.get("installed_srcversion") != module.get("srcversion"):
        reasons.append("selected installed module srcversion mismatch")
    if snapshot.get("selected_module_path") != module.get("module_path"):
        reasons.append("modinfo-selected module path mismatch")
    if snapshot.get("installed_compressed_sha256") != module.get("installed_compressed_sha256"):
        reasons.append("installed compressed module hash mismatch")
    if snapshot.get("installed_uncompressed_sha256") != module.get("installed_uncompressed_sha256"):
        reasons.append("installed module bytes hash mismatch")
    if snapshot.get("vermagic") != module.get("vermagic"):
        reasons.append("installed module vermagic mismatch")
    for name, expected in module.get("parameters", {}).items():
        if snapshot.get("module_parameters", {}).get(name) != expected:
            reasons.append(f"loaded {name} parameter mismatch")
    if options is not None:
        if snapshot.get("module_options_file_sha256") != options.get("content_sha256"):
            reasons.append("installed early-load Nouveau options file hash mismatch")
        if snapshot.get("embedded_module_options") != options.get("embedded_initramfs_entries"):
            reasons.append("initramfs early-load Nouveau options mismatch")
    if snapshot.get("initramfs_path") != initramfs.get("path"):
        reasons.append("initramfs path mismatch")
    if snapshot.get("initramfs_sha256") != initramfs.get("sha256"):
        reasons.append("initramfs hash mismatch")
    if snapshot.get("embedded_nouveau_sha256") != initramfs.get("embedded_uncompressed_sha256"):
        reasons.append("initramfs embedded Nouveau module hash mismatch")
    if not snapshot.get("journal_visible") or not snapshot.get("journal_current_boot"):
        reasons.append("current-boot kernel journal provenance not proven")
    if snapshot.get("bar2_pte_count", 0) <= 0:
        reasons.append("no BAR2/HOST_CPU/PTE fault is present for archival")
    if not snapshot.get("ambient_fault_correlation_parseable"):
        reasons.append(
            "ambient fault journal is not parseable: "
            + str(snapshot.get("ambient_fault_correlation_detail", "unknown"))
        )
    if not confirmed_no_reload:
        reasons.append("operator has not confirmed that Nouveau was not unloaded or reloaded")
    report = snapshot.get("ambient_fault_correlation_report") or {}
    if report.get("fault_count", 0) <= 0:
        reasons.append("ambient correlation contains no fault records")
    if report.get("module_load_epoch_policy") != "single_nouveau_module_instance_per_boot":
        reasons.append("ambient correlation does not use the single-module-epoch policy")
    if not report.get("diagnostic_sequence_gaps_rejected"):
        reasons.append("ambient diagnostic sequence was not validated")
    return reasons


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--ambient-fault-capture",
        action="store_true",
        help="check provenance for read-only post-fault archival; never admits workloads",
    )
    parser.add_argument(
        "--confirm-no-nouveau-reload",
        action="store_true",
        help="operator assertion that Nouveau was not unloaded or reloaded this boot",
    )
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest(args.manifest.resolve(strict=True))
        verify_execution_identity(manifest)
        snapshot = collect_snapshot(manifest)
        if args.ambient_fault_capture:
            reasons = evaluate_ambient_capture(
                manifest,
                snapshot,
                confirmed_no_reload=args.confirm_no_nouveau_reload,
            )
            print("RUN_ELIGIBLE=false")
            print("AMBIENT_FAULT_CAPTURE_ELIGIBLE=" + ("false" if reasons else "true"))
            print("BAR2_HOST_CPU_PTE_COUNT=" + str(snapshot["bar2_pte_count"]))
            print("NOUVEAU_PCI_FUNCTIONS=" + ",".join(snapshot["bound_nouveau_pci_functions"]))
            print("MODULE_RELOAD_ASSERTION=" + (
                "operator_confirmed" if args.confirm_no_nouveau_reload else "missing"
            ))
            if reasons:
                for reason in reasons:
                    print(f"REASON={reason}")
                return 2
            return 0
        reasons = evaluate_snapshot(manifest, snapshot)
        if reasons:
            print("RUN_ELIGIBLE=false")
            for reason in reasons:
                print(f"REASON={reason}")
            return 2
        print("RUN_ELIGIBLE=true")
        print("BOOT_ID=" + snapshot["boot_id"])
        print("NOUVEAU_PCI_FUNCTIONS=" + ",".join(snapshot["bound_nouveau_pci_functions"]))
        print("BAR2_HOST_CPU_PTE_COUNT=0")
        print("HARD_STOP_COUNT=0")
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        print("RUN_ELIGIBLE=false")
        print(f"REASON=admission check error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
