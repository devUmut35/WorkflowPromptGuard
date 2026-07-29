"""GitHub Agentic Workflow rule tests."""

from __future__ import annotations

from pathlib import Path

from workflow_prompt_guard.loader import load_workflow
from workflow_prompt_guard.models import Severity
from workflow_prompt_guard.rules import evaluate


def _ids(path: Path, root: Path) -> list[str]:
    return [finding.rule_id for finding in evaluate(load_workflow(path, root=root))]


def test_hardened_agentic_workflow_is_clean(tmp_path: Path, write_file) -> None:
    path = write_file(
        tmp_path / ".github/workflows/reviewer.md",
        """
        ---
        on:
          issues:
            types: [opened]
        permissions:
          contents: read
          issues: read
        network: defaults
        tools:
          github:
            toolsets: [issues]
        safe-outputs:
          add-comment:
            max: 1
        ---
        # Issue reviewer
        Summarize the issue and propose a helpful response.
        """,
    )

    assert _ids(path, tmp_path) == []


def test_agentic_boundary_failures_are_traced(tmp_path: Path, write_file) -> None:
    path = write_file(
        tmp_path / ".github/workflows/unsafe.md",
        """
        ---
        on:
          issues:
            types: [opened]
          roles: all
        permissions:
          contents: write
        max-ai-credits: -1
        strict: false
        tools:
          bash: [":*"]
          custom:
            env:
              TOKEN: ${{ secrets.DEPLOY_TOKEN }}
        network:
          allowed: ["*"]
          allowed-input: true
        safe-outputs:
          threat-detection: false
          create-pull-request:
            target-repo: "*"
        runtimes:
          node:
            min-integrity: none
        ---
        # Unsafe agent
        Process the issue.
        """,
    )

    findings = evaluate(load_workflow(path, root=tmp_path))
    ids = [finding.rule_id for finding in findings]

    assert "AI001" in ids
    assert "AI002" in ids
    assert ids.count("AI004") == 3
    assert ids.count("AI005") == 3
    assert "AI006" in ids
    assert "AI008" in ids
    boundary = next(finding for finding in findings if finding.rule_id == "AI001")
    assert boundary.severity is Severity.CRITICAL
    assert len(boundary.trace) == 3
    broad_output = next(finding for finding in findings if finding.rule_id == "AI006")
    assert broad_output.severity is Severity.CRITICAL


def test_wildcard_allowed_repo_is_high_not_critical(tmp_path: Path, write_file) -> None:
    path = write_file(
        tmp_path / "cross-repo.md",
        """
        ---
        on: workflow_dispatch
        permissions:
          contents: read
        safe-outputs:
          create-issue:
            allowed-repos: ["my-org/*"]
        ---
        File an issue.
        """,
    )

    finding = next(
        item for item in evaluate(load_workflow(path, root=tmp_path)) if item.rule_id == "AI006"
    )

    assert finding.severity is Severity.HIGH
