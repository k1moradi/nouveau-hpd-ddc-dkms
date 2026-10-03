#!/usr/bin/env python3
"""Build matched, private Mesa VA DSO variants for the NVIF DEL-fd A/B.

The pinned Meson build tree contains the exact VA-enabled functional baseline
used by the saved traces. This script pins the diagnostic patch and
baseline build metadata, clones the build directory twice, and adjusts only
the generated Ninja edge for nouveau.c: both builds consume the same
zero-fuzz-patched source file, and variant B adds only
NOUVEAU_DIAG_NVIF_CORRECT_DEL_FD to variant A's diagnostic define. It checks
the build graph before and after linking. All builds are private and the
script has no install, loader, module, or GPU actions.
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
EXPECTED_BASE_VERSION_SCRIPT_SHA256 = (
    "61fc96386f8ab4ca3473c7d48d2aaec54681b2ac6192bd21752518949d34a7f2"
)
EXPECTED_DIAGNOSTIC_PATCH_SHA256 = (
    "f89c752fc445d4534f46ce45813a877b4229efdeae7d6d306429fb7462f913b7"
)
# These baseline build metadata hashes were measured in the post-link audit.
# They are mandatory pins for future helper runs, not claimed as pre-link
# checks for the original A/B build recorded in the 87d44e9 checkpoint.
EXPECTED_BASE_BUILD_METADATA_SHA256 = {
    "build.ninja": (
        "ba2acdaa119fb43ef5c98780e19bc59af2d5cfc51bf4cf34769af06534c71ff7"
    ),
    "meson-private/coredata.dat": (
        "5fa21fef3f842c90196b57a5e1a9b8aefefcff39db25cef0c58a0c3e872dfed7"
    ),
    "compile_commands.json": (
        "c6824053b21c8d8f6b7d68021da7f75e4ad947a41008201259543e4576206151"
    ),
}
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


def require_sha256(path: Path, expected: str, description: str) -> str:
    if not path.is_file():
        raise RuntimeError(f"missing {description}: {path}")
    actual = sha256(path)
    if actual != expected:
        raise RuntimeError(
            f"unexpected {description} SHA-256 for {path}: {actual}"
        )
    return actual


def verify_base_metadata(base_build: Path) -> dict[str, str]:
    actual: dict[str, str] = {}
    for relative, expected in EXPECTED_BASE_BUILD_METADATA_SHA256.items():
        actual[relative] = require_sha256(
            base_build / relative,
            expected,
            f"pinned base build metadata ({relative})",
        )
    return actual


def verify_base_version_script(base_build: Path) -> str:
    return require_sha256(
        base_build / "src/gallium/targets/va/va.sym",
        EXPECTED_BASE_VERSION_SCRIPT_SHA256,
        "pinned read-only VA version-script input",
    )


def verify_cloned_metadata(build_dir: Path) -> dict[str, str]:
    actual: dict[str, str] = {}
    for relative, expected in EXPECTED_BASE_BUILD_METADATA_SHA256.items():
        if relative == "build.ninja":
            continue
        actual[relative] = require_sha256(
            build_dir / relative,
            expected,
            f"cloned build metadata ({relative})",
        )
    return actual


def verify_ab_ninja(a_ninja: bytes, b_ninja: bytes) -> None:
    candidate_bytes = (" " + CANDIDATE_DEFINE).encode("ascii")
    if candidate_bytes in a_ninja:
        raise RuntimeError("baseline Ninja file contains the candidate define")
    if b_ninja.count(candidate_bytes) != 1:
        raise RuntimeError("candidate Ninja file must contain one candidate define")
    if b_ninja.replace(candidate_bytes, b"", 1) != a_ninja:
        raise RuntimeError("A/B Ninja files differ beyond the candidate define")


def verify_base_build_references(
    commands: list[str],
    base_build: Path,
) -> int:
    """Reject command paths into the baseline build except its read-only va.sym."""
    base_text = str(base_build)
    allowed_script = str(base_build / "src/gallium/targets/va/va.sym")
    allowed_count = 0
    for command in commands:
        tokens = shlex.split(command)
        for index, token in enumerate(tokens):
            if base_text not in token:
                continue
            if token != allowed_script:
                raise RuntimeError(
                    f"command references unexpected baseline build path: {token}"
                )
            if index == 0 or tokens[index - 1] != "-Wl,--version-script":
                raise RuntimeError(
                    "baseline va.sym reference is not a read-only version-script input"
                )
            allowed_count += 1
    if allowed_count != 1:
        raise RuntimeError(
            "expected one read-only baseline va.sym reference in the target graph, "
            f"found {allowed_count}"
        )
    return allowed_count


def verify_target_commands(
    a_commands: list[str],
    b_commands: list[str],
    base_build: Path,
) -> int:
    if len(a_commands) != len(b_commands):
        raise RuntimeError("A/B dependency command graphs differ in length")
    a_tokens = [token for command in a_commands for token in shlex.split(command)]
    b_tokens = [token for command in b_commands for token in shlex.split(command)]
    if a_tokens.count(CANDIDATE_DEFINE) != 0:
        raise RuntimeError("baseline target graph contains the candidate define")
    if b_tokens.count(CANDIDATE_DEFINE) != 1:
        raise RuntimeError("candidate define is not unique in the B target graph")
    if a_tokens.count(COMMON_DEFINE) != 1 or b_tokens.count(COMMON_DEFINE) != 1:
        raise RuntimeError("shared lifetime diagnostic define is not unique per graph")
    verify_base_build_references(a_commands, base_build)
    verify_base_build_references(b_commands, base_build)

    changed_indices = [
        index
        for index, (a, b) in enumerate(
            zip(a_commands, b_commands, strict=True)
        )
        if a != b
    ]
    if (
        len(changed_indices) != 1
        or normalize_candidate_flag(b_commands[changed_indices[0]])
        != shlex.join(shlex.split(a_commands[changed_indices[0]]))
    ):
        raise RuntimeError(
            "A/B target command graphs differ outside the one DEL-fd define"
        )
    return changed_indices[0]


def graph_hashes(
    build_a: Path,
    build_b: Path,
    a_compile: str,
    b_compile: str,
    a_commands: list[str],
    b_commands: list[str],
) -> dict[str, str]:
    def text_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    return {
        "build_ninja_a_sha256": sha256(build_a / "build.ninja"),
        "build_ninja_b_sha256": sha256(build_b / "build.ninja"),
        "nouveau_compile_a_sha256": text_hash(a_compile + "\n"),
        "nouveau_compile_b_sha256": text_hash(b_compile + "\n"),
        "target_commands_a_sha256": text_hash("\n".join(a_commands) + "\n"),
        "target_commands_b_sha256": text_hash("\n".join(b_commands) + "\n"),
    }


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
        raise RuntimeError("command lacks exactly one candidate define")
    tokens.remove(CANDIDATE_DEFINE)
    return shlex.join(tokens)


def verify_compile_commands(a_compile: str, b_compile: str) -> None:
    a_tokens = shlex.split(a_compile)
    b_tokens = shlex.split(b_compile)
    if a_tokens.count(COMMON_DEFINE) != 1 or b_tokens.count(COMMON_DEFINE) != 1:
        raise RuntimeError("shared lifetime diagnostic define is not unique per variant")
    if CANDIDATE_DEFINE in a_tokens:
        raise RuntimeError("baseline variant unexpectedly selects corrected DEL fd")
    if normalize_candidate_flag(b_compile) != shlex.join(shlex.split(a_compile)):
        raise RuntimeError(
            "A/B nouveau.c compiler commands differ beyond the candidate define"
        )


def inspect_ab_graph(
    build_a: Path,
    build_b: Path,
    base_build: Path,
) -> dict[str, object]:
    a_metadata = verify_cloned_metadata(build_a)
    b_metadata = verify_cloned_metadata(build_b)
    if a_metadata != b_metadata:
        raise RuntimeError("A/B copied Meson metadata differs")

    a_ninja = (build_a / "build.ninja").read_bytes()
    b_ninja = (build_b / "build.ninja").read_bytes()
    verify_ab_ninja(a_ninja, b_ninja)

    a_compile = object_compile_command(build_a)
    b_compile = object_compile_command(build_b)
    verify_compile_commands(a_compile, b_compile)

    a_commands = ninja_commands(build_a, TARGET)
    b_commands = ninja_commands(build_b, TARGET)
    changed_index = verify_target_commands(a_commands, b_commands, base_build)

    return {
        "a_compile": a_compile,
        "b_compile": b_compile,
        "a_commands": a_commands,
        "b_commands": b_commands,
        "changed_command_index": changed_index,
        "hashes": graph_hashes(
            build_a,
            build_b,
            a_compile,
            b_compile,
            a_commands,
            b_commands,
        ),
    }


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

    patch_sha = require_sha256(
        patch_file,
        EXPECTED_DIAGNOSTIC_PATCH_SHA256,
        "pinned NVIF lifetime diagnostic patch",
    )
    base_dso = base_build / TARGET
    base_dso_sha = require_sha256(
        base_dso,
        EXPECTED_BASE_DSO_SHA256,
        "pinned baseline VA DSO",
    )
    base_metadata = verify_base_metadata(base_build)
    base_version_script_sha = verify_base_version_script(base_build)

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

        pre_graph = inspect_ab_graph(build_a, build_b, base_build)
        a_compile = str(pre_graph["a_compile"])
        b_compile = str(pre_graph["b_compile"])
        a_commands = list(pre_graph["a_commands"])
        b_commands = list(pre_graph["b_commands"])
        changed_index = int(pre_graph["changed_command_index"])
        pre_hashes = dict(pre_graph["hashes"])

        (output_root / "nouveau.c.compile-A.txt").write_text(a_compile + "\n", encoding="utf-8")
        (output_root / "nouveau.c.compile-B.txt").write_text(b_compile + "\n", encoding="utf-8")
        (output_root / "target.commands-A.txt").write_text("\n".join(a_commands) + "\n", encoding="utf-8")
        (output_root / "target.commands-B.txt").write_text("\n".join(b_commands) + "\n", encoding="utf-8")
        meson_options = run(["meson", "introspect", "--buildoptions", str(base_build)])
        (output_root / "meson-buildoptions.json").write_text(
            meson_options, encoding="utf-8"
        )
        (output_root / "target-command-diff.txt").write_text(
            f"target={TARGET}\n"
            f"command_count={len(a_commands)}\n"
            "changed_command_count=1\n"
            f"changed_command_index={changed_index}\n"
            f"only_normalized_difference={CANDIDATE_DEFINE}\n",
            encoding="utf-8",
        )
        (output_root / "base-build-metadata.json").write_text(
            json.dumps(base_metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (output_root / "ab-graph-pre-build.json").write_text(
            json.dumps(pre_hashes, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        dso_a = build_variant(build_a, output_root, "A")
        dso_b = build_variant(build_b, output_root, "B")

        post_graph = inspect_ab_graph(build_a, build_b, base_build)
        post_hashes = dict(post_graph["hashes"])
        if post_hashes != pre_hashes:
            raise RuntimeError(
                "A/B Ninja/compiler/target graph changed during linking"
            )
        post_base_metadata = verify_base_metadata(base_build)
        if post_base_metadata != base_metadata:
            raise RuntimeError("baseline build metadata changed during A/B linking")
        post_version_script_sha = verify_base_version_script(base_build)
        if post_version_script_sha != base_version_script_sha:
            raise RuntimeError("baseline VA version script changed during A/B linking")
        post_base_dso_sha = require_sha256(
            base_dso,
            EXPECTED_BASE_DSO_SHA256,
            "baseline VA DSO after A/B linking",
        )
        (output_root / "target.commands-A-post.txt").write_text(
            "\n".join(post_graph["a_commands"]) + "\n", encoding="utf-8"
        )
        (output_root / "target.commands-B-post.txt").write_text(
            "\n".join(post_graph["b_commands"]) + "\n", encoding="utf-8"
        )
        (output_root / "nouveau.c.compile-A-post.txt").write_text(
            str(post_graph["a_compile"]) + "\n", encoding="utf-8"
        )
        (output_root / "nouveau.c.compile-B-post.txt").write_text(
            str(post_graph["b_compile"]) + "\n", encoding="utf-8"
        )
        (output_root / "ab-graph-post-build.json").write_text(
            json.dumps(post_hashes, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

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
            "diagnostic_patch_sha256": patch_sha,
            "patched_source_sha256": patched_sha,
            "base_dso_sha256": base_dso_sha,
            "base_dso_sha256_after_build": post_base_dso_sha,
            "base_build_metadata_sha256": base_metadata,
            "base_build_metadata_sha256_after_build": post_base_metadata,
            "base_version_script_sha256": base_version_script_sha,
            "base_version_script_sha256_after_build": post_version_script_sha,
            "meson_buildoptions_sha256": sha256(output_root / "meson-buildoptions.json"),
            "meson_coredata_sha256": sha256(base_build / "meson-private/coredata.dat"),
            "ab_graph_pre_build": pre_hashes,
            "ab_graph_post_build": post_hashes,
            "post_build_graph_matches_pre_build": True,
            "baseline_build_tree_unchanged": True,
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
            "baseline_build_references": {
                "allowed_read_only_input": "src/gallium/targets/va/va.sym",
                "writes_into_baseline_build_tree": 0,
            },
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
