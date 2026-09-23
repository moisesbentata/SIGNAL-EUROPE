"""Turns a static PNG graphic into a short MP4 suitable for the
Instagram Reels API (which only accepts video, not still images).

Uses the ffmpeg binary already present on the host (installed via
nixpacks.toml on Railway, or via `brew install ffmpeg` / `apt install
ffmpeg` locally). Output is a 5-second silent H.264 clip with a stereo
AAC audio track — Reels rejects video without an audio track, so we
mux in a silent one.
"""

import shutil
import subprocess
from pathlib import Path

VIDEO_DURATION_SECONDS = 5
VIDEO_FRAMERATE = 30


class VideoBuildError(RuntimeError):
    pass


def png_to_reel_mp4(png_path: Path, output_path: Path) -> Path:
    if shutil.which("ffmpeg") is None:
        raise VideoBuildError(
            "ffmpeg not found on PATH. Install it (Railway: covered by "
            "nixpacks.toml; local: brew install ffmpeg / apt install ffmpeg)."
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-framerate", str(VIDEO_FRAMERATE),
        "-i", str(png_path),
        "-f", "lavfi",
        "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-t", str(VIDEO_DURATION_SECONDS),
        "-c:v", "libx264",
        "-tune", "stillimage",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-shortest",
        "-movflags", "+faststart",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise VideoBuildError(
            f"ffmpeg failed (exit {result.returncode}):\n{result.stderr[-2000:]}"
        )
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise VideoBuildError("ffmpeg reported success but produced no output file")
    return output_path
