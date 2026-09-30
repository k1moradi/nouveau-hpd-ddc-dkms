#!/usr/bin/env python3
"""Verify initramfs refresh selects dracut or initramfs-tools safely."""

from pathlib import Path
import os
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "dkms/refresh-initramfs.sh"


def run_case(
    backend: str,
    *,
    auto_dracut: bool = False,
    kernel: str = "test-kernel",
    modules: tuple[str, ...] = ("test-kernel",),
    mode: str | None = None,
) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="nouveau-initramfs-test-") as temp:
        base = Path(temp)
        bindir = base / "bin"
        modules_root = base / "modules"
        boot = base / "boot"
        config = base / "etc"
        log = base / "commands.log"
        for directory in (bindir, boot, config):
            directory.mkdir(parents=True, exist_ok=True)
        for module in modules:
            (modules_root / module).mkdir(parents=True)
            (boot / f"initrd.img-{module}").touch()
        if auto_dracut:
            (config / "dracut.conf.d").mkdir()
        else:
            (config / "initramfs-tools").mkdir()
            (config / "initramfs-tools/initramfs.conf").touch()

        for command in ("dracut", "update-initramfs"):
            executable = bindir / command
            executable.write_text(
                "#!/bin/sh\n"
                f"printf '%s %s\\n' '{command}' \"$*\" >> \"$TEST_LOG\"\n"
            )
            executable.chmod(0o755)

        env = os.environ.copy()
        env.update({
            "PATH": f"{bindir}:{env['PATH']}",
            "TEST_LOG": str(log),
            "NOUVEAU_MODULES_DIR": str(modules_root),
            "NOUVEAU_BOOT_DIR": str(boot),
            "NOUVEAU_RUNNING_KERNEL": kernel,
            "NOUVEAU_DRACUT_CONF": str(config / "dracut.conf"),
            "NOUVEAU_DRACUT_CONF_DIR": str(config / "dracut.conf.d"),
            "NOUVEAU_INITRAMFS_TOOLS_CONFIG": str(config / "initramfs-tools/initramfs.conf"),
        })
        if backend != "auto":
            env["NOUVEAU_INITRAMFS_TOOL"] = backend

        subprocess.run(
            [str(HELPER), mode or kernel],
            check=True,
            env=env,
            text=True,
            capture_output=True,
        )
        return log.read_text().splitlines() if log.exists() else []


def main() -> None:
    dracut = run_case("dracut")
    if (
        len(dracut) != 1
        or not dracut[0].startswith("dracut --force ")
        or not dracut[0].endswith("/initrd.img-test-kernel test-kernel")
    ):
        raise AssertionError(f"forced dracut selection failed: {dracut!r}")

    automatic = run_case("auto", auto_dracut=True)
    if not automatic or not automatic[0].startswith("dracut --force "):
        raise AssertionError(f"auto did not select dracut configuration: {automatic!r}")

    initramfs_tools = run_case("auto", auto_dracut=False)
    if initramfs_tools != ["update-initramfs -u -k test-kernel"]:
        raise AssertionError(f"auto did not select initramfs-tools: {initramfs_tools!r}")

    others = run_case(
        "dracut", kernel="current", modules=("current", "old"), mode="others"
    )
    if len(others) != 1 or not others[0].endswith("/initrd.img-old old"):
        raise AssertionError(f"others mode did not skip the running kernel: {others!r}")

    only_current = run_case("dracut", kernel="current", modules=("current",), mode="others")
    if only_current:
        raise AssertionError(f"others mode unexpectedly refreshed the current image: {only_current!r}")

    print("PASS: explicit and auto dracut selection")
    print("PASS: auto initramfs-tools selection when its config is present")


if __name__ == "__main__":
    main()
