"""Shared paths and helpers for the test suites in this folder (see README.md)."""
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]                      # the project folder
WORK = Path(os.environ.get("ANY2ANY_TEST_WORK") or (Path(tempfile.gettempdir()) / "any2any_tests"))
WORK.mkdir(parents=True, exist_ok=True)
FFMPEG = ROOT / "vendor" / "ffmpeg" / "ffmpeg.exe"              # the same ffmpeg the app ships


def synthetic_clip(path, seconds=19, size="640x360", fps=30, with_audio=True):
    """A small, deterministic test video (no network needed). Returns the path as a string."""
    path = Path(path)
    if path.exists() and path.stat().st_size > 0:
        return str(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(FFMPEG), "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}:duration={seconds}"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-shortest", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", str(path)]
    subprocess.run(cmd, capture_output=True, check=True)
    return str(path)
