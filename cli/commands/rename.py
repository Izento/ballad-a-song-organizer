"""Filename review and apply command."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from cli.commands.shared import command_folders, online_key
from cli.output import Output
from renamer.apply import apply_review_plan
from renamer.proposal_selection import eligible_ids
from renamer.review_models import ReviewPlan
from renamer.review_service import plan_renames
from renamer.track_extraction import scan_folder


def _selected_ids(proposals, interactive: bool, output: Output) -> list[str]:
    eligible = set(eligible_ids(proposals, include_review=interactive))
    selected = []
    for item in proposals:
        if item.id not in eligible:
            continue
        if interactive and not output.confirm(
            f"\n{item.old_path}\n→ {item.new_path}\nApply? [Y/n] "
        ):
            continue
        selected.append(item.id)
    return selected


def _process_folder(
    folder,
    args: Namespace,
    acoustid_key: str | None,
    output: Output,
) -> tuple[int, int, int, int] | None:
    path = Path(folder.path)
    if not path.is_dir():
        output.print(f"[yellow]Skipping missing folder:[/yellow] {path}")
        return None
    recursive = folder.recursive_or(True)
    proposals, issues = plan_renames(
        folder_path=str(path),
        strategy=folder.strategy,
        recursive=recursive,
        lookup=args.lookup or folder.lookup,
        acoustid_key=acoustid_key,
    )
    total_files = len(scan_folder(str(path), recursive=recursive))
    problems = len(issues)
    selected = _selected_ids(proposals, args.interactive, output)
    renamed = would_rename = 0
    if args.apply and selected:
        review = ReviewPlan.create(
            root=str(path),
            recursive=recursive,
            rename_proposals=proposals,
            issues=issues,
        )
        results = apply_review_plan(review, selected)
        renamed = sum(result.status == "succeeded" for result in results)
        problems += sum(result.status in {"blocked", "failed"} for result in results)
    else:
        would_rename = len(selected)
        for item in proposals[:20]:
            output.print(f"  {item.old_path}\n  → {item.new_path}")
    for issue in issues[:10]:
        output.print(f'  [red]ERROR[/red] {issue["path"]}: {issue["message"]}')
    return renamed, would_rename, total_files, problems


def run(args: Namespace, output: Output) -> int:
    folders = command_folders(
        args,
        output,
        include_strategy=True,
        include_lookup=True,
    )
    if folders is None:
        return 2
    acoustid_key = online_key(args.fingerprint, output)
    renamed = would_rename = total_files = problems = valid_folders = 0
    for folder in folders:
        result = _process_folder(folder, args, acoustid_key, output)
        if result is None:
            problems += 1
            continue
        folder_renamed, folder_would_rename, file_count, folder_problems = result
        valid_folders += 1
        renamed += folder_renamed
        would_rename += folder_would_rename
        total_files += file_count
        problems += folder_problems
    if args.apply:
        output.print(
            f"\nDone. Renamed {renamed} of {total_files} files. "
            f"Problems: {problems}."
        )
    else:
        output.print(
            f"\nDry run complete — {would_rename} of {total_files} files "
            f"would be renamed. Problems: {problems}. "
            "Add --apply to commit selected changes."
        )
    return 1 if valid_folders == 0 or problems else 0


__all__ = ["run"]
