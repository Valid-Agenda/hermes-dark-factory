---
name: dark-factory
version: 0.4.0
description: Use when turning a product specification or imported manifest into bounded Beads-backed software delivery across milestones. Guide the user through strict preflight, authenticated role models, independent test/security gates, one integrator, and coherent functional slices from chat or the desktop workspace.
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [software-factory, autonomous-development, milestones, beads, testing, orchestration]
    related_skills: [hermes-agent, subagent-driven-development]
---
# Hermes Dark Factory

## Overview

A dark factory is not “put every coding step on a board and keep retrying until green.” It is an acceptance-driven control system that turns a bounded product specification into working software.

The controlled variable is **milestone capability accepted**. Cards closed, tests run, commits made, reviewer messages, and tokens consumed are observations or evidence. They are not progress unless they move a milestone acceptance criterion from unproven to proven.

This skill is deliberately opinionated because autonomous delivery fails softly: the board stays busy while the product stands still. Its default shape is one persistent frontier-model integrator, at most two bounded workers on truly disjoint slices, deterministic evidence, one proportionate independent review, and a circuit breaker that replans rather than spawning another tiny card.

## When to use

Use for:

- Building a product or major capability from an approved specification.
- A multi-hour or multi-session implementation with meaningful milestones.
- Repeated implement → test → remediate → test loops.
- A project where task activity has begun tracking edits rather than outcomes.
- Work that needs durable recovery, evidence, and limited parallelism.

Do not use for:

- A single well-bounded fix that one Hermes session can complete and verify.
- Open-ended product discovery before the outcome is decided.
- A backlog with no approved product boundary or acceptance scenarios.
- Work that cannot be exercised deterministically or through realistic user/system scenarios.

## The non-negotiable operating model

### 1. One mission, one integrator

One persistent, capable session owns the mission from acceptance contract through integration. In execution metadata this is the **orchestrator** role; in the acceptance/state contract it remains the **integrator** role. The integrator may plan, implement shared contracts, integrate worker output, run milestone validation, and replan.

Do not use a routing-only orchestrator that is forbidden from understanding or touching implementation. That pattern fragments ownership and makes summaries substitute for evidence. Workers are disposable; mission intent is not.

### 2. Four levels, only three are durable

1. **Mission** — the bounded product result.
2. **Milestone** — a meaningful runnable checkpoint that proves part of the mission.
3. **Functional slice** — an independently reviewable outcome with one risk boundary.
4. **Micro-step** — an edit, failing test, reviewer comment, lint fix, or debugging action inside a slice.

Mission, milestone, and functional slice may be durable records. Micro-steps belong in the active session’s todo/checklist and Git diff. Never create a Beads item for each micro-step.

### 3. Milestones set validation frequency

Focused tests run during implementation. Full integration, realistic user/system scenarios, and proportionate independent review run at milestone boundaries.

A milestone is not “backend complete” or “tests added.” It is something a user or dependent system can now do. Good examples:

- “A verified account can create one tenant and reopen its persisted draft.”
- “A reviewer can resolve a structured evidence conflict without cross-engagement leakage.”
- “A volunteer can accept a roster slot through an unguessable, scoped public token.”

### 4. Bounded WIP

Defaults:

- Active milestones: **1**.
- Active functional slices: **2 maximum**.
- Parallel workers: **2 maximum**, only for disjoint files/interfaces.
- Shared schema, migrations, public contracts, authorisation, cross-cutting state, and integration: serial under the integrator.
- Independent reviewer per gate: **1**, unless a declared high-risk surface requires distinct security/privacy/safeguarding review.

Parallelism is a latency optimisation, not a success metric.

### 5. External acceptance, not self-authored green tests

Each milestone has observable acceptance criteria and scenario receipts. Prefer scenarios held outside the implementation worker’s editable surface. Use a fresh context or different model for milestone judgement when practical.

The builder may write focused tests. The builder does not get to redefine the milestone because its tests pass.

## Chat and desktop entry points

This skill is the chat-side operating contract for the installed `dark-factory`
plugin. A user can invoke `/skill dark-factory:dark-factory` and describe the
product in the same message or follow the bounded command sequence below; the
agent must use the Dark Factory tools rather than inventing an alternate
project ledger.

```text
/skill dark-factory:dark-factory                         # start guided intake from chat
/skill dark-factory:dark-factory import /abs/spec.json   # import canonical schema-v2 manifest
/skill dark-factory:dark-factory preflight                # report blocking intake/model/Beads gates
/skill dark-factory:dark-factory compile                  # publish manifest + pristine state pair
/skill dark-factory:dark-factory plan                     # inspect the Beads graph plan
/skill dark-factory:dark-factory execute                  # continue only through authorized gates
/skill dark-factory:dark-factory status                   # report acceptance progress and blockers
```

