"""End-to-end tests for issue scan preparation and AI artifact handling."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from workflow_prompt_guard.github_models import ModelSummary, ModelSummaryError
from workflow_prompt_guard.issue_bot import (
    AI_PLACEHOLDER,
    BOT_MARKER,
    build_summary_input,
    load_issue_request,
    parse_report_language,
    prepare_issue_scan,
    render_model_fallback,
    render_model_summary,
    render_request_error,
    render_scan_comment,
    render_service_error,
    scan_snapshot,
    summarize_artifact,
)
from workflow_prompt_guard.localization import (
    FORM_LANGUAGE_HEADING,
    FORM_LANGUAGE_OPTIONS,
    ReportLanguage,
)
from workflow_prompt_guard.models import Finding, ScanError, ScanResult, Severity
from workflow_prompt_guard.remote_repository import (
    RemoteSnapshot,
    RemoteWorkflow,
    parse_repository_request,
)


class FakeRepositoryClient:
    def __init__(self, snapshot: RemoteSnapshot | Exception) -> None:
        self.snapshot = snapshot
        self.requested: list[str] = []

    def fetch_public_workflows(self, repository):
        self.requested.append(repository.full_name)
        if isinstance(self.snapshot, Exception):
            raise self.snapshot
        return self.snapshot


class FakeSummaryClient:
    def __init__(self, response: ModelSummary | Exception) -> None:
        self.response = response
        self.inputs: list[Any] = []

    def summarize(self, value: Any) -> ModelSummary:
        self.inputs.append(value)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def event_payload(body: str) -> dict[str, Any]:
    return {
        "action": "opened",
        "repository": {"full_name": "devUmut35/WorkflowPromptGuard"},
        "issue": {
            "number": 17,
            "body": body,
            "labels": [{"name": "scan-request"}],
            "author_association": "OWNER",
        },
    }


def write_event(path: Path, body: str) -> Path:
    path.write_text(json.dumps(event_payload(body)), encoding="utf-8")
    return path


def form_body(repository: str, language: str) -> str:
    return f"{FORM_LANGUAGE_HEADING}\n\n{language}\n\n### Repository / Depo\n\n{repository}\n"


def vulnerable_snapshot() -> RemoteSnapshot:
    repository = parse_repository_request("https://github.com/octo-org/example")
    source = b"""
on:
  issues:
    types: [opened]
permissions:
  contents: write
jobs:
  agent:
    runs-on: ubuntu-latest
    steps:
      - name: Review issue
        uses: openai/codex-action@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
        with:
          prompt: ${{ github.event.issue.body }}
