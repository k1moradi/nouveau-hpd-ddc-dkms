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
    vma_state: str | None = None,
    access: str = "none",
    refs: int = 0,
    rc: int = 0,
    mem_addr: int = 0x10000000,
    mem_len: int = 0x1000,
    comm: str = "kworker_0_1",
    map_source: str | None = None,
    map_reset_gen: int | None = None,
    current_reset_gen: int = 0,
    cache_state: str | None = None,
) -> str:
    if vma_state is None:
        vma_state = {
            "ALLOCATED": "not_established",
            "MAP_BEGIN": "not_established",
            "MAP_VMA_RESERVED": "establishing",
            "MAP_VMM_MAP_OK": "establishing",
            "MAP_READY": "resident",
            "KMAP_ACTIVE": "resident" if access == "bar2" else "not_established",
            "KMAP_LAST_RELEASE": "resident" if length else "not_established",
            "BOOT_MAP_PINNED": "resident",
            "VMA_EVICTING": "resident",
            "VMA_EVICTED": "evicted",
            "OBJECT_DESTROYING": "resident" if length else "not_established",
            "OBJECT_IOUNMAP_BEGIN": "resident",
            "OBJECT_IOUNMAP_DONE": "resident",
            "OBJECT_VMM_PUT_BEGIN": "resident",
            "OBJECT_VMM_PUT_DONE": "destroyed",
            "OBJECT_VMM_PUT_SKIPPED": "unknown",
            "OBJECT_DESTROYED": "destroyed" if length else "not_established",
        }.get(stage, "not_established")
    if cache_state is None:
        cache_state = {
            "KMAP_ACTIVE": "none" if access == "bar0" else "active",
            "KMAP_LAST_RELEASE": "lru" if vma_state == "resident" else "none",
            "BOOT_MAP_PINNED": "pinned",
            "VMA_EVICTING": "lru",
        }.get(stage, "none")
    if map_source is None:
        map_source = (
            "new" if stage in {"MAP_BEGIN", "MAP_VMA_RESERVED", "MAP_VMM_MAP_OK",
                               "MAP_READY", "MAP_FAILED", "MAP_DISCARDED",
                               "MAP_ROLLBACK_BEGIN", "MAP_ROLLBACK_DONE"}
            else "new" if stage in {"KMAP_ACTIVE", "BOOT_MAP_PINNED"} and access == "bar2"
            else "none"
        )
    if map_reset_gen is None and vma_state in {"resident", "evicted", "destroyed"}:
        map_reset_gen = current_reset_gen
    map_generation = "none" if map_reset_gen is None else str(map_reset_gen)
    return (
        "nouveau 0000:01:00.0: instmem: " + TAG
        + f"seq={seq} id={allocation_id} stage={stage} attempt={attempt} vmm=BAR2 "
        + f"mem_addr=0x{mem_addr:x} mem_len=0x{mem_len:x} "
        + f"bar2_va=0x{va:x} bar2_len=0x{length:x} vma_state={vma_state} "
        + f"cache_state={cache_state} access={access} map_source={map_source} "
        + f"map_reset_gen={map_generation} current_reset_gen={current_reset_gen} "
        + f"refs={refs} pid=321 comm={comm} rc={rc}"
    )


def reset_message(seq: int, reset_id: int, stage: str) -> str:
    return f"nouveau 0000:01:00.0: bar: {RESET_TAG}seq={seq} gen={reset_id} stage={stage}"


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


