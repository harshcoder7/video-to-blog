# Video Knowledge Graph

Turn YouTube videos into a browsable, conversational knowledge base:
**vidblog** ingests a video into a transcript + best-frame screenshots + an
illustrated blog PDF; **kgwiki** turns everything ingested into an
Obsidian-style knowledge graph you can search and chat with — including
real, local LLM-generated answers with zero API cost.

## Quickstart (Docker, recommended)

Needs Docker Desktop. An NVIDIA GPU makes the local LLM fast (tested on a
4GB RTX 3050 laptop GPU); without one, Ollama falls back to CPU (slower, still
works) or you can skip it entirely and use the free extractive fallback.

```
docker compose up -d --build
docker exec kgwiki-ollama ollama pull qwen2.5:3b
docker exec kgwiki-ollama ollama pull nomic-embed-text
```

Open **http://localhost:8765** — paste a YouTube URL right into the Library
view to ingest your first video (no CLI needed). Everything runs in two
containers: `kgwiki-app` (the web app + ingestion pipeline) and
`kgwiki-ollama` (the local LLM), talking to each other over Docker's internal
network. Both restart automatically with Docker Desktop.

## What's inside

### kgwiki — the knowledge graph app

Sidebar has three views:

- **Graph** — a force-directed, drag/zoom/pan graph (teal = video, indigo =
  section, amber = topic/keyword). Hover to highlight connections, type in
  the filter box to dim everything except matching nodes, click a node to
  open its detail panel (full text, screenshot, timestamped link back to the
  original video).
- **Ask** — a real conversation, not just search: ask a question, get a
  generated answer plus the source sections (screenshot, heading,
  timestamp) it drew from. Follow-up questions work ("how much does it
  pay?" after "what is FDE?" correctly resolves "it"). Ctrl/Cmd+K focuses
  the input from anywhere; every answer has a copy button.
- **Library** — paste a YouTube URL to ingest a new video directly from the
  UI, with a live progress log. Also lists every video already ingested,
  each expandable into its ordered section list.

### Answer quality, in priority order (all free)

1. **Local Ollama model** (`qwen2.5:3b` generation + `nomic-embed-text`
   embeddings) if running — genuine generated answers grounded in your
   videos' actual transcripts via semantic search, but it'll also answer
   general questions normally, like a real assistant. Fully offline.
2. **Anthropic API**, only if `ANTHROPIC_API_KEY` is set and Ollama isn't
   reachable.
3. **Extractive fallback** — quotes the actual answering sentence(s)
   straight out of the transcript. No model needed at all, always works.

Topic/keyword nodes in the graph are also extracted for free (TF-IDF +
capitalized-phrase heuristics), so the whole app can run with zero API cost
and zero paid dependencies if you want.

Check what's currently active at `GET /api/llm_status`, or look at the
status pill in the Ask view.

### vidblog — the ingestion pipeline

Runs automatically when you paste a URL into the UI, or standalone via CLI:

```
venv\Scripts\python -m vidblog "https://www.youtube.com/watch?v=XXXXXXXXXXX"
```

Downloads the video + captions, splits the transcript into sections, picks
the sharpest/most representative screenshot per section, writes blog prose
(LLM if `ANTHROPIC_API_KEY` is set, otherwise a free rule-based cleanup
writer), and renders an illustrated PDF. Output lands in
`output/<video_id>/`:

- `blog.pdf` — the illustrated blog post
- `screenshots/` — the selected frame per section
- `sections.json` — raw transcript per section (also the source material for
  hand-authoring a `blog_override.json` in the same schema, if you want to
  write the prose yourself — drop it in the video's folder and rerun; it
  takes priority over both writers)

Downloaded video/captions and extracted candidate frames are cached per
video ID (portable between a local Windows run and the Docker container
sharing the same `output/` folder), so rerunning against the same URL is fast.

CLI options: `--no-llm`, `--max-screenshots N` (default 14), `--section-seconds N`
(default 150), `--whisper-model SIZE` (used only if a video has no captions
at all, falls back to local transcription).

## Local (non-Docker) setup

```
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python -m kgwiki.server
```

Opens at `http://127.0.0.1:8765` (`KGWIKI_PORT` to change it,
`KGWIKI_NO_BROWSER=1` to stop it auto-opening a browser tab). For the local
LLM tier without Docker, run Ollama natively and set `OLLAMA_URL` if it's
not on the default `http://localhost:11434`.

Optional: copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY`.

It rescans `output/` at startup; hit the refresh icon in the sidebar after
ingesting a video outside the UI (e.g. via the CLI) instead of restarting.

## Configuration reference

| Env var | Default | Notes |
|---|---|---|
| `KGWIKI_PORT` | `8765` | kgwiki server port |
| `KGWIKI_HOST` | `127.0.0.1` | set to `0.0.0.0` in containers so it's reachable from outside |
| `KGWIKI_NO_BROWSER` | unset | `1` to skip auto-opening a browser tab |
| `OLLAMA_URL` | `http://localhost:11434` | `http://ollama:11434` inside docker-compose |
| `OLLAMA_CHAT_MODEL` | `qwen2.5:3b` | any Ollama chat model |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text` | any Ollama embedding model |
| `ANTHROPIC_API_KEY` | unset | optional, see priority order above |
