#!/usr/bin/env python3
"""Build matched, private Mesa VA DSO variants for the NVIF DEL-fd A/B.

The checked-in Meson build already contains the exact VA-enabled functional
baseline used by the saved traces.  This script clones that build directory
twice and adjusts only the generated Ninja edge for nouveau.c: both builds
consume the same zero-fuzz-patched source file, and variant B adds only
NOUVEAU_DIAG_NVIF_CORRECT_DEL_FD to variant A's diagnostic define.  All builds
are private and the script has no install, loader, module, or GPU actions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import shlex
import subprocess
from pathlib import Path


EXPECTED_SOURCE_SHA256 = (
    "2140bca6de1666e4517ebabf97a63b417db43bfbc575198d7835d40ad92dc57f"
)
EXPECTED_BASE_DSO_SHA256 = (
    "aceac163eabeefe5e4c1f2eb38469239044e30671aebcec743e4277f5897b536"
)
SOURCE_REL = Path("src/gallium/winsys/nouveau/drm/nouveau.c")
OBJECT = "src/gallium/winsys/nouveau/drm/libnouveauwinsys.a.p/nouveau.c.o"
TARGET = "src/gallium/targets/va/libgallium_drv_video.so"
COMMON_DEFINE = "-DNOUVEAU_DIAG_NVIF_LIFETIME"
CANDIDATE_DEFINE = "-DNOUVEAU_DIAG_NVIF_CORRECT_DEL_FD"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(command: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(
            f"command failed ({result.returncode}): {shlex.join(command)}\n"
            f"{result.stdout}"
        )
    return result.stdout


def patched_source(source_tree: Path, patch_file: Path, output_root: Path) -> Path:
    original = source_tree / SOURCE_REL
    actual = sha256(original)
    if actual != EXPECTED_SOURCE_SHA256:
        raise RuntimeError(
            f"unexpected pinned nouveau.c SHA-256: {actual}"
        )

    root = output_root / "patched-source"
    target = root / SOURCE_REL
    target.parent.mkdir(parents=True)
    shutil.copy2(original, target)
    run(
        ["patch", "--batch", "--fuzz=0", "-p1", "-i", str(patch_file)],
        cwd=root,
    )
    return target


def patch_build_edge(
    build_dir: Path,
    source_file: Path,
    defines: tuple[str, ...],
) -> None:
    build_file = build_dir / "build.ninja"
    lines = build_file.read_text(encoding="utf-8").splitlines(keepends=True)
    marker = f"build {OBJECT}: c_COMPILER "
    matches = [i for i, line in enumerate(lines) if line.startswith(marker)]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one nouveau.c Ninja edge in {build_file}, found {len(matches)}"
        )

    index = matches[0]
    line = lines[index]
    relative_source = "../source/mesa-26.0.8/" + SOURCE_REL.as_posix()
    if relative_source not in line:
        raise RuntimeError("Ninja nouveau.c edge source path changed")
    lines[index] = line.replace(relative_source, str(source_file), 1)

    args_index = None
    for position in range(index + 1, len(lines)):
        if not lines[position].strip() or lines[position].startswith("build "):
            break
        if lines[position].lstrip().startswith("ARGS = "):
            args_index = position
            break
    if args_index is None:
        raise RuntimeError("Ninja nouveau.c edge has no local ARGS")

    prefix = lines[args_index][: len(lines[args_index]) - len(lines[args_index].lstrip())]
    args = lines[args_index].strip()[len("ARGS = ") :]
    if COMMON_DEFINE in args or CANDIDATE_DEFINE in args:
        raise RuntimeError("diagnostic defines already present in base Ninja edge")
    lines[args_index] = prefix + "ARGS = " + args + " " + " ".join(defines) + "\n"
    build_file.write_text("".join(lines), encoding="utf-8")


def ninja_commands(build_dir: Path, target: str) -> list[str]:
    output = run(["ninja", "-C", str(build_dir), "-t", "commands", target])
    return output.splitlines()


def object_compile_command(build_dir: Path) -> str:
    commands = ninja_commands(build_dir, OBJECT)
    marker = f"-MQ {OBJECT} "
    found = [line for line in commands if marker in line]
    if len(found) != 1:
        raise RuntimeError(
            f"expected one compile command for nouveau.c.o, found {len(found)}"
        )
    return found[0]


def normalize_candidate_flag(command: str) -> str:
    tokens = shlex.split(command)
    if tokens.count(CANDIDATE_DEFINE) != 1:
        raise RuntimeError("candidate compile command lacks exactly one candidate define")
    tokens.remove(CANDIDATE_DEFINE)
    return shlex.join(tokens)


def clone_build(base: Path, destination: Path) -> None:
    if destination.exists():
        raise RuntimeError(f"refusing to overwrite build tree {destination}")
    shutil.copytree(base, destination, symlinks=True, copy_function=shutil.copy2)


def build_variant(build_dir: Path, output_root: Path, name: str) -> Path:
    log = output_root / f"build-{name}.log"
    command = [
        "nice",
        "-n",
        "10",
        "ninja",
        "-C",
        str(build_dir),
        "-j1",
        "-v",
        TARGET,
    ]
    with log.open("w", encoding="utf-8") as stream:
        result = subprocess.run(
            command,
            check=False,
            stdout=stream,
            stderr=subprocess.STDOUT,
            text=True,
        )
    (output_root / f"build-{name}.exit").write_text(
        f"{result.returncode}\n", encoding="ascii"
    )
    if result.returncode:
        raise RuntimeError(f"{name} Mesa link build failed; see {log}")

    dso = build_dir / TARGET
    if not dso.is_file():
        raise RuntimeError(f"{name} build did not produce {dso}")
    copied = output_root / name / "libgallium_drv_video.so"
    copied.parent.mkdir(parents=True)
    shutil.copy2(dso, copied)
    return copied


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-tree", type=Path, required=True)
    parser.add_argument("--base-build", type=Path, required=True)
    parser.add_argument("--diagnostic-patch", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    source_tree = args.source_tree.resolve()
    base_build = args.base_build.resolve()
    patch_file = args.diagnostic_patch.resolve()
    output_root = args.output_root.resolve()
    if output_root.exists():
        raise RuntimeError(f"refusing to overwrite output root {output_root}")
    if not (source_tree / SOURCE_REL).is_file():
        raise RuntimeError(f"missing source file under {source_tree}")
    if not (base_build / "build.ninja").is_file():
        raise RuntimeError(f"not a Ninja build directory: {base_build}")
    if not patch_file.is_file():
        raise RuntimeError(f"missing diagnostic patch: {patch_file}")

    base_dso = base_build / TARGET
    base_dso_sha = sha256(base_dso)
    if base_dso_sha != EXPECTED_BASE_DSO_SHA256:
        raise RuntimeError(f"unexpected baseline VA DSO SHA-256: {base_dso_sha}")

    output_root.mkdir(parents=True)
    try:
        patched = patched_source(source_tree, patch_file, output_root)
        patched_sha = sha256(patched)
        base_parent = base_build.parent
        build_a = base_parent / f"{output_root.name}-build-A"
        build_b = base_parent / f"{output_root.name}-build-B"
        clone_build(base_build, build_a)
        clone_build(base_build, build_b)

        patch_build_edge(build_a, patched, (COMMON_DEFINE,))
        patch_build_edge(build_b, patched, (COMMON_DEFINE, CANDIDATE_DEFINE))

        a_ninja = (build_a / "build.ninja").read_bytes()
        b_ninja = (build_b / "build.ninja").read_bytes()
        candidate_bytes = (" " + CANDIDATE_DEFINE).encode("ascii")
        if b_ninja.count(candidate_bytes) != 1 or b_ninja.replace(candidate_bytes, b"", 1) != a_ninja:
            raise RuntimeError("A/B Ninja files differ beyond the candidate define")

        a_compile = object_compile_command(build_a)
        b_compile = object_compile_command(build_b)
        if normalize_candidate_flag(b_compile) != shlex.join(shlex.split(a_compile)):
            raise RuntimeError("A/B nouveau.c compiler commands differ beyond the candidate define")
        if COMMON_DEFINE not in a_compile or COMMON_DEFINE not in b_compile:
            raise RuntimeError("shared lifetime diagnostic define missing from a variant")
        if CANDIDATE_DEFINE in a_compile:
            raise RuntimeError("baseline variant unexpectedly selects corrected DEL fd")

        a_commands = ninja_commands(build_a, TARGET)
        b_commands = ninja_commands(build_b, TARGET)
        if len(a_commands) != len(b_commands):
            raise RuntimeError("A/B dependency command graphs differ in length")
        changed = [
            (a, b)
            for a, b in zip(a_commands, b_commands, strict=True)
            if a != b
        ]
        if len(changed) != 1 or normalize_candidate_flag(changed[0][1]) != shlex.join(shlex.split(changed[0][0])):
            raise RuntimeError("A/B target command graphs differ outside the one DEL-fd define")

        (output_root / "nouveau.c.compile-A.txt").write_text(a_compile + "\n", encoding="utf-8")
        (output_root / "nouveau.c.compile-B.txt").write_text(b_compile + "\n", encoding="utf-8")
        (output_root / "target.commands-A.txt").write_text("\n".join(a_commands) + "\n", encoding="utf-8")
        (output_root / "target.commands-B.txt").write_text("\n".join(b_commands) + "\n", encoding="utf-8")
        meson_options = run(["meson", "introspect", "--buildoptions", str(base_build)])
        (output_root / "meson-buildoptions.json").write_text(
            meson_options, encoding="utf-8"
        )
        changed_index = next(
            index for index, pair in enumerate(zip(a_commands, b_commands, strict=True))
            if pair[0] != pair[1]
        )
        (output_root / "target-command-diff.txt").write_text(
            f"target={TARGET}\n"
            f"command_count={len(a_commands)}\n"
            f"changed_command_count={len(changed)}\n"
            f"changed_command_index={changed_index}\n"
            f"only_normalized_difference={CANDIDATE_DEFINE}\n",
            encoding="utf-8",
        )
        (output_root / "meson-coredata.sha256").write_text(
            sha256(base_build / "meson-private/coredata.dat") + "\n", encoding="ascii"
        )

        dso_a = build_variant(build_a, output_root, "A")
        dso_b = build_variant(build_b, output_root, "B")
        dso_a_sha = sha256(dso_a)
        dso_b_sha = sha256(dso_b)
        if dso_a_sha == dso_b_sha:
            raise RuntimeError("A/B DSOs unexpectedly have identical hashes")

        marker_sets: dict[str, list[str]] = {}
        for label, dso in (("A", dso_a), ("B", dso_b)):
            strings = run(["strings", "-a", str(dso)])
            markers = [
                marker
                for marker in (
                    "NOUVEAU_DIAG_NVIF_NEW",
                    "NOUVEAU_DIAG_NVIF_DEL",
                    "NOUVEAU_DIAG_NVIF_CHANNEL_FREE",
                )
                if marker in strings
            ]
            if len(markers) != 3:
                raise RuntimeError(f"{label} DSO is missing lifetime log strings")
            marker_sets[label] = markers

        manifest = {
            "schema": 1,
            "status": "LINKED_BUILD_ONLY_NOT_INSTALLED_OR_RUN",
            "source_file_sha256": EXPECTED_SOURCE_SHA256,
            "diagnostic_patch_sha256": sha256(patch_file),
            "patched_source_sha256": patched_sha,
            "base_dso_sha256": base_dso_sha,
            "meson_buildoptions_sha256": sha256(output_root / "meson-buildoptions.json"),
            "meson_coredata_sha256": sha256(base_build / "meson-private/coredata.dat"),
            "variant_a": {
                "defines": [COMMON_DEFINE],
                "dso": str(dso_a),
                "dso_sha256": dso_a_sha,
                "diagnostic_strings": marker_sets["A"],
            },
            "variant_b": {
                "defines": [COMMON_DEFINE, CANDIDATE_DEFINE],
                "dso": str(dso_b),
                "dso_sha256": dso_b_sha,
                "diagnostic_strings": marker_sets["B"],
            },
            "build_dirs": {"A": str(build_a), "B": str(build_b)},
            "build_job_limit": 1,
            "build_priority": "nice -n 10",
            "only_compile_command_difference": CANDIDATE_DEFINE,
            "only_ninja_graph_difference": CANDIDATE_DEFINE,
        }
        (output_root / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0
    except BaseException:
        # Preserve all partial logs and build trees for diagnosis.
        raise


if __name__ == "__main__":
    raise SystemExit(main())
