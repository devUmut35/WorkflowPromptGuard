"""Workflow discovery tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from workflow_prompt_guard.discovery import discover_workflows


def _symlink_or_skip(link: Path, target: Path, *, directory: bool = False) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")


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


def test_discovery_skips_file_symlink_outside_input(tmp_path: Path, write_file) -> None:
    repository = tmp_path / "repository"
    safe = write_file(
        repository / ".github/workflows/safe.yml",
        "on: push\njobs: {}\n",
    )
    outside = write_file(tmp_path / "outside.yml", "on: push\njobs: {}\n")
    _symlink_or_skip(repository / ".github/workflows/escape.yml", outside)

    discovered = discover_workflows((repository,), root=repository)

    assert discovered == (safe.resolve(),)


def test_discovery_skips_workflow_directory_symlink_outside_input(
    tmp_path: Path,
    write_file,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    outside = tmp_path / "outside-workflows"
    write_file(outside / "escape.yml", "on: push\njobs: {}\n")
    _symlink_or_skip(
        repository / ".github/workflows",
        outside,
        directory=True,
    )

    discovered = discover_workflows((repository,), root=repository)

    assert discovered == ()


def test_discovery_accepts_and_deduplicates_internal_file_symlink(
    tmp_path: Path,
    write_file,
) -> None:
    repository = tmp_path / "repository"
    source = write_file(
        repository / ".github/workflows/source.yml",
        "on: push\njobs: {}\n",
    )
    _symlink_or_skip(repository / ".github/workflows/alias.yml", source)

    discovered = discover_workflows((repository,), root=repository)

    assert discovered == (source.resolve(),)