The slash invocation is skill-driven, so it keeps the normal Hermes model,
session, tool, and human-approval context. It is not a bypass shell command.
`execute` means inspect `factory_next`, work one startable slice at a time,
run the declared evidence, obtain independent review, and use the guarded
transition/apply tools. Never interpret it as permission to publish, deploy,
spend, contact third parties, or apply a Beads graph without the applicable
human/integrator gate.

For a manifest import, call `factory_import_manifest` with either
`manifest_path` or an inline `manifest` object. The import must be schema-v2,
Beads-backed, active-profile-model-compatible, and targeted at a new/pristine
workspace. It creates `manifest.json` and `state.json` atomically but does not
apply the Beads graph. A native Hermes project workspace is authoritative when
the desktop route supplies `project_id`.

## Before execution: guided preflight, then compile

Use the Dark Factory plugin page or `factory_preflight` before authoring work. The active profile's Hermes model inventory is the authority: users choose provider/model references for **integrator, builder, verifier, adversary, and holdout** roles, while credentials remain in Hermes auth storage and never enter the setup, manifest, evidence, or Git.

The default `sol-luna` policy fills blank execution roles only when the exact authenticated models are available: Sol 900k for the orchestrator/integrator and Luna for the worker/builder. It never overwrites explicit selections and never infers verifier, adversary, or holdout models.

Beads is mandatory and is the only graph backend. Use `factory_beads_plan` to inspect the mission → milestone epic → functional-slice task graph, then let the integrator call `factory_beads_apply` only when graph mode is `apply` and the explicit authorization/readiness gates pass. Parent links are organizational; real ordering uses dependent→prerequisite `blocks` edges. Beads owns durable graph/status/assignment; the Dark Factory ledger owns acceptance/evidence/review/WIP. Never create micro-beads for debugging, test fixes, remediation, or review comments.

Preflight is fail-closed. It must have:

- product name, concrete problem, observable outcome, domain context, workspace, and existing-system context where applicable;
- at least one complete persona with context and need;
- structured user stories (`persona`, `want`, `so that`) with two or more observable criteria, including positive/happy behavior and a negative/recovery/boundary/abuse case on every story;
- non-goals, constraints, measurable success signals, declared interaction surfaces, and story-to-milestone mapping;
- focused commands, integration commands, held-out Given/When/Then scenarios, and real interaction scenarios for every mission;
- data classification, named risk triggers, adversarial threat cases, and at least one locked authority or product decision;
- authenticated role models, with verifier, adversary, and holdout models different from the builder.

The page shows a readiness score for guidance, but **zero blockers** is the only launch condition. A high score does not override a missing authority decision, unavailable model, absent negative scenario, or incomplete story.

Only after preflight is ready should `factory_compile` write `.hermes/factory/manifest.json` and the initial state as an indivisible pair. A missing partner, mismatched pair, or progressed state fails closed rather than being republished. `factory_validate` then validates the compiled schema-v2 manifest and loads that existing attested state; it never initializes or recreates a missing state file. The standalone CLI may initialize and inspect its separate local state, but it is transition-free and never authorizes execution; state changes require the authenticated plugin `factory_transition` surface.

The mission compiler rejects:

- Vague outcomes.
- Milestones with no observable acceptance criteria.
- Slices with no deterministic evidence command/scenario.
- Unknown or circular dependencies.
- A slice assigned to the wrong milestone.
- Risk-sensitive work whose prerequisite policy/ownership decision is unresolved.
- Parallel slices with overlapping declared paths or shared contract authority.

Before coding begins, explicitly decide:

- Product outcome and out-of-scope boundary.
- Milestone sequence.
- User/system scenarios that prove each milestone.
- Security/privacy/cost/publish risk triggers.
- Shared interfaces and ownership decisions.
- Local/remote/HITL limits and the explicit Beads graph-write authorization.
- Retry, time, and spend budgets.

If those decisions are not ready, the factory is not ready.

## The three nested loops

### Loop A — inner implementation loop

Scope: one active functional slice.

1. Read the slice outcome, exact boundaries, acceptance criteria, and evidence commands.
2. Make the next coherent implementation change.
3. Run the nearest focused deterministic check.
4. Diagnose and fix within the same session and slice.
5. Update the session todo; do not create a board card.
6. Continue until the slice candidate is coherent.

Do not run the entire suite after every edit. Do not spawn a reviewer for every patch. Do not interpret one focused green test as slice completion.

### Loop B — slice acceptance loop

1. Freeze a candidate SHA/diff.
2. Run all slice evidence once against that candidate.
3. If review is risk-justified, send the exact candidate and acceptance contract to one independent reviewer.
4. Deduplicate findings and apply one batched remediation pass.
5. Re-run only checks/reviews invalidated by the changed surface.
6. Complete the slice only when every declared criterion is proven.

