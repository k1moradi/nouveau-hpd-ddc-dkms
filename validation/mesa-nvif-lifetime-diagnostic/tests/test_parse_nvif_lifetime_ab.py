from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import parse_nvif_lifetime_ab as parser


def journal_line(
    message: str,
    stamp: int,
    *,
    pid: int | None = 222,
    boot_id: str = "boot-a",
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


def duplicate_line(*, layer: str = "abi16", channel: int = 0x3) -> str:
    message = (
        f"NOUVEAU_DIAG_NVIF_DUP layer={layer} key=0x500 "
        "class=0x000095b1"
    )
    if layer == "abi16":
        message += f" channel=0x{channel:x}"
    return message


def baseline_records(*, duplicate_channel: int = 0x3,
                     duplicate_layer: str = "abi16",
                     include_free: bool = True) -> str:
    records = [
        journal_line(new_line(ret=0, route=0x2), 100),
        journal_line(del_line(fd=2, ret=-9), 200),
    ]
    if include_free:
        records.append(journal_line(channel_free_line(), 300))
    records.extend([
        journal_line(
            "nouveau 0000:01:00.0: drm: "
            + duplicate_line(layer=duplicate_layer, channel=duplicate_channel),
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
    return {
        "schema": 1,
        "variant": variant,
        "boot_id": boot_id or f"boot-{variant.lower()}",
        "input_sha256": parser.EXPECTED_INPUT_SHA256,
        "command_argv": ["mpv", "--hwdec=vaapi-copy", "/home/keivan/test_1080p.mkv"],
        "normalized_environment": {
            "LIBVA_DRIVER_NAME": "nouveau",
            "LIBVA_DRIVERS_PATH": "<variant-private-directory>",
            "DISPLAY": ":0",
        },
        "kernel": "7.0.0-34-generic",
        "nouveau_srcversion": parser.EXPECTED_NOUVEAU_SRCVERSION,
        "dso_sha256": parser.EXPECTED_DSO_SHA256[variant],
    }


class NvifLifetimeParserTests(unittest.TestCase):
    def parse(self, text: str) -> list[parser.Event]:
        return parser.read_events(io.StringIO(text))

    def test_baseline_requires_complete_wrong_fd_duplicate_chain(self) -> None:
        for layer in ("abi16", "nvkm"):
            text = baseline_records(duplicate_layer=layer)
            result = parser.analyze(self.parse(text), "A")
            self.assertEqual(result["result"], "BASELINE_REPRODUCED_EEXIST_CHAIN")
            self.assertEqual(result["matching_lifecycles"][0]["duplicate_layer"], layer)

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
        result = parser.compare_runs(
            self.parse(baseline_records()),
            self.parse(candidate_records()),
            manifest("A"),
            manifest("B"),
        )
        self.assertEqual(result["outcome"], "A_REPRODUCED_B_LIFECYCLE_SUCCEEDED")

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

    def test_manifest_loader_pins_kernel_module_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            bad = manifest("A")
            bad["nouveau_srcversion"] = "reviewed-diagnostic-srcversion"
            path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "srcversion"):
                parser.read_manifest(path, "A")

    def test_cli_emits_machine_readable_pair_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal_a = root / "a.jsonl"
            journal_b = root / "b.jsonl"
            manifest_a = root / "a.json"
            manifest_b = root / "b.json"
            journal_a.write_text(baseline_records(), encoding="utf-8")
            journal_b.write_text(candidate_records(), encoding="utf-8")
            manifest_a.write_text(json.dumps(manifest("A")), encoding="utf-8")
            manifest_b.write_text(json.dumps(manifest("B")), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
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


if __name__ == "__main__":
    unittest.main()
