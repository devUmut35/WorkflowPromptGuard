"""Core immutable models used by the scanner and reporters."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, TypeAlias

YamlPathPart: TypeAlias = str | int
YamlPath: TypeAlias = tuple[YamlPathPart, ...]


class Severity(str, Enum):
    """Finding severity in ascending policy order."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        """Return a stable numeric rank for policy comparisons."""

        return {
            Severity.INFO: 0,
            Severity.LOW: 1,
            Severity.MEDIUM: 2,
            Severity.HIGH: 3,
            Severity.CRITICAL: 4,
        }[self]

    def meets(self, threshold: Severity) -> bool:
        """Return whether this severity reaches a policy threshold."""

        return self.rank >= threshold.rank

    @classmethod
    def parse(cls, value: str) -> Severity:
        """Parse a case-insensitive user-facing severity value."""

        try:
            return cls(value.casefold())
        except ValueError as exc:
            choices = ", ".join(item.value for item in cls)
            raise ValueError(f"unknown severity {value!r}; expected one of: {choices}") from exc


class WorkflowKind(str, Enum):
    """Supported workflow source formats."""

    ACTIONS = "github-actions"
    AGENTIC = "github-agentic-workflow"


@dataclass(frozen=True)
class Location:
    """A one-based source location."""

    line: int
    column: int = 1


@dataclass(frozen=True)
class RuleMetadata:
    """Stable public metadata for one scanner rule."""

    rule_id: str
    title: str
    severity: Severity
    summary: str
    remediation: str
    reference: str


@dataclass(frozen=True)
class Finding:
    """One actionable security finding."""

    rule_id: str
    title: str
    severity: Severity
    message: str
    path: str
    line: int
    column: int
    remediation: str
    reference: str
    trace: tuple[str, ...] = ()
    fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""

        return {
            "rule_id": self.rule_id,
            "title": self.title,
            "severity": self.severity.value,
            "message": self.message,
            "path": self.path,
            "line": self.line,
            "column": self.column,
            "remediation": self.remediation,
            "reference": self.reference,
            "trace": list(self.trace),
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class ScanError:
    """A file-level discovery or parse error."""

    path: str
    message: str
    line: int | None = None
    column: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""

        return {
            "path": self.path,
            "message": self.message,
            "line": self.line,
            "column": self.column,
        }


@dataclass(frozen=True)
class WorkflowDocument:
    """A parsed workflow and its source location index."""

    path: Path
    display_path: str
    kind: WorkflowKind
    data: dict[str, Any]
    source: str
    locations: dict[YamlPath, Location]
    body: str = ""

    def location(self, path: YamlPath = ()) -> Location:
        """Return the nearest indexed location for a YAML path."""

        candidate = path
        while candidate:
            if candidate in self.locations:
                return self.locations[candidate]
            candidate = candidate[:-1]
        return self.locations.get((), Location(1, 1))


@dataclass(frozen=True)
class ScanResult:
    """Complete scanner output before rendering or policy evaluation."""

    scanned_files: tuple[str, ...]
    findings: tuple[Finding, ...]
    errors: tuple[ScanError, ...] = ()
    ignored_findings: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def failing_findings(self, threshold: Severity) -> tuple[Finding, ...]:
        """Return findings that meet or exceed a policy threshold."""

        return tuple(item for item in self.findings if item.severity.meets(threshold))
