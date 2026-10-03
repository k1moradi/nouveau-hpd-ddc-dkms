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


def new_line(*, ret: int, route: int, parent: int = 0x22) -> str:
    return (
        "NOUVEAU_DIAG_NVIF_NEW key=0x500 obj=0x500 "
        f"object_token=0x500 route_token=0x{route:x} parent=0x{parent:x} "
        f"parent_handle=0x{route:x} fd=9 class=0x000095b1 "
        f"ret={ret}"
    )


def del_line(*, fd: int, ret: int) -> str:
    return (
        "NOUVEAU_DIAG_NVIF_DEL key=0x500 obj=0x500 parent=0x22 "
        "parent_handle=0x2 object_handle=0x501 "
        f"fd={fd} drm_fd=9 class=0x000095b1 ret={ret}"
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
                     delete_ret: int = -9,
                     include_free: bool = True) -> str:
    records = [
        journal_line(new_line(ret=0, route=0x2), 100),
        journal_line(del_line(fd=2, ret=delete_ret), 200),
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
        journal_line(new_line(ret=-17, route=0x3, parent=0x23), 400),
    ])
    return "\n".join(records) + "\n"


def candidate_records(*, delete_fd: int = 9, delete_ret: int = 0,
                      second_ret: int = 0) -> str:
    return "\n".join([
        journal_line(new_line(ret=0, route=0x2), 100, boot_id="boot-b"),
        journal_line(del_line(fd=delete_fd, ret=delete_ret), 200, boot_id="boot-b"),
        journal_line(channel_free_line(), 300, boot_id="boot-b"),
        journal_line(
            new_line(ret=second_ret, route=0x3, parent=0x23),
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
        "nouveau_srcversion": parser.EXPECTED_NOUVEAU_SRCVERSION,
        "nouveau_module_path": str(TEST_MODULE_PATH.resolve()),
        "nouveau_module_file_sha256": TEST_MODULE_SHA256,
        "nouveau_module_srcversion": parser.EXPECTED_NOUVEAU_SRCVERSION,
        "nouveau_module_vermagic": (
            "7.0.0-34-generic SMP preempt mod_unload modversions"
        ),
        "nouveau_parameters": {"diag_ctxsw": "N"},
        "dso_sha256": parser.EXPECTED_DSO_SHA256[variant],
        "dso_resolved_path": f"/tmp/{variant}/libgallium_drv_video.so",
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


class NvifLifetimeParserTests(unittest.TestCase):
    def parse(self, text: str) -> list[parser.Event]:
        return parser.read_events(io.StringIO(text))

    def test_baseline_requires_complete_wrong_fd_duplicate_chain(self) -> None:
        text = baseline_records(duplicate_layer="abi16")
        result = parser.analyze(self.parse(text), "A")
        self.assertEqual(result["result"], "BASELINE_REPRODUCED_EEXIST_CHAIN")
        self.assertEqual(result["matching_lifecycles"][0]["duplicate_layer"], "abi16")
        self.assertEqual(result["matching_lifecycles"][0]["delete_ret"], -9)

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
        self.assertEqual(result["outcome"], "INCONCLUSIVE_BASELINE_NOT_REPRODUCED")

    def test_pair_comparison_accepts_only_matching_a_and_b_sequences(self) -> None:
        baseline = manifest("A")
        baseline["workload_returncode"] = -2
        baseline["termination_reason"] = "bsp-eexist"
        result = parser.compare_runs(
            self.parse(baseline_records()),
            self.parse(candidate_records()),
            baseline,
            manifest("B"),
        )
        self.assertEqual(result["outcome"], "A_REPRODUCED_B_LIFECYCLE_SUCCEEDED")

    def test_pair_comparison_accepts_zero_return_del_with_abi16_stale_key(self) -> None:
        baseline = manifest("A")
        baseline["workload_returncode"] = -2
        baseline["termination_reason"] = "bsp-eexist"
        result = parser.compare_runs(
            self.parse(baseline_records(delete_ret=0)),
            self.parse(candidate_records()),
            baseline,
            manifest("B"),
        )
        self.assertEqual(result["outcome"], "A_REPRODUCED_B_LIFECYCLE_SUCCEEDED")
        self.assertEqual(
            result["variant_a"]["result"],
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
        self.assertEqual(result["outcome"], "INCOMPLETE_WORKLOAD")

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
            manifest("A"),
            manifest("B"),
            hard_stops_b=[{
                "source": "kernel",
                "kinds": ["CTXSW_TIMEOUT"],
                "message": "nouveau fifo: SCHED_ERROR 0a [CTXSW_TIMEOUT]",
            }],
        )
        self.assertEqual(result["outcome"], "KERNEL_FAILURE")

    def test_workload_fatal_is_scoped_and_overrides_lifecycle_success(self) -> None:
        result = parser.compare_runs(
            self.parse(baseline_records()),
            self.parse(candidate_records()),
            manifest("A"),
            manifest("B"),
            hard_stops_b=[{
                "source": "workload",
                "kinds": ["SIGBUS"],
                "message": "Bus error",
            }],
        )
        self.assertEqual(result["outcome"], "WORKLOAD_FAILURE")

    def test_unproven_journal_boundary_overrides_lifecycle_success(self) -> None:
        candidate = manifest("B")
        candidate["journal_boundary_proven"] = False
        result = parser.compare_runs(
            self.parse(baseline_records()),
            self.parse(candidate_records()),
            manifest("A"),
            candidate,
        )
        self.assertEqual(result["outcome"], "JOURNAL_BOUNDARY_UNPROVEN")

    def test_nonzero_workload_overrides_lifecycle_success(self) -> None:
        candidate = manifest("B")
        candidate["workload_returncode"] = 1
        result = parser.compare_runs(
            self.parse(baseline_records()),
            self.parse(candidate_records()),
            manifest("A"),
            candidate,
        )
        self.assertEqual(result["outcome"], "INCOMPLETE_WORKLOAD")

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
                "nouveau_module_path": str(module),
                "nouveau_module_file_sha256": hashlib.sha256(
                    module.read_bytes()
                ).hexdigest(),
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
            "A_REPRODUCED_B_LIFECYCLE_SUCCEEDED",
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
            item_a["journal_delta_sha256"] = hashlib.sha256(data_a).hexdigest()
            item_b["journal_delta_sha256"] = hashlib.sha256(data_b).hexdigest()
            item_b["termination_reason"] = "workload-hard-stop"
            item_b["postrun_hard_stops"] = parser.hard_stop_records(data_b)
            item_b["workload_returncode"] = -7

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
        self.assertEqual(result["outcome"], "WORKLOAD_FAILURE")
        self.assertEqual(status, 4)

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
                4,
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
