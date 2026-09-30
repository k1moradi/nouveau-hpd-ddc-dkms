#!/usr/bin/env python3
"""Exercise capture exit precedence without a GPU or DKMS installation."""

from pathlib import Path
import fcntl
import os
import subprocess
import tempfile
import time


REPO = Path(__file__).resolve().parents[1]
REAL_HARNESS = REPO / "tools/vaapi-capture.sh"
LOW_MEMORY_SCRIPT = Path(
    os.environ.get("NOUVEAU_CLI_LOW_MEMORY_SCRIPT", "/home/keivan/cli-low-memory.sh")
)
TRACE_HOOK = "NOUVEAU_DIAG_VA_SURFACE mono_ns=123456 hook=active point=nvc0_init_surface_functions"
TRACE_ALLOCATE = "NOUVEAU_DIAG_VA_SURFACE mono_ns=123457 phase=allocate-entry"


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
    expect_display_manager_change: bool = False,
    capture_frames: int | None = None,
) -> None:
    env = os.environ.copy()
    env.pop("NOUVEAU_CAPTURE_FRAMES", None)
    args_file = capture_root / f"{name}-ffmpeg-args.txt"
    args_file.parent.mkdir(parents=True, exist_ok=True)
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "NOUVEAU_CAPTURE_ROOT": str(capture_root / name),
            "HOME": str(capture_root / name / "home"),
            "TEST_FFMPEG_EXIT": str(ffmpeg_exit),
            "TEST_TRACE": "1" if trace else "0",
            "TEST_FFMPEG_ARGS_FILE": str(args_file),
        }
    )
    if capture_frames is not None:
        env["NOUVEAU_CAPTURE_FRAMES"] = str(capture_frames)
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
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
    recorded_args = args_file.read_text().splitlines()
    frames_index = recorded_args.index("-frames:v")
    expected_frames = 3000 if capture_frames is None else capture_frames
    assert recorded_args[frames_index + 1] == str(expected_frames), (
        f"{name}: expected FFmpeg frame limit {expected_frames}, "
        f"got {recorded_args[frames_index + 1]}"
    )
    if expect_invalid_trace:
        assert "capture invalid: instrumented Mesa trace was not positively exercised" in output
    else:
        assert "capture invalid: instrumented Mesa trace was not positively exercised" not in output
    if expect_display_manager_change:
        assert "capture invalid: SDDM/display-manager state changed during capture" in output
    else:
        assert "capture invalid: SDDM/display-manager state changed during capture" not in output


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
            "#!/bin/sh\n"
            "case \" $* \" in *' -b '*) echo '0.100000 boot-kernel-marker'; exit 0 ;; esac\n"
            "if [ -n \"${TEST_JOURNAL_STARTED:-}\" ]; then : > \"$TEST_JOURNAL_STARTED\"; fi\n"
            "exec /bin/sleep 300\n",
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
            fake_bin / "systemctl",
            "#!/bin/sh\n"
            "if [ \"${1:-}\" = is-active ] && [ \"${2:-}\" = sddm.service ]; then\n"
            "  if [ \"${TEST_SDDM_TRANSITION:-0}\" = 1 ]; then\n"
            "    count=0; [ -f \"$TEST_SDDM_COUNT_FILE\" ] && count=$(cat \"$TEST_SDDM_COUNT_FILE\")\n"
            "    count=$((count + 1)); printf '%s' \"$count\" > \"$TEST_SDDM_COUNT_FILE\"\n"
            "    if [ \"$count\" -eq 1 ]; then echo active; else echo inactive; exit 3; fi\n"
            "  else echo inactive; exit 3; fi\n"
            "else echo inactive; exit 3; fi\n",
        )
        write_executable(
            fake_bin / "ffmpeg",
            "#!/bin/sh\n"
            "printf 'libva: Trying to open %s/nouveau_drv_video.so\\n' \"$LIBVA_DRIVERS_PATH\"\n"
            "echo 'libva: va_openDriver() returns 0'\n"
            "echo 'VAAPI driver: mocked Nouveau'\n"
            "if [ -n \"${TEST_FFMPEG_STARTED:-}\" ]; then : > \"$TEST_FFMPEG_STARTED\"; fi\n"
            "if [ -n \"${TEST_FFMPEG_ARGS_FILE:-}\" ]; then printf '%s\\n' \"$@\" > \"$TEST_FFMPEG_ARGS_FILE\"; fi\n"
            "if [ \"${TEST_TRACE:-0}\" = 1 ]; then\n"
            f"  echo '{TRACE_HOOK}'\n"
            f"  echo '{TRACE_ALLOCATE}'\n"
            "fi\n"
            "if [ -n \"${TEST_FFMPEG_RELEASE:-}\" ]; then\n"
            "  while [ ! -e \"$TEST_FFMPEG_RELEASE\" ]; do /bin/sleep 0.05; done\n"
            "fi\n"
            "exit \"${TEST_FFMPEG_EXIT:-0}\"\n",
        )

        # The low-memory helper must refuse to stop SDDM/services while a
        # capture holds the exclusive lock. This exits before service work.
        if LOW_MEMORY_SCRIPT.is_file():
            guard_home = root / "guard-home"
            guard_lock = (
                guard_home
                / ".cache/nouveau-vaapi-followup-20260928/hardware-capture.lock"
            )
            guard_lock.parent.mkdir(parents=True)
            guard_home.mkdir(parents=True, exist_ok=True)
            lock_fd = guard_lock.open("w")
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            guard_env = os.environ.copy()
            guard_env["HOME"] = str(guard_home)
            guard_result = subprocess.run(
                [str(LOW_MEMORY_SCRIPT)],
                env=guard_env,
                text=True,
                capture_output=True,
                check=False,
            )
            assert guard_result.returncode == 3, (
                "low-memory helper should refuse while a capture lock is held: "
                f"{guard_result.stdout}{guard_result.stderr}"
            )
            assert "a Nouveau VA-API capture is active" in guard_result.stderr
            lock_fd.close()
            print("PASS: cli-low-memory.sh refuses to stop SDDM during a capture lock")
        else:
            print(f"SKIP: low-memory helper not found: {LOW_MEMORY_SCRIPT}")

        # Conversely, an exclusive maintenance lock prevents the capture from
        # starting, so the kernel logger and FFmpeg never overlap maintenance.
        capture_home = root / "maintenance-home"
        capture_lock = (
            capture_home
            / ".cache/nouveau-vaapi-followup-20260928/hardware-capture.lock"
        )
        capture_lock.parent.mkdir(parents=True)
        capture_home.mkdir(parents=True, exist_ok=True)
        lock_fd = capture_lock.open("w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        capture_env = os.environ.copy()
        capture_env.update(
            {
                "PATH": f"{fake_bin}:{capture_env['PATH']}",
                "HOME": str(capture_home),
                "NOUVEAU_CAPTURE_ROOT": str(root / "blocked-capture"),
            }
        )
        blocked = subprocess.run(
            [str(harness), "private-instrumented", str(dri_dir)],
            env=capture_env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert blocked.returncode == 3, (
            f"capture should refuse maintenance lock: {blocked.stdout}{blocked.stderr}"
        )
        assert "another hardware capture or low-memory maintenance operation" in blocked.stderr
        lock_fd.close()
        print("PASS: VA-API harness refuses to start during low-memory maintenance")

        # A live first capture owns the lock. A concurrent second capture must
        # be rejected before it starts its journal follower or FFmpeg process.
        concurrent_home = root / "concurrent-home"
        concurrent_home.mkdir(parents=True, exist_ok=True)
        capture_a_started = root / "capture-a-ffmpeg-started"
        capture_a_release = root / "capture-a-release"
        capture_a_journal = root / "capture-a-journal-started"
        capture_b_started = root / "capture-b-ffmpeg-started"
        capture_b_journal = root / "capture-b-journal-started"
        capture_a_env = os.environ.copy()
        capture_a_env.update(
            {
                "PATH": f"{fake_bin}:{capture_a_env['PATH']}",
                "HOME": str(concurrent_home),
                "NOUVEAU_CAPTURE_ROOT": str(root / "capture-a"),
                "TEST_TRACE": "1",
                "TEST_FFMPEG_STARTED": str(capture_a_started),
                "TEST_FFMPEG_RELEASE": str(capture_a_release),
                "TEST_JOURNAL_STARTED": str(capture_a_journal),
            }
        )
        capture_a = subprocess.Popen(
            [str(harness), "private-instrumented", str(dri_dir)],
            env=capture_a_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and capture_a.poll() is None:
                if capture_a_started.exists():
                    break
                time.sleep(0.02)
            assert capture_a_started.exists(), "first capture never reached FFmpeg"
            assert capture_a_journal.exists(), "first capture logger did not start"

            capture_b_env = os.environ.copy()
            capture_b_env.update(
                {
                    "PATH": f"{fake_bin}:{capture_b_env['PATH']}",
                    "HOME": str(concurrent_home),
                    "NOUVEAU_CAPTURE_ROOT": str(root / "capture-b"),
                    "TEST_TRACE": "1",
                    "TEST_FFMPEG_STARTED": str(capture_b_started),
                    "TEST_JOURNAL_STARTED": str(capture_b_journal),
                }
            )
            capture_b = subprocess.run(
                [str(harness), "private-instrumented", str(dri_dir)],
                env=capture_b_env,
                text=True,
                capture_output=True,
                check=False,
            )
            assert capture_b.returncode == 3, (
                "concurrent capture should be refused: "
                f"{capture_b.stdout}{capture_b.stderr}"
            )
            assert "another hardware capture or low-memory maintenance operation" in capture_b.stderr
            assert not capture_b_journal.exists(), "second capture started its journal follower"
            assert not capture_b_started.exists(), "second capture started FFmpeg"
        finally:
            capture_a_release.touch()
            try:
                capture_a_output, _ = capture_a.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                capture_a.kill()
                capture_a_output, _ = capture_a.communicate()
                raise AssertionError(f"first capture did not finish:\n{capture_a_output}")
        assert capture_a.returncode == 0, (
            f"first serialized capture failed ({capture_a.returncode}):\n{capture_a_output}"
        )
        assert "STOP_A_B=0" in capture_a_output
        print("PASS: concurrent hardware capture is refused before logger or FFmpeg starts")

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
            "STOP_A_B=1 FFmpeg failure",
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
            capture_frames=40561,
        )
        print("PASS: successful instrumented run with both trace markers exits 0")

        boot_capture_root = root / "captures-with-boot-journal"
        boot_home = root / "boot-journal-home"
        boot_home.mkdir(parents=True, exist_ok=True)
        boot_env = os.environ.copy()
        boot_env.update(
            {
                "PATH": f"{fake_bin}:{boot_env['PATH']}",
                "HOME": str(boot_home),
                "NOUVEAU_CAPTURE_ROOT": str(boot_capture_root),
                "NOUVEAU_CAPTURE_BOOT_KERNEL_LOG": "1",
                "TEST_FFMPEG_EXIT": "0",
                "TEST_TRACE": "1",
            }
        )
        boot_capture = subprocess.run(
            [str(harness), "private-instrumented", str(dri_dir)],
            env=boot_env,
            text=True,
            capture_output=True,
            check=False,
        )
        boot_output = boot_capture.stdout + boot_capture.stderr
        assert boot_capture.returncode == 0, (
            "boot-journal capture should succeed: "
            f"{boot_output}"
        )
        assert "boot_kernel_snapshot=enabled" in boot_output
        boot_run_line = next(
            line for line in boot_output.splitlines()
            if line.startswith("capture_directory=")
        )
        boot_run_dir = Path(boot_run_line.split("=", 1)[1])
        assert (boot_run_dir / "boot-kernel.log").read_text().strip() == (
            "0.100000 boot-kernel-marker"
        )
        sums = (boot_run_dir / "SHA256SUMS").read_text()
        assert "boot-kernel.log" in sums
        assert "kernel.log" in sums and "ffmpeg.log" in sums
        print("PASS: optional current-boot kernel snapshot is captured and hashed")

        transition_capture_root = root / "captures-display-manager-change"
        transition_home = transition_capture_root / "display-manager-transition" / "home"
        transition_home.mkdir(parents=True, exist_ok=True)
        transition_env = os.environ.copy()
        transition_env.update(
            {
                "PATH": f"{fake_bin}:{transition_env['PATH']}",
                "HOME": str(transition_home),
                "NOUVEAU_CAPTURE_ROOT": str(transition_capture_root),
                "TEST_FFMPEG_EXIT": "0",
                "TEST_TRACE": "1",
                "TEST_SDDM_TRANSITION": "1",
                "TEST_SDDM_COUNT_FILE": str(root / "sddm-count"),
            }
        )
        transition = subprocess.run(
            [str(harness), "private-instrumented", str(dri_dir)],
            env=transition_env,
            text=True,
            capture_output=True,
            check=False,
        )
        transition_output = transition.stdout + transition.stderr
        assert transition.returncode == 4, (
            "display-manager transition should invalidate a successful capture: "
            f"{transition_output}"
        )
        assert "sddm_state_start=active" in transition_output
        assert "sddm_state_end=inactive" in transition_output
        assert "STOP_A_B=1" in transition_output
        print("PASS: display-manager transitions invalidate an otherwise successful capture")


if __name__ == "__main__":
    main()
