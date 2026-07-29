"""Generate a structured audit brief for a folder, merging every video and
document it contains into one accurate, well-organized summary -- built for
feeding into agent-building work (what are the inputs, what systems are
touched, what are the steps), with a healthcare process-automation lean
since that's the primary domain this is used for.

Map-reduce, not a single big prompt: a folder can hold a full 50+ section
walkthrough plus several documents, which blows past a small local model's
context window long before you'd want it to. Instead, sections are grouped
into small chunks, each chunk is mapped (concurrently) to a short list of
extracted facts, and a final reduce pass merges every chunk's facts into the
requested structure. This scales to a folder of any size without silently
truncating content or without needing a bigger model.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import ollama_client

_MAP_GROUP_SIZE = 8

_MAP_SYSTEM_PROMPT = """You are helping build an audit trail for a business process that a healthcare \
organization wants to automate with AI agents. You'll see several excerpts from either a recorded \
process walkthrough (transcript) or a process document (intake form, checklist, SOP). Extract ONLY \
facts explicitly present in the text below, as short bullet points, under exactly these three \
headers:

INPUTS: (data, forms, requests, files, or triggers that start or feed the process)
SYSTEMS: (any named software, platform, or tool actually mentioned)
STEPS: (concrete actions taken, in the order they appear)

Skip a header entirely if nothing relevant appears -- do not guess or pad. Be terse: short phrases, \
not full sentences. Never invent a system name, form name, or step that isn't explicitly there."""

_REDUCE_SYSTEM_PROMPT = """You are producing the final structured audit brief for a healthcare \
process-automation project, merging notes already extracted from every video and document in this \
folder. Combine and deduplicate them into one clean, well-organized brief a healthcare operations \
or automation team can act on directly, with exactly these three sections, each as a clean bullet \
list, in this order:

## Initial Inputs
## Systems Used
## Process Steps

"Process Steps" should read as a single logical end-to-end sequence (merge duplicate/overlapping \
steps mentioned in multiple sources into one, and order them logically even if sources described \
them out of order). Only use facts present in the notes below -- never invent a system, input, or \
step. If a section would be empty, write "Not mentioned in the provided sources." for it rather \
than omitting it silently. Be precise and concise; this is a working document, not prose."""


def _group(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _map_chunk(sources: list[dict]) -> str | None:
    parts = []
    for s in sources:
        label = "Document" if s.get("kind") == "document" else "Video"
        parts.append(f"[{label}: {s['video_title']} — {s['heading']}]\n{s['text']}")
    context = "\n\n".join(parts)
    # Generous timeout: this can be the very first chat call since the container
    # started (or after Ollama's idle-unload), and loading qwen2.5:3b alone can
    # take close to a minute on a small GPU before generation even begins.
    return ollama_client.chat(_MAP_SYSTEM_PROMPT, context, temperature=0.1, max_tokens=350, timeout=150.0)


def generate_folder_brief(folder_name: str, sources: list[dict]) -> dict:
    """sources: the folder's sections/chunks (from SearchIndex.all_sections_for_folder),
    each a dict with at least video_title/heading/text/kind.
    Returns {"brief": str, "used_llm": bool, "source_count": int}."""
    if not sources:
        return {
            "brief": "This folder is empty -- add a video or document to generate a brief.",
            "used_llm": False,
            "source_count": 0,
        }

    if not ollama_client.is_available():
        # Free fallback: no synthesis, just a labeled dump of what's there so
        # the feature still returns something useful with zero setup.
        listing = "\n\n".join(
            f"[{'Document' if s.get('kind') == 'document' else 'Video'}: {s['video_title']} — {s['heading']}]\n{s['text'][:400]}"
            for s in sources
        )
        return {
            "brief": (
                "## Initial Inputs\nNot available -- no local model is running, so this is a raw "
                f"excerpt dump instead of a synthesized brief.\n\n## Systems Used\nSee excerpts below.\n\n"
                f"## Process Steps\nSee excerpts below.\n\n---\n\n{listing}"
            ),
            "used_llm": False,
            "source_count": len(sources),
        }

    groups = _group(sources, _MAP_GROUP_SIZE)
    with ThreadPoolExecutor(max_workers=4) as pool:
        chunk_notes = list(pool.map(_map_chunk, groups))
    chunk_notes = [n for n in chunk_notes if n]

    if not chunk_notes:
        return {
            "brief": "Couldn't extract anything usable from this folder's content.",
            "used_llm": True,
            "source_count": len(sources),
        }

    merged_notes = "\n\n---\n\n".join(f"Notes from source group {i + 1}:\n{n}" for i, n in enumerate(chunk_notes))
    brief = ollama_client.chat(
        _REDUCE_SYSTEM_PROMPT,
        f'Folder: "{folder_name}"\n\n{merged_notes}',
        temperature=0.2,
        max_tokens=900,
        timeout=180.0,
    )
    if not brief:
        brief = merged_notes  # degrade gracefully to the raw extracted notes rather than nothing

    return {"brief": brief, "used_llm": True, "source_count": len(sources)}
