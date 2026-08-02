"""Typed review issues and apply-eligibility policy."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class IssueCode(StrEnum):
    DESTINATION_COLLISION = "destination_collision"
    DESTINATION_EXISTS = "destination_exists"
    VERSION_CONFLICT = "version_conflict"
    IDENTITY_CONFLICT = "identity_conflict"
    INVALID_EVIDENCE = "invalid_evidence"
    LOCAL_DERIVATIVE = "local_derivative"
    ONLINE_EVIDENCE = "online_evidence"
    AUDIO_SCORE = "audio_score"
    TAG_SYNC = "tag_sync"
    METADATA_ENRICHMENT = "metadata_enrichment"
    DUPLICATE_AUDIT = "duplicate_audit"
    GENERIC = "generic"


class IssueSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


_CATEGORY_CODES = {
    "identity-conflict": IssueCode.IDENTITY_CONFLICT,
    "tag-sync": IssueCode.TAG_SYNC,
    "metadata-identification": IssueCode.INVALID_EVIDENCE,
    "metadata-enrichment": IssueCode.METADATA_ENRICHMENT,
    "duplicate-audit": IssueCode.DUPLICATE_AUDIT,
}


def issue_code_for_message(message: str) -> IssueCode:
    if message.startswith("Identity mismatch:"):
        return IssueCode.IDENTITY_CONFLICT
    if message.startswith("Destination collides with another proposal."):
        return IssueCode.DESTINATION_COLLISION
    if message.startswith("Destination already exists:"):
        return IssueCode.DESTINATION_EXISTS
    if message.startswith("Version qualifier conflicts with AcoustID metadata;"):
        return IssueCode.VERSION_CONFLICT
    if message.startswith("Local derivative:"):
        return IssueCode.LOCAL_DERIVATIVE
    if message.startswith("Identity came from "):
        return IssueCode.ONLINE_EVIDENCE
    if message.startswith("Audio match score:"):
        return IssueCode.AUDIO_SCORE
    return IssueCode.GENERIC


def severity_for_code(code: IssueCode) -> IssueSeverity:
    if code in {
        IssueCode.DESTINATION_COLLISION,
        IssueCode.DESTINATION_EXISTS,
        IssueCode.INVALID_EVIDENCE,
    }:
        return IssueSeverity.BLOCKING
    if code in {IssueCode.VERSION_CONFLICT, IssueCode.IDENTITY_CONFLICT}:
        return IssueSeverity.WARNING
    return IssueSeverity.INFO


@dataclass(frozen=True, eq=False)
class ReviewIssue(Mapping[str, Any]):
    """An issue whose policy is independent from its display wording."""

    code: IssueCode
    message: str
    severity: IssueSeverity = IssueSeverity.INFO
    path: str = ""
    category: str = ""

    @classmethod
    def from_message(
        cls,
        message: str,
        *,
        path: str = "",
        category: str = "",
    ) -> "ReviewIssue":
        code = _CATEGORY_CODES.get(category, issue_code_for_message(message))
        return cls(
            code=code,
            message=message,
            severity=severity_for_code(code),
            path=path,
            category=category or code.value.replace("_", "-"),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReviewIssue":
        message = str(value.get("message") or "")
        category = str(value.get("category") or "")
        raw_code = value.get("code")
        code = (
            IssueCode(str(raw_code))
            if raw_code
            else _CATEGORY_CODES.get(category, issue_code_for_message(message))
        )
        raw_severity = value.get("severity")
        severity = (
            IssueSeverity(str(raw_severity))
            if raw_severity
            else severity_for_code(code)
        )
        return cls(
            code=code,
            message=message,
            severity=severity,
            path=str(value.get("path") or ""),
            category=category or code.value.replace("_", "-"),
        )

    @property
    def requires_review(self) -> bool:
        return self.severity in {IssueSeverity.WARNING, IssueSeverity.BLOCKING}

    @property
    def apply_eligible(self) -> bool:
        return self.severity is not IssueSeverity.BLOCKING

    def to_dict(self, *, legacy: bool = True) -> dict[str, Any]:
        result = {
            "path": self.path,
            "category": self.category,
            "message": self.message,
        }
        if not legacy:
            result.update(
                {
                    "code": self.code.value,
                    "severity": self.severity.value,
                }
            )
        return result

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return 3

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ReviewIssue):
            return (
                self.code,
                self.message,
                self.severity,
                self.path,
                self.category,
            ) == (
                other.code,
                other.message,
                other.severity,
                other.path,
                other.category,
            )
        return isinstance(other, Mapping) and self.to_dict() == dict(other)


def proposal_issues(messages: tuple[str, ...] | list[str]) -> tuple[ReviewIssue, ...]:
    return tuple(ReviewIssue.from_message(message) for message in messages)


def apply_eligible(issues: tuple[ReviewIssue, ...]) -> bool:
    return all(issue.apply_eligible for issue in issues)


def requires_review(issues: tuple[ReviewIssue, ...]) -> bool:
    return any(issue.requires_review for issue in issues)


__all__ = [
    "IssueCode",
    "IssueSeverity",
    "ReviewIssue",
    "apply_eligible",
    "issue_code_for_message",
    "proposal_issues",
    "requires_review",
]
