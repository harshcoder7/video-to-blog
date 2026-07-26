"""Turn transcript sections into blog prose.

Two modes:
  - LLM mode (ANTHROPIC_API_KEY set): asks Claude to rewrite each section's
    raw transcript into clean, engaging blog prose with headings.
  - Free fallback: rule-based cleanup (drops filler words, re-punctuates,
    title-cases a heading from the section text). No cost, no API key
    needed, works offline.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from vidblog.downloader import VideoAssets
from vidblog.segmenter import Section

FILLER_WORDS = {
    "um", "uh", "uhh", "umm", "erm", "like,", "y'know", "you know",
    "kind of", "sort of", "basically", "actually", "literally", "so,",
}

_FILLER_RE = re.compile(
    r"\b(um+|uh+|erm+|y'know)\b[, ]*", re.IGNORECASE
)


@dataclass
class BlogSection:
    index: int
    heading: str
    paragraphs: list[str]
    timestamp_label: str
    screenshot_path: str | None = None
    screenshot_caption: str = ""


@dataclass
class BlogPost:
    title: str
    subtitle: str
    intro: list[str]
    sections: list[BlogSection] = field(default_factory=list)
    conclusion: list[str] = field(default_factory=list)
    source_url: str = ""
    used_llm: bool = False


def _fmt_ts(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:d}:{s:02d}"


# ---------------------------------------------------------------------------
# Free rule-based fallback
# ---------------------------------------------------------------------------

def _clean_words(text: str) -> str:
    text = _FILLER_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _chunk_into_sentences(text: str, chunk_words: int = 20) -> list[str]:
    words = text.split(" ")
    sentences = []
    for i in range(0, len(words), chunk_words):
        chunk = " ".join(words[i : i + chunk_words]).strip()
        if not chunk:
            continue
        chunk = chunk[0].upper() + chunk[1:]
        if not chunk.endswith((".", "!", "?")):
            chunk += "."
        sentences.append(chunk)
    return sentences


def _fallback_write(assets: VideoAssets, sections: list[Section]) -> BlogPost:
    blog_sections = []
    for i, sec in enumerate(sections):
        clean = _clean_words(sec.text)
        sentences = _chunk_into_sentences(clean)
        # group sentences into ~3 sentence paragraphs
        paragraphs = []
        for j in range(0, len(sentences), 3):
            paragraphs.append(" ".join(sentences[j : j + 3]))
        blog_sections.append(
            BlogSection(
                index=sec.index,
                # Raw auto-captions have no reliable sentence/topic boundaries to
                # pull a real headline from, so the free fallback labels sections
                # plainly rather than guessing a misleading title. Set
                # ANTHROPIC_API_KEY for real LLM-written headings and prose.
                heading=f"Part {i + 1} — {_fmt_ts(sec.start)}",
                paragraphs=paragraphs or ["(No transcript available for this section.)"],
                timestamp_label=f"{_fmt_ts(sec.start)} - {_fmt_ts(sec.end)}",
            )
        )

    return BlogPost(
        title=assets.title,
        subtitle=f"A written walkthrough of the video by {assets.uploader}",
        intro=[
            f"This post is a written walkthrough of \"{assets.title}\" by {assets.uploader}, "
            "distilled from the video's transcript and illustrated with screenshots from "
            "key moments."
        ],
        sections=blog_sections,
        conclusion=["That covers the full video — watch the original above for the complete context."],
        source_url=assets.url,
        used_llm=False,
    )


# ---------------------------------------------------------------------------
# LLM mode
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a skilled technical blog writer. You turn raw, \
punctuation-free video transcripts into a polished, engaging blog post. \
Rewrite the content in clear prose — do not just add punctuation to the \
transcript verbatim. Fix grammar, remove filler and repetition, merge \
fragmented thoughts into full sentences, and organize each section into \
1-3 short paragraphs. Keep all factual/technical content from the \
transcript; do not invent facts not present in it. Write in an inviting, \
clear tone suitable for a general tech-savvy reader. Respond with ONLY a \
single JSON object, no markdown fences, matching this schema:
{
  "title": string,
  "subtitle": string,
  "intro": [string, ...],       // 1-2 paragraphs
  "sections": [
    {"index": number, "heading": string, "paragraphs": [string, ...]}
  ],
  "conclusion": [string, ...]    // 1 short paragraph
}
"""


