"""CLI entrypoint: video -> illustrated blog-post PDF, or process-audit Markdown.

Usage:
    python -m vidblog "https://www.youtube.com/watch?v=XXXXXXXXXXX"
    python -m vidblog "C:\\path\\to\\local-recording.mp4"
"""
from __future__ import annotations

import argparse
import os
import time

from dotenv import load_dotenv

from vidblog import audit_writer, downloader, local_source, md_builder, pdf_builder, screenshots, segmenter, transcript, writer


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Turn a video into an illustrated blog-post PDF, or (for local process-walkthrough "
        "recordings) a structured process-audit Markdown document."
    )
    parser.add_argument("url", help="YouTube video URL, or the path to a local video file")
    parser.add_argument("--out", default="output", help="Root output directory (default: output)")
    parser.add_argument(
        "--doc-type", choices=["blog", "audit"], default=None,
        help="Output style. Defaults to 'audit' for local files, 'blog' for YouTube URLs.",
    )
    parser.add_argument("--title", default=None, help="Override the display title (local files only)")
    parser.add_argument(
        "--no-llm", action="store_true",
        help="Skip LLM writing even if available (Anthropic for blog mode, local Ollama for audit mode)",
    )
    parser.add_argument(
        "--max-screenshots", type=int, default=None,
        help="Maximum number of screenshots (default: 14 for blog, 50 for audit)",
    )
    parser.add_argument(
        "--section-seconds", type=float, default=None,
        help="Target seconds of video per section (default: 150 for blog, 90 for audit -- "
        "audit mode wants finer-grained steps for a thorough walkthrough)",
    )
    parser.add_argument("--whisper-model", default="small", help="faster-whisper model size if no captions exist")
    return parser


def run(args: argparse.Namespace) -> str:
    load_dotenv()
    t0 = time.time()

    is_local = local_source.is_local_file(args.url)
    doc_type = args.doc_type or ("audit" if is_local else "blog")
    section_seconds = args.section_seconds or (90.0 if doc_type == "audit" else 150.0)
    max_screenshots = args.max_screenshots or (50 if doc_type == "audit" else 14)

    print("[1/6] Loading video...")
    if is_local:
        assets = local_source.ingest_local_file(args.url, args.out, title=args.title)
        print(f"      -> \"{assets.title}\" (local file, {assets.duration}s)")
    else:
        assets = downloader.download(args.url, args.out)
        print(f"      -> \"{assets.title}\" by {assets.uploader} ({assets.duration}s)")

    print("[2/6] Loading transcript...")
    if assets.subtitle_path:
        kind = "auto-generated" if assets.subtitle_is_auto else "manual"
        print(f"      Using {kind} captions: {os.path.basename(assets.subtitle_path)}")
        events = transcript.load_transcript(assets.subtitle_path)
    else:
        print("      No captions found; transcribing locally with faster-whisper (this can take a while)...")
        events = transcript.transcribe_with_whisper(assets.video_path, model_size=args.whisper_model)
    print(f"      -> {len(events)} words of transcript")

    if not events:
        raise RuntimeError("Could not obtain any transcript for this video.")

    print("[3/6] Splitting transcript into sections...")
    sections = segmenter.make_sections(events, assets.duration, target_section_seconds=section_seconds)
    print(f"      -> {len(sections)} sections")
    sections_path = writer.dump_sections(assets, sections)
    print(f"      -> raw per-section transcript dumped to {sections_path}")

    print("[4/6] Extracting and scoring screenshots...")
    shots = screenshots.select_screenshots(
        assets.video_path, sections, assets.work_dir, max_total=max_screenshots
    )
    print(f"      -> {len(shots)} screenshots selected")

    if doc_type == "audit":
        print("[5/6] Writing audit-doc copy (transcript + local VLM frame captions)...")
        doc = audit_writer.generate_audit_doc(assets, sections, shots, use_llm=not args.no_llm)
        print(f"      -> {'Ollama-written' if doc.used_llm else 'rule-based (Ollama unavailable)'} steps, "
              f"{sum(1 for s in doc.steps if s.on_screen)} frames captioned")

        print("[6/6] Rendering Markdown...")
        out_md = os.path.join(assets.work_dir, "audit.md")
        md_builder.build_markdown(doc, out_md)
        override_path = audit_writer.dump_blog_override(doc, assets)
        print(f"      -> also wrote {override_path} so the knowledge graph shows this same polished text")
        elapsed = time.time() - t0
        print(f"\nDone in {elapsed:.0f}s -> {out_md}")
        return out_md

    print("[5/6] Writing blog copy...")
    post = writer.generate_blog(assets, sections, use_llm=not args.no_llm)
    for sec in post.sections:
        path = shots.get(sec.index)
        if path:
            sec.screenshot_path = path
            sec.screenshot_caption = f"Captured at {sec.timestamp_label}"
    print(f"      -> {'LLM-written' if post.used_llm else 'rule-based (no API key found)'} prose")

    print("[6/6] Rendering PDF...")
    out_pdf = os.path.join(assets.work_dir, "blog.pdf")
    upload_date = assets.upload_date
    date_label = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}" if len(upload_date) == 8 else ""
    byline = f"By {assets.uploader}" + (f" • {date_label}" if date_label else "")
    pdf_builder.build_pdf(post, out_pdf, thumbnail_path=assets.thumbnail_path, byline_extra=byline)

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.0f}s -> {out_pdf}")
    return out_pdf


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
