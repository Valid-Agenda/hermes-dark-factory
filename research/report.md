# Research report: an opinionated dark factory for Hermes

**Date:** 2026-08-31
**Status:** Research synthesis and tested prototype
**Scope:** Hermes Agent plus comparative research on bounded autonomous software factories, milestone validation, and agentic delivery patterns

## Executive decision

Hermes can become the preferred dark-factory harness, but **not by adding more Kanban decomposition or longer goal loops**. The recurring failure is structural: the current system controls task activity while the project owner needs it to control product acceptance.

The recommended design is a thin, opinionated **milestone control plane** on top of existing Hermes primitives:

- one persistent frontier-model **integrator** owns mission intent and integration;
- one active milestone at a time;
- at most two disjoint functional blocks/workers;
- thin top-to-bottom slices and edit/test/debug micro-steps stay inside the active block and session;
- milestone acceptance is measured by held-out user/system scenarios;
- one risk-proportionate independent review targets an exact milestone candidate;
- a worker timeout/context boundary resumes the same block in a fresh session;
- repeated materially identical failure triggers **replanning**, not another card;
- Kanban is used for durability, dependencies and human gates, not as the coding loop;
- deterministic plugin guards enforce the parts prompts repeatedly fail to preserve.

This is materially closer to **Factory Missions + StrongDM scenario validation + Kilroy checkpoints**, with selected Gas Town durability patterns and Ralph’s fresh-context handoff. It deliberately rejects Gas Town’s scale and Ralph’s unlimited eventual-consistency loop. The durable unit is a complete functional block—for example, a complete home page with its login/payment wiring—not a thin end-to-end scaffold.

A working prototype exists in this repository. The research report describes design evidence only; it does not enable a profile or change an external board or repository.

## 1. What went wrong locally

### 1.1 The control variable was wrong

Across all three projects, the machinery optimised measurable local activity:

- card transitions;
- focused test results;
- reviewer verdicts;
- retries and respawns;
- commits and evidence files.

The intended controlled variable was different:

> **How many milestone acceptance criteria moved from unproven to proven?**

When those differ, the factory can look busy while the product does not advance.

### 1.2 Project A: the clearest micro-loop failure

The delivery plan reached the same conclusion independently: optimise for complete, testable product capability rather than card movement; use coherent functional blocks; do not create delivery cards for individual edits, test fixes or reviewer comments; review after meaningful blocks rather than each patch; and keep Kanban off by default.

Concrete evidence from anonymized internal postmortems and board audits:

- In one project, a high-priority card ran **62 times**: 53 blocked runs, seven 150-turn iteration-budget failures, one reclaim, then completion. The seven goal loops represented **1,050 allotted turns** before a bounded recovery checkpoint completed.
- A separate remediation recorded six crashes, one timeout and one gave-up outcome before completion.
- A review card blocked 13 times before completion, showing that unchanged candidates were being recycled rather than routed to one remediation decision.
- Automatic decomposition contradicted a single-thread policy and created duplicate specialist, compilation and consolidation-review layers.
- A reviewer breached read-only scope and modified product paths, forcing containment and recovery.
- A control-plane addition generated an evidence-format false negative despite a valid checkpoint and passing tests.
- Independent review still found real product defects, including an undiscardable second proposal and an unexpected network request in a supposedly local-only shell. A bounded remediation fixed both and a fresh review approved the new candidate.

**Interpretation:** review was valuable; the ritualised task/review machinery around every step was not. The successful recovery unit was a coherent checkpoint owned by one integrator.

The project-specific source documents and session records behind these observations are intentionally omitted from this public repository. The findings are retained as anonymized design evidence; no private paths, session IDs, task IDs, or commit identifiers are required to apply the controls described here.

### 1.3 Project B: leaf completion hid milestone failure

Project B produced real domain, security and UI foundations, but a later tranche optimised route migration and leaf-card closure rather than the original output-backed milestone. Its first slice was an integrated synthetic workflow: reviewed records → deterministic views → portfolio/workbench → document export, then ingestion/interrogation. The approved production contract retained a server-rendered core with additive frontend tooling, but the later tranche expanded the shell and every-workspace migration without a mandatory milestone rebaseline.