def row(
    message: str,
    mono: int,
    boot: str = BOOT,
    *,
    real: int | None = None,
    ring_ns: int | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "MESSAGE": message,
        "_BOOT_ID": boot,
        "_TRANSPORT": "kernel",
        "SYSLOG_IDENTIFIER": "kernel",
        "__MONOTONIC_TIMESTAMP": str(mono),
    }
    if real is not None:
        result["__REALTIME_TIMESTAMP"] = str(real)
    if ring_ns is not None:
        result["NOUVEAU_DIAG_RING_MONO_NS"] = str(ring_ns)
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
        ("ALLOCATED", 0, 0, 0, "not_established", "none", 0),
        ("MAP_BEGIN", attempt, 0, 0, "not_established", "none", 0),
        ("MAP_VMA_RESERVED", attempt, va, length, "establishing", "none", 0),
        ("MAP_VMM_MAP_OK", attempt, va, length, "establishing", "none", 0),
        ("MAP_READY", attempt, va, length, "resident", "none", 0),
        ("KMAP_ACTIVE", 0, va, length, "resident", "bar2", 1),
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
                vma_state=stage_state,
                access=access,
                refs=refs,
                map_reset_gen=(
                    0 if stage == "MAP_VMM_MAP_OK" else None
                ),
            ),
            time_start + offset,
        )
        for offset, (stage, current_attempt, current_va, current_len, stage_state, access, refs)
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
        self.assertEqual(fault["outcome"], "ONE_ACTIVE_KMAP_RESIDENT_VMA_CANDIDATE")
        self.assertEqual(fault["active_candidates"][0]["allocation_id"], 1)
        self.assertFalse(fault["active_candidates"][0]["causal_ownership_proven"])
        candidate = fault["active_candidates"][0]
        self.assertEqual(candidate["kmap_ref_state_at_fault"], "NONZERO")
        self.assertIsNone(candidate["exact_kmap_refcount_at_fault"])
        self.assertEqual(candidate["last_kmap_ref_edge"]["refcount_at_edge"], 1)

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
        records.append(row(map_message(7, 1, "KMAP_LAST_RELEASE", va=0x4000,
                                       length=0x1000, vma_state="resident", cache_state="lru"), 40))
        records += fault_pair(va=0x4001, mono=50)
        with tempfile.TemporaryDirectory() as temporary:
            parsed = parse_journal(write_journal(records, temporary))
        transitions = [event for event in parsed.maps if event.stage in {
            "KMAP_ACTIVE", "KMAP_LAST_RELEASE"
        }]
        self.assertEqual([(event.stage, event.refs) for event in transitions], [
            ("KMAP_ACTIVE", 1), ("KMAP_LAST_RELEASE", 0)
        ])
        result = correlate(parsed)["faults"][0]
        self.assertEqual(result["outcome"], "ONE_ZERO_REF_CACHED_VMA_CANDIDATE")
        candidate = result["zero_ref_candidates"][0]
        self.assertEqual(candidate["kmap_ref_state_at_fault"], "ZERO")
        self.assertEqual(candidate["exact_kmap_refcount_at_fault"], 0)
        self.assertEqual(candidate["vma_state"], "resident")
        self.assertEqual(candidate["cache_state"], "lru")

    def test_bar0_kmap_edges_do_not_require_a_bar2_cache(self):
        import tempfile
        records = [
            row(map_message(1, 1, "ALLOCATED"), 10),
            row(map_message(2, 1, "KMAP_ACTIVE", access="bar0", refs=1), 11),
            row(map_message(3, 1, "KMAP_LAST_RELEASE", refs=0), 12),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            parsed = parse_journal(write_journal(records, temporary))
        self.assertEqual(parsed.maps[1].access, "bar0")
        self.assertEqual(parsed.maps[1].cache_state, "none")
        self.assertEqual(parsed.maps[1].vma_state, "not_established")
        self.assertEqual(correlate(parsed)["fault_count"], 0)

    def test_bar0_kmap_without_vma_rejects_false_active_cache(self):
        import tempfile
        records = [
            row(map_message(1, 1, "ALLOCATED"), 10),
            row(map_message(
                2, 1, "KMAP_ACTIVE", access="bar0", refs=1,
                cache_state="active",
            ), 11),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(CorrelationInputError, "unmapped BAR0 kmap"):
                parse_journal(write_journal(records, temporary))

    def test_fault_at_active_transition_timestamp_is_inconclusive(self):
        import tempfile
        records = active_lifecycle()
        records += fault_pair(va=0x4000, mono=15)
        with tempfile.TemporaryDirectory() as temporary:
            fault = read_result(records, temporary)["faults"][0]
        self.assertEqual(fault["outcome"], "INCONCLUSIVE_VMA_TRANSITION")

    def test_ring_lifecycle_and_fault_same_microsecond_are_inconclusive(self):
        import tempfile
        records = active_lifecycle()
        records[-1]["__MONOTONIC_TIMESTAMP"] = "29"
        records[-1]["NOUVEAU_DIAG_RING_MONO_NS"] = "29001"
        records += fault_pair(va=0x4000, mono=30)
        with tempfile.TemporaryDirectory() as temporary:
            fault = read_result(records, temporary)["faults"][0]
        self.assertEqual(fault["outcome"], "INCONCLUSIVE_TIMESTAMP_COLLISION")
        self.assertEqual(fault["timestamp_collision_records"][0]["ring_monotonic_ns"], 29001)
        self.assertEqual(fault["active_candidates"], [])
        self.assertFalse(fault["causal_ownership_proven"])

    def test_ring_nanoseconds_must_agree_with_journal_microseconds(self):
        import tempfile
        records = active_lifecycle()
        records[-1]["NOUVEAU_DIAG_RING_MONO_NS"] = "16999"
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(CorrelationInputError, "disagree with journal microseconds"):
                parse_journal(write_journal(records, temporary))

    def test_release_after_raw_fault_does_not_retroactively_change_fault_state(self):
        import tempfile
        records = active_lifecycle()
        records.append(row(map_message(7, 1, "KMAP_LAST_RELEASE", va=0x4000,
                                       length=0x1000, vma_state="resident", cache_state="lru"), 31))
        records.extend([
            row(raw_fault_message(0x4000), 30),
            row(decoded_fault_message(0x4000), 31),
        ])
        with tempfile.TemporaryDirectory() as temporary:
            result = read_result(records, temporary)["faults"][0]
        self.assertEqual(result["outcome"], "ONE_ACTIVE_KMAP_RESIDENT_VMA_CANDIDATE")
        self.assertEqual(result["recently_evicted_ranges"], [])
        self.assertTrue(result["fault"]["fault_monotonic_usec"] > 0)

    def test_destroy_after_release_preserves_released_range(self):
        import tempfile
        records = active_lifecycle()
        records.extend([
            row(map_message(7, 1, "KMAP_LAST_RELEASE", va=0x4000, length=0x1000,
                            vma_state="resident", cache_state="lru"), 20),
            row(map_message(8, 1, "OBJECT_DESTROYING", va=0x4000, length=0x1000,
                            vma_state="resident"), 40),
            row(map_message(9, 1, "OBJECT_DESTROYED", va=0x4000, length=0x1000,
                            vma_state="destroyed"), 41),
        ])
        records += fault_pair(va=0x4000, mono=50)
        with tempfile.TemporaryDirectory() as temporary:
            result = read_result(records, temporary)["faults"][0]
        self.assertEqual(result["outcome"], "RECENTLY_EVICTED_VMA_RANGE")
        released = result["recently_evicted_ranges"][0]
        self.assertEqual(released["terminal_stage"], "OBJECT_DESTROYED")
        self.assertFalse(released["active_at_fault"])

    def test_fault_during_vmm_put_reports_exact_destruction_phase(self):
        import tempfile
        records = active_lifecycle()
        records.extend([
            row(map_message(7, 1, "KMAP_LAST_RELEASE", va=0x4000, length=0x1000,
                            vma_state="resident", cache_state="lru"), 20),
            row(map_message(8, 1, "OBJECT_DESTROYING", va=0x4000, length=0x1000,
                            vma_state="resident"), 40),
            row(map_message(9, 1, "OBJECT_IOUNMAP_BEGIN", va=0x4000, length=0x1000,
                            vma_state="resident"), 41),
            row(map_message(10, 1, "OBJECT_IOUNMAP_DONE", va=0x4000, length=0x1000,
                            vma_state="resident"), 42),
            row(map_message(11, 1, "OBJECT_VMM_PUT_BEGIN", va=0x4000, length=0x1000,
                            vma_state="resident"), 43),
            row(map_message(12, 1, "OBJECT_VMM_PUT_DONE", va=0x4000, length=0x1000,
                            vma_state="destroyed"), 45),
            row(map_message(13, 1, "OBJECT_DESTROYED", va=0x4000, length=0x1000,
                            vma_state="destroyed"), 46),
        ])
        records += fault_pair(va=0x4000, mono=44)
        with tempfile.TemporaryDirectory() as temporary:
            result = read_result(records, temporary)["faults"][0]
        self.assertEqual(result["outcome"], "INCONCLUSIVE_VMA_TRANSITION")
        self.assertEqual(result["resident_candidates"], [])
        self.assertEqual(result["transition_records"][0]["phase"], "OBJECT_VMM_PUT_BEGIN")
        self.assertEqual(result["transition_records"][0]["next_stage"], "OBJECT_VMM_PUT_DONE")
        self.assertEqual(result["transition_records"][0]["start_usec"], 43)
        self.assertEqual(result["transition_records"][0]["end_usec"], 45)

    def test_new_teardown_phases_require_source_order(self):
        import tempfile
        records = active_lifecycle()
        records.extend([
            row(map_message(7, 1, "KMAP_LAST_RELEASE", va=0x4000, length=0x1000,
                            vma_state="resident", cache_state="lru"), 20),
            row(map_message(8, 1, "OBJECT_DESTROYING", va=0x4000, length=0x1000,
                            vma_state="resident"), 30),
            row(map_message(9, 1, "OBJECT_VMM_PUT_BEGIN", va=0x4000, length=0x1000,
                            vma_state="resident"), 31),
            row(map_message(10, 1, "OBJECT_DESTROYED", va=0x4000, length=0x1000,
                            vma_state="destroyed"), 32),
        ])
        records += fault_pair(mono=10)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(CorrelationInputError, "invalid object teardown phase order"):
                parse_journal(write_journal(records, temporary))

    def test_destroy_with_active_refs_is_reported_as_incomplete(self):
        import tempfile
        records = active_lifecycle()
        records.extend([
            row(map_message(7, 1, "OBJECT_DESTROYING", va=0x4000, length=0x1000,
                            vma_state="resident", refs=1, rc=-16), 40),
            row(map_message(8, 1, "OBJECT_DESTROYED", va=0x4000, length=0x1000,
                            vma_state="destroyed", refs=1, rc=-16), 41),
        ])
        records += fault_pair(va=0x4000, mono=50)
        with tempfile.TemporaryDirectory() as temporary:
            result = read_result(records, temporary)["faults"][0]
        self.assertEqual(result["outcome"], "INCOMPLETE_MAPPING_LIFECYCLE")
        self.assertEqual(
            result["incomplete_lifecycles"][0]["reason"],
            "object was destroyed with active kmap refs",
        )

    def test_destroyed_unknown_range_remains_lifecycle_inconclusive(self):
        import tempfile
        records = active_lifecycle()
        records.extend([
            row(map_message(7, 1, "KMAP_LAST_RELEASE", va=0x4000, length=0x1000,
                            vma_state="resident", cache_state="lru"), 20),
            row(map_message(8, 1, "OBJECT_DESTROYING", va=0x4000, length=0x1000,
                            vma_state="resident"), 40),
            row(map_message(9, 1, "OBJECT_DESTROYED", va=0x4000, length=0x1000,
                            vma_state="unknown", rc=-19), 41),
        ])
        records += fault_pair(va=0x4000, mono=50)
        with tempfile.TemporaryDirectory() as temporary:
            result = read_result(records, temporary)["faults"][0]
        self.assertEqual(result["outcome"], "INCOMPLETE_MAPPING_LIFECYCLE")

    def test_second_first_map_without_final_release_is_rejected(self):
        import tempfile
        records = active_lifecycle()
        records.append(row(map_message(7, 1, "KMAP_ACTIVE", va=0x4000,
                                       length=0x1000, vma_state="resident",
                                       access="bar2", refs=1), 20))
        records += fault_pair(va=0x4000, mono=30)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(CorrelationInputError, "zero-to-nonzero kmap edge"):
                parse_journal(write_journal(records, temporary))

    def test_map_after_destroy_is_rejected(self):
        import tempfile
        records = [
            row(map_message(1, 1, "ALLOCATED"), 1),
            row(map_message(2, 1, "OBJECT_DESTROYING"), 2),
            row(map_message(3, 1, "OBJECT_DESTROYED"), 3),
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
                                   vma_state="establishing"), 3)]
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
        self.assertEqual(result["outcome"], "MULTIPLE_RESIDENT_VMA_CANDIDATES")
        self.assertEqual({item["allocation_id"] for item in result["active_candidates"]}, {1, 2})

    def test_last_kmap_release_leaves_zero_ref_cached_vma_resident(self):
        import tempfile
        records = active_lifecycle()
        records.append(row(map_message(7, 1, "KMAP_LAST_RELEASE", va=0x4000,
                                       length=0x1000, vma_state="resident", cache_state="lru"), 20))
        records += fault_pair(va=0x4000, mono=30)
        with tempfile.TemporaryDirectory() as temporary:
            result = read_result(records, temporary)["faults"][0]
        self.assertEqual(result["outcome"], "ONE_ZERO_REF_CACHED_VMA_CANDIDATE")
        self.assertEqual(result["active_candidates"], [])
        candidate = result["zero_ref_candidates"][0]
        self.assertEqual(candidate["vma_state"], "resident")
        self.assertEqual(candidate["cache_state"], "lru")

    def test_reused_va_selects_only_new_active_allocation(self):
        import tempfile
        old = active_lifecycle(1, seq_start=1, time_start=1, va=0x4000)
        old.extend([
            row(map_message(7, 1, "KMAP_LAST_RELEASE", va=0x4000, length=0x1000,
                            vma_state="resident", cache_state="lru"), 10),
                            row(map_message(8, 1, "VMA_EVICTING", va=0x4000, length=0x1000,
                                            vma_state="resident"), 11),
            row(map_message(9, 1, "VMA_EVICTED", va=0x4000, length=0x1000,
                            vma_state="evicted"), 12),
        ])
        new = active_lifecycle(2, seq_start=10, time_start=20, va=0x4000)
        records = old + new + fault_pair(va=0x4000, mono=40)
        with tempfile.TemporaryDirectory() as temporary:
            result = read_result(records, temporary)["faults"][0]
        self.assertEqual(result["outcome"], "ONE_ACTIVE_KMAP_RESIDENT_VMA_CANDIDATE")
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
        self.assertEqual(result["outcome"], "ONE_RESET_SPANNING_ACTIVE_VMA_CANDIDATE")
        self.assertTrue(candidate["mapping_spans_reset"])
        self.assertEqual(candidate["current_reset_gen_at_fault"], 1)
        self.assertEqual(candidate["kmap_ref_state_at_fault"], "NONZERO")
        self.assertFalse(candidate["reused_from_cache_after_reset"])
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
        self.assertEqual(result["outcome"], "INCONCLUSIVE_VMA_TRANSITION")

    def test_map_setup_window_is_inconclusive(self):
        import tempfile
        records = [
            row(map_message(1, 1, "ALLOCATED"), 1),
            row(map_message(2, 1, "MAP_BEGIN", attempt=1), 2),
        ] + fault_pair(va=0x4000, mono=3)
        with tempfile.TemporaryDirectory() as temporary:
            result = read_result(records, temporary)["faults"][0]
        self.assertEqual(result["outcome"], "INCONCLUSIVE_VMA_TRANSITION")

    def test_map_attempt_id_reuse_across_objects_is_rejected(self):
        import tempfile
        records = [
            row(map_message(1, 1, "ALLOCATED"), 1),
            row(map_message(2, 1, "MAP_BEGIN", attempt=7), 2),
            row(map_message(3, 2, "ALLOCATED"), 3),
            row(map_message(4, 2, "MAP_BEGIN", attempt=7), 4),
        ] + fault_pair(mono=10)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(CorrelationInputError, "reused by another allocation"):
                parse_journal(write_journal(records, temporary))

    def test_no_mapping_records_is_only_no_observed_match(self):
        import tempfile
        records = fault_pair(va=0x4000, mono=10)
        with tempfile.TemporaryDirectory() as temporary:
            result = read_result(records, temporary)
        self.assertEqual(result["faults"][0]["outcome"], "NO_OBSERVED_BAR2_MAPPING_MATCH")
        self.assertFalse(result["input_completeness_proven"])
        self.assertEqual(
            result["module_load_epoch_policy"],
            "single_nouveau_module_instance_per_boot",
        )

    def test_same_fault_address_across_separate_lifetimes_uses_active_id(self):
        import tempfile
        old = active_lifecycle(1, seq_start=1, time_start=1, va=0x4000)
        old.extend([
            row(map_message(7, 1, "KMAP_LAST_RELEASE", va=0x4000, length=0x1000,
                            vma_state="resident", cache_state="lru"), 10),
            row(map_message(8, 1, "VMA_EVICTING", va=0x4000, length=0x1000,
                            vma_state="resident"), 11),
            row(map_message(9, 1, "VMA_EVICTED", va=0x4000, length=0x1000,
                            vma_state="evicted"), 12),
        ])
        current = active_lifecycle(2, seq_start=10, time_start=20, va=0x4000)
        records = old + current + fault_pair(va=0x4000, mono=40)
        with tempfile.TemporaryDirectory() as temporary:
            result = read_result(records, temporary)["faults"][0]
        self.assertEqual([c["allocation_id"] for c in result["active_candidates"]], [2])
        self.assertTrue(result["recently_evicted_ranges"])

    def test_reset_spanning_cached_mapping_records_cache_reuse(self):
        import tempfile
        records = active_lifecycle()
        records.append(row(map_message(
            7, 1, "KMAP_LAST_RELEASE", va=0x4000, length=0x1000,
            vma_state="resident", cache_state="lru", map_reset_gen=0,
            current_reset_gen=0,
        ), 20))
        records.extend([
            row(reset_message(8, 1, "BEGIN"), 25),
            row(reset_message(9, 1, "END"), 26),
            row(map_message(
                10, 1, "KMAP_ACTIVE", va=0x4000, length=0x1000,
                vma_state="resident", cache_state="active", access="bar2",
                map_source="cached", map_reset_gen=0, current_reset_gen=1, refs=1,
            ), 27),
            row(map_message(
                11, 1, "KMAP_LAST_RELEASE", va=0x4000, length=0x1000,
                vma_state="resident", cache_state="lru", map_reset_gen=0,
                current_reset_gen=1,
            ), 28),
        ])
        records += fault_pair(va=0x4000, mono=40)
        with tempfile.TemporaryDirectory() as temporary:
            result = read_result(records, temporary)["faults"][0]
        candidate = result["zero_ref_candidates"][0]
        self.assertEqual(result["outcome"], "ONE_RESET_SPANNING_CACHED_VMA_CANDIDATE")
        self.assertTrue(candidate["mapping_spans_reset"])
        self.assertTrue(candidate["reused_from_cache_after_reset"])
        self.assertEqual(candidate["map_reset_gen"], 0)
        self.assertEqual(candidate["current_reset_gen_at_fault"], 1)

    def test_zero_ref_cached_mapping_spanning_reset_without_reuse_is_distinguished(self):
        import tempfile
        records = active_lifecycle()
        records.append(row(map_message(
            7, 1, "KMAP_LAST_RELEASE", va=0x4000, length=0x1000,
            vma_state="resident", cache_state="lru", map_reset_gen=0,
            current_reset_gen=0,
        ), 20))
        records.extend([
            row(reset_message(8, 1, "BEGIN"), 25),
            row(reset_message(9, 1, "END"), 26),
        ])
        records += fault_pair(va=0x4000, mono=40)
        with tempfile.TemporaryDirectory() as temporary:
            result = read_result(records, temporary)["faults"][0]
        candidate = result["zero_ref_candidates"][0]
        self.assertEqual(result["outcome"], "ONE_RESET_SPANNING_CACHED_VMA_CANDIDATE")
        self.assertTrue(candidate["mapping_spans_reset"])
        self.assertFalse(candidate["reused_from_cache_after_reset"])

    def test_evicted_mapping_is_not_active_candidate(self):
        import tempfile
        records = active_lifecycle()
        records.extend([
            row(map_message(
                7, 1, "KMAP_LAST_RELEASE", va=0x4000, length=0x1000,
                vma_state="resident", cache_state="lru", map_reset_gen=0,
            ), 20),
            row(map_message(
                8, 1, "VMA_EVICTING", va=0x4000, length=0x1000,
                vma_state="resident", cache_state="lru", map_reset_gen=0,
            ), 21),
            row(map_message(
                9, 1, "VMA_EVICTED", va=0x4000, length=0x1000,
                vma_state="evicted", cache_state="none", map_reset_gen=0,
            ), 22),
        ])
        records += fault_pair(va=0x4001, mono=30)
        with tempfile.TemporaryDirectory() as temporary:
            result = read_result(records, temporary)["faults"][0]
        self.assertEqual(result["outcome"], "RECENTLY_EVICTED_VMA_RANGE")
        self.assertEqual(result["active_candidates"], [])
        self.assertEqual(result["zero_ref_candidates"], [])
        self.assertEqual(result["recently_evicted_ranges"][0]["allocation_id"], 1)

    def test_same_va_reused_after_reset_gets_new_mapping_generation(self):
        import tempfile
        records = active_lifecycle()
        records.extend([
            row(map_message(
                7, 1, "KMAP_LAST_RELEASE", va=0x4000, length=0x1000,
                vma_state="resident", cache_state="lru", map_reset_gen=0,
            ), 20),
            row(map_message(
                8, 1, "VMA_EVICTING", va=0x4000, length=0x1000,
                vma_state="resident", cache_state="lru", map_reset_gen=0,
            ), 21),
            row(map_message(
                9, 1, "VMA_EVICTED", va=0x4000, length=0x1000,
                vma_state="evicted", cache_state="none", map_reset_gen=0,
            ), 22),
            row(reset_message(10, 1, "BEGIN"), 23),
            row(reset_message(11, 1, "END"), 24),
            row(map_message(12, 1, "MAP_BEGIN", attempt=2,
                            current_reset_gen=1), 25),
            row(map_message(13, 1, "MAP_VMA_RESERVED", attempt=2,
                            va=0x4000, length=0x1000,
                            vma_state="establishing", map_source="new",
                            current_reset_gen=1), 26),
            row(map_message(14, 1, "MAP_VMM_MAP_OK", attempt=2,
                            va=0x4000, length=0x1000,
                            vma_state="establishing", map_source="new",
                            map_reset_gen=1,
                            current_reset_gen=1), 27),
            row(map_message(15, 1, "MAP_READY", attempt=2,
                            va=0x4000, length=0x1000,
                            vma_state="resident", map_source="new",
                            map_reset_gen=1, current_reset_gen=1), 28),
            row(map_message(16, 1, "KMAP_ACTIVE", va=0x4000, length=0x1000,
                            vma_state="resident", cache_state="active",
                            map_source="new", map_reset_gen=1,
                            current_reset_gen=1, access="bar2", refs=1), 29),
        ])
        records += fault_pair(va=0x4001, mono=40)
        with tempfile.TemporaryDirectory() as temporary:
            result = read_result(records, temporary)["faults"][0]
        candidate = result["active_candidates"][0]
        self.assertEqual(result["outcome"], "ONE_ACTIVE_KMAP_RESIDENT_VMA_CANDIDATE")
        self.assertEqual(candidate["allocation_id"], 1)
        self.assertEqual(candidate["map_reset_gen"], 1)
        self.assertEqual(candidate["current_reset_gen_at_fault"], 1)
        self.assertFalse(candidate["mapping_spans_reset"])

    def test_thirty_faults_same_inst_are_counted_not_merged(self):
        import tempfile
        records = []
        for index in range(30):
            va = 0x46F000 if index < 25 else 0x41F000
            mono = 100 + index * 100
            records.extend([
                row(raw_fault_message(va), mono),
                row(decoded_fault_message(va), mono + 1),
            ])
        with tempfile.TemporaryDirectory() as temporary:
            result = read_result(records, temporary)
        self.assertEqual(result["fault_count"], 30)
        self.assertEqual([item["fault_ordinal"] for item in result["faults"]], list(range(1, 31)))
        self.assertEqual(
            [item["fault"]["fault_va"] for item in result["faults"][:25]],
            ["0x46f000"] * 25,
        )
        self.assertEqual(
            [item["fault"]["fault_va"] for item in result["faults"][25:]],
            ["0x41f000"] * 5,
        )
        self.assertEqual(len({item["fault"]["inst"] for item in result["faults"]}), 1)
        self.assertTrue(all(
            item["outcome"] == "NO_OBSERVED_BAR2_MAPPING_MATCH"
            for item in result["faults"]
        ))

    def test_first_fault_and_recurrence_separate_recovery_reset(self):
        import tempfile
        records = []
        records.extend([
            row(raw_fault_message(0x46F000), 100),
            row(decoded_fault_message(0x46F000), 104),
            row(reset_message(1, 1, "BEGIN"), 102),
            row(reset_message(2, 1, "END"), 103),
            row(raw_fault_message(0x46F000), 200),
            row(decoded_fault_message(0x46F000), 201),
        ])
        with tempfile.TemporaryDirectory() as temporary:
            result = read_result(records, temporary)["faults"]
        self.assertEqual(result[0]["fault_ordinal"], 1)
        self.assertEqual(result[0]["bar2_reset_generation_at_fault"], 0)
        self.assertEqual(result[0]["post_fault_recovery_reset_generations"], [1])
        self.assertEqual(result[1]["fault_ordinal"], 2)
        self.assertEqual(result[1]["bar2_reset_generation_at_fault"], 1)
        self.assertEqual(result[1]["bar2_resets_since_previous_fault"], 1)
        self.assertEqual(result[1]["time_since_previous_fault_usec"], 100)

    def test_same_boot_module_reload_sequence_reset_is_rejected(self):
        import tempfile
        records = [
            row(map_message(1, 1, "ALLOCATED"), 1),
            row(map_message(2, 1, "MAP_BEGIN", attempt=1), 2),
            # A second load in this boot restarts the module-local counters.
            row(map_message(1, 1, "ALLOCATED"), 3),
        ] + fault_pair(mono=10)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(CorrelationInputError, "duplicate diagnostic sequence"):
                parse_journal(write_journal(records, temporary))

    def test_cached_reuse_without_previously_resident_vma_is_rejected(self):
        import tempfile
        records = [
            row(map_message(1, 1, "ALLOCATED"), 1),
            row(map_message(
                2, 1, "KMAP_ACTIVE", va=0x4000, length=0x1000,
                vma_state="resident", cache_state="active", access="bar2",
                map_source="cached", map_reset_gen=0, refs=1,
            ), 2),
        ] + fault_pair(mono=10)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(CorrelationInputError, "previously established resident VMA"):
                parse_journal(write_journal(records, temporary))

    def test_map_reset_generation_cannot_exceed_current_generation(self):
        import tempfile
        records = [
            row(map_message(1, 1, "ALLOCATED"), 1),
            row(map_message(2, 1, "MAP_BEGIN", attempt=1), 2),
            row(map_message(3, 1, "MAP_VMA_RESERVED", attempt=1, va=0x4000,
                            length=0x1000, vma_state="establishing"), 3),
            row(map_message(4, 1, "MAP_VMM_MAP_OK", attempt=1, va=0x4000,
                            length=0x1000, vma_state="establishing"), 4),
            row(map_message(5, 1, "MAP_READY", attempt=1, va=0x4000,
                            length=0x1000, vma_state="resident", map_reset_gen=1,
                            current_reset_gen=0), 5),
        ] + fault_pair(mono=10)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(CorrelationInputError, "newer than current"):
                parse_journal(write_journal(records, temporary))

    def test_vmm_success_generation_is_preserved_across_later_reset(self):
        import tempfile
        records = [
            row(map_message(1, 1, "ALLOCATED"), 1),
            row(map_message(2, 1, "MAP_BEGIN", attempt=1), 2),
            row(map_message(3, 1, "MAP_VMA_RESERVED", attempt=1,
                            va=0x4000, length=0x1000), 3),
            row(map_message(4, 1, "MAP_VMM_MAP_OK", attempt=1,
                            va=0x4000, length=0x1000,
                            map_reset_gen=0, current_reset_gen=0), 4),
            row(reset_message(5, 1, "BEGIN"), 5),
            row(reset_message(6, 1, "END"), 6),
            row(map_message(7, 1, "MAP_READY", attempt=1,
                            va=0x4000, length=0x1000,
                            map_reset_gen=0, current_reset_gen=1), 7),
            row(map_message(8, 1, "KMAP_ACTIVE", va=0x4000,
                            length=0x1000, access="bar2", refs=1,
                            map_source="new", map_reset_gen=0,
                            current_reset_gen=1), 8),
        ] + fault_pair(mono=20)
        with tempfile.TemporaryDirectory() as temporary:
            item = read_result(records, temporary)["faults"][0]
        candidate = item["resident_candidates"][0]
        self.assertEqual(item["outcome"], "ONE_RESET_SPANNING_ACTIVE_VMA_CANDIDATE")
        self.assertEqual(candidate["map_reset_gen"], 0)
        self.assertEqual(candidate["current_reset_gen_at_fault"], 1)
        self.assertTrue(candidate["mapping_spans_reset"])
        self.assertFalse(candidate["reused_from_cache_after_reset"])

    def test_map_attempt_generation_must_match_ready_vma(self):
        import tempfile
        records = [
            row(map_message(1, 1, "ALLOCATED"), 1),
            row(map_message(2, 1, "MAP_BEGIN", attempt=1), 2),
            row(map_message(3, 1, "MAP_VMA_RESERVED", attempt=1,
                            va=0x4000, length=0x1000), 3),
            row(map_message(4, 1, "MAP_VMM_MAP_OK", attempt=1,
                            va=0x4000, length=0x1000,
                            map_reset_gen=0, current_reset_gen=0), 4),
            row(map_message(5, 1, "MAP_READY", attempt=1,
                            va=0x4000, length=0x1000,
                            map_reset_gen=1, current_reset_gen=1), 5),
        ] + fault_pair(mono=20)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(CorrelationInputError, "changed its VMM establishment generation"):
                parse_journal(write_journal(records, temporary))

    def test_one_mapping_range_can_contain_multiple_fault_addresses(self):
        import tempfile
        records = active_lifecycle(va=0x4000, length=0x3000)
        records.extend([
            row(raw_fault_message(0x4001), 30),
            row(decoded_fault_message(0x4001), 31),
            row(raw_fault_message(0x6FFF), 40),
            row(decoded_fault_message(0x6FFF), 41),
        ])
        with tempfile.TemporaryDirectory() as temporary:
            result = read_result(records, temporary)["faults"]
        self.assertEqual(len(result), 2)
        self.assertTrue(all(
            item["outcome"] == "ONE_ACTIVE_KMAP_RESIDENT_VMA_CANDIDATE"
            for item in result
        ))
        self.assertEqual(
            [item["active_candidates"][0]["allocation_id"] for item in result],
            [1, 1],
        )

    def test_negative_kmap_reference_count_is_rejected(self):
        import tempfile
        message = map_message(1, 1, "ALLOCATED").replace("refs=0", "refs=-1")
        records = [row(message, 1)] + fault_pair(mono=10)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(CorrelationInputError, "out of range"):
                parse_journal(write_journal(records, temporary))

    def test_vma_eviction_without_resident_mapping_is_rejected(self):
        import tempfile
        records = [
            row(map_message(1, 1, "ALLOCATED"), 1),
            row(map_message(
                2, 1, "VMA_EVICTING", va=0x4000, length=0x1000,
                vma_state="resident", cache_state="lru", map_reset_gen=0,
            ), 2),
        ] + fault_pair(mono=10)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(CorrelationInputError, "does not match resident VMA"):
                parse_journal(write_journal(records, temporary))

    def test_double_destroy_is_rejected(self):
        import tempfile
        records = [
            row(map_message(1, 1, "ALLOCATED"), 1),
            row(map_message(2, 1, "OBJECT_DESTROYING"), 2),
            row(map_message(3, 1, "OBJECT_DESTROYED"), 3),
            row(map_message(4, 1, "OBJECT_DESTROYING"), 4),
        ] + fault_pair(mono=10)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(CorrelationInputError, "continues after OBJECT_DESTROYED"):
                parse_journal(write_journal(records, temporary))

    def test_reset_generation_decrease_is_rejected(self):
        import tempfile
        records = [
            row(reset_message(1, 1, "BEGIN"), 1),
            row(reset_message(2, 1, "END"), 2),
            row(reset_message(3, 1, "BEGIN"), 3),
            row(reset_message(4, 1, "END"), 4),
        ] + fault_pair(mono=10)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(CorrelationInputError, "generation is not monotonic"):
                parse_journal(write_journal(records, temporary))
