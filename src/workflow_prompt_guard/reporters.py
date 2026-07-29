"""Console, Markdown, JSON, and SARIF renderers."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from workflow_prompt_guard import __version__
from workflow_prompt_guard.catalog import RULES
from workflow_prompt_guard.models import Finding, ScanError, ScanResult, Severity

FORMAT_NAMES = ("console", "json", "markdown", "sarif")


def _counts(findings: tuple[Finding, ...]) -> dict[str, int]:
    counter = Counter(item.severity.value for item in findings)
    return {severity.value: counter[severity.value] for severity in Severity}


def _error_location(error: ScanError) -> str:
    location = error.path
    if error.line is not None:
        location += f":{error.line}"
        if error.column is not None:
            location += f":{error.column}"
    return location


def render_console(result: ScanResult) -> str:
    """Render compact, readable terminal output without hidden ANSI state."""

    lines = [
        f"WorkflowPromptGuard {__version__}",
        f"Scanned {len(result.scanned_files)} workflow file(s)",
        "",
    ]
    for finding in result.findings:
        lines.extend(
            [
                (
                    f"{finding.severity.value.upper():8} {finding.rule_id} "
                    f"{finding.path}:{finding.line}:{finding.column}"
                ),
                f"  {finding.title}",
                f"  {finding.message}",
            ]
        )
        if finding.trace:
            lines.append(f"  Trace: {' -> '.join(finding.trace)}")
        lines.extend(
            [
                f"  Fix: {finding.remediation}",
                f"  Ref: {finding.reference}",
                "",
            ]
        )

    for error in result.errors:
        lines.extend(
            [
                f"ERROR    PARSE {_error_location(error)}",
                f"  {error.message}",
                "",
            ]
        )

    if not result.findings and not result.errors:
        lines.extend(["No findings.", ""])

    counts = _counts(result.findings)
    summary = " ".join(f"{name}={count}" for name, count in counts.items())
    lines.append(f"Summary: {summary}")
    if result.ignored_findings:
        lines.append(f"Ignored by policy: {result.ignored_findings}")
    return "\n".join(lines).rstrip() + "\n"


def render_markdown(result: ScanResult) -> str:
    """Render a GitHub job-summary-friendly Markdown report."""

    lines = [
        "# WorkflowPromptGuard",
        "",
        f"Scanned **{len(result.scanned_files)}** workflow file(s).",
        "",
    ]
    if result.findings:
        lines.extend(
            [
                "| Severity | Rule | Location | Finding |",
                "| --- | --- | --- | --- |",
            ]
        )
        for finding in result.findings:
            location = f"`{finding.path}:{finding.line}:{finding.column}`"
            message = finding.message.replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| **{finding.severity.value.upper()}** | "
                f"[`{finding.rule_id}`]({finding.reference}) | {location} | {message} |"
            )
        lines.append("")
        lines.append("## Remediation")
        lines.append("")
        for finding in result.findings:
            lines.extend(
                [
                    f"### {finding.rule_id} — {finding.title}",
                    "",
                    finding.remediation,
                    "",
                ]
            )
    elif not result.errors:
        lines.extend(["No findings. ✅", ""])

    if result.errors:
        lines.extend(["## Parse errors", ""])
        lines.extend(f"- `{_error_location(error)}` — {error.message}" for error in result.errors)
        lines.append("")

    counts = _counts(result.findings)
    lines.extend(
        [
            "## Summary",
            "",
            " · ".join(f"**{name}** {count}" for name, count in counts.items()),
        ]
    )
    if result.ignored_findings:
        lines.extend(["", f"Ignored by policy: **{result.ignored_findings}**"])
    return "\n".join(lines).rstrip() + "\n"


def render_json(result: ScanResult) -> str:
    """Render stable machine-readable JSON."""

    payload = {
        "schema_version": 1,
        "tool": {"name": "WorkflowPromptGuard", "version": __version__},
        "scanned_files": list(result.scanned_files),
        "summary": _counts(result.findings),
        "ignored_findings": result.ignored_findings,
        "findings": [finding.to_dict() for finding in result.findings],
        "errors": [error.to_dict() for error in result.errors],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _sarif_level(severity: Severity) -> str:
    if severity in {Severity.CRITICAL, Severity.HIGH}:
        return "error"
    if severity is Severity.MEDIUM:
        return "warning"
    return "note"


def _sarif_rule(rule_id: str) -> dict[str, Any]:
    metadata = RULES[rule_id]
    return {
        "id": metadata.rule_id,
        "name": metadata.title.replace(" ", ""),
        "shortDescription": {"text": metadata.title},
        "fullDescription": {"text": metadata.summary},
        "help": {
            "text": metadata.remediation,
            "markdown": f"{metadata.remediation}\n\n[Reference]({metadata.reference})",
        },
        "helpUri": metadata.reference,
        "defaultConfiguration": {"level": _sarif_level(metadata.severity)},
        "properties": {
            "precision": "high",
            "security-severity": str(
                {
                    Severity.CRITICAL: 9.0,
                    Severity.HIGH: 7.0,
                    Severity.MEDIUM: 5.0,
                    Severity.LOW: 2.0,
                    Severity.INFO: 0.0,
                }[metadata.severity]
            ),
            "tags": ["security", "github-actions", "ai-agent"],
        },
    }


def _sarif_result(finding: Finding) -> dict[str, Any]:
    physical_location = {
        "artifactLocation": {"uri": finding.path, "uriBaseId": "%SRCROOT%"},
        "region": {
            "startLine": finding.line,
            "startColumn": finding.column,
        },
    }
    result: dict[str, Any] = {
        "ruleId": finding.rule_id,
        "level": _sarif_level(finding.severity),
        "message": {"text": finding.message},
        "locations": [{"physicalLocation": physical_location}],
        "partialFingerprints": {
            "primaryLocationLineHash": finding.fingerprint,
        },
        "properties": {
            "severity": finding.severity.value,
            "remediation": finding.remediation,
        },
    }
    if finding.trace:
        result["codeFlows"] = [
            {
                "threadFlows": [
                    {
                        "locations": [
                            {
                                "location": {
                                    "physicalLocation": physical_location,
                                    "message": {"text": edge},
                                }
                            }
                            for edge in finding.trace
                        ]
                    }
                ]
            }
        ]
    return result


def render_sarif(result: ScanResult) -> str:
    """Render SARIF 2.1.0 with evidence traces as code flows."""

    used_rule_ids = sorted({finding.rule_id for finding in result.findings})
    notifications = [
        {
            "descriptor": {"id": "WPG-PARSE"},
            "level": "error",
            "message": {"text": f"{_error_location(error)}: {error.message}"},
        }
        for error in result.errors
    ]
    payload = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "WorkflowPromptGuard",
                        "version": __version__,
                        "informationUri": "https://github.com/devUmut35/WorkflowPromptGuard",
                        "rules": [_sarif_rule(rule_id) for rule_id in used_rule_ids],
                    }
                },
                "originalUriBaseIds": {
                    "%SRCROOT%": {"uri": "file:///"},
                },
                "results": [_sarif_result(finding) for finding in result.findings],
                "invocations": [
                    {
                        "executionSuccessful": not result.errors,
                        "toolExecutionNotifications": notifications,
                    }
                ],
            }
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def render(result: ScanResult, format_name: str) -> str:
    """Render a result using one validated format name."""

    renderers = {
        "console": render_console,
        "json": render_json,
        "markdown": render_markdown,
        "sarif": render_sarif,
    }
    try:
        renderer = renderers[format_name]
    except KeyError as exc:
        raise ValueError(f"unknown output format: {format_name}") from exc
    return renderer(result)
