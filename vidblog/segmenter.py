"""Split a word timeline into readable, time-bucketed sections."""
from __future__ import annotations

from dataclasses import dataclass

from vidblog.transcript import WordEvent


@dataclass
class Section:
    index: int
    start: float
    end: float
    text: str
    word_count: int


def make_sections(
    events: list[WordEvent],
    duration: float,
    target_section_seconds: float = 150.0,
    min_words: int = 40,
) -> list[Section]:
    if not events:
        return []

    duration = max(duration, events[-1].time + 1.0)
    n_sections = max(1, round(duration / target_section_seconds))
    bucket_len = duration / n_sections

    buckets: list[list[WordEvent]] = [[] for _ in range(n_sections)]
    for e in events:
        idx = min(int(e.time // bucket_len), n_sections - 1)
        buckets[idx].append(e)

    # Merge undersized buckets into their neighbor so tiny/silent stretches
    # don't produce a near-empty section.
    merged: list[list[WordEvent]] = []
    carry: list[WordEvent] = []
    for bucket in buckets:
        carry.extend(bucket)
        if len(carry) >= min_words:
            merged.append(carry)
            carry = []
    if carry:
        if merged:
            merged[-1].extend(carry)
        else:
            merged.append(carry)

    sections: list[Section] = []
    for i, word_events in enumerate(merged):
        if not word_events:
            continue
        text = " ".join(e.word for e in word_events)
        sections.append(
            Section(
                index=i,
                start=word_events[0].time,
                end=word_events[-1].time,
                text=text,
                word_count=len(word_events),
            )
        )
    return sections
