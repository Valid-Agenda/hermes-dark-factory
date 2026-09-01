# Contributing

Thanks for helping improve Hermes Dark Factory. The project is a bounded control plane for acceptance-driven software delivery, so changes must preserve explicit contracts and fail-closed behavior.

## Development setup

Use Python 3.11 or newer and a Hermes Agent environment with native plugin support. Beads integration tests that require the external CLI are optional; when exercised, use Beads CLI v1.2.2 in an isolated temporary directory.

Run the local gates from the repository root:

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q plugin tests scripts
node --check plugin/dashboard/dist/index.js
node --check plugin/desktop/plugin.js
hermes plugins doctor ./plugin --ci
```

A successful test run must discover a nonzero number of tests and finish with `OK`. Do not use a bare discovery command that can silently run zero tests.

## Change guidelines

- Keep each change within a coherent milestone slice; do not turn individual edits or reviewer comments into durable work items.
- Preserve the canonical manifest schemas. Reject unknown fields at boundaries rather than silently projecting legacy/backend-only fields.
- Keep model references as `{provider, model}` only. Never persist credentials, API keys, OAuth tokens, connection strings, or secret-shaped values.
- Treat the active profile's authenticated model inventory as authoritative. Do not add silent fallback or truthy-but-unauthenticated model selection.
- Keep verifier, adversary, and holdout roles independent from the builder. The Kryptonite/adversarial lens must remain enabled.
- Keep reviewer-role write restrictions and the one-remediation circuit breaker intact.
- For stateful changes, add subprocess or persistence coverage for missing, corrupt, mismatched, replayed, and progressed state.
- For dashboard or desktop changes, exercise the real browser-visible interaction and check the browser console; syntax/build success alone is not sufficient.
- Keep test fixtures portable. Do not add personal filesystem paths, session links, internal task IDs, credentials, or private product artifacts.

## Pull requests

A pull request should include:

1. A concise problem statement and the acceptance behavior changed.
2. The candidate commit SHA and affected files.
3. Exact commands, exit codes, and observed results for the full test and plugin gates.
4. Raw artifact paths and SHA-256 digests for release-facing evidence.
5. Any security, privacy, compatibility, or migration impact.
6. Explicitly documented limitations or follow-up work; do not hide an unresolved defect behind a green unit test.

Use conventional commit subjects such as `feat:`, `fix:`, `docs:`, `test:`, or `chore:`. Keep the first line concise and explain contract changes in the body when needed.

## Scope and release decisions

Do not publish, deploy, contact external systems, or spend money as part of an automated contribution workflow. Those actions require an explicit human decision outside the repository's test gates.
