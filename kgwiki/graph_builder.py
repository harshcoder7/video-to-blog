"""Scan vidblog's output/ folder and build a knowledge graph out of it.

Reads, per ingested video folder (output/<video_id>/):
  - sections.json        (required)   raw per-section transcript + metadata
  - blog_override.json   (optional)   hand/LLM-written heading + paragraphs
  - screenshots/section_NN.jpg (optional) the picked screenshot per section
  - video.webp / video.jpg (optional) thumbnail

and produces a Graph of video / section / entity nodes (see models.py).
"""
from __future__ import annotations

import json
import os
import re

from kgwiki import entities as entity_extractor
from kgwiki.models import Edge, Graph, Node

_TS_RE = re.compile(r"^(?:(\d+):)?(\d+):(\d+)$")


def _parse_timestamp(ts: str) -> int:
    m = _TS_RE.match(ts.strip())
    if not m:
        return 0
    h, mi, s = m.groups()
    h = int(h) if h else 0
    return h * 3600 + int(mi) * 60 + int(s)


def _find_media(work_dir: str, *names: str) -> str | None:
    for name in names:
        p = os.path.join(work_dir, name)
        if os.path.exists(p):
            return p
    return None


def _load_video_folder(work_dir: str, video_id: str) -> dict | None:
    sections_path = os.path.join(work_dir, "sections.json")
    if not os.path.exists(sections_path):
        return None
    with open(sections_path, "r", encoding="utf-8") as f:
        sections_data = json.load(f)

    override = None
    override_path = os.path.join(work_dir, "blog_override.json")
    if os.path.exists(override_path):
        with open(override_path, "r", encoding="utf-8") as f:
            override = json.load(f)
    override_sections = {}
    if override:
        for s in override.get("sections", []):
            override_sections[s["index"]] = s

    thumb = _find_media(work_dir, "video.webp", "video.jpg", "video.png")

    intro = " ".join((override or {}).get("intro", []))
    conclusion = " ".join((override or {}).get("conclusion", []))
    subtitle = (override or {}).get("subtitle", "")
    overview_text = " ".join(p for p in [subtitle, intro, conclusion] if p)

    return {
        "video_id": video_id,
        "title": (override or {}).get("title") or sections_data.get("video_title", "Untitled"),
        "channel": sections_data.get("channel", "Unknown"),
        "url": sections_data.get("url", ""),
        "thumbnail": thumb,
        "raw_sections": sections_data.get("sections", []),
        "override_sections": override_sections,
        "subtitle": subtitle,
        "overview_text": overview_text,
    }


def _media_url(work_dir_relative: str, abs_path: str | None, out_root: str) -> str | None:
    if not abs_path:
        return None
    rel = os.path.relpath(abs_path, out_root).replace(os.sep, "/")
    return f"/media/{rel}"


