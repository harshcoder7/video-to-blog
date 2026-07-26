"""Query the knowledge graph.

Retrieval: semantic search via local Ollama embeddings when available
(falls back to TF-IDF keyword search otherwise, always works, zero setup).

Answering, in priority order:
  1. Local Ollama chat model (qwen2.5:3b by default) -- real generative RAG,
     free, fully offline, handles both video-specific and general questions.
  2. Anthropic API, only if ANTHROPIC_API_KEY is set and Ollama isn't running.
  3. Free extractive fallback -- pulls the actual answering sentence(s)
     straight out of the transcript, no model needed at all.
"""
from __future__ import annotations

import hashlib
import json
import os
import re

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from kgwiki import llm_client
from kgwiki.models import Graph, Node

_CACHE_VERSION = 1


def _text_hash(model: str, text: str) -> str:
    return hashlib.sha256(f"{_CACHE_VERSION}:{model}:{text}".encode("utf-8")).hexdigest()


class EmbeddingCache:
    """Disk-persisted cache of section_id -> embedding, keyed by a hash of
    (model, text) so edits to a section or a model switch auto-invalidate."""

    def __init__(self, path: str):
        self.path = path
        self._data: dict[str, dict] = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._data = {}
        self._dirty = False

    def get(self, key: str, model: str, text: str) -> list[float] | None:
        entry = self._data.get(key)
        if entry and entry.get("hash") == _text_hash(model, text):
            return entry.get("embedding")
        return None

    def set(self, key: str, model: str, text: str, embedding: list[float]) -> None:
        self._data[key] = {"hash": _text_hash(model, text), "embedding": embedding}
        self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._data, f)
            self._dirty = False
        except OSError:
            pass


class SearchIndex:
    def __init__(self, graph: Graph, cache_path: str = "output/.kgwiki_cache.json"):
        self.sections: list[Node] = [n for n in graph.nodes if n.type == "section"]
        self.videos_by_id: dict[str, Node] = {
            n.id.split(":", 1)[1]: n for n in graph.nodes if n.type == "video"
        }
        texts = [f"{n.label}. {n.data.get('text', '')}" for n in self.sections]

        # TF-IDF: always built, it's the guaranteed-available fallback.
        if texts:
            self.vectorizer = TfidfVectorizer(
                stop_words="english", ngram_range=(1, 2), min_df=1, max_df=0.6, sublinear_tf=True,
            )
            self.matrix = self.vectorizer.fit_transform(texts)
        else:
            self.vectorizer = None
            self.matrix = None

        # Embeddings: only if a local Ollama is actually reachable right now.
        self.embeddings: np.ndarray | None = None
        self.embeddings_ready = False
        if self.sections and llm_client.is_available():
            cache = EmbeddingCache(cache_path)
            vectors = []
            ok = True
            for n, text in zip(self.sections, texts):
                vec = cache.get(n.id, llm_client.EMBED_MODEL, text)
                if vec is None:
                    vec = llm_client.embed(text)
                    if vec is None:
                        ok = False
                        break
                    cache.set(n.id, llm_client.EMBED_MODEL, text, vec)
                vectors.append(vec)
            cache.save()
            if ok and vectors:
                self.embeddings = np.array(vectors, dtype=np.float32)
                self.embeddings_ready = True

    def _query_embedding(self, q: str, top_k: int) -> list[dict] | None:
        if not self.embeddings_ready:
            return None
        qvec = llm_client.embed(q)
        if qvec is None:
            return None
        qvec = np.array(qvec, dtype=np.float32).reshape(1, -1)
        sims = cosine_similarity(qvec, self.embeddings)[0]
        ranked = sims.argsort()[::-1]
        results = []
        for i in ranked[: top_k * 3]:
            if sims[i] <= 0.35:
                continue
            results.append(self._to_result(self.sections[i], float(sims[i])))
            if len(results) >= top_k:
                break
        return results

    def _query_tfidf(self, q: str, top_k: int) -> list[dict]:
        if not self.sections or self.vectorizer is None:
            return []
        qvec = self.vectorizer.transform([q])
        sims = cosine_similarity(qvec, self.matrix)[0]
        ranked = sims.argsort()[::-1]
        results = []
        for i in ranked[: top_k * 3]:
            if sims[i] <= 0.02:
                continue
            results.append(self._to_result(self.sections[i], float(sims[i])))
            if len(results) >= top_k:
                break
        return results

    def _to_result(self, n: Node, score: float) -> dict:
        return {
            "id": n.id,
            "heading": n.label,
            "video_id": n.data.get("video_id"),
            "video_title": n.data.get("video_title"),
            "excerpt": n.data.get("excerpt"),
            "text": n.data.get("text"),
            "screenshot": n.data.get("screenshot"),
            "start": n.data.get("start"),
            "end": n.data.get("end"),
            "timestamp_url": n.data.get("timestamp_url"),
            "score": round(score, 4),
        }

    def query(self, q: str, top_k: int = 6) -> list[dict]:
        embedding_results = self._query_embedding(q, top_k)
        if embedding_results is not None:
            return embedding_results
        return self._query_tfidf(q, top_k)

    def video_overviews(self, video_ids: list[str]) -> list[tuple[str, str]]:
        """(title, overview_text) pairs for the given video ids, for RAG context."""
        out = []
        for vid in dict.fromkeys(video_ids):  # dedupe, preserve order
            node = self.videos_by_id.get(vid)
            if node and node.data.get("overview_text"):
                out.append((node.label, node.data["overview_text"]))
        return out

    def all_video_overviews(self) -> list[tuple[str, str]]:
        """Every ingested video's (title, overview_text), regardless of what
        section-level retrieval found. Always included in RAG context so
        whole-video meta-questions ("what is this about?") work even when
        they share no vocabulary with any single section -- the corpus of
        videos is small enough that this costs little and never hurts."""
        return [
            (n.label, n.data["overview_text"])
            for n in self.videos_by_id.values()
            if n.data.get("overview_text")
        ]


