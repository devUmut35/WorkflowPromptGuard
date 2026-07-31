"""Secure orchestration for public repository scan requests opened as issues."""

from __future__ import annotations

import json
import re
import sys
import tempfile
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from workflow_prompt_guard.catalog import RULES
from workflow_prompt_guard.config import Config
from workflow_prompt_guard.engine import scan
from workflow_prompt_guard.github_api import (
    AnonymousLLM7Transport,
    GitHubServiceError,
    HTTPSJsonTransport,
)
from workflow_prompt_guard.github_models import (
    MODEL_PROVIDER,
    CloudModelsClient,
    ModelSummary,
    ModelSummaryError,
)
from workflow_prompt_guard.localization import (
    FORM_LANGUAGE_HEADING,
    FORM_LANGUAGE_OPTIONS,
    ReportLanguage,
    rule_title,
    severity_label,
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
    language: ReportLanguage


def _load_bounded_json(path: Path, *, maximum: int) -> Any:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise IssueBotError("could not read the input artifact") from exc
    if len(raw) > maximum:
        raise IssueBotError("input artifact exceeded its size limit")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise IssueBotError("input artifact was not valid UTF-8 JSON") from exc


def parse_report_language(body: str) -> ReportLanguage:
    """Read one closed-set language selection from a GitHub issue-form body."""

    lines = body.splitlines()
    headings = [index for index, line in enumerate(lines) if line.strip() == FORM_LANGUAGE_HEADING]
    if not headings:
        return ReportLanguage.ENGLISH
    if len(headings) != 1:
        raise IssueBotError("issue body contained an ambiguous report language")

    selections: list[str] = []
    for line in lines[headings[0] + 1 :]:
        selection = line.strip()
        if not selection:
            continue
        if selection.startswith("### "):
            break
        selections.append(selection)
    if len(selections) != 1:
        raise IssueBotError("issue body did not contain one report language selection")
    try:
        return FORM_LANGUAGE_OPTIONS[selections[0]]
    except KeyError as exc:
        raise IssueBotError("issue body selected an unsupported report language") from exc


def _event_report_language(path: Path) -> ReportLanguage:
    """Best-effort language selection for localized safe error reports."""

    try:
        payload = _load_bounded_json(path, maximum=MAX_EVENT_BYTES)
        if not isinstance(payload, dict) or not isinstance(payload.get("issue"), dict):
            return ReportLanguage.ENGLISH
        body = payload["issue"].get("body")
        if not isinstance(body, str):
            return ReportLanguage.ENGLISH
        return parse_report_language(body)
    except IssueBotError:
        return ReportLanguage.ENGLISH


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
        language=parse_report_language(body),
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


def _summary_line(result: ScanResult, language: ReportLanguage) -> str:
    counts = _counts(result)
    return " · ".join(
        f"**{severity_label(severity, language)}** {counts[severity.value]}"
        for severity in reversed(tuple(Severity))
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
    language: ReportLanguage = ReportLanguage.ENGLISH,
) -> str:
    """Render a bounded deterministic report with no raw workflow content."""

    turkish = language is ReportLanguage.TURKISH
    lines = [
        BOT_MARKER,
        "## WorkflowPromptGuard taraması" if turkish else "## WorkflowPromptGuard scan",
        "",
        f"**{'Hedef' if turkish else 'Target'}:** {_target_link(snapshot)}",
        (
            f"**Taranan iş akışı dosyası:** {len(result.scanned_files)}"
            if turkish
            else f"**Workflows scanned:** {len(result.scanned_files)}"
        ),
        "",
        _summary_line(result, language),
        "",
    ]

    if result.findings:
        lines.extend(
            [
                "### Deterministik bulgular" if turkish else "### Deterministic findings",
                "",
                (
                    "| Önem | Kural | Konum | Bulgu |"
                    if turkish
                    else "| Severity | Rule | Location | Finding |"
                ),
                "| --- | --- | --- | --- |",
            ]
        )
        for finding in result.findings[:MAX_COMMENT_FINDINGS]:
            location = _safe_markdown(f"{finding.path}:{finding.line}:{finding.column}")
            title = _safe_markdown(rule_title(finding.rule_id, finding.title, language))
            localized_severity = severity_label(finding.severity, language)
            severity = localized_severity.capitalize() if turkish else localized_severity.upper()
            lines.append(f"| **{severity}** | `{finding.rule_id}` | `{location}` | {title} |")
        hidden = len(result.findings) - MAX_COMMENT_FINDINGS
        if hidden > 0:
            lines.extend(
                [
                    "",
                    (
                        f"_Yalnızca ilk {MAX_COMMENT_FINDINGS} bulgu gösteriliyor; "
                        f"{hidden} bulgu daha bulundu._"
                        if turkish
                        else f"_Only the first {MAX_COMMENT_FINDINGS} findings are shown; "
                        f"{hidden} more were found._"
                    ),
                ]
            )
        lines.append("")
    elif not result.errors and result.scanned_files:
        lines.extend(
            [
                (
                    "Tarayıcının mevcut kural kümesine göre bulgu bulunmadı. "
                    "Bu sonuç bir güvenlik garantisi değildir."
                    if turkish
                    else "No findings reached the scanner's rule set. "
                    "This is not a security guarantee."
                ),
                "",
            ]
        )
    elif not result.scanned_files:
        lines.extend(
            [
                (
                    "`.github/workflows` dizininde desteklenen bir iş akışı dosyası bulunamadı."
                    if turkish
                    else "No supported workflow files were found in `.github/workflows`."
                ),
                "",
            ]
        )

    if result.errors:
        lines.extend(
            [
                "### Ayrıştırılamayan dosyalar"
                if turkish
                else "### Files that could not be parsed",
                "",
            ]
        )
        for error in result.errors[:MAX_COMMENT_ERRORS]:
            location = error.path
            if error.line is not None:
                location += f":{error.line}"
            error_message = "Dosya güvenli biçimde ayrıştırılamadı." if turkish else error.message
            lines.append(f"- `{_safe_markdown(location)}` — {_safe_markdown(error_message)}")
        hidden_errors = len(result.errors) - MAX_COMMENT_ERRORS
        if hidden_errors > 0:
            lines.append(
                f"- _{hidden_errors} ek ayrıştırma hatası gösterilmedi._"
                if turkish
                else f"- _{hidden_errors} additional parse errors were omitted._"
            )
        lines.append("")

    ai_gate_notice = (
        "_Yapay zekâ açıklaması yalnızca isteği açan kullanıcının bu WorkflowPromptGuard "
        "deposundaki ilişkisi `OWNER`, `MEMBER` veya `COLLABORATOR` olduğunda ya da depo "
        "yöneticisi taramaya `ai-approved` etiketi eklediğinde sunulur._"
        if turkish
        else "_AI explanation is limited to users whose association with this "
        "WorkflowPromptGuard repository is `OWNER`, `MEMBER`, or `COLLABORATOR`, or scans "
        "carrying the maintainer-applied `ai-approved` label._"
    )
    deterministic_notice = (
        "Yukarıdaki bulgular ve başarılı/başarısız değerlendirmesi deterministik tarayıcıdan "
        "gelir. İsteğe bağlı yapay zekâ bölümü yalnızca açıklama amaçlıdır."
        if turkish
        else "The findings and pass/fail interpretation above come from the deterministic "
        "scanner. The optional AI section is explanatory only."
    )
    lines.extend(
        [
            AI_PLACEHOLDER,
            "",
            *([ai_gate_notice, ""] if not ai_allowed else []),
            deterministic_notice,
        ]
    )
    comment = "\n".join(lines).rstrip() + "\n"
    if len(comment) > MAX_COMMENT_LENGTH:
        raise IssueBotError("rendered issue comment exceeded its size limit")
    return comment


def render_request_error(
    language: ReportLanguage = ReportLanguage.ENGLISH,
) -> str:
    """Render usage instructions without reflecting attacker-controlled input."""

    if language is ReportLanguage.TURKISH:
        lines = [
            BOT_MARKER,
            "## WorkflowPromptGuard tarama isteği",
            "",
            "Bu isteği işleyemedim.",
            "",
            "**Herkese açık depoyu tara / Scan a public repository** issue formunu kullanın ve "
            "ayrı bir satıra tam olarak bir URL yazın:",
            "",
            "```text",
            "https://github.com/OWNER/REPOSITORY",
            "```",
            "",
            "Yalnızca herkese açık GitHub depoları desteklenir. Dal, alt dizin, sorgu "
            "parametresi, URL parçası, kimlik bilgisi ve GitHub dışındaki sunucular kabul edilmez.",
            "",
            AI_PLACEHOLDER,
        ]
    else:
        lines = [
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
    return "\n".join(lines) + "\n"


def render_service_error(
    language: ReportLanguage = ReportLanguage.ENGLISH,
) -> str:
    """Render a non-sensitive upstream failure response."""

    if language is ReportLanguage.TURKISH:
        lines = [
            BOT_MARKER,
            "## WorkflowPromptGuard tarama isteği",
            "",
            "Depoya erişilemedi; depo gizli veya çok büyük olabilir ya da güvenli biçimde "
            "okunamamış olabilir.",
            "Hedef depodaki hiçbir kod çalıştırılmadı. Yeniden denemek için issue'yu yeniden "
            "açabilirsiniz.",
            "",
            AI_PLACEHOLDER,
        ]
    else:
        lines = [
            BOT_MARKER,
            "## WorkflowPromptGuard scan request",
            "",
            "The repository was unavailable, private, too large, or could not be read safely.",
            "No target repository code was executed. You can reopen the issue to try again.",
            "",
            AI_PLACEHOLDER,
        ]
    return "\n".join(lines) + "\n"


def build_summary_input(
    snapshot: RemoteSnapshot,
    result: ScanResult,
    *,
    ai_allowed: bool = True,
    language: ReportLanguage = ReportLanguage.ENGLISH,
) -> dict[str, Any]:
    """Create a prompt-safe artifact containing only catalog-backed aggregates."""

    rule_counts = Counter(finding.rule_id for finding in result.findings)
    return {
        "schema_version": 2,
        "enabled": bool(result.scanned_files) and ai_allowed,
        "language": language.value,
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


def _disabled_summary_input(language: ReportLanguage) -> dict[str, Any]:
    return {"schema_version": 2, "enabled": False, "language": language.value}


def prepare_issue_scan(
    event_path: Path,
    output_dir: Path,
    *,
    token: str,
    repository_client: RepositoryFetcher | None = None,
) -> int:
    """Prepare deterministic report artifacts for the read-only scan job."""

    language = _event_report_language(event_path)
    try:
        request = load_issue_request(event_path)
        language = request.language
        client = repository_client
        if client is None:
            client = GitHubRepositoryClient(HTTPSJsonTransport(token))
        snapshot = client.fetch_public_workflows(request.target_repository)
        result = scan_snapshot(snapshot)
        _write_prepare_artifacts(
            output_dir,
            render_scan_comment(
                snapshot,
                result,
                ai_allowed=request.ai_allowed,
                language=request.language,
            ),
            build_summary_input(
                snapshot,
                result,
                ai_allowed=request.ai_allowed,
                language=request.language,
            ),
        )
    except (IssueBotError, RepositoryRequestError):
        _write_prepare_artifacts(
            output_dir,
            render_request_error(language),
            _disabled_summary_input(language),
        )
    except (GitHubServiceError, OSError, UnicodeError):
        _write_prepare_artifacts(
            output_dir,
            render_service_error(language),
            _disabled_summary_input(language),
        )
    return 0


def render_model_summary(
    summary: ModelSummary,
    language: ReportLanguage = ReportLanguage.ENGLISH,
) -> str:
    """Render already validated model text with explicit AI provenance."""

    if language is ReportLanguage.TURKISH:
        lines = [
            "### Yapay zekâ tarafından oluşturulan açıklama",
            "",
            f"_{MODEL_PROVIDER} (`{summary.model}`) üzerinden oluşturuldu; yalnızca "
            "bilgilendirme amaçlıdır._",
            "",
            _safe_markdown(summary.overview),
        ]
        priorities = "**Önerilen öncelikler:**"
    else:
        lines = [
            "### AI-generated explanation",
            "",
            f"_Generated through {MODEL_PROVIDER} (`{summary.model}`); advisory only._",
            "",
            _safe_markdown(summary.overview),
        ]
        priorities = "**Suggested priorities:**"
    if summary.recommendations:
        lines.extend(["", priorities, ""])
        lines.extend(f"- {_safe_markdown(item)}" for item in summary.recommendations)
    return "\n".join(lines).rstrip() + "\n"


def render_model_fallback(
    language: ReportLanguage = ReportLanguage.ENGLISH,
) -> str:
    """Explain that quota or service failure did not affect the deterministic scan."""

    if language is ReportLanguage.TURKISH:
        lines = [
            "### Yapay zekâ tarafından oluşturulan açıklama",
            "",
            f"_{MODEL_PROVIDER} kullanılamadı veya hız sınırına takıldı. "
            "Deterministik tarama raporu "
            "eksiksizdir ve esas alınması gereken kaynak olmaya devam eder._",
        ]
    else:
        lines = [
            "### AI-generated explanation",
            "",
            f"_{MODEL_PROVIDER} was unavailable or rate-limited. The deterministic scan report is "
            "complete and remains the source of truth._",
        ]
    return "\n".join(lines) + "\n"


def _artifact_report_language(value: Any) -> ReportLanguage:
    if isinstance(value, dict) and value.get("language") == ReportLanguage.TURKISH.value:
        return ReportLanguage.TURKISH
    return ReportLanguage.ENGLISH


def summarize_artifact(
    input_path: Path,
    output_path: Path,
    *,
    model_client: SummaryClient | None = None,
) -> int:
    """Generate a bounded model summary or a deterministic fallback artifact."""

    language = ReportLanguage.ENGLISH
    try:
        value = _load_bounded_json(input_path, maximum=MAX_ARTIFACT_BYTES)
        language = _artifact_report_language(value)
        if isinstance(value, dict) and value.get("enabled") is False:
            summary_text = ""
        else:
            client = model_client
            if client is None:
                client = CloudModelsClient(AnonymousLLM7Transport())
            summary_text = render_model_summary(client.summarize(value), language)
    except (IssueBotError, ModelSummaryError, GitHubServiceError) as exc:
        if isinstance(exc, ModelSummaryError):
            category = exc.category
        elif isinstance(exc, GitHubServiceError):
            category = "transport"
        else:
            category = "artifact"
        print(f"WorkflowPromptGuard AI fallback: {category}", file=sys.stderr)
        summary_text = render_model_fallback(language)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(summary_text, encoding="utf-8")
    return 0
