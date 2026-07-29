"""End-to-end tests for issue scan preparation and AI artifact handling."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from workflow_prompt_guard.github_models import ModelSummary, ModelSummaryError
from workflow_prompt_guard.issue_bot import (
    AI_PLACEHOLDER,
    BOT_MARKER,
    build_summary_input,
    load_issue_request,
    prepare_issue_scan,
    render_model_summary,
    render_scan_comment,
    scan_snapshot,
    summarize_artifact,
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


def test_external_request_scans_but_does_not_consume_model_quota(tmp_path: Path) -> None:
    payload = event_payload("https://github.com/octo-org/example")
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
    assert ai_input["enabled"] is False


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
                "schema_version": 1,
                "enabled": True,
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
            model="openai/gpt-4.1-mini",
        )
    )

    assert summarize_artifact(input_path, output, token="token", model_client=client) == 0
    assert "AI-generated explanation" in output.read_text(encoding="utf-8")
    assert "advisory only" in output.read_text(encoding="utf-8")

    input_path.write_text('{"schema_version": 1, "enabled": false}', encoding="utf-8")
    summarize_artifact(input_path, output, token="token", model_client=client)
    assert output.read_text(encoding="utf-8") == ""

    input_path.write_text(json.dumps(client.inputs[0]), encoding="utf-8")
    failing = FakeSummaryClient(ModelSummaryError("quota details"))
    summarize_artifact(input_path, output, token="token", model_client=failing)
    fallback = output.read_text(encoding="utf-8")
    assert "rate-limited" in fallback
    assert "quota details" not in fallback


def test_render_model_summary_neutralizes_mentions() -> None:
    rendered = render_model_summary(
        ModelSummary(
            overview="@octocat <script>",
            recommendations=("Do `this`.",),
            model="openai/gpt-4.1-mini",
        )
    )

    assert "@octocat" not in rendered
    assert "<script>" not in rendered


def test_render_comment_handles_empty_clean_truncated_and_parse_results() -> None:
    snapshot = vulnerable_snapshot()
    empty = ScanResult(scanned_files=(), findings=())
    assert "No supported workflow files" in render_scan_comment(snapshot, empty)

    clean = ScanResult(scanned_files=(".github/workflows/ci.yml",), findings=())
    assert "No findings reached" in render_scan_comment(snapshot, clean)

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