The most serious failure was **evidence semantics**:

- recorder-derived filmstrip evidence was replaced with settled screenshots and a video generated from those frames;
- that proved settled-state readability, not transition continuity;
- a fresh reviewer inspected the original raw video and found blank opening/late frames, incomplete formation/state evidence and polluted synthetic rows;
- another reviewer correctly refused closure because named measurements and a full test result were missing.

Other state failures:

- the board expanded to **87 cards**: 52 done, 25 archived and 10 still blocked, with 75 blocked runs, 47 links and 32 unlinks;
- stale candidates were repeatedly re-reviewed: one blocked 35 times on the same stale-revision defect and another 12 times; an Outputs review recorded five failures and then PASS against the same candidate without a visible acceptance reinterpretation;
- external task identifiers were confused with Hermes task identifiers;
- a review carrying blockers could still end in generic `done`;
- shared browser fixtures accumulated duplicate data because runs lacked isolated DB/runtime/port leases;
- one verdict could not be recorded because the work item was not claimed from review state;
- command-policy interruption around unattended script execution was not preflighted.

The productive part of the system must remain: fresh independent review found material provenance, scoring, scope, security, rendering and lifecycle defects. The correction is typed acceptance state and immutable evidence, not weaker review.

### 1.4 Project C: safety improved while integrated delivery stalled

Project C’s controls caught genuine authorization, public-token, migration and authority defects. The issue was that governance artifacts and retry state often became the work itself.

Key evidence from the read-only postmortem:

- evidence and independent reviews targeted different candidates, creating a manual re-evidence loop;
- a late authority correction should have been an executable prerequisite before implementation. Its evidence package covered **95 changed files**, showing that the “vertical slice” had grown beyond an independently reviewable outcome;
- a roster/service slice began as moderate risk but contained public response tokens, authorization, migration and personal-data disclosure. It was escalated to high risk only after implementation and audit exposed predictable tokens and broad reads;
- one work item became a 50-file, 4,539-addition mega-slice with no final evidence/review package at the inspected branch tip;
- a small harness change accumulated repeated configuration, test, reviewer-dispatch and recovery cycles;
- board state did not reconcile with active worktrees and branches, so dispatch safety could not be inferred from the board.

The corrective pattern is fast micro-steps inside a coherent task, evidence and review once at the risk-appropriate delivery boundary, and a human outcome/decision surface rather than an execution log.

Project-specific source documents and session records are intentionally omitted for public sharing.

## 2. Why Hermes falls into micro-loops

### 2.1 Proximity bias

The current failing test is immediate, concrete and machine-readable. Milestone intent is distant and usually prose. An agent optimises the nearest observable error until the harness makes milestone acceptance equally explicit and executable.

### 2.2 Kanban’s default ontology is task-centric

Hermes Kanban has task, parent, child, run, review, blocked and done states. It does not natively distinguish:

- worker finished;
- implementation candidate exists;
- reviewer requested changes;
- slice accepted;
- milestone accepted;
- mission complete.

A generic `done` therefore collapses execution completion and product acceptance.

### 2.3 The generic orchestrator is rewarded for decomposition

Hermes’ built-in orchestrator guidance says a well-behaved orchestrator does not implement; it decomposes, links, assigns and steps back. That is appropriate for independent research/operations pipelines. For tightly integrated software it creates a telephone game and leaves no accountable integration owner.

### 2.4 Auto-decomposition and creator wake-ups are multiplicative

Current Hermes documentation says:

- `kanban.auto_decompose` defaults to `true`;
- triage can fan out into a task graph;
- children can auto-promote;
- terminal task events can wake the creating session, which may create follow-ups.

These are useful general automation features. In a factory already prone to “one more fix/review card,” they amplify process faster than acceptance.

### 2.5 Goal mode repeats one context

Kanban goal mode runs a Ralph-style continuation judge inside the same worker session. This can help one open-ended card, but it does not supply fresh-context decorrelation or milestone semantics. One project's 1,050-iteration history demonstrates the danger of continuing the same local objective after its plan has stopped producing progress.

### 2.6 Reviewer ritual creates recursive work

