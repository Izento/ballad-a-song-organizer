"""Automatic, evidence-backed metadata enrichment command."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from cli.commands.shared import command_folders, online_key
from cli.output import Output
from renamer.apply import apply_review_plan
from renamer.proposal_selection import action_items, ready_ids
from renamer.review_service import analyze_folder


def _render_preview(plan, output: Output) -> None:
    for item in plan.rename_proposals[:20]:
        output.print(f"  {item.old_path}\n  → {item.new_path}")
    output.print(f"{len(plan.tag_proposals)} verified metadata proposals prepared.")


def _process_folder(
    folder,
    args: Namespace,
    acoustid_key: str | None,
    output: Output,
) -> tuple[int, int, int]:
    path = Path(folder.path)
    if not path.is_dir():
        output.print(f"[yellow]Skipping missing folder:[/yellow] {path}")
        return 0, 0, 1
    plan = analyze_folder(
        str(path),
        recursive=folder.recursive_or(True),
        acoustid_key=acoustid_key,
        include_duplicates=False,
        enrich_metadata=True,
        include_artwork=args.cover_art,
    )
    ready = ready_ids(plan)
    selected = [item.id for item in action_items(plan) if item.id in ready]
    skipped = len(plan.rename_proposals) + len(plan.tag_proposals) - len(selected)
    problems = len(plan.issues)
    applied = 0
    if args.apply and selected:
        results = apply_review_plan(plan, selected)
        applied = sum(result.status == "succeeded" for result in results)
        problems += sum(result.status in {"blocked", "failed"} for result in results)
    else:
        _render_preview(plan, output)
    for issue in plan.issues[:10]:
        output.print(f"  [yellow]SKIPPED[/yellow] {issue['path']}: {issue['message']}")
    return applied, skipped, problems


def run(args: Namespace, output: Output) -> int:
    folders = command_folders(args, output)
    if folders is None:
        return 2
    acoustid_key = online_key(args.fingerprint, output)
    applied = skipped = problems = 0
    for folder in folders:
        folder_applied, folder_skipped, folder_problems = _process_folder(
            folder,
            args,
            acoustid_key,
            output,
        )
        applied += folder_applied
        skipped += folder_skipped
        problems += folder_problems
    if args.apply:
        output.print(
            f"Enrichment complete. Applied {applied} actions; "
            f"{skipped} ambiguous changes require review. Problems: {problems}."
        )
    else:
        output.print(
            "Enrichment preview complete. Add --apply to write verified "
            "metadata, artwork, and filename repairs."
        )
    return 1 if problems else 0


__all__ = ["run"]
