"""CLI entrypoint: YouTube URL -> illustrated blog-post PDF.

Usage:
    python -m vidblog "https://www.youtube.com/watch?v=XXXXXXXXXXX"
"""
from __future__ import annotations

import argparse
import os
import time

from dotenv import load_dotenv

from vidblog import downloader, pdf_builder, screenshots, segmenter, transcript, writer


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Turn a YouTube video into an illustrated blog-post PDF.")
    parser.add_argument("url", help="YouTube video URL")
    parser.add_argument("--out", default="output", help="Root output directory (default: output)")
    parser.add_argument("--no-llm", action="store_true", help="Skip the LLM writer even if ANTHROPIC_API_KEY is set")
    parser.add_argument("--max-screenshots", type=int, default=14, help="Maximum number of screenshots to include")
    parser.add_argument(
        "--section-seconds", type=float, default=150.0,
        help="Target seconds of video per blog section (default: 150s ~= one section per 2.5 min)",
    )
    parser.add_argument("--whisper-model", default="small", help="faster-whisper model size if no captions exist")
    return parser


def run(args: argparse.Namespace) -> str:
    load_dotenv()
    t0 = time.time()

    print("[1/6] Downloading video + captions...")
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
    sections = segmenter.make_sections(events, assets.duration, target_section_seconds=args.section_seconds)
    print(f"      -> {len(sections)} sections")
    sections_path = writer.dump_sections(assets, sections)
    print(f"      -> raw per-section transcript dumped to {sections_path}")

    print("[4/6] Extracting and scoring screenshots...")
    shots = screenshots.select_screenshots(
        assets.video_path, sections, assets.work_dir, max_total=args.max_screenshots
    )
    print(f"      -> {len(shots)} screenshots selected")

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
