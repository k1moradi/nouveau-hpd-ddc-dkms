#!/usr/bin/env python3
"""CPU-only regression tests for the BAR2 clean-baseline matcher."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path


TEST_DIR = Path(__file__).resolve().parent
ROOT = TEST_DIR if (TEST_DIR / "bar2_baseline.py").is_file() else TEST_DIR.parent
MATCHER_PATH = ROOT / "bar2_baseline.py"
SAVED_LINES_PATH = ROOT / "preflight-correction.txt"
POST_REBOOT_EVIDENCE_PATH = ROOT / "evidence/POST-REBOOT-CPU-CHECK-20261003.md"
EXPECTED_FAULT_ADDRESSES = {
    "00000000003f3000",
    "00000000005d6000",
    "00000000005da000",
}
spec = importlib.util.spec_from_file_location("bar2_baseline", MATCHER_PATH)
assert spec is not None and spec.loader is not None
bar2 = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bar2
spec.loader.exec_module(bar2)


def saved_fault_lines() -> list[str]:
    text = SAVED_LINES_PATH.read_text(encoding="utf-8")
    return [
        line
        for line in text.splitlines()
        if "fifo: fault 00 [READ]" in line and "reason 02 [PTE]" in line
    ]


class Bar2BaselineTests(unittest.TestCase):
    def test_saved_three_fault_lines_match(self) -> None:
        saved = saved_fault_lines()
        self.assertEqual(len(saved), 3, "fixture must remain the three recorded events")
        addresses = {line.split(" at ", 1)[1].split(" ", 1)[0] for line in saved}
        self.assertEqual(addresses, EXPECTED_FAULT_ADDRESSES)
        self.assertEqual(len(bar2.matching_lines(saved)), 3)

    def test_saved_post_reboot_471000_fault_matches(self) -> None:
        saved = [
            line
            for line in POST_REBOOT_EVIDENCE_PATH.read_text(encoding="utf-8").splitlines()
            if "0000000000471000" in line and "reason 02 [PTE]" in line
        ]
        self.assertEqual(len(saved), 1, "fixture must retain the recorded 0x471000 event")
        self.assertEqual(len(bar2.matching_lines(saved)), 1)

    def test_literal_brackets_are_matched(self) -> None:
        sample = (
            "nouveau: fault engine 05 [BAR2] client 07 [HUB/HOST_CPU] "
            "reason 02 [PTE]"
        )
        self.assertEqual(bar2.matching_lines([sample]), [sample])

    def test_near_misses_do_not_match(self) -> None:
        prefix = "nouveau: fault 00 [READ] at 00000000003f3000 "
        near_misses = [
            prefix + "engine 04 [COPY0] client 07 [HUB/HOST_CPU] reason 02 [PTE]",
            prefix + "engine 05 [BAR2] client 06 [HOST] reason 02 [PTE]",
            prefix + "engine 05 [BAR2] client 07 [HUB/HOST_CPU] reason 01 [VM]",
            "ordinary kernel line mentioning BAR2 PTE without the signature",
        ]
        self.assertEqual(bar2.matching_lines(near_misses), [])

    def test_zero_baseline_gate_rejects_saved_faults(self) -> None:
        result = subprocess.run(
            [sys.executable, str(MATCHER_PATH), "--require-zero", str(SAVED_LINES_PATH)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("BAR2_HOST_CPU_PTE_COUNT=3", result.stdout)
        self.assertIn("BAR2_BASELINE=CONTAMINATED", result.stdout)
        for line in saved_fault_lines():
            self.assertIn(line, result.stdout)

    def test_empty_stdin_is_inconclusive(self) -> None:
        result = subprocess.run(
            [sys.executable, str(MATCHER_PATH), "--require-zero"],
            input="",
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 3)
        self.assertIn("BAR2_HOST_CPU_PTE_COUNT=0", result.stdout)
        self.assertIn("BAR2_BASELINE=UNKNOWN_EMPTY_INPUT", result.stdout)

    def test_nonmatching_nonempty_input_passes_zero_gate(self) -> None:
        result = subprocess.run(
            [sys.executable, str(MATCHER_PATH), "--require-zero"],
            input="unrelated boot log\n",
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("BAR2_HOST_CPU_PTE_COUNT=0", result.stdout)
        self.assertIn("BAR2_BASELINE=CLEAN", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
