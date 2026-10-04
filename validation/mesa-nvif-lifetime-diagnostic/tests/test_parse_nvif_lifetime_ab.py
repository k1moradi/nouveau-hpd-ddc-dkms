from __future__ import annotations

import io
import json
import hashlib
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import parse_nvif_lifetime_ab as parser


TEST_MODULE_PATH = Path(__file__).parent / "fixtures" / "synthetic-nouveau-module.bin"
TEST_MODULE_SHA256 = hashlib.sha256(TEST_MODULE_PATH.read_bytes()).hexdigest()
TEST_PROVENANCE_ROOT = Path(tempfile.mkdtemp(prefix="nvif-parser-provenance-"))
TEST_MESA_DSO_PATHS = {
    "A": Path(
        "/home/keivan/nouveau-vaapi-app-validation/"
        "mesa-nvif-lifetime-ab-build-20261004-pointer-free/A/"
        "libgallium_drv_video.so"
    ),
    "B": Path(
        "/home/keivan/nouveau-vaapi-app-validation/"
        "mesa-nvif-lifetime-ab-build-20261004-pointer-free/B/"
        "libgallium_drv_video.so"
    ),
}


def write_test_deployment_manifest(
    *,
    dso_paths: dict[str, Path] | None = None,
    module_path: Path = TEST_MODULE_PATH,
) -> tuple[Path, str, dict[str, str]]:
    """Create a complete, isolated deployment record for parser tests."""
    dso_paths = dso_paths or TEST_MESA_DSO_PATHS
    root = Path(tempfile.mkdtemp(prefix="case-", dir=TEST_PROVENANCE_ROOT))
    build_path = root / "build-manifest.json"
    plan_path = root / "deployment-plan.json"
    build_path.write_text(
        json.dumps({
            "status": "CLEAN_ENABLED_BUILD_RETAINED_NOT_INSTALLED",
            "kernel_release": parser.EXPECTED_KERNEL,
            "review_commit": "c97442c053137b0ad48f507c9e72770e08756489",
            "review_branch": "review/gk104-vaapi-selftest-v8-20261002",
            "main_commit": "7b44b1c1e282eac7c54c1cfa8c758118cd66312c",
            "kernel_source_archive_sha256": parser.EXPECTED_KERNEL_SOURCE_ARCHIVE_SHA256,
            "patch_sha256": parser.EXPECTED_KERNEL_PATCH_SHA256,
            "srcversion": parser.EXPECTED_NOUVEAU_SRCVERSION,
            "vermagic": "7.0.0-34-generic SMP preempt mod_unload modversions",
            "raw_module_sha256": hashlib.sha256(module_path.read_bytes()).hexdigest(),
        }, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    capture_path = Path(__file__).resolve().parents[1] / "capture_nvif_lifetime_run.py"
    parser_path = Path(parser.__file__).resolve()
    tool_hashes = {
        "nvif_capture_sha256": hashlib.sha256(capture_path.read_bytes()).hexdigest(),
        "nvif_parser_sha256": hashlib.sha256(parser_path.read_bytes()).hexdigest(),
    }
    mesa_variants = {
        variant: {
            "path": str(path.resolve()),
            "sha256": parser.EXPECTED_DSO_SHA256[variant],
        }
        for variant, path in dso_paths.items()
    }
    tools = {
        "nvif_capture": {
            "path": str(capture_path),
            "sha256": tool_hashes["nvif_capture_sha256"],
        },
        "nvif_parser": {
            "path": str(parser_path),
            "sha256": tool_hashes["nvif_parser_sha256"],
        },
    }
    input_record = {
        "path": str(Path(parser.load_runtime_profile()["argv"][-1]).resolve()),
        "sha256": parser.EXPECTED_INPUT_SHA256,
    }
    plan_path.write_text(
        json.dumps({
            "schema": 1,
            "expected_review_branch": "review/gk104-vaapi-selftest-v8-20261002",
            "expected_main_commit": "7b44b1c1e282eac7c54c1cfa8c758118cd66312c",
            "kernel_release": parser.EXPECTED_KERNEL,
            "kernel_source_archive_sha256": parser.EXPECTED_KERNEL_SOURCE_ARCHIVE_SHA256,
            "patch_sha256": parser.EXPECTED_KERNEL_PATCH_SHA256,
            "expected_srcversion": parser.EXPECTED_NOUVEAU_SRCVERSION,
            "expected_vermagic": "7.0.0-34-generic SMP preempt mod_unload modversions",
            "module_install_path": str(module_path.resolve()),
            "module_parameters": {"diag_ctxsw": "N"},
            "mesa_variants": mesa_variants,
            "tools": tools,
            "input": input_record,
        }, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    deployment = {
        "schema": 2,
        "status": "FINALIZED_NOT_REBOOTED_NOT_RUNTIME_VERIFIED",
        "review_branch": "review/gk104-vaapi-selftest-v8-20261002",
        "review_commit": "c97442c053137b0ad48f507c9e72770e08756489",
        "main_commit": "7b44b1c1e282eac7c54c1cfa8c758118cd66312c",
        "kernel": parser.EXPECTED_KERNEL,
        "source": {
            "kernel_source_archive_sha256": parser.EXPECTED_KERNEL_SOURCE_ARCHIVE_SHA256,
            "patch_sha256": parser.EXPECTED_KERNEL_PATCH_SHA256,
        },
        "build_manifest": {
            "path": str(build_path.resolve()),
            "sha256": hashlib.sha256(build_path.read_bytes()).hexdigest(),
        },
        "deployment_plan": {
            "path": str(plan_path.resolve()),
            "sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        },
        "nouveau": {
            "module_path": str(module_path.resolve()),
            "installed_compressed_sha256": hashlib.sha256(
                module_path.read_bytes()
            ).hexdigest(),
            "raw_module_sha256": hashlib.sha256(
                module_path.read_bytes()
            ).hexdigest(),
            "srcversion": parser.EXPECTED_NOUVEAU_SRCVERSION,
            "vermagic": "7.0.0-34-generic SMP preempt mod_unload modversions",
            "parameters": {"diag_ctxsw": "N"},
        },
        "initramfs": {
            "path": "/boot/initrd.img-7.0.0-34-generic",
            "sha256": "f" * 64,
        },
        "mesa": {
            "variants": mesa_variants
        },
        "tools": tools,
        "input": input_record,
    }
    deployment_path = root / "deployment-manifest.json"
    payload = json.dumps(deployment, indent=2, sort_keys=True).encode() + b"\n"
    deployment_path.write_bytes(payload)
    return (
        deployment_path.resolve(),
        hashlib.sha256(payload).hexdigest(),
        tool_hashes,
    )


DEFAULT_TEST_DEPLOYMENT_PATH, DEFAULT_TEST_DEPLOYMENT_SHA, DEFAULT_TEST_TOOL_HASHES = (
    write_test_deployment_manifest()
)


def bind_test_deployment(
    item: dict[str, object],
    *,
    dso_paths: dict[str, Path] | None = None,
    module_path: Path = TEST_MODULE_PATH,
) -> None:
    deployment_path, deployment_sha, tool_hashes = write_test_deployment_manifest(
        dso_paths=dso_paths,
        module_path=module_path,
    )
    item.update({
        "deployment_manifest_path": str(deployment_path),
        "deployment_manifest_sha256": deployment_sha,
        **tool_hashes,
    })


def journal_line(
    message: str,
    stamp: int,
    *,
    pid: int | None = 222,
    boot_id: str = "boot-a",
    syslog_identifier: str | None = None,
) -> str:
    record: dict[str, object] = {
        "MESSAGE": message,
        "__MONOTONIC_TIMESTAMP": str(stamp),
        "_BOOT_ID": boot_id,
    }
    if pid is not None:
        record["_PID"] = str(pid)
        record["TID"] = str(pid + 1)
    else:
        record["_TRANSPORT"] = "kernel"
    if syslog_identifier is not None:
        record["SYSLOG_IDENTIFIER"] = syslog_identifier
    return json.dumps(record)


def new_line(
    *,
    ret: int,
    route: int,
    key: int = 0x500,
    selected_fd: int = 9,
    drm_fd: int = 9,
) -> str:
    return (
        f"NOUVEAU_DIAG_NVIF_NEW key=0x{key:x} "
        f"parent_handle=0x{route:x} route_token=0x{route:x} "
        f"selected_fd={selected_fd} drm_fd={drm_fd} class=0x000095b1 "
        f"ret={ret}"
    )


def del_line(
    *,
    selected_fd: int,
    ret: int,
    key: int = 0x500,
    parent_handle: int = 0x2,
    drm_fd: int = 9,
) -> str:
    return (
        f"NOUVEAU_DIAG_NVIF_DEL key=0x{key:x} "
        f"parent_handle=0x{parent_handle:x} object_handle=0x501 "
        f"selected_fd={selected_fd} drm_fd={drm_fd} class=0x000095b1 ret={ret}"
    )


def channel_free_line(ret: int = 0) -> str:
    return f"NOUVEAU_DIAG_NVIF_CHANNEL_FREE channel=0x2 fd=9 ret={ret}"


def duplicate_line(*, layer: str = "abi16", channel: int = 0x3,
                   key: int = 0x500) -> str:
    message = (
        f"NOUVEAU_DIAG_NVIF_DUP layer={layer} key=0x{key:x} "
        "class=0x000095b1"
    )
    if layer == "abi16":
        message += f" channel=0x{channel:x}"
    return message


def baseline_records(*, duplicate_channel: int = 0x3,
                     duplicate_layer: str = "abi16",
                     duplicate_key: int = 0x500,
                     delete_key: int = 0x500,
                     replacement_key: int = 0x500,
                     delete_ret: int = -9,
                     delete_drm_fd: int = 9,
                     include_free: bool = True) -> str:
    records = [
        journal_line(new_line(ret=0, route=0x2), 100),
        journal_line(
            del_line(
                selected_fd=2,
                ret=delete_ret,
                key=delete_key,
                drm_fd=delete_drm_fd,
            ), 200
        ),
    ]
    if include_free:
        records.append(journal_line(channel_free_line(), 300))
    records.extend([
        journal_line(
            "nouveau 0000:01:00.0: drm: "
            + duplicate_line(
                layer=duplicate_layer,
                channel=duplicate_channel,
                key=duplicate_key,
            ),
            390,
            pid=None,
        ),
        journal_line(
            new_line(ret=-17, route=0x3, key=replacement_key), 400
        ),
    ])
    return "\n".join(records) + "\n"


def candidate_records(*, delete_fd: int = 9, delete_ret: int = 0,
                      second_ret: int = 0) -> str:
    return "\n".join([
        journal_line(new_line(ret=0, route=0x2), 100, boot_id="boot-b"),
        journal_line(
            del_line(selected_fd=delete_fd, ret=delete_ret),
            200,
            boot_id="boot-b",
        ),
        journal_line(channel_free_line(), 300, boot_id="boot-b"),
        journal_line(
            new_line(ret=second_ret, route=0x3),
            400,
            boot_id="boot-b",
        ),
    ]) + "\n"


def manifest(variant: str, *, boot_id: str | None = None) -> dict[str, object]:
    profile = parser.load_runtime_profile()
    environment = {
        "DISPLAY": ":0",
        "XAUTHORITY": "<session-xauthority>",
        "LIBVA_DRIVER_NAME": "nouveau",
        "LIBVA_DRIVERS_PATH": "<variant-private-directory>",
        "HOME": "/home/keivan",
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
    return {
        "schema": 2,
        "variant": variant,
        "boot_id": boot_id or f"boot-{variant.lower()}",
        "input_sha256": parser.EXPECTED_INPUT_SHA256,
        "command_argv": profile["argv"],
        "launcher_argv": [
            "/usr/bin/systemd-cat", "--identifier=nouveau-nvif-lifetime", "--",
            *profile["argv"],
        ],
        "normalized_environment": environment,
        "kernel": "7.0.0-34-generic",
        "runtime_profile_sha256": parser.EXPECTED_RUNTIME_PROFILE_SHA256,
        "nouveau_srcversion": parser.EXPECTED_NOUVEAU_SRCVERSION,
        "nouveau_module_path": str(TEST_MODULE_PATH.resolve()),
        "nouveau_module_file_sha256": TEST_MODULE_SHA256,
        "nouveau_module_srcversion": parser.EXPECTED_NOUVEAU_SRCVERSION,
        "nouveau_module_vermagic": (
            "7.0.0-34-generic SMP preempt mod_unload modversions"
        ),
        "deployment_manifest_path": str(DEFAULT_TEST_DEPLOYMENT_PATH),
        "deployment_manifest_sha256": DEFAULT_TEST_DEPLOYMENT_SHA,
        **DEFAULT_TEST_TOOL_HASHES,
        "nouveau_parameters": {"diag_ctxsw": "N"},
        "dso_sha256": parser.EXPECTED_DSO_SHA256[variant],
        "dso_resolved_path": str(TEST_MESA_DSO_PATHS[variant].resolve()),
        "dso_alias_path": f"/tmp/{variant}/driver/nouveau_drv_video.so",
        "mpv_sha256": "d" * 64,
        "mpv_resolved_path": "/usr/bin/mpv",
        "systemd_cat_sha256": "e" * 64,
        "systemd_cat_resolved_path": "/usr/bin/systemd-cat",
        "preflight_journal_path": "journal-preflight.jsonl",
        "working_directory": "/home/keivan",
        "xauthority_sha256": "c" * 64,
        "termination_reason": "process-exit",
        "journal_start_cursor": f"cursor-{variant}",
        "journal_boundary_proven": True,
        "preflight_journal_sha256": "a" * 64,
        "preflight_hard_stops": [],
        "journal_delta_sha256": "b" * 64,
        "postrun_hard_stops": [],
        "workload_returncode": 0,
        "workload_timed_out": False,
        "journal_monitor_error": None,
    }


def proven_a_manifest() -> dict[str, object]:
    result = manifest("A")
    result["termination_reason"] = "bsp-eexist"
    result["workload_returncode"] = -2
    return result


class NvifLifetimeParserTests(unittest.TestCase):
    def parse(self, text: str) -> list[parser.Event]:
        return parser.read_events(io.StringIO(text))

    def test_baseline_requires_complete_wrong_fd_duplicate_chain(self) -> None:
        text = baseline_records(duplicate_layer="abi16")
        result = parser.analyze(self.parse(text), "A")
        self.assertEqual(result["result"], "BASELINE_REPRODUCED_EEXIST_CHAIN")
        self.assertEqual(result["matching_lifecycles"][0]["duplicate_layer"], "abi16")
        self.assertEqual(result["matching_lifecycles"][0]["delete_ret"], -9)

    def test_a_chain_proof_uses_numeric_key_and_channel_handles_only(self) -> None:
        events = self.parse(baseline_records())
        result = parser.classify_a_run(events, proven_a_manifest())
        self.assertEqual(result["outcome"], "A_CHAIN_PROVEN")
        self.assertEqual(
            result["evidence"]["matching_lifecycles"][0]["key"],
            0x500,
        )
        new_event = next(event for event in events if event.kind == "new")
        self.assertNotIn("obj", new_event.fields)
        self.assertNotIn("parent", new_event.fields)

    def test_a_clean_natural_run_without_chain_is_not_reproduced(self) -> None:
        result = parser.classify_a_run([], manifest("A"))
        self.assertEqual(result["outcome"], "A_CHAIN_NOT_REPRODUCED")

    def test_hard_stop_before_replacement_makes_a_chain_inconclusive(self) -> None:
        lines = baseline_records().splitlines()
        lines.insert(
            3,
            journal_line(
                "nouveau fifo: fault engine 05 [BAR2] "
                "client 07 [HUB/HOST_CPU] reason 02 [PTE]",
                350,
                pid=None,
            ),
        )
        capture = "\n".join(lines) + "\n"
        result = parser.classify_a_run(
            self.parse(capture),
            proven_a_manifest(),
            parser.hard_stop_records(capture.encode()),
        )
        self.assertEqual(result["outcome"], "A_INCONCLUSIVE")

    def test_wrong_delete_key_cannot_match_lifecycle(self) -> None:
        result = parser.analyze(
            self.parse(baseline_records(delete_key=0x501)),
            "A",
        )

    def test_delete_canonical_fd_must_match_both_new_records(self) -> None:
        result = parser.analyze(
            self.parse(baseline_records(delete_drm_fd=10)),
            "A",
        )
        self.assertEqual(
            result["result"],
            "INCONCLUSIVE_BASELINE_NOT_REPRODUCED",
        )
        self.assertEqual(
            result["result"],
            "INCONCLUSIVE_BASELINE_NOT_REPRODUCED",
        )

    def test_replacement_with_different_key_cannot_match_lifecycle(self) -> None:
        result = parser.analyze(
            self.parse(baseline_records(replacement_key=0x501)),
            "A",
        )
        self.assertEqual(
            result["result"],
            "INCONCLUSIVE_BASELINE_NOT_REPRODUCED",
        )

    def test_del_result_is_required(self) -> None:
        text = baseline_records().replace(" ret=-9", "", 1)
        with self.assertRaisesRegex(ValueError, "DEL record missing fields: ret"):
            self.parse(text)

    def test_pointer_fields_are_rejected_as_unpinned_identity(self) -> None:
        text = baseline_records().replace(
            "parent_handle=0x2",
            "parent_handle=0x2 obj=0x500",
            1,
        )
        with self.assertRaisesRegex(ValueError, "unexpected fields: obj"):
            self.parse(text)

    def test_b_correction_requires_prior_a_proof(self) -> None:
        result = parser.classify_b_run(
            self.parse(candidate_records()),
            manifest("B"),
            a_chain_proven=False,
        )
        self.assertEqual(result["outcome"], "A_INCONCLUSIVE")

    def test_b_correction_exact_chain_and_failed_correction(self) -> None:
        clean = parser.classify_b_run(
            self.parse(candidate_records()),
            manifest("B"),
            a_chain_proven=True,
        )
        failed = parser.classify_b_run(
            self.parse(candidate_records(delete_fd=2, delete_ret=-9)),
            manifest("B"),
            a_chain_proven=True,
        )
        self.assertEqual(clean["outcome"], "B_CORRECTION_PROVEN")
        self.assertEqual(failed["outcome"], "B_CORRECTION_FAILED")

    def test_b_correction_requires_no_abi16_duplicate_anywhere_in_run(self) -> None:
        text = candidate_records() + journal_line(
            "nouveau 0000:01:00.0: drm: "
            + duplicate_line(layer="abi16", channel=0x99, key=0x999),
            500,
            pid=None,
            boot_id="boot-b",
        ) + "\n"
        result = parser.classify_b_run(
            self.parse(text), manifest("B"), a_chain_proven=True
        )
        self.assertEqual(result["outcome"], "B_CORRECTION_FAILED")
        self.assertEqual(len(result["abi16_duplicate_records"]), 1)

    def test_baseline_accepts_zero_return_wrong_fd_del_only_with_stale_key_proof(
        self,
    ) -> None:
        result = parser.analyze(
            self.parse(baseline_records(delete_ret=0)),
            "A",
        )
        self.assertEqual(
            result["result"],
            "BASELINE_REPRODUCED_EEXIST_AFTER_ZERO_RETURN_DEL",
        )
        self.assertEqual(result["matching_lifecycles"][0]["delete_ret"], 0)

    def test_zero_return_wrong_fd_del_without_abi16_duplicate_is_inconclusive(
        self,
    ) -> None:
        result = parser.analyze(
            self.parse(baseline_records(delete_ret=0, duplicate_layer="nvkm")),
            "A",
        )
        self.assertEqual(
            result["result"],
            "INCONCLUSIVE_NVKM_DUPLICATE_IDENTITY_WEAK",
        )

    def test_zero_return_wrong_fd_del_requires_same_duplicate_key(self) -> None:
        result = parser.analyze(
            self.parse(baseline_records(delete_ret=0, duplicate_key=0x501)),
            "A",
        )
        self.assertEqual(result["result"], "INCONCLUSIVE_BASELINE_NOT_REPRODUCED")

    def test_nvkm_duplicate_without_process_identity_is_inconclusive(self) -> None:
        result = parser.analyze(
            self.parse(baseline_records(duplicate_layer="nvkm")),
            "A",
        )
        self.assertEqual(
            result["result"],
            "INCONCLUSIVE_NVKM_DUPLICATE_IDENTITY_WEAK",
        )
        self.assertEqual(
            result["weak_nvkm_lifecycles"][0]["duplicate_layer"],
            "nvkm",
        )

    def test_baseline_without_reused_key_is_inconclusive(self) -> None:
        text = journal_line(new_line(ret=0, route=0x2), 100) + "\n"
        result = parser.analyze(self.parse(text), "A")
        self.assertEqual(result["result"], "INCONCLUSIVE_BASELINE_NOT_REPRODUCED")

    def test_baseline_rejects_duplicate_for_another_route(self) -> None:
        result = parser.analyze(
            self.parse(baseline_records(duplicate_channel=0x999)), "A"
        )
        self.assertEqual(result["result"], "INCONCLUSIVE_BASELINE_NOT_REPRODUCED")

    def test_abi16_duplicate_requires_channel_token(self) -> None:
        text = baseline_records().replace(" channel=0x3", "")
        with self.assertRaisesRegex(ValueError, "ABI16 duplicate record"):
            self.parse(text)

    def test_baseline_requires_successful_old_channel_free(self) -> None:
        result = parser.analyze(
            self.parse(baseline_records(include_free=False)), "A"
        )
        self.assertEqual(result["result"], "INCONCLUSIVE_BASELINE_NOT_REPRODUCED")

    def test_candidate_requires_correct_fd_success_and_reused_key(self) -> None:
        result = parser.analyze(self.parse(candidate_records()), "B")
        self.assertEqual(result["result"], "CANDIDATE_LIFECYCLE_SUCCEEDED")

    def test_candidate_rejects_failed_delete_or_replacement(self) -> None:
        for text in (
            candidate_records(delete_fd=2, delete_ret=-9),
            candidate_records(second_ret=-17),
        ):
            result = parser.analyze(self.parse(text), "B")
            self.assertEqual(result["result"], "INCONCLUSIVE_CANDIDATE_SHAPE_MISMATCH")

    def test_truncated_tagged_record_fails_closed(self) -> None:
        line = journal_line("NOUVEAU_DIAG_NVIF_DEL key=0x500 ret=-9", 100)
        with self.assertRaisesRegex(ValueError, "missing fields"):
            self.parse(line + "\n")

    def test_tagged_record_requires_monotonic_timestamp(self) -> None:
        record = {
            "MESSAGE": new_line(ret=0, route=0x2),
            "_PID": "222",
            "_BOOT_ID": "boot-a",
        }
        with self.assertRaisesRegex(ValueError, "__MONOTONIC_TIMESTAMP"):
            self.parse(json.dumps(record) + "\n")

    def test_mixed_boot_capture_fails_closed(self) -> None:
        first = json.loads(journal_line(new_line(ret=0, route=0x2), 100))
        second = json.loads(journal_line(new_line(ret=0, route=0x2), 200))
        second["_BOOT_ID"] = "boot-b"
        with self.assertRaisesRegex(ValueError, "multiple boots"):
            self.parse(json.dumps(first) + "\n" + json.dumps(second) + "\n")

    def test_invalid_json_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid journal JSON"):
            self.parse("not-json\n")

    def test_pair_comparison_requires_baseline_reproduction(self) -> None:
        result = parser.compare_runs(
            self.parse(baseline_records(include_free=False)),
            self.parse(candidate_records()),
            manifest("A"),
            manifest("B"),
        )
        self.assertEqual(result["outcome"], "A_CHAIN_NOT_REPRODUCED")

    def test_pair_comparison_accepts_only_matching_a_and_b_sequences(self) -> None:
        baseline = proven_a_manifest()
        result = parser.compare_runs(
            self.parse(baseline_records()),
            self.parse(candidate_records()),
            baseline,
            manifest("B"),
        )
        self.assertEqual(result["outcome"], "B_CORRECTION_PROVEN")

    def test_pair_comparison_accepts_zero_return_del_with_abi16_stale_key(self) -> None:
        baseline = proven_a_manifest()
        result = parser.compare_runs(
            self.parse(baseline_records(delete_ret=0)),
            self.parse(candidate_records()),
            baseline,
            manifest("B"),
        )
        self.assertEqual(result["outcome"], "B_CORRECTION_PROVEN")
        self.assertEqual(
            result["variant_a"]["evidence"]["result"],
            "BASELINE_REPRODUCED_EEXIST_AFTER_ZERO_RETURN_DEL",
        )

    def test_nonzero_baseline_exit_is_allowed_only_for_eexist_early_stop(self) -> None:
        baseline = manifest("A")
        baseline["workload_returncode"] = -2
        baseline["termination_reason"] = "process-exit"
        result = parser.compare_runs(
            self.parse(baseline_records()),
            self.parse(candidate_records()),
            baseline,
            manifest("B"),
        )
        self.assertEqual(result["outcome"], "A_INCONCLUSIVE")

    def test_pair_comparison_rejects_workload_mismatch(self) -> None:
        candidate = manifest("B")
        candidate["command_argv"] = ["mpv", "--hwdec=no", "/home/keivan/test_1080p.mkv"]
        with self.assertRaisesRegex(ValueError, "command_argv"):
            parser.compare_runs(
                self.parse(baseline_records()),
                self.parse(candidate_records()),
                manifest("A"),
                candidate,
            )

    def test_pair_comparison_requires_same_launcher_binaries(self) -> None:
        candidate = manifest("B")
        candidate["mpv_sha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "mpv_sha256"):
            parser.compare_runs(
                self.parse(baseline_records()),
                self.parse(candidate_records()),
                manifest("A"),
                candidate,
            )

    def test_kernel_hard_stop_overrides_lifecycle_success(self) -> None:
        result = parser.compare_runs(
            self.parse(baseline_records()),
            self.parse(candidate_records()),
            proven_a_manifest(),
            manifest("B"),
            hard_stops_b=[{
                "source": "kernel",
                "kinds": ["CTXSW_TIMEOUT"],
                "message": "nouveau fifo: SCHED_ERROR 0a [CTXSW_TIMEOUT]",
            }],
        )
        self.assertEqual(result["outcome"], "A_INCONCLUSIVE")

    def test_workload_fatal_is_scoped_and_overrides_lifecycle_success(self) -> None:
        result = parser.compare_runs(
            self.parse(baseline_records()),
            self.parse(candidate_records()),
            proven_a_manifest(),
            manifest("B"),
            hard_stops_b=[{
                "source": "workload",
                "kinds": ["SIGBUS"],
                "message": "Bus error",
            }],
        )
        self.assertEqual(result["outcome"], "A_INCONCLUSIVE")

    def test_unproven_journal_boundary_overrides_lifecycle_success(self) -> None:
        candidate = manifest("B")
        candidate["journal_boundary_proven"] = False
        result = parser.compare_runs(
            self.parse(baseline_records()),
            self.parse(candidate_records()),
            proven_a_manifest(),
            candidate,
        )
        self.assertEqual(result["outcome"], "A_INCONCLUSIVE")

    def test_nonzero_workload_overrides_lifecycle_success(self) -> None:
        candidate = manifest("B")
        candidate["workload_returncode"] = 1
        result = parser.compare_runs(
            self.parse(baseline_records()),
            self.parse(candidate_records()),
            proven_a_manifest(),
            candidate,
        )
        self.assertEqual(result["outcome"], "A_INCONCLUSIVE")

    def test_hard_stop_classifier_catches_delayed_bar2_and_ctxsw(self) -> None:
        self.assertEqual(
            parser.journal_record_hard_stop_kinds({
                "MESSAGE": "fault engine 05 [BAR2] client 07 [HOST_CPU] reason [PTE]",
                "_TRANSPORT": "kernel",
            }),
            ("BAR2", "HOST_CPU", "PTE"),
        )
        self.assertIn(
            "CTXSW_TIMEOUT",
            parser.journal_record_hard_stop_kinds({
                "MESSAGE": "fifo: SCHED_ERROR 0a [CTXSW_TIMEOUT]",
                "_TRANSPORT": "kernel",
            }),
        )

    def test_hard_stop_classifier_catches_privilege_bus_and_gpu_reset(self) -> None:
        cases = (
            ("fifo: PRIV_VIOLATION on channel 4", "PRIV_VIOLATION"),
            ("mpv terminated by SIGBUS", "SIGBUS"),
            ("nouveau: GPU reset after engine failure", "GPU-reset"),
        )
        for message, expected in cases:
            with self.subTest(message=message):
                if expected == "SIGBUS":
                    record = {
                        "MESSAGE": message,
                        "SYSLOG_IDENTIFIER": "nouveau-nvif-lifetime",
                        "_TRANSPORT": "stdout",
                    }
                else:
                    record = {"MESSAGE": message, "_TRANSPORT": "kernel"}
                self.assertIn(expected, parser.journal_record_hard_stop_kinds(record))

    def test_unrelated_userspace_messages_do_not_trip_kernel_classifier(self) -> None:
        records = "\n".join(
            json.dumps({
                "MESSAGE": message,
                "_TRANSPORT": "journal",
                "SYSLOG_IDENTIFIER": "unrelated-process",
            })
            for message in (
                "WARNING: this text mentions PTE",
                "BAR2 HOST_CPU PTE",
                "process received SIGBUS",
            )
        ) + "\n"
        self.assertEqual(parser.hard_stop_records(records.encode()), [])

    def test_kernel_warning_is_classified_but_workload_bus_error_is_scoped(self) -> None:
        records = "\n".join(
            json.dumps(record)
            for record in (
                {
                    "MESSAGE": "WARNING: Nouveau reports a kernel warning",
                    "_TRANSPORT": "kernel",
                },
                {
                    "MESSAGE": "Bus error",
                    "_TRANSPORT": "stdout",
                    "SYSLOG_IDENTIFIER": "nouveau-nvif-lifetime",
                },
                {
                    "MESSAGE": "Bus error",
                    "_TRANSPORT": "stdout",
                    "SYSLOG_IDENTIFIER": "unrelated-process",
                },
            )
        ) + "\n"
        stops = parser.hard_stop_records(records.encode())
        self.assertEqual(
            stops,
            [
                {
                    "source": "kernel",
                    "kinds": ["kernel-WARNING"],
                    "message": "WARNING: Nouveau reports a kernel warning",
                },
                {
                    "source": "workload",
                    "kinds": ["SIGBUS"],
                    "message": "Bus error",
                },
            ],
        )

    def test_pair_comparison_rejects_same_boot(self) -> None:
        with self.assertRaisesRegex(ValueError, "separate boots"):
            parser.compare_runs(
                self.parse(baseline_records()),
                self.parse(candidate_records()),
                manifest("A", boot_id="same-boot"),
                manifest("B", boot_id="same-boot"),
            )

    def test_manifest_loader_pins_variant_dso_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            bad = manifest("A")
            bad["dso_sha256"] = "0" * 64
            path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "DSO SHA-256"):
                parser.read_manifest(path, "A")

    def test_deployment_provenance_binds_module_dso_and_current_nvif_tools(self) -> None:
        item = manifest("A")
        parser._validate_deployment_provenance(item, "A")

        bad_tool = dict(item)
        bad_tool["nvif_parser_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "parser provenance mismatch"):
            parser._validate_deployment_provenance(bad_tool, "A")

        bad_module = dict(item)
        bad_module["nouveau_module_file_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "installed_compressed_sha256 mismatch"):
            parser._validate_deployment_provenance(bad_module, "A")

    def test_deployment_manifest_file_hash_is_rechecked(self) -> None:
        item = manifest("A")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "deployment-manifest.json"
            path.write_bytes(
                Path(item["deployment_manifest_path"]).read_bytes() + b" "
            )
            item["deployment_manifest_path"] = str(path)
            with self.assertRaisesRegex(ValueError, "deployment manifest SHA-256 mismatch"):
                parser._validate_deployment_provenance(item, "A")

    def test_manifest_loader_accepts_workload_hard_stop_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            item = manifest("A", boot_id="boot-a")
            dso = root / "libgallium_drv_video.so"
            dso.write_bytes(b"synthetic DSO")
            alias = root / "nouveau_drv_video.so"
            alias.symlink_to(dso)
            module = root / "nouveau.ko.zst"
            module.write_bytes(b"synthetic installed module")
            preflight = root / "journal-preflight.jsonl"
            preflight.write_text(
                json.dumps({
                    "MESSAGE": "nouveau: initialized",
                    "_BOOT_ID": "boot-a",
                    "_TRANSPORT": "kernel",
                }) + "\n",
                encoding="utf-8",
            )
            item.update({
                "dso_resolved_path": str(dso),
                "dso_alias_path": str(alias),
                "nouveau_module_path": str(TEST_MODULE_PATH.resolve()),
                "nouveau_module_file_sha256": TEST_MODULE_SHA256,
                "preflight_journal_sha256": hashlib.sha256(
                    preflight.read_bytes()
                ).hexdigest(),
                "termination_reason": "workload-hard-stop",
                "postrun_hard_stops": [{
                    "source": "workload",
                    "kinds": ["SIGBUS"],
                    "message": "Bus error",
                }],
                "workload_returncode": -7,
            })
            bind_test_deployment(item, dso_paths={"A": dso, "B": TEST_MESA_DSO_PATHS["B"]})
            path = root / "manifest.json"
            path.write_text(json.dumps(item), encoding="utf-8")
            media = Path(parser.load_runtime_profile()["argv"][-1])
            original_hash = parser._sha256_file

            def fake_hash(candidate: Path) -> str:
                if candidate == media:
                    return parser.EXPECTED_INPUT_SHA256
                if candidate == dso:
                    return parser.EXPECTED_DSO_SHA256["A"]
                return original_hash(candidate)

            with mock.patch.object(parser, "_sha256_file", side_effect=fake_hash):
                loaded = parser.read_manifest(path, "A")

                item["termination_reason"] = "kernel-hard-stop"
                path.write_text(json.dumps(item), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "lacks a kernel"):
                    parser.read_manifest(path, "A")

                item["termination_reason"] = "workload-hard-stop"
                item["postrun_hard_stops"] = []
                path.write_text(json.dumps(item), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "lacks a workload"):
                    parser.read_manifest(path, "A")

        self.assertEqual(loaded["termination_reason"], "workload-hard-stop")
        self.assertEqual(loaded["postrun_hard_stops"][0]["source"], "workload")

    def test_workload_termination_allows_later_kernel_tail_record(self) -> None:
        parser._validate_termination_reason_sources(
            "workload-hard-stop",
            [
                {"source": "workload", "kinds": ["SIGBUS"]},
                {"source": "kernel", "kinds": ["PTE"]},
            ],
            variant="A",
        )

    def test_termination_source_validator_rejects_malformed_records(self) -> None:
        for stops in ([None], [{"source": []}], [{"source": "other"}]):
            with self.subTest(stops=stops), self.assertRaises(ValueError):
                parser._validate_termination_reason_sources(
                    "workload-hard-stop",
                    stops,
                    variant="A",
                )

    def test_runtime_profile_is_pinned_and_has_stage4_command(self) -> None:
        profile = parser.load_runtime_profile()
        self.assertEqual(
            profile["argv"],
            [
                "/usr/bin/mpv", "--no-config", "--vo=gpu", "--gpu-api=opengl",
                "--hwdec=vaapi", "--hwdec-codecs=h264", "--hwdec-threads=1",
                "--hwdec-software-fallback=no", "--no-audio", "--frames=1",
                "/home/keivan/test_1080p.mkv",
            ],
        )
        self.assertIn(
            "not recorded",
            profile["historical_capture"]["failure_timestamp"],
        )
        self.assertEqual(
            profile["historical_capture"]["command_argv"][-2:],
            ["--frames=1", "/home/keivan/test_1080p.mkv"],
        )

    def test_manifest_loader_requires_exact_environment_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            bad = manifest("A")
            bad["normalized_environment"] = {"X": "Y"}
            path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "environment keys"):
                parser.read_manifest(path, "A")

    def test_manifest_loader_rechecks_full_boot_preflight_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "A"
            root.mkdir()
            item = manifest("A", boot_id="boot-a")
            dso = root / "libgallium_drv_video.so"
            dso.write_bytes(b"synthetic DSO")
            alias = root / "nouveau_drv_video.so"
            alias.symlink_to(dso)
            preflight = root / "journal-preflight.jsonl"
            preflight.write_text(
                json.dumps({
                    "MESSAGE": "nouveau fault [BAR2] [HOST_CPU] [PTE]",
                    "_BOOT_ID": "boot-a",
                    "_TRANSPORT": "kernel",
                }) + "\n",
                encoding="utf-8",
            )
            item["dso_resolved_path"] = str(dso)
            item["dso_alias_path"] = str(alias)
            module = root / "nouveau.ko.zst"
            module.write_bytes(b"synthetic installed compressed module")
            item["nouveau_module_path"] = str(module)
            item["nouveau_module_file_sha256"] = hashlib.sha256(
                module.read_bytes()
            ).hexdigest()
            item["preflight_journal_sha256"] = hashlib.sha256(
                preflight.read_bytes()
            ).hexdigest()
            path = root / "manifest.json"
            path.write_text(json.dumps(item), encoding="utf-8")
            media = Path(parser.load_runtime_profile()["argv"][-1])

            def fake_hash(candidate: Path) -> str:
                if candidate == media:
                    return parser.EXPECTED_INPUT_SHA256
                if candidate == dso:
                    return parser.EXPECTED_DSO_SHA256["A"]
                return hashlib.sha256(candidate.read_bytes()).hexdigest()

            with mock.patch.object(parser, "_sha256_file", side_effect=fake_hash):
                with self.assertRaisesRegex(ValueError, "preflight hard-stop records mismatch"):
                    parser.read_manifest(path, "A")

    def test_manifest_loader_pins_kernel_module_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            bad = manifest("A")
            bad["nouveau_srcversion"] = "reviewed-diagnostic-srcversion"
            path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "srcversion"):
                parser.read_manifest(path, "A")

    def test_manifest_loader_requires_selected_module_to_match_loaded_srcversion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            bad = manifest("A")
            bad["nouveau_module_srcversion"] = "different-installed-module"
            path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not match the loaded module"):
                parser.read_manifest(path, "A")

    def test_manifest_loader_rejects_changed_installed_module_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            module = root / "nouveau.ko.zst"
            module.write_bytes(b"installed module A")
            bad = manifest("A")
            bad["nouveau_module_path"] = str(module)
            bad["nouveau_module_file_sha256"] = "0" * 64
            path = root / "manifest.json"
            path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "module file hash changed"):
                parser.read_manifest(path, "A")

    def test_manifest_loader_rejects_missing_installed_module_file(self) -> None:
        bad = manifest("A")
        bad["nouveau_module_path"] = "/no/such/nouveau.ko.zst"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "module file is unavailable"):
                parser.read_manifest(path, "A")

    def test_manifest_loader_rejects_module_symlink_alias(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            module = root / "nouveau.ko.zst"
            module.write_bytes(b"synthetic installed module")
            alias = root / "nouveau-selected.ko.zst"
            alias.symlink_to(module)
            bad = manifest("A")
            bad["nouveau_module_path"] = str(alias)
            bad["nouveau_module_file_sha256"] = hashlib.sha256(
                module.read_bytes()
            ).hexdigest()
            path = root / "manifest.json"
            path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "path is not canonical"):
                parser.read_manifest(path, "A")

    def test_pair_comparison_rejects_different_installed_module_files(self) -> None:
        candidate = manifest("B")
        candidate["nouveau_module_file_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "nouveau_module_file_sha256"):
            parser.compare_runs(
                self.parse(baseline_records()),
                self.parse(candidate_records()),
                manifest("A"),
                candidate,
            )

    def test_pair_comparison_requires_identical_deployment_and_nvif_tools(self) -> None:
        candidate = manifest("B")
        candidate["deployment_manifest_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "deployment_manifest_sha256"):
            parser.compare_runs(
                self.parse(baseline_records()),
                self.parse(candidate_records()),
                manifest("A"),
                candidate,
            )

    def test_cli_emits_machine_readable_pair_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal_a = root / "a.jsonl"
            journal_b = root / "b.jsonl"
            manifest_a = root / "A" / "manifest.json"
            manifest_b = root / "B" / "manifest.json"
            data_a = baseline_records().encode("utf-8")
            data_b = candidate_records().encode("utf-8")
            journal_a.write_bytes(data_a)
            journal_b.write_bytes(data_b)
            item_a = manifest("A")
            item_b = manifest("B")
            item_a["termination_reason"] = "bsp-eexist"
            item_a["workload_returncode"] = -2
            item_a["journal_delta_sha256"] = hashlib.sha256(data_a).hexdigest()
            item_b["journal_delta_sha256"] = hashlib.sha256(data_b).hexdigest()
            for variant, item in (("A", item_a), ("B", item_b)):
                dso = root / variant / "libgallium_drv_video.so"
                alias = root / variant / "driver" / "nouveau_drv_video.so"
                dso.parent.mkdir(parents=True)
                alias.parent.mkdir(parents=True)
                dso.write_bytes(b"synthetic dso content")
                alias.symlink_to(dso)
                item["dso_resolved_path"] = str(dso)
                item["dso_alias_path"] = str(alias)
                preflight = root / variant / "journal-preflight.jsonl"
                preflight.write_text(
                    json.dumps({
                        "MESSAGE": "nouveau: initialized",
                        "_BOOT_ID": item["boot_id"],
                        "_TRANSPORT": "kernel",
                    }) + "\n",
                    encoding="utf-8",
                )
                item["preflight_journal_sha256"] = hashlib.sha256(
                    preflight.read_bytes()
                ).hexdigest()
                item["nouveau_module_path"] = str(TEST_MODULE_PATH.resolve())
                item["nouveau_module_file_sha256"] = TEST_MODULE_SHA256
            bind_test_deployment(
                item_a,
                dso_paths={
                    "A": root / "A" / "libgallium_drv_video.so",
                    "B": root / "B" / "libgallium_drv_video.so",
                },
            )
            item_b.update({
                "deployment_manifest_path": item_a["deployment_manifest_path"],
                "deployment_manifest_sha256": item_a["deployment_manifest_sha256"],
                "nvif_capture_sha256": item_a["nvif_capture_sha256"],
                "nvif_parser_sha256": item_a["nvif_parser_sha256"],
            })
            item_a["journal_delta_sha256"] = hashlib.sha256(data_a).hexdigest()
            item_b["journal_delta_sha256"] = hashlib.sha256(data_b).hexdigest()
            manifest_a.write_text(json.dumps(item_a), encoding="utf-8")
            manifest_b.write_text(json.dumps(item_b), encoding="utf-8")
            output = io.StringIO()
            def fake_hash(path: Path) -> str:
                if path.name == "libgallium_drv_video.so":
                    return parser.EXPECTED_DSO_SHA256[path.parent.name]
                if path == Path(parser.load_runtime_profile()["argv"][-1]):
                    return parser.EXPECTED_INPUT_SHA256
                if path == TEST_MODULE_PATH.resolve():
                    return TEST_MODULE_SHA256
                return hashlib.sha256(path.read_bytes()).hexdigest()

            with (
                mock.patch.object(parser, "_sha256_file", side_effect=fake_hash),
                redirect_stdout(output),
            ):
                status = parser.main([
                    "--journal-a", str(journal_a),
                    "--manifest-a", str(manifest_a),
                    "--journal-b", str(journal_b),
                    "--manifest-b", str(manifest_b),
                ])

        self.assertEqual(status, 0)
        self.assertEqual(
            json.loads(output.getvalue())["outcome"],
            "B_CORRECTION_PROVEN",
        )

    def test_single_a_cli_emits_reusable_proof_only_for_exact_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal = root / "journal-delta.jsonl"
            manifest_path = root / "manifest.json"
            data = baseline_records().encode("utf-8")
            journal.write_bytes(data)
            item = proven_a_manifest()
            item["journal_delta_sha256"] = hashlib.sha256(data).hexdigest()
            manifest_path.write_text(json.dumps(item), encoding="utf-8")
            manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            output = io.StringIO()
            with (
                mock.patch.object(parser, "read_manifest", return_value=item),
                redirect_stdout(output),
            ):
                status = parser.main([
                    "--variant", "A",
                    "--journal", str(journal),
                    "--manifest", str(manifest_path),
                ])

        report = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(report["outcome"], "A_CHAIN_PROVEN")
        self.assertEqual(
            report["identity"]["input_sha256"], parser.EXPECTED_INPUT_SHA256
        )
        self.assertEqual(report["manifest_sha256"], manifest_sha256)

    def test_a_proof_files_are_recomputed_before_variant_b(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal_path = root / "journal-delta.jsonl"
            manifest_path = root / "manifest.json"
            proof_path = root / "proof.json"
            journal_bytes = baseline_records().encode("utf-8")
            journal_path.write_bytes(journal_bytes)
            item = proven_a_manifest()
            item["journal_delta_sha256"] = hashlib.sha256(journal_bytes).hexdigest()
            manifest_bytes = json.dumps(item, sort_keys=True).encode("utf-8") + b"\n"
            manifest_path.write_bytes(manifest_bytes)
            events = parser.read_events(io.StringIO(journal_bytes.decode("utf-8")))
            proof = parser.single_a_report(
                events,
                item,
                manifest_bytes,
                journal_bytes,
                parser.hard_stop_records(journal_bytes),
            )
            proof_path.write_text(json.dumps(proof), encoding="utf-8")

            with mock.patch.object(parser, "read_manifest", return_value=item):
                validated = parser.validate_a_proof_files(
                    proof_path=proof_path,
                    manifest_path=manifest_path,
                    journal_path=journal_path,
                )

            self.assertEqual(
                validated["identity"]["dso_sha256"],
                parser.EXPECTED_DSO_SHA256["A"],
            )
            self.assertEqual(
                validated["journal_delta_sha256"],
                hashlib.sha256(journal_bytes).hexdigest(),
            )
            proof["outcome"] = "A_CHAIN_NOT_REPRODUCED"
            proof_path.write_text(json.dumps(proof), encoding="utf-8")
            with mock.patch.object(parser, "read_manifest", return_value=item):
                with self.assertRaisesRegex(ValueError, "does not exactly match"):
                    parser.validate_a_proof_files(
                        proof_path=proof_path,
                        manifest_path=manifest_path,
                        journal_path=journal_path,
                    )

    def test_cli_classifies_workload_hard_stop_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal_a = root / "a.jsonl"
            journal_b = root / "b.jsonl"
            manifest_a = root / "A" / "manifest.json"
            manifest_b = root / "B" / "manifest.json"
            data_a = baseline_records().encode("utf-8")
            text_b = candidate_records() + journal_line(
                "Bus error",
                500,
                boot_id="boot-b",
                syslog_identifier="nouveau-nvif-lifetime",
            ) + "\n"
            data_b = text_b.encode("utf-8")
            journal_a.write_bytes(data_a)
            journal_b.write_bytes(data_b)
            item_a = manifest("A")
            item_b = manifest("B")
            item_a["termination_reason"] = "bsp-eexist"
            item_a["workload_returncode"] = -2
            item_a["journal_delta_sha256"] = hashlib.sha256(data_a).hexdigest()
            item_b["journal_delta_sha256"] = hashlib.sha256(data_b).hexdigest()
            item_b["termination_reason"] = "workload-hard-stop"
            item_b["postrun_hard_stops"] = parser.hard_stop_records(data_b)
            item_b["workload_returncode"] = -7

            pair_dso_paths: dict[str, Path] = {}
            for variant, item in (("A", item_a), ("B", item_b)):
                dso = root / variant / "libgallium_drv_video.so"
                pair_dso_paths[variant] = dso
                alias = root / variant / "driver" / "nouveau_drv_video.so"
                dso.parent.mkdir(parents=True)
                alias.parent.mkdir(parents=True)
                dso.write_bytes(b"synthetic dso content")
                alias.symlink_to(dso)
                item["dso_resolved_path"] = str(dso)
                item["dso_alias_path"] = str(alias)
                preflight = root / variant / "journal-preflight.jsonl"
                preflight.write_text(
                    json.dumps({
                        "MESSAGE": "nouveau: initialized",
                        "_BOOT_ID": item["boot_id"],
                        "_TRANSPORT": "kernel",
                    }) + "\n",
                    encoding="utf-8",
                )
                item["preflight_journal_sha256"] = hashlib.sha256(
                    preflight.read_bytes()
                ).hexdigest()
                item["nouveau_module_path"] = str(TEST_MODULE_PATH.resolve())
                item["nouveau_module_file_sha256"] = TEST_MODULE_SHA256
            bind_test_deployment(item_a, dso_paths=pair_dso_paths)
            item_b.update({
                "deployment_manifest_path": item_a["deployment_manifest_path"],
                "deployment_manifest_sha256": item_a["deployment_manifest_sha256"],
                "nvif_capture_sha256": item_a["nvif_capture_sha256"],
                "nvif_parser_sha256": item_a["nvif_parser_sha256"],
            })
            for variant, item in (("A", item_a), ("B", item_b)):
                (root / variant / "manifest.json").write_text(
                    json.dumps(item),
                    encoding="utf-8",
                )

            output = io.StringIO()

            def fake_hash(path: Path) -> str:
                if path.name == "libgallium_drv_video.so":
                    return parser.EXPECTED_DSO_SHA256[path.parent.name]
                if path == Path(parser.load_runtime_profile()["argv"][-1]):
                    return parser.EXPECTED_INPUT_SHA256
                if path == TEST_MODULE_PATH.resolve():
                    return TEST_MODULE_SHA256
                return hashlib.sha256(path.read_bytes()).hexdigest()

            with (
                mock.patch.object(parser, "_sha256_file", side_effect=fake_hash),
                redirect_stdout(output),
            ):
                status = parser.main([
                    "--journal-a", str(journal_a),
                    "--manifest-a", str(manifest_a),
                    "--journal-b", str(journal_b),
                    "--manifest-b", str(manifest_b),
                ])

        result = json.loads(output.getvalue())
        self.assertEqual(result["outcome"], "A_INCONCLUSIVE")
        self.assertEqual(status, 3)

    def test_cli_returns_nonzero_for_inconclusive_and_kernel_failure(self) -> None:
        cases = (
            (
                "inconclusive",
                baseline_records(include_free=False),
                candidate_records(),
                None,
                3,
            ),
            (
                "kernel-failure",
                baseline_records(),
                candidate_records() + journal_line(
                    "nouveau fifo: SCHED_ERROR 0a [CTXSW_TIMEOUT]",
                    500,
                    pid=None,
                    boot_id="boot-b",
                ) + "\n",
                [{
                    "source": "kernel",
                    "kinds": ["CTXSW_TIMEOUT"],
                    "message": "nouveau fifo: SCHED_ERROR 0a [CTXSW_TIMEOUT]",
                }],
                3,
            ),
        )
        for name, text_a, text_b, stops_b, expected_status in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                journal_a = root / "a.jsonl"
                journal_b = root / "b.jsonl"
                data_a = text_a.encode("utf-8")
                data_b = text_b.encode("utf-8")
                journal_a.write_bytes(data_a)
                journal_b.write_bytes(data_b)
                item_a = manifest("A")
                item_b = manifest("B")
                item_a["journal_delta_sha256"] = hashlib.sha256(data_a).hexdigest()
                item_b["journal_delta_sha256"] = hashlib.sha256(data_b).hexdigest()
                item_b["postrun_hard_stops"] = stops_b or []
                output = io.StringIO()
                with (
                    mock.patch.object(
                        parser,
                        "read_manifest",
                        side_effect=[item_a, item_b],
                    ),
                    redirect_stdout(output),
                ):
                    status = parser.main([
                        "--journal-a", str(journal_a),
                        "--manifest-a", str(root / "a-manifest.json"),
                        "--journal-b", str(journal_b),
                        "--manifest-b", str(root / "b-manifest.json"),
                    ])
                self.assertEqual(status, expected_status)


if __name__ == "__main__":
    unittest.main()
