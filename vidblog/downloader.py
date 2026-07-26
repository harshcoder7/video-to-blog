"""Download a YouTube video, its captions, and metadata via yt-dlp."""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field
from typing import Optional

import imageio_ffmpeg
import yt_dlp

_FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()


@dataclass
class VideoAssets:
    video_id: str
    title: str
    uploader: str
    upload_date: str  # YYYYMMDD
    duration: int  # seconds
    url: str
    description: str
    thumbnail_path: Optional[str]
    video_path: Optional[str]
    subtitle_path: Optional[str]  # .vtt path, or None if no captions found
    subtitle_is_auto: bool
    work_dir: str


def _find_first(pattern: str) -> Optional[str]:
    matches = glob.glob(pattern)
    return matches[0] if matches else None


def _build_assets(info: dict, url: str, work_dir: str, langs: tuple[str, ...]) -> VideoAssets:
    video_path = _find_first(os.path.join(work_dir, "video.mp4"))
    thumb_path = _find_first(os.path.join(work_dir, "video.webp")) or _find_first(
        os.path.join(work_dir, "video.jpg")
    )

    subtitle_path = None
    subtitle_is_auto = False
    for lang in langs:
        manual = _find_first(os.path.join(work_dir, f"video.{lang}.vtt"))
        if manual:
            subtitle_path = manual
            break
    if not subtitle_path:
        # any manual-language subs at all
        any_manual = _find_first(os.path.join(work_dir, "video.*.vtt"))
        if any_manual:
            subtitle_path = any_manual
    if not subtitle_path:
        # fall back to auto captions in any language we requested
        auto = _find_first(os.path.join(work_dir, "video.*.vtt"))
        if auto:
            subtitle_path = auto
            subtitle_is_auto = True

    return VideoAssets(
        video_id=info["id"],
        title=info.get("title", "Untitled"),
        uploader=info.get("uploader", "Unknown"),
        upload_date=info.get("upload_date", ""),
        duration=int(info.get("duration") or 0),
        url=url,
        description=info.get("description", "") or "",
        thumbnail_path=thumb_path,
        video_path=video_path,
        subtitle_path=subtitle_path,
        subtitle_is_auto=subtitle_is_auto,
        work_dir=work_dir,
    )


def download(
    url: str,
    out_root: str,
    langs: tuple[str, ...] = ("en", "en-US", "en-orig"),
    force: bool = False,
) -> VideoAssets:
    """Download video + subtitles + metadata into out_root/<video_id>/.

    If a video.mp4 already exists in the target folder (from a previous run),
    the download is skipped and the cached files are reused. Pass force=True
    to re-download anyway.
    """
    os.makedirs(out_root, exist_ok=True)

    probe_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(probe_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    video_id = info["id"]
    work_dir = os.path.join(out_root, video_id)
    os.makedirs(work_dir, exist_ok=True)

    cached_video = _find_first(os.path.join(work_dir, "video.mp4"))
    if cached_video and not force:
        return _build_assets(info, url, work_dir, langs)

    outtmpl = os.path.join(work_dir, "video.%(ext)s")
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "ffmpeg_location": _FFMPEG_EXE,
        "outtmpl": outtmpl,
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/best",
        "merge_output_format": "mp4",
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": list(langs),
        "subtitlesformat": "vtt",
        "writethumbnail": True,
        "postprocessors": [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    return _build_assets(info, url, work_dir, langs)
