#!/usr/bin/env python3
"""Prepare and compare exact-PTS NV12 captures; hardware mode is opt-in."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from typing import Any


EXPECTED_INPUT_SHA256 = (
    "d1bab5275bcb585791fbfb15c801c1aab582256e7b7fca280c76f78a0a1c1ec2"
)
TARGET_PTS = (530.021, 530.063, 530.105, 530.146, 530.188)
CAPTURE_WINDOW_SECONDS = 0.010
PAIR_TOLERANCE_SECONDS = 0.002
INPUT_SEEK_START_SECONDS = 527.0
INPUT_DECODE_DURATION_SECONDS = 10.0
OUTPUT_TIME_BASE = "1:1000"
SOFTWARE_REFERENCE_CONTAINER_SHA256 = (
    "8c53f7ea946dbf36d9588aae95051e9f2b9bfbd92dfe83806ace8744bb029aa1"
)
SOFTWARE_REFERENCE_MANIFEST_SHA256 = (
    "b05d433fde7d37581464cafe356c68de6eab20a672ffa9c36b91758eb54b291d"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def select_expression(
    targets: tuple[float, ...] = TARGET_PTS,
    tolerance: float = CAPTURE_WINDOW_SECONDS,
) -> str:
    if tolerance <= 0:
        raise ValueError("capture tolerance must be positive")
    terms = [
        f"between(t,{target - tolerance:.6f},{target + tolerance:.6f})"
        for target in targets
    ]
    return "+".join(terms)


def capture_argv(
    *,
    mode: str,
    ffmpeg: str,
    input_path: Path,
    output_path: Path,
    device: str = "/dev/dri/renderD128",
) -> list[str]:
    if mode not in {"hardware", "software"}:
        raise ValueError("mode must be hardware or software")
    filter_graph = select_expression()
    argv = [
        ffmpeg, "-hide_banner", "-nostdin", "-nostats", "-loglevel", "warning",
        "-copyts", "-ss", f"{INPUT_SEEK_START_SECONDS:.3f}",
        "-t", f"{INPUT_DECODE_DURATION_SECONDS:.3f}",
    ]
    if mode == "hardware":
        argv.extend([
            "-init_hw_device", f"vaapi=va:{device}",
            "-filter_hw_device", "va",
            "-hwaccel", "vaapi",
            "-hwaccel_device", "va",
            "-hwaccel_output_format", "vaapi",
        ])
        filter_graph = f"hwdownload,format=nv12,select='{filter_graph}'"
    else:
        filter_graph = f"format=nv12,select='{filter_graph}'"
    argv.extend([
        "-i", str(input_path),
        "-map", "0:v:0", "-an", "-sn", "-dn",
        "-vf", filter_graph,
        "-fps_mode", "passthrough",
        "-frames:v", str(len(TARGET_PTS)),
        "-enc_time_base", OUTPUT_TIME_BASE,
        "-c:v", "rawvideo", "-pix_fmt", "nv12",
        "-f", "nut", str(output_path),
    ])
    return argv


def probe_frames(ffprobe: str, container: Path) -> list[dict[str, Any]]:
    result = subprocess.run(
        [
            ffprobe, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "frame=best_effort_timestamp_time,width,height",
            "-of", "json", str(container),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode:
        raise RuntimeError(f"ffprobe failed for {container}: {result.stderr.strip()}")
    try:
        payload = json.loads(result.stdout)
        raw_frames = payload["frames"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RuntimeError(f"invalid ffprobe frame output for {container}") from exc
    frames = []
    for index, frame in enumerate(raw_frames):
        if "best_effort_timestamp_time" not in frame:
            raise RuntimeError(f"frame {index} has no timestamp in {container}")
        frames.append({
            "index": index,
            "pts": float(frame["best_effort_timestamp_time"]),
            "width": int(frame["width"]),
            "height": int(frame["height"]),
        })
    return frames


def raw_frames(ffmpeg: str, container: Path, frames: list[dict[str, Any]]) -> list[bytes]:
    if not frames:
        return []
    width = frames[0]["width"]
    height = frames[0]["height"]
    if width <= 0 or height <= 0 or width % 2 or height % 2:
        raise RuntimeError(f"invalid NV12 dimensions {width}x{height}")
    if any((item["width"], item["height"]) != (width, height) for item in frames):
        raise RuntimeError("capture contains changing frame dimensions")
    frame_size = width * height * 3 // 2
    result = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-i",
            str(container), "-map", "0:v:0", "-fps_mode", "passthrough",
            "-pix_fmt", "nv12", "-f", "rawvideo", "pipe:1",
        ],
        check=False,
        capture_output=True,
        timeout=60,
    )
    if result.returncode:
        raise RuntimeError(
            f"raw extraction failed for {container}: "
            f"{result.stderr.decode('utf-8', 'replace').strip()}"
        )
    if len(result.stdout) != len(frames) * frame_size:
        raise RuntimeError(
            f"raw byte count {len(result.stdout)} does not match "
            f"{len(frames)} frames at {frame_size} bytes each"
        )
    return [
        result.stdout[offset:offset + frame_size]
        for offset in range(0, len(result.stdout), frame_size)
    ]


def difference_plane(a: bytes, b: bytes, width: int, height: int) -> dict[str, Any]:
    if len(a) != len(b) or len(a) != width * height:
        raise ValueError("plane sizes do not match declared geometry")
    changed = 0
    maximum = 0
    squared = 0
    left = width
    top = height
    right = -1
    bottom = -1
    for index, (av, bv) in enumerate(zip(a, b, strict=True)):
        delta = abs(av - bv)
        if delta == 0:
            continue
        changed += 1
        maximum = max(maximum, delta)
        squared += delta * delta
        x = index % width
        y = index // width
        left = min(left, x)
        top = min(top, y)
        right = max(right, x)
        bottom = max(bottom, y)
    bbox = None if changed == 0 else [left, top, right, bottom]
    return {
        "changed": changed,
        "max_delta": maximum,
        "rmse": math.sqrt(squared / len(a)),
        "bbox": bbox,
    }


def frame_metrics(hw: bytes, sw: bytes, width: int, height: int) -> dict[str, Any]:
    if width <= 0 or height <= 0 or width % 2 or height % 2:
        raise ValueError("NV12 geometry must be positive and even")
    frame_size = width * height * 3 // 2
    if len(hw) != frame_size or len(sw) != frame_size:
        raise ValueError("frame byte count does not match NV12 geometry")
    y_size = width * height
    y = difference_plane(hw[:y_size], sw[:y_size], width, height)
    uv_width = width
    uv_height = height // 2
    uv = difference_plane(hw[y_size:], sw[y_size:], uv_width, uv_height)
    return {
        "hw_sha256": hashlib.sha256(hw).hexdigest(),
        "sw_sha256": hashlib.sha256(sw).hexdigest(),
        "y_changed": y["changed"],
        "uv_changed": uv["changed"],
        "max_y_delta": y["max_delta"],
        "max_uv_delta": uv["max_delta"],
        "y_rmse": y["rmse"],
        "uv_rmse": uv["rmse"],
        "difference_bbox": {"y": y["bbox"], "uv_bytes": uv["bbox"]},
    }


def verify_capture_manifest(container: Path, expected_mode: str) -> dict[str, Any]:
    manifest_path = container.parent / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read capture manifest {manifest_path}") from exc
    if manifest.get("schema") != 1:
        raise RuntimeError(f"unsupported capture manifest schema in {manifest_path}")
    if manifest.get("mode") != expected_mode:
        raise RuntimeError(f"capture mode mismatch in {manifest_path}")
    if manifest.get("status") != "CAPTURED_RAW_FRAMES_NOT_VISUAL_ACCEPTANCE":
        raise RuntimeError(f"capture status is not complete in {manifest_path}")
    if manifest.get("input_sha256") != EXPECTED_INPUT_SHA256:
        raise RuntimeError(f"pinned input hash is absent from {manifest_path}")
    if manifest.get("target_pts") != list(TARGET_PTS):
        raise RuntimeError(f"target PTS set differs in {manifest_path}")
    if manifest.get("input_seek_start_seconds") != INPUT_SEEK_START_SECONDS:
        raise RuntimeError(f"input seek position differs in {manifest_path}")
    if manifest.get("input_decode_duration_seconds") != INPUT_DECODE_DURATION_SECONDS:
        raise RuntimeError(f"input decode duration differs in {manifest_path}")
    if manifest.get("output_time_base") != OUTPUT_TIME_BASE:
        raise RuntimeError(f"output time base differs in {manifest_path}")
    if len(manifest.get("captured_pts", [])) != len(TARGET_PTS):
        raise RuntimeError("capture manifest lacks all requested frame timestamps")
    if manifest.get("output") != container.name or not container.is_file():
        raise RuntimeError(f"capture path does not match {manifest_path}")
    if manifest.get("output_sha256") != sha256_file(container):
        raise RuntimeError(f"capture hash mismatch for {container}")
    ffmpeg_path = Path(str(manifest.get("ffmpeg", "")))
    if not ffmpeg_path.is_file() or manifest.get("ffmpeg_sha256") != sha256_file(ffmpeg_path):
        raise RuntimeError(f"FFmpeg binary identity changed for {container}")
    ffprobe_path = Path(str(manifest.get("ffprobe", "")))
    if not ffprobe_path.is_file() or manifest.get("ffprobe_sha256") != sha256_file(ffprobe_path):
        raise RuntimeError(f"ffprobe binary identity changed for {container}")
    return manifest


def verify_software_reference(container: Path) -> dict[str, Any]:
    manifest = verify_capture_manifest(container, "software")
    manifest_path = container.parent / "manifest.json"
    if sha256_file(manifest_path) != SOFTWARE_REFERENCE_MANIFEST_SHA256:
        raise RuntimeError("software reference manifest is not the reviewed capture")
    if sha256_file(container) != SOFTWARE_REFERENCE_CONTAINER_SHA256:
        raise RuntimeError("software reference container is not the reviewed capture")
    return manifest


def match_target(
    target: float,
    hw_frames: list[dict[str, Any]],
    sw_frames: list[dict[str, Any]],
    tolerance: float = PAIR_TOLERANCE_SECONDS,
) -> tuple[int, int]:
    def nearest(frames: list[dict[str, Any]], used: set[int]) -> int:
        candidates = [
            (abs(float(frame["pts"]) - target), int(frame["index"]))
            for frame in frames
            if int(frame["index"]) not in used
        ]
        if not candidates:
            raise ValueError(f"no unused frame is available near PTS {target:.6f}")
        delta, index = min(candidates)
        if delta > tolerance:
            raise ValueError(
                f"nearest frame is {delta:.6f}s from requested PTS {target:.6f}"
            )
        return index

    hw_index = nearest(hw_frames, set())
    sw_index = nearest(sw_frames, set())
    hw_pts = float(hw_frames[hw_index]["pts"])
    sw_pts = float(sw_frames[sw_index]["pts"])
    if abs(hw_pts - sw_pts) > tolerance:
        raise ValueError(
            f"hardware/software frame PTS differ: {hw_pts:.6f} vs {sw_pts:.6f}"
        )
    return hw_index, sw_index


def compare_captures(
    *,
    ffmpeg: str,
    ffprobe: str,
    hardware_container: Path,
    software_container: Path,
    targets: tuple[float, ...] = TARGET_PTS,
) -> dict[str, Any]:
    hw_manifest = verify_capture_manifest(hardware_container, "hardware")
    sw_manifest = verify_software_reference(software_container)
    if hw_manifest["input_sha256"] != sw_manifest["input_sha256"]:
        raise ValueError("hardware/software capture input hashes differ")
    if hw_manifest["ffmpeg_sha256"] != sw_manifest["ffmpeg_sha256"]:
        raise ValueError("hardware/software capture FFmpeg binaries differ")
    actual_ffmpeg = Path(shutil.which(ffmpeg) or ffmpeg).resolve(strict=True)
    actual_ffprobe = Path(shutil.which(ffprobe) or ffprobe).resolve(strict=True)
    if sha256_file(actual_ffmpeg) != hw_manifest["ffmpeg_sha256"]:
        raise ValueError("comparison FFmpeg differs from the captured FFmpeg")
    if sha256_file(actual_ffprobe) != hw_manifest["ffprobe_sha256"]:
        raise ValueError("comparison ffprobe differs from the captured ffprobe")
    hw_meta = probe_frames(ffprobe, hardware_container)
    sw_meta = probe_frames(ffprobe, software_container)
    hw_raw = raw_frames(ffmpeg, hardware_container, hw_meta)
    sw_raw = raw_frames(ffmpeg, software_container, sw_meta)
    report: list[dict[str, Any]] = []
    for target in targets:
        hw_index, sw_index = match_target(target, hw_meta, sw_meta)
        hw_frame = hw_meta[hw_index]
        sw_frame = sw_meta[sw_index]
        width, height = hw_frame["width"], hw_frame["height"]
        if (width, height) != (sw_frame["width"], sw_frame["height"]):
            raise ValueError("hardware/software visible dimensions differ")
        report.append({
            "requested_pts": target,
            "hw_pts": hw_frame["pts"],
            "sw_pts": sw_frame["pts"],
            "width": width,
            "height": height,
            **frame_metrics(hw_raw[hw_index], sw_raw[sw_index], width, height),
        })
    return {
        "schema": 1,
        "input_sha256": EXPECTED_INPUT_SHA256,
        "hardware_capture_sha256": hw_manifest["output_sha256"],
        "software_capture_sha256": sw_manifest["output_sha256"],
        "ffmpeg_sha256": hw_manifest["ffmpeg_sha256"],
        "interpretation": "raw decoded NV12 comparison; not presentation proof",
        "frame_count_hardware": len(hw_meta),
        "frame_count_software": len(sw_meta),
        "frames": report,
    }


def run_capture(
    *,
    mode: str,
    input_path: Path,
    output_dir: Path,
    ffmpeg: str,
    device: str,
) -> Path:
    if output_dir.exists():
        raise FileExistsError(f"refusing existing capture directory {output_dir}")
    input_path = input_path.resolve(strict=True)
    actual_input_sha = sha256_file(input_path)
    if actual_input_sha != EXPECTED_INPUT_SHA256:
        raise RuntimeError(f"input SHA-256 mismatch: {actual_input_sha}")
    ffmpeg_path = Path(shutil.which(ffmpeg) or ffmpeg).resolve(strict=True)
    output_dir.mkdir(parents=True)
    output_path = output_dir / f"{mode}.nut"
    argv = capture_argv(
        mode=mode,
        ffmpeg=str(ffmpeg_path),
        input_path=input_path,
        output_path=output_path,
        device=device,
    )
    result = subprocess.run(
        argv,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=900,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": os.environ.get("HOME", "/nonexistent"),
            "LC_ALL": "C",
        },
    )
    (output_dir / "ffmpeg.log").write_text(result.stdout, encoding="utf-8")
    if result.returncode or not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError(
            f"{mode} capture failed with status {result.returncode}; "
            f"see {output_dir / 'ffmpeg.log'}"
        )
    ffprobe_path = Path(shutil.which("/usr/bin/ffprobe") or "/usr/bin/ffprobe").resolve(strict=True)
    captured_frames = probe_frames(str(ffprobe_path), output_path)
    if len(captured_frames) != len(TARGET_PTS):
        raise RuntimeError(
            f"capture produced {len(captured_frames)} frames; expected {len(TARGET_PTS)}"
        )
    captured_pts: list[float] = []
    used: set[int] = set()
    for target in TARGET_PTS:
        candidates = [
            (abs(float(frame["pts"]) - target), int(frame["index"]))
            for frame in captured_frames
            if int(frame["index"]) not in used
        ]
        delta, index = min(candidates)
        if delta > CAPTURE_WINDOW_SECONDS:
            raise RuntimeError(
                f"capture has no frame within {CAPTURE_WINDOW_SECONDS}s of {target:.6f}"
            )
        used.add(index)
        captured_pts.append(float(captured_frames[index]["pts"]))
    manifest = {
        "schema": 1,
        "mode": mode,
        "status": "CAPTURED_RAW_FRAMES_NOT_VISUAL_ACCEPTANCE",
        "input": str(input_path),
        "input_sha256": actual_input_sha,
        "ffmpeg": str(ffmpeg_path),
        "ffmpeg_sha256": sha256_file(ffmpeg_path),
        "ffmpeg_version": subprocess.run(
            [str(ffmpeg_path), "-version"], check=True,
            capture_output=True, text=True, timeout=10,
        ).stdout.splitlines()[0],
        "ffprobe": str(ffprobe_path),
        "ffprobe_sha256": sha256_file(ffprobe_path),
        "device": device if mode == "hardware" else None,
        "target_pts": list(TARGET_PTS),
        "captured_pts": captured_pts,
        "input_seek_start_seconds": INPUT_SEEK_START_SECONDS,
        "input_decode_duration_seconds": INPUT_DECODE_DURATION_SECONDS,
        "output_time_base": OUTPUT_TIME_BASE,
        "capture_window_seconds": CAPTURE_WINDOW_SECONDS,
        "argv": argv,
        "output": output_path.name,
        "output_sha256": sha256_file(output_path),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("capture")
    capture.add_argument("--mode", choices=("hardware", "software"), required=True)
    capture.add_argument("--input", type=Path, required=True)
    capture.add_argument("--output-dir", type=Path, required=True)
    capture.add_argument("--ffmpeg", default="/usr/bin/ffmpeg")
    capture.add_argument("--device", default="/dev/dri/renderD128")
    capture.add_argument("--execute", action="store_true", required=True)

    compare = commands.add_parser("compare")
    compare.add_argument("--hardware", type=Path, required=True)
    compare.add_argument("--software", type=Path, required=True)
    compare.add_argument("--ffmpeg", default="/usr/bin/ffmpeg")
    compare.add_argument("--ffprobe", default="/usr/bin/ffprobe")
    compare.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "capture":
            run_capture(
                mode=args.mode,
                input_path=args.input,
                output_dir=args.output_dir,
                ffmpeg=args.ffmpeg,
                device=args.device,
            )
            return 0
        report = compare_captures(
            ffmpeg=args.ffmpeg,
            ffprobe=args.ffprobe,
            hardware_container=args.hardware,
            software_container=args.software,
        )
        if args.output.exists():
            raise FileExistsError(f"refusing existing comparison report {args.output}")
        args.output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
