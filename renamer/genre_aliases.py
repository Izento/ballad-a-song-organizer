"""Explicit genre alias normalization.

Unlike a denylist (guessing which genres are "junk"), this is a small,
user-requested consolidation: fold a narrower/older genre name into the
broader one the user actually wants on their files. It's applied uniformly
regardless of where the genre value came from (freshly enriched from
MusicBrainz, or already sitting in the file's existing tags).
"""

from __future__ import annotations

from .regular_parser import normalize_text

GENRE_ALIASES: dict[str, str] = {
    "rap": "Hip-Hop",
    "electronic": "Techno",
    "edm": "Techno",
}


def normalize_genre(value: str) -> str:
    """Map a single genre value through the alias table, if it has one."""
    return GENRE_ALIASES.get(normalize_text(value), value)


def normalize_genre_list(values: list[str]) -> list[str]:
    """Apply `normalize_genre` to every value, preserving order and merging
    duplicates that aliasing creates (e.g. an existing "Hip-Hop" plus a
    "Rap" that now also maps to "Hip-Hop")."""
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        mapped = normalize_genre(value)
        key = normalize_text(mapped)
        if key and key not in seen:
            seen.add(key)
            result.append(mapped)
    return result


__all__ = ["GENRE_ALIASES", "normalize_genre", "normalize_genre_list"]
