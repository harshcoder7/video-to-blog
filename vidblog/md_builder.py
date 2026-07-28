"""Render an AuditDoc into a clean, GitHub-flavored Markdown document with
embedded screenshots -- readable directly on GitHub/most editors, and easy
to diff/version alongside the rest of the repo."""
from __future__ import annotations

import os
import re

from vidblog.audit_writer import AuditDoc


def _anchor(heading: str) -> str:
    slug = heading.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug)
    return slug


def build_markdown(doc: AuditDoc, out_path: str) -> str:
    out_dir = os.path.dirname(os.path.abspath(out_path))
    lines: list[str] = []

    lines.append(f"# {doc.title}")
    lines.append("")
    lines.append(f"*{doc.subtitle}*")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Overview")
    lines.append("")
    lines.append(doc.overview)
    lines.append("")
    lines.append("## Contents")
    lines.append("")
    for s in doc.steps:
        lines.append(f"- [{s.heading}](#{_anchor(s.heading)}) — {s.timestamp_label}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for s in doc.steps:
        lines.append(f"## {s.heading}")
        lines.append("")
        lines.append(f"**Timestamp:** {s.timestamp_label}")
        lines.append("")
        if s.screenshot_path and os.path.exists(s.screenshot_path):
            rel = os.path.relpath(s.screenshot_path, out_dir).replace(os.sep, "/")
            lines.append(f"![{s.screenshot_caption}]({rel})")
            lines.append("")
            lines.append(f"*{s.screenshot_caption}*")
            lines.append("")
        lines.append(s.narration)
        lines.append("")
        if s.on_screen:
            lines.append(f"> **On-screen detail:** {s.on_screen}")
            lines.append("")
        lines.append("---")
        lines.append("")

    lines.append("## Closing Notes")
    lines.append("")
    lines.append(doc.closing)
    lines.append("")

    os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return out_path
