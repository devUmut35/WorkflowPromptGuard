"""Workflow loader behavior and source-location tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from workflow_prompt_guard.loader import WorkflowParseError, load_workflow, safe_load_yaml
from workflow_prompt_guard.models import WorkflowKind


def test_yaml_12_preserves_on_key_and_plain_words() -> None:
    loaded = safe_load_yaml("on: true\nanswer: no\nenabled: false\n")

    assert loaded == {"on": True, "answer": "no", "enabled": False}


def test_load_actions_workflow_indexes_nested_locations(tmp_path: Path, write_file) -> None:
    workflow = write_file(
        tmp_path / ".github/workflows/ci.yml",
        """
        name: CI
        on: pull_request
        jobs:
          test:
            runs-on: ubuntu-latest
            steps:
              - run: echo ok
        """,
    )

    document = load_workflow(workflow, root=tmp_path)

    assert document.kind is WorkflowKind.ACTIONS
    assert document.data["on"] == "pull_request"
    assert document.display_path == ".github/workflows/ci.yml"
    assert document.location(("jobs", "test", "steps", 0, "run")).line == 7


def test_load_agentic_frontmatter_offsets_line_numbers(tmp_path: Path, write_file) -> None:
    workflow = write_file(
        tmp_path / ".github/workflows/reviewer.md",
        """
        ---
        on:
          issues:
            types: [opened]
        permissions:
          contents: read
        ---
        # Reviewer
        Review the issue.
        """,
    )

    document = load_workflow(workflow, root=tmp_path)

    assert document.kind is WorkflowKind.AGENTIC
    assert document.location(("permissions", "contents")).line == 6
    assert document.body.lstrip().startswith("# Reviewer")


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("# no frontmatter\n", "must start"),
        ("---\non: issues\n", "missing its closing"),
        ("---\n- invalid\n---\n", "root must be"),
    ],
)
def test_invalid_agentic_markdown_raises(
    tmp_path: Path, write_file, content: str, message: str
) -> None:
    path = write_file(tmp_path / "bad.md", content)

    with pytest.raises(WorkflowParseError, match=message):
        load_workflow(path, root=tmp_path)


def test_invalid_yaml_exposes_location(tmp_path: Path, write_file) -> None:
    path = write_file(tmp_path / "bad.yml", "jobs:\n  test: [\n")

    with pytest.raises(WorkflowParseError) as caught:
        load_workflow(path, root=tmp_path)

    assert caught.value.line == 3
    assert caught.value.column == 1


def test_unsupported_extension_raises(tmp_path: Path, write_file) -> None:
    path = write_file(tmp_path / "workflow.txt", "on: push\n")

    with pytest.raises(WorkflowParseError, match="unsupported"):
        load_workflow(path, root=tmp_path)
