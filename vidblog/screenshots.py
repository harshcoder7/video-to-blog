"""Pick the "best" screenshot per blog section: sharp, content-rich, non-duplicate.

Strategy:
1. Extract candidate frames two ways so both talking-head and static-screen
   videos are covered: ffmpeg scene-change detection, plus a fixed-interval
   grid fallback.
2. Score every candidate for sharpness (Laplacian variance -> rejects motion
   blur / transition frames) and visual content density (Canny edge ratio ->
   prefers frames with slides/code/diagrams over blank frames).
3. Drop near-duplicate candidates that landed within a short time window of a
   higher-scoring neighbor.
4. For each transcript section, choose the best-scoring candidate whose
   timestamp falls inside that section's time range (nearest fallback if the
   range had no candidate).
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass

import cv2
import imageio_ffmpeg

from vidblog.segmenter import Section

_PTS_RE = re.compile(r"pts_time:([0-9.]+)")

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


@dataclass
class Candidate:
    path: str
    time: float
    score: float = 0.0


def _run_extract(video_path: str, vf: str, out_pattern: str) -> list[float]:
    """Run ffmpeg with the given filter chain (must end in showinfo), return
    the pts_time (seconds) of each frame it wrote, in order."""
    cmd = [
        FFMPEG,
        "-y",
        "-loglevel",
        "info",
        "-i",
        video_path,
        "-vf",
        vf,
        "-vsync",
        "vfr",
        "-q:v",
        "3",
        out_pattern,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="ignore")
    return [float(m) for m in _PTS_RE.findall(proc.stderr)]


def _load_cached_candidates(tmp_dir: str) -> list[Candidate]:
    """Reuse frame candidates already extracted by a previous run (matched by
    their pts_time sidecar file), skipping the expensive ffmpeg decode pass."""
    times_file = os.path.join(tmp_dir, "_times.txt")
    if not os.path.exists(times_file):
        return []
    candidates: list[Candidate] = []
    with open(times_file, "r", encoding="utf-8") as f:
        for line in f:
            path, _, t = line.strip().partition("\t")
            if path and t and os.path.exists(path):
                candidates.append(Candidate(path=path, time=float(t)))
    return candidates


def _save_candidate_times(tmp_dir: str, candidates: list[Candidate]) -> None:
    with open(os.path.join(tmp_dir, "_times.txt"), "w", encoding="utf-8") as f:
        for c in candidates:
            f.write(f"{c.path}\t{c.time}\n")


def _extract_candidates(video_path: str, tmp_dir: str, grid_interval: float = 6.0) -> list[Candidate]:
    os.makedirs(tmp_dir, exist_ok=True)

    cached = _load_cached_candidates(tmp_dir)
    if cached:
        return cached

    candidates: list[Candidate] = []

    scene_pattern = os.path.join(tmp_dir, "scene_%05d.jpg")
    scene_times = _run_extract(video_path, "select='gt(scene,0.12)',showinfo", scene_pattern)
    for i, t in enumerate(scene_times, start=1):
        p = os.path.join(tmp_dir, f"scene_{i:05d}.jpg")
        if os.path.exists(p):
            candidates.append(Candidate(path=p, time=t))

    grid_pattern = os.path.join(tmp_dir, "grid_%05d.jpg")
    grid_times = _run_extract(video_path, f"fps=1/{grid_interval},showinfo", grid_pattern)
    for i, t in enumerate(grid_times, start=1):
        p = os.path.join(tmp_dir, f"grid_{i:05d}.jpg")
        if os.path.exists(p):
            candidates.append(Candidate(path=p, time=t))

    candidates.sort(key=lambda c: c.time)
    _save_candidate_times(tmp_dir, candidates)
    return candidates


def _score(path: str) -> float:
    img = cv2.imread(path)
    if img is None:
        return 0.0
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    sharpness = cv2.Laplacian(gray, cv2.CV_64F).var()
    edges = cv2.Canny(gray, 80, 160)
    edge_density = edges.mean() / 255.0
    return sharpness * 0.7 + edge_density * 4000 * 0.3


def _dedupe(candidates: list[Candidate], window: float = 2.5) -> list[Candidate]:
    if not candidates:
        return []
    candidates = sorted(candidates, key=lambda c: c.time)
    kept: list[Candidate] = [candidates[0]]
    for c in candidates[1:]:
        if c.time - kept[-1].time < window:
            if c.score > kept[-1].score:
                kept[-1] = c
        else:
            kept.append(c)
    return kept


def select_screenshots(
    video_path: str,
    sections: list[Section],
    work_dir: str,
    max_total: int = 14,
) -> dict[int, str]:
    """Return {section.index: final_screenshot_path}."""
    if not sections:
        return {}

    tmp_dir = os.path.join(work_dir, "_frame_candidates")
    out_dir = os.path.join(work_dir, "screenshots")
    os.makedirs(out_dir, exist_ok=True)

    candidates = _extract_candidates(video_path, tmp_dir)
    for c in candidates:
        c.score = _score(c.path)
    candidates = _dedupe(candidates)

    result: dict[int, str] = {}
    used_times: set[float] = set()

    for section in sections:
        if len(result) >= max_total:
            break
        in_range = [c for c in candidates if section.start <= c.time <= section.end]
        pool = in_range if in_range else candidates
        if not pool:
            continue
        pool = sorted(pool, key=lambda c: c.score, reverse=True)
        chosen = None
        for c in pool:
            if c.time not in used_times:
                chosen = c
                break
        if chosen is None:
            continue
        used_times.add(chosen.time)

        dest = os.path.join(out_dir, f"section_{section.index:02d}.jpg")
        img = cv2.imread(chosen.path)
        if img is None:
            continue
        h, w = img.shape[:2]
        if w > 1280:
            scale = 1280 / w
            img = cv2.resize(img, (1280, int(h * scale)), interpolation=cv2.INTER_AREA)
        cv2.imwrite(dest, img, [cv2.IMWRITE_JPEG_QUALITY, 88])
        result[section.index] = dest

    return result
