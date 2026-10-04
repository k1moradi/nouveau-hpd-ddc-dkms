from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from correlate_bar2_lifetimes import (
    CorrelationInputError,
    correlate,
    parse_journal,
)

BOOT = "916996de-2805-4cf1-9dd1-abb2a60380f8"
OTHER_BOOT = "ffeeddccbbaa99887766554433221100"
TAG = "NOUVEAU_DIAG_BAR2_MAP "
RAW_TAG = "NOUVEAU_DIAG_BAR2_FAULT "
RESET_TAG = "NOUVEAU_DIAG_BAR2_RESET "


def map_message(
    seq: int,
    allocation_id: int,
    stage: str,
    *,
    attempt: int = 0,
    va: int = 0,
    length: int = 0,
    range_state: str = "none",
    access: str = "none",
    refs: int = 0,
    rc: int = 0,
    mem_addr: int = 0x10000000,
    mem_len: int = 0x1000,
    comm: str = "kworker_0_1",
) -> str:
    return (
        "nouveau 0000:01:00.0: instmem: " + TAG
        + f"seq={seq} id={allocation_id} stage={stage} attempt={attempt} vmm=BAR2 "
        + f"mem_addr=0x{mem_addr:x} mem_len=0x{mem_len:x} "
        + f"bar2_va=0x{va:x} bar2_len=0x{length:x} range={range_state} "
        + f"access={access} refs={refs} pid=321 comm={comm} rc={rc}"
    )


def reset_message(seq: int, reset_id: int, stage: str) -> str:
    return f"nouveau 0000:01:00.0: bar: {RESET_TAG}seq={seq} reset_id={reset_id} stage={stage}"


def raw_fault_message(va: int = 0x4000) -> str:
    return (
        "nouveau 0000:01:00.0: fifo: " + RAW_TAG
        + f"unit=05 inst=000ffbb7 valo={va & 0xffffffff:08x} "
        + f"vahi={(va >> 32):08x} type=00000742"
    )


def decoded_fault_message(va: int = 0x4000) -> str:
    return (
        "nouveau 0000:01:00.0: fifo: fault 00 [READ] "
        + f"at {va:016x} engine 05 [BAR2] client 07 [HUB/HOST_CPU] "
        + "reason 02 [PTE] on channel -1 [00ffbb7000 unknown]"
    )


def row(message: str, mono: int, boot: str = BOOT, *, real: int | None = None) -> dict[str, object]:
    result: dict[str, object] = {
        "MESSAGE": message,
        "_BOOT_ID": boot,
        "_TRANSPORT": "kernel",
        "SYSLOG_IDENTIFIER": "kernel",
        "__MONOTONIC_TIMESTAMP": str(mono),
    }
    if real is not None:
        result["__REALTIME_TIMESTAMP"] = str(real)
    return result


def active_lifecycle(
    allocation_id: int = 1,
    *,
    seq_start: int = 1,
    time_start: int = 10,
    va: int = 0x4000,
    length: int = 0x1000,
    attempt: int | None = None,
) -> list[dict[str, object]]:
    if attempt is None:
        attempt = allocation_id
    stages = [
        ("ALLOCATED", 0, 0, 0, "none", "none", 0),
        ("MAP_BEGIN", attempt, 0, 0, "none", "none", 0),
        ("MAP_VMA_RESERVED", attempt, va, length, "reserved", "none", 0),
        ("MAP_PTE_READY", attempt, va, length, "reserved", "none", 0),
        ("MAP_READY", attempt, va, length, "current", "none", 0),
        ("FIRST_MAP_ACTIVE", 0, va, length, "current", "bar2", 1),
    ]
    return [
        row(
            map_message(
                seq_start + offset,
                allocation_id,
                stage,
                attempt=current_attempt,
                va=current_va,
                length=current_len,
                range_state=range_state,
                access=access,
                refs=refs,
            ),
            time_start + offset,
        )
        for offset, (stage, current_attempt, current_va, current_len, range_state, access, refs)
        in enumerate(stages)
    ]


