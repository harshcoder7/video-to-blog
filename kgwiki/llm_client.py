"""Thin client for a locally-running Ollama instance (e.g. via docker-compose.yml
in this repo). Fully free, fully offline, no API key -- everything here fails
soft (returns None) so callers can fall back to the TF-IDF/extractive path if
Ollama isn't running.
"""
from __future__ import annotations

import os

import requests

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
CHAT_MODEL = os.environ.get("OLLAMA_CHAT_MODEL", "qwen2.5:3b")
EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")

_TIMEOUT_SHORT = 2.0
_TIMEOUT_CHAT = 60.0
_TIMEOUT_EMBED = 20.0

def is_available() -> bool:
    """Live reachability check (localhost, so this is cheap -- a few ms).
    Deliberately not cached: Ollama can start up or go away between calls
    (e.g. the container is still pulling/starting), and a stale "down"
    result would wrongly disable RAG for the rest of the process."""
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=_TIMEOUT_SHORT)
        return resp.ok
    except requests.RequestException:
        return False


def embed(text: str) -> list[float] | None:
    if not text or not text.strip():
        return None
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text},
            timeout=_TIMEOUT_EMBED,
        )
        resp.raise_for_status()
        vec = resp.json().get("embedding")
        return vec if vec else None
    except requests.RequestException:
        return None


def embed_many(texts: list[str]) -> list[list[float] | None]:
    return [embed(t) for t in texts]


def chat(system: str, user: str, temperature: float = 0.3, max_tokens: int = 500) -> str | None:
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": CHAT_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
                "options": {"temperature": temperature, "num_predict": max_tokens},
            },
            timeout=_TIMEOUT_CHAT,
        )
        resp.raise_for_status()
        return resp.json().get("message", {}).get("content", "").strip() or None
    except requests.RequestException:
        return None
