"""Query the knowledge graph: TF-IDF retrieval over section text, with an
optional LLM-synthesized answer on top when ANTHROPIC_API_KEY is set, and a
free extractive-answer fallback (no API key needed) otherwise."""
from __future__ import annotations

import os
import re

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from kgwiki.models import Graph, Node


class SearchIndex:
    def __init__(self, graph: Graph):
        self.sections: list[Node] = [n for n in graph.nodes if n.type == "section"]
        texts = [f"{n.label}. {n.data.get('text', '')}" for n in self.sections]
        if texts:
            self.vectorizer = TfidfVectorizer(
                stop_words="english",
                ngram_range=(1, 2),
                min_df=1,
                max_df=0.6,
                sublinear_tf=True,
            )
            self.matrix = self.vectorizer.fit_transform(texts)
        else:
            self.vectorizer = None
            self.matrix = None

    def query(self, q: str, top_k: int = 6) -> list[dict]:
        if not self.sections or self.vectorizer is None:
            return []
        qvec = self.vectorizer.transform([q])
        sims = cosine_similarity(qvec, self.matrix)[0]
        ranked = sims.argsort()[::-1]
        results = []
        for i in ranked[: top_k * 3]:
            if sims[i] <= 0.02:
                continue
            n = self.sections[i]
            results.append(
                {
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
                    "score": round(float(sims[i]), 4),
                }
            )
            if len(results) >= top_k:
                break
        return results


_GREETING_RE = re.compile(r"^\s*(hi|hello|hey+|yo|sup|howdy|good\s?(morning|afternoon|evening))\b", re.IGNORECASE)
_HOW_ARE_YOU_RE = re.compile(r"how('?s| is| are) (you|u|it going)|what'?s up\??$", re.IGNORECASE)
_THANKS_RE = re.compile(r"^\s*(thanks|thank you|thx|ty)\b", re.IGNORECASE)
_BYE_RE = re.compile(r"^\s*(bye|goodbye|see (you|ya)|later|ok(ay)? bye)\b", re.IGNORECASE)
_WHOAMI_RE = re.compile(r"who are you|what are you|what can you do|what do you do|^\s*help\s*\??\s*$", re.IGNORECASE)


def chitchat_reply(query: str) -> str | None:
    """Handle small talk locally and for free -- no need to hit the search
    index (or an LLM) just to answer "hi, how are you?"."""
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
    """Free, no-API-key fallback: pull the sentence(s) most relevant to the
    query directly out of the top matching sections, verbatim.

    Keyword overlap alone ties constantly on short queries (e.g. a one-word
    "FDE" query matches every sentence that mentions FDE equally) so this
    also weights by how highly TF-IDF ranked the source section, and -- for
    "what is X" / "who is X" questions -- gives a boost to sentences that
    actually look like a definition ("X is...", "X (Y)...", "X refers to...").
    """
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
