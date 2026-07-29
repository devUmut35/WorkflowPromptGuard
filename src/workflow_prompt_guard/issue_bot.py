"""Secure orchestration for public repository scan requests opened as issues."""

from __future__ import annotations

import json
import re
import tempfile
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from workflow_prompt_guard.catalog import RULES
from workflow_prompt_guard.config import Config
from workflow_prompt_guard.engine import scan
from workflow_prompt_guard.github_api import GitHubServiceError, HTTPSJsonTransport
from workflow_prompt_guard.github_models import (
    GitHubModelsClient,
    ModelSummary,
    ModelSummaryError,
)
from workflow_prompt_guard.models import ScanResult, Severity
from workflow_prompt_guard.remote_repository import (
    GitHubRepositoryClient,
    RemoteSnapshot,
    RepositoryRef,
    RepositoryRequestError,
    parse_repository_request,
)

BOT_MARKER = "<!-- workflow-prompt-guard:issue-report:v1 -->"
AI_PLACEHOLDER = "<!-- workflow-prompt-guard:ai-summary -->"
SCAN_LABEL = "scan-request"
AI_APPROVAL_LABEL = "ai-approved"
TRUSTED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
MAX_EVENT_BYTES = 1_000_000
MAX_ARTIFACT_BYTES = 256_000
MAX_COMMENT_FINDINGS = 25
MAX_COMMENT_ERRORS = 10
MAX_COMMENT_LENGTH = 60_000
_CURRENT_REPOSITORY = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9_.-]{1,100}$")


class IssueBotError(ValueError):
    """An issue event or an intermediate bot artifact was invalid."""


class RepositoryFetcher(Protocol):
    """Minimal remote repository interface used by the scan preparation stage."""

    def fetch_public_workflows(self, repository: RepositoryRef) -> RemoteSnapshot:
        """Return a commit-pinned set of public workflow files."""


class SummaryClient(Protocol):
    """Minimal model interface used by the isolated AI stage."""

    def summarize(self, value: Any) -> ModelSummary:
        """Return a validated model summary."""


@dataclass(frozen=True)
class IssueRequest:
    """Trusted context plus one validated target repository."""

    issue_number: int
    source_repository: str
    target_repository: RepositoryRef
    ai_allowed: bool


def _load_bounded_json(path: Path, *, maximum: int) -> Any:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise IssueBotError("could not read the input artifact") from exc
    if len(raw) > maximum:
        raise IssueBotError("input artifact exceeded its size limit")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise IssueBotError("input artifact was not valid UTF-8 JSON") from exc


def load_issue_request(path: Path) -> IssueRequest:
    """Load an ``issues`` event without interpolating attacker-controlled fields in a shell."""

    payload = _load_bounded_json(path, maximum=MAX_EVENT_BYTES)
    if not isinstance(payload, dict) or payload.get("action") not in {
        "opened",
        "reopened",
        "labeled",
    }:
        raise IssueBotError("event must be an opened, reopened, or approved issue")
    if payload.get("action") == "labeled":
        event_label = payload.get("label")
        if not isinstance(event_label, dict) or event_label.get("name") != AI_APPROVAL_LABEL:
            raise IssueBotError("labeled event did not approve an AI explanation")

    repository = payload.get("repository")
    issue = payload.get("issue")
    if not isinstance(repository, dict) or not isinstance(issue, dict):
        raise IssueBotError("event did not contain repository and issue objects")

    source_repository = repository.get("full_name")
    issue_number = issue.get("number")
    body = issue.get("body")
    labels = issue.get("labels")
    author_association = issue.get("author_association")
    if (
        not isinstance(source_repository, str)
        or _CURRENT_REPOSITORY.fullmatch(source_repository) is None
    ):
        raise IssueBotError("event repository was not canonical owner/name")
    if not isinstance(issue_number, int) or isinstance(issue_number, bool) or issue_number < 1:
        raise IssueBotError("event issue number was invalid")
    if not isinstance(body, str):
        raise IssueBotError("issue body was missing")
    if not isinstance(labels, list) or not any(
        isinstance(label, dict) and label.get("name") == SCAN_LABEL for label in labels
    ):
        raise IssueBotError(f"issue must carry the {SCAN_LABEL!r} label")

    return IssueRequest(
        issue_number=issue_number,
        source_repository=source_repository,
        target_repository=parse_repository_request(body),
        ai_allowed=author_association in TRUSTED_ASSOCIATIONS
        or any(
            isinstance(label, dict) and label.get("name") == AI_APPROVAL_LABEL for label in labels
        ),
    )


def _remap_result(
    result: ScanResult,
    paths: Mapping[str, str],
) -> ScanResult:
    findings = tuple(
        replace(finding, path=paths.get(finding.path, finding.path)) for finding in result.findings
    )
    errors = tuple(
        replace(error, path=paths.get(error.path, error.path)) for error in result.errors
    )
    scanned_files = tuple(paths.get(path, path) for path in result.scanned_files)
    return replace(
        result,
        findings=findings,
        errors=errors,
        scanned_files=scanned_files,
    )


