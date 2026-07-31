# Threat model

WorkflowPromptGuard models AI agents in CI as untrusted decision-makers operating inside a
potentially privileged automation environment.

## Security assumption

The agent may follow malicious instructions from issue text, pull-request content, repository
files, web pages, MCP responses, artifacts, or other data it reads. A system prompt, delimiter, or
instruction such as "ignore commands in user content" is not an enforcement boundary.

## Sources

- Issue, pull-request, review, discussion, and comment bodies.
- Titles, branch names, commit messages, labels, and workflow inputs.
- Checked-out pull-request files and repository instruction files.
- Artifacts, caches, persistent agent memory, websites, and MCP responses.

The first release has direct syntax models for common GitHub event expressions and GitHub
Agentic Workflow actor roles. Some indirect sources remain unknown edges.

## Assets

- `GITHUB_TOKEN`, personal access tokens, GitHub App credentials, and provider API keys.
- Cloud credentials, package/release credentials, signing keys, and deployment environments.
- Repository contents, pull requests, issues, releases, packages, workflows, and settings.
- Self-hosted runners, internal networks, persistent caches/memory, and AI budget.

## Dangerous capabilities and sinks

- Direct repository write scopes in the agent job.
- Secrets placed in agent inputs, environment variables, tools, or MCP servers.
- Unrestricted shell, broad network egress, wildcard repository targets, or write-capable tools.
- Agent output interpolated into shell, API, release, deployment, or package operations.
- Privileged operations later in the same mutable runner/workspace as the agent.

## Desired boundaries

1. The agent runs read-only and receives no non-model secrets.
2. Shell commands, tools, repositories, and network destinations are explicitly allowlisted.
3. Agent output is treated as untrusted data, never executable input.
4. Structured output crosses into a fresh, least-privilege job.
5. Schema validation, sanitization, threat detection, and environment approval happen before
   writes.
6. Expensive external triggers have actor, timeout, concurrency, and budget limits.
7. External Actions dependencies are pinned to reviewed full commit SHAs.

## Non-goals

- Detecting malicious phrases inside prompts or model output.
- Proving that a workflow, model, action, MCP server, or repository is secure.
- Executing a workflow or scanning live GitHub/cloud infrastructure.
- Resolving remote action code or organization/repository policy in offline mode.
- Automatically rewriting security-sensitive workflows.

## Residual risk

Static analysis cannot fully resolve custom wrapper actions, shell-installed agents, reusable
workflow internals, cross-job filesystem state, artifact semantics, or external repository
settings. WorkflowPromptGuard reports high-confidence visible evidence and intentionally avoids a
"secure" score.

## Hosted issue bot boundary

The public issue bot treats the issue author, issue body, target repository, workflow filenames,
workflow bytes, and model output as untrusted.

Its data flow is intentionally split across three jobs:

1. A read-only scan job validates one canonical public GitHub URL, resolves the default branch to
   a full commit SHA, retrieves only bounded workflow blobs through the fixed GitHub API host, and
   emits a deterministic report plus catalog-backed aggregates.
2. A model job has no issue-write permission. It validates a bounded artifact, then sends an
   anonymous request to the fixed `https://api.llm7.io/v1/chat/completions` endpoint with the
   `default` selector. The request contains only normalized `language`, `scanned_files`, `counts`,
   and catalog-backed `rules` aggregates. Repository identity, commit SHA, raw issue text,
   workflow content, paths, GitHub tokens, and provider keys never enter the prompt. The model uses
   no tools; its text is parsed and schema-checked locally before publication.
3. A comment job has `issues: write` but no model or repository-content permission. It validates
   fixed artifact markers and creates or updates only the current issue's bot report.

The target repository is never cloned or executed. User-supplied hosts, branches, refs,
subdirectories, redirects, symlinks, submodules, oversized files, recursive YAML structures, and
unknown artifact schemas fail closed. LLM7.io currently documents anonymous limits of 60 requests
per hour and 500,000 input-plus-output tokens per rolling 24 hours. It may process anonymous usage
data for analysis and model improvement. Its `default` route can vary the underlying model and
provides no availability, service-level, or reproducibility guarantee. Quota, provider, routing,
malformed-output, and local-validation failures do not affect the deterministic scan.

Public requests receive the deterministic scan automatically, while model inference requires an
`OWNER`, `MEMBER`, or `COLLABORATOR` author association on this WorkflowPromptGuard repository, or
the maintainer-controlled `ai-approved` label. A global concurrency group bounds simultaneous bot
runs, but it does not provide per-actor rate limiting: sustained issue spam can replace the single
pending run GitHub retains for the group and delay legitimate scans. Deployments that require
availability guarantees need an external queue and per-actor limiter.

## References

- [GitHub Actions secure use reference](https://docs.github.com/en/actions/reference/security/secure-use)
- [GitHub Actions script injections](https://docs.github.com/en/actions/concepts/security/script-injections)
- [LLM7.io service information and anonymous-use notice](https://llm7.io/)
- [LLM7.io quickstart](https://docs.llm7.io/quickstart)
- [LLM7.io model selectors](https://docs.llm7.io/guides/models)
- [LLM7.io limits](https://docs.llm7.io/limits)
- [LLM7.io service status](https://status.llm7.io/)
- [GitHub Agentic Workflows overview](https://docs.github.com/en/copilot/concepts/agents/about-github-agentic-workflows)
- [GitHub Agentic Workflows security architecture](https://github.blog/ai-and-ml/generative-ai/under-the-hood-security-architecture-of-github-agentic-workflows/)
- [GitHub Agentic Workflows safe outputs](https://github.github.com/gh-aw/reference/safe-outputs/)
- [GitHub Agentic Workflows threat detection](https://github.github.com/gh-aw/reference/threat-detection/)
