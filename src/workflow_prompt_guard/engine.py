"""Scanner orchestration across discovery, parsing, rules, and policy."""

from __future__ import annotations

from pathlib import Path

from workflow_prompt_guard.config import Config
from workflow_prompt_guard.discovery import discover_workflows
from workflow_prompt_guard.loader import WorkflowParseError, load_workflow
from workflow_prompt_guard.models import Finding, ScanError, ScanResult
from workflow_prompt_guard.rules import evaluate


def scan(
    inputs: tuple[Path, ...],
    *,
    root: Path,
    config: Config,
) -> ScanResult:
    """Scan explicit files or repository roots and return deterministic results."""

    paths = discover_workflows(
        inputs,
        root=root,
        exclude=config.exclude,
        include_generated=config.include_generated,
    )
    findings: list[Finding] = []
    errors: list[ScanError] = []
    scanned_files: list[str] = []
    ignored = 0

    for path in paths:
        try:
            document = load_workflow(path, root=root)
        except (OSError, UnicodeError, WorkflowParseError) as exc:
            try:
                display_path = path.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                display_path = path.resolve().as_posix()
            errors.append(
                ScanError(
                    path=display_path,
                    message=str(exc),
                    line=getattr(exc, "line", None),
                    column=getattr(exc, "column", None),
                )
            )
            continue

        scanned_files.append(document.display_path)
        for finding in evaluate(document):
            if config.is_ignored(finding):
                ignored += 1
            else:
                findings.append(finding)

    findings.sort(
        key=lambda item: (
            item.path.casefold(),
            item.line,
            item.column,
            -item.severity.rank,
            item.rule_id,
        )
    )
    errors.sort(key=lambda item: (item.path.casefold(), item.line or 0, item.column or 0))
    return ScanResult(
        scanned_files=tuple(scanned_files),
        findings=tuple(findings),
        errors=tuple(errors),
        ignored_findings=ignored,
    )
