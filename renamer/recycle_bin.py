"""Validated Windows Recycle Bin operations for reviewed duplicate files."""

from __future__ import annotations

import ctypes
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from .review_models import DuplicateFinding, path_key, sha256_file
from .runtime import atomic_write_json, ensure_app_dirs


class RecycleBinUnavailable(RuntimeError):
    """The current platform cannot provide the requested reversible action."""


@dataclass(frozen=True)
class RecycleResult:
    path: str
    status: str
    message: str = ""
    finding_id: str = ""


def send_to_recycle_bin(path: str) -> None:
    """Move one file to the Windows Recycle Bin without permanent deletion."""
    if os.name != "nt":
        raise RecycleBinUnavailable("The Windows Recycle Bin is unavailable here")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", ctypes.c_void_p),
            ("wFunc", ctypes.c_uint),
            ("pFrom", ctypes.c_wchar_p),
            ("pTo", ctypes.c_wchar_p),
            ("fFlags", ctypes.c_uint16),
            ("fAnyOperationsAborted", ctypes.c_int),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", ctypes.c_wchar_p),
        ]

    operation = SHFILEOPSTRUCTW()
    operation.wFunc = 3
    operation.pFrom = f"{str(Path(path).resolve())}\0\0"
    operation.fFlags = 0x40 | 0x10 | 0x400 | 0x4
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    if result:
        raise OSError(result, f"Recycle Bin move failed for {path}")
    if operation.fAnyOperationsAborted:
        raise OSError("Recycle Bin move was aborted")


def _validate_selection(
    finding: DuplicateFinding,
    selected_paths: list[str] | tuple[str, ...],
) -> tuple[str, ...]:
    if not selected_paths:
        raise ValueError("Select at least one duplicate file.")
    allowed = {path_key(path) for path in finding.paths}
    selected = tuple(selected_paths)
    selected_keys = {path_key(path) for path in selected}
    unknown = selected_keys - allowed
    if unknown:
        raise ValueError("A selected file is not part of this duplicate finding.")
    if selected_keys == allowed:
        raise ValueError("Keep at least one file in each duplicate group.")
    return selected


def _expected_hash(finding: DuplicateFinding, path: str) -> str | None:
    hashes = finding.evidence.get("hashes", {})
    return next(
        (value for candidate, value in hashes.items() if path_key(candidate) == path_key(path)),
        None,
    )


def _move_validated_file(finding: DuplicateFinding, path: str) -> RecycleResult:
    try:
        expected = _expected_hash(finding, path)
        if not expected:
            raise RuntimeError(f"No content hash was captured for {path}")
        if sha256_file(path) != expected:
            raise RuntimeError(f"File changed since review: {path}")
        send_to_recycle_bin(path)
        return RecycleResult(path, "succeeded", "Sent to Recycle Bin.", finding.id)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return RecycleResult(path, "failed", str(exc), finding.id)


def apply_selected_duplicates(
    finding: DuplicateFinding,
    selected_paths: list[str] | tuple[str, ...],
) -> list[RecycleResult]:
    """Move explicitly selected, unchanged losers to the Windows Recycle Bin."""
    selected = _validate_selection(finding, selected_paths)
    results = [_move_validated_file(finding, path) for path in selected]
    state = ensure_app_dirs()
    atomic_write_json(
        state["logs"] / f"recycle-{finding.id}.json",
        {
            "created_at": datetime.now(UTC).isoformat(),
            "finding_id": finding.id,
            "paths": [asdict(result) for result in results],
            "restore_note": "Restore is managed by Windows Recycle Bin.",
        },
    )
    return results


__all__ = [
    "RecycleBinUnavailable",
    "RecycleResult",
    "apply_selected_duplicates",
    "send_to_recycle_bin",
]
