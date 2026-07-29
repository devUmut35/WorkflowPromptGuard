# Changelog

All notable changes to WorkflowPromptGuard are documented here.

The project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-07-29

### Added

- Public repository scan requests through a structured GitHub issue form.
- A three-job hosted bot that separates deterministic scanning, GitHub Models inference, and
  issue-comment write authority.
- Free, rate-limited AI explanations through GitHub Models with no separately stored API key.
- Commit-pinned retrieval of only public `.github/workflows` files through the GitHub API.
- Bounded artifacts, prompt-safe rule aggregates, idempotent comments, and deterministic fallback
  when AI inference is unavailable.
- Automatic deterministic scans for public requests with collaborator or maintainer approval
  gates protecting the free AI quota.

### Security

- Hardened hostile-repository handling against path escape, symlinks, oversized inputs, recursive
  YAML structures, unsafe repository URLs, and unbounded file retrieval.
- Removed issue-triggered build dependency resolution in favor of a hash-pinned runtime wheel and
  direct execution from the trusted source tree.

## [0.1.0] - 2026-07-29

### Added

- Offline discovery for GitHub Actions YAML and GitHub Agentic Workflow Markdown.
- YAML 1.2-compatible parsing that preserves the `on` key and source locations.
- Twelve boundary-focused rules covering prompt injection paths, agent secrets, write authority,
  guardrail escape hatches, broad tools, unsafe output sinks, privileged PR execution, expression
  injection, action pinning, and token permissions.
- Console, JSON, Markdown, and SARIF 2.1.0 reporters with evidence traces.
- Strict, versioned policy configuration with reasoned and expiring suppressions.
- Composite GitHub Action, secure CI, examples, and a 90% coverage gate.

[Unreleased]: https://github.com/devUmut35/WorkflowPromptGuard/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/devUmut35/WorkflowPromptGuard/releases/tag/v0.2.0
[0.1.0]: https://github.com/devUmut35/WorkflowPromptGuard/releases/tag/v0.1.0
