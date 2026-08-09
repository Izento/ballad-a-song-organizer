"""Bootstrap and dispatch for Ballad's command-line interface."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from importlib import import_module

from cli.output import ConsoleOutput, Output
from cli.parser import parse_args
from renamer.runtime import ensure_app_dirs, resource_path

_COMMAND_MODULES = {
    "rename": "cli.commands.rename",
    "audit": "cli.commands.audit",
    "tags": "cli.commands.tags",
    "enrich": "cli.commands.enrich",
    "dedup": "cli.commands.dedup",
    "auto-detect": "cli.commands.auto_detect",
    "undo": "cli.commands.undo",
}


def _configure_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def _load_local_environment() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(resource_path(".env"))


def _run_gui() -> int:
    from gui.app import run

    run()
    return 0


def _dispatch(command: str, args, output: Output) -> int:
    if command == "gui":
        return _run_gui()
    module_name = _COMMAND_MODULES.get(command)
    if module_name is None:
        raise ValueError(f"Unknown command: {command}")
    return import_module(module_name).run(args, output)


def main(
    argv: Sequence[str] | None = None,
    *,
    output: Output | None = None,
) -> int:
    _configure_utf8_console()
    _load_local_environment()
    ensure_app_dirs()
    args = parse_args(argv)
    if args.command is None:
        return _run_gui()
    return _dispatch(args.command, args, output or ConsoleOutput())


__all__ = ["main"]
