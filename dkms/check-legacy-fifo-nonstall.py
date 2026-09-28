#!/usr/bin/env python3
"""Fail-closed detector for Nouveau's legacy FIFO nonstall event index."""

from pathlib import Path
import re
import sys


def strip_comments(text: str) -> str:
    pattern = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)
    return pattern.sub(lambda match: "\n" * match.group(0).count("\n"), text)


def matching_brace(text: str, opening: int) -> int | None:
    depth = 0
    state = "code"
    escaped = False
    index = opening

    while index < len(text):
        char = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""

        if state in ("string", "char"):
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif (state == "string" and char == '"') or (
                state == "char" and char == "'"
            ):
                state = "code"
        elif char == '"':
            state = "string"
        elif char == "'":
            state = "char"
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index

        index += 1

    return None


def extract_function(source: str) -> str | None:
    source = strip_comments(source)
    match = re.search(
        r"\bstatic\s+int\s+nvkm_uchan_uevent\s*\([^;{}]*\)\s*\{",
        source,
        re.DOTALL,
    )
    if not match:
        return None

    opening = source.find("{", match.start(), match.end())
    closing = matching_brace(source, opening)
    if closing is None:
        return None
    return source[opening + 1 : closing]


def classify(body: str) -> str:
    calls = re.findall(
        r"case\s+NVIF_CHAN_EVENT_V0_NON_STALL_INTR\s*:\s*"
        r"return\s+nvkm_uevent_add\s*\(\s*uevent\s*,\s*"
        r"&\s*runl\s*->\s*fifo\s*->\s*nonstall\s*\.\s*event\s*,\s*"
        r"([^,]+?)\s*,\s*NVKM_FIFO_NONSTALL_EVENT\s*,\s*NULL\s*\)\s*;",
        body,
        re.DOTALL,
    )
    if len(calls) != 1:
        return "unknown"

    index = re.sub(r"\s+", "", calls[0])
    if index == "runl->id":
        return "vulnerable"
    if index == "runl->fifo->func->nonstall_ctor?runl->id:0":
        return "fixed"
    return "unknown"


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} PATH/TO/uchan.c", file=sys.stderr)
        return 2

    path = Path(sys.argv[1])
    try:
        body = extract_function(path.read_text())
    except OSError as error:
        print(f"unknown: cannot read {path}: {error}", file=sys.stderr)
        return 2

    if body is None:
        print("unknown: nvkm_uchan_uevent() not found or malformed", file=sys.stderr)
        return 2

    state = classify(body)
    if state == "unknown":
        print("unknown: unrecognized nonstall event registration", file=sys.stderr)
        return 2

    print(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
