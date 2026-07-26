"""Graph data model shared by the builder, search, and API layers.

Node types:
  - video:   one per ingested YouTube video
  - section: one per blog section (a chunk of the video + its screenshot)
  - entity:  a recurring topic/keyword, linked from every section that
             mentions it -- this is what turns a flat list of videos into
             an actual *graph*, since the same entity can hub together
             sections from different videos.

Edge types: "contains" (video -> section), "next" (section -> section,
sequential), "mentions" (section -> entity).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

NodeType = Literal["video", "section", "entity"]
EdgeType = Literal["contains", "next", "mentions"]


@dataclass
class Node:
    id: str
    type: NodeType
    label: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Edge:
    source: str
    target: str
    type: EdgeType


@dataclass
class Graph:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "nodes": [
                {"id": n.id, "type": n.type, "label": n.label, **n.data}
                for n in self.nodes
            ],
            "edges": [
                {"source": e.source, "target": e.target, "type": e.type}
                for e in self.edges
            ],
        }
