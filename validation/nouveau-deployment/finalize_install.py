#!/usr/bin/env python3
"""Install the retained diagnostic module and freeze post-install provenance.

The default mode is a read-only plan. System changes require --apply and root.
This tool does not load/reload Nouveau and never reboots.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        count = os.write(fd, view)
        if count <= 0 or count > len(view):
            raise OSError("short or invalid write while saving deployment manifest")
        view = view[count:]


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        os.fchmod(fd, 0o644)
        write_all(fd, payload)
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(temporary_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        temporary_path.unlink(missing_ok=True)
        raise


def command_output(argv: list[str]) -> str:
    result = subprocess.run(
        argv, check=False, capture_output=True, text=True, timeout=30
    )
    if result.returncode:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(argv)}: "
            f"{result.stderr.strip()}"
        )
    return result.stdout.strip()


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object in {path}")
    return data


def validate_inputs(
    build: dict[str, Any], plan: dict[str, Any], raw_module: Path
) -> dict[str, Any]:
    if build.get("status") != "CLEAN_ENABLED_BUILD_RETAINED_NOT_INSTALLED":
        raise ValueError("build manifest is not a retained diagnostic-enabled build")
    if plan.get("schema") != 1:
        raise ValueError("unsupported deployment plan schema")
    if build.get("review_branch") != plan.get("expected_review_branch"):
        raise ValueError("build manifest review branch does not match deployment plan")
    if build.get("main_commit") != plan.get("expected_main_commit"):
        raise ValueError("build manifest main commit does not match deployment plan")
    review_commit = build.get("review_commit")
    if not isinstance(review_commit, str) or len(review_commit) != 40:
        raise ValueError("build manifest lacks a full review commit ID")
    try:
        int(review_commit, 16)
    except ValueError as exc:
        raise ValueError("build manifest review commit is not hexadecimal") from exc
    kernel = plan.get("kernel_release")
    if not isinstance(kernel, str) or not kernel:
        raise ValueError("deployment plan has no kernel release")
    if build.get("kernel_release") != kernel:
        raise ValueError("build manifest kernel does not match deployment plan")
    if build.get("kernel_source_archive_sha256") != plan.get("kernel_source_archive_sha256"):
        raise ValueError("kernel source archive hash does not match deployment plan")
    if build.get("patch_sha256") != plan.get("patch_sha256"):
        raise ValueError("kernel diagnostic patch hashes do not match deployment plan")
    if build.get("srcversion") != plan.get("expected_srcversion"):
        raise ValueError("build srcversion does not match deployment plan")
    if build.get("vermagic") != plan.get("expected_vermagic"):
        raise ValueError("build vermagic does not match deployment plan")
    if raw_module.resolve(strict=True) != Path(build["raw_module"]).resolve(strict=True):
        raise ValueError("raw module path does not match build manifest")
    if sha256_file(raw_module) != build.get("raw_module_sha256"):
        raise ValueError("raw module hash does not match build manifest")
    if not isinstance(build.get("srcversion"), str) or not build["srcversion"]:
        raise ValueError("build manifest lacks srcversion")
    if not isinstance(build.get("vermagic"), str) or not build["vermagic"].startswith(kernel + " "):
        raise ValueError("build manifest vermagic does not match target kernel")
    params = plan.get("module_parameters")
    if params != {"diag_ctxsw": "N"}:
        raise ValueError("only the reviewed diag_ctxsw=N parameter set is allowed")
    for variant in ("A", "B"):
        item = plan.get("mesa_variants", {}).get(variant)
        if not isinstance(item, dict):
            raise ValueError(f"missing Mesa variant {variant}")
        path = Path(item.get("path", "")).resolve(strict=True)
        if not path.is_file() or sha256_file(path) != item.get("sha256"):
            raise ValueError(f"Mesa variant {variant} path/hash mismatch")
    tools = plan.get("tools")
    if not isinstance(tools, dict) or not {
        "supervisor", "bar2_correlator", "nvif_capture", "nvif_parser",
        "nvif_profile", "pixel_capture", "va_export_probe",
        "va_export_probe_source", "va_export_probe_build_manifest",
        "va_export_driver_dso",
        "mesa_ab_manifest", "mesa_nvif_diagnostic_patch",
        "admission_check", "deployment_finalizer", "retained_module_builder",
    } <= tools.keys():
        raise ValueError("deployment plan lacks one or more required pinned tools")
    for name, item in tools.items():
        if not isinstance(item, dict):
            raise ValueError(f"invalid pinned tool record {name}")
        path = Path(item.get("path", "")).resolve(strict=True)
        expected = item.get("sha256")
        if not isinstance(expected, str) or len(expected) != 64:
            raise ValueError(f"pinned tool {name} lacks a SHA-256")
        try:
            int(expected, 16)
        except ValueError as exc:
            raise ValueError(f"pinned tool {name} SHA-256 is not hexadecimal") from exc
        if not path.is_file() or sha256_file(path) != expected:
            raise ValueError(f"pinned tool path/hash mismatch: {path}")
    input_item = plan.get("input")
    if not isinstance(input_item, dict):
        raise ValueError("missing pinned input")
    input_path = Path(input_item.get("path", "")).resolve(strict=True)
    if not input_path.is_file() or sha256_file(input_path) != input_item.get("sha256"):
        raise ValueError("pinned media input path/hash mismatch")
    reference = plan.get("software_reference")
    if not isinstance(reference, dict):
        raise ValueError("missing exact software NV12 reference capture")
    reference_manifest_path = Path(reference.get("manifest_path", "")).resolve(strict=True)
    reference_container_path = Path(reference.get("container_path", "")).resolve(strict=True)
    if sha256_file(reference_manifest_path) != reference.get("manifest_sha256"):
        raise ValueError("software reference manifest SHA-256 mismatch")
    if sha256_file(reference_container_path) != reference.get("container_sha256"):
        raise ValueError("software reference container SHA-256 mismatch")
    reference_manifest = load_json(reference_manifest_path)
    if (
        reference_manifest.get("schema") != 1
        or reference_manifest.get("mode") != "software"
        or reference_manifest.get("input_sha256") != input_item.get("sha256")
        or reference_manifest.get("output") != reference_container_path.name
        or reference_manifest.get("output_sha256") != reference.get("container_sha256")
    ):
        raise ValueError("software reference manifest does not describe the pinned capture")
    module_path = Path(plan.get("module_install_path", ""))
    initramfs_path = Path(plan.get("initramfs_path", ""))
    if not module_path.is_absolute() or not module_path.name.endswith("nouveau.ko.zst"):
        raise ValueError("expected installed module path must be absolute .ko.zst")
    if initramfs_path != Path(f"/boot/initrd.img-{kernel}"):
        raise ValueError("initramfs path must match the pinned kernel release")
    return {
        "kernel_release": kernel,
        "review_branch": build["review_branch"],
        "review_commit": build["review_commit"],
        "main_commit": build["main_commit"],
        "module_install_path": str(module_path),
        "initramfs_path": str(initramfs_path),
        "mesa_variants": plan["mesa_variants"],
        "tools": plan["tools"],
        "input": input_item,
        "software_reference": reference,
        "module_parameters": params,
    }


def secure_boot_enabled() -> bool:
    variable = Path(
        "/sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"
    )
    if not Path("/sys/firmware/efi").is_dir():
        return False
    if not variable.is_file():
        raise RuntimeError("cannot determine Secure Boot state from EFI variables")
    payload = variable.read_bytes()
    if len(payload) < 5:
        raise RuntimeError("SecureBoot EFI variable is truncated")
    return payload[4] == 1


def decompress_module(path: Path, destination: Path) -> None:
    if path.suffix == ".zst":
        command = ["zstd", "--quiet", "--decompress", "--force", "-o", str(destination), str(path)]
    elif path.suffix == ".xz":
        command = ["xz", "--decompress", "--force", "--stdout", str(path)]
    elif path.suffix == ".gz":
        command = ["gzip", "--decompress", "--stdout", str(path)]
    else:
        shutil.copy2(path, destination)
        return
    if path.suffix == ".zst":
        subprocess.run(command, check=True, timeout=60)
    else:
        with destination.open("wb") as output:
            subprocess.run(command, check=True, stdout=output, timeout=60)


def compress_module(source: Path, destination: Path) -> None:
    subprocess.run(
        [
            "zstd", "--quiet", "--compress", "--threads=1",
            "--force", "-o", str(destination), str(source),
        ],
        check=True,
        timeout=300,
    )


def embedded_module_hashes(initramfs: Path, temporary_root: Path) -> list[dict[str, str]]:
    extract = temporary_root / "initramfs"
    extract.mkdir()
    subprocess.run(
        ["unmkinitramfs", str(initramfs), str(extract)],
        check=True,
        timeout=180,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    candidates = sorted(
        path for path in extract.rglob("nouveau.ko*") if path.is_file()
    )
    results: list[dict[str, str]] = []
    for index, path in enumerate(candidates):
        raw_path = temporary_root / f"embedded-{index}.ko"
        decompress_module(path, raw_path)
        results.append({
            "path_in_initramfs": path.relative_to(extract).as_posix(),
            "uncompressed_sha256": sha256_file(raw_path),
        })
    if not results:
        raise RuntimeError("rebuilt initramfs contains no Nouveau module")
    return results


def build_final_manifest(
    *,
    build: dict[str, Any],
    build_manifest_path: Path,
    build_manifest_sha256: str,
    deployment_plan_path: Path,
    deployment_plan_sha256: str,
    observed_module_path: Path,
    installed_uncompressed_sha256: str,
    compressed_sha256: str,
    initramfs_path: Path,
    initramfs_sha256: str,
    embedded: list[dict[str, str]],
    plan: dict[str, Any],
    signed: bool,
    certificate_sha256: str | None,
) -> dict[str, Any]:
    embedded_hashes = {item["uncompressed_sha256"] for item in embedded}
    if embedded_hashes != {installed_uncompressed_sha256}:
        raise RuntimeError(
            "initramfs Nouveau module bytes do not exactly match installed module"
        )
    if observed_module_path.resolve() != Path(plan["module_install_path"]).resolve():
        raise RuntimeError("modinfo-selected path does not match deployment plan")
    return {
        "schema": 2,
        "status": "FINALIZED_NOT_REBOOTED_NOT_RUNTIME_VERIFIED",
        "review_commit": build["review_commit"],
        "review_branch": build["review_branch"],
        "main_commit": build["main_commit"],
        "build_manifest": {
            "path": str(build_manifest_path.resolve()),
            "sha256": build_manifest_sha256,
        },
        "deployment_plan": {
            "path": str(deployment_plan_path.resolve()),
            "sha256": deployment_plan_sha256,
        },
        "kernel": build["kernel_release"],
        "source": {
            "kernel_source_archive_sha256": build["kernel_source_archive_sha256"],
            "source_sha256": build["source_overlay_tree"]["source_tree_sha256"],
            "source_overlay_tree_sha256": build["source_overlay_tree"]["source_tree_sha256"],
            "patch_sha256": build["patch_sha256"],
            "patch_0009_sha256": build["patch_sha256"]["0009-drm-nouveau-log-nvif-duplicate-layer.patch"],
            "patch_0010_sha256": build["patch_sha256"]["0010-drm-nouveau-correlate-instmem-vma-selftest.patch"],
        },
        "nouveau": {
            "module_path": str(observed_module_path.resolve()),
            "expected_installed_module_path": str(observed_module_path.resolve()),
            "raw_module_sha256": build["raw_module_sha256"],
            "installed_uncompressed_sha256": installed_uncompressed_sha256,
            "installed_compressed_sha256": compressed_sha256,
            "expected_compressed_module_sha256": compressed_sha256,
            "srcversion": build["srcversion"],
            "vermagic": build["vermagic"],
            "signed": signed,
            "signing_certificate_sha256": certificate_sha256,
            "parameters": plan["module_parameters"],
            "module_parameters": plan["module_parameters"],
        },
        "initramfs": {
            "path": str(initramfs_path.resolve()),
            "sha256": initramfs_sha256,
            "embedded_nouveau_modules": embedded,
            "embedded_uncompressed_sha256": installed_uncompressed_sha256,
        },
        "mesa": {
            "variants": plan["mesa_variants"],
        },
        "tools": {
            name: {
                "path": str(Path(value["path"]).resolve()),
                "sha256": sha256_file(Path(value["path"]).resolve(strict=True)),
            }
            for name, value in plan["tools"].items()
        },
        "input": {
            "path": str(Path(plan["input"]["path"]).resolve()),
            "sha256": plan["input"]["sha256"],
        },
        "software_reference": {
            "manifest_path": str(
                Path(plan["software_reference"]["manifest_path"]).resolve()
            ),
            "manifest_sha256": plan["software_reference"]["manifest_sha256"],
            "container_path": str(
                Path(plan["software_reference"]["container_path"]).resolve()
            ),
            "container_sha256": plan["software_reference"]["container_sha256"],
        },
    }


def apply_install(
    *,
    build: dict[str, Any],
    build_manifest_path: Path,
    build_manifest_sha256: str,
    plan: dict[str, Any],
    deployment_plan_path: Path,
    deployment_plan_sha256: str,
    raw_module: Path,
    manifest_path: Path,
    signing_key: Path | None,
    signing_cert: Path | None,
) -> dict[str, Any]:
    if os.geteuid() != 0:
        raise RuntimeError("--apply requires root; run this only from the controlled desktop")
    if manifest_path.exists():
        raise RuntimeError(f"refusing to overwrite finalized deployment manifest {manifest_path}")
    kernel = plan["kernel_release"]
    if command_output(["uname", "-r"]) != kernel:
        raise RuntimeError("running kernel is not the deployment target")
    secure = secure_boot_enabled()
    if secure and (signing_key is None or signing_cert is None):
        raise RuntimeError("Secure Boot is enabled; provide the enrolled module signing key and certificate")
    if (signing_key is None) != (signing_cert is None):
        raise RuntimeError("signing key and certificate must be supplied together")

    required = ("zstd", "depmod", "update-initramfs", "unmkinitramfs", "modinfo")
    missing = [name for name in required if shutil.which(name) is None]
    if missing:
        raise RuntimeError("required deployment command missing: " + ", ".join(missing))
    sign_file = Path(f"/usr/src/linux-headers-{kernel}/scripts/sign-file")
    if signing_key is not None:
        if not sign_file.is_file() or not signing_key.is_file() or not signing_cert.is_file():
            raise RuntimeError("signing helper/key/certificate is missing")
    if secure:
        mokutil = shutil.which("mokutil")
        if not mokutil:
            raise RuntimeError("Secure Boot is enabled but mokutil cannot verify certificate enrollment")
        enrollment = subprocess.run(
            [mokutil, "--test-key", str(signing_cert)],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if enrollment.returncode:
            raise RuntimeError("module signing certificate is not reported enrolled by mokutil")

    target = Path(plan["module_install_path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    initramfs = Path(plan["initramfs_path"])
    with tempfile.TemporaryDirectory(prefix="nouveau-deploy-") as temporary:
        root = Path(temporary)
        prepared = root / "nouveau.ko"
        shutil.copy2(raw_module, prepared)
        if signing_key is not None and signing_cert is not None:
            subprocess.run(
                [str(sign_file), "sha256", str(signing_key), str(signing_cert), str(prepared)],
                check=True,
                timeout=60,
            )
        if command_output(["modinfo", "-F", "srcversion", str(prepared)]) != build["srcversion"]:
            raise RuntimeError("prepared module srcversion changed")
        if command_output(["modinfo", "-F", "vermagic", str(prepared)]) != build["vermagic"]:
            raise RuntimeError("prepared module vermagic changed")
        uncompressed_sha = sha256_file(prepared)
        compressed_temp = root / "nouveau.ko.zst"
        compress_module(prepared, compressed_temp)
        target_temp = target.with_name(f".{target.name}.prepared")
        shutil.copy2(compressed_temp, target_temp)
        os.chmod(target_temp, 0o644)
        os.replace(target_temp, target)
        subprocess.run(["depmod", "-a", kernel], check=True, timeout=60)
        subprocess.run(["update-initramfs", "-u", "-k", kernel], check=True, timeout=300)

        selected = Path(command_output(["modinfo", "-n", "nouveau"])).resolve(strict=True)
        if selected != target.resolve(strict=True):
            raise RuntimeError(f"modinfo selected {selected}, expected {target}")
        installed_uncompressed = root / "installed-nouveau.ko"
        decompress_module(selected, installed_uncompressed)
        if sha256_file(installed_uncompressed) != uncompressed_sha:
            raise RuntimeError("installed compressed module does not match prepared bytes")
        if command_output(["modinfo", "-F", "srcversion", str(selected)]) != build["srcversion"]:
            raise RuntimeError("installed module srcversion mismatch")
        if not initramfs.is_file():
            raise RuntimeError(f"rebuilt initramfs is missing: {initramfs}")
        embedded = embedded_module_hashes(initramfs, root)
        final = build_final_manifest(
            build=build,
            build_manifest_path=build_manifest_path,
            build_manifest_sha256=build_manifest_sha256,
            deployment_plan_path=deployment_plan_path,
            deployment_plan_sha256=deployment_plan_sha256,
            observed_module_path=selected,
            installed_uncompressed_sha256=uncompressed_sha,
            compressed_sha256=sha256_file(selected),
            initramfs_path=initramfs,
            initramfs_sha256=sha256_file(initramfs),
            embedded=embedded,
            plan=plan,
            signed=signing_key is not None,
            certificate_sha256=(
                sha256_file(signing_cert) if signing_cert is not None else None
            ),
        )
        atomic_write(
            manifest_path,
            (json.dumps(final, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )
        return final


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-manifest", type=Path, required=True)
    parser.add_argument("--plan", type=Path, default=Path(__file__).with_name("deployment-plan.json"))
    parser.add_argument("--raw-module", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path, required=True)
    parser.add_argument("--signing-key", type=Path)
    parser.add_argument("--signing-cert", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        build = load_json(args.build_manifest)
        plan = load_json(args.plan)
        values = validate_inputs(build, plan, args.raw_module.resolve(strict=True))
        if not args.apply:
            print("INSTALL_PLAN_ONLY=true")
            print(f"KERNEL={values['kernel_release']}")
            print(f"MODULE_TARGET={values['module_install_path']}")
            print(f"INITRAMFS_TARGET={values['initramfs_path']}")
            print("NO_SYSTEM_CHANGES=true")
            print("To install, sign if required, rebuild initramfs, and finalize provenance, rerun with --apply as root from the controlled desktop.")
            return 0
        final = apply_install(
            build=build,
            build_manifest_path=args.build_manifest.resolve(strict=True),
            build_manifest_sha256=sha256_file(args.build_manifest.resolve(strict=True)),
            plan=plan,
            deployment_plan_path=args.plan.resolve(strict=True),
            deployment_plan_sha256=sha256_file(args.plan.resolve(strict=True)),
            raw_module=args.raw_module.resolve(strict=True),
            manifest_path=args.manifest_output.resolve(),
            signing_key=args.signing_key.resolve(strict=True) if args.signing_key else None,
            signing_cert=args.signing_cert.resolve(strict=True) if args.signing_cert else None,
        )
        print(json.dumps(final, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
