# Hosted issue bot

**English** | [Türkçe](issue-bot.tr.md)

The hosted bot lets a visitor scan one public GitHub repository from a structured issue form.
It is a convenience layer around the same deterministic scanner used by the CLI.

## Request flow

1. Select **Herkese açık depoyu tara / Scan a public repository** on the new-issue page.
2. Choose **Türkçe** or **English** as the report language.
3. Enter exactly one `https://github.com/OWNER/REPOSITORY` URL on its own line.
4. Submit the issue. The form adds the `scan-request` label.
5. The bot resolves the target's default branch to an immutable commit SHA.
6. A deterministic report and, when available, an AI-generated explanation are posted in the
   selected language as one bot comment.

Reopening the issue reruns the scan and updates the existing bot comment when it is among the
first 100 comments.

Deterministic scans run automatically for every valid form submission. To protect the free model
quota from public issue spam, AI explanations run automatically only when the requester's
association with this WorkflowPromptGuard repository is `OWNER`, `MEMBER`, or `COLLABORATOR`. A
maintainer can enable AI for another request by applying the `ai-approved` label.

## Authentication and cost

No third-party key or long-lived repository secret is required. GitHub creates a short-lived
`GITHUB_TOKEN` for each job. The scan job uses it for read-only GitHub API requests, the model job
uses `models: read`, and the comment job uses `issues: write`.

GitHub Models includes free, rate-limited inference. This project does not enable paid model
usage. When the free quota is unavailable or GitHub Models rejects a request, the bot posts the
complete deterministic report with a short fallback notice.

The bot imports its trusted source tree directly and installs only a version- and hash-pinned
PyYAML wheel on a GitHub-hosted Linux runner with Python 3.13. It does not resolve build
dependencies during an issue-triggered job.

## Security controls

- Target URLs cannot select a host, port, credential, branch, ref, path, query, or fragment.
- Only public repositories are accepted.
- Only regular `.yml`, `.yaml`, and `.md` files directly under `.github/workflows` are fetched.
- The target default branch is resolved once and all content calls use that full commit SHA.
- At most 64 workflow files, 256 KiB per file, and 2 MiB total are accepted.
- Target repositories are not cloned; hooks, submodules, LFS filters, dependencies, and code are
  never executed.
- YAML source size, nesting, nodes, aliases, and expanded graph traversal are bounded.
- Raw issue text, workflow source, finding messages, traces, and paths are not sent to the model.
- The selected language is reduced to the closed set `tr` or `en`; raw form text is not sent to
  the model.
- The model receives catalog-backed rule IDs, severities, counts, and remediation text only.
- The scan, model, and comment jobs have separate least-privilege tokens.
- Model output is schema-checked, length-limited, mention-neutralized, and never used as a command,
  URL, identifier, or API target.

## Operational limits

The issue form is public, so GitHub abuse controls and the GitHub Models free quota are the
practical request-rate boundaries. High-volume production service would require an external queue
and per-actor rate limiter. The current bot is intended for public demonstrations and bounded
repository checks. The global concurrency group limits simultaneous work, but it is not a rate
limiter: GitHub keeps only one pending run in a group, so sustained issue spam can replace a
legitimate pending scan and delay service. AI inference remains separately protected by the
trusted-author/`ai-approved` gate.
