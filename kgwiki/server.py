"""FastAPI app serving the knowledge-graph API and its frontend.

Run with:  python -m kgwiki.server
"""
from __future__ import annotations

import os
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


class QueryRequest(BaseModel):
    q: str
    top_k: int = 8
    synthesize: bool = True


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

    answer = None
    source = None
    if req.synthesize:
        answer = generate_rag_answer(req.q, matches, overviews)
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

    port = int(os.environ.get("KGWIKI_PORT", "8765"))
    url = f"http://127.0.0.1:{port}"
    if os.environ.get("KGWIKI_NO_BROWSER") != "1":
        Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"Video Knowledge Graph running at {url}")
    uvicorn.run("kgwiki.server:app", host="127.0.0.1", port=port, reload=False)


if __name__ == "__main__":
    main()
