"""Folder registry: named groupings of videos + documents into one audit
project (e.g. one healthcare process end to end), so everything relevant --
walkthrough recordings, intake forms, architecture checklists -- can be
queried and summarized together instead of as a flat, undifferentiated pile.
"""
from __future__ import annotations

import json
import os
import time
import uuid


def _registry_path(out_root: str) -> str:
    return os.path.join(out_root, "_folders.json")


def _load_registry(out_root: str) -> dict:
    path = _registry_path(out_root)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_registry(out_root: str, registry: dict) -> None:
    os.makedirs(out_root, exist_ok=True)
    with open(_registry_path(out_root), "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2)


def list_folders(out_root: str) -> list[dict]:
    return sorted(_load_registry(out_root).values(), key=lambda f: f.get("created_at", 0), reverse=True)


def get_folder(out_root: str, folder_id: str) -> dict | None:
    return _load_registry(out_root).get(folder_id)


def create_folder(out_root: str, name: str) -> dict:
    registry = _load_registry(out_root)
    folder_id = uuid.uuid4().hex[:12]
    folder = {"id": folder_id, "name": (name or "").strip() or "Untitled folder", "created_at": time.time()}
    registry[folder_id] = folder
    _save_registry(out_root, registry)
    return folder


def delete_folder(out_root: str, folder_id: str) -> bool:
    registry = _load_registry(out_root)
    if folder_id not in registry:
        return False
    del registry[folder_id]
    _save_registry(out_root, registry)
    return True