def _llm_write(assets: VideoAssets, sections: list[Section], model: str) -> BlogPost:
    import anthropic

    client = anthropic.Anthropic()

    sections_payload = [
        {
            "index": sec.index,
            "start": _fmt_ts(sec.start),
            "end": _fmt_ts(sec.end),
            "raw_transcript": sec.text,
        }
        for sec in sections
    ]

    user_prompt = json.dumps(
        {
            "video_title": assets.title,
            "channel": assets.uploader,
            "url": assets.url,
            "sections": sections_payload,
        },
        ensure_ascii=False,
    )

    resp = client.messages.create(
        model=model,
        max_tokens=8000,
        temperature=0.6,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    raw = "".join(block.text for block in resp.content if block.type == "text").strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    data = json.loads(raw)

    sections_by_index = {sec.index: sec for sec in sections}
    blog_sections = []
    for s in data.get("sections", []):
        idx = s["index"]
        sec = sections_by_index.get(idx)
        ts_label = f"{_fmt_ts(sec.start)} - {_fmt_ts(sec.end)}" if sec else ""
        blog_sections.append(
            BlogSection(
                index=idx,
                heading=s.get("heading", f"Part {idx + 1}"),
                paragraphs=s.get("paragraphs") or [""],
                timestamp_label=ts_label,
            )
        )
    blog_sections.sort(key=lambda b: b.index)

    return BlogPost(
        title=data.get("title", assets.title),
        subtitle=data.get("subtitle", ""),
        intro=data.get("intro", []),
        sections=blog_sections,
        conclusion=data.get("conclusion", []),
        source_url=assets.url,
        used_llm=True,
    )


def _load_override(assets: VideoAssets, sections: list[Section]) -> BlogPost | None:
    """If work_dir/blog_override.json exists, use it verbatim instead of the
    LLM or rule-based writer. Lets a human (or an assistant with no API key)
    hand-author polished prose using the same JSON schema the LLM writer
    produces -- see sections.json in the same folder for the raw transcript
    per section to work from."""
    path = os.path.join(assets.work_dir, "blog_override.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    sections_by_index = {sec.index: sec for sec in sections}
    blog_sections = []
    for s in data.get("sections", []):
        idx = s["index"]
        sec = sections_by_index.get(idx)
        ts_label = f"{_fmt_ts(sec.start)} - {_fmt_ts(sec.end)}" if sec else ""
        blog_sections.append(
            BlogSection(
                index=idx,
                heading=s.get("heading", f"Part {idx + 1}"),
                paragraphs=s.get("paragraphs") or [""],
                timestamp_label=ts_label,
            )
        )
    blog_sections.sort(key=lambda b: b.index)

    return BlogPost(
        title=data.get("title", assets.title),
        subtitle=data.get("subtitle", ""),
        intro=data.get("intro", []),
        sections=blog_sections,
        conclusion=data.get("conclusion", []),
        source_url=assets.url,
        used_llm=True,
    )


def dump_sections(assets: VideoAssets, sections: list[Section]) -> str:
    """Write sections.json: the raw per-section transcript, for debugging and
    as the source material for a hand-authored blog_override.json."""
    path = os.path.join(assets.work_dir, "sections.json")
    payload = {
        "video_title": assets.title,
        "channel": assets.uploader,
        "url": assets.url,
        "sections": [
            {
                "index": sec.index,
                "start": _fmt_ts(sec.start),
                "end": _fmt_ts(sec.end),
                "raw_transcript": sec.text,
            }
            for sec in sections
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def generate_blog(
    assets: VideoAssets,
    sections: list[Section],
    use_llm: bool = True,
    model: str = "claude-sonnet-5",
) -> BlogPost:
    override = _load_override(assets, sections)
    if override is not None:
        return override
    if use_llm and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return _llm_write(assets, sections, model=model)
        except Exception as exc:  # fall back gracefully, never hard-fail the pipeline
            print(f"[writer] LLM writing failed ({exc}); falling back to rule-based writer.")
    return _fallback_write(assets, sections)
