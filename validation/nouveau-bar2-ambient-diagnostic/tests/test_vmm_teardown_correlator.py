from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from correlate_bar2_lifetimes import CorrelationInputError, correlate, parse_journal


BOOT = "11223344-5566-7788-99aa-bbccddeeff00"
VMM_TAG = "NOUVEAU_DIAG_BAR2_VMM "
RAW_TAG = "NOUVEAU_DIAG_BAR2_FAULT "
TARGET_VA = 0x4000
TARGET_LEN = 0x3000


def vmm_row(
    seq: int,
    phase: str,
    mono_usec: int,
    *,
    operation_id: int = 9,
    target_id: int = 47,
    target_va: int = TARGET_VA,
    target_len: int = TARGET_LEN,
    target_map_refs: int = 0,
    pt_id: int = 0,
    pte_va: int = 0,
    pte_first: int = 0,
    pte_count: int = 0,
    pte_shift: int = 0,
    map_reset_valid: int = 1,
    map_reset_gen: int = 2,
    current_reset_gen: int = 2,
    flush_depth: int = -1,
) -> dict[str, object]:
    message = (
        "nouveau 0000:01:00.0: bar: " + VMM_TAG
        + f"seq={seq} op={operation_id} target_id={target_id} phase={phase} "
        + f"target_va=0x{target_va:x} target_len=0x{target_len:x} "
        + f"target_map_refs={target_map_refs} pt_id={pt_id} pte_va=0x{pte_va:x} "
        + f"pte_first={pte_first} pte_count={pte_count} pte_shift={pte_shift} "
        + f"map_reset_valid={map_reset_valid} map_reset_gen={map_reset_gen} "
        + f"current_reset_gen={current_reset_gen} flush_depth={flush_depth}"
    )
    return {
        "MESSAGE": message,
        "_BOOT_ID": BOOT,
        "_TRANSPORT": "kernel",
        "__MONOTONIC_TIMESTAMP": str(mono_usec),
        "NOUVEAU_DIAG_RING_MONO_NS": str(mono_usec * 1000),
    }


def pte_row(
    seq: int, phase: str, mono_usec: int, *, pt_id: int = 5,
    operation_id: int = 9,
    map_reset_gen: int = 2, current_reset_gen: int = 2,
) -> dict[str, object]:
    return vmm_row(
        seq, phase, mono_usec, pt_id=pt_id, pte_va=TARGET_VA,
        pte_first=3, pte_count=1, pte_shift=12,
        operation_id=operation_id,
        map_reset_gen=map_reset_gen, current_reset_gen=current_reset_gen,
    )


def reset_row(seq: int, generation: int, stage: str, mono_usec: int) -> dict[str, object]:
    message = (
        "nouveau 0000:01:00.0: bar: NOUVEAU_DIAG_BAR2_RESET "
        + f"seq={seq} gen={generation} stage={stage}"
    )
    return {
        "MESSAGE": message,
        "_BOOT_ID": BOOT,
        "_TRANSPORT": "kernel",
        "__MONOTONIC_TIMESTAMP": str(mono_usec),
        "NOUVEAU_DIAG_RING_MONO_NS": str(mono_usec * 1000),
    }


def flush_row(seq: int, phase: str, mono_usec: int, depth: int = 2) -> dict[str, object]:
    return vmm_row(seq, phase, mono_usec, flush_depth=depth)


def fault_pair(va: int = TARGET_VA, raw_usec: int = 14, decoded_usec: int = 17):
    raw = (
        "nouveau 0000:01:00.0: fifo: " + RAW_TAG
        + f"unit=05 inst=000ffbb7 valo={va & 0xffffffff:08x} "
        + f"vahi={(va >> 32):08x} type=00000742"
    )
    decoded = (
        "nouveau 0000:01:00.0: fifo: fault 00 [READ] "
        + f"at {va:016x} engine 05 [BAR2] client 07 [HUB/HOST_CPU] "
        + "reason 02 [PTE] on channel -1 [00ffbb7000 unknown]"
    )
    return [
        {"MESSAGE": raw, "_BOOT_ID": BOOT, "_TRANSPORT": "kernel",
         "__MONOTONIC_TIMESTAMP": str(raw_usec)},
        {"MESSAGE": decoded, "_BOOT_ID": BOOT, "_TRANSPORT": "kernel",
         "__MONOTONIC_TIMESTAMP": str(decoded_usec)},
    ]


