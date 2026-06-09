#!/usr/bin/env python3
"""Convert OpenMV MJPEG preview clips to timestamp-corrected MP4.

The OpenMV recorder writes one frames.csv per session. This script uses the
per-frame ticks_ms column to estimate the actual capture FPS for each clip, then
passes that FPS to ffmpeg as the input frame rate before encoding MP4.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


PREVIEW_RE = re.compile(r"^preview_(?P<clip>\d+)\.mjpeg$", re.IGNORECASE)


@dataclass(frozen=True)
class TimingStats:
    clip: int
    rows: int
    first_frame: int
    last_frame: int
    duration_s: float
    observed_fps: float


def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=True, text=True, capture_output=True)


def require_ffmpeg() -> None:
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        raise SystemExit(
            "Missing %s in PATH. Install ffmpeg, then rerun this command."
            % " and ".join(missing)
        )


def parse_clip_index(path: Path) -> int:
    match = PREVIEW_RE.match(path.name)
    if not match:
        raise ValueError("Expected preview_0000.mjpeg-style filename: %s" % path.name)
    return int(match.group("clip"))


def read_clip_rows(frames_csv: Path, clip_index: int) -> list[dict[str, str]]:
    with frames_csv.open(newline="") as handle:
        rows = [
            row
            for row in csv.DictReader(handle)
            if int(row.get("clip", "-1")) == clip_index
        ]
    if not rows:
        raise RuntimeError("No rows for clip %d in %s" % (clip_index, frames_csv))
    return rows


def timing_from_rows(rows: list[dict[str, str]], clip_index: int) -> TimingStats:
    first = rows[0]
    last = rows[-1]
    first_tick = int(first["ticks_ms"])
    last_tick = int(last["ticks_ms"])
    duration_s = (last_tick - first_tick) / 1000.0
    if duration_s <= 0:
        raise RuntimeError("Non-positive timestamp duration for clip %d" % clip_index)

    rows_n = len(rows)
    observed_fps = (rows_n - 1) / duration_s
    return TimingStats(
        clip=clip_index,
        rows=rows_n,
        first_frame=int(first["frame"]),
        last_frame=int(last["frame"]),
        duration_s=duration_s,
        observed_fps=observed_fps,
    )


def ffprobe_frames(video_path: Path) -> int | None:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-count_frames",
        "-show_entries",
        "stream=nb_read_frames,nb_frames",
        "-of",
        "json",
        str(video_path),
    ]
    try:
        data = json.loads(run_cmd(cmd).stdout)
    except Exception:
        return None

    streams = data.get("streams", [])
    if not streams:
        return None

    stream = streams[0]
    for key in ("nb_read_frames", "nb_frames"):
        value = stream.get(key)
        if value not in (None, "N/A"):
            try:
                return int(value)
            except ValueError:
                pass
    return None


def parse_scale(text: str) -> tuple[int, int] | None:
    if text.lower() in ("none", "off", "false", "0"):
        return None
    parts = text.lower().split("x")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("Scale must look like 640x480 or none.")
    width = int(parts[0])
    height = int(parts[1])
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("Scale dimensions must be positive.")
    return width, height


def output_path_for(video_path: Path, scale: tuple[int, int] | None, output_dir: Path | None) -> Path:
    if scale is None:
        name = video_path.with_suffix(".mp4").name
    else:
        name = "%s_%dx%d.mp4" % (video_path.stem, scale[0], scale[1])
    return (output_dir or video_path.parent) / name


def convert_clip(
    video_path: Path,
    frames_csv: Path,
    scale: tuple[int, int] | None,
    output_dir: Path | None,
    crf: int,
    preset: str,
    overwrite: bool,
) -> Path:
    clip_index = parse_clip_index(video_path)
    rows = read_clip_rows(frames_csv, clip_index)
    timing = timing_from_rows(rows, clip_index)
    output_path = output_path_for(video_path, scale, output_dir)

    cmd = ["ffmpeg"]
    if overwrite:
        cmd.append("-y")
    else:
        cmd.append("-n")

    cmd.extend(
        [
            "-r",
            "%.6f" % timing.observed_fps,
            "-i",
            str(video_path),
            "-an",
        ]
    )
    if scale is not None:
        cmd.extend(["-vf", "scale=%d:%d:flags=neighbor" % (scale[0], scale[1])])
    cmd.extend(
        [
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            str(crf),
            "-preset",
            preset,
            str(output_path),
        ]
    )

    ffprobe_n = ffprobe_frames(video_path)
    print("Clip:", video_path)
    print("  frames.csv rows:       %d" % timing.rows)
    print("  ffprobe frames:        %s" % (ffprobe_n if ffprobe_n is not None else "unknown"))
    print("  timestamp duration s:  %.3f" % timing.duration_s)
    print("  observed fps:          %.6f" % timing.observed_fps)
    print("  output:                %s" % output_path)
    print("  command:               %s" % " ".join(cmd))
    subprocess.run(cmd, check=True)
    return output_path


def find_video_inputs(path: Path) -> list[Path]:
    if path.is_dir():
        return sorted(path.glob("preview_*.mjpeg"))
    return [path]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="Session directory or preview_0000.mjpeg file.")
    parser.add_argument("--csv", type=Path, default=None, help="Path to frames.csv. Defaults to input dir/frames.csv.")
    parser.add_argument("--scale", type=parse_scale, default=parse_scale("640x480"))
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--crf", type=int, default=20)
    parser.add_argument("--preset", default="medium")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    require_ffmpeg()

    input_path = args.input
    frames_csv = args.csv or (input_path / "frames.csv" if input_path.is_dir() else input_path.parent / "frames.csv")
    if not frames_csv.exists():
        raise SystemExit("Missing frames.csv: %s" % frames_csv)

    videos = find_video_inputs(input_path)
    if not videos:
        raise SystemExit("No preview_*.mjpeg files found in %s" % input_path)

    if args.output_dir is not None:
        args.output_dir.mkdir(parents=True, exist_ok=True)

    outputs = [
        convert_clip(
            video_path=video,
            frames_csv=frames_csv,
            scale=args.scale,
            output_dir=args.output_dir,
            crf=args.crf,
            preset=args.preset,
            overwrite=args.overwrite,
        )
        for video in videos
    ]

    print("\nWrote:")
    for output in outputs:
        print("  " + str(output))


if __name__ == "__main__":
    main()
