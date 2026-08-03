"""Verified temporary-file restoration for metadata snapshots."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from ..media import read_media, write_tags_to_file
from ..media.schema import metadata_matches
from .state import ApplyBlocked


def _restore_paths(
    path: str,
    backup_path: str,
    temporary_path: str,
) -> tuple[Path, Path]:
    backup = Path(backup_path)
    temporary = Path(temporary_path)
    if not backup.is_file():
        raise FileNotFoundError(backup)
    if not Path(path).is_file():
        raise FileNotFoundError(path)
    if temporary.exists():
        raise ApplyBlocked(f"Restore temporary path already exists: {temporary}")
    return backup, temporary


def _verify_restored_artwork(artwork, media) -> None:
    if artwork is None:
        if media.artwork is not None:
            raise ApplyBlocked("Restored artwork removal did not verify.")
    elif media.artwork is None or media.artwork.sha256 != artwork.get("sha256"):
        raise ApplyBlocked("Restored artwork did not verify.")


def _restore_temporary_file(
    path: str,
    temporary: Path,
    before,
    artwork,
    *,
    writer,
    media_reader,
) -> None:
    shutil.copy2(path, temporary)
    result = writer(
        str(temporary),
        before,
        artwork,
        remove_artwork=artwork is None,
    )
    if result.get("status") not in {"updated", "already_ok"}:
        raise ApplyBlocked(result.get("reason", "Could not restore metadata snapshot"))
    media = media_reader(str(temporary))
    if not metadata_matches(before, media.tags):
        raise ApplyBlocked("Restored canonical tags did not verify.")
    _verify_restored_artwork(artwork, media)


def restore_metadata_snapshot(
    path: str,
    backup_path: str,
    temporary_path: str,
    *,
    writer=write_tags_to_file,
    media_reader=read_media,
) -> None:
    """Restore tags/artwork atomically, then verify canonical state."""
    backup, temporary = _restore_paths(path, backup_path, temporary_path)
    snapshot = json.loads(backup.read_text(encoding="utf-8"))
    before = snapshot.get("before", {})
    artwork = snapshot.get("artwork_before")
    try:
        _restore_temporary_file(
            path,
            temporary,
            before,
            artwork,
            writer=writer,
            media_reader=media_reader,
        )
        os.replace(temporary, path)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise


__all__ = ["restore_metadata_snapshot"]
