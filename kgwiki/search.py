"""Query the knowledge graph: TF-IDF retrieval over section text, with an
optional LLM-synthesized answer on top when ANTHROPIC_API_KEY is set."""
from __future__ import annotations

import os

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
