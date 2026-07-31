"""Command-line interface for WorkflowPromptGuard."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from workflow_prompt_guard import __version__
from workflow_prompt_guard.catalog import RULES, get_rule
from workflow_prompt_guard.config import ConfigError, discover_config, load_config
from workflow_prompt_guard.engine import scan
from workflow_prompt_guard.issue_bot import prepare_issue_scan, summarize_artifact
from workflow_prompt_guard.models import Severity
from workflow_prompt_guard.reporters import FORMAT_NAMES, render


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workflow-prompt-guard",
        description="Audit trust boundaries in AI-agent GitHub workflows.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser(
        "scan",
        help="scan workflow files or repository roots",
    )
    scan_parser.add_argument(
        "paths",
        nargs="*",
        default=["."],
        help="workflow file or repository root (default: current directory)",
    )
    scan_parser.add_argument(
        "--config",
        type=Path,
        help="configuration file (default: discover .workflow-prompt-guard.yml)",
    )
    scan_parser.add_argument(
        "--format",
        choices=FORMAT_NAMES,
        default="console",
        help="report format (default: console)",
    )
    scan_parser.add_argument(
        "--output",
        type=Path,
        help="write the report to a file instead of stdout",
    )
    scan_parser.add_argument(
        "--fail-on",
        choices=[severity.value for severity in Severity],
        help="override the configured failure threshold",
    )
    scan_parser.add_argument(
        "--include-generated",
        action="store_true",
        help="also scan generated *.lock.yml workflows",
    )

    rules_parser = subparsers.add_parser("rules", help="list the implemented rule catalog")
    rules_parser.add_argument("--json", action="store_true", help="emit JSON")

    explain_parser = subparsers.add_parser("explain", help="explain one rule")
    explain_parser.add_argument("rule_id", help="rule identifier, for example AI001")

    bot_parser = subparsers.add_parser(
        "issue-bot",
        help="run the hosted issue scan bot stages",
    )
    bot_subparsers = bot_parser.add_subparsers(dest="bot_command", required=True)
    prepare_parser = bot_subparsers.add_parser(
        "prepare",
        help="validate an issue event and prepare deterministic scan artifacts",
    )
    prepare_parser.add_argument("--event", type=Path, required=True)
    prepare_parser.add_argument("--output-dir", type=Path, required=True)
    summarize_parser = bot_subparsers.add_parser(
        "summarize",
        help="create an optional LLM7.io explanation from a sanitized artifact",
    )
    summarize_parser.add_argument("--input", type=Path, required=True)
    summarize_parser.add_argument("--output", type=Path, required=True)
    return parser


def _write_report(report: str, output: Path | None) -> None:
    if output is None:
        sys.stdout.write(report)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")


def _scan_command(arguments: argparse.Namespace) -> int:
    root = Path.cwd().resolve()
    config_path = arguments.config
    if config_path is None:
        config_path = discover_config(root)
    elif not config_path.is_absolute():
        config_path = root / config_path

    try:
        config = load_config(config_path)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    if arguments.include_generated:
        config = replace(config, include_generated=True)
    threshold = (
        Severity.parse(arguments.fail_on) if arguments.fail_on is not None else config.fail_on
    )
    paths = tuple(Path(value) for value in arguments.paths)
    result = scan(paths, root=root, config=config)
    report = render(result, arguments.format)
    try:
        _write_report(report, arguments.output)
    except OSError as exc:
        print(f"cannot write report: {exc}", file=sys.stderr)
        return 2

    if not result.scanned_files and not result.errors:
        print("no supported workflow files found", file=sys.stderr)
        return 2
    if result.errors:
        return 2
    return 1 if result.failing_findings(threshold) else 0


def _rules_command(as_json: bool) -> int:
    if as_json:
        payload = [
            {
                "rule_id": rule.rule_id,
                "title": rule.title,
                "severity": rule.severity.value,
                "summary": rule.summary,
                "remediation": rule.remediation,
                "reference": rule.reference,
            }
            for rule in RULES.values()
        ]
        print(json.dumps(payload, indent=2))
        return 0

    for rule in RULES.values():
        print(f"{rule.rule_id} {rule.severity.value.upper():8} {rule.title}")
    return 0


def _explain_command(rule_id: str) -> int:
    try:
        rule = get_rule(rule_id.upper())
    except KeyError:
        print(f"unknown rule: {rule_id}", file=sys.stderr)
        return 2
    print(f"{rule.rule_id} — {rule.title}")
    print(f"Severity: {rule.severity.value}")
    print()
    print(rule.summary)
    print()
    print(f"Remediation: {rule.remediation}")
    print(f"Reference: {rule.reference}")
    return 0


def _issue_bot_command(arguments: argparse.Namespace) -> int:
    if arguments.bot_command == "prepare":
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            print("GITHUB_TOKEN is required for the hosted issue bot", file=sys.stderr)
            return 2
        return prepare_issue_scan(arguments.event, arguments.output_dir, token=token)
    if arguments.bot_command == "summarize":
        return summarize_artifact(arguments.input, arguments.output)
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface and return a process exit code."""

    parser = _parser()
    arguments = parser.parse_args(argv)
    if arguments.command == "scan":
        return _scan_command(arguments)
    if arguments.command == "rules":
        return _rules_command(arguments.json)
    if arguments.command == "explain":
        return _explain_command(arguments.rule_id)
    if arguments.command == "issue-bot":
        return _issue_bot_command(arguments)
    parser.error(f"unknown command: {arguments.command}")
    return 2
