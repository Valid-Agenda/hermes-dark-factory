# Security policy

Hermes Dark Factory handles mission contracts, model-role references, evidence receipts, and optional local graph metadata. It is a bounded dogfood prototype, not a production attestation or deployment system.

## Reporting a vulnerability

Please do **not** disclose suspected vulnerabilities in a public issue. After this repository is hosted, use the repository's private security-advisory/reporting channel. Until then, share the report privately with the project maintainer and include:

- the affected version or commit;
- a minimal reproduction that contains no credentials or personal data;
- the expected and observed behavior;
- the impact and any safe mitigation.

Never include API keys, OAuth tokens, passwords, private keys, connection strings, or raw private session/board artifacts in a report. Replace sensitive values with `[REDACTED]`.

## Security design commitments

- Only authenticated provider/model inventory entries are eligible for model-role selection.
- Setup and manifest persistence stores role references, never credentials.
- Reviewers are read-only when their factory role is active.
- The Kryptonite/adversarial lens is mandatory.
- State, receipt, revision, and actor mismatches fail closed.
- Plugin installation is subject to Hermes' security scan.
- External deployment, publishing, spending, and messaging are outside the factory's implicit authority.

See the repository README and the bundled Dark Factory skill for the current threat model and known prototype limitations.