def scan_snapshot(snapshot: RemoteSnapshot) -> ScanResult:
    """Materialize only remote workflow blobs and scan them with default, unsuppressed policy."""

    with tempfile.TemporaryDirectory(prefix="workflow-prompt-guard-") as directory:
        root = Path(directory).resolve()
        workflow_root = root / ".github" / "workflows"
        workflow_root.mkdir(parents=True)
        display_paths: dict[str, str] = {}

        for index, workflow in enumerate(snapshot.workflows):
            suffix = Path(workflow.path).suffix.casefold()
            local = workflow_root / f"{index:03d}-{workflow.sha[:12]}{suffix}"
            local.write_bytes(workflow.content)
            local_display = local.relative_to(root).as_posix()
            display_paths[local_display] = workflow.path

        result = scan((root,), root=root, config=Config())
        return _remap_result(result, display_paths)


def _safe_markdown(value: str) -> str:
    cleaned = "".join(character for character in value if character >= " " or character == "\t")
    for character in ("\\", "*", "_", "[", "]", "(", ")", "#", "!"):
        cleaned = cleaned.replace(character, f"\\{character}")
    return (
        cleaned.replace("&", "&amp;")
        .replace("@", "&#64;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("|", "\\|")
        .replace("`", "&#96;")
    )


def _counts(result: ScanResult) -> dict[str, int]:
    counter = Counter(finding.severity.value for finding in result.findings)
    return {severity.value: counter[severity.value] for severity in Severity}


def _summary_line(result: ScanResult) -> str:
    counts = _counts(result)
    return " · ".join(
        f"**{severity.value}** {counts[severity.value]}" for severity in reversed(tuple(Severity))
    )


def _target_link(snapshot: RemoteSnapshot) -> str:
    target = snapshot.repository
    url = f"{target.canonical_url}/tree/{snapshot.commit_sha}"
    return f"[`{target.full_name}@{snapshot.commit_sha[:12]}`]({url})"


def render_scan_comment(
    snapshot: RemoteSnapshot,
    result: ScanResult,
    *,
    ai_allowed: bool = True,
) -> str:
    """Render a bounded deterministic report with no raw workflow content."""

    lines = [
        BOT_MARKER,
        "## WorkflowPromptGuard scan",
        "",
        f"**Target:** {_target_link(snapshot)}",
        f"**Workflows scanned:** {len(result.scanned_files)}",
        "",
        _summary_line(result),
        "",
    ]

    if result.findings:
        lines.extend(
            [
                "### Deterministic findings",
                "",
                "| Severity | Rule | Location | Finding |",
                "| --- | --- | --- | --- |",
            ]
        )
        for finding in result.findings[:MAX_COMMENT_FINDINGS]:
            location = _safe_markdown(f"{finding.path}:{finding.line}:{finding.column}")
            title = _safe_markdown(finding.title)
            lines.append(
                f"| **{finding.severity.value.upper()}** | `{finding.rule_id}` | "
                f"`{location}` | {title} |"
            )
        hidden = len(result.findings) - MAX_COMMENT_FINDINGS
        if hidden > 0:
            lines.extend(
                [
                    "",
                    f"_Only the first {MAX_COMMENT_FINDINGS} findings are shown; "
                    f"{hidden} more were found._",
                ]
            )
        lines.append("")
    elif not result.errors and result.scanned_files:
        lines.extend(
            [
                "No findings reached the scanner's rule set. This is not a security guarantee.",
                "",
            ]
        )
    elif not result.scanned_files:
        lines.extend(
            [
                "No supported workflow files were found in `.github/workflows`.",
                "",
            ]
        )

    if result.errors:
        lines.extend(["### Files that could not be parsed", ""])
        for error in result.errors[:MAX_COMMENT_ERRORS]:
            location = error.path
            if error.line is not None:
                location += f":{error.line}"
            lines.append(f"- `{_safe_markdown(location)}` — {_safe_markdown(error.message)}")
        hidden_errors = len(result.errors) - MAX_COMMENT_ERRORS
        if hidden_errors > 0:
            lines.append(f"- _{hidden_errors} additional parse errors were omitted._")
        lines.append("")

    lines.extend(
        [
            AI_PLACEHOLDER,
            "",
            *(
                [
                    "_AI explanation is limited to repository collaborators or scans carrying "
                    "the maintainer-applied `ai-approved` label._",
                    "",
                ]
                if not ai_allowed
                else []
            ),
            "The findings and pass/fail interpretation above come from the deterministic scanner. "
            "The optional AI section is explanatory only.",
        ]
    )
    comment = "\n".join(lines).rstrip() + "\n"
    if len(comment) > MAX_COMMENT_LENGTH:
        raise IssueBotError("rendered issue comment exceeded its size limit")
    return comment


def render_request_error() -> str:
    """Render usage instructions without reflecting attacker-controlled input."""

    return (
        "\n".join(
            [
                BOT_MARKER,
                "## WorkflowPromptGuard scan request",
                "",
                "I could not process this request.",
                "",
                "Use the **Scan a public repository** issue form and provide exactly one URL "
                "on its own line:",
                "",
                "```text",
                "https://github.com/OWNER/REPOSITORY",
                "```",
                "",
                "Only public GitHub repositories are supported. Branches, subdirectories, query "
                "strings, fragments, credentials, and non-GitHub hosts are rejected.",
                "",
                AI_PLACEHOLDER,
            ]
        )
        + "\n"
    )


def render_service_error() -> str:
    """Render a non-sensitive upstream failure response."""

    return (
        "\n".join(
            [
                BOT_MARKER,
                "## WorkflowPromptGuard scan request",
                "",
                "The repository was unavailable, private, too large, or could not be read safely.",
                "No target repository code was executed. You can reopen the issue to try again.",
                "",
                AI_PLACEHOLDER,
            ]
        )
        + "\n"
    )


def build_summary_input(
    snapshot: RemoteSnapshot,
    result: ScanResult,
    *,
    ai_allowed: bool = True,
) -> dict[str, Any]:
    """Create a prompt-safe artifact containing only catalog-backed aggregates."""

    rule_counts = Counter(finding.rule_id for finding in result.findings)
    return {
        "schema_version": 1,
        "enabled": bool(result.scanned_files) and ai_allowed,
        "repository": snapshot.repository.full_name,
        "commit_sha": snapshot.commit_sha,
        "scanned_files": len(result.scanned_files),
        "counts": _counts(result),
        "rules": [
            {"rule_id": rule_id, "count": rule_counts[rule_id]}
            for rule_id in RULES
            if rule_counts[rule_id]
        ],
    }


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_prepare_artifacts(output_dir: Path, comment: str, summary_input: Any) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "comment.md").write_text(comment, encoding="utf-8")
    _write_json(output_dir / "ai-input.json", summary_input)


