#!/usr/bin/env python3
"""Count Nouveau BAR2/HOST_CPU PTE faults in journal text or stdin.

Exit status 2 means a required-zero scan found a fault; 3 means no input was
available, so cleanliness could not be established. This tool parses supplied
text; it does not query the journal itself. The pattern deliberately uses one
Python-regex escape per literal bracket.
"""

from __future__ import annotations

import argparse
from itertools import chain
import re
import sys
from pathlib import Path
from typing import Iterable


BAR2_HOST_CPU_PTE = re.compile(
    r"engine 05 \[BAR2\].*client 07 \[HUB/HOST_CPU\].*reason 02 \[PTE\]"
)


def matching_lines(lines: Iterable[str]) -> list[str]:
    return [line.rstrip("\n") for line in lines if BAR2_HOST_CPU_PTE.search(line)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", nargs="?", type=Path, help="log file (default: stdin)")
    parser.add_argument(
        "--require-zero",
        action="store_true",
        help="return status 2 when the baseline contains any matching fault",
    )
    args = parser.parse_args(argv)

    if args.log is None:
        lines = sys.stdin
    else:
        try:
            lines = args.log.open("r", encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"BAR2_MATCHER_ERROR {exc}", file=sys.stderr)
            return 3

    try:
        nonblank_lines = (line for line in lines if line.strip())
        first_line = next(nonblank_lines, None)
        if first_line is None:
            print("BAR2_HOST_CPU_PTE_COUNT=0")
            print("BAR2_BASELINE=UNKNOWN_EMPTY_INPUT")
            return 3
        matches = matching_lines(chain((first_line,), nonblank_lines))
    finally:
        if args.log is not None:
            lines.close()

    print(f"BAR2_HOST_CPU_PTE_COUNT={len(matches)}")
    for line in matches:
        print(line)

    if args.require_zero and matches:
        print("BAR2_BASELINE=CONTAMINATED")
        return 2
    print("BAR2_BASELINE=CLEAN" if not matches else "BAR2_BASELINE=FAULTS_PRESENT")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