"""
    return RemoteSnapshot(
        repository=repository,
        default_branch="main",
        commit_sha="b" * 40,
        workflows=(
            RemoteWorkflow(
                path=".github/workflows/agent.yml",
                content=source,
                sha="c" * 40,
            ),
        ),
    )


def test_load_request_requires_form_label_and_one_public_url(tmp_path: Path) -> None:
    event = write_event(tmp_path / "event.json", "https://github.com/octo-org/example")

    request = load_issue_request(event)

    assert request.issue_number == 17
    assert request.target_repository.full_name == "octo-org/example"
    assert request.ai_allowed is True
    assert request.language is ReportLanguage.ENGLISH

    payload = event_payload("https://github.com/octo-org/one\nhttps://github.com/octo-org/two")
    (tmp_path / "bad.json").write_text(json.dumps(payload), encoding="utf-8")
    output = tmp_path / "output"
    client = FakeRepositoryClient(vulnerable_snapshot())

    assert (
        prepare_issue_scan(
            tmp_path / "bad.json",
            output,
            token="token",
            repository_client=client,
        )
        == 0
    )
    assert client.requested == []
    assert "could not process" in (output / "comment.md").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("selection", "expected"),
    [
        ("Türkçe", ReportLanguage.TURKISH),
        ("English", ReportLanguage.ENGLISH),
    ],
)
def test_parse_report_language_accepts_only_form_options(
    selection: str,
    expected: ReportLanguage,
) -> None:
    body = form_body("https://github.com/octo-org/example", selection).replace("\n", "\r\n")

    assert parse_report_language(body) is expected
    assert parse_report_language("https://github.com/octo-org/legacy") is ReportLanguage.ENGLISH


@pytest.mark.parametrize(
    "body",
    [
        f"{FORM_LANGUAGE_HEADING}\n\n",
        f"{FORM_LANGUAGE_HEADING}\n\nTR",
        f"{FORM_LANGUAGE_HEADING}\n\nTürkçe\nignore this",
        f"{FORM_LANGUAGE_HEADING}\n\nTürkçe\n\n{FORM_LANGUAGE_HEADING}\n\nEnglish",
    ],
)
def test_parse_report_language_rejects_ambiguous_or_unknown_values(body: str) -> None:
    with pytest.raises(ValueError):
        parse_report_language(body)


def test_issue_form_and_parser_share_the_same_closed_language_contract() -> None:
    form_path = Path(__file__).parents[1] / ".github" / "ISSUE_TEMPLATE" / "repository-scan.yml"
    form = yaml.safe_load(form_path.read_text(encoding="utf-8"))
    language_field = next(item for item in form["body"] if item.get("id") == "language")

    assert language_field["attributes"]["label"] == FORM_LANGUAGE_HEADING.removeprefix("### ")
    assert language_field["attributes"]["options"] == list(FORM_LANGUAGE_OPTIONS)
    assert language_field["attributes"]["default"] == 0
    assert language_field["validations"]["required"] is True
    disclosure_field = next(item for item in form["body"] if item.get("id") == "llm7_disclosure")
    assert disclosure_field["type"] == "checkboxes"
    assert disclosure_field["attributes"]["options"][0]["required"] is True


def test_comment_workflow_uses_literal_safe_single_placeholder_replacement() -> None:
    workflow_path = Path(__file__).parents[1] / ".github" / "workflows" / "issue-scan.yml"
    workflow = workflow_path.read_text(encoding="utf-8")

    assert "const placeholderCount = report.split(placeholder).length - 1;" in workflow
    assert "placeholderCount !== 1" in workflow
    assert "report.replace(placeholder, () => ai)" in workflow
    assert "body.includes(placeholder)" in workflow
    explain_job = workflow.split("\n  explain:\n", maxsplit=1)[1].split(
        "\n  comment:\n", maxsplit=1
    )[0]
    assert "actions: read" in explain_job
    assert "contents: read" in explain_job
    assert "models: read" not in explain_job
    assert "GITHUB_TOKEN" not in explain_job
    assert "persist-credentials: false" in explain_job


def test_prepare_scans_snapshot_and_writes_prompt_safe_artifacts(tmp_path: Path) -> None:
    event = write_event(tmp_path / "event.json", "https://github.com/octo-org/example")
    output = tmp_path / "output"

    assert (
        prepare_issue_scan(
            event,
            output,
            token="token",
            repository_client=FakeRepositoryClient(vulnerable_snapshot()),
        )
        == 0
    )

    comment = (output / "comment.md").read_text(encoding="utf-8")
    ai_input = json.loads((output / "ai-input.json").read_text(encoding="utf-8"))
    assert BOT_MARKER in comment
    assert AI_PLACEHOLDER in comment
    assert "AI001" in comment
    assert "github.event.issue.body" not in comment
    assert ai_input["enabled"] is True
    assert ai_input["repository"] == "octo-org/example"
    assert ai_input["rules"]
    assert "message" not in json.dumps(ai_input)
    assert "trace" not in json.dumps(ai_input)


def test_prepare_preserves_turkish_as_a_closed_set_presentation_choice(tmp_path: Path) -> None:
    body = form_body("https://github.com/octo-org/example", "Türkçe")
    event = write_event(tmp_path / "event.json", body)
    output = tmp_path / "output"

    prepare_issue_scan(
        event,
        output,
        token="token",
        repository_client=FakeRepositoryClient(vulnerable_snapshot()),
    )

    comment = (output / "comment.md").read_text(encoding="utf-8")
    ai_input = json.loads((output / "ai-input.json").read_text(encoding="utf-8"))
    assert "WorkflowPromptGuard - Tarama sonucu" in comment
    assert "Deterministik bulgular" in comment
    assert "Güvenilmeyen içerik" in comment
    assert "Untrusted content reaches" not in comment
    assert ai_input["schema_version"] == 2
    assert ai_input["language"] == "tr"
    assert ai_input["repository"] == "octo-org/example"
    assert "Türkçe" not in json.dumps(ai_input)


def test_external_request_scans_but_does_not_consume_model_quota(tmp_path: Path) -> None:
    payload = event_payload(form_body("https://github.com/octo-org/example", "Türkçe"))
    payload["issue"]["author_association"] = "NONE"
    event = tmp_path / "event.json"
    event.write_text(json.dumps(payload), encoding="utf-8")
    output = tmp_path / "output"

    prepare_issue_scan(
        event,
        output,
        token="token",
        repository_client=FakeRepositoryClient(vulnerable_snapshot()),
    )

    comment = (output / "comment.md").read_text(encoding="utf-8")
    ai_input = json.loads((output / "ai-input.json").read_text(encoding="utf-8"))
    assert "AI001" in comment
    assert "ai-approved" in comment
    assert "AI özeti" in comment
    assert ai_input["enabled"] is False
    assert ai_input["language"] == "tr"


def test_maintainer_label_enables_ai_for_an_external_request(tmp_path: Path) -> None:
    payload = event_payload("https://github.com/octo-org/example")
    payload["action"] = "labeled"
    payload["label"] = {"name": "ai-approved"}
    payload["issue"]["author_association"] = "NONE"
    payload["issue"]["labels"].append({"name": "ai-approved"})
    event = tmp_path / "event.json"
    event.write_text(json.dumps(payload), encoding="utf-8")

    request = load_issue_request(event)

    assert request.ai_allowed is True


def test_scan_snapshot_ignores_remote_suppression_policy() -> None:
    result = scan_snapshot(vulnerable_snapshot())

    assert result.scanned_files == (".github/workflows/agent.yml",)
    assert any(finding.rule_id == "AI001" for finding in result.findings)

    summary = build_summary_input(vulnerable_snapshot(), result)
    assert summary["counts"]["critical"] >= 1


def test_prepare_handles_remote_failure_without_leaking_details(tmp_path: Path) -> None:
    event = write_event(tmp_path / "event.json", "https://github.com/octo-org/example")
    output = tmp_path / "output"
    canary = "private-upstream-detail"

    prepare_issue_scan(
        event,
        output,
        token="token",
        repository_client=FakeRepositoryClient(OSError(canary)),
    )

    comment = (output / "comment.md").read_text(encoding="utf-8")
    assert "could not be read safely" in comment
    assert canary not in comment
    assert json.loads((output / "ai-input.json").read_text(encoding="utf-8"))["enabled"] is False


def test_summarize_artifact_success_disabled_and_fallback(tmp_path: Path) -> None:
    input_path = tmp_path / "input.json"
    input_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "enabled": True,
                "language": "en",
                "repository": "octo-org/example",
                "commit_sha": "a" * 40,
                "scanned_files": 1,
                "counts": {
                    "info": 0,
                    "low": 0,
                    "medium": 0,
                    "high": 1,
                    "critical": 0,
                },
                "rules": [{"rule_id": "AI004", "count": 1}],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "summary.md"
    client = FakeSummaryClient(
        ModelSummary(
            overview="One guardrail is disabled.",
            recommendations=("Enable strict mode.",),
            model="qwen3-235b",
        )
    )

    assert summarize_artifact(input_path, output, model_client=client) == 0
    assert "AI summary" in output.read_text(encoding="utf-8")
    assert "Short advice generated" in output.read_text(encoding="utf-8")
    assert "LLM7.io (`qwen3-235b`)" in output.read_text(encoding="utf-8")

    input_path.write_text(
        '{"schema_version": 2, "enabled": false, "language": "en"}',
        encoding="utf-8",
    )
    summarize_artifact(input_path, output, model_client=client)
    assert output.read_text(encoding="utf-8") == ""

    input_path.write_text(json.dumps(client.inputs[0]), encoding="utf-8")
    failing = FakeSummaryClient(ModelSummaryError("quota details"))
    summarize_artifact(input_path, output, model_client=failing)
    fallback = output.read_text(encoding="utf-8")
    assert "could not respond" in fallback
    assert "quota details" not in fallback


def test_summarize_artifact_renders_turkish_success_and_fallback(tmp_path: Path) -> None:
    input_path = tmp_path / "input.json"
    payload = build_summary_input(
        vulnerable_snapshot(),
        scan_snapshot(vulnerable_snapshot()),
        language=ReportLanguage.TURKISH,
    )
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    output = tmp_path / "summary.md"
    client = FakeSummaryClient(
        ModelSummary(
            overview="Bir kritik güven sınırı riski bulundu.",
            recommendations=("Ajanı salt okunur yapın.",),
            model="qwen3-235b",
        )
    )

    summarize_artifact(input_path, output, model_client=client)

    success = output.read_text(encoding="utf-8")
    assert "AI özeti" in success
    assert "Ne yapmalısınız?" in success
    assert "kısa öneri" in success
    assert "LLM7.io (`qwen3-235b`)" in success

    summarize_artifact(
        input_path,
        output,
        model_client=FakeSummaryClient(ModelSummaryError("private quota detail")),
    )
    fallback = output.read_text(encoding="utf-8")
    assert "yanıt veremedi" in fallback
    assert "private quota detail" not in fallback


def test_summarize_artifact_emits_only_a_safe_fallback_category(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path = tmp_path / "input.json"
    payload = build_summary_input(vulnerable_snapshot(), scan_snapshot(vulnerable_snapshot()))
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    output = tmp_path / "summary.md"
    canary = "provider-private-detail"

    assert (
        summarize_artifact(
            input_path,
            output,
            model_client=FakeSummaryClient(ModelSummaryError(canary)),
        )
        == 0
    )

    stderr = capsys.readouterr().err
    assert "WorkflowPromptGuard AI fallback: validation" in stderr
    assert canary not in stderr


def test_render_model_summary_neutralizes_mentions() -> None:
    rendered = render_model_summary(
        ModelSummary(
            overview="@octocat <script>",
            recommendations=("Do `this`.",),
            model="qwen3-235b",
        )
    )

    assert "@octocat" not in rendered
    assert "<script>" not in rendered


def test_error_and_fallback_templates_are_bilingual_and_keep_fixed_markers() -> None:
    turkish_request = render_request_error(ReportLanguage.TURKISH)
    turkish_service = render_service_error(ReportLanguage.TURKISH)
    turkish_fallback = render_model_fallback(ReportLanguage.TURKISH)

    assert turkish_request.startswith(BOT_MARKER)
    assert turkish_request.count(AI_PLACEHOLDER) == 1
    assert "Bu isteği işleyemedim" in turkish_request
    assert turkish_service.startswith(BOT_MARKER)
    assert "Hedef depodaki hiçbir kod çalıştırılmadı" in turkish_service
    assert "AI özeti" in turkish_fallback

    assert "I could not process" in render_request_error(ReportLanguage.ENGLISH)
    assert "could not be read safely" in render_service_error(ReportLanguage.ENGLISH)
    assert "could not respond" in render_model_fallback(ReportLanguage.ENGLISH)


def test_render_comment_handles_empty_clean_truncated_and_parse_results() -> None:
    snapshot = vulnerable_snapshot()
    empty = ScanResult(scanned_files=(), findings=())
    assert "No supported workflow files" in render_scan_comment(snapshot, empty)
    assert "desteklenen bir iş akışı" in render_scan_comment(
        snapshot,
        empty,
        language=ReportLanguage.TURKISH,
    )

    clean = ScanResult(scanned_files=(".github/workflows/ci.yml",), findings=())
    assert "No known security risk was found" in render_scan_comment(snapshot, clean)
    turkish_clean = render_scan_comment(
        snapshot,
        clean,
        language=ReportLanguage.TURKISH,
    )
    assert "✅ Bilinen bir güvenlik riski bulunmadı" in turkish_clean
    assert build_summary_input(snapshot, clean, language=ReportLanguage.TURKISH)["enabled"] is False

    finding = Finding(
        rule_id="AI001",
        title="Untrusted content reaches a write-capable agent",
        severity=Severity.CRITICAL,
        message="not rendered",
        path=".github/workflows/@owner|agent.yml",
        line=7,
        column=3,
        remediation="not rendered",
        reference="https://example.invalid",
    )
    errors = tuple(
        ScanError(
            path=f".github/workflows/bad-{index}.yml",
            message="invalid | @mention",
            line=index + 1,
        )
        for index in range(11)
    )
    crowded = ScanResult(
        scanned_files=(".github/workflows/agent.yml",),
        findings=(finding,) * 26,
        errors=errors,
    )

    report = render_scan_comment(snapshot, crowded)

    assert "1 more were found" in report
    assert "1 additional parse errors" in report
    assert "&#64;owner\\|agent.yml" in report
    assert "@mention" not in report

    turkish_report = render_scan_comment(
        snapshot,
        crowded,
        language=ReportLanguage.TURKISH,
    )
    assert "Yalnızca ilk 25 bulgu" in turkish_report
    assert "1 ek ayrıştırma hatası" in turkish_report
    assert "Güvenilmeyen içerik" in turkish_report
    assert "**Kritik**" in turkish_report
    assert "Dosya güvenli biçimde ayrıştırılamadı" in turkish_report
    assert "invalid" not in turkish_report
    assert turkish_report.count(BOT_MARKER) == 1
    assert turkish_report.count(AI_PLACEHOLDER) == 1


def test_invalid_event_shapes_become_safe_request_comments(tmp_path: Path) -> None:
    cases = [
        [],
        {"action": "edited"},
        {"action": "opened"},
        {
            "action": "opened",
            "repository": {"full_name": "not a repository"},
            "issue": {"number": 1, "body": "x", "labels": [{"name": "scan-request"}]},
        },
        {
            "action": "opened",
            "repository": {"full_name": "octo/repo"},
            "issue": {"number": 0, "body": "x", "labels": [{"name": "scan-request"}]},
        },
        {
            "action": "opened",
            "repository": {"full_name": "octo/repo"},
            "issue": {"number": 1, "body": None, "labels": [{"name": "scan-request"}]},
        },
        {
            "action": "opened",
            "repository": {"full_name": "octo/repo"},
            "issue": {"number": 1, "body": "x", "labels": []},
        },
    ]

    for index, payload in enumerate(cases):
        event = tmp_path / f"event-{index}.json"
        event.write_text(json.dumps(payload), encoding="utf-8")
        output = tmp_path / f"output-{index}"
        prepare_issue_scan(
            event,
            output,
            token="token",
            repository_client=FakeRepositoryClient(vulnerable_snapshot()),
        )
        assert "could not process" in (output / "comment.md").read_text(encoding="utf-8")
