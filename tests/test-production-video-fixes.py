#!/usr/bin/env python3
"""Exercise fail-closed Nouveau video-patch classification and packaging."""

from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]

GK104_COMMON = """
int gk104_ectx_ctor(struct nvkm_engn *engn, struct nvkm_vctx *vctx)
{
    struct gf100_vmm_map_v0 args = { .priv = 1 };
    int ret;
    ret = nvkm_vmm_get(vctx->vmm, 12, vctx->inst->size, &vctx->vma);
    if (ret)
        return ret;
    return nvkm_memory_map(vctx->vmm, 0, 0, &args, sizeof(args));
}
"""

GK104_FIXED = GK104_COMMON.replace(
    "    return nvkm_memory_map",
    """    switch (engn->engine->subdev.type) {
    case NVKM_ENGINE_MSPDEC:
    case NVKM_ENGINE_MSPPP:
    case NVKM_ENGINE_MSVLD:
        args.priv = 0;
        break;
    default:
        break;
    }
    return nvkm_memory_map""",
)

NONSTALL_VULNERABLE = """
static int nvkm_uchan_uevent(struct nvif_event *args)
{
    switch (args->v0.type) {
    case NVIF_CHAN_EVENT_V0_NON_STALL_INTR:
        return nvkm_uevent_add(uevent, &runl->fifo->nonstall.event,
                               runl->id, NVKM_FIFO_NONSTALL_EVENT, NULL);
    }
}
"""

NONSTALL_FIXED = NONSTALL_VULNERABLE.replace(
    "runl->id, NVKM_FIFO_NONSTALL_EVENT",
    "runl->fifo->func->nonstall_ctor ? runl->id : 0, NVKM_FIFO_NONSTALL_EVENT",
)


def check_classifier(script: Path, source_name: str, cases: dict[str, str]) -> None:
    for expected, source in cases.items():
        with tempfile.TemporaryDirectory(prefix="nouveau-video-classifier-") as temp:
            source_path = Path(temp) / source_name
            source_path.write_text(source)
            result = subprocess.run(
                [sys.executable, str(script), str(source_path)],
                text=True,
                capture_output=True,
                check=False,
            )
        if expected == "unknown":
            if result.returncode == 0 or "unknown:" not in result.stderr:
                raise AssertionError(
                    f"{script.name} accepted unknown source: "
                    f"rc={result.returncode} stdout={result.stdout!r} "
                    f"stderr={result.stderr!r}"
                )
        elif result.returncode != 0 or result.stdout.strip() != expected:
            raise AssertionError(
                f"{script.name} expected {expected}, got "
                f"rc={result.returncode} stdout={result.stdout!r} "
                f"stderr={result.stderr!r}"
            )


def main() -> None:
    check_classifier(
        ROOT / "dkms/check-gk104-video-context.py",
        "gk104.c",
        {
            "vulnerable": GK104_COMMON,
            "fixed": GK104_FIXED,
            "unknown": GK104_COMMON.replace("nvkm_vmm_get", "nvkm_vmm_new"),
        },
    )
    check_classifier(
        ROOT / "dkms/check-legacy-fifo-nonstall.py",
        "uchan.c",
        {
            "vulnerable": NONSTALL_VULNERABLE,
            "fixed": NONSTALL_FIXED,
            "unknown": NONSTALL_VULNERABLE.replace("runl->id,", "17,"),
        },
    )

    build = (ROOT / "dkms/dkms-build.sh").read_text()
    for marker in (
        "experimental-legacy-nonstall.enabled",
        "diagnostic-legacy-video.enabled",
        "diag_fence_wait",
        "diag_ctxsw",
        "diag_bar2_map",
    ):
        if marker in build:
            raise AssertionError(f"normal video build must not contain gate {marker!r}")
    for required in (
        "check-gk104-video-context.py",
        "check-legacy-fifo-nonstall.py",
        "gk104-legacy-video-context-nonpriv.patch",
        "legacy-fifo-nonstall-event-index.patch",
        "patch --fuzz=0",
    ):
        if required not in build:
            raise AssertionError(f"production DKMS build is missing {required!r}")

    for path in (
        ROOT / "dkms/dkms.conf",
        ROOT / "install.sh",
        ROOT / "verify-after-reboot.sh",
        ROOT / "debian/DEBIAN/control",
        ROOT / "debian/DEBIAN/postinst",
    ):
        if "0.1.14" not in path.read_text():
            raise AssertionError(f"{path.relative_to(ROOT)} is not versioned 0.1.14")

    for path in (ROOT / "install.sh", ROOT / "build-deb.sh"):
        text = path.read_text()
        for patch_name in (
            "gk104-legacy-video-context-nonpriv.patch",
            "legacy-fifo-nonstall-event-index.patch",
        ):
            if patch_name not in text:
                raise AssertionError(f"{path.name} does not stage {patch_name}")

    installer = (ROOT / "install.sh").read_text()
    build_pos = installer.index('dkms build -m "$NAME" -v "$VER" -k "$k"')
    old_cleanup_pos = installer.index('for oldver in 0.1.0')
    install_pos = installer.index('dkms install -m "$NAME" -v "$VER" -k "$k"')
    if not build_pos < old_cleanup_pos < install_pos:
        raise AssertionError("installer must build .14 before removing old DKMS versions")

    if "refresh-initramfs.sh" not in (ROOT / "install.sh").read_text():
        raise AssertionError("installer does not require the initramfs refresh helper")
    for path in (ROOT / "dkms/dkms-post-install.sh", ROOT / "dkms/dkms-post-remove.sh"):
        if "refresh-initramfs.sh" not in path.read_text():
            raise AssertionError(f"{path.name} does not use the initramfs refresh helper")

    print("PASS: GK104 classifier accepts only vulnerable/fixed states")
    print("PASS: nonstall classifier accepts only vulnerable/fixed states")
    print("PASS: production DKMS applies both fixes without diagnostic gates")
    print("PASS: DKMS/deb version and staged patch inputs are consistent")


if __name__ == "__main__":
    main()
