"""Visual constants and row styling helpers for the desktop interface."""

_FIXED_TREE_COLUMNS = {"selected", "action", "confidence"}
_TREE_STYLE = "Ballad.Treeview"
_PRIMARY_BUTTON_BG = "#238636"
_PRIMARY_BUTTON_ACTIVE_BG = "#2ea043"
_PRIMARY_BUTTON_DISABLED_FG = "#d3f4dc"
_ACTIVITY_SIDEBAR_WIDTH = 360
_ACTIVITY_COLLAPSED_WIDTH = 82
_SHIFT_MASK = 0x0001
_SHARED_ARTWORK_PREVIEW_LIMIT = 8
_SHARED_ARTWORK_NAMES = {
    "albumart.jpg",
    "albumartsmall.jpg",
    "cover.jpg",
    "cover.png",
    "folder.jpg",
    "folder.jpeg",
    "folder.png",
    "front.jpg",
    "front.png",
}
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
_CONFIDENCE_ROW_STYLES = {
    "low": ("#f8d7da", "#842029"),
    "review": ("#f8d7da", "#842029"),
    "error": ("#f8d7da", "#842029"),
    "medium": ("#fff3cd", "#664d03"),
    "warning": ("#fff3cd", "#664d03"),
}


def _confidence_row_tags(confidence: str) -> tuple[str, ...]:
    """Map a confidence level to its optional Treeview tag."""
    if confidence in _CONFIDENCE_ROW_STYLES:
        return (f"conf-{confidence}",)
    return ()


__all__ = [
    "_ACTIVITY_COLLAPSED_WIDTH",
    "_ACTIVITY_SIDEBAR_WIDTH",
    "_CONFIDENCE_ROW_STYLES",
    "_FIXED_TREE_COLUMNS",
    "_IMAGE_EXTENSIONS",
    "_PRIMARY_BUTTON_ACTIVE_BG",
    "_PRIMARY_BUTTON_BG",
    "_PRIMARY_BUTTON_DISABLED_FG",
    "_SHARED_ARTWORK_NAMES",
    "_SHARED_ARTWORK_PREVIEW_LIMIT",
    "_SHIFT_MASK",
    "_TREE_STYLE",
    "_confidence_row_tags",
]
