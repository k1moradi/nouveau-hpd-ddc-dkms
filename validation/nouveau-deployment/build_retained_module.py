#!/usr/bin/env python3
"""Clean-build and retain the pinned diagnostic Nouveau module only."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile


EXPECTED_KERNEL = "7.0.0-34-generic"
EXPECTED_VERMAGIC = "7.0.0-34-generic SMP preempt mod_unload modversions"
EXPECTED_REVIEW_BRANCH = "review/gk104-vaapi-selftest-v8-20261002"
EXPECTED_MAIN_COMMIT = "7b44b1c1e282eac7c54c1cfa8c758118cd66312c"
SOURCE_ARCHIVE_SHA256 = (
    "a874e1fb08d2ee695b08e0c8ce6fd2c76a4bf7ffa98882fbabd233380ef8a85a"
)
CANONICAL_BASE_FILE_SHA256 = {
    "drivers/gpu/drm/nouveau/include/nvkm/core/memory.h":
        "dbf6854812e3a1154f429b7a0d2e9ed628f9e34b4942c090d9953f760631c76c",
    "drivers/gpu/drm/nouveau/include/nvkm/subdev/bar.h":
        "6784ad3dc1137866a042fc376b935fd30e26bfd47d3d8aabfeb691d11434b4ac",
    "drivers/gpu/drm/nouveau/nouveau_abi16.c":
        "3d4c81dd87c8e426b7e948eb448a49075d1df54ab22c68b8e181a0b88bd28fec",
    "drivers/gpu/drm/nouveau/nouveau_debugfs.c":
        "5f9d6fbb928f72784757db688c8be6ee823d6485095ac4eea1b1074e0c0a8e6c",
    "drivers/gpu/drm/nouveau/nouveau_drm.c":
        "a69bfbb7cc441171a80837d4806d8f677eede00abd6edcdf64ff8766911b4a07",
    "drivers/gpu/drm/nouveau/nvkm/core/ioctl.c":
        "2a18013840cee2298edadd19171b083cfed2bb7227a13303ac9afad34a107e18",
    "drivers/gpu/drm/nouveau/nvkm/engine/fifo/gf100.c":
        "1887dd37924288d9a641ac7ea6279e02f2436d42f79943eeba5b37ce28074b95",
    "drivers/gpu/drm/nouveau/nvkm/subdev/bar/base.c":
        "e0d5ec84f39c2ca3335489c96303144c41abe88a3bd2276359fa186ba16b6cd8",
    "drivers/gpu/drm/nouveau/nvkm/subdev/instmem/nv50.c":
        "19060f6a69bfae03044443d310cbe15806f161f2bf1c0f5b24e72f704796894e",
}
PATCHES = {
    "0009-drm-nouveau-log-nvif-duplicate-layer.patch":
        "442b14bfb53201dc7c9e7ac8bcfd6a1f74bb94280a8d766ffc119bcf20d327fc",
    "0010-drm-nouveau-correlate-instmem-vma-selftest.patch":
        "472886814c7dd67ff2685bc50791030f24f4267f6a22be16f57ce5c9e1b4818e",
    "0011-drm-nouveau-trace-ambient-bar2-lifetimes.patch":
        "67f0ba18f3e4ce979bc97784f622a33e0eb5f6e0472b7de4cd3d96925004e625",
    "0012-drm-nouveau-mark-bar2-teardown-phases.patch":
        "b0b549a128a77ade7dbcccd88fcd2d37ce30558fdf23d1a8f4792546c267266b",
    "0013-drm-nouveau-capture-ambient-bar2-events.patch":
        "d74002da984b672c510f7ecc1eb05e397f4ab869454276ab5c4c58c60740d6eb",
}
PATCH_ARGUMENTS = tuple(PATCHES)
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FROZEN_TOOL_PATHS = (
    "validation/nouveau-bar2-ambient-diagnostic/export_ring.py",
    "validation/nouveau-bar2-ambient-diagnostic/correlate_bar2_lifetimes.py",
    "validation/nouveau-bar2-ambient-diagnostic/check_ring_headroom.py",
    "validation/nouveau-bar2-ambient-diagnostic/run_xrdp_console_reproducer.py",
    "validation/nouveau-bar2-ambient-diagnostic/xrdp-trigger-profile.json",
    "validation/nouveau-deployment/finalize_install.py",
    "validation/nouveau-deployment/admit_run.py",
)
REQUIRED_MARKERS = (
    "NOUVEAU_DIAG_NVIF_DUP",
    "NOUVEAU_DIAG_BAR2_FAULT",
    "NOUVEAU_DIAG_V3_VMA",
    "NOUVEAU_DIAG_BAR2_MAP",
    "NOUVEAU_DIAG_BAR2_RESET",
    "OBJECT_IOUNMAP_BEGIN",
    "OBJECT_VMM_PUT_BEGIN",
    "NOUVEAU_DIAG_BAR2_RING",
    "NOUVEAU_DIAG_BAR2_RING_EVENT",
    "NOUVEAU_DIAG_BAR2_RING_END",
    "NOUVEAU_DIAG_BAR2_RING_GAP",
    "ambient_bar2_events",
    "NOUVEAU_DIAG_BAR2_RING_STATUS",
    "ambient_bar2_status",
    "diag_bar2_map",
)
DISABLED_MARKERS = (
    *REQUIRED_MARKERS,
    "nvkm_diag_bar2_map",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class ReviewTreeSnapshot:
    branch: str
    head: str
    main_head: str
    status: str
    builder_sha256: str
    patch_sha256: tuple[tuple[str, str], ...]
    tool_sha256: tuple[tuple[str, str], ...]


def review_tree_snapshot(patch_paths: dict[str, Path]) -> ReviewTreeSnapshot:
    builder = Path(__file__).resolve(strict=True)
    tools = []
    for relative in FROZEN_TOOL_PATHS:
        path = REPOSITORY_ROOT / relative
        if not path.is_file():
            raise RuntimeError(f"missing frozen build-validation tool: {path}")
        tools.append((relative, sha256_file(path)))
    return ReviewTreeSnapshot(
        branch=git_output("branch", "--show-current"),
        head=git_output("rev-parse", "HEAD"),
        main_head=git_output("rev-parse", "main"),
        status=git_output("status", "--porcelain=v1", "--untracked-files=all"),
        builder_sha256=sha256_file(builder),
        patch_sha256=tuple(sorted(
            (name, sha256_file(path)) for name, path in patch_paths.items()
        )),
        tool_sha256=tuple(tools),
    )


def require_unchanged_review_tree(
    before: ReviewTreeSnapshot,
    after: ReviewTreeSnapshot,
) -> None:
    if before != after:
        changed = [
            field
            for field in ReviewTreeSnapshot.__dataclass_fields__
            if getattr(before, field) != getattr(after, field)
        ]
        raise RuntimeError(
            "review repository or patch inputs changed during retained build: "
            + ", ".join(changed)
        )


def verify_patch_inputs(*patch_inputs: Path) -> dict[str, Path]:
    if len(patch_inputs) != len(PATCH_ARGUMENTS):
        raise RuntimeError(
            f"expected {len(PATCH_ARGUMENTS)} pinned kernel patches, got {len(patch_inputs)}"
        )
    paths = {
        filename: path.resolve(strict=True)
        for filename, path in zip(PATCH_ARGUMENTS, patch_inputs, strict=True)
    }
    for filename, path in paths.items():
        if not path.is_file():
            raise RuntimeError(f"kernel patch input is not a regular file: {path}")
        actual = sha256_file(path)
        expected = PATCHES[filename]
        if actual != expected:
            raise RuntimeError(
                f"unexpected hash for {filename}: {actual} != {expected}"
            )
    return paths


def source_fingerprint(root: Path) -> dict[str, object]:
    excluded_suffixes = {
        ".o", ".ko", ".a", ".cmd", ".d", ".mod", ".symvers",
    }
    excluded_names = {"modules.order", "Module.symvers"}
    records = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name in excluded_names:
            continue
        if path.suffix in excluded_suffixes or path.name.endswith(".mod.c"):
            continue
        relative = path.relative_to(root).as_posix()
        records.append({"path": relative, "sha256": sha256_file(path), "size": path.stat().st_size})
    if not records:
        raise RuntimeError(f"no source records under {root}")
    aggregate = hashlib.sha256()
    for item in records:
        aggregate.update(item["path"].encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(item["sha256"].encode("ascii"))
        aggregate.update(b"\n")
    return {
        "source_file_count": len(records),
        "source_tree_sha256": aggregate.hexdigest(),
        "files": records,
    }


def patch_file_paths(patch_path: Path) -> set[str]:
    paths: set[str] = set()
    lines = patch_path.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        match = re.fullmatch(r"diff --git a/(\S+) b/(\S+)", line)
        if match is not None:
            old_path, new_path = match.groups()
            index += 1
        else:
            match = re.fullmatch(r"--- a/(\S+)", line)
            if match is None:
                index += 1
                continue
            old_path = match.group(1)
            if index + 1 >= len(lines):
                raise RuntimeError(f"unpaired unified patch path header: {patch_path}")
            new_match = re.fullmatch(r"\+\+\+ b/(\S+)", lines[index + 1])
            if new_match is None:
                raise RuntimeError(f"unpaired unified patch path header: {patch_path}")
            new_path = new_match.group(1)
            index += 2
        if old_path != new_path:
            raise RuntimeError(f"rename patches are not supported: {patch_path}")
        relative = Path(old_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError(f"unsafe patch path in {patch_path}: {old_path}")
        paths.add(relative.as_posix())
    if not paths:
        raise RuntimeError(f"patch contains no file paths: {patch_path}")
    return paths


def verify_patch_stack(
    source_root: Path,
    canonical_base_root: Path,
    patch_paths: dict[str, Path],
    log_dir: Path,
    expected_base_sha256: dict[str, str] | None = None,
) -> dict[str, str]:
    """Reverse the stack, then prove touched files equal the pinned base."""
    expected_hashes = (
        CANONICAL_BASE_FILE_SHA256
        if expected_base_sha256 is None else expected_base_sha256
    )
    touched_files: set[str] = set()
    for patch_path in patch_paths.values():
        touched_files.update(patch_file_paths(patch_path))

    if touched_files != set(expected_hashes):
        missing = sorted(set(expected_hashes) - touched_files)
        unexpected = sorted(touched_files - set(expected_hashes))
        raise RuntimeError(
            f"patch stack touched-file set differs from canonical base pin: "
            f"missing={missing}, unexpected={unexpected}"
        )

    with tempfile.TemporaryDirectory(prefix="nouveau-patch-stack-verify-") as temporary:
        verification_root = Path(temporary)
        for relative in sorted(touched_files):
            source = source_root / relative
            if not source.is_file():
                raise RuntimeError(f"patch stack source file is missing: {source}")
            destination = verification_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

        for filename, patch_path in reversed(tuple(patch_paths.items())):
            check_command = [
                "git", "apply", "--reverse", "--check", "--unidiff-zero",
                str(patch_path),
            ]
            run_logged(
                check_command, log_dir / f"verify-check-{filename}.log",
                cwd=verification_root,
            )
            apply_command = [
                "git", "apply", "--reverse", "--unidiff-zero",
                str(patch_path),
            ]
            run_logged(
                apply_command, log_dir / f"verify-{filename}.log",
                cwd=verification_root,
            )

        return verify_reversed_files(
            verification_root,
            canonical_base_root,
            touched_files,
            expected_hashes,
        )


def verify_reversed_files(
    verification_root: Path,
    canonical_base_root: Path,
    touched_files: set[str],
    expected_sha256: dict[str, str],
) -> dict[str, str]:
    """Require the reverse-applied overlay to equal every pinned base file."""
    if touched_files != set(expected_sha256):
        raise RuntimeError("reversed patch file set differs from canonical hash set")

    verified: dict[str, str] = {}
    for relative in sorted(touched_files):
        canonical = canonical_base_root / relative
        reversed_file = verification_root / relative
        if not canonical.is_file():
            raise RuntimeError(f"missing canonical base file: {canonical}")
        if not reversed_file.is_file():
            raise RuntimeError(f"missing reverse-applied file: {reversed_file}")
        expected = expected_sha256[relative]
        canonical_hash = sha256_file(canonical)
        if canonical_hash != expected:
            raise RuntimeError(
                f"canonical base hash mismatch for {relative}: "
                f"{canonical_hash} != {expected}"
            )
        reversed_hash = sha256_file(reversed_file)
        if reversed_hash != expected:
            raise RuntimeError(
                f"reversed patch stack does not reproduce canonical base: {relative}"
            )
        verified[relative] = reversed_hash
    return verified


def run_logged(command: list[str], log_path: Path, *, cwd: Path | None = None) -> None:
    with log_path.open("w", encoding="utf-8") as log:
        log.write(shlex.join(command) + "\n")
        log.flush()
        result = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}); see {log_path}")


def configured_kernel_release(headers: Path) -> tuple[str, str]:
    """Return the distro target release and upstream Kbuild base release."""
    release_file = headers / "include/config/kernel.release"
    uts_header = headers / "include/generated/utsrelease.h"
    release = release_file.read_text(encoding="ascii").strip()
    uts_text = uts_header.read_text(encoding="ascii")
    match = re.search(r'^#define UTS_RELEASE "([^\"]+)"$', uts_text, re.MULTILINE)
    if not match:
        raise RuntimeError(f"cannot read UTS_RELEASE from {uts_header}")
    uts_release = match.group(1)
    if release != uts_release:
        raise RuntimeError(
            f"configured kernel release mismatch: {release!r} != {uts_release!r}"
        )
    upstream = subprocess.run(
        ["make", "-s", "-C", str(headers), "kernelrelease"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return release, upstream


def read_modinfo(module: Path, field: str) -> str:
    result = subprocess.run(
        ["modinfo", "-F", field, str(module)],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def git_output(*arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(Path(__file__).resolve().parents[2]), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--canonical-base-root", type=Path, required=True)
    parser.add_argument("--kernel-headers", type=Path, required=True)
    parser.add_argument("--nvif-duplicate-patch", type=Path, required=True)
    parser.add_argument("--vma-lifecycle-patch", type=Path, required=True)
    parser.add_argument("--ambient-lifetime-patch", type=Path, required=True)
    parser.add_argument("--teardown-phase-patch", type=Path, required=True)
    parser.add_argument("--ambient-ring-patch", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--review-commit", required=True)
    args = parser.parse_args()

    source_root = args.source_root.resolve(strict=True)
    canonical_base_root = args.canonical_base_root.resolve(strict=True)
    module_source = source_root / "drivers/gpu/drm/nouveau"
    kernel_headers = args.kernel_headers.resolve(strict=True)
    patch_paths = verify_patch_inputs(
        args.nvif_duplicate_patch,
        args.vma_lifecycle_patch,
        args.ambient_lifetime_patch,
        args.teardown_phase_patch,
        args.ambient_ring_patch,
    )
    review_before = review_tree_snapshot(patch_paths)
    review_branch = review_before.branch
    review_commit = review_before.head
    main_commit = review_before.main_head
    if review_branch != EXPECTED_REVIEW_BRANCH:
        raise RuntimeError(f"unexpected review branch {review_branch!r}")
    if review_commit != args.review_commit:
        raise RuntimeError(
            f"--review-commit {args.review_commit} does not match HEAD {review_commit}"
        )
    if main_commit != EXPECTED_MAIN_COMMIT:
        raise RuntimeError(f"main moved unexpectedly: {main_commit}")
    if review_before.status:
        raise RuntimeError("refusing to build from a dirty review worktree")
    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        raise RuntimeError(f"refusing existing output directory {output_dir}")
    if not (module_source / "Kbuild").is_file():
        raise RuntimeError(f"missing external Nouveau Kbuild under {module_source}")
    if not (kernel_headers / "Makefile").is_file():
        raise RuntimeError(f"invalid kernel header tree {kernel_headers}")
    kernel_release, upstream_kernel_release = configured_kernel_release(kernel_headers)
    if kernel_release != EXPECTED_KERNEL:
        raise RuntimeError(f"unexpected kernel headers release {kernel_release}")

    patch_hashes = {
        filename: sha256_file(path)
        for filename, path in patch_paths.items()
    }

    output_dir.mkdir(parents=True)
    # Reverse the patch stack on temporary copies: later patches can modify
    # the same source lines as earlier ones, so independent dry-runs against
    # the unchanged final overlay are not a valid stack check.
    canonical_base_hashes = verify_patch_stack(
        source_root, canonical_base_root, patch_paths, output_dir,
    )

    clean_command = [
        "make", "-C", str(kernel_headers), "M=" + str(module_source), "clean",
    ]
    run_logged(clean_command, output_dir / "clean-enabled.log")
    tree = source_fingerprint(module_source)

    build_command = [
        "nice", "-n", "10", "make", "-C", str(kernel_headers),
        "M=" + str(module_source), "CONFIG_DRM_NOUVEAU=m",
        "KCFLAGS=-DCONFIG_DRM_NOUVEAU_SELFTEST=1", "W=1", "-j1", "modules",
    ]
    run_logged(build_command, output_dir / "build-enabled-W1.log")
    built_module = module_source / "nouveau.ko"
    if not built_module.is_file():
        raise RuntimeError(f"build did not produce {built_module}")
    srcversion = read_modinfo(built_module, "srcversion")
    vermagic = read_modinfo(built_module, "vermagic")
    if not srcversion:
        raise RuntimeError("enabled module has no srcversion")
    if vermagic != EXPECTED_VERMAGIC:
        raise RuntimeError(f"unexpected module vermagic {vermagic}")
    marker_text = subprocess.run(
        ["strings", "-a", str(built_module)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    markers = [marker for marker in REQUIRED_MARKERS if marker in marker_text]
    if markers != list(REQUIRED_MARKERS):
        raise RuntimeError(f"diagnostic marker check failed: {markers}")

    enabled_retained = output_dir / "nouveau-enabled.ko"
    shutil.copy2(built_module, enabled_retained)

    run_logged(clean_command, output_dir / "clean-disabled.log")
    disabled_tree = source_fingerprint(module_source)
    if disabled_tree != tree:
        raise RuntimeError("source overlay changed between enabled and disabled builds")
    disabled_build_command = [
        "nice", "-n", "10", "make", "-C", str(kernel_headers),
        "M=" + str(module_source), "CONFIG_DRM_NOUVEAU=m", "W=1", "-j1", "modules",
    ]
    run_logged(disabled_build_command, output_dir / "build-disabled-W1.log")
    disabled_module = module_source / "nouveau.ko"
    if not disabled_module.is_file():
        raise RuntimeError(f"disabled build did not produce {disabled_module}")
    disabled_srcversion = read_modinfo(disabled_module, "srcversion")
    disabled_vermagic = read_modinfo(disabled_module, "vermagic")
    if disabled_vermagic != EXPECTED_VERMAGIC:
        raise RuntimeError(f"unexpected disabled module vermagic {disabled_vermagic}")
    disabled_strings = subprocess.run(
        ["strings", "-a", str(disabled_module)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    leaked = [marker for marker in DISABLED_MARKERS if marker in disabled_strings]
    if leaked:
        raise RuntimeError(f"diagnostic markers leaked into disabled build: {leaked}")
    disabled_retained = output_dir / "nouveau-disabled.ko"
    shutil.copy2(disabled_module, disabled_retained)
    if source_fingerprint(module_source) != tree:
        raise RuntimeError("source overlay changed during enabled/disabled build matrix")

    retained = enabled_retained
    shutil.copy2(module_source / "Module.symvers", output_dir / "Module.symvers")
    shutil.copy2(module_source / "modules.order", output_dir / "modules.order")
    compiler_path = Path(shutil.which("gcc") or "gcc").resolve(strict=True)
    headers_package = subprocess.run(
        [
            "dpkg-query", "-W", "-f=${Version}",
            f"linux-headers-{kernel_release}",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    manifest = {
        "schema": 1,
        "status": "CLEAN_ENABLED_BUILD_RETAINED_NOT_INSTALLED",
        "review_commit": args.review_commit,
        "review_branch": review_branch,
        "main_commit": main_commit,
        "review_tree_unchanged_during_build": True,
        "review_builder_sha256": review_before.builder_sha256,
        "review_patch_sha256_at_build_start": dict(review_before.patch_sha256),
        "review_tool_sha256_at_build_start": dict(review_before.tool_sha256),
        "kernel_release": kernel_release,
        "upstream_kbuild_kernelrelease": upstream_kernel_release,
        "kernel_source_archive_sha256": SOURCE_ARCHIVE_SHA256,
        "source_overlay": str(source_root),
        "source_overlay_tree": tree,
        "canonical_base_source": str(canonical_base_root),
        "canonical_base_archive_sha256": SOURCE_ARCHIVE_SHA256,
        "canonical_base_touched_file_sha256": canonical_base_hashes,
        "patch_sha256": patch_hashes,
        "raw_module": str(retained),
        "raw_module_sha256": sha256_file(retained),
        "module_size_bytes": retained.stat().st_size,
        "srcversion": srcversion,
        "vermagic": vermagic,
        "diagnostic_markers": markers,
        "build_command": build_command,
        "enabled_module": str(enabled_retained),
        "enabled_module_sha256": sha256_file(enabled_retained),
        "enabled_srcversion": srcversion,
        "disabled_module": str(disabled_retained),
        "disabled_module_sha256": sha256_file(disabled_retained),
        "disabled_srcversion": disabled_srcversion,
        "disabled_vermagic": disabled_vermagic,
        "disabled_markers_absent": leaked == [],
        "disabled_diagnostic_markers_checked": list(DISABLED_MARKERS),
        "disabled_build_command": disabled_build_command,
        "build_log_sha256": sha256_file(output_dir / "build-enabled-W1.log"),
        "disabled_build_log_sha256": sha256_file(output_dir / "build-disabled-W1.log"),
        "clean_log_sha256": {
            "enabled": sha256_file(output_dir / "clean-enabled.log"),
            "disabled": sha256_file(output_dir / "clean-disabled.log"),
        },
        "patch_verify_log_sha256": {
            path.name: sha256_file(path)
            for path in output_dir.glob("verify-*.log")
        },
        "compiler_version": subprocess.run(
            [str(compiler_path), "--version"], check=True,
            capture_output=True, text=True,
        ).stdout.splitlines()[0],
        "compiler_path": str(compiler_path),
        "compiler_sha256": sha256_file(compiler_path),
        "kernel_headers_package_version": (
            headers_package.stdout.strip()
            if headers_package.returncode == 0
            else "unavailable: " + headers_package.stderr.strip()
        ),
    }
    review_after = review_tree_snapshot(patch_paths)
    require_unchanged_review_tree(review_before, review_after)
    manifest_path = output_dir / "build-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output_dir / "SHA256SUMS").write_text(
        f"{sha256_file(enabled_retained)}  nouveau-enabled.ko\n"
        f"{sha256_file(disabled_retained)}  nouveau-disabled.ko\n"
        f"{sha256_file(manifest_path)}  build-manifest.json\n"
        f"{sha256_file(output_dir / 'build-enabled-W1.log')}  build-enabled-W1.log\n",
        encoding="ascii",
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(2)
