"""Deterministic trust-boundary rules for GitHub AI workflows."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Iterator, Mapping
from typing import Any

from workflow_prompt_guard.catalog import get_rule
from workflow_prompt_guard.models import (
    Finding,
    Severity,
    WorkflowDocument,
    WorkflowKind,
    YamlPath,
)

UNTRUSTED_TRIGGERS = {
    "discussion",
    "discussion_comment",
    "issue_comment",
    "issues",
    "pull_request",
    "pull_request_review",
    "pull_request_review_comment",
    "pull_request_target",
}
EXTERNAL_ROLES = {"all", "read", "triage"}
SAFE_AGENT_WRITE_SCOPES = {"copilot-requests", "id-token"}
PROMPT_KEYS = {
    "additional_context",
    "direct_prompt",
    "input",
    "instructions",
    "message",
    "prompt",
    "query",
    "task",
}
KNOWN_AGENT_ACTIONS = (
    "anthropics/claude-code-action",
    "github/copilot",
    "google-github-actions/run-gemini-cli",
    "google-gemini/gemini-cli-action",
    "openai/codex",
)
PRIVILEGED_ACTIONS = (
    "actions/github-script",
    "docker/build-push-action",
    "peter-evans/create-pull-request",
    "softprops/action-gh-release",
)
MUTATING_COMMAND = re.compile(
    r"\b(?:deploy|docker\s+push|gh\s+api|git\s+push|npm\s+publish|release|"
    r"twine\s+upload)\b",
    re.IGNORECASE,
)
AGENT_COMMAND = re.compile(r"(?:^|\s)(?:claude|codex|copilot|gemini)(?:\s|$)|\bgh\s+aw\b", re.I)
SECRET_EXPRESSION = re.compile(r"\$\{\{\s*(?:secrets\.|github\.token\b)", re.IGNORECASE)
UNTRUSTED_EXPRESSION = re.compile(
    r"\$\{\{\s*(?:"
    r"github\.(?:head_ref|ref_name)\b|"
    r"github\.event\.(?:"
    r"comment\.(?:body|user)|"
    r"discussion\.(?:body|title)|"
    r"head_commit\.message|"
    r"issue\.(?:body|title)|"
    r"pull_request\.(?:body|head|title)|"
    r"review\.(?:body)|"
    r"workflow_run\."
    r")|"
    r"inputs\."
    r")",
    re.IGNORECASE,
)
FULL_SHA = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
AGENT_OUTPUT = re.compile(r"\$\{\{\s*steps\.([A-Za-z0-9_-]+)\.outputs\.", re.IGNORECASE)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _iter_nodes(value: Any, path: YamlPath = ()) -> Iterator[tuple[YamlPath, Any]]:
    yield path, value
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield from _iter_nodes(child, (*path, str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_nodes(child, (*path, index))


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for child in value.values():
            yield from _strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)


def _contains(pattern: re.Pattern[str], value: Any) -> bool:
    return any(pattern.search(item) is not None for item in _strings(value))


def _triggers(data: Mapping[str, Any]) -> set[str]:
    value = data.get("on")
    if isinstance(value, str):
        return {value.casefold()}
    if isinstance(value, list):
        return {str(item).casefold() for item in value}
    if isinstance(value, Mapping):
        metadata = {
            "bots",
            "forks",
            "github-app",
            "github-token",
            "manual-approval",
            "needs",
            "permissions",
            "roles",
            "skip-author-associations",
            "skip-bots",
            "skip-if-match",
            "skip-if-no-match",
            "skip-roles",
            "steps",
            "stop-after",
        }
        return {str(key).casefold() for key in value if str(key).casefold() not in metadata}
    return set()


def _write_scopes(value: Any) -> set[str]:
    if isinstance(value, str):
        return {"write-all"} if value.casefold() == "write-all" else set()
    if not isinstance(value, Mapping):
        return set()
    return {
        str(scope)
        for scope, permission in value.items()
        if isinstance(permission, str) and permission.casefold() == "write"
    }


def _finding(  # noqa: PLR0913 - keeps each rule call explicit and readable
    document: WorkflowDocument,
    rule_id: str,
    path: YamlPath,
    message: str,
    *,
    severity: Severity | None = None,
    trace: Iterable[str] = (),
) -> Finding:
    metadata = get_rule(rule_id)
    location = document.location(path)
    resolved_severity = severity or metadata.severity
    fingerprint_source = f"{rule_id}\0{document.display_path}\0{location.line}\0{message}".encode()
    fingerprint = hashlib.sha256(fingerprint_source).hexdigest()[:20]
    return Finding(
        rule_id=rule_id,
        title=metadata.title,
        severity=resolved_severity,
        message=message,
        path=document.display_path,
        line=location.line,
        column=location.column,
        remediation=metadata.remediation,
        reference=metadata.reference,
        trace=tuple(trace),
        fingerprint=fingerprint,
    )


def _is_agent_step(step: Mapping[str, Any]) -> bool:
    uses = str(step.get("uses", "")).casefold()
    if any(marker in uses for marker in KNOWN_AGENT_ACTIONS):
        return True
    run = step.get("run")
    return isinstance(run, str) and AGENT_COMMAND.search(run) is not None


def _is_privileged_step(step: Mapping[str, Any]) -> bool:
    uses = str(step.get("uses", "")).casefold()
    if any(marker in uses for marker in PRIVILEGED_ACTIONS):
        return True
    run = step.get("run")
    return isinstance(run, str) and MUTATING_COMMAND.search(run) is not None


def _mutable_action_ref(uses: str) -> bool:
    normalized = uses.strip()
    if not normalized or normalized.startswith(("./", "docker://")):
        return False
    if "@" not in normalized:
        return True
    reference = normalized.rsplit("@", 1)[1]
    return FULL_SHA.fullmatch(reference) is None


def _roles(on_value: Any) -> set[str]:
    if not isinstance(on_value, Mapping):
        return set()
    value = on_value.get("roles")
    if isinstance(value, str):
        return {value.casefold()}
    if isinstance(value, list):
        return {str(item).casefold() for item in value}
    return set()


def _threat_detection_disabled(safe_outputs: Mapping[str, Any]) -> bool:
    value = safe_outputs.get("threat-detection")
    if value is False:
        return True
    return isinstance(value, Mapping) and value.get("enabled") is False


def _wildcard_repository(value: Any) -> str | None:
    for path, node in _iter_nodes(value):
        if not path:
            continue
        key = str(path[-1])
        if key == "target-repo" and node == "*":
            return "target-repo: *"
        if key == "allowed-repos" and isinstance(node, list):
            for item in node:
                if isinstance(item, str) and "*" in item:
                    return f"allowed-repos: {item}"
    return None


def _agentic_findings(document: WorkflowDocument) -> list[Finding]:
    data = document.data
    findings: list[Finding] = []
    trigger_names = _triggers(data)
    untrusted_trigger = bool(trigger_names & UNTRUSTED_TRIGGERS)
    on_value = data.get("on")
    external_roles = _roles(on_value) & EXTERNAL_ROLES
    permissions = data.get("permissions")
    writes = _write_scopes(permissions) - SAFE_AGENT_WRITE_SCOPES

    tool_sections = {key: data[key] for key in ("mcp-scripts", "tools") if key in data}
    tools_have_secret = _contains(SECRET_EXPRESSION, tool_sections)
    if untrusted_trigger and external_roles and (writes or tools_have_secret):
        capability = (
            f"write scopes: {', '.join(sorted(writes))}" if writes else "secret-bearing agent tools"
        )
        findings.append(
            _finding(
                document,
                "AI001",
                ("on", "roles"),
                f"External roles {sorted(external_roles)} can trigger an agent with {capability}.",
                trace=(
                    f"untrusted trigger: {', '.join(sorted(trigger_names & UNTRUSTED_TRIGGERS))}",
                    f"external roles: {', '.join(sorted(external_roles))}",
                    capability,
                ),
            )
        )

    if tools_have_secret:
        findings.append(
            _finding(
                document,
                "AI002",
                ("tools",) if "tools" in tool_sections else ("mcp-scripts",),
                "A secret expression is exposed through agent tools or MCP scripts.",
                trace=("repository secret", "agent tool environment", "prompt-injectable agent"),
            )
        )

    safe_outputs = _mapping(data.get("safe-outputs"))
    if _threat_detection_disabled(safe_outputs):
        findings.append(
            _finding(
                document,
                "AI004",
                ("safe-outputs", "threat-detection"),
                "Safe-output threat detection is explicitly disabled.",
            )
        )
    if data.get("strict") is False:
        findings.append(
            _finding(
                document,
                "AI004",
                ("strict",),
                "Strict agentic-workflow validation is explicitly disabled.",
            )
        )
    for path, value in _iter_nodes(data):
        if path and str(path[-1]) == "min-integrity" and str(value).casefold() == "none":
            findings.append(
                _finding(
                    document,
                    "AI004",
                    path,
                    "Action integrity enforcement is explicitly disabled.",
                )
            )

    tools = _mapping(data.get("tools"))
    bash_value = tools.get("bash")
    if any(item in {"*", ":*"} for item in _strings(bash_value)):
        findings.append(
            _finding(
                document,
                "AI005",
                ("tools", "bash"),
                "The agent may execute every shell command through a wildcard bash policy.",
                trace=("prompt-injectable agent", "unrestricted shell", "runner workspace"),
            )
        )
    network = _mapping(data.get("network"))
    if network.get("allowed-input") is True:
        findings.append(
            _finding(
                document,
                "AI005",
                ("network", "allowed-input"),
                "Workflow callers may extend the agent network allowlist at runtime.",
                severity=Severity.MEDIUM,
            )
        )
    allowed_network = network.get("allowed")
    if any(item in {"*", "0.0.0.0/0"} for item in _strings(allowed_network)):
        findings.append(
            _finding(
                document,
                "AI005",
                ("network", "allowed"),
                "The agent network policy contains an unrestricted destination.",
            )
        )

    wildcard = _wildcard_repository(safe_outputs)
    if wildcard is not None:
        findings.append(
            _finding(
                document,
                "AI006",
                ("safe-outputs",),
                f"Safe outputs use a wildcard cross-repository target ({wildcard}).",
                severity=Severity.CRITICAL if wildcard == "target-repo: *" else Severity.HIGH,
                trace=("agent output", "safe-output dispatcher", wildcard),
            )
        )

    if untrusted_trigger and external_roles:
        unlimited = data.get("max-ai-credits") == -1
        findings.append(
            _finding(
                document,
                "AI008",
                ("on", "roles"),
                (
                    "External actors can trigger agent runs with no AI credit cap."
                    if unlimited
                    else "External actors can trigger agent runs; review cost and rate limits."
                ),
                severity=Severity.HIGH if unlimited else Severity.MEDIUM,
            )
        )

    return findings


def _agent_jobs(
    data: Mapping[str, Any],
) -> list[tuple[str, Mapping[str, Any], list[tuple[int, Mapping[str, Any]]]]]:
    result: list[tuple[str, Mapping[str, Any], list[tuple[int, Mapping[str, Any]]]]] = []
    for job_id, raw_job in _mapping(data.get("jobs")).items():
        job = _mapping(raw_job)
        raw_steps = job.get("steps")
        if not isinstance(raw_steps, list):
            continue
        agent_steps = [
            (index, _mapping(step))
            for index, step in enumerate(raw_steps)
            if isinstance(step, Mapping) and _is_agent_step(step)
        ]
        if agent_steps:
            result.append((str(job_id), job, agent_steps))
    return result


def _permission_findings(
    document: WorkflowDocument,
    *,
    job_id: str,
    job: Mapping[str, Any],
    top_permissions: Any,
    untrusted_trigger: bool,
) -> tuple[list[Finding], set[str]]:
    findings: list[Finding] = []
    job_path: YamlPath = ("jobs", job_id)
    effective_permissions = job.get("permissions") if "permissions" in job else top_permissions
    writes = _write_scopes(effective_permissions)
    permissions_path = (*job_path, "permissions") if "permissions" in job else ("permissions",)
    if effective_permissions is None:
        findings.append(
            _finding(
                document,
                "GA004",
                job_path,
                "This agent workflow does not declare explicit GITHUB_TOKEN permissions.",
            )
        )
    elif "write-all" in writes:
        findings.append(
            _finding(
                document,
                "GA004",
                permissions_path,
                "This agent job grants write-all GITHUB_TOKEN permissions.",
                severity=Severity.HIGH,
            )
        )
    elif writes:
        findings.append(
            _finding(
                document,
                "GA004",
                permissions_path,
                f"This agent job has direct write scopes: {', '.join(sorted(writes))}.",
                severity=Severity.HIGH if untrusted_trigger else Severity.MEDIUM,
            )
        )
    return findings, writes


def _agent_step_findings(
    document: WorkflowDocument,
    *,
    job_id: str,
    job: Mapping[str, Any],
    agent_steps: list[tuple[int, Mapping[str, Any]]],
    writes: set[str],
) -> list[Finding]:
    findings: list[Finding] = []
    job_path: YamlPath = ("jobs", job_id)
    steps = job.get("steps")
    assert isinstance(steps, list)
    for step_index, step in agent_steps:
        step_path: YamlPath = (*job_path, "steps", step_index)
        prompt_value = {
            key: value
            for key, value in _mapping(step.get("with")).items()
            if str(key).casefold() in PROMPT_KEYS
        }
        has_untrusted_prompt = _contains(UNTRUSTED_EXPRESSION, prompt_value)
        has_secret = _contains(
            SECRET_EXPRESSION,
            {"with": step.get("with"), "env": step.get("env"), "job_env": job.get("env")},
        )

        if has_untrusted_prompt and writes:
            findings.append(
                _finding(
                    document,
                    "AI001",
                    (*step_path, "with"),
                    "Attacker-controlled event content reaches an agent with direct "
                    f"write scopes: {', '.join(sorted(writes))}.",
                    trace=(
                        "untrusted GitHub event content",
                        f"agent step: {step.get('name', step.get('uses', step_index))}",
                        f"GITHUB_TOKEN: {', '.join(sorted(writes))}",
                    ),
                )
            )
        if has_secret:
            findings.append(
                _finding(
                    document,
                    "AI002",
                    (*step_path, "env") if "env" in step else (*step_path, "with"),
                    "A secret or GitHub token expression is available to the agent step.",
                    trace=("repository credential", "agent process environment", "agent tools"),
                )
            )

        agent_id = step.get("id")
        for later_index, raw_later_step in enumerate(steps[step_index + 1 :], step_index + 1):
            later_step = _mapping(raw_later_step)
            later_path: YamlPath = (*job_path, "steps", later_index)
            if isinstance(agent_id, str):
                output_refs = {
                    match.casefold()
                    for value in _strings(later_step)
                    for match in AGENT_OUTPUT.findall(value)
                }
                if agent_id.casefold() in output_refs and (
                    "run" in later_step or _is_privileged_step(later_step)
                ):
                    findings.append(
                        _finding(
                            document,
                            "AI003",
                            (*later_path, "run") if "run" in later_step else later_path,
                            f"Output from agent step {agent_id!r} reaches an executable or "
                            "privileged step.",
                            trace=(
                                f"agent output: steps.{agent_id}.outputs",
                                f"consumer step: {later_step.get('name', later_index)}",
                                "executable or privileged sink",
                            ),
                        )
                    )
            if _is_privileged_step(later_step):
                findings.append(
                    _finding(
                        document,
                        "AI007",
                        later_path,
                        "A privileged operation runs later in the same mutable job as the agent.",
                        trace=(
                            f"agent step: {step.get('name', step_index)}",
                            "shared runner workspace",
                            f"privileged step: {later_step.get('name', later_index)}",
                        ),
                    )
                )
    return findings


def _pull_request_target_findings(
    document: WorkflowDocument, jobs: Mapping[str, Any]
) -> list[Finding]:
    findings: list[Finding] = []
    for job_id, raw_job in jobs.items():
        job = _mapping(raw_job)
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        for index, raw_step in enumerate(steps):
            step = _mapping(raw_step)
            uses = str(step.get("uses", "")).casefold()
            ref_value = _mapping(step.get("with")).get("ref")
            pr_head_checkout = uses.startswith("actions/checkout@") and _contains(
                UNTRUSTED_EXPRESSION, ref_value
            )
            executes_later = any(
                "run" in _mapping(candidate) or _is_agent_step(_mapping(candidate))
                for candidate in steps[index + 1 :]
            )
            if pr_head_checkout and executes_later:
                findings.append(
                    _finding(
                        document,
                        "GA001",
                        ("jobs", str(job_id), "steps", index, "with", "ref"),
                        "pull_request_target checks out the pull-request head before executing "
                        "later code.",
                        trace=(
                            "pull_request_target write-capable context",
                            "attacker-controlled PR checkout",
                            "later code execution",
                        ),
                    )
                )
    return findings


def _classic_findings(document: WorkflowDocument) -> list[Finding]:
    data = document.data
    findings: list[Finding] = []
    trigger_names = _triggers(data)
    untrusted_trigger = bool(trigger_names & UNTRUSTED_TRIGGERS)
    jobs = _mapping(data.get("jobs"))
    top_permissions = data.get("permissions")

    for job_id, job, agent_steps in _agent_jobs(data):
        permission_findings, writes = _permission_findings(
            document,
            job_id=job_id,
            job=job,
            top_permissions=top_permissions,
            untrusted_trigger=untrusted_trigger,
        )
        findings.extend(permission_findings)
        findings.extend(
            _agent_step_findings(
                document,
                job_id=job_id,
                job=job,
                agent_steps=agent_steps,
                writes=writes,
            )
        )
        if untrusted_trigger and "timeout-minutes" not in job and "concurrency" not in data:
            findings.append(
                _finding(
                    document,
                    "AI008",
                    ("jobs", job_id),
                    "An externally triggered agent job has neither timeout-minutes nor workflow "
                    "concurrency controls.",
                )
            )

    if "pull_request_target" in trigger_names:
        findings.extend(_pull_request_target_findings(document, jobs))

    return findings


def _general_actions_findings(document: WorkflowDocument) -> list[Finding]:
    findings: list[Finding] = []
    for path, value in _iter_nodes(document.data):
        if (
            path
            and path[-1] == "run"
            and isinstance(value, str)
            and UNTRUSTED_EXPRESSION.search(value)
        ):
            findings.append(
                _finding(
                    document,
                    "GA002",
                    path,
                    "A github.event, input, branch, or ref expression is expanded directly "
                    "inside a run script.",
                    trace=("untrusted GitHub context", "Actions expression expansion", "shell"),
                )
            )
        if path and path[-1] == "uses" and isinstance(value, str) and _mutable_action_ref(value):
            severity = (
                Severity.HIGH
                if any(marker in value.casefold() for marker in KNOWN_AGENT_ACTIONS)
                else Severity.MEDIUM
            )
            findings.append(
                _finding(
                    document,
                    "GA003",
                    path,
                    f"External dependency {value!r} is not pinned to a full commit SHA.",
                    severity=severity,
                )
            )
    return findings


def evaluate(document: WorkflowDocument) -> tuple[Finding, ...]:
    """Evaluate every applicable rule against one parsed workflow."""

    findings = _general_actions_findings(document)
    if document.kind is WorkflowKind.AGENTIC:
        findings.extend(_agentic_findings(document))
    else:
        findings.extend(_classic_findings(document))
    return tuple(findings)