_GREETING_RE = re.compile(r"^\s*(hi|hello|hey+|yo|sup|howdy|good\s?(morning|afternoon|evening))\b", re.IGNORECASE)
_HOW_ARE_YOU_RE = re.compile(r"how('?s| is| are) (you|u|it going)|what'?s up\??$", re.IGNORECASE)
_THANKS_RE = re.compile(r"^\s*(thanks|thank you|thx|ty)\b", re.IGNORECASE)
_BYE_RE = re.compile(r"^\s*(bye|goodbye|see (you|ya)|later|ok(ay)? bye)\b", re.IGNORECASE)
_WHOAMI_RE = re.compile(r"who are you|what are you|what can you do|what do you do|^\s*help\s*\??\s*$", re.IGNORECASE)


def chitchat_reply(query: str) -> str | None:
    """Handle small talk locally and for free -- no need to hit the search
    index (or an LLM) just to answer "hi, how are you?". Only fires for pure
    small talk; the RAG model handles everything else, including questions
    that mix chit-chat with a real question."""
    q = query.strip()
    if not q:
        return None
    if _WHOAMI_RE.search(q):
        return (
            "I'm your video knowledge base assistant. Ask me anything about the videos "
            "you've ingested with vidblog and I'll search the transcripts and pull back "
            "the relevant text, screenshots, and timestamps. Try something like "
            '"What is an FDE?" or "how much can I earn?"'
        )
    if _HOW_ARE_YOU_RE.search(q):
        return "Doing well, thanks for asking! Ask me anything about the videos in your library whenever you're ready."
    if _THANKS_RE.search(q):
        return "You're welcome! Let me know if you want to dig into anything else."
    if _BYE_RE.search(q):
        return "See you around -- your graph will be here whenever you want to come back."
    if _GREETING_RE.match(q):
        return "Hey! I'm ready when you are -- ask me anything about the videos you've processed."
    return None


_QUESTION_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "of",
    "to", "in", "on", "at", "for", "with", "and", "or", "but", "if", "then",
    "so", "than", "this", "that", "these", "those", "it", "its", "i", "you",
    "he", "she", "they", "we", "what", "which", "who", "whom", "how", "why",
    "when", "where", "do", "does", "did", "can", "could", "should", "would",
    "will", "much", "many", "about", "me", "my", "your",
}


def _keywords(text: str) -> set[str]:
    return {
        w.lower()
        for w in re.findall(r"[A-Za-z][A-Za-z'-]*", text)
        if w.lower() not in _QUESTION_STOPWORDS and len(w) > 2
    }


_DEFINITION_RE = re.compile(r"^\s*(what|who)\s+(is|are|was|were)\s+(?:an?\s+|the\s+)?(.+?)\??\s*$", re.IGNORECASE)