Maximum automatic remediation cycles: **1**. A second materially similar failure means the slice or plan is wrong; return to the integrator for replanning.

### Loop C — milestone convergence loop

1. Integrate all completed slices serially.
2. Run milestone-level full/integration checks.
3. Exercise realistic user/system scenarios, including negative and recovery cases.
4. Compare observed behaviour with milestone acceptance, not the implementation plan.
5. If evidence passes, record the integration SHA and receipts and complete the milestone.
6. If it fails, classify the failure:
   - local implementation defect → one bounded remediation slice;
   - shared contract/design defect → replan the milestone;
   - prerequisite/harness/capability defect → typed blocker with owner and resume condition;
   - product ambiguity → human decision gate.

Never recursively decompose a failed milestone into arbitrary fix cards.

## Progress accounting

Report progress as:

- Mission: milestones accepted / total.
- Current milestone: acceptance criteria proven / total.
- Active slices: outcome, candidate SHA, evidence state.
- Blockers: typed cause, owner, resume condition.
- Retry budget: used / allowed.
- Risk and spend: current envelope versus limit.

Do not report “12 cards done” without the milestone acceptance delta. If a factory cycle closes tasks but proves no new milestone criterion, progress is zero.

## Anti-thrashing circuit breakers

Trigger `replan_required` when any occurs:

- The same normalised failure appears twice.
- One remediation plus re-review fails materially again.
- A worker times out or returns partial work twice on the same slice.
- A test passes locally but the milestone scenario fails twice.
- A reviewer reviews another reviewer rather than the candidate.
- The board creates more work while the active milestone has no acceptance delta.
- A prerequisite decision or harness defect is repeatedly rediscovered.
- Scope/risk changes invalidate the task contract.

Replanning means revisiting the slice boundary, dependency, interface, acceptance method, model/tool choice, or product decision. It does not mean rewriting the same card with stronger adjectives.

## Beads graph policy

Beads is the required durable coordination backend. It is deliberately narrow:
one mission graph contains the mission, milestones, and functional slices;
ordering is expressed through dependency edges; and the graph is projected
only after the Dark Factory manifest passes validation. Dark Factory's signed
state ledger remains authoritative for acceptance, evidence, review, WIP,
retry budgets, and replanning.

