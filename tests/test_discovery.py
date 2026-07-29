"""Workflow discovery tests."""

from __future__ import annotations

from pathlib import Path

from workflow_prompt_guard.discovery import discover_workflows


def test_repository_discovery_prefers_workflow_directory_and_skips_generated(
    tmp_path: Path, write_file
) -> None:
    source = write_file(tmp_path / ".github/workflows/agent.md", "---\non: push\n---\n")
    write_file(tmp_path / ".github/workflows/agent.lock.yml", "on: push\njobs: {}\n")
    write_file(tmp_path / ".github/workflows/README.md", "# workflows\n")
    write_file(tmp_path / "elsewhere.yml", "on: push\n")

    discovered = discover_workflows((tmp_path,), root=tmp_path)

    assert discovered == (source.resolve(),)


def test_discovery_can_include_generated_and_apply_excludes(tmp_path: Path, write_file) -> None:
    write_file(tmp_path / ".github/workflows/a.yml", "on: push\njobs: {}\n")
    lock = write_file(tmp_path / ".github/workflows/b.lock.yml", "on: push\njobs: {}\n")

    discovered = discover_workflows(
        (tmp_path,),
        root=tmp_path,
        include_generated=True,
        exclude=(".github/workflows/a.yml",),
    )

    assert discovered == (lock.resolve(),)


def test_explicit_file_and_missing_path(tmp_path: Path, write_file) -> None:
    source = write_file(tmp_path / "custom.yaml", "on: push\njobs: {}\n")

    discovered = discover_workflows(
        (source, tmp_path / "missing"),
        root=tmp_path,
    )

    assert discovered == (source.resolve(),)
