"""Per-song apply state and legal transition policy."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from ..review_models import RenameProposal, TagProposal


class ApplyBlocked(RuntimeError):
    """A reviewed action cannot safely cross the mutation boundary."""


class TransactionState(StrEnum):
    READY = "ready"
    TAGGING = "tagging"
    TAGGED = "tagged"
    RENAMING = "renaming"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TRANSITIONS = {
    TransactionState.READY: {
        TransactionState.TAGGING,
        TransactionState.RENAMING,
        TransactionState.COMPLETED,
        TransactionState.BLOCKED,
        TransactionState.CANCELLED,
    },
    TransactionState.TAGGING: {
        TransactionState.TAGGED,
        TransactionState.FAILED,
        TransactionState.CANCELLED,
    },
    TransactionState.TAGGED: {
        TransactionState.RENAMING,
        TransactionState.COMPLETED,
    },
    TransactionState.RENAMING: {
        TransactionState.COMPLETED,
        TransactionState.FAILED,
        TransactionState.CANCELLED,
    },
}


@dataclass(frozen=True)
class SongTransaction:
    decision_group_id: str
    rename: RenameProposal | None = None
    tag: TagProposal | None = None
    state: TransactionState = TransactionState.READY

    def transition(self, state: TransactionState) -> SongTransaction:
        allowed = _TRANSITIONS.get(self.state, set())
        if state not in allowed:
            raise ValueError(f"Illegal transaction transition: {self.state} -> {state}")
        return replace(self, state=state)


def group_transactions(
    renames: list[RenameProposal],
    tags: list[TagProposal],
) -> list[SongTransaction]:
    """Group at most one selected tag and rename action per song."""
    transactions: dict[str, SongTransaction] = {}
    order: list[str] = []
    for item in (*tags, *renames):
        group_id = item.decision_group_id
        if group_id not in transactions:
            transactions[group_id] = SongTransaction(group_id)
            order.append(group_id)
        current = transactions[group_id]
        if isinstance(item, TagProposal):
            if current.tag is not None:
                raise ValueError(f"Multiple tag actions for {group_id}")
            transactions[group_id] = replace(current, tag=item)
        else:
            if current.rename is not None:
                raise ValueError(f"Multiple rename actions for {group_id}")
            transactions[group_id] = replace(current, rename=item)
    return [transactions[group_id] for group_id in order]


__all__ = [
    "ApplyBlocked",
    "SongTransaction",
    "TransactionState",
    "group_transactions",
]
