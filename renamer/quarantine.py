"""Durable match quarantine storage and checks."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .review_models import canonical_path, path_key
from .runtime import atomic_write_json, ensure_app_dirs


def _quarantine_file_path() -> Path:
    return ensure_app_dirs()["root"] / "Quarantine" / "quarantine.json"


def load_quarantine() -> list[dict[str, Any]]:
    path = _quarantine_file_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return list(data.get("quarantined_files", []))
        if isinstance(data, list):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return []


def save_quarantine(items: list[dict[str, Any]]) -> None:
    path = _quarantine_file_path()
    atomic_write_json(path, {"quarantined_files": items})


def file_id_for_path(path: str) -> str | None:
    try:
        stat = os.stat(path)
        return f"{getattr(stat, 'st_dev', 0)}:{getattr(stat, 'st_ino', 0)}"
    except OSError:
        return None


def is_quarantined(path: str, file_id: str | None = None) -> bool:
    items = load_quarantine()
    if not items:
        return False
    target_key = path_key(path)
    target_fid = file_id or file_id_for_path(path)
    for item in items:
        item_path_key = path_key(str(item.get("path") or ""))
        if item_path_key and item_path_key == target_key:
            return True
        item_fid = str(item.get("file_id") or "")
        if target_fid and item_fid and target_fid == item_fid:
            return True
    return False


def quarantine_file(
    path: str,
    file_id: str | None = None,
    artist: str = "",
    title: str = "",
    reason: str = "Match ignored by user",
) -> dict[str, Any]:
    items = load_quarantine()
    target_path = canonical_path(path)
    target_key = path_key(path)
    target_fid = file_id or file_id_for_path(path)

    for item in items:
        if path_key(str(item.get("path") or "")) == target_key:
            return item
        if target_fid and str(item.get("file_id") or "") == target_fid:
            return item

    record = {
        "path": target_path,
        "path_key": target_key,
        "file_id": target_fid or "",
        "artist": artist,
        "title": title,
        "reason": reason,
        "created_at": datetime.now(UTC).isoformat(),
    }
    items.append(record)
    save_quarantine(items)
    return record


def unquarantine_files(path_keys: set[str] | list[str]) -> int:
    keys = {str(k).casefold() for k in path_keys}
    items = load_quarantine()
    kept = []
    removed = 0
    for item in items:
        pk = path_key(str(item.get("path") or ""))
        if pk in keys or str(item.get("path_key") or "").casefold() in keys:
            removed += 1
        else:
            kept.append(item)
    if removed > 0:
        save_quarantine(kept)
    return removed


__all__ = [
    "is_quarantined",
    "load_quarantine",
    "quarantine_file",
    "save_quarantine",
    "unquarantine_files",
]
