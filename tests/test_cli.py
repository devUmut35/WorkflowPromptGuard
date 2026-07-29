"""CLI behavior and policy exit-code tests."""

from __future__ import annotations

import json
from pathlib import Path

from workflow_prompt_guard import cli
from workflow_prompt_guard.cli import main


def test_rules_and_explain_commands(capsys) -> None:
    assert main(["rules"]) == 0
    assert "AI001" in capsys.readouterr().out

    assert main(["explain", "AI001"]) == 0
    assert "write-capable agent" in capsys.readouterr().out

    assert main(["explain", "missing"]) == 2
    assert "unknown rule" in capsys.readouterr().err


def test_rules_json(capsys) -> None:
    assert main(["rules", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["rule_id"] == "AI001"


def test_scan_exit_codes_and_output_file(tmp_path: Path, write_file, monkeypatch, capsys) -> None:
    write_file(
        tmp_path / ".github/workflows/mutable.yml",
        """
        on: push
        jobs:
          test:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/checkout@v4
        """,
    )
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "reports/result.json"

    assert main(["scan", "--format", "json", "--output", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["summary"]["medium"] == 1
    assert main(["scan", "--fail-on", "medium"]) == 1
    assert "GA003" in capsys.readouterr().out


def test_scan_parse_error_and_empty_repository(
    tmp_path: Path, write_file, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["scan"]) == 2
    assert "no supported workflow" in capsys.readouterr().err

    write_file(tmp_path / ".github/workflows/bad.yml", "jobs: [\n")
    assert main(["scan"]) == 2
    assert "PARSE" in capsys.readouterr().out


def test_invalid_config_returns_two(tmp_path: Path, write_file, monkeypatch, capsys) -> None:
    write_file(tmp_path / ".workflow-prompt-guard.yml", "version: 99\n")
    monkeypatch.chdir(tmp_path)

    assert main(["scan"]) == 2
    assert "configuration error" in capsys.readouterr().err


def test_issue_bot_requires_environment_token_and_dispatches(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    event = tmp_path / "event.json"
    output = tmp_path / "output"

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert (
        main(
            [
                "issue-bot",
                "prepare",
                "--event",
                str(event),
                "--output-dir",
                str(output),
            ]
        )
        == 2
    )
    assert "GITHUB_TOKEN is required" in capsys.readouterr().err

    calls: list[tuple[Path, Path, str]] = []

    def fake_prepare(event_path: Path, output_dir: Path, *, token: str) -> int:
        calls.append((event_path, output_dir, token))
        return 0

    monkeypatch.setenv("GITHUB_TOKEN", "short-lived-token")
    monkeypatch.setattr(cli, "prepare_issue_scan", fake_prepare)
    assert (
        main(
            [
                "issue-bot",
                "prepare",
                "--event",
                str(event),
                "--output-dir",
                str(output),
            ]
        )
        == 0
    )
    assert calls == [(event, output, "short-lived-token")]
