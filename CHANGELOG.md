# Changelog

## 0.5.0 — bounded autonomous remediation

### Added

- Explicit `continue_slice` transitions and dispatch descriptors after a local review rejection.
- Functional blocks are now the durable build unit; thin slices and semantic edits remain inside a block. The compatibility manifest key remains `slices` and supports grouped `story_ids`.
- Fresh-session `resume_slice` and `resume_milestone` transitions do not consume semantic remediation budget.
- Milestone-scoped verifier, adversary, and holdout review for newly compiled blocks.
- Detached Beads-backed supervisor with durable process/run metadata, claims, requeue, fresh Hermes launches, and no mission-wide wall-clock limit.
- Fresh builder handoff support with attested builder-authority history.
- Three remediation cycles by default, while retaining repeated-failure and budget circuit breakers.
- Strict delegation checks that accept only startable, resumable, or explicitly continuable functional blocks.
- Plain-language continuation/stop guidance so the integrator does not end a mission while a safe continuation remains.

## 0.4.0 — desktop project workspace candidate

### Added

- Hermes-native desktop Projects overview for repeat Dark Factory builds.
- Per-project progress, milestone/slice status, bounded event/log tail, and Beads capability/readiness snapshot.
- Profile-scoped global defaults for model role references, system prompts, coordination mode, Beads settings, and policy/reasoning values.
- Sparse per-project overrides with reset-to-global behavior.
- Project-aware intake save and compile routes while preserving the legacy dashboard setup contract.
- Optional `system_prompts` manifest field with backward-compatible validation for older manifests.
- Canonical manifest import from chat/tool or desktop path into a pristine manifest/state pair, without applying Beads.
- Namespaced plugin skill workflow (`/skill dark-factory:dark-factory`) for chat-first intake, import, preflight, compile, planning, and guarded execution.
- Optional [Bead Me Up Scotty](https://github.com/brendan-appstart/bead-me-up-scotty) visibility link over the same local Beads store.

### Limitations

- Beads CLI v1.2.2, an initialized project Beads directory, and explicit write authorization are required before project compilation or graph writes.
- Hermes Kanban is intentionally not used; legacy `local`, `kanban`, and `both` settings migrate to the required Beads mode.
- The desktop plugin remains opt-in under Hermes' unified plugin security posture.

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

- The prototype's process-local attestation signer intentionally fails closed across runtime restarts; production requires a durable audited attestation service.
- The supervisor is for controlled local pilots only. It does not authorize deployment, public publishing, spending, or external communications, and explicit unattended authorization is required to launch it.
