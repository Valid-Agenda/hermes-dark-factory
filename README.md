# Hermes Dark Factory

A bounded, acceptance-driven software factory plugin for [Hermes Agent](https://hermes-agent.nousresearch.com/docs/).

> **Status:** v0.4.0 desktop/chat dogfood candidate. This release is suitable for controlled local pilots, not unattended production delivery.

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
├── beads_adapter.py                # Beads v1.2.2 graph adapter
├── dashboard/                      # Dashboard manifest, API, and distributable JS
├── desktop/                        # Native Hermes desktop integration
└── skills/dark-factory/SKILL.md    # Reusable operating protocol
scripts/factory.py                  # Offline validation/inspection CLI
fixtures/                           # Representative public-safe mission manifests
templates/manifest.example.json     # Canonical schema-v2 example
research/report.md                  # Sanitized research and design rationale
tests/                              # Unit, API, contract, security, and scenario tests
```

## Requirements and fresh-Hermes setup

- Hermes Agent with native plugin support.
- Python 3.11 or newer for the CLI and test suite; for a clean checkout, install the pinned test/API dependencies with `python3 -m pip install -r requirements-dev.txt`.
- Node.js 18 or newer only for dashboard/desktop syntax checks and the npm Beads install route.
- Beads CLI **v1.2.2** is a required runtime dependency for project compilation and graph writes.

**Installing Dark Factory does not install Beads.** Beads is a separate native CLI dependency, and Dark Factory also does not run `bd init` implicitly. Install and verify Beads in the same environment that launches Hermes, then initialize each project workspace explicitly.

### 1. Install and verify Beads

For WSL/Linux/macOS with Node.js/npm, install the pinned supported version:

```bash
npm install -g @beads/bd@1.2.2
command -v bd
bd --version
```

The last command must report Beads `1.2.2`. On Windows, run this inside the same WSL distribution/user environment used by Hermes if Hermes is running under WSL. If `bd` is found only from an interactive shell, make its bin directory available to the non-interactive process that launches Hermes and verify again. The official alternatives are documented in the [Beads installation guide](https://github.com/gastownhall/beads/blob/main/docs/getting-started/installation.md); if you use an unpinned installer, still verify the exact version before enabling graph writes.

### 2. Initialize each project/workspace

The normal `bd init` scope is the current project: it creates a project-local `.beads/` directory. Repeat this for every independent Dark Factory workspace:

```bash
cd /absolute/path/to/your/project
bd init
test -d .beads
```

Do **not** use `bd init --global` or `bd init --shared-server` for the normal setup. Those are explicit shared-database modes; Dark Factory's default is one Beads store per project/workspace. Preflight checks the target store but never initializes it.

### 3. Install the plugin from GitHub

Once this repository is available at [Valid-Agenda/hermes-dark-factory](https://github.com/Valid-Agenda/hermes-dark-factory), install its `plugin/` subdirectory:

```bash
hermes plugins install Valid-Agenda/hermes-dark-factory/plugin --enable
```

For reproducible testing, pin a full commit SHA:

```bash
hermes plugins install Valid-Agenda/hermes-dark-factory/plugin \
  --ref <40-character-commit-sha> \
  --enable
```

Hermes scans plugins before installation. Review the scan and install only from a trusted source. Start a new Hermes process/session after installation so plugin discovery is refreshed.

### 4. Verify the installed plugin

```bash
hermes plugins show dark-factory
hermes plugins capabilities dark-factory
hermes plugins doctor "$HOME/.hermes/plugins/dark-factory" --ci
```

For local development, run the same checks against `./plugin` before copying it into a profile-local plugin directory. Do not overwrite an existing installation without preserving a backup and making an explicit operator decision. The installable package's condensed setup guide is [plugin/README.md](plugin/README.md).

## Desktop project workspaces

The native Hermes desktop integration is a repeat-build workspace, not only a one-off setup form. Enable the installed `dark-factory` plugin under the desktop plugin settings, then use **Dark Factory** in the sidebar:

- **Projects** lists Hermes-native projects and shows factory status, milestone/slice progress, coordination mode, and the last recorded event.
- **New project** creates a named Hermes project, scopes the factory to its primary folder, and keeps its setup/state separate from every other build.
- **Project workspace** shows the derived progress snapshot, bounded event/log tail, Beads capability/readiness, and project-specific configuration.
- **Bead visibility** can be opened in [Bead Me Up Scotty](https://github.com/brendan-appstart/bead-me-up-scotty), an optional local visual UI over the same `bd` source of truth. Dark Factory does not copy Beads into a second board/database.
- **Global defaults** persist role-keyed models, role-keyed system prompts, the required Beads coordination mode, Beads directory/authorization, and policy defaults.
- A project may save sparse overrides or reset to global defaults. The project identity and workspace path always come from Hermes' native Projects registry.
- **Configure mission** opens the existing acceptance-driven intake form for the selected project. **Arm Factory** still requires zero preflight blockers and preserves the manifest/state transactional gates.

### Chat-first workflow

Plugin-provided skills are namespaced by Hermes. From chat, invoke the Dark Factory skill with `/skill dark-factory:dark-factory`; no desktop navigation is required. The skill drives the same guarded tools through intake, import, preflight, compile, Beads planning, independent review, and authorized execution. To start from a manifest populated by another agent, use `/skill dark-factory:dark-factory import /absolute/path/to/manifest.json` (or provide the manifest object to `factory_import_manifest`). Import writes only a pristine manifest/state pair; it never applies the Beads graph or publishes externally.

The current runtime treats the compiled Dark Factory manifest/state pair as the factory authority and Beads as the required work graph. Hermes Kanban is intentionally not used as a second coordination database. Legacy persisted `local`, `kanban`, and `both` settings migrate to Beads; new configuration rejects them. Project compilation fails closed until the `bd` executable, initialized `.beads` directory, and explicit write authorization are present.

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

The plugin exposes ten tools:

- `factory_preflight` — checks guided setup and active-profile model availability.
- `factory_compile` — publishes a manifest/state pair after preflight and refuses to overwrite active progress.
- `factory_import_manifest` — imports a canonical schema-v2 Beads-backed manifest from a path or inline object into a pristine workspace without applying the graph.
- `factory_validate` — revalidates roles and loads an existing compiled/attested state; it never recreates missing state.
- `factory_next` — applies the same gates before returning a safe dispatch descriptor.
- `factory_transition` — performs locked, revisioned/CAS state transitions.
- `factory_attest_review` — signs verifier, adversary, and holdout receipts from trusted review sessions.
- `factory_lint_card` — validates a durable work item against the slice contract.
- `factory_beads_plan` — deterministically projects a mission into Beads epics and functional-slice tasks without changing Beads.
- `factory_beads_apply` — integrator-only plan/apply/verify with an atomic local mapping receipt.

The plugin also bundles the `dark-factory:dark-factory` namespaced skill and provides the dashboard and native desktop setup surfaces.

## Beads graph backend

New setup uses Beads as its only graph backend. The operator must initialize an isolated Beads directory separately with the installed Beads CLI; Dark Factory never runs `bd init` implicitly. Preflight proves that the target is a readable initialized store. Apply uses argv (never a shell), pins the resolved `bd` executable, forces daemon-disabled mode, validates the complete observable graph, and writes a receipt only after read-back verification. The adapter recognizes normal `PATH` plus common user-owned WSL install locations such as `~/.bun/bin/bd`.

An exact retry re-verifies the graph instead of creating duplicates. Receipt loss, closed-node collisions, graph drift, or a mismatched receipt fail closed. Beads is the required graph authority. For a visual board or dependency graph, use the optional [Bead Me Up Scotty](https://github.com/brendan-appstart/bead-me-up-scotty) companion; it reads the same local `bd` store and is not a second Dark Factory ledger.

Current limitation: v0.4 creates and verifies the graph and emits model-bound dispatch descriptors, but does not include a long-running claimant that polls readiness, launches Hermes sessions, and reconciles graph status after every transition. This remains bounded dogfood support, not unattended production orchestration.

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

See [SECURITY.md](SECURITY.md) for vulnerability reporting guidance and [CHANGELOG.md](CHANGELOG.md) for the v0.4.0 change summary.

## License

This project is released under the [MIT License](LICENSE).
