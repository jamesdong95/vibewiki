"""Command-line entrypoint for VibeWiki."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from . import ANALYZER_VERSION, SCHEMA_VERSION, __version__
from .errors import VibeWikiError, format_error

_VERSION_TEXT = (
    f"vibewiki {__version__} "
    f"(analyzer {ANALYZER_VERSION}, schema {SCHEMA_VERSION})"
)


def build_parser() -> argparse.ArgumentParser:
    """Build the initial VibeWiki command-line parser."""
    parser = argparse.ArgumentParser(
        prog="vibewiki",
        description="Evidence-based product reverse engineering for local codebases.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=_VERSION_TEXT,
    )
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    """Run parsed commands and return a process exit status.

    Command implementations are added in later milestones. Keeping this small
    boundary lets those commands raise ``VibeWikiError`` without leaking
    tracebacks or sensitive filesystem context through the public CLI.
    """
    build_parser().parse_args(argv)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run VibeWiki and render categorised failures as concise diagnostics."""
    try:
        return run(argv)
    except VibeWikiError as error:
        print(format_error(error), file=sys.stderr)
        return error.exit_code
