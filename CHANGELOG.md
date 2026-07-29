# Changelog

All notable changes to WorkflowPromptGuard are documented here.

The project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[Unreleased]: https://github.com/devUmut35/WorkflowPromptGuard/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/devUmut35/WorkflowPromptGuard/releases/tag/v0.1.0