“Implementer → spec reviewer → quality reviewer → fixer → re-reviewer” is safe-looking but expensive. When repeated for every small slice, review becomes an autonomous organisation rather than a risk control. Reviewer-of-reviewer loops are especially low value.

### 2.7 Evidence is often narrative rather than typed

A text report can say “browser evidence passed” even when the artifact proves a different property. Without exact candidate SHA, raw artifact digest, criterion IDs and verifier disposition, evidence can drift semantically while remaining syntactically present.

### 2.8 Dual SSOTs create phantom state

Plane and Kanban served different audiences but were not always explicitly mapped. Similar drift occurred between cards, branches, worktrees, task packets, evidence and review files. A dispatcher cannot safely resume work when those projections disagree.

## 3. What other systems get right

| System | Verified useful pattern | What not to copy |
|---|---|---|
| **Kiro Specs** | Requirements → design → tasks; dependency waves; versioned artifacts; requirement-linked property-based tests; choose standard gates for unfamiliar/compliance work. | Discrete tasks alone do not solve milestone acceptance and can still encourage micro-granularity. |
| **Kiro Crew** | Persistent cross-session workspace; checkpoints; schedules/webhooks/heartbeats; visible memory/lessons/skills; ACP observability; OS sandbox, denied commands, path controls, credential redaction and signed audit logs. | Persistence and memory are workspace capabilities, not a complete mission-control policy. More memory can also preserve a bad local objective. |
| **Factory Missions** | Collaborative upfront plan; features grouped into meaningful milestones; validation workers at milestone boundaries; user-facing QA; repository Agent Readiness prerequisite; worker/validator cost is planned. | It is explicitly not fire-and-forget and still expects human project-management intervention. Optimal parallelism and long-horizon correctness remain open questions; parts of the broader Software Factory/Release surface were Private Preview at the research date. |
| **Ralph** | Fresh context per outer loop; durable plan/specs/Git state; one task per iteration; deterministic context stack; tests as backpressure; plan is disposable when stale. | Unlimited eventual consistency, autonomous priority selection, unrelated bug fixing, and tiny “one context” stories reproduce the project owner’s micro-loop problem. |
| **StrongDM Software Factory** | Humans define intent and scenarios; scenarios act as a holdout set outside builder reach; externally observable satisfaction replaces self-authored test confidence; digital twins allow dangerous/high-volume validation; CXDB keeps immutable run history. | Extreme spend, no-human-review absolutism, and same-technology builder/judge blind spots are not acceptable defaults. Public material does not yet provide production defect rates, calibrated satisfaction thresholds, security-escape rates or controlled comparisons. |
| **Kilroy / Attractor** | English requirements compile to a validated deterministic graph; node-by-node checkpoints; isolated worktree; typed artifacts; stage timeout/stall/retry policy; resume from logs/CXDB/run branch; human gates. | A highly configurable graph DSL can become its own product. Hermes needs a small fixed mission schema first. |
| **Gas Town / Beads** | Work as structured data; durable roles/tasks with ephemeral sessions; one tracked “convoy” for the feature; workflow templates; explicit gates; dedicated merge queue; watchdog; attribution and model outcome history. | Dozens of agents, recursive decomposition, lore-heavy abstractions, constant nudging, Git-backed orchestration noise and high token burn. |
| **Trycycle** | Separate plan critique from implementation critique; iterate until defects are resolved. | “Perfect” is not a measurable stop condition. Without budgets and external acceptance it is another infinite loop. |

## 4. The proposed Hermes architecture

### 4.1 Mission Compiler

Input: approved product specification plus project policies.
Output: `.hermes/factory/manifest.json`.

The compiler rejects:

- vague outcomes;
- milestones without observable acceptance scenarios;
- slices without deterministic evidence;
- unresolved ownership/policy decisions;
- risk-tier mismatches;
- dependency cycles;
- unsafe parallel path overlap;
- unbounded retry/review policy.

### 4.2 Factory Ledger

A small deterministic state file records:

- mission, milestone and slice status;
- acceptance criteria proven;
- candidate/integration SHA;
- checks and scenario receipts;
- reviewer verdict;
- normalised failure fingerprints;
- remediation count;
- typed blockers and replan state;
- a revision counter, manifest digest, and process-local HMAC attestation that rejects out-of-band state mutation.

