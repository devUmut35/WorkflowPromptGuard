"""Deterministic workflow discovery."""

from __future__ import annotations

from collections.abc import Iterable
from fnmatch import fnmatch
from pathlib import Path

SUPPORTED_SUFFIXES = {".yml", ".yaml", ".md"}


def _is_generated(path: Path) -> bool:
    name = path.name.casefold()
    return name.endswith((".lock.yml", ".lock.yaml"))


def _excluded(path: Path, *, root: Path, patterns: tuple[str, ...]) -> bool:
    try:
        display = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        display = path.resolve().as_posix()
    return any(fnmatch(display, pattern) for pattern in patterns)


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
        target = raw_input.resolve()
        candidates: tuple[Path, ...]
        if target.is_file():
            candidates = (target,)
        elif target.is_dir():
            workflow_dir = target / ".github" / "workflows"
            search_root = workflow_dir if workflow_dir.is_dir() else target
            candidates = tuple(
                path
                for path in search_root.rglob("*")
                if path.is_file() and path.suffix.casefold() in SUPPORTED_SUFFIXES
            )
        else:
            continue

        for candidate in candidates:
            if candidate.suffix.casefold() not in SUPPORTED_SUFFIXES:
                continue
            if candidate.name.casefold() == "readme.md":
                continue
            if _is_generated(candidate) and not include_generated:
                continue
            if _excluded(candidate, root=root, patterns=exclude):
                continue
            found[candidate.resolve().as_posix().casefold()] = candidate.resolve()

    return tuple(found[key] for key in sorted(found))
