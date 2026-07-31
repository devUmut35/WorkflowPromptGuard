# Security policy

## Supported versions

WorkflowPromptGuard is pre-1.0. Only the latest release receives security fixes.

## Reporting a vulnerability

Please do not open a public issue for a vulnerability that could expose repository credentials,
enable command execution, or create a scanner bypass with material impact.

Use GitHub's private vulnerability reporting feature:

1. Open the repository's **Security** tab.
2. Select **Report a vulnerability**.
3. Include the affected version, a minimal workflow fixture, expected and actual behavior, and
   impact.

Do not include real credentials, private repository content, or exploit production systems.

You should receive an acknowledgement within seven days. A fix, advisory, and credit will be
coordinated after validation.

## Scanner scope

The core WorkflowPromptGuard scanner performs static, local file analysis. It does not execute
workflows, resolve remote actions, or test live infrastructure. A clean result is not a security
guarantee.

The hosted issue bot uses the GitHub API to read only supported files directly under a public
repository's `.github/workflows` directory at a pinned commit. It does not clone repositories,
follow symlinks, install target dependencies, or execute target code. Repository size, file count,
individual file size, YAML structure, artifact size, and comment size are bounded.

The optional AI stage sends an anonymous request to the fixed
`https://api.llm7.io/v1/chat/completions` endpoint with the `default` selector. Its payload contains
only normalized `language`, `scanned_files`, `counts`, and catalog-backed `rules` aggregates. It
never contains repository identity, commit SHA, issue body, workflow source, finding paths or
traces, GitHub tokens, provider keys, or write capability. The GitHub token used by other jobs is
not sent to LLM7.io.

LLM7.io states that anonymous usage data may be processed for analysis and model improvement. The
`default` route can choose a different underlying model between requests and has no availability,
service-level, or reproducibility guarantee. Model text remains untrusted, is locally parsed and
schema-checked, and is explicitly labeled as advisory. Any quota, provider, routing, or validation
failure falls back to the complete deterministic report.

Issue-triggered jobs execute the trusted source tree directly and install only the hash-pinned
PyYAML runtime wheel. Public requests always receive deterministic analysis, but AI inference is
limited to `OWNER`, `MEMBER`, or `COLLABORATOR` author associations on this WorkflowPromptGuard
repository, or the maintainer-controlled `ai-approved` label, to protect the anonymous provider
quota.
