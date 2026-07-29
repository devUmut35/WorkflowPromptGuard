"""YAML 1.2-compatible workflow loading with source locations."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import yaml
from yaml.events import (
    AliasEvent,
    MappingEndEvent,
    MappingStartEvent,
    ScalarEvent,
    SequenceEndEvent,
    SequenceStartEvent,
)
from yaml.nodes import MappingNode, Node, SequenceNode

from workflow_prompt_guard.models import Location, WorkflowDocument, WorkflowKind, YamlPath

MAX_SOURCE_BYTES = 1024 * 1024
MAX_YAML_DEPTH = 64
MAX_YAML_NODES = 10_000
MAX_YAML_ALIASES = 1_000


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


class _YamlSafetyError(yaml.YAMLError):
    """YAML input exceeded a scanner safety boundary."""


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


def _validate_source_size(source: str) -> None:
    if len(source) > MAX_SOURCE_BYTES or len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise _YamlSafetyError(f"YAML source exceeds the {MAX_SOURCE_BYTES}-byte safety limit")


def _validate_yaml_events(source: str) -> None:
    depth = 0
    node_count = 0
    alias_count = 0
    active_anchors: list[str | None] = []

    for event in yaml.parse(source, Loader=Yaml12SafeLoader):
        if isinstance(event, (MappingStartEvent, SequenceStartEvent)):
            depth += 1
            node_count += 1
            active_anchors.append(event.anchor)
            if depth > MAX_YAML_DEPTH:
                raise _YamlSafetyError(
                    f"YAML nesting exceeds the {MAX_YAML_DEPTH}-level safety limit"
                )
        elif isinstance(event, (MappingEndEvent, SequenceEndEvent)):
            depth -= 1
            active_anchors.pop()
        elif isinstance(event, ScalarEvent):
            node_count += 1
        elif isinstance(event, AliasEvent):
            node_count += 1
            alias_count += 1
            if event.anchor in active_anchors:
                raise _YamlSafetyError("recursive YAML aliases are not supported")
            if alias_count > MAX_YAML_ALIASES:
                raise _YamlSafetyError(
                    f"YAML aliases exceed the {MAX_YAML_ALIASES}-alias safety limit"
                )

        if node_count > MAX_YAML_NODES:
            raise _YamlSafetyError(f"YAML nodes exceed the {MAX_YAML_NODES}-node safety limit")


def _node_children(node: Node) -> tuple[Node, ...]:
    if isinstance(node, MappingNode):
        return tuple(child for pair in node.value for child in pair)
    if isinstance(node, SequenceNode):
        return tuple(node.value)
    return ()


def _validate_node_graph(root_node: Node | None) -> None:
    if root_node is None:
        return

    expanded_nodes = 0
    active: set[int] = set()
    stack: list[tuple[Node, int, bool]] = [(root_node, 1, False)]
    while stack:
        node, depth, exiting = stack.pop()
        identity = id(node)
        if exiting:
            active.remove(identity)
            continue

        expanded_nodes += 1
        if expanded_nodes > MAX_YAML_NODES:
            raise _YamlSafetyError(
                f"expanded YAML nodes exceed the {MAX_YAML_NODES}-node safety limit"
            )
        if depth > MAX_YAML_DEPTH:
            raise _YamlSafetyError(
                f"expanded YAML nesting exceeds the {MAX_YAML_DEPTH}-level safety limit"
            )
        if identity in active:
            raise _YamlSafetyError("recursive YAML aliases are not supported")

        children = _node_children(node)
        if children:
            active.add(identity)
            stack.append((node, depth, True))
            stack.extend((child, depth + 1, False) for child in reversed(children))


def _load_yaml_with_node(source: str) -> tuple[Any, Node | None]:
    _validate_source_size(source)
    try:
        _validate_yaml_events(source)
        loader = Yaml12SafeLoader(source)
        try:
            root_node = loader.get_single_node()
            _validate_node_graph(root_node)
            if root_node is None:
                return None, None
            value: Any = loader.construct_document(root_node)  # type: ignore[no-untyped-call]
            return value, root_node
        finally:
            loader.dispose()  # type: ignore[no-untyped-call]
    except RecursionError as exc:
        raise _YamlSafetyError("YAML nesting exceeds safe parser limits") from exc


def safe_load_yaml(source: str) -> Any:
    """Load bounded YAML with the restricted YAML 1.2-compatible loader."""

    value, _ = _load_yaml_with_node(source)
    return value


def _is_relative_to(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    return True


def _resolve_workflow_path(path: Path, *, root: Path) -> Path:
    lexical_path = path.absolute()
    lexical_root = root.absolute()
    resolved_path = path.resolve(strict=True)
    resolved_root = root.resolve(strict=True)
    if _is_relative_to(lexical_path, lexical_root) and not _is_relative_to(
        resolved_path, resolved_root
    ):
        raise WorkflowParseError("workflow path resolves outside the scan root")
    return resolved_path


def _read_source(path: Path) -> str:
    with path.open("rb") as handle:
        payload = handle.read(MAX_SOURCE_BYTES + 1)
    if len(payload) > MAX_SOURCE_BYTES:
        raise WorkflowParseError(
            f"workflow source exceeds the {MAX_SOURCE_BYTES}-byte safety limit"
        )
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkflowParseError("workflow source must be valid UTF-8") from exc


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

    resolved_path = _resolve_workflow_path(path, root=root)
    suffix = resolved_path.suffix.casefold()
    if suffix == ".md":
        source = _read_source(resolved_path)
        yaml_text, body, line_offset = _frontmatter(source)
        kind = WorkflowKind.AGENTIC
    elif suffix in {".yml", ".yaml"}:
        source = _read_source(resolved_path)
        yaml_text, body, line_offset = source, "", 0
        kind = WorkflowKind.ACTIONS
    else:
        raise WorkflowParseError(f"unsupported workflow extension: {resolved_path.suffix}")

    try:
        loaded, root_node = _load_yaml_with_node(yaml_text)
    except yaml.MarkedYAMLError as exc:
        mark = exc.problem_mark
        line = mark.line + 1 + line_offset if mark is not None else None
        column = mark.column + 1 if mark is not None else None
        problem = exc.problem or "invalid YAML"
        raise WorkflowParseError(problem, line=line, column=column) from exc
    except yaml.YAMLError as exc:
        raise WorkflowParseError(f"invalid YAML: {exc}") from exc
    except RecursionError as exc:
        raise WorkflowParseError("YAML nesting exceeds safe parser limits") from exc

    if not isinstance(loaded, dict) or root_node is None:
        raise WorkflowParseError("workflow root must be a YAML mapping")

    return WorkflowDocument(
        path=resolved_path,
        display_path=_display_path(resolved_path, root),
        kind=kind,
        data=loaded,
        source=source,
        locations=_index_locations(root_node, line_offset=line_offset),
        body=body,
    )
