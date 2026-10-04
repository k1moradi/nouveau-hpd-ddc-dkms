#!/usr/bin/env python3
"""Clean-build and retain the pinned diagnostic Nouveau module only."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys


EXPECTED_KERNEL = "7.0.0-34-generic"
EXPECTED_SRCVERSION = "936407678F3DA1E8515F5EC"
EXPECTED_VERMAGIC = "7.0.0-34-generic SMP preempt mod_unload modversions"
EXPECTED_REVIEW_BRANCH = "review/gk104-vaapi-selftest-v8-20261002"
EXPECTED_MAIN_COMMIT = "7b44b1c1e282eac7c54c1cfa8c758118cd66312c"
SOURCE_ARCHIVE_SHA256 = (
    "a874e1fb08d2ee695b08e0c8ce6fd2c76a4bf7ffa98882fbabd233380ef8a85a"
)
PATCHES = {
    "0009-drm-nouveau-log-nvif-duplicate-layer.patch":
        "442b14bfb53201dc7c9e7ac8bcfd6a1f74bb94280a8d766ffc119bcf20d327fc",
    "0010-drm-nouveau-correlate-instmem-vma-selftest.patch":
        "472886814c7dd67ff2685bc50791030f24f4267f6a22be16f57ce5c9e1b4818e",
}
PATCH_ARGUMENTS = {
    "0009-drm-nouveau-log-nvif-duplicate-layer.patch": "nvif_duplicate_patch",
    "0010-drm-nouveau-correlate-instmem-vma-selftest.patch": "vma_lifecycle_patch",
}
REQUIRED_MARKERS = (
    "NOUVEAU_DIAG_NVIF_DUP",
    "NOUVEAU_DIAG_BAR2_FAULT",
    "NOUVEAU_DIAG_V3_VMA",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_patch_inputs(
    nvif_duplicate_patch: Path,
    vma_lifecycle_patch: Path,
) -> dict[str, Path]:
    paths = {
        "0009-drm-nouveau-log-nvif-duplicate-layer.patch":
            nvif_duplicate_patch.resolve(strict=True),
        "0010-drm-nouveau-correlate-instmem-vma-selftest.patch":
            vma_lifecycle_patch.resolve(strict=True),
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
        raise RuntimeError(f"build command failed ({result.returncode}); see {log_path}")


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
    parser.add_argument("--kernel-headers", type=Path, required=True)
    parser.add_argument("--nvif-duplicate-patch", type=Path, required=True)
    parser.add_argument("--vma-lifecycle-patch", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--review-commit", required=True)
    args = parser.parse_args()

    review_branch = git_output("branch", "--show-current")
    review_commit = git_output("rev-parse", "HEAD")
    main_commit = git_output("rev-parse", "main")
    dirty = git_output("status", "--porcelain=v1", "--untracked-files=all")
    if review_branch != EXPECTED_REVIEW_BRANCH:
        raise RuntimeError(f"unexpected review branch {review_branch!r}")
    if review_commit != args.review_commit:
        raise RuntimeError(
            f"--review-commit {args.review_commit} does not match HEAD {review_commit}"
        )
    if main_commit != EXPECTED_MAIN_COMMIT:
        raise RuntimeError(f"main moved unexpectedly: {main_commit}")
    if dirty:
        raise RuntimeError("refusing to build from a dirty review worktree")

    source_root = args.source_root.resolve(strict=True)
    module_source = source_root / "drivers/gpu/drm/nouveau"
    kernel_headers = args.kernel_headers.resolve(strict=True)
    patch_paths = verify_patch_inputs(
        args.nvif_duplicate_patch,
        args.vma_lifecycle_patch,
    )
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
    # Verify both patches are present in the retained source overlay without
    # modifying that source tree.
    for filename in (
        "0010-drm-nouveau-correlate-instmem-vma-selftest.patch",
        "0009-drm-nouveau-log-nvif-duplicate-layer.patch",
    ):
        command = [
            "patch", "--batch", "--fuzz=0", "--dry-run", "-R", "-p1",
            "-i", str(patch_paths[filename]),
        ]
        run_logged(command, output_dir / f"verify-{filename}.log", cwd=source_root)

    clean_command = [
        "make", "-C", str(kernel_headers), "M=" + str(module_source), "clean",
    ]
    run_logged(clean_command, output_dir / "clean.log")
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
    if srcversion != EXPECTED_SRCVERSION:
        raise RuntimeError(f"unexpected module srcversion {srcversion}")
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

    retained = output_dir / "nouveau.ko"
    shutil.copy2(built_module, retained)
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
        "kernel_release": kernel_release,
        "upstream_kbuild_kernelrelease": upstream_kernel_release,
        "kernel_source_archive_sha256": SOURCE_ARCHIVE_SHA256,
        "source_overlay": str(source_root),
        "source_overlay_tree": tree,
        "patch_sha256": patch_hashes,
        "raw_module": str(retained),
        "raw_module_sha256": sha256_file(retained),
        "module_size_bytes": retained.stat().st_size,
        "srcversion": srcversion,
        "vermagic": vermagic,
        "diagnostic_markers": markers,
        "build_command": build_command,
        "build_log_sha256": sha256_file(output_dir / "build-enabled-W1.log"),
        "clean_log_sha256": sha256_file(output_dir / "clean.log"),
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
    manifest_path = output_dir / "build-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output_dir / "SHA256SUMS").write_text(
        f"{sha256_file(retained)}  nouveau.ko\n"
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
