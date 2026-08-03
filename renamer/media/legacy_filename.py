"""Compatibility parser for filename-derived tag writes."""

from __future__ import annotations

import re

from ..regular_parser import (
    format_title,
    normalize_title_text,
    parse_regular_stem,
    strip_audio_extensions,
)

_OCREMIX_RE = re.compile(r"\[OC\s*Re[Mm]ix\]", re.IGNORECASE)
_OCREMIX_LABEL_RE = re.compile(r"\bOC\s*Re[Mm]ix\b", re.IGNORECASE)
_VERSION_LABEL_RE = re.compile(
    r"\b(?:acoustic|album|clean|club|demo|edit|extended|instrumental|"
    r"karaoke|live|mix|mono|original|radio|remaster(?:ed)?|reprise|"
    r"single|stereo|version)\b",
    re.IGNORECASE,
)


def _split_final_parenthetical(text: str) -> tuple[str, str] | None:
    text = text.rstrip()
    if not text.endswith(")"):
        return None
    depth = 0
    for index in range(len(text) - 1, -1, -1):
        character = text[index]
        if character == ")":
            depth += 1
        elif character == "(":
            depth -= 1
            if depth == 0:
                prefix = text[:index].strip()
                content = text[index + 1 : -1].strip()
                return (prefix, content) if prefix and content else None
    return None


def _clean_names(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        name = normalize_title_text(value)
        key = name.casefold()
        if name and key not in seen:
            cleaned.append(name)
            seen.add(key)
    return cleaned


def _is_version_label(text: str) -> bool:
    return bool(
        _OCREMIX_LABEL_RE.search(text or "")
        or _VERSION_LABEL_RE.search(text or "")
    )


def parse_stem(stem: str) -> dict | None:
    """Parse a legacy supported filename into canonical tag components."""
    stem = strip_audio_extensions(stem)
    if _OCREMIX_RE.search(stem):
        clean = normalize_title_text(_OCREMIX_RE.sub("", stem).strip())
        if " - " not in clean:
            return None
        game, rest = clean.split(" - ", 1)
        final = _split_final_parenthetical(rest.strip())
        if final and not _is_version_label(final[1]):
            title, remixer_text = final
            remixers = _clean_names(remixer_text.split(","))
        else:
            title, remixers = rest, []
        return {
            "is_ocremix": True,
            "game": normalize_title_text(game),
            "title": normalize_title_text(title),
            "remixers": remixers,
            "artist": normalize_title_text(game),
        }

    regular = parse_regular_stem(stem)
    if regular is None:
        return None
    full_title = format_title(regular)
    if regular.artist in {"", "Unknown Artist"}:
        return None
    if full_title in {"", "Unknown Title"}:
        return None
    return {
        "is_ocremix": False,
        "artist": regular.artist,
        "full_title": full_title,
    }


def parsed_tag_values(parsed: dict) -> dict[str, str]:
    if not parsed["is_ocremix"]:
        return {
            "artist": parsed["artist"],
            "title": parsed["full_title"],
        }
    return {
        "artist": parsed["game"],
        "title": parsed["title"],
        "album": parsed["game"],
        "album_artist": "OverClocked ReMix",
        "grouping": parsed["game"],
        "subtitle": ", ".join(parsed["remixers"]),
    }


__all__ = ["parse_stem", "parsed_tag_values"]
