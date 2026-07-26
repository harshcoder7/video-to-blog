# vidblog

Turn a YouTube video into an illustrated blog-post PDF: transcript, best-frame
screenshots per section, clean prose, and a properly laid-out PDF.

## Setup

```
python -m venv venv
venv\Scripts\pip install -r requirements.txt
```

Optional: copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY` to get
LLM-written blog prose (recommended). Without a key, the tool falls back to
a free rule-based writer that cleans up the transcript but won't read as
polished prose.

## Usage

```
venv\Scripts\python -m vidblog "https://www.youtube.com/watch?v=XXXXXXXXXXX"
```

Output lands in `output/<video_id>/`:
- `blog.pdf` — the final illustrated blog post
- `screenshots/` — the selected frame per section
- `sections.json` — raw transcript per section (useful for debugging, or for
  hand-authoring a `blog_override.json` in the same schema if you want to
  write the prose yourself instead of using the LLM/rule-based writer —
  drop it in the video's folder and rerun; it takes priority over both).

Downloaded video/captions and extracted candidate frames are cached per
video ID, so rerunning against the same URL (e.g. after editing
`blog_override.json`) is fast.

## Options

- `--no-llm` — skip the LLM writer even if `ANTHROPIC_API_KEY` is set
- `--max-screenshots N` — cap total screenshots (default 14)
- `--section-seconds N` — target seconds of video per section (default 150)
- `--whisper-model SIZE` — faster-whisper model size used only if the video has no captions at all
