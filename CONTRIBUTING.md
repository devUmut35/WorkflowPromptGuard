# Contributing

Focused bug fixes, action adapters, test fixtures, documentation improvements, and high-signal
boundary rules are welcome.

## Before opening a change

- Search existing issues and keep each pull request focused.
- For a new rule, describe the untrusted source, agent boundary, capability, sink, severity, and
  false-positive controls.
- Prefer deterministic structural checks over prompt-text keyword matching.
- Never add a real token, private workflow, or unredacted incident artifact to a fixture.
- Link security claims to primary documentation or research.

## Local checks

Use Python 3.10 or newer:

```bash
python -m pip install -e ".[dev]"
ruff check .
ruff format --check .
mypy src
bandit -r src -ll
pytest --cov
python -m build
```

Every rule change needs:

- one vulnerable fixture that demonstrates the evidence trace;
- one safe counterpart or a documented false-positive control;
- stable rule metadata and remediation;
- reporter/CLI coverage when output changes.

## Rule compatibility

Rule IDs are a public interface. Do not reuse an existing ID for a different security condition.
Severity changes and substantial detection changes belong in the changelog.

## Security reports

Use the private process in [SECURITY.md](SECURITY.md) for exploitable scanner vulnerabilities.
