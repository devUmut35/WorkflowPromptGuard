"""Workflow loader behavior and source-location tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from workflow_prompt_guard import loader as loader_module
from workflow_prompt_guard.loader import WorkflowParseError, load_workflow, safe_load_yaml
from workflow_prompt_guard.models import WorkflowKind


def _symlink_or_skip(link: Path, target: Path, *, directory: bool = False) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")


def test_yaml_12_preserves_on_key_and_plain_words() -> None:
    loaded = safe_load_yaml("on: true\nanswer: no\nenabled: false\n")

    assert loaded == {"on": True, "answer": "no", "enabled": False}


def test_safe_yaml_preserves_bounded_anchors() -> None:
    loaded = safe_load_yaml("defaults: &defaults\n  runs-on: ubuntu-latest\nbuild: *defaults\n")

    assert loaded == {
        "defaults": {"runs-on": "ubuntu-latest"},
        "build": {"runs-on": "ubuntu-latest"},
    }
    assert loaded["build"] is loaded["defaults"]


def test_safe_yaml_rejects_recursive_aliases() -> None:
    with pytest.raises(yaml.YAMLError, match="recursive YAML aliases"):
        safe_load_yaml("value: &value [*value]\n")


def test_safe_yaml_rejects_excessive_depth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loader_module, "MAX_YAML_DEPTH", 3)

    with pytest.raises(yaml.YAMLError, match="nesting exceeds"):
        safe_load_yaml("value: [[[]]]\n")


def test_safe_yaml_rejects_excessive_source_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loader_module, "MAX_YAML_NODES", 4)

    with pytest.raises(yaml.YAMLError, match="nodes exceed"):
        safe_load_yaml("items: [one, two, three]\n")


def test_safe_yaml_rejects_excessive_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loader_module, "MAX_YAML_ALIASES", 1)

    with pytest.raises(yaml.YAMLError, match="aliases exceed"):
        safe_load_yaml("base: &base value\none: *base\ntwo: *base\n")


def test_safe_yaml_rejects_excessive_alias_expansion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loader_module, "MAX_YAML_NODES", 30)
    source = "\n".join(
        (
            "a: &a [one, two]",
            "b: &b [*a, *a]",
            "c: &c [*b, *b]",
            "d: &d [*c, *c]",
        )
    )

    with pytest.raises(yaml.YAMLError, match="expanded YAML nodes exceed"):
        safe_load_yaml(source)


def test_safe_yaml_counts_utf8_source_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loader_module, "MAX_SOURCE_BYTES", 5)

    with pytest.raises(yaml.YAMLError, match="source exceeds"):
        safe_load_yaml("ééé")


def test_safe_yaml_converts_parser_recursion_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_validation(_source: str) -> None:
        raise RecursionError

    monkeypatch.setattr(loader_module, "_validate_yaml_events", fail_validation)

    with pytest.raises(yaml.YAMLError, match="parser limits"):
        safe_load_yaml("jobs: {}\n")


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


def test_load_workflow_rejects_source_over_byte_limit(
    tmp_path: Path,
    write_file,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loader_module, "MAX_SOURCE_BYTES", 16)
    path = write_file(tmp_path / "large.yml", "name: deliberately too large\n")

    with pytest.raises(WorkflowParseError, match="source exceeds"):
        load_workflow(path, root=tmp_path)


def test_load_workflow_rejects_symlink_escape(tmp_path: Path, write_file) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    outside = write_file(tmp_path / "outside.yml", "on: push\njobs: {}\n")
    link = root / "escape.yml"
    _symlink_or_skip(link, outside)

    with pytest.raises(WorkflowParseError, match="outside the scan root"):
        load_workflow(link, root=root)