This complements Kanban rather than replacing it. Kanban remains the durable multi-profile queue. The ledger owns product acceptance semantics. It is not a second task/workflow database: production should store acceptance state either as version-controlled mission receipts or typed metadata/events on the Kanban root task, with one deterministic reconciler. Do not introduce another autonomous scheduler or an LLM cron controller. The v0.2 process-local signer deliberately makes restart fail closed and therefore confines this implementation to bounded dogfood; production requires Hermes-managed durable attestation and an audited resume operation.

### 4.3 Integrator

One persistent frontier-model session owns:

- mission acceptance contract;
- shared design decisions;
- task boundaries;
- schema/public contracts/cross-cutting state;
- integration;
- milestone scenarios;
- recovery and replanning.

Unlike the generic Kanban orchestrator, the integrator is allowed to understand and edit the implementation. Workers are bounded accelerators, not owners of product coherence.

### 4.4 Slice Scheduler

Defaults:

- one active milestone;
- two active slices maximum;
- two workers maximum;
- one active task per profile;
- no parallel overlapping paths or shared interface decisions;
- R3/R4 work requires named risk triggers and independent review;
- open prerequisite decisions block dispatch.

### 4.5 Evidence Vault

Every completion should produce a machine-readable receipt:

- mission/milestone/slice IDs;
- exact candidate SHA;
- command/scenario and environment fingerprint;
- raw artifact path and digest;
- pass/fail/flake result;
- acceptance criterion IDs proven;
- reviewer identity/verdict;
- timestamp and residual risk.

Raw recordings and diagnostics are immutable. Derived screenshots/contact sheets are additional artifacts, never replacements.

### 4.6 Acceptance Judge

At the milestone boundary, a fresh context or model executes held-out user/system scenarios. It evaluates observable behaviour and negative/recovery paths. Builder-authored tests remain necessary but insufficient. Record per-scenario outcomes and critical-scenario floors; a high average satisfaction score must never hide a zero on security, tenancy, data integrity, cost or publish authority.

### 4.7 Circuit Breaker

Replan when:

- the same normalised failure occurs twice;
- a second remediation/re-review is requested;
- a worker times out/returns partial state twice;
- the same user scenario fails twice despite local test changes;
- scope or risk changes invalidate the contract;
- activity creates no milestone acceptance delta.

Replanning revisits boundary, dependency, decision, evidence method or tool/model. It does not create “fix test again.”

### 4.8 Reconciler

A later production plugin should reconcile:

- factory ledger;
- Kanban card/run/review state;
- Git branch/worktree/SHA;
- evidence receipts;
- Plane mirror IDs;
- active processes/ports for browser work.

It should surface mismatches and block unsafe dispatch. It must not invent or auto-write human decisions.

## 5. Recommended Hermes defaults

For factory-controlled project profiles:

```yaml
kanban:
  auto_decompose: false
  auto_promote_children: false
  auto_subscribe_on_create: false
  max_in_progress: 2
  max_in_progress_per_profile: 1
  failure_limit: 2
```

Other policy:

- Kanban off by default for ordinary coding inside one integrator session.
- No goal-mode cards for implementation milestones.
- Kanban cards only for durable functional blocks, real dependencies, human gates or cross-session ownership.
- Independent verifier/adversary/holdout review targets the integrated milestone candidate, not each routine block.
- Fresh worker sessions may resume a block; repeated identical failure, not a timer, triggers replan.
- Full suite and realistic scenario validation at milestone boundaries, not each edit.

## 6. Tested prototype

Location: repository root

### Skill

`plugin/skills/dark-factory/SKILL.md` defines:

- mission → milestone → functional block → thin-slice/micro-step hierarchy;
- three nested loops;
- WIP and review policy;
- Kanban/delegation contracts;
- progress accounting;
- circuit breakers;
- evidence receipts and human gates.

### Plugin

`plugin/` registers nine tools:

