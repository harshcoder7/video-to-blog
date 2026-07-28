"""Thin client for a locally-running Ollama instance (e.g. via docker-compose.yml
in this repo). Fully free, fully offline, no API key -- everything here fails
soft (returns None) so callers can fall back to the TF-IDF/extractive path if
Ollama isn't running.
"""
from __future__ import annotations

import base64
import os

import requests

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
CHAT_MODEL = os.environ.get("OLLAMA_CHAT_MODEL", "qwen2.5:3b")
EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")
VISION_MODEL = os.environ.get("OLLAMA_VISION_MODEL", "moondream")

_TIMEOUT_SHORT = 2.0
_TIMEOUT_CHAT = 60.0
_TIMEOUT_EMBED = 20.0
_TIMEOUT_VISION = 60.0

_DEFAULT_CAPTION_PROMPT = (
    "This is a screenshot from a screen-recorded walkthrough of a business software "
    "system. Describe factually what is on screen: name the application/system or "
    "screen if identifiable, and list the key visible fields, labels, buttons, tabs, "
    "or data (e.g. names, statuses, numbers) exactly as shown. Do not guess at things "
    "you cannot actually read. Be concise -- a few sentences."
)

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
    """Embed multiple texts in as few HTTP round-trips as possible.

    Ollama's newer /api/embed endpoint accepts a batch "input" list and
    returns all embeddings in one call/model-load, instead of one HTTP
    request per text -- meaningful when ingesting a new video with dozens
    of sections that all need embedding at once. Falls back to embedding
    one at a time if the batch endpoint isn't available (older Ollama) or
    the batch call fails outright, so this never makes things worse.
    """
    non_empty = [(i, t) for i, t in enumerate(texts) if t and t.strip()]
    results: list[list[float] | None] = [None] * len(texts)
    if not non_empty:
        return results

    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/embed",
            json={"model": EMBED_MODEL, "input": [t for _, t in non_empty]},
            timeout=_TIMEOUT_EMBED * max(1, len(non_empty) // 8),
        )
        resp.raise_for_status()
        embeddings = resp.json().get("embeddings")
        if embeddings and len(embeddings) == len(non_empty):
            for (i, _), vec in zip(non_empty, embeddings):
                results[i] = vec
            return results
    except requests.RequestException:
        pass

    for i, t in non_empty:
        results[i] = embed(t)
    return results


def chat_messages(messages: list[dict], temperature: float = 0.3, max_tokens: int = 500) -> str | None:
    """messages: list of {"role": "system"|"user"|"assistant", "content": str}."""
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": CHAT_MODEL,
                "messages": messages,
                "stream": False,
                "options": {"temperature": temperature, "num_predict": max_tokens},
            },
            timeout=_TIMEOUT_CHAT,
        )
        resp.raise_for_status()
        return resp.json().get("message", {}).get("content", "").strip() or None
    except requests.RequestException:
        return None


def chat(system: str, user: str, temperature: float = 0.3, max_tokens: int = 500) -> str | None:
    return chat_messages(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=temperature,
        max_tokens=max_tokens,
    )


def caption_image(image_path: str, prompt: str = _DEFAULT_CAPTION_PROMPT) -> str | None:
    """Ask the local vision model (moondream by default) what's on screen in
    an image -- used to describe UI/system screenshots that a transcript
    alone can't capture (e.g. "click here" with no idea what "here" was)."""
    try:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": VISION_MODEL,
                "messages": [{"role": "user", "content": prompt, "images": [b64]}],
                "stream": False,
                "options": {"temperature": 0.2, "num_predict": 300},
            },
            timeout=_TIMEOUT_VISION,
        )
        resp.raise_for_status()
        return resp.json().get("message", {}).get("content", "").strip() or None
    except (requests.RequestException, OSError):
        return None
