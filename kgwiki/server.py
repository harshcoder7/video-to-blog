"""FastAPI app serving the knowledge-graph API and its frontend.

Run with:  python -m kgwiki.server
"""
from __future__ import annotations

import contextlib
import io
import os
import re
import threading
import time
import traceback
import uuid
import webbrowser
from threading import Timer

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from kgwiki import llm_client
from kgwiki.graph_builder import build_graph
from kgwiki.search import (
    SearchIndex,
    chitchat_reply,
    extractive_answer,
    generate_rag_answer,
    synthesize_answer,
)

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_ROOT = os.path.join(BASE_DIR, "output")
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

app = FastAPI(title="Video Knowledge Graph")

_state: dict = {"graph": None, "index": None}


def _load() -> None:
    graph = build_graph(OUT_ROOT)
    _state["graph"] = graph
    _state["index"] = SearchIndex(graph)


_load()

if os.path.isdir(OUT_ROOT):
    app.mount("/media", StaticFiles(directory=OUT_ROOT), name="media")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/graph")
def get_graph():
    return _state["graph"].to_json()


@app.post("/api/refresh")
def refresh():
    _load()
    return {"ok": True, "node_count": len(_state["graph"].nodes)}


@app.get("/api/node/{node_id}")
def get_node(node_id: str):
    for n in _state["graph"].nodes:
        if n.id == node_id:
            return {"id": n.id, "type": n.type, "label": n.label, **n.data}
    raise HTTPException(status_code=404, detail="node not found")


@app.get("/api/videos")
def list_videos():
    graph = _state["graph"]
    videos = [
        {"id": n.id, "label": n.label, **n.data}
        for n in graph.nodes
        if n.type == "video"
    ]
    sections_by_video: dict[str, list] = {}
    for n in graph.nodes:
        if n.type == "section":
            sections_by_video.setdefault(n.data["video_id"], []).append(
                {"id": n.id, "label": n.label, "start": n.data.get("start"), "end": n.data.get("end")}
            )
    for v in videos:
        vid = v["id"].split(":", 1)[1]
        v["sections"] = sorted(
            sections_by_video.get(vid, []), key=lambda s: s["id"]
        )
    return videos


_YOUTUBE_URL_RE = re.compile(r"^https?://(www\.)?(youtube\.com/watch\?v=|youtu\.be/)[\w-]+", re.IGNORECASE)

_ingest_lock = threading.Lock()
_ingest_state: dict = {
    "status": "idle",  # idle | running | done | error
    "job_id": None,
    "url": None,
    "log": [],
    "error": None,
    "started_at": None,
    "finished_at": None,
}


class _TeeWriter(io.TextIOBase):
    """Captures the vidblog pipeline's print() progress output line by line
    into the job log, so the UI can show it as a live-updating feed."""

    def __init__(self, log: list[str]):
        self._log = log
        self._buf = ""

    def write(self, s: str) -> int:
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self._log.append(line)
        return len(s)

    def flush(self) -> None:
        pass


class IngestRequest(BaseModel):
    url: str


def _run_ingest_job(url: str) -> None:
    from vidblog import cli as vidblog_cli

    writer = _TeeWriter(_ingest_state["log"])
    try:
        args = vidblog_cli.build_arg_parser().parse_args([url])
        with contextlib.redirect_stdout(writer):
            vidblog_cli.run(args)
        _ingest_state["status"] = "done"
    except Exception as exc:  # surface the real error to the UI rather than a bare 500
        _ingest_state["status"] = "error"
        _ingest_state["error"] = str(exc)
        _ingest_state["log"].append(f"ERROR: {exc}")
        _ingest_state["log"].append(traceback.format_exc())
    finally:
        _ingest_state["finished_at"] = time.time()
        try:
            _load()  # pick up the new video immediately, no manual refresh needed
        except Exception:
            pass  # don't let a graph-rebuild hiccup mask the ingest result
        _ingest_lock.release()


@app.post("/api/ingest")
def start_ingest(req: IngestRequest):
    url = req.url.strip()
    if not _YOUTUBE_URL_RE.match(url):
        raise HTTPException(status_code=400, detail="That doesn't look like a YouTube URL.")
    if not _ingest_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409, detail="A video is already being processed -- check /api/ingest/status."
        )

    _ingest_state.update(
        {
            "status": "running",
            "job_id": str(uuid.uuid4()),
            "url": url,
            "log": [f"Starting ingestion for {url}..."],
            "error": None,
            "started_at": time.time(),
            "finished_at": None,
        }
    )
    threading.Thread(target=_run_ingest_job, args=(url,), daemon=True).start()
    return {"job_id": _ingest_state["job_id"], "status": "running"}


@app.get("/api/ingest/status")
def ingest_status():
    return {
        "status": _ingest_state["status"],
        "job_id": _ingest_state["job_id"],
        "url": _ingest_state["url"],
        "log": _ingest_state["log"][-300:],
        "error": _ingest_state["error"],
    }


class ChatTurn(BaseModel):
    role: str
    content: str


class QueryRequest(BaseModel):
    q: str
    top_k: int = 8
    synthesize: bool = True
    history: list[ChatTurn] = []


@app.get("/api/llm_status")
def llm_status():
    return {
        "ollama_available": llm_client.is_available(),
        "chat_model": llm_client.CHAT_MODEL,
        "embed_model": llm_client.EMBED_MODEL,
        "embeddings_ready": getattr(_state["index"], "embeddings_ready", False),
        "anthropic_key_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
    }


@app.post("/api/query")
def query(req: QueryRequest):
    chitchat = chitchat_reply(req.q)
    if chitchat:
        return {"query": req.q, "answer": chitchat, "matches": [], "answer_source": "chitchat"}

    index: SearchIndex = _state["index"]
    matches = index.query(req.q, top_k=req.top_k)
    overviews = index.all_video_overviews()

    history = [{"role": t.role, "content": t.content} for t in req.history]

    answer = None
    source = None
    if req.synthesize:
        answer = generate_rag_answer(req.q, matches, overviews, history=history)
        if answer:
            source = "ollama"
    if not answer and req.synthesize:
        answer = synthesize_answer(req.q, matches)
        if answer:
            source = "anthropic"
    if not answer:
        answer = extractive_answer(req.q, matches)
        if answer:
            source = "extractive"
    if not answer and not matches:
        answer = "I couldn't find anything about that in your videos yet. Try rephrasing, or ingest more videos with vidblog."
        source = "none"

    return {"query": req.q, "answer": answer, "matches": matches, "answer_source": source}


def main():
    import uvicorn

    host = os.environ.get("KGWIKI_HOST", "127.0.0.1")
    port = int(os.environ.get("KGWIKI_PORT", "8765"))
    url = f"http://127.0.0.1:{port}"
    if os.environ.get("KGWIKI_NO_BROWSER") != "1":
        Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"Video Knowledge Graph running at {url}")
    uvicorn.run("kgwiki.server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
