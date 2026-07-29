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

WorkflowPromptGuard performs static, local file analysis. It does not execute workflows, resolve
remote actions, call GitHub APIs, or test live infrastructure. A clean result is not a security
guarantee.
