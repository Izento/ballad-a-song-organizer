"""Persistent cache for online identification and enrichment evidence."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .runtime import app_paths


_SCHEMA_VERSION = 1
_LOCK = threading.RLock()


class EnrichmentCache:
    """A small SQLite cache with optional expiry and content-addressed assets."""

    def __init__(self, path: Path | None = None) -> None:
        cache_root = app_paths()["cache"]
        self.path = path or cache_root / "enrichment.sqlite3"
        self.asset_dir = self.path.parent / "Artwork"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.asset_dir.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with _LOCK, self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS cache_entries (
                    namespace TEXT NOT NULL,
                    cache_key TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    expires_at REAL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (namespace, cache_key)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS cache_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO cache_meta (key, value)
                VALUES ('schema_version', ?)
                """,
                (str(_SCHEMA_VERSION),),
            )

    def get(self, namespace: str, cache_key: str) -> Any | None:
        """Return a cached JSON-safe value, deleting expired entries."""
        now = time.time()
        with _LOCK, self._connect() as connection:
            row = connection.execute(
                """
                SELECT value_json, expires_at
                FROM cache_entries
                WHERE namespace = ? AND cache_key = ?
                """,
                (namespace, cache_key),
            ).fetchone()
            if row is None:
                return None
            value_json, expires_at = row
            if expires_at is not None and expires_at <= now:
                connection.execute(
                    "DELETE FROM cache_entries WHERE namespace = ? AND cache_key = ?",
                    (namespace, cache_key),
                )
                return None
        return json.loads(value_json)

    def set(
        self,
        namespace: str,
        cache_key: str,
        value: Any,
        *,
        ttl_seconds: float | None = None,
    ) -> None:
        """Persist a JSON-safe value with an optional TTL."""
        expires_at = None if ttl_seconds is None else time.time() + ttl_seconds
        value_json = json.dumps(value, ensure_ascii=False, sort_keys=True)
        with _LOCK, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO cache_entries (
                    namespace, cache_key, value_json, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (namespace, cache_key, value_json, expires_at, time.time()),
            )

    def delete(self, namespace: str, cache_key: str) -> None:
        with _LOCK, self._connect() as connection:
            connection.execute(
                "DELETE FROM cache_entries WHERE namespace = ? AND cache_key = ?",
                (namespace, cache_key),
            )

    def put_asset(self, data: bytes, suffix: str = ".img") -> dict[str, str | int]:
        """Store binary data by digest and return its JSON-safe descriptor."""
        digest = hashlib.sha256(data).hexdigest()
        suffix = suffix if suffix.startswith(".") else f".{suffix}"
        path = self.asset_dir / f"{digest}{suffix.casefold()}"
        if not path.exists():
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_bytes(data)
            os.replace(temporary, path)
        return {
            "sha256": digest,
            "path": str(path),
            "size": len(data),
        }


_default_cache: EnrichmentCache | None = None


def enrichment_cache() -> EnrichmentCache:
    """Return the process-wide enrichment cache."""
    global _default_cache  # pylint: disable=global-statement
    with _LOCK:
        if _default_cache is None:
            _default_cache = EnrichmentCache()
        return _default_cache


__all__ = ["EnrichmentCache", "enrichment_cache"]
