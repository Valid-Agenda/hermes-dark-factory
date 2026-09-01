# Changelog

## 0.3.0 — bounded dogfood candidate

### Added

- Guided setup and readiness validation for actionable product briefs.
- Explicit integrator, builder, verifier, adversary, and holdout role policy.
- Authenticated active-profile model inventory filtering with no credential persistence.
- Atomic manifest/state compilation and fail-closed revisioned transitions.
- Signed review receipts bound to the trusted review session and candidate state.
- Beads CLI v1.2.2 plan/apply/verify adapter with deterministic graph mapping and replay checks.
- Dashboard and native desktop setup surfaces backed by the same profile-scoped API.
- Offline validation/inspection CLI and representative schema-v2 fixtures.
- Strict controls for worker limits, contract parity, reviewer isolation, evidence, and path safety.
- Regression coverage for legacy persisted threat rows, credential-shaped input, malformed model inventory, replay, drift, and authority mismatches.

### Known limitations

- The Beads backend does not provide a long-running claimant/dispatcher or unattended reconciliation loop.
- The prototype's process-local attestation signer intentionally fails closed across runtime restarts; production requires a durable audited attestation service.
- This release is for controlled local pilots only. It does not authorize deployment, public publishing, spending, or external communications.
