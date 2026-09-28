#!/usr/bin/env python3
"""Classify the GK104 engine-context mapping before applying its backport."""

from pathlib import Path
import re
import sys


EXPECTED_ENGINES = {
    "NVKM_ENGINE_MSPDEC",
    "NVKM_ENGINE_MSPPP",
    "NVKM_ENGINE_MSVLD",
}


def strip_comments(text: str) -> str:
    """Replace C comments with whitespace while preserving line structure."""
    pattern = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)
    return pattern.sub(lambda match: "\n" * match.group(0).count("\n"), text)


def matching_brace(text: str, opening: int) -> int | None:
    """Return the matching closing brace, ignoring C strings and comments."""
    depth = 0
    state = "code"
    escaped = False
    index = opening

    while index < len(text):
        char = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""

        if state == "line_comment":
            if char == "\n":
                state = "code"
        elif state == "block_comment":
            if char == "*" and following == "/":
                state = "code"
                index += 1
        elif state in ("string", "char"):
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif (state == "string" and char == '"') or (
                state == "char" and char == "'"
            ):
                state = "code"
        elif char == "/" and following == "/":
            state = "line_comment"
            index += 1
        elif char == "/" and following == "*":
            state = "block_comment"
            index += 1
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
        r"\bint\s+gk104_ectx_ctor\s*\([^;{}]*\)\s*\{",
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


def has_expected_map_contract(body: str) -> bool:
    return bool(
        re.search(
            r"struct\s+gf100_vmm_map_v0\s+args\s*=\s*\{\s*\.\s*priv\s*=\s*1\s*\}\s*;",
            body,
        )
        and re.search(
            r"\bnvkm_memory_map\s*\([^;]*&\s*args\s*,\s*sizeof\s*\(\s*args\s*\)",
            body,
            re.DOTALL,
        )
    )


def has_upstream_fix(body: str) -> bool:
    if not has_expected_map_contract(body):
        return False

    switch = re.search(
        r"\bswitch\s*\(\s*engn\s*->\s*engine\s*->\s*subdev\s*\.\s*type\s*\)\s*\{",
        body,
    )
    if not switch:
        return False

    opening = body.find("{", switch.start(), switch.end())
    closing = matching_brace(body, opening)
    if closing is None:
        return False

    switch_body = body[opening + 1 : closing]
    labels = list(
        re.finditer(r"\bcase\s+(NVKM_ENGINE_[A-Z0-9_]+)\s*:", switch_body)
    )
    if len(labels) != len(EXPECTED_ENGINES):
        return False
    if {label.group(1) for label in labels} != EXPECTED_ENGINES:
        return False

    if switch_body[: labels[0].start()].strip():
        return False
    for current, following in zip(labels, labels[1:]):
        if switch_body[current.end() : following.start()].strip():
            return False

    assignment = re.search(r"\bargs\s*\.\s*priv\s*=\s*0\s*;", switch_body)
    default = re.search(r"\bdefault\s*:", switch_body)
    if not assignment or not default:
        return False
    if not labels[-1].end() <= assignment.start() < default.start():
        return False
    if len(re.findall(r"\bargs\s*\.\s*priv\s*=", body)) != 1:
        return False
    if not re.fullmatch(
        r"\s*break\s*;\s*", switch_body[assignment.end() : default.start()]
    ):
        return False
    return bool(re.fullmatch(r"\s*break\s*;\s*", switch_body[default.end() :]))


def is_vulnerable_preimage(body: str) -> bool:
    if not has_expected_map_contract(body):
        return False
    if not re.search(r"\bnvkm_vmm_get\s*\(", body):
        return False
    if re.search(r"\bargs\s*\.\s*priv\s*=", body):
        return False
    if re.search(r"\bNVKM_ENGINE_(?:MSVLD|MSPDEC|MSPPP)\b", body):
        return False
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} PATH/TO/gk104.c", file=sys.stderr)
        return 2

    path = Path(sys.argv[1])
    try:
        body = extract_function(path.read_text())
    except OSError as error:
        print(f"unknown: cannot read {path}: {error}", file=sys.stderr)
        return 2

    if body is None:
        print("unknown: gk104_ectx_ctor() not found or malformed", file=sys.stderr)
        return 2
    if has_upstream_fix(body):
        print("fixed")
        return 0
    if is_vulnerable_preimage(body):
        print("vulnerable")
        return 0

    print("unknown: unrecognized gk104_ectx_ctor() layout", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
