"""Strict scanner configuration and finding suppressions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import yaml

from workflow_prompt_guard.loader import safe_load_yaml
from workflow_prompt_guard.models import Finding, Severity

CONFIG_NAMES = (".workflow-prompt-guard.yml", ".workflow-prompt-guard.yaml")


class ConfigError(ValueError):
    """Configuration is missing required values or has an invalid schema."""


@dataclass(frozen=True)
class Ignore:
    """A documented, optionally expiring finding suppression."""

    rule: str
    path: str
    reason: str
    expires: date | None = None

    def matches(self, finding: Finding, *, today: date | None = None) -> bool:
        """Return whether this active suppression matches a finding."""

        current_date = today or date.today()
        if self.expires is not None and self.expires < current_date:
            return False
        return self.rule == finding.rule_id and fnmatch(finding.path, self.path)


@dataclass(frozen=True)
class Config:
    """Scanner policy loaded from one optional YAML file."""

    fail_on: Severity = Severity.HIGH
    exclude: tuple[str, ...] = ()
    ignore: tuple[Ignore, ...] = ()
    include_generated: bool = False

    def is_ignored(self, finding: Finding) -> bool:
        """Return whether any active suppression matches a finding."""

        return any(item.matches(finding) for item in self.ignore)


def _expect_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be a mapping")
    return value


def _expect_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{label} must be a non-empty string")
    return value.strip()


def _parse_ignore(value: Any, index: int) -> Ignore:
    item = _expect_mapping(value, f"ignore[{index}]")
    allowed = {"rule", "path", "reason", "expires"}
    unknown = set(item) - allowed
    if unknown:
        raise ConfigError(f"ignore[{index}] has unknown keys: {', '.join(sorted(unknown))}")

    expires_value = item.get("expires")
    expires: date | None = None
    if expires_value is not None:
        if isinstance(expires_value, date):
            expires = expires_value
        elif isinstance(expires_value, str):
            try:
                expires = date.fromisoformat(expires_value)
            except ValueError as exc:
                raise ConfigError(f"ignore[{index}].expires must use YYYY-MM-DD") from exc
        else:
            raise ConfigError(f"ignore[{index}].expires must use YYYY-MM-DD")

    return Ignore(
        rule=_expect_string(item.get("rule"), f"ignore[{index}].rule"),
        path=_expect_string(item.get("path", "*"), f"ignore[{index}].path"),
        reason=_expect_string(item.get("reason"), f"ignore[{index}].reason"),
        expires=expires,
    )


def discover_config(root: Path) -> Path | None:
    """Return the first conventional configuration path, if present."""

    for name in CONFIG_NAMES:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def load_config(path: Path | None) -> Config:
    """Load a strict versioned configuration file."""

    if path is None:
        return Config()
    try:
        raw = safe_load_yaml(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot load {path}: {exc}") from exc

    data = _expect_mapping(raw, "configuration")
    allowed = {"version", "fail_on", "exclude", "ignore", "include_generated"}
    unknown = set(data) - allowed
    if unknown:
        raise ConfigError(f"unknown configuration keys: {', '.join(sorted(unknown))}")
    if data.get("version") != 1:
        raise ConfigError("configuration version must be 1")

    try:
        fail_on = Severity.parse(str(data.get("fail_on", "high")))
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc

    exclude_value = data.get("exclude", [])
    if not isinstance(exclude_value, list) or not all(
        isinstance(item, str) and item for item in exclude_value
    ):
        raise ConfigError("exclude must be a list of non-empty glob strings")

    ignore_value = data.get("ignore", [])
    if not isinstance(ignore_value, list):
        raise ConfigError("ignore must be a list")

    include_generated = data.get("include_generated", False)
    if not isinstance(include_generated, bool):
        raise ConfigError("include_generated must be a boolean")

    return Config(
        fail_on=fail_on,
        exclude=tuple(exclude_value),
        ignore=tuple(_parse_ignore(item, index) for index, item in enumerate(ignore_value)),
        include_generated=include_generated,
    )
