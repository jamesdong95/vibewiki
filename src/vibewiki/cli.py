"""Command-line entrypoint for VibeWiki."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from . import ANALYZER_VERSION, SCHEMA_VERSION, __version__
from .build import build_repository
from .discovery.manifest import canonical_json
from .errors import VibeWikiError, format_error
from .history import history_for_subject
from .observe import observe_repository
from .scan import scan_repository
from .serve import serve_repository

_VERSION_TEXT = (
    f"vibewiki {__version__} (analyzer {ANALYZER_VERSION}, schema {SCHEMA_VERSION})"
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
    commands = parser.add_subparsers(dest="command")
    scan_parser = commands.add_parser(
        "scan",
        help="scan a supported repository locally and offline",
    )
    scan_parser.add_argument("repository", help="repository directory to scan")
    build_parser = commands.add_parser(
        "build",
        help="build deterministic facts and wiki artifacts from a scan",
    )
    build_parser.add_argument("repository", help="repository directory to build")
    history_parser = commands.add_parser(
        "history",
        help="show scan runs touching a path or graph subject",
    )
    history_parser.add_argument("repository", help="repository directory")
    history_parser.add_argument("subject", help="relative path or graph subject")
    observe_parser = commands.add_parser(
        "observe",
        help="observe local routes with read-only GET requests",
    )
    observe_parser.add_argument("target", help="http(s) application URL")
    observe_parser.add_argument(
        "--mode",
        choices=("http", "browser"),
        default="http",
        help="observation mode; browser executes JavaScript in a safe context",
    )
    observe_parser.add_argument(
        "--repository",
        default=".",
        help="workspace where runtime.json is written (default: current directory)",
    )
    observe_parser.add_argument(
        "--allow-network",
        action="store_true",
        help="allow explicitly remote same-origin targets",
    )
    observe_parser.add_argument(
        "--max-routes",
        default=24,
        type=int,
        help="maximum same-origin document routes to GET",
    )
    observe_parser.add_argument(
        "--screenshots",
        action="store_true",
        help="save local PNG screenshots in browser mode",
    )
    serve_parser = commands.add_parser(
        "serve",
        help="serve the built local viewer on loopback",
    )
    serve_parser.add_argument("repository", help="repository directory to serve")
    serve_parser.add_argument("--host", default="127.0.0.1", help="bind address")
    serve_parser.add_argument("--port", default=4173, type=int, help="bind port")
    serve_parser.add_argument(
        "--llm-provider",
        choices=("none", "ollama", "openai-compatible"),
        help="optional grounded discussion provider; API key stays in environment",
    )
    serve_parser.add_argument(
        "--llm-model", help="model name passed to the selected LLM provider"
    )
    serve_parser.add_argument(
        "--llm-base-url", help="optional provider base URL override"
    )
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    """Run parsed commands and return a process exit status.

    Command implementations are added in later milestones. Keeping this small
    boundary lets those commands raise ``VibeWikiError`` without leaking
    tracebacks or sensitive filesystem context through the public CLI.
    """
    arguments = build_parser().parse_args(argv)
    if arguments.command == "scan":
        summary = scan_repository(arguments.repository)
        print(canonical_json(summary), end="")
    elif arguments.command == "build":
        print(canonical_json(build_repository(arguments.repository)), end="")
    elif arguments.command == "history":
        print(
            canonical_json(
                history_for_subject(arguments.repository, arguments.subject)
            ),
            end="",
        )
    elif arguments.command == "observe":
        print(
            canonical_json(
                observe_repository(
                    arguments.repository,
                    arguments.target,
                    allow_network=arguments.allow_network,
                    max_routes=arguments.max_routes,
                    mode=arguments.mode,
                    screenshots=arguments.screenshots,
                )
            ),
            end="",
        )
    elif arguments.command == "serve":
        serve_repository(
            arguments.repository,
            arguments.host,
            arguments.port,
            llm_provider=arguments.llm_provider,
            llm_model=arguments.llm_model,
            llm_base_url=arguments.llm_base_url,
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run VibeWiki and render categorised failures as concise diagnostics."""
    try:
        return run(argv)
    except VibeWikiError as error:
        print(format_error(error), file=sys.stderr)
        return error.exit_code
