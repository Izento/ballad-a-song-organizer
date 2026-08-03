"""Version-label parsing that keeps recording identity separate from noise."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher


_BLOCK_RE = re.compile(r"[\[(]\s*([^\])]+?)\s*[\])]")
_TRAILING_VIP_RE = re.compile(
    r"(?:\s*[-–—]\s*|\s+)(?P<label>v\.?\s*i\.?\s*p\.?"
    r"(?:\s+(?:mix|edit))?)\s*$",
    re.IGNORECASE,
)
_A_CAPPELLA_RE = re.compile(
    r"\ba\s*cap+pella\b|\bacap+pella\b",
    re.IGNORECASE,
)
_VIP_RE = re.compile(
    r"^v\.?\s*i\.?\s*p\.?(?:\s+(?P<kind>mix|edit))?$",
    re.IGNORECASE,
)
_QUALITY_RE = re.compile(
    r"^(?:hq|hd|official\s+(?:audio|video)|"
    r"\d{2,4}\s*(?:kbps|k)|(?:web|vinyl|cd)\s*rip)$",
    re.IGNORECASE,
)
_RELEASE_CONTEXT_RE = re.compile(
    r"^(?:bonus\s+track|deluxe\s+edition|expanded\s+edition|"
    r"anniversary\s+edition)$",
    re.IGNORECASE,
)
_PROMO_RE = re.compile(
    r"^(?:free\s+download|download\s+for\s+free|"
    r"(?:https?://)?(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)+"
    r"(?:/[^\s]*)?)$",
    re.IGNORECASE,
)
_DERIVATIVE_KINDS = frozenset({"instrumental", "a_cappella", "vip", "remix"})
_INSTRUMENTAL = "instrumental"
_INSTRUMENTAL_ABBREVIATIONS = frozenset(
    {"inst", "instr", "instrum", "instrume", "instrumen", "instrument"}
)
_TRAILING_BLOCK_RE = re.compile(r"\s*\(([^()]*)\)\s*$")


@dataclass(frozen=True)
class Qualifier:
    """A structural filename annotation with a conservative classification."""

    value: str
    kind: str
    source: str = "parenthetical"

    @property
    def is_recording_identity(self) -> bool:
        return self.kind in {
            "instrumental",
            "a_cappella",
            "vip",
            "remix",
            "version",
        }

    @property
    def is_derivative(self) -> bool:
        return self.kind in _DERIVATIVE_KINDS


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" .-_")


def _is_instrumental_token(value: str) -> bool:
    word = value.casefold()
    if word in _INSTRUMENTAL_ABBREVIATIONS:
        return True
    return (
        9 <= len(word) <= 13
        and SequenceMatcher(None, word, _INSTRUMENTAL).ratio() >= 0.9
    )


def normalize_instrumental_spelling(value: str) -> str:
    """Correct clear misspellings of the semantic Instrumental label."""
    return re.sub(
        r"[A-Za-z]+",
        lambda match: (
            "Instrumental"
            if _is_instrumental_token(match.group())
            else match.group()
        ),
        value or "",
    )


def has_instrumental_qualifier(value: str) -> bool:
    return any(
        _is_instrumental_token(word)
        for word in re.findall(r"[A-Za-z]+", value or "")
    )


def _canonical_label(value: str) -> tuple[str, str]:
    normalized = _clean(normalize_instrumental_spelling(value))
    folded = normalized.casefold()
    if _is_instrumental_token(folded):
        return "Instrumental", "instrumental"
    if _A_CAPPELLA_RE.search(normalized):
        return "A Cappella", "a_cappella"
    vip = _VIP_RE.fullmatch(normalized)
    if vip:
        kind = (vip.group("kind") or "").casefold()
        return (
            "VIP" if not kind else f"VIP {kind.title()}",
            "vip",
        )
    if _QUALITY_RE.fullmatch(normalized):
        return normalized.upper(), "quality"
    if _RELEASE_CONTEXT_RE.fullmatch(normalized):
        return normalized.title(), "release_context"
    if _PROMO_RE.fullmatch(normalized):
        return normalized, "promo"

    words = folded.split()
    if "remix" in words or "remx" in words:
        return re.sub(r"\bremx\b", "Remix", normalized, flags=re.IGNORECASE), "remix"
    if any(
        marker in folded
        for marker in (
            "original mix",
            "radio edit",
            "extended",
            "club mix",
            "dub mix",
            "clean",
            "explicit",
            "live",
            "acoustic",
            "demo",
            "remaster",
            "edit",
            "mix",
        )
    ):
        return normalized, "version"
    return normalized, "unknown"


def parse_qualifiers(value: str) -> tuple[Qualifier, ...]:
    """Return explicitly annotated version, release-context, and noise labels."""
    text = value or ""
    qualifiers: list[Qualifier] = []
    for match in _BLOCK_RE.finditer(text):
        label, kind = _canonical_label(match.group(1))
        qualifiers.append(Qualifier(label, kind))
    trailing = _TRAILING_VIP_RE.search(text)
    if trailing and not any(item.kind == "vip" for item in qualifiers):
        label, kind = _canonical_label(trailing.group("label"))
        qualifiers.append(Qualifier(label, kind, source="trailing"))
    return tuple(qualifiers)


def has_explicit_variant(value: str) -> bool:
    """Whether a filename explicitly identifies a non-base recording version."""
    return any(item.is_recording_identity for item in parse_qualifiers(value))


def has_matching_qualifier(local: str, online: str) -> bool:
    """Compare meaningful local and online labels without treating noise as identity."""
    def identity_key(item: Qualifier) -> str:
        if item.kind != "version":
            return item.value.casefold()
        lowered = item.value.casefold()
        for marker in (
            "radio edit",
            "original mix",
            "extended mix",
            "extended",
            "club mix",
            "dub mix",
            "clean",
            "explicit",
            "live",
            "acoustic",
            "demo",
            "remaster",
        ):
            if marker in lowered:
                return marker
        return lowered

    local_values = {
        identity_key(item)
        for item in parse_qualifiers(local)
        if item.is_recording_identity
    }
    online_values = {
        identity_key(item)
        for item in parse_qualifiers(online)
        if item.is_recording_identity
    }
    if not local_values:
        return True
    return local_values <= online_values


def is_version_qualifier(value: str) -> bool:
    _label, kind = _canonical_label(value)
    return kind in {
        "instrumental",
        "a_cappella",
        "vip",
        "remix",
        "version",
    }


def split_version_qualifiers(title: str) -> tuple[str, tuple[str, ...]]:
    """Split trailing semantic version labels from a title."""
    remaining = _clean(normalize_instrumental_spelling(title))
    qualifiers: list[str] = []
    while match := _TRAILING_BLOCK_RE.search(remaining):
        label, kind = _canonical_label(match.group(1))
        if kind not in {
            "instrumental",
            "a_cappella",
            "vip",
            "remix",
            "version",
        }:
            break
        qualifiers.insert(0, label)
        remaining = _clean(remaining[: match.start()])
    return remaining, tuple(qualifiers)


def remove_safe_noise(value: str) -> str:
    """Remove only isolated technical/release/promo blocks from a title candidate."""
    def replace(match: re.Match[str]) -> str:
        label, kind = _canonical_label(match.group(1))
        del label
        return " " if kind in {"quality", "release_context", "promo"} else match.group()

    result = _BLOCK_RE.sub(replace, value or "")
    return _clean(result)


def preserve_local_versions(local_title: str, online_title: str) -> str:
    """Append explicit local recording variants absent from online metadata."""
    retained = [
        item.value
        for item in parse_qualifiers(local_title)
        if item.is_recording_identity
    ]
    if has_instrumental_qualifier(local_title) and "Instrumental" not in retained:
        retained.append("Instrumental")
    known_title = online_title
    missing = [
        value
        for value in retained
        if not has_matching_qualifier(
            f"Track ({value})",
            known_title,
        )
    ]
    return _clean(
        " ".join([online_title, *(f"({value})" for value in missing)])
    )


__all__ = [
    "Qualifier",
    "has_explicit_variant",
    "has_instrumental_qualifier",
    "has_matching_qualifier",
    "is_version_qualifier",
    "normalize_instrumental_spelling",
    "parse_qualifiers",
    "preserve_local_versions",
    "remove_safe_noise",
    "split_version_qualifiers",
]
