"""Typing contracts shared by the composed GUI mixins."""

from __future__ import annotations

from typing import Any, Protocol


class GuiAppProtocol(Protocol):
    """Fallback contract for attributes supplied by sibling GUI mixins."""

    def __getattr__(self, name: str) -> Any: ...


__all__ = ["GuiAppProtocol"]