def _disabled_summary_input() -> dict[str, Any]:
    return {"schema_version": 1, "enabled": False}


def prepare_issue_scan(
    event_path: Path,
    output_dir: Path,
    *,
    token: str,
    repository_client: RepositoryFetcher | None = None,
) -> int:
    """Prepare deterministic report artifacts for the read-only scan job."""

    try:
        request = load_issue_request(event_path)
        client = repository_client
        if client is None:
            client = GitHubRepositoryClient(HTTPSJsonTransport(token))
        snapshot = client.fetch_public_workflows(request.target_repository)
        result = scan_snapshot(snapshot)
        _write_prepare_artifacts(
            output_dir,
            render_scan_comment(snapshot, result, ai_allowed=request.ai_allowed),
            build_summary_input(snapshot, result, ai_allowed=request.ai_allowed),
        )
    except (IssueBotError, RepositoryRequestError):
        _write_prepare_artifacts(output_dir, render_request_error(), _disabled_summary_input())
    except (GitHubServiceError, OSError, UnicodeError):
        _write_prepare_artifacts(output_dir, render_service_error(), _disabled_summary_input())
    return 0


def render_model_summary(summary: ModelSummary) -> str:
    """Render already validated model text with explicit AI provenance."""

    lines = [
        "### AI-generated explanation",
        "",
        f"_Generated by GitHub Models (`{summary.model}`); advisory only._",
        "",
        _safe_markdown(summary.overview),
    ]
    if summary.recommendations:
        lines.extend(["", "**Suggested priorities:**", ""])
        lines.extend(f"- {_safe_markdown(item)}" for item in summary.recommendations)
    return "\n".join(lines).rstrip() + "\n"


def render_model_fallback() -> str:
    """Explain that quota or service failure did not affect the deterministic scan."""

    return (
        "\n".join(
            [
                "### AI-generated explanation",
                "",
                "_GitHub Models was unavailable or rate-limited. The deterministic scan report is "
                "complete and remains the source of truth._",
            ]
        )
        + "\n"
    )


def summarize_artifact(
    input_path: Path,
    output_path: Path,
    *,
    token: str,
    model_client: SummaryClient | None = None,
) -> int:
    """Generate a bounded model summary or a deterministic fallback artifact."""

    try:
        value = _load_bounded_json(input_path, maximum=MAX_ARTIFACT_BYTES)
        if isinstance(value, dict) and value.get("enabled") is False:
            summary_text = ""
        else:
            client = model_client
            if client is None:
                client = GitHubModelsClient(HTTPSJsonTransport(token))
            summary_text = render_model_summary(client.summarize(value))
    except (IssueBotError, ModelSummaryError, GitHubServiceError):
        summary_text = render_model_fallback()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(summary_text, encoding="utf-8")
    return 0
