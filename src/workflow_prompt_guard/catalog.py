"""Stable rule catalog for WorkflowPromptGuard."""

from __future__ import annotations

from workflow_prompt_guard.models import RuleMetadata, Severity

GITHUB_SECURE_USE = "https://docs.github.com/en/actions/reference/security/secure-use"
GITHUB_SCRIPT_INJECTION = "https://docs.github.com/en/actions/concepts/security/script-injections"
GITHUB_PERMISSIONS = (
    "https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#permissions"
)
GH_AW_SECURITY = (
    "https://github.blog/ai-and-ml/generative-ai/"
    "under-the-hood-security-architecture-of-github-agentic-workflows/"
)
GH_AW_THREAT_DETECTION = "https://github.github.com/gh-aw/reference/threat-detection/"
GH_AW_TOOLS = "https://github.github.com/gh-aw/reference/tools/"
GH_AW_SAFE_OUTPUTS = "https://github.github.com/gh-aw/reference/safe-outputs/"
GH_AW_TRIGGERS = "https://github.github.com/gh-aw/reference/triggers/"


def _rule(  # noqa: PLR0913, PLR0917 - mirrors the public RuleMetadata fields
    rule_id: str,
    title: str,
    severity: Severity,
    summary: str,
    remediation: str,
    reference: str,
) -> RuleMetadata:
    return RuleMetadata(rule_id, title, severity, summary, remediation, reference)


RULES: dict[str, RuleMetadata] = {
    "AI001": _rule(
        "AI001",
        "Untrusted content reaches a write-capable agent",
        Severity.CRITICAL,
        "Attacker-controlled repository content can influence an agent that holds direct write "
        "authority.",
        "Keep the agent read-only and apply validated, structured output in a separate "
        "least-privilege job.",
        GH_AW_SECURITY,
    ),
    "AI002": _rule(
        "AI002",
        "Secret exposed to the agent trust domain",
        Severity.CRITICAL,
        "A non-public credential is available to a prompt-injectable agent or one of its tools.",
        "Move credentials into an isolated proxy, GitHub App, or downstream job that the agent "
        "cannot inspect.",
        GH_AW_SECURITY,
    ),
    "AI003": _rule(
        "AI003",
        "Agent output reaches executable code",
        Severity.CRITICAL,
        "Agent-controlled output is interpolated into a shell or another executable sink.",
        "Transfer structured data to a fresh job, validate it against a strict schema, and map "
        "allowlisted values to fixed commands.",
        GH_AW_SECURITY,
    ),
    "AI004": _rule(
        "AI004",
        "Agent guardrail disabled",
        Severity.HIGH,
        "A documented safety or integrity control is explicitly disabled.",
        "Restore strict mode, integrity checks, and threat detection. Document any unavoidable "
        "exception with a narrow, expiring suppression.",
        GH_AW_THREAT_DETECTION,
    ),
    "AI005": _rule(
        "AI005",
        "Agent capability is unrestricted",
        Severity.HIGH,
        "The agent receives broad shell, MCP, repository, or network capability.",
        "Allowlist only the commands, tools, repositories, and network destinations needed for "
        "this workflow.",
        GH_AW_TOOLS,
    ),
    "AI006": _rule(
        "AI006",
        "Safe output scope is too broad",
        Severity.HIGH,
        "An agent can select arbitrary cross-repository write targets.",
        "Use a fixed target or a small explicit repository allowlist, then require threat "
        "detection and environment approval for high-impact writes.",
        GH_AW_SAFE_OUTPUTS,
    ),
    "AI007": _rule(
        "AI007",
        "Agent shares a job with a privileged step",
        Severity.HIGH,
        "A later privileged operation shares the agent's mutable runner and workspace.",
        "Put the privileged operation in a fresh job and pass only validated, immutable data "
        "across the boundary.",
        GH_AW_SECURITY,
    ),
    "AI008": _rule(
        "AI008",
        "Untrusted actors can trigger an unbounded agent run",
        Severity.MEDIUM,
        "External actors can consume agent capacity without an effective budget or execution "
        "limit.",
        "Restrict actor roles and add explicit AI credit, timeout, and concurrency limits.",
        GH_AW_TRIGGERS,
    ),
    "GA001": _rule(
        "GA001",
        "Privileged workflow executes pull-request code",
        Severity.CRITICAL,
        "A pull_request_target workflow fetches attacker-controlled PR code and executes it in "
        "the privileged base-repository context.",
        "Use pull_request for untrusted code. If pull_request_target is required, inspect base "
        "code only and never execute the PR head.",
        GITHUB_SECURE_USE,
    ),
    "GA002": _rule(
        "GA002",
        "Untrusted expression is interpolated into a script",
        Severity.HIGH,
        "A flexible github.event or ref value is expanded directly inside a run block.",
        "Assign the expression to an environment variable and consume the quoted shell variable, "
        "or pass it to a purpose-built action input.",
        GITHUB_SCRIPT_INJECTION,
    ),
    "GA003": _rule(
        "GA003",
        "External action uses a mutable reference",
        Severity.MEDIUM,
        "An external action or reusable workflow is referenced by a mutable tag or branch.",
        "Pin the dependency to its reviewed full 40-character commit SHA and keep a version "
        "comment for maintainability.",
        GITHUB_SECURE_USE,
    ),
    "GA004": _rule(
        "GA004",
        "Agent workflow token permissions are not least privilege",
        Severity.MEDIUM,
        "An agent workflow omits explicit token permissions or grants broad write access.",
        "Declare explicit workflow or job permissions and grant only the read scopes required by "
        "the agent.",
        GITHUB_PERMISSIONS,
    ),
}


def get_rule(rule_id: str) -> RuleMetadata:
    """Return one rule or raise a useful programming error."""

    try:
        return RULES[rule_id]
    except KeyError as exc:
        raise KeyError(f"unknown rule id: {rule_id}") from exc
