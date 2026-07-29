"""End-to-end engine and reporter tests."""

from __future__ import annotations

import json
from pathlib import Path

from workflow_prompt_guard.config import Config, Ignore
from workflow_prompt_guard.engine import scan
from workflow_prompt_guard.models import Severity
from workflow_prompt_guard.reporters import render


def test_engine_suppresses_findings_and_reports_parse_errors(tmp_path: Path, write_file) -> None:
    write_file(
        tmp_path / ".github/workflows/script.yml",
        """
        on: issues
        jobs:
          test:
            runs-on: ubuntu-latest
            steps:
              - run: echo "${{ github.event.issue.title }}"
        """,
    )
    write_file(tmp_path / ".github/workflows/bad.yml", "jobs:\n  bad: [\n")
    config = Config(
        ignore=(
            Ignore(
                rule="GA002",
                path=".github/workflows/script.yml",
                reason="test suppression",
            ),
        )
    )

    result = scan((tmp_path,), root=tmp_path, config=config)

    assert result.findings == ()
    assert result.ignored_findings == 1
    assert len(result.errors) == 1
    assert result.errors[0].path == ".github/workflows/bad.yml"


def test_all_report_formats_are_structured(tmp_path: Path, write_file) -> None:
    write_file(
        tmp_path / ".github/workflows/script.yml",
        """
        on: issues
        jobs:
          test:
            runs-on: ubuntu-latest
            steps:
              - run: echo "${{ github.event.issue.title }}"
        """,
    )
    result = scan((tmp_path,), root=tmp_path, config=Config())

    console = render(result, "console")
    markdown = render(result, "markdown")
    json_payload = json.loads(render(result, "json"))
    sarif_payload = json.loads(render(result, "sarif"))

    assert "HIGH     GA002" in console
    assert "| **HIGH** | [`GA002`]" in markdown
    assert json_payload["findings"][0]["rule_id"] == "GA002"
    sarif_result = sarif_payload["runs"][0]["results"][0]
    assert sarif_payload["version"] == "2.1.0"
    assert sarif_result["ruleId"] == "GA002"
    assert sarif_result["codeFlows"]


def test_policy_threshold_uses_severity_order(tmp_path: Path, write_file) -> None:
    write_file(
        tmp_path / ".github/workflows/mutable.yml",
        """
        on: push
        jobs:
          test:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/checkout@v4
        """,
    )
    result = scan((tmp_path,), root=tmp_path, config=Config())

    assert not result.failing_findings(Severity.HIGH)
    assert len(result.failing_findings(Severity.MEDIUM)) == 1
