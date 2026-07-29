"""Classic GitHub Actions workflow rule tests."""

from __future__ import annotations

from pathlib import Path

from workflow_prompt_guard.loader import load_workflow
from workflow_prompt_guard.models import Severity
from workflow_prompt_guard.rules import evaluate

PIN = "a" * 40


def test_risky_agent_workflow_reports_capability_chain(tmp_path: Path, write_file) -> None:
    path = write_file(
        tmp_path / ".github/workflows/agent.yml",
        """
        name: Unsafe agent
        on:
          issues:
            types: [opened]
        permissions: write-all
        jobs:
          agent:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/checkout@v4
              - id: agent
                uses: anthropics/claude-code-action@v1
                with:
                  prompt: ${{ github.event.issue.body }}
                  token: ${{ secrets.AGENT_TOKEN }}
              - name: Apply output
                run: |
                  gh api repos/${{ github.repository }} \
                    -f body="${{ steps.agent.outputs.result }}"
        """,
    )

    findings = evaluate(load_workflow(path, root=tmp_path))
    ids = [finding.rule_id for finding in findings]

    assert "AI001" in ids
    assert "AI002" in ids
    assert "AI003" in ids
    assert "AI007" in ids
    assert "AI008" in ids
    assert ids.count("GA003") == 2
    assert "GA004" in ids
    assert next(item for item in findings if item.rule_id == "AI001").trace


def test_read_only_bounded_agent_workflow_is_clean(tmp_path: Path, write_file) -> None:
    path = write_file(
        tmp_path / ".github/workflows/safe.yml",
        f"""
        name: Bounded agent
        on: workflow_dispatch
        permissions:
          contents: read
        concurrency:
          group: agent-review
        jobs:
          review:
            runs-on: ubuntu-latest
            timeout-minutes: 10
            steps:
              - uses: openai/codex-action@{PIN}
                with:
                  prompt: Review the checked-out code without making changes.
        """,
    )

    assert evaluate(load_workflow(path, root=tmp_path)) == ()


def test_missing_permissions_is_medium(tmp_path: Path, write_file) -> None:
    path = write_file(
        tmp_path / "agent.yml",
        """
        on: workflow_dispatch
        jobs:
          review:
            runs-on: ubuntu-latest
            steps:
              - run: codex review
        """,
    )

    finding = next(
        item for item in evaluate(load_workflow(path, root=tmp_path)) if item.rule_id == "GA004"
    )

    assert finding.severity is Severity.MEDIUM


def test_pull_request_target_head_checkout_is_critical(tmp_path: Path, write_file) -> None:
    path = write_file(
        tmp_path / "pr.yml",
        f"""
        on: pull_request_target
        permissions:
          contents: read
        concurrency: pr-agent
        jobs:
          test:
            timeout-minutes: 5
            runs-on: ubuntu-latest
            steps:
              - uses: actions/checkout@{PIN}
                with:
                  ref: ${{{{ github.event.pull_request.head.sha }}}}
              - run: codex review
        """,
    )

    findings = evaluate(load_workflow(path, root=tmp_path))

    assert next(item for item in findings if item.rule_id == "GA001").severity is Severity.CRITICAL
    assert any(item.rule_id == "GA002" for item in findings) is False


def test_untrusted_run_expression_is_high(tmp_path: Path, write_file) -> None:
    path = write_file(
        tmp_path / "script.yml",
        """
        on: issues
        jobs:
          echo:
            runs-on: ubuntu-latest
            steps:
              - run: echo "${{ github.event.issue.title }}"
        """,
    )

    findings = evaluate(load_workflow(path, root=tmp_path))

    assert [item.rule_id for item in findings] == ["GA002"]
    assert findings[0].severity is Severity.HIGH