def fault_pair(va: int = 0x4000, mono: int = 30, *, raw_line: int | None = None) -> list[dict[str, object]]:
    del raw_line
    return [row(raw_fault_message(va), mono - 1), row(decoded_fault_message(va), mono)]


def write_journal(records: list[dict[str, object]], directory: str) -> Path:
    path = Path(directory) / "journal.jsonl"
    path.write_text("".join(json.dumps(item, sort_keys=True) + "\n" for item in records))
    return path


def read_result(records: list[dict[str, object]], directory: str) -> dict[str, object]:
    return correlate(parse_journal(write_journal(records, directory)))


class AmbientCorrelationTests(unittest.TestCase):
    def test_fault_at_inclusive_lower_bound_matches_active_range(self):
        import tempfile
        records = active_lifecycle(va=0x4000)
        records += fault_pair(va=0x4000, mono=30)
        with tempfile.TemporaryDirectory() as temporary:
            fault = read_result(records, temporary)["faults"][0]
        self.assertEqual(fault["outcome"], "ONE_NUMERIC_ACTIVE_BAR2_MAPPING_CANDIDATE")
        self.assertEqual(fault["active_candidates"][0]["allocation_id"], 1)
        self.assertFalse(fault["active_candidates"][0]["causal_ownership_proven"])

    def test_fault_at_exclusive_upper_bound_does_not_match(self):
        import tempfile
        records = active_lifecycle(va=0x4000, length=0x1000)
        records += fault_pair(va=0x5000, mono=30)
        with tempfile.TemporaryDirectory() as temporary:
            fault = read_result(records, temporary)["faults"][0]
        self.assertEqual(fault["outcome"], "NO_OBSERVED_BAR2_MAPPING_MATCH")

    def test_zero_to_one_and_one_to_zero_are_the_only_access_edges(self):
        import tempfile
        records = active_lifecycle()
        records.append(row(map_message(7, 1, "LAST_MAP_RELEASE", va=0x4000,
                                       length=0x1000, range_state="cached"), 40))
        records += fault_pair(va=0x4001, mono=50)
        with tempfile.TemporaryDirectory() as temporary:
            parsed = parse_journal(write_journal(records, temporary))
        transitions = [event for event in parsed.maps if event.stage in {
            "FIRST_MAP_ACTIVE", "LAST_MAP_RELEASE"
        }]
        self.assertEqual([(event.stage, event.refs) for event in transitions], [
            ("FIRST_MAP_ACTIVE", 1), ("LAST_MAP_RELEASE", 0)
        ])
        result = correlate(parsed)["faults"][0]
        self.assertEqual(result["outcome"], "RECENTLY_RELEASED_BAR2_RANGE")

    def test_fault_at_active_transition_timestamp_is_inconclusive(self):
        import tempfile
        records = active_lifecycle()
        records += fault_pair(va=0x4000, mono=15)
        with tempfile.TemporaryDirectory() as temporary:
            fault = read_result(records, temporary)["faults"][0]
        self.assertEqual(fault["outcome"], "INCONCLUSIVE_MAPPING_TRANSITION")

    def test_release_inside_raw_to_decoded_observation_window_is_not_recently_released(self):
        import tempfile
        records = active_lifecycle()
        records.append(row(map_message(7, 1, "LAST_MAP_RELEASE", va=0x4000,
                                       length=0x1000, range_state="cached"), 31))
        records.extend([
            row(raw_fault_message(0x4000), 30),
            row(decoded_fault_message(0x4000), 31),
        ])
        with tempfile.TemporaryDirectory() as temporary:
            result = read_result(records, temporary)["faults"][0]
        self.assertEqual(result["outcome"], "INCONCLUSIVE_MAPPING_TRANSITION")
        self.assertEqual(result["recently_released_ranges"], [])
        self.assertFalse(result["fault_event_time_exact"])

    def test_destroy_after_release_preserves_released_range(self):
        import tempfile
        records = active_lifecycle()
        records.extend([
            row(map_message(7, 1, "LAST_MAP_RELEASE", va=0x4000, length=0x1000,
                            range_state="cached"), 20),
            row(map_message(8, 1, "DESTROYING", va=0x4000, length=0x1000,
                            range_state="current"), 40),
            row(map_message(9, 1, "DESTROYED", va=0x4000, length=0x1000,
                            range_state="released"), 41),
        ])
        records += fault_pair(va=0x4000, mono=50)
        with tempfile.TemporaryDirectory() as temporary:
            result = read_result(records, temporary)["faults"][0]
        self.assertEqual(result["outcome"], "RECENTLY_RELEASED_BAR2_RANGE")
        released = result["recently_released_ranges"][0]
        self.assertEqual(released["release_stage"], "DESTROYED")
        self.assertTrue(released["vma_teardown_confirmed"])
        self.assertEqual(released["bar2_vma_state_at_record"], "released")

    def test_destroy_with_active_refs_is_rejected(self):
        import tempfile
        records = active_lifecycle()
        records.extend([
            row(map_message(7, 1, "DESTROYING", va=0x4000, length=0x1000,
                            range_state="current", refs=1, rc=-16), 40),
            row(map_message(8, 1, "DESTROYED", va=0x4000, length=0x1000,
                            range_state="released", refs=1, rc=-16), 41),
        ])
        records += fault_pair(va=0x4000, mono=50)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(CorrelationInputError):
                parse_journal(write_journal(records, temporary))

    def test_destroyed_unknown_range_remains_lifecycle_inconclusive(self):
        import tempfile
        records = active_lifecycle()
        records.extend([
            row(map_message(7, 1, "LAST_MAP_RELEASE", va=0x4000, length=0x1000,
                            range_state="cached"), 20),
            row(map_message(8, 1, "DESTROYING", va=0x4000, length=0x1000,
                            range_state="current"), 40),
            row(map_message(9, 1, "DESTROYED", va=0x4000, length=0x1000,
                            range_state="unknown", rc=-19), 41),
        ])
        records += fault_pair(va=0x4000, mono=50)
        with tempfile.TemporaryDirectory() as temporary:
            result = read_result(records, temporary)["faults"][0]
        self.assertEqual(result["outcome"], "INCONCLUSIVE_MAPPING_LIFECYCLE")

    def test_second_first_map_without_final_release_is_rejected(self):
        import tempfile
        records = active_lifecycle()
        records.append(row(map_message(7, 1, "FIRST_MAP_ACTIVE", va=0x4000,
                                       length=0x1000, range_state="current",
                                       access="bar2", refs=1), 20))
        records += fault_pair(va=0x4000, mono=30)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(CorrelationInputError, "0-to-1 map transition"):
                parse_journal(write_journal(records, temporary))

    def test_map_after_destroy_is_rejected(self):
        import tempfile
        records = [
            row(map_message(1, 1, "ALLOCATED"), 1),
            row(map_message(2, 1, "DESTROYING"), 2),
            row(map_message(3, 1, "DESTROYED"), 3),
            row(map_message(4, 1, "MAP_BEGIN", attempt=9), 4),
            row(map_message(5, 1, "MAP_FAILED", attempt=9), 5),
        ]
        records += fault_pair(mono=10)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(CorrelationInputError):
                parse_journal(write_journal(records, temporary))

    def test_duplicate_allocation_id_is_rejected(self):
        import tempfile
        records = [row(map_message(1, 1, "ALLOCATED"), 1),
                   row(map_message(2, 1, "ALLOCATED"), 2)]
        records += fault_pair(mono=10)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(CorrelationInputError):
                parse_journal(write_journal(records, temporary))

    def test_address_range_overflow_is_rejected(self):
        import tempfile
        records = [row(map_message(1, 1, "ALLOCATED"), 1),
                   row(map_message(2, 1, "MAP_BEGIN", attempt=1), 2),
                   row(map_message(3, 1, "MAP_VMA_RESERVED", attempt=1,
                                   va=(1 << 64) - 1, length=1,
                                   range_state="reserved"), 3)]
        records += fault_pair(mono=10)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(CorrelationInputError):
                parse_journal(write_journal(records, temporary))

    def test_overlapping_active_ranges_are_ambiguous(self):
        import tempfile
        records = active_lifecycle(1, seq_start=1, time_start=1, va=0x4000)
        second = active_lifecycle(2, seq_start=7, time_start=20, va=0x4000)
        records.extend(second)
        records += fault_pair(va=0x4000, mono=40)
        with tempfile.TemporaryDirectory() as temporary:
            result = read_result(records, temporary)["faults"][0]
        self.assertEqual(result["outcome"], "MULTIPLE_NUMERIC_ACTIVE_BAR2_MAPPING_CANDIDATES")
        self.assertEqual({item["allocation_id"] for item in result["active_candidates"]}, {1, 2})

    def test_released_mapping_is_not_an_active_candidate(self):
        import tempfile
        records = active_lifecycle()
        records.append(row(map_message(7, 1, "LAST_MAP_RELEASE", va=0x4000,
                                       length=0x1000, range_state="cached"), 20))
        records += fault_pair(va=0x4000, mono=30)
        with tempfile.TemporaryDirectory() as temporary:
            result = read_result(records, temporary)["faults"][0]
        self.assertEqual(result["outcome"], "RECENTLY_RELEASED_BAR2_RANGE")
        self.assertEqual(result["active_candidates"], [])
        released = result["recently_released_ranges"][0]
        self.assertFalse(released["map_ref_active_at_fault"])
        self.assertFalse(released["vma_teardown_confirmed"])
        self.assertEqual(released["bar2_vma_state_at_record"], "cached_in_lru")

    def test_reused_va_selects_only_new_active_allocation(self):
        import tempfile
        old = active_lifecycle(1, seq_start=1, time_start=1, va=0x4000)
        old.extend([
            row(map_message(7, 1, "LAST_MAP_RELEASE", va=0x4000, length=0x1000,
                            range_state="cached"), 10),
            row(map_message(8, 1, "EVICTING", va=0x4000, length=0x1000,
                            range_state="current"), 11),
            row(map_message(9, 1, "EVICTED", va=0x4000, length=0x1000,
                            range_state="released"), 12),
        ])
        new = active_lifecycle(2, seq_start=10, time_start=20, va=0x4000)
        records = old + new + fault_pair(va=0x4000, mono=40)
        with tempfile.TemporaryDirectory() as temporary:
            result = read_result(records, temporary)["faults"][0]
        self.assertEqual(result["outcome"], "ONE_NUMERIC_ACTIVE_BAR2_MAPPING_CANDIDATE")
        self.assertEqual([item["allocation_id"] for item in result["active_candidates"]], [2])

    def test_mixed_boot_input_is_rejected(self):
        import tempfile
        records = [row(map_message(1, 1, "ALLOCATED"), 1),
                   row("unrelated record", 2, OTHER_BOOT)]
        records += fault_pair(mono=10)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(CorrelationInputError, "mixed boot"):
                parse_journal(write_journal(records, temporary))

    def test_duplicate_and_unknown_map_fields_are_rejected(self):
        import tempfile
        message = map_message(1, 1, "ALLOCATED") + " id=1"
        records = [row(message, 1)] + fault_pair(mono=10)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(CorrelationInputError):
                parse_journal(write_journal(records, temporary))

    def test_raw_fault_unknown_field_is_rejected(self):
        import tempfile
        records = [row(raw_fault_message() + " extra=1", 9),
                   row(decoded_fault_message(), 10)]
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(CorrelationInputError, "fields mismatch"):
                parse_journal(write_journal(records, temporary))

    def test_raw_fault_register_overflow_is_rejected(self):
        import tempfile
        message = (
            "nouveau: " + RAW_TAG
            + "unit=100000000 inst=000ffbb7 valo=00400000 vahi=00000000 type=742"
        )
        records = [row(message, 9), row(decoded_fault_message(), 10)]
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(CorrelationInputError, "out of range"):
                parse_journal(write_journal(records, temporary))
        records[0] = row(map_message(1, 1, "ALLOCATED") + " mystery=x", 1)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(CorrelationInputError):
                parse_journal(write_journal(records, temporary))

    def test_unknown_stage_is_rejected(self):
        import tempfile
        records = [row(map_message(1, 1, "ALIEN_STAGE"), 1)] + fault_pair(mono=10)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(CorrelationInputError):
                parse_journal(write_journal(records, temporary))

    def test_missing_monotonic_timestamp_fails_without_realtime_fallback(self):
        import tempfile
        record = row("unrelated record", 1)
        del record["__MONOTONIC_TIMESTAMP"]
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(CorrelationInputError, "refusing realtime fallback"):
                parse_journal(write_journal([record], temporary))

    def test_reset_survival_is_reported_without_causality(self):
        import tempfile
        records = active_lifecycle()
        records.extend([
            row(reset_message(7, 1, "BEGIN"), 25),
            row(reset_message(8, 1, "END"), 26),
        ])
        records += fault_pair(va=0x4000, mono=30)
        with tempfile.TemporaryDirectory() as temporary:
            result = read_result(records, temporary)["faults"][0]
        candidate = result["active_candidates"][0]
        self.assertEqual(result["outcome"], "ONE_NUMERIC_ACTIVE_BAR2_MAPPING_CANDIDATE")
        self.assertEqual(candidate["active_mapping_spanned_completed_bar2_reset_ids"], [1])
        self.assertFalse(candidate["causal_ownership_proven"])

    def test_fault_during_reset_is_inconclusive(self):
        import tempfile
        records = active_lifecycle()
        records.extend([
            row(reset_message(7, 1, "BEGIN"), 25),
            row(reset_message(8, 1, "END"), 40),
        ])
        records += fault_pair(va=0x4000, mono=30)
        with tempfile.TemporaryDirectory() as temporary:
            result = read_result(records, temporary)["faults"][0]
        self.assertEqual(result["outcome"], "INCONCLUSIVE_MAPPING_TRANSITION")

    def test_map_setup_window_is_inconclusive(self):
        import tempfile
        records = [
            row(map_message(1, 1, "ALLOCATED"), 1),
            row(map_message(2, 1, "MAP_BEGIN", attempt=1), 2),
        ] + fault_pair(va=0x4000, mono=3)
        with tempfile.TemporaryDirectory() as temporary:
            result = read_result(records, temporary)["faults"][0]
        self.assertEqual(result["outcome"], "INCONCLUSIVE_MAPPING_TRANSITION")

    def test_map_attempt_id_reuse_across_objects_is_rejected(self):
        import tempfile
        records = [
            row(map_message(1, 1, "ALLOCATED"), 1),
            row(map_message(2, 1, "MAP_BEGIN", attempt=7), 2),
            row(map_message(3, 2, "ALLOCATED"), 3),
            row(map_message(4, 2, "MAP_BEGIN", attempt=7), 4),
        ] + fault_pair(mono=10)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(CorrelationInputError, "reused across allocations"):
                parse_journal(write_journal(records, temporary))

    def test_no_mapping_records_is_only_no_observed_match(self):
        import tempfile
        records = fault_pair(va=0x4000, mono=10)
        with tempfile.TemporaryDirectory() as temporary:
            result = read_result(records, temporary)
        self.assertEqual(result["faults"][0]["outcome"], "NO_OBSERVED_BAR2_MAPPING_MATCH")
        self.assertFalse(result["input_completeness_proven"])

    def test_same_fault_address_across_separate_lifetimes_uses_active_id(self):
        import tempfile
        old = active_lifecycle(1, seq_start=1, time_start=1, va=0x4000)
        old.extend([
            row(map_message(7, 1, "LAST_MAP_RELEASE", va=0x4000, length=0x1000,
                            range_state="cached"), 10),
            row(map_message(8, 1, "EVICTING", va=0x4000, length=0x1000,
                            range_state="current"), 11),
            row(map_message(9, 1, "EVICTED", va=0x4000, length=0x1000,
                            range_state="released"), 12),
        ])
        current = active_lifecycle(2, seq_start=10, time_start=20, va=0x4000)
        records = old + current + fault_pair(va=0x4000, mono=40)
        with tempfile.TemporaryDirectory() as temporary:
            result = read_result(records, temporary)["faults"][0]
        self.assertEqual([c["allocation_id"] for c in result["active_candidates"]], [2])
        self.assertTrue(result["recently_released_ranges"])