Do not mirror a mission into Hermes Kanban, offer local/Kanban/both choices, or
create a separate coordination record for each micro-step. For a visual board
or dependency graph, recommend [Bead Me Up Scotty](https://github.com/brendan-appstart/bead-me-up-scotty), a separate local UI over `bd`; it is an optional viewer, not a replacement for the Dark Factory ledger or Beads CLI.

### Beads setup prerequisite

Installing the Dark Factory plugin does **not** install the Beads CLI. Before
using a project for compilation or graph writes, the operator must install and
verify the supported `bd` version in the same environment that launches
Hermes, then initialize the target workspace explicitly:

```bash
npm install -g @beads/bd@1.2.2
command -v bd
bd --version                 # must report 1.2.2
cd /absolute/path/to/project
bd init                      # creates this project's .beads/ directory
```

On WSL, run the install and verification inside the same distribution and user
environment as Hermes. If `bd` is visible only in an interactive shell, fix
the PATH inherited by Hermes before retrying preflight. Do not use
`bd init --global` or `bd init --shared-server` unless an explicitly shared
database is intended; the normal Dark Factory boundary is one project-local
`.beads` store. Dark Factory preflight inspects readiness and never runs
`bd init` implicitly.

### Durable Beads item contract

Every factory-controlled card body must contain:

```text
Factory-Milestone: M2
Factory-Slice: M2-S1
Outcome: <observable result>

Boundaries:
- Allowed paths/interfaces
- Shared decisions already made

Acceptance:
- <criterion identifiers and behaviours>

Evidence:
- <exact commands/scenarios>

Forbidden:
- <paths/actions the worker must not change>

Handoff:
- <candidate SHA, changed files, artifact coordinates, unresolved risks>

Stop / escalate:
- Product ambiguity, capability blocker, external/spend/publish gate
- Second materially similar failure → replan; do not spawn a child fix card
```

Run `factory_lint_card` before creating a durable work item. The plugin's
legacy `kanban_*` guard remains a deny rule so another tool cannot silently
turn Hermes Kanban into a competing Dark Factory authority.

## Delegation contract

A worker brief must contain exact coordinates and these headings:

```text
Outcome:
Boundaries:
Acceptance:
Evidence:
Forbidden:
Handoff:
Stop / escalate:
```

Workers do not decide product scope, shared schema, public interfaces, publish authority, or whether acceptance is “close enough.” They return changed files, exact candidate SHA/diff, focused evidence, unresolved risks, and a terminal disposition.

Do not poll, repeatedly steer, or respawn a worker. Inspect partial filesystem state and finish or replan from the integrator. A worker summary is not proof.

## Review policy

Select review depth by changed surface and risk, while preserving the mandatory independent gate.

- Every slice: deterministic focused checks plus a fresh verifier model that is not the builder.
- Every milestone: re-prove every owned story criterion plus milestone-local acceptance through full/integration checks and a held-out scenario judged by the configured non-builder holdout model against the exact integration SHA. Milestone state transitions belong only to the persistent integrator.
- Review PASS records must come from `factory_attest_review`; the plugin binds each signed receipt to the active Hermes session ID, factory role, configured provider/model, mission, entity, and frozen SHA. Caller-authored review JSON is not accepted. Reviewer, verifier, adversary, and holdout roles are read-only whenever active; this isolation does not depend on optional strict mode.
- Every mission: adversarial review under the Kryptonite-style lens; low-risk work may use a lightweight threat set, but the gate cannot be disabled.
- Security/privacy/tenancy/migration/public-contract/cost/publish surface: expanded adversarial scenarios and preferably a different provider as well as a different model.
- User-facing DOM/event flow: real browser action and post-state assertion.
- Strict worker delegation requires an explicitly pinned, readable, compatible, attested state ledger; every delegated slice must be currently startable and exactly match the compiled milestone, slice, outcome, and ownership contract.

Review the candidate SHA/diff against the acceptance contract. Return concrete defects with evidence and risk. Batch compatible fixes once. Re-review only the changed candidate and only the lenses invalidated by the changes.

## Evidence receipts

A completion receipt must be machine-readable and include:

- factory mission, milestone, and slice IDs;
- candidate/integration SHA;
- command or scenario;
- zero integer exit code;
- environment fingerprint;
- an explicit passing observed result;
- timezone-qualified timestamp;
- artifact path and matching SHA-256 digest;
- acceptance criterion IDs proven;
- reviewer identity, verdict, and residual risks where required.

Every receipt is bound to the exact mission, entity, and candidate/integration SHA; identity fields are preserved and revalidated from stored state. The raw digested artifact must independently provide a recognized positive result—absence of failure or caller-authored `PASS` is not proof. Nonzero failure/error/exception counts, `pass except` clauses, unknown or indeterminate outcomes, unknown fields, plain-text success claims without an explicit result, failing or contradictory artifacts, missing fields, stale/mismatched digests, and incomplete criterion coverage are blocked. Missing evidence is `blocked`, never “almost done.” A later evidence-only commit may refer to an immutable implementation SHA only if the project validator understands and verifies that relationship.

## Human gates

Stop only for decisions humans actually own:

- Product objective or acceptance ambiguity.
- Paid spend or budget increase.
- Real customer/client data.
- External communication.
- Push, deploy, publish, release, or production mutation where approval is required.
- Risk acceptance.
- Final usability judgement.

Do not manufacture human gates for routine local dependencies, tests, commits, or the first bounded remediation.

## Common pitfalls

1. **Auto-decomposing a product brief.** A generic decomposer optimises graph production, not coherent capabilities. Compile milestones first.
2. **Treating reviewers as a pipeline.** Review is a risk control, not a mandatory card factory.
3. **Goal loops in one context.** Same-session Ralph loops accumulate blind spots. Use fresh slice contexts but durable external state.
4. **Unlimited eventual consistency.** More loops can converge, but only when the spec is correct and progress is measured externally. Bound retries.
5. **Mega-slices.** A slice spanning unrelated UI, auth, migrations, and workflows cannot be independently accepted.
6. **Micro-slices.** “Fix lint” and “update snapshot” are inner-loop steps, not outcomes.
7. **Board/repository drift.** Reconcile cards, worktrees, branches, evidence, and milestone state. A todo card with an active branch is a control-plane defect.
8. **Test-only success.** Passing builder-authored tests is weaker than an independently held user/system scenario.
9. **Parallel shared decisions.** Decide schema, file format, naming, and APIs once before workers start.
10. **No merge owner.** Parallel workers without one integrator produce branches, not a product.

## Verification checklist

- [ ] Product mission is bounded and approved.
- [ ] One active milestone has observable acceptance scenarios.
- [ ] Every durable slice yields a coherent user/system outcome.
- [ ] Micro-steps remain inside session todo and Git history.
- [ ] WIP is one milestone and at most two disjoint slices.
- [ ] Shared contracts and risk decisions precede parallel work.
- [ ] Focused tests run in the inner loop; full/scenario validation runs at the milestone gate.
- [ ] Review is selected by risk and targets an exact candidate.
- [ ] One remediation is allowed; repeat failure triggers replan.
- [ ] Progress is acceptance delta, not activity count.
- [ ] Completion has machine-readable evidence receipts.
- [ ] Human gates cover only actual human authority.
