#!/usr/bin/env python3
"""Exercise capture exit precedence without a GPU or DKMS installation."""

from pathlib import Path
import os
import subprocess
import tempfile


REPO = Path(__file__).resolve().parents[1]
REAL_HARNESS = REPO / "tools/vaapi-capture.sh"
TRACE_HOOK = "NOUVEAU_DIAG_VA_SURFACE hook=active point=nvc0_init_surface_functions"
TRACE_ALLOCATE = "NOUVEAU_DIAG_VA_SURFACE phase=allocate-entry"


def write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def run_case(
    harness: Path,
    dri_dir: Path,
    fake_bin: Path,
    capture_root: Path,
    name: str,
    ffmpeg_exit: int,
    trace: bool,
    expected_status: int,
    expected_stop: str,
    expect_invalid_trace: bool,
) -> None:
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "NOUVEAU_CAPTURE_ROOT": str(capture_root / name),
            "TEST_FFMPEG_EXIT": str(ffmpeg_exit),
            "TEST_TRACE": "1" if trace else "0",
        }
    )
    result = subprocess.run(
        [str(harness), "private-instrumented", str(dri_dir)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == expected_status, (
        f"{name}: expected exit {expected_status}, got {result.returncode}\n{output}"
    )
    assert expected_stop in output, f"{name}: missing {expected_stop!r}\n{output}"
    if expect_invalid_trace:
        assert "capture invalid: instrumented Mesa trace was not positively exercised" in output
    else:
        assert "capture invalid: instrumented Mesa trace was not positively exercised" not in output


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="vaapi-capture-exit-order-") as temporary:
        root = Path(temporary)
        fake_bin = root / "bin"
        fake_bin.mkdir()

        input_path = root / "test_1080p.mkv"
        input_path.write_bytes(b"mock input")
        source = REAL_HARNESS.read_text()
        source = source.replace(
            "input=/home/keivan/test_1080p.mkv", f"input={input_path}"
        )
        assert source != REAL_HARNESS.read_text(), "test input path replacement failed"
        harness = root / "vaapi-capture.sh"
        write_executable(harness, source)

        dri_dir = root / "dri"
        dri_dir.mkdir()
        (dri_dir / "nouveau_drv_video.so").touch()

        write_executable(
            fake_bin / "journalctl",
            "#!/bin/sh\nexec /bin/sleep 300\n",
        )
        write_executable(
            fake_bin / "modinfo",
            "#!/bin/sh\n"
            "case \"$1\" in\n"
            "  -n) echo /lib/modules/test/updates/dkms/nouveau.ko.zst ;;\n"
            "  -F) echo TEST-SRCVERSION ;;\n"
            "  *) exit 2 ;;\n"
            "esac\n",
        )
        write_executable(
            fake_bin / "cat",
            "#!/bin/sh\n"
            "if [ \"${1:-}\" = /sys/module/nouveau/srcversion ]; then\n"
            "  echo TEST-SRCVERSION\n"
            "else\n"
            "  exec /bin/cat \"$@\"\n"
            "fi\n",
        )
        write_executable(
            fake_bin / "sleep",
            "#!/bin/sh\n"
            "if [ \"${1:-}\" = 15 ]; then exit 0; fi\n"
            "exec /bin/sleep 0.05\n",
        )
        write_executable(
            fake_bin / "ffmpeg",
            "#!/bin/sh\n"
            "printf 'libva: Trying to open %s/nouveau_drv_video.so\\n' \"$LIBVA_DRIVERS_PATH\"\n"
            "echo 'libva: va_openDriver() returns 0'\n"
            "echo 'VAAPI driver: mocked Nouveau'\n"
            "if [ \"${TEST_TRACE:-0}\" = 1 ]; then\n"
            f"  echo '{TRACE_HOOK}'\n"
            f"  echo '{TRACE_ALLOCATE}'\n"
            "fi\n"
            "exit \"${TEST_FFMPEG_EXIT:-0}\"\n",
        )

        capture_root = root / "captures"
        run_case(
            harness,
            dri_dir,
            fake_bin,
            capture_root,
            "failed-before-trace",
            135,
            False,
            135,
            "STOP_A_B=1 nonzero FFmpeg exit",
            True,
        )
        print("PASS: instrumented FFmpeg exit 135 is preserved when trace markers are absent")

        run_case(
            harness,
            dri_dir,
            fake_bin,
            capture_root,
            "success-before-trace",
            0,
            False,
            4,
            "STOP_A_B=0",
            True,
        )
        print("PASS: successful instrumented run without trace markers exits 4")

        run_case(
            harness,
            dri_dir,
            fake_bin,
            capture_root,
            "success-with-trace",
            0,
            True,
            0,
            "STOP_A_B=0",
            False,
        )
        print("PASS: successful instrumented run with both trace markers exits 0")


if __name__ == "__main__":
    main()