def extractive_answer(query: str, results: list[dict], max_sentences: int = 3) -> str | None:
    """Free, no-model fallback: pull the sentence(s) most relevant to the
    query directly out of the top matching sections, verbatim."""
    if not results:
        return None
    keywords = _keywords(query)
    if not keywords:
        return None

    def_match = _DEFINITION_RE.match(query.strip())
    def_term = def_match.group(3).strip().lower() if def_match else None

    scored: list[tuple[float, str, str, str]] = []
    for rank, r in enumerate(results):
        text = r.get("text") or ""
        rank_bonus = max(0, (len(results) - rank)) * 0.15
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            words = _keywords(sentence)
            overlap = len(words & keywords)
            if overlap == 0:
                continue
            score = overlap + rank_bonus
            if def_term and (
                re.search(
                    rf"\b{re.escape(def_term)}\b[^.]{{0,60}}\b(is|are|refers? to|means?)\b",
                    sentence,
                    re.IGNORECASE,
                )
                or re.search(rf"\(\s*{re.escape(def_term)}\s*\)", sentence, re.IGNORECASE)
            ):
                score += 5
            scored.append((score, sentence.strip(), r["heading"], r["video_title"]))

    if not scored:
        return None
    scored.sort(key=lambda t: -t[0])
    top = scored[:max_sentences]
    quote = " ".join(s[1] for s in top)
    return f'{quote}\n\n(from "{top[0][2]}" in {top[0][3]})'


_RAG_SYSTEM_PROMPT = """You are a friendly, sharp assistant embedded in someone's personal \
video knowledge base. You can see excerpts from the videos they've processed (transcript \
sections and an overview of each video), provided below as context.

Rules:
- If the context answers the question, answer using it directly and naturally -- don't say \
"according to the context". Mention the source video by title when it's useful, and feel \
free to reference timestamps if given.
- If the context is only partially relevant, use what's useful and say plainly what isn't covered.
- If the question has nothing to do with the videos (general knowledge, casual conversation, \
math, whatever), just answer it normally like a helpful assistant would -- don't force it to \
be about the videos.
- CRITICAL: never invent structure that isn't explicitly in the context -- no extra steps, \
weeks, stages, numbers, or names beyond what's written there. If the context only shows some \
of a sequence (e.g. week 1 and week 2 of a plan but not the rest), summarize only those and \
say plainly that the rest isn't in view, rather than guessing or inventing what a "week 3" or \
"week 4" might contain. When unsure whether something is stated in the context, leave it out.
- Be concise: a few sentences unless the question genuinely needs more."""


_MAX_HISTORY_TURNS = 3  # user+assistant pairs; keeps context within the model's ctx window


def generate_rag_answer(
    query: str,
    matches: list[dict],
    overviews: list[tuple[str, str]],
    history: list[dict] | None = None,
) -> str | None:
    """history: prior turns as [{"role": "user"|"assistant", "content": str}, ...],
    oldest first. Retrieval is redone fresh for the current query every time
    (so the context always reflects what's actually relevant right now) --
    history only gives the model conversational memory for things like
    pronouns and follow-ups ("what about the pricing?")."""
    if not llm_client.is_available():
        return None

    context_parts = []
    for title, overview in overviews:
        context_parts.append(f'[Video overview: "{title}"]\n{overview}')
    for r in matches[:8]:
        context_parts.append(
            f"[{r['video_title']} — {r['heading']} ({r['start']}-{r['end']})]\n{r['text']}"
        )
    context = "\n\n".join(context_parts) if context_parts else "(No matching video content found for this question.)"

    messages = [{"role": "system", "content": _RAG_SYSTEM_PROMPT}]
    if history:
        messages.extend(history[-_MAX_HISTORY_TURNS * 2 :])
    messages.append({"role": "user", "content": f"Context:\n\n{context}\n\nQuestion: {query}"})

    return llm_client.chat_messages(messages, temperature=0.4, max_tokens=500)


_SYNTH_SYSTEM_PROMPT = """You answer questions about a personal video knowledge base \
using only the excerpts provided. Be concise (2-4 sentences). If the excerpts don't \
actually answer the question, say so plainly instead of guessing. Do not invent facts \
not present in the excerpts. Refer to specific videos by title when useful."""


def synthesize_answer(query: str, results: list[dict], model: str = "claude-sonnet-5") -> str | None:
    if not results or not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic

        client = anthropic.Anthropic()
        context = "\n\n".join(
            f"[{r['video_title']} — {r['heading']} ({r['start']}-{r['end']})]\n{r['text']}"
            for r in results[:5]
        )
        resp = client.messages.create(
            model=model,
            max_tokens=400,
            temperature=0.3,
            system=_SYNTH_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Excerpts:\n\n{context}\n\nQuestion: {query}"}],
        )
        return "".join(b.text for b in resp.content if b.type == "text").strip()
    except Exception:
        return None
