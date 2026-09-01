# Hermes Dark Factory

A bounded, acceptance-driven software factory plugin for [Hermes Agent](https://hermes-agent.nousresearch.com/docs/).

> **Status:** v0.3.0 bounded dogfood. This release is suitable for controlled local pilots, not unattended production delivery.

Dark Factory turns an approved product brief into a validated mission contract, disjoint milestone slices, independently checked evidence, and explicit stop/replan decisions. Its control variable is **milestone capability accepted**, not card movement, test activity, token spend, or an ever-growing retry loop.

## Safety boundary

- It does not silently deploy, publish, spend money, or contact external systems.
- The active Hermes profile's authenticated provider/model inventory is the only model authority.
- Setup and compiled manifests store provider/model references only; credentials, API keys, OAuth tokens, base credentials, and secret values are not persisted.
- Verifier, adversary, and holdout assignments must be independent from the builder. The Kryptonite/adversarial lens is mandatory.
- Reviewer-role write restrictions are always active when a reviewer role is present.
- State transitions are authenticated, revisioned, and fail closed on missing, corrupt, mismatched, or progressed state.
- A single bounded remediation pass is supported. Repeated identical failures stop and require replanning or human escalation.

The prototype intentionally requires a human-visible decision before high-risk, public, deployment, or spend actions. It is not a replacement for a durable production attestation service or a long-running dispatcher.

## Repository layout

```text
plugin/
├── plugin.yaml                     # Hermes native plugin manifest
├── __init__.py                     # Tool and hook registration
├── engine.py                       # Mission validation and state machine
├── intake.py                       # Guided setup, persistence, and scrubbing
├── model_policy.py                 # Authenticated model-role policy
├── beads_adapter.py                # Optional Beads v1.2.2 graph adapter
├── dashboard/                      # Dashboard manifest, API, and distributable JS
├── desktop/                        # Native Hermes desktop integration
└── skills/dark-factory/SKILL.md    # Reusable operating protocol
scripts/factory.py                  # Offline validation/inspection CLI
fixtures/                           # Representative public-safe mission manifests
templates/manifest.example.json     # Canonical schema-v2 example
research/report.md                  # Sanitized research and design rationale
tests/                              # Unit, API, contract, security, and scenario tests
```

## Requirements

- Hermes Agent with native plugin support.
- Python 3.11 or newer for the CLI and test suite.
- Node.js 18 or newer only for dashboard/desktop syntax checks.
- For a clean checkout, install the pinned test/API dependencies with `python3 -m pip install -r requirements-dev.txt`.
- Optional: Beads CLI **v1.2.2** for the Beads graph backend. The adapter does not initialize or mutate a Beads store during preflight.

The plugin host supplies Hermes runtime modules. The repository does not silently install host packages during plugin installation.

## Install from a public Git repository

After this repository is hosted, install the plugin subdirectory with Hermes' Git installer:

```bash
hermes plugins install OWNER/REPOSITORY/plugin --enable
```

For reproducible installation, pin a full 40-character commit SHA:

```bash
hermes plugins install OWNER/REPOSITORY/plugin \
  --ref 0123456789abcdef0123456789abcdef01234567 \
  --enable
```

Hermes scans plugins before installation. Review the scan result and install only from a source you trust. Restart or start a new Hermes session after installation so plugin discovery is refreshed.

Verify the installation:

```bash
hermes plugins show dark-factory
hermes plugins capabilities dark-factory
hermes plugins doctor "$HOME/.hermes/plugins/dark-factory" --ci
```

For local development, run the same checks against the repository copy before copying `plugin/` into the profile-local plugin directory. Do not overwrite an existing installation without an explicit operator decision.

## Guided setup and model roles

The dashboard and native desktop page read the active profile's authenticated Hermes model inventory. They expose five explicit roles:

| Role | Responsibility |
|---|---|
| Orchestrator / Integrator | Retains mission and milestone intent; owns shared contracts and integration |
| Worker / Builder | Implements one coherent functional slice |
| Verifier | Independently checks the exact candidate against acceptance |
| Adversary | Runs the mandatory hostile/security lens |
| Holdout | Judges milestone scenarios the builder cannot redefine |

The optional `sol-luna` preset fills only otherwise-empty execution roles when the exact authenticated models are present. It never silently substitutes an unavailable model and never assigns verifier, adversary, or holdout roles implicitly.

The readiness form requires an actionable problem, observable outcome, context, personas, non-goals, constraints, measurable success signals, interaction surfaces, mapped milestones, focused/integration checks, held-out and real interaction scenarios, data classification, substantive threat contracts, and at least one locked authority/product decision. **Zero blockers** is the launch condition; a readiness score alone is advisory.

- **Save Draft** persists a schema-allowlisted, profile-scoped setup.
- **Preflight** reports blocker-specific guidance and model availability.
- **Arm Factory** publishes the manifest/state pair atomically only after deterministic checks pass.

## Local CLI

Validate a mission and inspect safe next actions without installing the plugin:

```bash
python3 scripts/factory.py \
  --manifest templates/manifest.example.json \
  --state /tmp/factory-state.json \
  validate

python3 scripts/factory.py \
  --manifest templates/manifest.example.json \
  --state /tmp/factory-state.json \
  next
```

The offline CLI is intentionally read-only after initial validation. It does not authorize model-bound execution and reports `execution_authorized: false`. Runtime state changes must go through the authenticated plugin tools.

## Plugin tools

The plugin exposes nine tools:

- `factory_preflight` — checks guided setup and active-profile model availability.
- `factory_compile` — publishes a manifest/state pair after preflight and refuses to overwrite active progress.
- `factory_validate` — revalidates roles and loads an existing compiled/attested state; it never recreates missing state.
- `factory_next` — applies the same gates before returning a safe dispatch descriptor.
- `factory_transition` — performs locked, revisioned/CAS state transitions.
- `factory_attest_review` — signs verifier, adversary, and holdout receipts from trusted review sessions.
- `factory_lint_card` — validates a durable work item against the slice contract.
- `factory_beads_plan` — deterministically projects a mission into Beads epics and functional-slice tasks without changing Beads.
- `factory_beads_apply` — integrator-only plan/apply/verify with an atomic local mapping receipt.

The plugin also bundles the `plugin:dark-factory` skill and provides the dashboard and native desktop setup surfaces.

## Beads graph backend

New setup defaults to the Beads graph backend when it is available. The operator must initialize an isolated Beads directory separately with the installed Beads CLI; Dark Factory never runs `bd init` implicitly. Preflight proves that the target is a readable initialized store. Apply uses argv (never a shell), pins the resolved `bd` executable, forces daemon-disabled mode, validates the complete observable graph, and writes a receipt only after read-back verification.

An exact retry re-verifies the graph instead of creating duplicates. Receipt loss, closed-node collisions, graph drift, or a mismatched receipt fail closed. Beads is an alternative graph backend, not a mirror of Hermes Kanban; do not use both as competing status authorities for one mission.

Current limitation: v0.3 creates and verifies the graph and emits model-bound dispatch descriptors, but does not include a long-running claimant that polls readiness, launches Hermes sessions, and reconciles graph status after every transition. This remains bounded dogfood support, not unattended production orchestration.

## Verification

Run the canonical test discovery command from the repository root:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/public_release_scan.py
python3 -m compileall -q plugin tests scripts
node --check plugin/dashboard/dist/index.js
node --check plugin/desktop/plugin.js
hermes plugins doctor ./plugin --ci
```

The test suite must discover a nonzero number of tests and finish with `OK`; a bare discovery invocation that runs zero tests is not evidence. The release process also scans the plugin tree for credential-shaped content before packaging.

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) before changing the plugin. In particular:

1. Keep slices coherent and file ownership disjoint.
2. Add positive and negative/recovery/boundary acceptance coverage.
3. Preserve exact model-inventory and credential-scrubbing guarantees.
4. Run the complete unittest discovery command, syntax checks, and Plugin Doctor.
5. Include raw evidence coordinates, criterion IDs, exit codes, and SHA-256 digests for release-facing verification.

See [SECURITY.md](SECURITY.md) for vulnerability reporting guidance and [CHANGELOG.md](CHANGELOG.md) for the v0.3.0 change summary.

## License status

No license has been selected for this release candidate. The repository may be inspected locally, but do not redistribute or reuse the code until the maintainer adds an explicit license.