def run_correlation(records: list[dict[str, object]]) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "merged.jsonl"
        path.write_text("".join(json.dumps(item) + "\n" for item in records))
        return correlate(parse_journal(path))


def operation_prefix() -> list[dict[str, object]]:
    return [vmm_row(1, "VMM_PUT_BEGIN", 10)]


class VmmTeardownCorrelationTests(unittest.TestCase):
    def test_fault_during_pte_clear_matches_page_table_memory_and_range(self):
        records = operation_prefix() + [
            pte_row(2, "PTE_UNMAP_BEGIN", 12),
            pte_row(3, "PTE_WRITE_DONE", 16),
            flush_row(4, "VMM_FLUSH_BEGIN", 18),
            flush_row(5, "VMM_FLUSH_DONE", 19),
            vmm_row(6, "VMA_RANGE_RELEASE", 20),
            vmm_row(7, "VMM_PUT_DONE", 21),
        ] + fault_pair(raw_usec=14, decoded_usec=17)
        result = run_correlation(records)
        teardown = result["faults"][0]["vmm_teardown"]
        self.assertEqual(teardown["outcome"], "DURING_PTE_UNMAP")
        candidate = teardown["candidate"]
        self.assertEqual(candidate["operation_id"], 9)
        self.assertEqual(candidate["target_instobj_id"], 47)
        self.assertEqual(candidate["target_map_refs_at_teardown"], 0)
        self.assertEqual(candidate["page_table_memory_id"], 5)
        self.assertTrue(candidate["exact_pte_range_match"])
        self.assertFalse(candidate["causal_ownership_proven"])

    def test_after_pte_write_before_flush_is_distinct(self):
        records = operation_prefix() + [
            pte_row(2, "PTE_UNMAP_BEGIN", 11),
            pte_row(3, "PTE_WRITE_DONE", 12),
            flush_row(4, "VMM_FLUSH_BEGIN", 16),
            flush_row(5, "VMM_FLUSH_DONE", 18),
            vmm_row(6, "VMA_RANGE_RELEASE", 20),
            vmm_row(7, "VMM_PUT_DONE", 21),
        ] + fault_pair(raw_usec=14, decoded_usec=15)
        teardown = run_correlation(records)["faults"][0]["vmm_teardown"]
        self.assertEqual(teardown["outcome"], "AFTER_PTE_WRITE_BEFORE_FLUSH")

    def test_fault_during_flush_is_distinct(self):
        records = operation_prefix() + [
            pte_row(2, "PTE_UNMAP_BEGIN", 11),
            pte_row(3, "PTE_WRITE_DONE", 12),
            flush_row(4, "VMM_FLUSH_BEGIN", 16),
            flush_row(5, "VMM_FLUSH_DONE", 19),
            vmm_row(6, "VMA_RANGE_RELEASE", 20),
            vmm_row(7, "VMM_PUT_DONE", 21),
        ] + fault_pair(raw_usec=17, decoded_usec=18)
        teardown = run_correlation(records)["faults"][0]["vmm_teardown"]
        self.assertEqual(teardown["outcome"], "DURING_VMM_FLUSH")

    def test_fault_during_page_table_last_reference_preserves_identity(self):
        records = operation_prefix() + [
            pte_row(2, "PT_LAST_REF_BEGIN", 12),
            flush_row(3, "VMM_FLUSH_BEGIN", 14),
            flush_row(4, "VMM_FLUSH_DONE", 15),
            pte_row(5, "PT_LAST_REF_DONE", 18),
            vmm_row(6, "VMA_RANGE_RELEASE", 20),
            vmm_row(7, "VMM_PUT_DONE", 21),
        ] + fault_pair(raw_usec=13, decoded_usec=16)
        candidate = run_correlation(records)["faults"][0]["vmm_teardown"]["candidate"]
        self.assertEqual(candidate["phase"], "DURING_PT_LAST_REF")
        self.assertEqual(candidate["page_table_memory_id"], 5)
        self.assertTrue(candidate["pt_operation_match_at_fault"])

    def test_unclosed_page_table_last_reference_is_rejected(self):
        records = operation_prefix() + [
            pte_row(2, "PT_LAST_REF_BEGIN", 12),
            vmm_row(3, "VMA_RANGE_RELEASE", 20),
            vmm_row(4, "VMM_PUT_DONE", 21),
        ] + fault_pair(raw_usec=16, decoded_usec=17)
        with self.assertRaisesRegex(CorrelationInputError, "invalid VMA_RANGE_RELEASE"):
            run_correlation(records)

    def test_unknown_page_table_memory_id_is_explicit_not_rejected(self):
        records = operation_prefix() + [
            pte_row(2, "PTE_UNMAP_BEGIN", 12, pt_id=0),
            pte_row(3, "PTE_WRITE_DONE", 16, pt_id=0),
            vmm_row(4, "VMA_RANGE_RELEASE", 20),
            vmm_row(5, "VMM_PUT_DONE", 21),
        ] + fault_pair(raw_usec=14, decoded_usec=17)
        candidate = run_correlation(records)["faults"][0]["vmm_teardown"]["candidate"]
        self.assertTrue(candidate["exact_pte_range_match"])
        self.assertFalse(candidate["page_table_memory_identity_known"])
        self.assertIsNone(candidate["page_table_memory_id"])

    def test_fault_after_flush_before_release_is_distinct(self):
        records = operation_prefix() + [
            pte_row(2, "PTE_UNMAP_BEGIN", 11),
            pte_row(3, "PTE_WRITE_DONE", 12),
            flush_row(4, "VMM_FLUSH_BEGIN", 13),
            flush_row(5, "VMM_FLUSH_DONE", 14),
            vmm_row(6, "VMA_RANGE_RELEASE", 17),
            vmm_row(7, "VMM_PUT_DONE", 18),
        ] + fault_pair(raw_usec=15, decoded_usec=16)
        teardown = run_correlation(records)["faults"][0]["vmm_teardown"]
        self.assertEqual(teardown["outcome"], "AFTER_FLUSH_BEFORE_VMA_RELEASE")

    def test_fault_after_vma_range_release_before_put_done_is_distinct(self):
        records = operation_prefix() + [
            vmm_row(2, "VMA_RANGE_RELEASE", 16),
            vmm_row(3, "VMM_PUT_DONE", 20),
        ] + fault_pair(raw_usec=17, decoded_usec=18)
        teardown = run_correlation(records)["faults"][0]["vmm_teardown"]
        self.assertEqual(
            teardown["outcome"], "AFTER_VMA_RELEASE_BEFORE_VMM_PUT_DONE",
        )

    def test_fault_after_completed_put_is_numeric_historical_match_only(self):
        records = operation_prefix() + [
            vmm_row(2, "VMA_RANGE_RELEASE", 11),
            vmm_row(3, "VMM_PUT_DONE", 12),
        ] + fault_pair(raw_usec=14, decoded_usec=15)
        teardown = run_correlation(records)["faults"][0]["vmm_teardown"]
        self.assertEqual(teardown["outcome"], "AFTER_VMM_PUT_DONE")
        self.assertTrue(teardown["candidate"]["numeric_target_range_match"])
        self.assertFalse(teardown["candidate"]["causal_ownership_proven"])

    def test_fault_before_first_pte_unmap_is_reported(self):
        records = operation_prefix() + [
            pte_row(2, "PTE_UNMAP_BEGIN", 18),
            pte_row(3, "PTE_WRITE_DONE", 19),
            vmm_row(4, "VMA_RANGE_RELEASE", 20),
            vmm_row(5, "VMM_PUT_DONE", 21),
        ] + fault_pair(raw_usec=14, decoded_usec=15)
        teardown = run_correlation(records)["faults"][0]["vmm_teardown"]
        self.assertEqual(teardown["outcome"], "BEFORE_PTE_UNMAP")

    def test_no_tracked_target_range_match_is_not_attributed(self):
        records = operation_prefix() + [
            vmm_row(2, "VMA_RANGE_RELEASE", 11),
            vmm_row(3, "VMM_PUT_DONE", 12),
        ] + fault_pair(va=0x9000, raw_usec=14, decoded_usec=15)
        teardown = run_correlation(records)["faults"][0]["vmm_teardown"]
        self.assertEqual(teardown["outcome"], "NO_TRACKED_TARGET_TEARDOWN")
        self.assertFalse(teardown["numeric_target_range_match"])

    def test_future_teardown_does_not_explain_prior_fault(self):
        records = [
            vmm_row(1, "VMM_PUT_BEGIN", 20),
            vmm_row(2, "VMA_RANGE_RELEASE", 21),
            vmm_row(3, "VMM_PUT_DONE", 22),
        ] + fault_pair(raw_usec=14, decoded_usec=15)
        teardown = run_correlation(records)["faults"][0]["vmm_teardown"]
        self.assertEqual(teardown["outcome"], "NO_TRACKED_TARGET_TEARDOWN")
        self.assertTrue(teardown["teardown_started_after_fault"])

    def test_same_microsecond_lifecycle_fault_order_is_inconclusive(self):
        records = [vmm_row(1, "VMM_PUT_BEGIN", 14)] + fault_pair(
            raw_usec=14, decoded_usec=16,
        )
        result = run_correlation(records)["faults"][0]
        self.assertEqual(result["outcome"], "INCONCLUSIVE_TIMESTAMP_COLLISION")
        self.assertEqual(result["vmm_teardown"]["outcome"], "TIMESTAMP_INCONCLUSIVE")

    def test_reset_generation_and_pt_range_fields_are_preserved(self):
        records = [
            reset_row(1, 1, "BEGIN", 2),
            reset_row(2, 1, "END", 3),
            reset_row(3, 2, "BEGIN", 4),
            reset_row(4, 2, "END", 5),
            vmm_row(5, "VMM_PUT_BEGIN", 10, map_reset_gen=1, current_reset_gen=2),
            pte_row(6, "PTE_UNMAP_BEGIN", 11, map_reset_gen=1, current_reset_gen=2),
            pte_row(7, "PTE_WRITE_DONE", 12, map_reset_gen=1, current_reset_gen=2),
            vmm_row(8, "VMA_RANGE_RELEASE", 13, map_reset_gen=1, current_reset_gen=2),
            vmm_row(9, "VMM_PUT_DONE", 14, map_reset_gen=1, current_reset_gen=2),
        ] + fault_pair(raw_usec=16, decoded_usec=17)
        teardown = run_correlation(records)["faults"][0]["vmm_teardown"]
        self.assertEqual(teardown["outcome"], "AFTER_VMM_PUT_DONE")
        self.assertEqual(teardown["candidate"]["map_reset_generation"], 1)
        self.assertEqual(teardown["candidate"]["current_reset_generation_at_fault"], 2)
        self.assertEqual(teardown["candidate"]["pte_ranges_containing_fault_va"][0]["pte_shift"], 12)

    def test_pte_range_upper_bound_is_exclusive_within_target_vma(self):
        records = operation_prefix() + [
            pte_row(2, "PTE_UNMAP_BEGIN", 11),
            pte_row(3, "PTE_WRITE_DONE", 12),
            flush_row(4, "VMM_FLUSH_BEGIN", 13),
            flush_row(5, "VMM_FLUSH_DONE", 14),
            vmm_row(6, "VMA_RANGE_RELEASE", 20),
            vmm_row(7, "VMM_PUT_DONE", 21),
        ] + fault_pair(va=TARGET_VA + 0x1000, raw_usec=16, decoded_usec=17)
        candidate = run_correlation(records)["faults"][0]["vmm_teardown"]["candidate"]
        self.assertTrue(candidate["numeric_target_range_match"])
        self.assertFalse(candidate["exact_pte_range_match"])

    def test_fault_after_pte_write_without_flush_callback_is_not_mislabeled(self):
        records = operation_prefix() + [
            pte_row(2, "PTE_UNMAP_BEGIN", 11),
            pte_row(3, "PTE_WRITE_DONE", 12),
            vmm_row(4, "VMA_RANGE_RELEASE", 20),
            vmm_row(5, "VMM_PUT_DONE", 21),
        ] + fault_pair(raw_usec=14, decoded_usec=15)
        teardown = run_correlation(records)["faults"][0]["vmm_teardown"]
        self.assertEqual(teardown["outcome"], "AFTER_PTE_WRITE_BEFORE_VMA_RELEASE")

    def test_concurrent_overlapping_operations_remain_ambiguous(self):
        records = [
            vmm_row(1, "VMM_PUT_BEGIN", 10, operation_id=9),
            vmm_row(2, "VMM_PUT_BEGIN", 11, operation_id=10),
            pte_row(3, "PTE_UNMAP_BEGIN", 12, operation_id=9),
            pte_row(4, "PTE_UNMAP_BEGIN", 13, operation_id=10),
            pte_row(5, "PTE_WRITE_DONE", 16, operation_id=9),
            pte_row(6, "PTE_WRITE_DONE", 17, operation_id=10),
            vmm_row(7, "VMA_RANGE_RELEASE", 20, operation_id=9),
            vmm_row(8, "VMM_PUT_DONE", 21, operation_id=9),
            vmm_row(9, "VMA_RANGE_RELEASE", 22, operation_id=10),
            vmm_row(10, "VMM_PUT_DONE", 23, operation_id=10),
        ] + fault_pair(raw_usec=14, decoded_usec=15)
        teardown = run_correlation(records)["faults"][0]["vmm_teardown"]
        self.assertEqual(
            teardown["outcome"], "MULTIPLE_NUMERIC_TARGET_TEARDOWN_CANDIDATES",
        )
        self.assertEqual(
            {item["operation_id"] for item in teardown["candidates"]}, {9, 10},
        )

    def test_reused_target_va_by_later_operation_is_not_merged(self):
        records = [
            vmm_row(1, "VMM_PUT_BEGIN", 10, operation_id=9),
            vmm_row(2, "VMA_RANGE_RELEASE", 11, operation_id=9),
            vmm_row(3, "VMM_PUT_DONE", 12, operation_id=9),
            vmm_row(4, "VMM_PUT_BEGIN", 13, operation_id=10),
            pte_row(5, "PTE_UNMAP_BEGIN", 14, operation_id=10),
            pte_row(6, "PTE_WRITE_DONE", 18, operation_id=10),
            vmm_row(7, "VMA_RANGE_RELEASE", 20, operation_id=10),
            vmm_row(8, "VMM_PUT_DONE", 21, operation_id=10),
        ] + fault_pair(raw_usec=16, decoded_usec=17)
        teardown = run_correlation(records)["faults"][0]["vmm_teardown"]
        self.assertEqual(
            teardown["outcome"], "MULTIPLE_NUMERIC_TARGET_TEARDOWN_CANDIDATES",
        )
        self.assertEqual(
            {item["operation_id"] for item in teardown["candidates"]}, {9, 10},
        )

    def test_malformed_operation_transitions_are_rejected(self):
        cases = (
            [pte_row(1, "PTE_WRITE_DONE", 11)],
            [vmm_row(1, "VMM_PUT_BEGIN", 10, map_reset_valid=0, map_reset_gen=1)],
            operation_prefix() + [
            pte_row(2, "PTE_UNMAP_BEGIN", 11),
            pte_row(3, "PTE_WRITE_DONE", 12, pt_id=6),
            ],
            operation_prefix() + [
                vmm_row(2, "VMM_PUT_DONE", 12),
            ],
        )
        for records in cases:
            with self.subTest(records=records):
                with self.assertRaises(CorrelationInputError):
                    run_correlation(records)

    def test_mixed_boot_input_is_rejected(self):
        records = operation_prefix() + [
            {"MESSAGE": "kernel row", "_BOOT_ID": "ffeeddccbbaa99887766554433221100",
             "_TRANSPORT": "kernel", "__MONOTONIC_TIMESTAMP": "11"},
        ]
        with self.assertRaisesRegex(CorrelationInputError, "mixed boot IDs"):
            run_correlation(records)


if __name__ == "__main__":
    unittest.main()
