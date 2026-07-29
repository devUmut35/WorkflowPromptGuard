# Rule reference

WorkflowPromptGuard rules describe enforceable trust boundaries. Default severity reflects the
worst supported path; individual findings may be downgraded when a capability is narrower.

## Agent boundary rules

### AI001 — Untrusted content reaches a write-capable agent

**Default: Critical**

Detects external event content or actor roles reaching an agent with direct repository write
permissions or secret-bearing tools.

Preferred fix: keep the agent job read-only, emit structured data, and apply validated writes from
a separate scoped job.

### AI002 — Secret exposed to the agent trust domain

**Default: Critical**

Detects secret or token expressions in conventional agent steps and in GitHub Agentic Workflow
tool/MCP configuration.

Provider authentication may be unavoidable in conventional wrappers. Prefer an isolated proxy or
GitHub App. Otherwise document the reviewed isolation boundary with a narrow, expiring
suppression.

### AI003 — Agent output reaches executable code

**Default: Critical**

Detects an agent step output referenced by a later `run` or privileged action in the same job.
Quoting does not turn model output into trusted code.

### AI004 — Agent guardrail disabled

**Default: High**

Detects `strict: false`, disabled safe-output threat detection, and disabled minimum action
integrity.

### AI005 — Agent capability is unrestricted

**Default: High**

Detects wildcard shell policy, unrestricted network destinations, and caller-extensible network
allowlists. `network.allowed-input` is Medium because the caller still passes through the
compiled workflow's controls.

### AI006 — Safe output scope is too broad

**Default: High**

Detects `target-repo: "*"` (Critical) and wildcard entries such as `my-org/*` in
`allowed-repos` (High).

### AI007 — Agent shares a job with a privileged step

**Default: High**

Detects release, deployment, package, push, and write-capable GitHub operations after an agent in
the same job. The agent may change files, binaries, environment state, or background processes
that the later step trusts.

### AI008 — Untrusted actors can trigger an unbounded agent run

**Default: Medium**

Detects external GitHub Agentic Workflow roles with disabled credit caps, and conventional
externally triggered agent jobs without timeout and concurrency controls.

## GitHub Actions foundation rules

### GA001 — Privileged workflow executes pull-request code

**Default: Critical**

Detects `pull_request_target` workflows that check out `github.event.pull_request.head` and then
execute a script or agent.

### GA002 — Untrusted expression is interpolated into a script

**Default: High**

Detects flexible `github.event`, branch/ref, and workflow input expressions directly inside
`run`. Pass the expression through `env` and consume a quoted shell variable, or use a
purpose-built action input.

### GA003 — External action uses a mutable reference

**Default: Medium; High for recognized agent actions**

External `uses:` values must end in a full 40-character commit SHA. Local actions and
`docker://` references are exempt.

### GA004 — Agent workflow token permissions are not least privilege

**Default: Medium**

Detects missing explicit permissions, `write-all`, or individual write scopes in a workflow with
a recognized agent step. Write permissions on untrusted triggers are High.

## Suppression standard

Use a suppression only when an enforcement boundary exists outside syntax the scanner can see:

```yaml
ignore:
  - rule: AI002
    path: .github/workflows/reviewer.yml
    reason: Secret is mounted only in an isolated provider proxy; review SEC-142.
    expires: 2026-12-31
```

Reasons are mandatory. Prefer exact paths over broad globs and include an expiry for temporary
exceptions.
