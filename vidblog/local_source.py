"""Treat a local video file as a pipeline source, no YouTube download needed.

Produces a VideoAssets identical in shape to downloader.download()'s output,
so the rest of the pipeline (transcript, segmenter, screenshots, writer,
pdf_builder) works completely unchanged regardless of where the video came
from. subtitle_path is always None here -- local recordings never have
platform captions, so the pipeline always falls back to Whisper.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess

import imageio_ffmpeg

from vidblog.downloader import VideoAssets

_FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)")


def _probe_duration(path: str) -> int:
    proc = subprocess.run([_FFMPEG_EXE, "-i", path], capture_output=True, text=True)
    m = _DURATION_RE.search(proc.stderr)
    if not m:
        return 0
    h, minutes, s = m.groups()
    return int(h) * 3600 + int(minutes) * 60 + int(float(s))


def _slugify_id(path: str) -> str:
    base = os.path.splitext(os.path.basename(path))[0]
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", base).strip("-").lower()[:40] or "local-video"
    digest = hashlib.sha1(os.path.abspath(path).encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"


def is_local_file(source: str) -> bool:
    return not source.lower().startswith(("http://", "https://")) and os.path.exists(source)


def ingest_local_file(path: str, out_root: str, title: str | None = None) -> VideoAssets:
    if not os.path.exists(path):
        raise FileNotFoundError(f"No such file: {path}")

    video_id = _slugify_id(path)
    work_dir = os.path.join(out_root, video_id)
    os.makedirs(work_dir, exist_ok=True)

    video_path = os.path.join(work_dir, "video.mp4")
    if not os.path.exists(video_path):
        try:
            os.link(path, video_path)  # instant, no extra disk, if same volume
        except OSError:
            shutil.copy2(path, video_path)

    duration = _probe_duration(video_path)
    display_title = title or os.path.splitext(os.path.basename(path))[0].strip()

    return VideoAssets(
        video_id=video_id,
        title=display_title,
        uploader="Local recording",
        upload_date="",
        duration=duration,
        url="",
        description="",
        thumbnail_path=None,
        video_path=video_path,
        subtitle_path=None,
        subtitle_is_auto=False,
        work_dir=work_dir,
    )
