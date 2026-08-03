"""Durable write-ahead journal for apply and recovery."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from ..review_models import ReviewPlan
from ..runtime import atomic_write_json


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


class TransactionJournal:
    def __init__(
        self,
        plan: ReviewPlan,
        selected_ids: Iterable[str],
        path: Path,
    ) -> None:
        self.path = path
        self.data = {
            "batch_id": plan.batch_id,
            "plan_digest": plan.digest,
            "schema_version": plan.schema_version,
            "app_version": plan.app_version,
            "root": plan.root,
            "created_at": plan.created_at,
            "status": "preflighting",
            "selected_ids": sorted(selected_ids),
            "plan": plan.to_dict(),
            "events": [],
            "actions": [],
        }
        self.flush()

    def flush(self) -> None:
        atomic_write_json(self.path, self.data)

    def event(self, kind: str, **payload) -> None:
        self.data["events"].append(
            {"kind": kind, "timestamp": _timestamp(), **payload}
        )
        self.flush()

    def intent(self, kind: str, **payload) -> int:
        action = {
            "kind": kind,
            "status": "intent",
            "intent_timestamp": _timestamp(),
            **payload,
        }
        self.data["actions"].append(action)
        self.flush()
        return len(self.data["actions"]) - 1

    def complete(self, index: int, **payload) -> None:
        self.data["actions"][index].update(
            {
                "status": "completed",
                "completed_timestamp": _timestamp(),
                **payload,
            }
        )
        self.flush()

    def fail(self, index: int, **payload) -> None:
        self.data["actions"][index].update(
            {
                "status": "failed",
                "failed_timestamp": _timestamp(),
                **payload,
            }
        )
        self.flush()

    def finish(self, status: str) -> None:
        self.data["status"] = status
        self.data["finished_at"] = _timestamp()
        self.flush()


__all__ = ["TransactionJournal"]
