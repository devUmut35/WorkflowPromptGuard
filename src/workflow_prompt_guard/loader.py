"""YAML 1.2-compatible workflow loading with source locations."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import yaml
from yaml.nodes import MappingNode, Node, SequenceNode

from workflow_prompt_guard.models import Location, WorkflowDocument, WorkflowKind, YamlPath


class WorkflowParseError(ValueError):
    """A source file could not be parsed as a supported workflow."""

    def __init__(
        self,
        message: str,
        *,
        line: int | None = None,
        column: int | None = None,
    ) -> None:
        super().__init__(message)
        self.line = line
        self.column = column


class Yaml12SafeLoader(yaml.SafeLoader):
    """Safe loader that does not coerce the GitHub Actions key ``on`` to bool."""


Yaml12SafeLoader.yaml_implicit_resolvers = copy.deepcopy(yaml.SafeLoader.yaml_implicit_resolvers)
for first_character, resolvers in tuple(Yaml12SafeLoader.yaml_implicit_resolvers.items()):
    Yaml12SafeLoader.yaml_implicit_resolvers[first_character] = [
        resolver for resolver in resolvers if resolver[0] != "tag:yaml.org,2002:bool"
    ]
Yaml12SafeLoader.add_implicit_resolver(  # type: ignore[no-untyped-call]
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false)$", re.IGNORECASE),
    list("tTfF"),
)


def safe_load_yaml(source: str) -> Any:
    """Load YAML with the restricted YAML 1.2-compatible loader."""

    loader = Yaml12SafeLoader(source)
    try:
        value: Any = loader.get_single_data()
        return value
    finally:
        loader.dispose()  # type: ignore[no-untyped-call]


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _frontmatter(source: str) -> tuple[str, str, int]:
    lines = source.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise WorkflowParseError("agentic workflow markdown must start with YAML frontmatter")

    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            yaml_text = "".join(lines[1:index])
            body = "".join(lines[index + 1 :])
            return yaml_text, body, 1

    raise WorkflowParseError("agentic workflow frontmatter is missing its closing '---'")


def _index_locations(node: Node, *, line_offset: int) -> dict[YamlPath, Location]:
    locations: dict[YamlPath, Location] = {}

    def visit(current: Node, path: YamlPath) -> None:
        locations[path] = Location(
            line=current.start_mark.line + 1 + line_offset,
            column=current.start_mark.column + 1,
        )
        if isinstance(current, MappingNode):
            for key_node, value_node in current.value:
                key = key_node.value
                child_path = (*path, key)
                locations[child_path] = Location(
                    line=key_node.start_mark.line + 1 + line_offset,
                    column=key_node.start_mark.column + 1,
                )
                visit(value_node, child_path)
        elif isinstance(current, SequenceNode):
            for index, child in enumerate(current.value):
                visit(child, (*path, index))

    visit(node, ())
    return locations


def load_workflow(path: Path, *, root: Path) -> WorkflowDocument:
    """Load one YAML or Markdown workflow from disk."""

    source = path.read_text(encoding="utf-8")
    suffix = path.suffix.casefold()
    if suffix == ".md":
        yaml_text, body, line_offset = _frontmatter(source)
        kind = WorkflowKind.AGENTIC
    elif suffix in {".yml", ".yaml"}:
        yaml_text, body, line_offset = source, "", 0
        kind = WorkflowKind.ACTIONS
    else:
        raise WorkflowParseError(f"unsupported workflow extension: {path.suffix}")

    try:
        loaded = safe_load_yaml(yaml_text)
        root_node = yaml.compose(yaml_text, Loader=Yaml12SafeLoader)
    except yaml.MarkedYAMLError as exc:
        mark = exc.problem_mark
        line = mark.line + 1 + line_offset if mark is not None else None
        column = mark.column + 1 if mark is not None else None
        problem = exc.problem or "invalid YAML"
        raise WorkflowParseError(problem, line=line, column=column) from exc
    except yaml.YAMLError as exc:
        raise WorkflowParseError(f"invalid YAML: {exc}") from exc

    if not isinstance(loaded, dict) or root_node is None:
        raise WorkflowParseError("workflow root must be a YAML mapping")

    return WorkflowDocument(
        path=path.resolve(),
        display_path=_display_path(path, root),
        kind=kind,
        data=loaded,
        source=source,
        locations=_index_locations(root_node, line_offset=line_offset),
        body=body,
    )
