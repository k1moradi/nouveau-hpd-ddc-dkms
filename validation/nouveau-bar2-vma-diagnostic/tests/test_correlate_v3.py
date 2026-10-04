#!/usr/bin/env python3
"""CPU-only tests for conservative v3 BAR2 numeric-range correlation."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest


PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))
import correlate_v3 as correlate_module


BOOT = "00112233445566778899aabbccddeeff"
FAULT_ADDRESS = 0x5EA123


def journal_line(message: str, usec: int, boot: str = BOOT) -> str:
    return json.dumps(
        {
            "_TRANSPORT": "kernel",
            "_BOOT_ID": boot,
            "__MONOTONIC_TIMESTAMP": str(usec),
            "MESSAGE": message,
        }
    )


def vma(test_id: int, stage: str, source: str, usec: int,
        start: int = 0x5E9000, size: int = 0x2000, rc: int = 0) -> str:
    return journal_line(
        "nouveau 0000:01:00.0: NOUVEAU_DIAG_V3_VMA "
        f"vmm=BAR2 id={test_id} stage={stage} obj=0xffff888000000000 "
        f"src={source} va={start:#x} len={size:#x} rc={rc}",
        usec,
    )


def fault(usec: int, address: int = FAULT_ADDRESS, boot: str = BOOT) -> str:
    return journal_line(
        "nouveau 0000:01:00.0: NOUVEAU_DIAG_BAR2_FAULT "
        f"unit=05 inst=000ffbb7 valo={address & 0xffffffff:08x} "
        f"vahi={address >> 32:08x} type=00000002",
        usec,
        boot,
    )


def complete_test(
    test_id: int = 1,
    start: int = 0x5E9000,
    size: int = 0x2000,
    time_offset: int = 0,
) -> list[str]:
    return [
        vma(test_id, "allocated", "unavailable", time_offset + 5, start, size),
        vma(test_id, "kmap_acquired", "current", time_offset + 10, start, size),
        vma(test_id, "nested_acquired", "current", time_offset + 20, start, size),
        vma(test_id, "nested_ref_remaining", "current", time_offset + 30, start, size),
        vma(test_id, "released", "last_observed", time_offset + 40, start, size),
        vma(test_id, "reacquired", "current", time_offset + 50, start, size),
        vma(test_id, "verified", "current", time_offset + 60, start, size),
        vma(test_id, "destroying", "last_observed", time_offset + 70, start, size),
        vma(test_id, "destroyed", "last_observed", time_offset + 80, start, size),
    ]


class CorrelateV3Tests(unittest.TestCase):
    def test_fault_between_two_pinned_current_snapshots_is_numeric_candidate(self) -> None:
        records = complete_test()
        records.append(fault(15))
        result = correlate_module.correlate(records)
        self.assertTrue(result["numeric_address_correlation_only"])
        self.assertFalse(result["pointer_identity_used"])
        item = result["faults"][0]
        self.assertEqual(item["fault_address"], "0x5ea123")
        self.assertEqual(item["fault_instance_address"], "0xffbb7000")
        self.assertEqual(item["outcome"], "ONE_NUMERIC_ACTIVE_VMA_CANDIDATE")
        self.assertEqual(item["candidates"][0]["test_id"], 1)

    def test_cached_released_range_never_matches_after_release(self) -> None:
        records = complete_test()
        records.append(fault(45))
        item = correlate_module.correlate(records)["faults"][0]
        self.assertEqual(item["outcome"], "NO_OBSERVED_ACTIVE_V3_VMA_MATCH")
        self.assertEqual(item["candidates"], [])

    def test_fault_in_unlogged_last_release_interval_is_inconclusive(self) -> None:
        records = complete_test()
        records.append(fault(35))
        item = correlate_module.correlate(records)["faults"][0]
        self.assertEqual(item["outcome"], "INCONCLUSIVE_RELEASE_TRANSITION_WINDOW")
        self.assertEqual(item["candidates"][0]["kind"], "release_transition")

    def test_equal_monotonic_timestamp_does_not_infer_event_order(self) -> None:
        records = complete_test()
        records.append(fault(20))
        item = correlate_module.correlate(records)["faults"][0]
        self.assertEqual(item["outcome"], "INCONCLUSIVE_EQUAL_TIMESTAMP_ORDER")

    def test_overlapping_live_test_ranges_are_ambiguous(self) -> None:
        records = [
            *complete_test(1, 0x5000, 0x4000),
            *complete_test(2, 0x6000, 0x4000),
            fault(25, 0x7000),
        ]
        item = correlate_module.correlate(records)["faults"][0]
        self.assertEqual(
            item["outcome"], "AMBIGUOUS_MULTIPLE_NUMERIC_VMA_CANDIDATES"
        )
        self.assertEqual({entry["test_id"] for entry in item["candidates"]}, {1, 2})

    def test_missing_destroyed_record_prevents_strong_attribution(self) -> None:
        records = [
            vma(1, "allocated", "unavailable", 5),
            vma(1, "kmap_acquired", "current", 10),
            vma(1, "nested_acquired", "current", 20),
            fault(15),
        ]
        item = correlate_module.correlate(records)["faults"][0]
        self.assertEqual(item["outcome"], "INCONCLUSIVE_TEST_LIFECYCLE_INCOMPLETE")

    def test_failed_current_lookup_does_not_extend_cached_vma_range(self) -> None:
        records = complete_test()
        records[2] = vma(
            1, "nested_acquired", "unavailable", 20, rc=-95
        )
        item = correlate_module.correlate([*records, fault(25)])["faults"][0]
        self.assertEqual(item["outcome"], "INCONCLUSIVE_ACTIVE_VMA_UNAVAILABLE")
        self.assertEqual(item["candidates"][0]["kind"], "unavailable")

    def test_changed_vma_while_map_reference_is_active_is_inconclusive(self) -> None:
        records = [
            vma(1, "allocated", "unavailable", 5, 0x5E9000, 0x2000),
            vma(1, "kmap_acquired", "current", 10, 0x5E9000, 0x2000),
            vma(1, "nested_acquired", "current", 20, 0x6E9000, 0x2000),
            vma(1, "nested_ref_remaining", "current", 30, 0x6E9000, 0x2000),
            vma(1, "released", "last_observed", 40, 0x6E9000, 0x2000),
            vma(1, "reacquired", "current", 50, 0x6E9000, 0x2000),
            vma(1, "verified", "current", 60, 0x6E9000, 0x2000),
            vma(1, "destroying", "last_observed", 70, 0x6E9000, 0x2000),
            vma(1, "destroyed", "last_observed", 80, 0x6E9000, 0x2000),
            fault(15, 0x5E9123),
        ]
        item = correlate_module.correlate(records)["faults"][0]
        self.assertEqual(
            item["outcome"], "INCONCLUSIVE_VMA_CHANGED_DURING_ACTIVE_MAP"
        )
        self.assertEqual(item["candidates"][0]["kind"], "vma_changed")

    def test_missing_final_release_record_does_not_bridge_reacquisition(self) -> None:
        records = [
            vma(1, "allocated", "unavailable", 5),
            vma(1, "kmap_acquired", "current", 10),
            vma(1, "nested_acquired", "current", 20),
            vma(1, "nested_ref_remaining", "current", 30),
            # The expected final release record is absent here.
            vma(1, "reacquired", "current", 50),
            vma(1, "verified", "current", 60),
            vma(1, "destroying", "last_observed", 70),
            vma(1, "destroyed", "last_observed", 80),
            fault(40),
        ]
        item = correlate_module.correlate(records)["faults"][0]
        self.assertEqual(item["outcome"], "INCONCLUSIVE_VMA_LIFECYCLE_GAP")
        self.assertEqual(item["candidates"][0]["kind"], "lifecycle_gap")

    def test_missing_initial_map_stages_cannot_yield_numeric_candidate(self) -> None:
        records = [
            vma(1, "nested_acquired", "current", 20),
            vma(1, "nested_ref_remaining", "current", 30),
            vma(1, "released", "last_observed", 40),
            vma(1, "reacquired", "current", 50),
            vma(1, "verified", "current", 60),
            vma(1, "destroying", "last_observed", 70),
            vma(1, "destroyed", "last_observed", 80),
            fault(25),
        ]
        item = correlate_module.correlate(records)["faults"][0]
        self.assertEqual(
            item["outcome"], "INCONCLUSIVE_TEST_LIFECYCLE_INCOMPLETE"
        )
        self.assertEqual(item["candidates"][0]["kind"], "active")

    def test_missing_allocated_stage_is_inconclusive(self) -> None:
        records = complete_test()[1:] + [fault(15)]
        item = correlate_module.correlate(records)["faults"][0]
        self.assertEqual(
            item["outcome"], "INCONCLUSIVE_TEST_LIFECYCLE_INCOMPLETE"
        )

    def test_reordered_active_stages_are_inconclusive(self) -> None:
        records = [
            vma(1, "allocated", "unavailable", 5),
            vma(1, "nested_acquired", "current", 10),
            vma(1, "kmap_acquired", "current", 20),
            vma(1, "destroying", "last_observed", 30),
            vma(1, "destroyed", "last_observed", 40),
            fault(15),
        ]
        item = correlate_module.correlate(records)["faults"][0]
        self.assertEqual(
            item["outcome"], "INCONCLUSIVE_VMA_LIFECYCLE_GAP"
        )

    def test_complete_early_failure_stays_in_release_transition_window(self) -> None:
        records = [
            vma(1, "allocated", "unavailable", 5),
            vma(1, "kmap_acquired", "current", 10),
            vma(1, "destroying", "last_observed", 30),
            vma(1, "destroyed", "last_observed", 40),
            fault(20),
        ]
        item = correlate_module.correlate(records)["faults"][0]
        self.assertEqual(
            item["outcome"], "INCONCLUSIVE_RELEASE_TRANSITION_WINDOW"
        )

    def test_allocated_stage_must_have_unavailable_source(self) -> None:
        with self.assertRaisesRegex(
            correlate_module.CorrelationInputError,
            "allocated stage must begin with src=unavailable",
        ):
            correlate_module.correlate(
                [vma(1, "allocated", "last_observed", 5)]
            )

    def test_unobserved_vma_input_is_not_global_negative_proof(self) -> None:
        item = correlate_module.correlate([fault(20)])["faults"][0]
        self.assertEqual(
            item["outcome"], "NO_OBSERVED_ACTIVE_V3_VMA_MATCH"
        )

    def test_unexpected_fields_and_oversized_registers_are_rejected(self) -> None:
        record = json.loads(vma(1, "allocated", "unavailable", 5))
        record["MESSAGE"] += " extra=1"
        with self.assertRaisesRegex(
            correlate_module.CorrelationInputError, "unexpected fields"
        ):
            correlate_module.correlate([json.dumps(record)])

        record = json.loads(fault(20))
        record["MESSAGE"] = record["MESSAGE"].replace(
            "inst=000ffbb7", "inst=1000ffbb7"
        )
        with self.assertRaisesRegex(
            correlate_module.CorrelationInputError, "fault INST register exceeds 32 bits"
        ):
            correlate_module.correlate([json.dumps(record)])

    def test_current_source_is_rejected_for_release_stage(self) -> None:
        with self.assertRaisesRegex(
            correlate_module.CorrelationInputError, "non-mapped stage"
        ):
            correlate_module.correlate(
                [vma(1, "released", "current", 10)]
            )

    def test_malformed_lookup_result_is_reported_as_input_error(self) -> None:
        record = json.loads(vma(1, "kmap_acquired", "current", 10))
        record["MESSAGE"] = record["MESSAGE"].replace("rc=0", "rc=broken")
        with self.assertRaisesRegex(
            correlate_module.CorrelationInputError, "invalid VMA lookup result"
        ):
            correlate_module.correlate([json.dumps(record)])

    def test_mixed_boots_and_missing_journal_metadata_fail_closed(self) -> None:
        result = correlate_module.correlate(
            [vma(1, "kmap_acquired", "current", 10), fault(20, boot="ffeeddccbbaa99887766554433221100")]
        )
        self.assertEqual(result["outcome"], "INCONCLUSIVE_MIXED_BOOT_IDS")

        record = json.loads(vma(1, "kmap_acquired", "current", 10))
        del record["__MONOTONIC_TIMESTAMP"]
        with self.assertRaisesRegex(
            correlate_module.CorrelationInputError, "no monotonic timestamp"
        ):
            correlate_module.correlate([json.dumps(record)])

        record = json.loads(vma(1, "kmap_acquired", "current", 10))
        record["_TRANSPORT"] = "journal"
        with self.assertRaisesRegex(
            correlate_module.CorrelationInputError, "not kernel transport"
        ):
            correlate_module.correlate([json.dumps(record)])

    def test_no_records_and_fault_address_outside_vma_are_not_matches(self) -> None:
        empty = correlate_module.correlate([journal_line("ordinary kernel line", 1)])
        self.assertEqual(empty["outcome"], "INCONCLUSIVE_NO_DIAGNOSTIC_RECORDS")

        records = complete_test()
        records.append(fault(15, 0x700000))
        item = correlate_module.correlate(records)["faults"][0]
        self.assertEqual(item["outcome"], "NO_OBSERVED_ACTIVE_V3_VMA_MATCH")

    def test_cli_reports_numeric_candidate_without_calling_it_a_pass(self) -> None:
        records = complete_test()
        records.append(fault(15))
        with tempfile.TemporaryDirectory() as temp_dir:
            journal_path = Path(temp_dir) / "kernel.jsonl"
            journal_path.write_text("\n".join(records) + "\n", encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = correlate_module.main([str(journal_path)])

        self.assertEqual(status, 0)
        self.assertEqual(stderr.getvalue(), "")
        report = json.loads(stdout.getvalue())
        self.assertEqual(report["outcome"], "CORRELATION_REPORT_ONLY")
        self.assertEqual(
            report["faults"][0]["outcome"],
            "ONE_NUMERIC_ACTIVE_VMA_CANDIDATE",
        )

    def test_cli_no_fault_and_invalid_input_have_distinct_nonzero_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            journal_path = Path(temp_dir) / "kernel.jsonl"
            stdout = io.StringIO()
            stderr = io.StringIO()
            journal_path.write_text(
                journal_line("ordinary kernel line", 1) + "\n", encoding="utf-8"
            )
            with redirect_stdout(stdout), redirect_stderr(stderr):
                no_fault_status = correlate_module.main([str(journal_path)])
            self.assertEqual(no_fault_status, 3)
            self.assertEqual(
                json.loads(stdout.getvalue())["outcome"],
                "INCONCLUSIVE_NO_DIAGNOSTIC_RECORDS",
            )

            stdout = io.StringIO()
            stderr = io.StringIO()
            journal_path.write_text("not-json\n", encoding="utf-8")
            with redirect_stdout(stdout), redirect_stderr(stderr):
                invalid_status = correlate_module.main([str(journal_path)])

        self.assertEqual(invalid_status, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("VMA_CORRELATION_ERROR", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_cli_mixed_boots_are_reported_separately_from_no_faults(self) -> None:
        records = [
            vma(1, "kmap_acquired", "current", 10),
            fault(20, boot="ffeeddccbbaa99887766554433221100"),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            journal_path = Path(temp_dir) / "kernel.jsonl"
            journal_path.write_text("\n".join(records) + "\n", encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = correlate_module.main([str(journal_path)])

        self.assertEqual(status, 4)
        self.assertEqual(stderr.getvalue(), "")
        report = json.loads(stdout.getvalue())
        self.assertEqual(report["outcome"], "INCONCLUSIVE_MIXED_BOOT_IDS")
        self.assertEqual(report["fault_count"], 1)
        self.assertEqual(len(report["boot_ids"]), 2)

    def test_cli_invalid_utf8_is_a_structured_input_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            journal_path = Path(temp_dir) / "kernel.jsonl"
            journal_path.write_bytes(b"\xff\n")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = correlate_module.main([str(journal_path)])

        self.assertEqual(status, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("VMA_CORRELATION_ERROR", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
