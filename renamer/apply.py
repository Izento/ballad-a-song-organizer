"""Transactional apply services for reviewed rename and tag proposals."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .domain.metadata import artwork_to_dict
from .media import read_front_artwork, read_media, write_tags_to_file
from .media.schema import metadata_matches
from .review_models import (
    ApplyResult,
    RenameProposal,
    ReviewPlan,
    TagProposal,
    canonical_path,
    path_key,
    sha256_file,
)
from .runtime import atomic_write_json, ensure_app_dirs
from .transactions import (
    ApplyBlocked,
    TransactionState,
    group_transactions,
    restore_metadata_snapshot,
)
from .transactions.journal import TransactionJournal
from .transactions.preflight import (
    preflight as transaction_preflight,
)
from .transactions.preflight import (
    selected_proposals as transaction_selected_proposals,
)

ProgressCallback = Callable[[str, int, int, ApplyResult | None], None]


def _error_details(exc: BaseException) -> tuple[int | None, int | None]:
    return getattr(exc, "errno", None), getattr(exc, "winerror", None)


def _result_error(item_id: str, path: str, exc: BaseException) -> ApplyResult:
    errno, winerror = _error_details(exc)
    return ApplyResult(
        proposal_id=item_id,
        status="failed",
        path=path,
        message=str(exc),
        error_type=type(exc).__name__,
        os_error=errno,
        winerror=winerror,
    )


def _same_path(left: str, right: str) -> bool:
    return path_key(left) == path_key(right)


def _retry_filesystem(operation, attempts: int = 6):
    """Retry past transient Windows sharing violations (winerror 32/33).

    Antivirus and search-indexer scans commonly hold a brief lock on a file
    right after it is written or renamed. A short, fixed backoff can run out
    before the lock clears, permanently stranding an apply/undo action even
    though retrying moments later would succeed. Backoff grows and is capped
    rather than fixed, to tolerate longer scans without hanging forever on a
    truly locked file.
    """
    for attempt in range(attempts):
        try:
            return operation()
        except OSError as exc:
            if getattr(exc, "winerror", None) not in {32, 33} or attempt == attempts - 1:
                raise
            time.sleep(min(0.25 * (2**attempt), 2.0))
    return None


def _rename_with_retry(source: str, destination: str) -> None:
    _retry_filesystem(lambda: os.rename(source, destination))


def _copy_with_retry(source: str, destination: str) -> None:
    _retry_filesystem(lambda: shutil.copy2(source, destination))


def _blocked_result(item_id: str, path: str, message: str) -> ApplyResult:
    return ApplyResult(
        proposal_id=item_id,
        status="blocked",
        path=path,
        message=message,
        error_type="ApplyBlocked",
    )


def _journal_path(batch_id: str) -> Path:
    return ensure_app_dirs()["journals"] / f"{batch_id}.json"


def _metadata_backup_path(batch_id: str, source: str) -> Path:
    backup_dir = ensure_app_dirs()["backups"] / batch_id
    backup_dir.mkdir(parents=True, exist_ok=True)
    path_digest = hashlib.sha256(canonical_path(source).encode("utf-8")).hexdigest()[:16]
    safe_name = f"{path_digest}-{Path(source).name}.metadata.json"
    return backup_dir / safe_name


def _tag_temporary_path(path: str, batch_id: str, proposal_id: str) -> Path:
    source = Path(path)
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "", proposal_id)[:24]
    return source.with_name(f".songorganizer-{batch_id[:12]}-{safe_id}{source.suffix}")


def _backup_front_artwork(item: TagProposal, backup: Path) -> dict | None:
    if item.artwork_before is None:
        return None
    image = read_front_artwork(item.path)
    if image is None:
        raise ApplyBlocked("Original artwork disappeared before backup.")
    data, mime_type = image
    digest = hashlib.sha256(data).hexdigest()
    if digest != item.artwork_before.sha256:
        raise ApplyBlocked("Original artwork changed before backup.")
    suffix = ".png" if mime_type == "image/png" else ".jpg"
    destination = backup.with_name(f"{backup.stem}.artwork{suffix}")
    temporary = destination.with_suffix(f"{destination.suffix}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, destination)
    return {
        "path": str(destination),
        "sha256": digest,
        "size": len(data),
        "mime_type": mime_type,
        "release_id": "",
        "source_url": "",
    }


def _prepare_tag_backup(
    item: TagProposal,
    backup: Path,
    temporary: Path,
) -> str:
    if not item.snapshot.matches(item.path):
        raise ApplyBlocked(f"Tag source changed since preflight: {item.path}")
    if temporary.exists():
        raise ApplyBlocked(f"Tag temporary path already exists: {temporary}")
    artwork_backup = _backup_front_artwork(item, backup)
    atomic_write_json(
        backup,
        {
            "before": item.before.to_dict(),
            "artwork_before": artwork_backup,
            "source": item.path,
        },
    )
    return sha256_file(item.path)


def _record_tag_intent(
    item: TagProposal,
    journal: TransactionJournal,
    backup: Path,
    temporary: Path,
) -> int:
    return journal.intent(
        "tag",
        proposal_id=item.id,
        decision_group_id=item.decision_group_id,
        path=item.path,
        backup_path=str(backup),
        temporary_path=str(temporary),
        before=item.before.to_dict(),
        after=item.after.to_dict(),
        artwork_before=artwork_to_dict(item.artwork_before),
        artwork_after=artwork_to_dict(item.artwork_after),
    )


def _write_and_verify_tag(item: TagProposal, temporary: Path) -> None:
    if item.artwork_after:
        result = _retry_filesystem(
            lambda: write_tags_to_file(str(temporary), item.after, item.artwork_after)
        )
    else:
        result = _retry_filesystem(lambda: write_tags_to_file(str(temporary), item.after))
    if result.get("status") not in {"updated", "already_ok"}:
        raise ApplyBlocked(result.get("reason", "Tag writer skipped file"))
    media = read_media(str(temporary))
    if not metadata_matches(item.after, media.tags):
        raise ApplyBlocked("Canonical tag verification failed.")
    if item.artwork_after and (
        media.artwork is None or media.artwork.get("sha256") != item.artwork_after.get("sha256")
    ):
        raise ApplyBlocked("Artwork verification failed.")


def _clean_failed_tag(
    temporary: Path,
    journal: TransactionJournal,
    action_index: int | None,
) -> None:
    if temporary.exists():
        temporary.unlink()
    if action_index is not None:
        journal.data["actions"][action_index]["rollback_status"] = "succeeded"
        journal.flush()


def _apply_tag(
    item: TagProposal,
    journal: TransactionJournal,
) -> ApplyResult:
    backup = _metadata_backup_path(journal.data["batch_id"], item.path)
    temporary = _tag_temporary_path(
        item.path,
        journal.data["batch_id"],
        item.id,
    )
    action_index: int | None = None
    try:
        post_hash_before = _prepare_tag_backup(item, backup, temporary)
        _retry_filesystem(lambda: shutil.copy2(item.path, temporary))
        action_index = _record_tag_intent(item, journal, backup, temporary)
        _write_and_verify_tag(item, temporary)
        _retry_filesystem(lambda: os.replace(temporary, item.path))
        post_hash = sha256_file(item.path)
        journal.complete(
            action_index,
            status="completed",
            post_hash=post_hash,
            original_hash=post_hash_before,
        )
        return ApplyResult(
            proposal_id=item.id,
            status="succeeded",
            path=item.path,
            message="Tags written and verified.",
            backup_path=str(backup),
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        if action_index is not None:
            journal.fail(action_index, error=str(exc))
        try:
            _clean_failed_tag(temporary, journal, action_index)
        except OSError as cleanup_exc:
            exc = RuntimeError(f"{exc}; temporary cleanup failed: {cleanup_exc}")
            if action_index is not None:
                journal.data["actions"][action_index]["rollback_status"] = "failed"
                journal.flush()
        return _result_error(item.id, item.path, exc)


def _temporary_path(path: str, batch_id: str, index: int | str) -> str:
    parent = Path(path).parent
    candidate = parent / f".songorganizer-{batch_id}-{index}.tmp"
    if candidate.exists():
        raise ApplyBlocked(f"Temporary rename path already exists: {candidate}")
    return str(candidate)


def _staged_rename_paths(
    renames: list[RenameProposal],
    journal: TransactionJournal,
) -> dict[str, str]:
    source_keys = {path_key(item.old_path) for item in renames}
    staged = {}
    for index, item in enumerate(renames):
        if path_key(item.new_path) in source_keys or _same_path(item.old_path, item.new_path):
            staged[item.id] = _temporary_path(
                item.old_path,
                journal.data["batch_id"],
                index,
            )
    return staged


def _completed_tag_path(item: RenameProposal, completed_tag_paths: set[str] | None) -> bool:
    return bool(completed_tag_paths and path_key(item.old_path) in completed_tag_paths)


def _stage_rename(
    item: RenameProposal,
    temporary: str,
    journal: TransactionJournal,
) -> ApplyResult | None:
    action_index = journal.intent(
        "rename-stage",
        proposal_id=item.id,
        decision_group_id=item.decision_group_id,
        old=item.old_path,
        new=temporary,
    )
    try:
        _rename_with_retry(item.old_path, temporary)
        journal.complete(action_index)
    except OSError as exc:
        journal.fail(action_index, error=str(exc))
        return _result_error(item.id, item.old_path, exc)
    return None


def _stage_renames(
    renames: list[RenameProposal],
    staged: dict[str, str],
    journal: TransactionJournal,
    cancel_event,
    completed_tag_paths: set[str] | None,
) -> list[ApplyResult] | None:
    for item in renames:
        if cancel_event is not None and cancel_event.is_set():
            break
        if not _completed_tag_path(item, completed_tag_paths) and not item.snapshot.matches(
            item.old_path
        ):
            return [
                ApplyResult(
                    proposal_id=item.id,
                    status="stale",
                    path=item.old_path,
                    message="Source changed before rename.",
                    error_type="ApplyBlocked",
                )
            ]
        temporary = staged.get(item.id)
        if temporary is None:
            continue
        result = _stage_rename(item, temporary, journal)
        if result is not None:
            return [result]
    return None


def _apply_rename(item: RenameProposal, current: str, journal: TransactionJournal) -> ApplyResult:
    action_index = journal.intent(
        "rename",
        proposal_id=item.id,
        decision_group_id=item.decision_group_id,
        old=item.old_path,
        new=item.new_path,
    )
    try:
        _rename_with_retry(current, item.new_path)
        journal.complete(action_index)
        return ApplyResult(
            proposal_id=item.id,
            status="succeeded",
            path=item.new_path,
            message="Rename completed.",
        )
    except OSError as exc:
        journal.fail(action_index, error=str(exc))
        return _result_error(item.id, item.old_path, exc)


def _apply_renames(
    renames: list[RenameProposal],
    journal: TransactionJournal,
    cancel_event=None,
    progress: ProgressCallback | None = None,
    completed_tag_paths: set[str] | None = None,
) -> list[ApplyResult]:
    if not renames:
        return []
    staged = _staged_rename_paths(renames, journal)
    staging_results = _stage_renames(
        renames,
        staged,
        journal,
        cancel_event,
        completed_tag_paths,
    )
    if staging_results is not None:
        return staging_results
    results: list[ApplyResult] = []
    for index, item in enumerate(renames):
        if cancel_event is not None and cancel_event.is_set():
            results.append(
                ApplyResult(
                    proposal_id=item.id,
                    status="cancelled",
                    path=item.old_path,
                    message="Cancellation requested before rename.",
                )
            )
            continue
        current = staged.get(item.id, item.old_path)
        result = _apply_rename(item, current, journal)
        results.append(result)
        if progress:
            progress("rename", index + 1, len(renames), result)
    return results


def _ordered_results(
    selected_ids: list[str],
    results: list[ApplyResult],
) -> list[ApplyResult]:
    results_by_id = {result.proposal_id: result for result in results}
    return [
        results_by_id[proposal_id] for proposal_id in selected_ids if proposal_id in results_by_id
    ]


def _blocked_selected_results(selected_ids: list[str], message: str) -> list[ApplyResult]:
    return [_blocked_result(proposal_id, "", message) for proposal_id in selected_ids]


def _record_blocked_results(
    journal: TransactionJournal,
    blocked_results: list[ApplyResult],
) -> None:
    for result in blocked_results:
        journal.event(
            "proposal-blocked",
            proposal_id=result.proposal_id,
            path=result.path,
            message=result.message,
        )


def _blocked_tag_groups(
    blocked_results: list[ApplyResult],
    selected_tags: dict[str, TagProposal],
) -> set[str]:
    return {
        selected_tags[result.proposal_id].decision_group_id
        for result in blocked_results
        if result.proposal_id in selected_tags
    }


def _block_dependent_renames(
    renames: list[RenameProposal],
    blocked_groups: set[str],
    blocked_results: list[ApplyResult],
    journal: TransactionJournal,
    message: str,
) -> list[RenameProposal]:
    dependent = [item for item in renames if item.decision_group_id in blocked_groups]
    if not dependent:
        return renames
    dependent_ids = {item.id for item in dependent}
    eligible = [item for item in renames if item.id not in dependent_ids]
    for item in dependent:
        result = _blocked_result(item.id, item.old_path, message)
        blocked_results.append(result)
        journal.event(
            "proposal-blocked",
            proposal_id=item.id,
            path=item.old_path,
            message=result.message,
        )
    return eligible


def _record_transaction_state(
    transaction,
    journal: TransactionJournal,
) -> None:
    journal.event(
        "transaction-state",
        decision_group_id=transaction.decision_group_id,
        state=transaction.state.value,
    )


def _cancelled_tag_result(item: TagProposal) -> ApplyResult:
    return ApplyResult(
        proposal_id=item.id,
        status="cancelled",
        path=item.path,
        message="Cancellation requested before tag write.",
    )


def _apply_tags(
    transactions,
    journal: TransactionJournal,
    cancel_event,
    progress: ProgressCallback | None,
    results: list[ApplyResult],
) -> tuple[dict[str, ApplyResult], dict]:
    transaction_by_group = {
        transaction.decision_group_id: transaction for transaction in transactions
    }
    tag_transactions = [transaction for transaction in transactions if transaction.tag is not None]
    tag_results_by_group: dict[str, ApplyResult] = {}
    for index, original_transaction in enumerate(tag_transactions):
        item = original_transaction.tag
        if item is None:
            continue
        if cancel_event is not None and cancel_event.is_set():
            result = _cancelled_tag_result(item)
            results.append(result)
            tag_results_by_group[item.decision_group_id] = result
            transaction_by_group[item.decision_group_id] = original_transaction.transition(
                TransactionState.CANCELLED
            )
            break
        transaction = original_transaction.transition(TransactionState.TAGGING)
        _record_transaction_state(transaction, journal)
        result = _apply_tag(item, journal)
        results.append(result)
        tag_results_by_group[item.decision_group_id] = result
        transaction = transaction.transition(
            TransactionState.TAGGED if result.status == "succeeded" else TransactionState.FAILED
        )
        transaction_by_group[item.decision_group_id] = transaction
        _record_transaction_state(transaction, journal)
        if progress:
            progress("tag", index + 1, len(tag_transactions), result)
    return tag_results_by_group, transaction_by_group


def _completed_tag_paths(
    tag_results_by_group: dict[str, ApplyResult],
    selected_tags: dict[str, TagProposal],
) -> set[str]:
    return {
        path_key(selected_tags[result.proposal_id].path)
        for result in tag_results_by_group.values()
        if result.status == "succeeded"
    }


def _complete_rename_transaction(
    result: ApplyResult,
    item: RenameProposal,
    transaction_by_group: dict,
    journal: TransactionJournal,
) -> None:
    transaction = transaction_by_group[item.decision_group_id]
    transaction = transaction.transition(TransactionState.RENAMING)
    transaction = transaction.transition(
        TransactionState.COMPLETED if result.status == "succeeded" else TransactionState.FAILED
    )
    transaction_by_group[item.decision_group_id] = transaction
    _record_transaction_state(transaction, journal)


@dataclass
class _RenameApplyContext:
    journal: TransactionJournal
    cancel_event: object
    progress: ProgressCallback | None
    results: list[ApplyResult]
    transaction_by_group: dict


def _apply_eligible_renames(
    renames: list[RenameProposal],
    tag_results_by_group: dict[str, ApplyResult],
    selected_tags: dict[str, TagProposal],
    context: _RenameApplyContext,
) -> None:
    failed_tag_groups = {
        group_id
        for group_id, result in tag_results_by_group.items()
        if result.status != "succeeded"
    }
    renames = _block_dependent_renames(
        renames,
        failed_tag_groups,
        context.results,
        context.journal,
        "Coordinated tag action did not succeed; rename was not attempted.",
    )
    rename_results = _apply_renames(
        renames,
        context.journal,
        cancel_event=context.cancel_event,
        progress=context.progress,
        completed_tag_paths=_completed_tag_paths(tag_results_by_group, selected_tags),
    )
    context.results.extend(rename_results)
    rename_by_id = {item.id: item for item in renames}
    for result in rename_results:
        _complete_rename_transaction(
            result,
            rename_by_id[result.proposal_id],
            context.transaction_by_group,
            context.journal,
        )


def _apply_status(
    results: list[ApplyResult],
    cancel_event,
) -> str:
    if any(result.status in {"failed", "stale"} for result in results):
        return "failed"
    if cancel_event is not None and cancel_event.is_set():
        return "cancelled"
    return "completed"


def _preflight_apply_plan(
    renames: list[RenameProposal],
    tags: list[TagProposal],
    selected_tags: dict[str, TagProposal],
    journal: TransactionJournal,
) -> tuple[tuple[list[RenameProposal], list[TagProposal], list[ApplyResult]] | None, str | None]:
    try:
        renames, tags, blocked_results = transaction_preflight(renames, tags)
    except ApplyBlocked as exc:
        message = str(exc)
        journal.event("preflight-failed", message=message)
        journal.finish("blocked")
        return None, message
    _record_blocked_results(journal, blocked_results)
    renames = _block_dependent_renames(
        renames,
        _blocked_tag_groups(blocked_results, selected_tags),
        blocked_results,
        journal,
        "Coordinated tag action was blocked during preflight.",
    )
    journal.event(
        "preflight-passed",
        blocked_count=len(blocked_results),
        actionable_count=len(renames) + len(tags),
    )
    return (renames, tags, blocked_results), None


def apply_review_plan(
    plan: ReviewPlan,
    selected_ids: Iterable[str],
    cancel_event=None,
    progress: ProgressCallback | None = None,
) -> list[ApplyResult]:
    """Apply selected proposals while isolating individually blocked items."""
    selected_ids = list(selected_ids)
    if not selected_ids:
        return []
    if not plan.validate_digest():
        message = "Review plan digest does not match its contents."
        return _blocked_selected_results(selected_ids, message)
    try:
        renames, tags = transaction_selected_proposals(plan, selected_ids)
    except ApplyBlocked as exc:
        return _blocked_selected_results(selected_ids, str(exc))
    selected_tags = {item.id: item for item in tags}
    journal = TransactionJournal(
        plan,
        selected_ids,
        _journal_path(plan.batch_id),
    )
    preflight_result, error_message = _preflight_apply_plan(
        renames,
        tags,
        selected_tags,
        journal,
    )
    if error_message is not None:
        return _blocked_selected_results(selected_ids, error_message)
    assert preflight_result is not None
    renames, tags, blocked_results = preflight_result
    if not renames and not tags:
        journal.finish("completed")
        return blocked_results

    journal.data["status"] = "applying"
    journal.flush()
    results: list[ApplyResult] = list(blocked_results)
    transactions = group_transactions(renames, tags)
    tag_results_by_group, transaction_by_group = _apply_tags(
        transactions,
        journal,
        cancel_event,
        progress,
        results,
    )
    rename_context = _RenameApplyContext(
        journal=journal,
        cancel_event=cancel_event,
        progress=progress,
        results=results,
        transaction_by_group=transaction_by_group,
    )
    _apply_eligible_renames(renames, tag_results_by_group, selected_tags, rename_context)
    journal.finish(_apply_status(results, cancel_event))
    return _ordered_results(selected_ids, results)


def read_batch(batch_id: str) -> dict:
    path = _journal_path(batch_id)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _undo_action_group(action: dict) -> str:
    path = action.get("path") or action.get("old") or action.get("new") or ""
    return str(action.get("decision_group_id") or path_key(path)).casefold()


def _mark_undone(action: dict) -> None:
    action.update(
        {
            "status": "undone",
            "undone_timestamp": datetime.now(UTC).isoformat(),
        }
    )
    for stale_key in ("undo_error", "undo_error_type", "undo_error_at"):
        action.pop(stale_key, None)


def _record_undo_error(action: dict, exc: BaseException) -> None:
    # Persisted so a later "Review the batch journal" is actually
    # actionable instead of pointing at a file with no failure reason.
    action["undo_error"] = str(exc)
    action["undo_error_type"] = type(exc).__name__
    action["undo_error_at"] = datetime.now(UTC).isoformat()


def _undo_interrupted_tag(action: dict, batch_id: str) -> ApplyResult:
    temporary = Path(action.get("temporary_path", ""))
    if temporary.is_file():
        temporary.unlink()
        message = "Interrupted temporary tag write discarded."
    else:
        backup_path = Path(action.get("backup_path", ""))
        if not backup_path.is_file() or not os.path.exists(action["path"]):
            raise FileNotFoundError(action.get("backup_path", ""))
        restore_metadata_snapshot(
            action["path"],
            str(backup_path),
            str(
                _tag_temporary_path(
                    action["path"],
                    batch_id,
                    f"restore-{action.get('proposal_id', '')}",
                )
            ),
            writer=write_tags_to_file,
            media_reader=read_media,
        )
        message = "Interrupted tag write restored from metadata snapshot."
    return ApplyResult(
        proposal_id=action.get("proposal_id", ""),
        status="succeeded",
        path=action.get("path", ""),
        message=message,
    )


def _restore_tag_action(action: dict, batch_id: str) -> ApplyResult:
    path = action["path"]
    if not os.path.exists(action["backup_path"]):
        raise FileNotFoundError(action["backup_path"])
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    if action.get("post_hash") and sha256_file(path) != action["post_hash"]:
        raise ApplyBlocked(f"File changed after apply: {path}")
    backup_path = Path(action["backup_path"])
    if backup_path.name.endswith(".metadata.json"):
        restore_metadata_snapshot(
            path,
            str(backup_path),
            str(
                _tag_temporary_path(
                    path,
                    batch_id,
                    f"restore-{action['proposal_id']}",
                )
            ),
            writer=write_tags_to_file,
            media_reader=read_media,
        )
    else:
        _copy_with_retry(action["backup_path"], path)
    return ApplyResult(
        proposal_id=action["proposal_id"],
        status="succeeded",
        path=path,
        message="Tags restored.",
    )


def _restore_rename_action(action: dict, batch_id: str) -> ApplyResult:
    old = action["old"]
    new = action["new"]
    if not os.path.exists(new):
        raise FileNotFoundError(new)
    if not _same_path(old, new) and os.path.exists(old):
        raise ApplyBlocked(f"Restore destination already exists: {old}")
    if _same_path(old, new):
        temporary = _temporary_path(
            new,
            batch_id,
            f"undo-{action['proposal_id']}",
        )
        _rename_with_retry(new, temporary)
        _rename_with_retry(temporary, old)
    else:
        _rename_with_retry(new, old)
    return ApplyResult(
        proposal_id=action["proposal_id"],
        status="succeeded",
        path=old,
        message="Rename restored.",
    )


def _restore_staged_rename(
    action: dict,
    results: list[ApplyResult],
) -> None:
    old = action["old"]
    temporary = action["new"]
    if os.path.exists(temporary) and not os.path.exists(old):
        _rename_with_retry(temporary, old)
        results.append(
            ApplyResult(
                proposal_id=action["proposal_id"],
                status="succeeded",
                path=old,
                message="Staged rename restored.",
            )
        )
        _mark_undone(action)
    elif os.path.exists(old) and not os.path.exists(temporary):
        _mark_undone(action)


def _undo_completed_action(
    action: dict,
    batch_id: str,
    results: list[ApplyResult],
) -> None:
    if action["kind"] == "tag":
        result = _restore_tag_action(action, batch_id)
    elif action["kind"] == "rename":
        result = _restore_rename_action(action, batch_id)
    elif action["kind"] == "rename-stage":
        _restore_staged_rename(action, results)
        return
    else:
        return
    results.append(result)
    _mark_undone(action)


def _append_undo_failure(
    action: dict,
    exc: BaseException,
    results: list[ApplyResult],
) -> None:
    _record_undo_error(action, exc)
    results.append(
        _result_error(
            action.get("proposal_id", ""),
            action.get("path", ""),
            exc,
        )
    )


def _set_undo_status(data: dict, results: list[ApplyResult]) -> None:
    remaining_completed = any(
        action.get("status") == "completed" for action in data.get("actions", [])
    )
    if any(result.status == "failed" for result in results):
        data["status"] = "recovery-required"
    elif not remaining_completed:
        data["status"] = "undone"
    else:
        data["status"] = "completed"


def undo_batch(
    batch_id: str,
    decision_group_ids: set[str] | list[str] | None = None,
) -> list[ApplyResult]:
    """Restore completed actions without overwriting unrelated files."""
    data = read_batch(batch_id)
    results: list[ApplyResult] = []
    target_groups = (
        {str(g).casefold() for g in decision_group_ids} if decision_group_ids is not None else None
    )
    for action in reversed(data.get("actions", [])):
        if target_groups is not None and _undo_action_group(action) not in target_groups:
            continue
        if action.get("status") == "intent" and action.get("kind") == "tag":
            try:
                results.append(_undo_interrupted_tag(action, batch_id))
                _mark_undone(action)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                _append_undo_failure(action, exc, results)
            continue
        if action.get("status") != "completed":
            continue
        try:
            _undo_completed_action(action, batch_id, results)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _append_undo_failure(action, exc, results)
    _set_undo_status(data, results)
    atomic_write_json(_journal_path(batch_id), data)
    return results


def incomplete_batches() -> list[dict]:
    journal_dir = ensure_app_dirs()["journals"]
    batches = []
    for path in sorted(journal_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("status") not in {"completed", "undone"}:
            batches.append(data)
    return batches


def _batch_matches_root(batch: dict, root: str | None) -> bool:
    if root is None:
        return True
    batch_root = batch.get("root")
    return bool(batch_root) and path_key(batch_root) == path_key(root)


def batches_requiring_recovery(root: str | None = None) -> list[dict]:
    """Return interrupted journals, optionally limited to one review root."""
    batches = []
    for batch in incomplete_batches():
        if not _batch_matches_root(batch, root):
            continue
        actions = batch.get("actions", ())
        if any(
            action.get("status") in {"intent", "completed"}
            or (action.get("status") == "failed" and action.get("rollback_status") != "succeeded")
            for action in actions
        ):
            batches.append(batch)
    return batches


def latest_undoable_batch(root: str | None = None) -> dict | None:
    """Return the newest undoable batch, optionally limited to one root."""
    recoverable_statuses = {
        "completed",
        "failed",
        "cancelled",
        "applying",
        "recovery-required",
    }
    return next(
        (
            batch
            for batch in batch_history()
            if _batch_matches_root(batch, root)
            if batch.get("status") in recoverable_statuses
            and any(action.get("status") == "completed" for action in batch.get("actions", ()))
        ),
        None,
    )


def batch_history() -> list[dict]:
    """Return journal summaries for the GUI history view."""
    journal_dir = ensure_app_dirs()["journals"]
    batches = []
    for path in sorted(
        journal_dir.glob("*.json"),
        key=lambda value: value.stat().st_mtime_ns,
        reverse=True,
    ):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        batches.append(data)
    return batches


__all__ = [
    "apply_review_plan",
    "batch_history",
    "batches_requiring_recovery",
    "latest_undoable_batch",
    "read_batch",
    "undo_batch",
]
