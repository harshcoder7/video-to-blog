"""Parse YouTube VTT captions into a clean, deduplicated word/cue timeline.

YouTube auto-generated captions are "rolling": each cue re-sends part of the
previous cue's text plus a few new words (karaoke style), and sometimes embeds
per-word timestamp tags like ``<00:00:01.230><c> word</c>``. This module
strips that noise and reconstructs a single deduplicated timeline of
(word, timestamp) events, then groups it back into readable cues.
"""
from __future__ import annotations

import difflib
import html
import re
from dataclasses import dataclass

import webvtt

_TAG_RE = re.compile(r"<[^>]+>")
_MULTISPACE_RE = re.compile(r"\s+")
_SPEAKER_MARK_RE = re.compile(r"(?:>>|<<)+")


@dataclass
class WordEvent:
    word: str
    time: float  # seconds


@dataclass
class Cue:
    start: float
    end: float
    text: str


def _clean_line(line: str) -> str:
    line = _TAG_RE.sub("", line)
    line = html.unescape(line)
    line = _SPEAKER_MARK_RE.sub(" ", line)
    return _MULTISPACE_RE.sub(" ", line).strip()


def _cue_words(text: str) -> list[str]:
    words = []
    for line in text.splitlines():
        line = _clean_line(line)
        if line:
            words.extend(line.split(" "))
    return [w for w in words if w]


def parse_vtt(path: str) -> list[WordEvent]:
    """Return a flat, deduplicated (word, timestamp) timeline from a VTT file."""
    captions = webvtt.read(path)
    events: list[WordEvent] = []
    prev_words: list[str] = []

    for caption in captions:
        start = caption.start_in_seconds
        words = _cue_words(caption.text)
        if not words:
            continue

        if prev_words:
            matcher = difflib.SequenceMatcher(a=prev_words, b=words, autojunk=False)
            match = matcher.find_longest_match(0, len(prev_words), 0, len(words))
            # If the new cue largely overlaps the previous one's start, only the
            # words after the overlapping block are genuinely new.
            if match.size > 0 and match.b == 0:
                new_words = words[match.size:]
            else:
                new_words = words
        else:
            new_words = words

        if new_words:
            span = max(caption.end_in_seconds - start, 0.1)
            per_word = span / max(len(new_words), 1)
            for i, w in enumerate(new_words):
                events.append(WordEvent(word=w, time=start + i * per_word))

        prev_words = words

    return events


def events_to_text(events: list[WordEvent]) -> str:
    return " ".join(e.word for e in events)


def events_to_cues(events: list[WordEvent], words_per_cue: int = 18) -> list[Cue]:
    """Regroup the deduplicated word timeline into fixed-size pseudo-cues.

    Useful for downstream section-splitting and screenshot alignment when the
    source captions have no reliable sentence boundaries.
    """
    cues: list[Cue] = []
    for i in range(0, len(events), words_per_cue):
        chunk = events[i : i + words_per_cue]
        if not chunk:
            continue
        text = " ".join(e.word for e in chunk)
        cues.append(Cue(start=chunk[0].time, end=chunk[-1].time, text=text))
    return cues


def load_transcript(subtitle_path: str) -> list[WordEvent]:
    return parse_vtt(subtitle_path)


def transcribe_with_whisper(audio_or_video_path: str, model_size: str = "small") -> list[WordEvent]:
    """Fallback transcription when the video has no captions at all.

    Requires the optional ``faster-whisper`` package (CPU-friendly, no torch
    dependency). Installed lazily since most videos won't need it:
        pip install faster-whisper
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "No captions were found for this video, and faster-whisper is not "
            "installed for local transcription. Run: pip install faster-whisper"
        ) from exc

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(audio_or_video_path, vad_filter=True)

    events: list[WordEvent] = []
    for seg in segments:
        words = [w for w in seg.text.strip().split(" ") if w]
        if not words:
            continue
        span = max(seg.end - seg.start, 0.1)
        per_word = span / len(words)
        for i, w in enumerate(words):
            events.append(WordEvent(word=w, time=seg.start + i * per_word))
    return events
