"""Strict configuration and suppression tests."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from workflow_prompt_guard.config import ConfigError, Ignore, discover_config, load_config
from workflow_prompt_guard.models import Finding, Severity


def _finding() -> Finding:
    return Finding(
        rule_id="AI002",
        title="Secret",
        severity=Severity.CRITICAL,
        message="secret",
        path=".github/workflows/agent.yml",
        line=12,
        column=3,
        remediation="isolate",
        reference="https://example.test",
    )


def test_default_config() -> None:
    config = load_config(None)

    assert config.fail_on is Severity.HIGH
    assert not config.include_generated
    assert not config.is_ignored(_finding())


def test_load_config_and_active_suppression(tmp_path: Path, write_file) -> None:
    path = write_file(
        tmp_path / ".workflow-prompt-guard.yml",
        """
        version: 1
        fail_on: critical
        include_generated: true
        exclude:
          - vendor/**
        ignore:
          - rule: AI002
            path: .github/workflows/*.yml
            reason: Isolated by a reviewed provider proxy.
            expires: 2099-01-01
        """,
    )

    config = load_config(path)

    assert config.fail_on is Severity.CRITICAL
    assert config.include_generated
    assert config.exclude == ("vendor/**",)
    assert config.is_ignored(_finding())
    assert discover_config(tmp_path) == path


def test_expired_suppression_does_not_match() -> None:
    suppression = Ignore(
        rule="AI002",
        path="*",
        reason="temporary",
        expires=date(2020, 1, 1),
    )

    assert not suppression.matches(_finding(), today=date(2020, 1, 2))


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("version: 2\n", "version must be 1"),
        ("version: 1\nunknown: true\n", "unknown configuration"),
        ("version: 1\nexclude: nope\n", "exclude must be"),
        ("version: 1\nignore: nope\n", "ignore must be"),
        ("version: 1\ninclude_generated: yes\n", "include_generated must be"),
        (
            "version: 1\nignore:\n  - rule: AI002\n    path: '*'\n",
            "reason",
        ),
        (
            "version: 1\nignore:\n  - rule: AI002\n    reason: why\n    expires: tomorrow\n",
            "YYYY-MM-DD",
        ),
    ],
)
def test_invalid_config_is_rejected(tmp_path: Path, write_file, content: str, message: str) -> None:
    path = write_file(tmp_path / "config.yml", content)

    with pytest.raises(ConfigError, match=message):
        load_config(path)
