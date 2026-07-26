"""Lightweight, free (no-LLM) keyword/entity extraction.

Two complementary signals, combined:
  1. TF-IDF over all section texts in the corpus -- surfaces terms that are
     distinctive to a given section rather than just frequent overall
     (e.g. "forward deployed engineer" scores high, "the" / "really" don't).
  2. Regex-detected capitalized phrases -- catches proper nouns (people,
     products, companies) that may only be mentioned once or twice and so
     would be underweighted by TF-IDF alone.

Entities are deduplicated across the whole corpus by a lowercase slug, which
is what lets the same entity hub together sections from different videos
into one graph node.
"""
from __future__ import annotations

import re
from collections import Counter

from sklearn.feature_extraction.text import TfidfVectorizer

_STOPWORDS_EXTRA = {
    "music", "laughter", "snorts", "yeah", "okay", "cool", "totally", "right",
    "um", "uh", "gonna", "wanna", "kinda", "sort", "thing", "things", "stuff",
    "really", "like", "just", "know", "think", "going", "got", "get", "lot",
    "step", "end", "hour", "different", "happens", "early", "week", "talk",
    "day", "days", "way", "ways", "part", "parts", "point", "points", "bit",
    "little", "big", "good", "bad", "great", "sort", "kind", "case", "cases",
}

_CAP_PHRASE_RE = re.compile(
    r"\b[A-Z][a-zA-Z0-9]*(?:\s+[A-Z][a-zA-Z0-9]*){0,2}\b"
)
_COMMON_SENTENCE_STARTS = {
    "The", "This", "That", "These", "Those", "It", "So", "But", "And", "If",
    "When", "You", "We", "I", "They", "In", "On", "At", "For", "Um", "Now",
    "There", "Once", "Every", "Then", "Here", "Well", "Yeah", "Okay", "Cool",
    "Absolutely", "Totally", "Sweet", "Right", "What", "How", "Why", "Who",
    "Some", "Many", "Most", "All", "Any", "No", "Not", "Yes", "Um", "Uh",
    "Let", "Let's", "One", "Two", "Three", "Four", "Five", "First", "Second",
    "Third", "Finally", "Also", "Even", "Just", "Like", "Because", "Since",
    "As", "Or", "Yet", "Still", "Again", "Actually", "Basically", "Um", "My",
}


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug


def _capitalized_phrases(text: str) -> list[str]:
    candidates = _CAP_PHRASE_RE.findall(text)
    out = []
    for c in candidates:
        first_word = c.split(" ")[0]
        if first_word in _COMMON_SENTENCE_STARTS and " " not in c:
            continue
        if len(c) < 3 or c.lower() in _STOPWORDS_EXTRA:
            continue
        out.append(c)
    return out


def extract_entities_per_section(
    section_texts: list[str], top_k: int = 6
) -> list[list[str]]:
    """Given the raw text of every section in the corpus (same order they'll
    be used as graph nodes), return the top-k entity strings per section."""
    if not section_texts:
        return []

    vectorizer = TfidfVectorizer(
        ngram_range=(1, 3),
        stop_words="english",
        max_df=0.6,
        min_df=1,
    )
    try:
        tfidf = vectorizer.fit_transform(section_texts)
        terms = vectorizer.get_feature_names_out()
    except ValueError:
        tfidf = None
        terms = []

    results: list[list[str]] = []
    for i, text in enumerate(section_texts):
        scored: Counter[str] = Counter()

        if tfidf is not None:
            row = tfidf[i].tocoo()
            for col, score in zip(row.col, row.data):
                term = terms[col]
                words = term.split(" ")
                if any(w in _STOPWORDS_EXTRA or len(w) < 3 for w in words):
                    continue
                scored[term] += float(score) * 10

        for phrase in _capitalized_phrases(text):
            word_count = phrase.count(" ") + 1
            weight = 1.0 if word_count == 1 else 2.5
            if word_count == 1 and len(phrase) < 4:
                continue
            scored[phrase.lower()] += weight

        # Prefer the nicest-cased version of each slug for display.
        best_label: dict[str, str] = {}
        for phrase in _capitalized_phrases(text):
            slug = slugify(phrase)
            if slug not in best_label or len(phrase) > len(best_label[slug]):
                best_label[slug] = phrase

        ranked = [w for w, _ in scored.most_common(top_k * 3)]
        picked: list[str] = []
        seen_slugs: set[str] = set()
        for term in ranked:
            slug = slugify(term)
            if not slug or slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            label = best_label.get(slug, term)
            picked.append(label)
            if len(picked) >= top_k:
                break
        results.append(picked)

    return results