1. `factory_preflight` — validate guided intake and model availability.
2. `factory_compile` — publish a validated manifest/state pair.
3. `factory_validate` — validate mission and existing state.
4. `factory_next` — compute safe next actions.
5. `factory_transition` — apply typed state transitions and evidence.
6. `factory_attest_review` — attest independent review receipts.
7. `factory_lint_card` — reject uncontracted durable cards.
8. `factory_beads_plan` — project a mission into a deterministic Beads graph plan.
9. `factory_beads_apply` — apply and verify that graph through the integrator boundary.

Opt-in strict guardrails activate only when both are set:

```bash
HERMES_FACTORY_STRICT=1
HERMES_FACTORY_MANIFEST=/absolute/path/.hermes/factory/manifest.json
```

Strict mode blocks:

- Kanban cards missing milestone/slice/outcome/acceptance/evidence/stop markers;
- delegation to more than two concurrent workers;
- worker briefs missing Acceptance/Evidence/Forbidden headings;
- Kanban completion without slice ID, candidate SHA, checks and acceptance results;
- reviewer-role file writes, preventing a verifier from silently becoming an implementer;
- inline heredoc scripts that bypass reviewable script artifacts.

It is intentionally inert by default.

### Deterministic state machine

The engine enforces:

- exact dependencies;
- one/two WIP policy;
- conservative path-overlap exclusion;
- open decision gates;
- high-risk keyword/risk-tier mismatch detection;
- mandatory R3/R4 review and named risk triggers;
- exact candidate SHA matching for review/completion;
- complete acceptance criterion coverage;
- repeated-failure fingerprints;
- one-remediation circuit breaker;
- milestone completion only after all slices and scenario receipts;
- scenario receipts must carry raw artifact paths, SHA-256 digests and criterion IDs so derived evidence cannot silently replace the original.

### v0.2 guided-intake and model gate

The follow-up implementation adds the missing product-definition and review configuration layer:

- dashboard and native-desktop plugin pages use the same profile-scoped backend;
- model choices come from Hermes' authenticated active-profile inventory, not a hard-coded catalogue;
- only provider/model references are persisted—never API keys, OAuth tokens, or credential material;
- integrator, builder, verifier, adversary, and holdout are explicit roles;
- verifier, adversary, and holdout must differ from the builder model;
- the Kryptonite adversarial lens is mandatory and cannot be disabled;
- preflight requires product context, personas, structured stories, negative/recovery criteria, non-goals, constraints, success metrics, mapped milestones, forced test commands, held-out scenarios, threats, and locked high-risk authority decisions;
- readiness score is advisory, while any blocker prevents compile/launch;
- the compiler emits a schema-v2 manifest and initial state only after every gate passes.

The backend uses the same `hermes_cli.inventory.build_models_payload` substrate as Hermes' model picker with `explicit_only=True`, `include_unconfigured=False`, and profile-local `HERMES_HOME` resolution. This avoids creating a second auth catalogue and fails clearly when a saved model is no longer available.

### Verification executed

- `python3 -m unittest discover -s tests -v`: **213 tests passed** on the release candidate.
- `python3 -m compileall -q plugin tests scripts`: passed.
- `node --check plugin/dashboard/dist/index.js` and `node --check plugin/desktop/plugin.js`: passed.
- `hermes plugins doctor ./plugin --ci`: passed; **9 tools, 1 hook** registered.
- Active-profile inventory smoke: isolated active profiles resolved their own authenticated provider/model catalogues with `credentials_included: false`.
- Isolated dashboard browser smoke: the six-step wizard rendered, draft save survived reload, the Kryptonite gate was visible, five model selectors were present, provider selection populated model choices, no credential input existed, and an incomplete compile failed closed with server blockers.

Representative fixtures validated:

- one fixture demonstrates two disjoint high-risk slices with a later dependent milestone.
- one fixture demonstrates serial domain authority followed by disjoint UI and export work.
- one fixture demonstrates a high-risk foundation blocked until an authority decision is locked.

## 7. Recommended rollout

### Phase 0 — completed: lab only

- Prototype and tests exist outside all live projects.
- Plugin installation is opt-in per Hermes profile; no live project state is bundled.
- No project profile, board, cron, Plane item or repo changed.

### Phase 1 — controlled dogfood

