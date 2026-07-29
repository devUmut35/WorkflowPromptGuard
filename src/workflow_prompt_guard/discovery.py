"""Deterministic workflow discovery."""

from __future__ import annotations

from collections.abc import Iterable
from fnmatch import fnmatch
from pathlib import Path

SUPPORTED_SUFFIXES = {".yml", ".yaml", ".md"}


def _is_relative_to(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    return True


def _resolve(path: Path) -> Path | None:
    try:
        return path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None


def _escapes_root(path: Path, *, resolved: Path, root: Path) -> bool:
    lexical_path = path.absolute()
    lexical_root = root.absolute()
    resolved_root = _resolve(root)
    return resolved_root is None or (
        _is_relative_to(lexical_path, lexical_root) and not _is_relative_to(resolved, resolved_root)
    )


def _is_generated(path: Path) -> bool:
    name = path.name.casefold()
    return name.endswith((".lock.yml", ".lock.yaml"))


def _excluded(path: Path, *, root: Path, patterns: tuple[str, ...]) -> bool:
    try:
        display = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        display = path.resolve().as_posix()
    return any(fnmatch(display, pattern) for pattern in patterns)


def _candidate_paths(target: Path) -> tuple[Path, tuple[Path, ...]] | None:
    if target.is_file():
        return target, (target,)
    if not target.is_dir():
        return None

    workflow_dir = target / ".github" / "workflows"
    resolved_workflow_dir = _resolve(workflow_dir)
    if workflow_dir.is_dir():
        if resolved_workflow_dir is None or not _is_relative_to(resolved_workflow_dir, target):
            return None
        search_root = resolved_workflow_dir
    else:
        search_root = target

    candidates = tuple(
        path
        for path in search_root.rglob("*")
        if path.is_file() and path.suffix.casefold() in SUPPORTED_SUFFIXES
    )
    return search_root, candidates


def discover_workflows(
    inputs: Iterable[Path],
    *,
    root: Path,
    exclude: tuple[str, ...] = (),
    include_generated: bool = False,
) -> tuple[Path, ...]:
    """Discover supported workflow files beneath explicit files or repositories."""

    found: dict[str, Path] = {}
    for raw_input in inputs:
        target = _resolve(raw_input)
        if target is None or _escapes_root(raw_input, resolved=target, root=root):
            continue

        selection = _candidate_paths(target)
        if selection is None:
            continue
        search_root, candidates = selection

        for candidate in candidates:
            if candidate.suffix.casefold() not in SUPPORTED_SUFFIXES:
                continue
            resolved_candidate = _resolve(candidate)
            if resolved_candidate is None or not _is_relative_to(resolved_candidate, search_root):
                continue
            if candidate.name.casefold() == "readme.md":
                continue
            if _is_generated(candidate) and not include_generated:
                continue
            if _excluded(resolved_candidate, root=root, patterns=exclude):
                continue
            found[resolved_candidate.as_posix().casefold()] = resolved_candidate

    return tuple(found[key] for key in sorted(found))
