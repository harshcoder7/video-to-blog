"""Turn a process-walkthrough recording's transcript + frame captions into a
structured audit/process document -- a numbered sequence of steps, each
naming the system/screen involved and what happened on it, grounded in both
what was said (transcript) and what was actually on screen (VLM caption).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

import ollama_client
from vidblog.downloader import VideoAssets
from vidblog.segmenter import Section

_FILLER_RE = re.compile(r"\b(um+|uh+|erm+|y'know)\b[, ]*", re.IGNORECASE)


@dataclass
class AuditStep:
    index: int
    heading: str
    timestamp_label: str
    narration: str
    on_screen: str | None = None
    screenshot_path: str | None = None
    screenshot_caption: str = ""


@dataclass
class AuditDoc:
    title: str
    subtitle: str
    overview: str
    steps: list[AuditStep] = field(default_factory=list)
    closing: str = ""
    source_path: str = ""
    used_llm: bool = False


def _fmt_ts(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


def _clean(text: str) -> str:
    text = _FILLER_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def caption_screenshots(screenshots: dict[int, str]) -> dict[int, str]:
    """Run the local VLM over every selected screenshot. Best-effort: a
    caption failure for one frame just leaves that step without an
    on-screen description rather than failing the whole run."""
    captions: dict[int, str] = {}
    if not ollama_client.is_available():
        return captions
    for idx, path in screenshots.items():
        caption = ollama_client.caption_image(path)
        if caption:
            captions[idx] = caption
    return captions


_STEP_SYSTEM_PROMPT_NARRATION_ONLY = """You are documenting a recorded process walkthrough for an \
internal audit trail. You'll get the raw, unpunctuated spoken transcript for one step of the \
process. Rewrite it as a clean, precise, factual description of what the speaker describes doing \
in this step. You have NOT seen the screen and have no screenshot description -- never invent, \
guess, or name a specific system, screen, button, field, document title, or on-screen data that \
isn't explicitly said aloud in the transcript. If the narration is vague (e.g. "click here", "this \
dropdown"), keep your description equally general rather than inventing specifics -- vagueness in, \
vagueness out. Write 2-4 factual, neutral sentences (this is documentation, not a blog post). Do \
not start with filler like "In this step" -- describe it directly."""

_STEP_SYSTEM_PROMPT_WITH_SCREEN = """You are documenting a recorded process walkthrough for an \
internal audit trail. For one step of the process you'll get the raw, unpunctuated spoken \
transcript, and a factual description of what was visible on screen at that moment. Write a \
clean, precise audit-style description of this step -- what the person is doing, what system or \
screen is involved, and what specific data, fields, or actions are shown. Use the on-screen \
description to ground vague narration (e.g. "click here" -> name the actual field or button from \
the screen description) -- but never invent details present in neither source. Write 2-4 factual, \
neutral sentences. Do not start with filler like "In this step". The on-screen description is \
shown separately elsewhere in the document already -- weave relevant details in naturally, but \
don't write a sentence starting with "On screen" / "The screen shows" that just repeats it."""


def _write_step_llm(narration: str, on_screen: str | None) -> str | None:
    if on_screen:
        user_msg = f"Spoken narration: {narration}\n\nOn-screen content: {on_screen}"
        system_prompt = _STEP_SYSTEM_PROMPT_WITH_SCREEN
    else:
        user_msg = f"Spoken narration: {narration}"
        system_prompt = _STEP_SYSTEM_PROMPT_NARRATION_ONLY
    return ollama_client.chat(system_prompt, user_msg, temperature=0.2, max_tokens=200)


def _write_step_fallback(narration: str, on_screen: str | None) -> str:
    body = _clean(narration)
    if len(body) > 500:
        body = body[:500].rsplit(" ", 1)[0] + "..."
    if on_screen:
        body += f"\n\nOn screen: {on_screen}"
    return body


_OVERVIEW_SYSTEM_PROMPT = """You write short, factual overviews for internal process-audit \
documents. Given the titles of every step in a recorded process walkthrough, write a 2-3 \
sentence overview of what process this recording documents end to end. Neutral, factual tone."""


def _write_overview_llm(title: str, step_headings: list[str]) -> str | None:
    listing = "\n".join(f"- {h}" for h in step_headings)
    return ollama_client.chat(
        _OVERVIEW_SYSTEM_PROMPT,
        f'Recording title: "{title}"\n\nSteps:\n{listing}',
        temperature=0.3,
        max_tokens=200,
    )


def generate_audit_doc(
    assets: VideoAssets,
    sections: list[Section],
    screenshots: dict[int, str],
    use_llm: bool = True,
    use_vlm_captions: bool = False,
) -> AuditDoc:
    # VLM screen-captioning (moondream/llava-phi3, the small local vision
    # models that fit this GPU) was tested against real UI screenshots and
    # confidently hallucinated fabricated details (fake filenames, fake
    # people) rather than reading the actual screen -- unacceptable for an
    # audit trail. Off by default: the real screenshot is embedded in the
    # doc directly, so a human reviewer can verify on-screen content by eye
    # instead of trusting an unreliable auto-caption. Flip use_vlm_captions=True
    # to re-enable once a stronger local vision model is validated.
    use_llm = use_llm and ollama_client.is_available()
    captions = caption_screenshots(screenshots) if (use_llm and use_vlm_captions) else {}

    steps: list[AuditStep] = []
    for i, sec in enumerate(sections):
        narration = _clean(sec.text)
        on_screen = captions.get(sec.index)
        shot = screenshots.get(sec.index)

        if use_llm:
            body = _write_step_llm(narration, on_screen) or _write_step_fallback(narration, on_screen)
        else:
            body = _write_step_fallback(narration, on_screen)

        heading = f"Step {i + 1} — {_fmt_ts(sec.start)}"
        steps.append(
            AuditStep(
                index=sec.index,
                heading=heading,
                timestamp_label=f"{_fmt_ts(sec.start)} - {_fmt_ts(sec.end)}",
                narration=body,
                on_screen=on_screen,
                screenshot_path=shot,
                screenshot_caption=f"Captured at {_fmt_ts(sec.start)}",
            )
        )

    if use_llm:
        overview = _write_overview_llm(assets.title, [s.heading for s in steps]) or (
            f"This document walks through {len(steps)} steps recorded in \"{assets.title}\"."
        )
    else:
        overview = f"This document walks through {len(steps)} steps recorded in \"{assets.title}\"."

    return AuditDoc(
        title=f"Process Walkthrough Audit: {assets.title}",
        subtitle=f"Recorded {assets.upload_date or 'walkthrough'} · {len(steps)} steps · source: {os.path.basename(assets.video_path or assets.title)}",
        overview=overview,
        steps=steps,
        closing="End of recorded walkthrough. Review each step above against current SOPs for gaps or deviations.",
        source_path=assets.video_path or "",
        used_llm=use_llm,
    )


def dump_blog_override(doc: AuditDoc, assets: VideoAssets) -> str:
    """Write blog_override.json in the same schema vidblog's blog writer uses,
    so kgwiki's graph_builder picks up the polished audit-step narration
    instead of falling back to raw, unpunctuated transcript text -- the
    knowledge graph and the Markdown doc end up showing the same content."""
    path = os.path.join(assets.work_dir, "blog_override.json")
    payload = {
        "title": doc.title,
        "subtitle": doc.subtitle,
        "intro": [doc.overview],
        "sections": [
            {"index": s.index, "heading": s.heading, "paragraphs": [s.narration]}
            for s in doc.steps
        ],
        "conclusion": [doc.closing],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path
