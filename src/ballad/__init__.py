"""Public package namespace for Ballad."""

from __future__ import annotations

from renamer.version import __version__


def main() -> int:
    from cli import main as cli_main

    return cli_main()


__all__ = ["__version__", "main"]