Use a bounded local synthetic product or the next newly approved milestone, not an in-flight epic. Acceptance:

- one milestone completes;
- no micro-cards are created;
- retry circuit breaker fires correctly in an injected failure;
- held-out scenario finds at least one seeded defect;
- token/time cost is measured against direct Sol-led delivery.

### Phase 2 — first local pilot

Use the safest local-only candidate with explicit output, security and browser contracts. Do not import historical task cards. Compile only the next approved milestone into the new manifest.

### Phase 3 — broader product pilot

Adopt only after the functional-block model is durably committed and historical dispatch remains parked. The plugin should reinforce the current plan, not reactivate old work.

### Phase 4 — high-assurance pilot

Add the stronger repository/task/lease/evidence reconciliation adapter and risk classifier before use. The existing high-assurance safety model must remain authoritative.

### Success/fail decision

Continue investing in Hermes if the pilot demonstrates:

- milestone acceptance progresses faster than direct Sol-led work;
- no repeated micro-loop exceeds its budget;
- evidence semantics survive independent review;
- integration ownership remains clear;
- cost is materially below Gas Town/StrongDM-style swarms;
- the project owner experiences the system as one coherent mission, not a board to nurse.

If Hermes cannot meet those criteria after a bounded pilot, **Factory Missions is the most relevant external harness to evaluate**, because its product model already centres features, milestones, validation cadence and Agent Readiness. Kiro Crew is a strong persistent workspace but less directly a milestone factory. Gas Town and raw Ralph loops are not suitable defaults for the project owner’s projects.

## 8. Source catalogue

### Primary/vendor sources

1. Kiro Specs: https://kiro.dev/docs/specs/
2. Kiro correctness/property-based testing: https://kiro.dev/docs/specs/correctness/
3. Kiro best practices: https://kiro.dev/docs/specs/best-practices/
4. Kiro Crew launch: https://kiro.dev/blog/introducing-kiro-crew/
5. Kiro Crew product/security details: https://kiro.dev/crew/
6. Factory Missions overview: https://docs.factory.ai/missions/overview
7. Factory Missions planning/validation: https://docs.factory.ai/missions/planning
8. Factory Agent Readiness: https://docs.factory.ai/agent-readiness/overview
9. Factory Mission Control: https://docs.factory.ai/missions/running-cli
10. StrongDM Software Factory: https://www.strongdm.com/blog/the-strongdm-software-factory-building-software-with-ai
11. Kilroy README: https://github.com/danshapiro/kilroy
12. Ralph primary article: https://ghuntley.com/ralph/
13. Ralph implementation/playbook: https://github.com/ghuntley/how-to-ralph-wiggum
14. Gas Town concepts: https://docs.gastownhall.ai/
15. Gas Town work management/formulas/gates/convoys: https://docs.gastownhall.ai/usage/work-management/
16. Gas Town design rationale: https://docs.gastownhall.ai/other/why-these-features/
17. Dan Shapiro’s five levels: https://www.danshapiro.com/blog/2026/01/the-five-levels-from-spicy-autocomplete-to-the-software-factory/
18. Trycycle comparison: https://www.danshapiro.com/blog/2026/03/dark-factories-rise-of-the-trycycle/
19. Hermes Kanban reference: https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban
20. Hermes plugins: https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins
21. Hermes hooks: https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks

### Independent analysis

22. Simon Willison on StrongDM scenarios and Digital Twin Universe: https://simonwillison.net/2026/Feb/7/software-factory/
23. Maggie Appleton on Gas Town patterns and design bottlenecks: https://maggieappleton.com/gastown

## Final opinion

The right answer is not “more agents” and not “smaller cards.” It is a better controlled system.

Hermes already has profiles, worktrees, review states, retries, hooks, tools, cron, durable Kanban, delegation and session persistence. The missing layer is a **mission compiler plus milestone acceptance ledger** that makes the product outcome harder to ignore than the latest failing test.

That layer is small enough to build and test. It is also strict enough to prevent the behaviour the project owner has repeatedly experienced. The prototype is a credible start; the next decision should be whether to run one bounded local pilot, not whether to unleash another autonomous board across all three projects.