def build_graph(out_root: str = "output", top_k_entities: int = 6) -> Graph:
    graph = Graph()
    if not os.path.isdir(out_root):
        return graph

    videos = []
    for entry in sorted(os.listdir(out_root)):
        work_dir = os.path.join(out_root, entry)
        if not os.path.isdir(work_dir):
            continue
        info = _load_video_folder(work_dir, entry)
        if info:
            videos.append((work_dir, info))

    # Flatten all sections (across all videos) into one ordered list so the
    # entity extractor's TF-IDF corpus spans everything -- this is what lets
    # an entity mentioned in two different videos become a shared hub node.
    flat_sections: list[dict] = []
    for work_dir, info in videos:
        for raw_sec in info["raw_sections"]:
            idx = raw_sec["index"]
            override_sec = info["override_sections"].get(idx)
            heading = (override_sec or {}).get("heading") or f"Part {idx + 1}"
            paragraphs = (override_sec or {}).get("paragraphs")
            text = " ".join(paragraphs) if paragraphs else raw_sec.get("raw_transcript", "")
            screenshot_path = os.path.join(work_dir, "screenshots", f"section_{idx:02d}.jpg")
            screenshot = screenshot_path if os.path.exists(screenshot_path) else None
            flat_sections.append(
                {
                    "work_dir": work_dir,
                    "video_id": info["video_id"],
                    "video_title": info["title"],
                    "video_url": info["url"],
                    "index": idx,
                    "heading": heading,
                    "text": text,
                    "start": raw_sec.get("start", "0:00"),
                    "end": raw_sec.get("end", "0:00"),
                    "screenshot": screenshot,
                }
            )

    entity_lists = entity_extractor.extract_entities_per_section(
        [s["text"] for s in flat_sections], top_k=top_k_entities
    )

    entity_nodes: dict[str, Node] = {}

    for work_dir, info in videos:
        video_node_id = f"video:{info['video_id']}"
        graph.nodes.append(
            Node(
                id=video_node_id,
                type="video",
                label=info["title"],
                data={
                    "channel": info["channel"],
                    "url": info["url"],
                    "thumbnail": _media_url(work_dir, info["thumbnail"], out_root),
                    "subtitle": info["subtitle"],
                    "overview_text": info["overview_text"],
                    "section_count": len(info["raw_sections"]),
                },
            )
        )

    prev_section_id_by_video: dict[str, str] = {}
    for flat_idx, sec in enumerate(flat_sections):
        section_node_id = f"section:{sec['video_id']}:{sec['index']}"
        excerpt = sec["text"].strip()
        if len(excerpt) > 220:
            excerpt = excerpt[:220].rsplit(" ", 1)[0] + "..."
        start_seconds = _parse_timestamp(sec["start"])
        timestamp_url = (
            f"{sec['video_url']}&t={start_seconds}s" if "watch?v=" in sec["video_url"] else sec["video_url"]
        )

        graph.nodes.append(
            Node(
                id=section_node_id,
                type="section",
                label=sec["heading"],
                data={
                    "video_id": sec["video_id"],
                    "video_title": sec["video_title"],
                    "text": sec["text"],
                    "excerpt": excerpt,
                    "screenshot": _media_url(sec["work_dir"], sec["screenshot"], out_root),
                    "start": sec["start"],
                    "end": sec["end"],
                    "start_seconds": start_seconds,
                    "timestamp_url": timestamp_url,
                },
            )
        )
        graph.edges.append(Edge(source=f"video:{sec['video_id']}", target=section_node_id, type="contains"))

        prev_id = prev_section_id_by_video.get(sec["video_id"])
        if prev_id:
            graph.edges.append(Edge(source=prev_id, target=section_node_id, type="next"))
        prev_section_id_by_video[sec["video_id"]] = section_node_id

        for ent in entity_lists[flat_idx] if flat_idx < len(entity_lists) else []:
            slug = entity_extractor.slugify(ent)
            if not slug:
                continue
            entity_node_id = f"entity:{slug}"
            if entity_node_id not in entity_nodes:
                entity_nodes[entity_node_id] = Node(
                    id=entity_node_id, type="entity", label=ent, data={"mentions": 0}
                )
            node = entity_nodes[entity_node_id]
            node.data["mentions"] += 1
            if len(ent) > len(node.label):
                node.label = ent
            graph.edges.append(Edge(source=section_node_id, target=entity_node_id, type="mentions"))

    # Drop entities only mentioned once when the corpus is already large,
    # to keep the graph from becoming an unreadable hairball as more videos
    # get ingested. With a small corpus, keep everything -- it's the point.
    entity_list = list(entity_nodes.values())
    if len(entity_list) > 60:
        entity_list.sort(key=lambda n: n.data["mentions"], reverse=True)
        keep_ids = {n.id for n in entity_list[:60]}
        graph.edges = [e for e in graph.edges if e.type != "mentions" or e.target in keep_ids]
        entity_list = [n for n in entity_list if n.id in keep_ids]

    graph.nodes.extend(entity_list)
    return graph
