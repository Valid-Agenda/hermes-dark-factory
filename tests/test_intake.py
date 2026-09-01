from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugin.engine import FactoryError, _attest_state, initial_state, next_actions, save_transition, validate_manifest
from plugin.intake import (
    _publish_factory_pair,
    compile_manifest,
    compile_to_workspace,
    default_setup,
    load_setup,
    normalise_setup,
    resolve_setup_models,
    validate_intake,
)
from plugin.model_policy import DEFAULT_PRESET_ID
from hermes_test_stubs import ensure_inventory_module

ROOT = Path(__file__).resolve().parents[1]


def ready_setup(workspace: str) -> dict:
    setup = default_setup()
    setup.update(
        {
            "project_mode": "existing",
            "workspace_path": workspace,
            "personas": [
                {
                    "id": "editor",
                    "name": "Content editor",
                    "context": "Works inside a shared editorial workspace with tenant-scoped drafts",
                    "need": "Publish an approved article without exposing another tenant's content",
                }
            ],
            "user_stories": [
                {
                    "id": "US1",
                    "persona_id": "editor",
                    "want": "create and reopen an article draft",
                    "so_that": "approved work survives interruptions and later review",
                    "acceptance": [
                        {
                            "id": "US1-A1",
                            "type": "happy",
                            "statement": "The editor reopens the same durable draft after reload",
                        },
                        {
                            "id": "US1-A2",
                            "type": "negative",
                            "statement": "A different tenant cannot read or modify the protected draft",
                        },
                    ],
                    "paths": ["src/articles/**", "tests/articles/**"],
                }
            ],
            "non_goals": ["Production deployment and live customer migration"],
            "constraints": ["Preserve the existing API and tenant identity authority"],
            "milestones": [
                {
                    "id": "M1",
                    "outcome": "An editor safely creates, reloads, and reopens one durable article draft",
                    "story_ids": ["US1"],
                    "acceptance": [
                        {
                            "id": "M1-A1",
                            "type": "happy",
                            "statement": "The complete browser journey passes against the integrated candidate SHA",
                        }
                    ],
                }
            ],
        }
    )
    setup["product"] = {
        "name": "Editorial Drafts",
        "problem": "Editors lose work and risk crossing tenant boundaries during interrupted draft workflows",
        "outcome": "An authorized editor creates, reloads, and safely reopens one durable article draft",
        "context": "The product serves tenant-scoped editorial teams using an existing browser application and API",
        "existing_system": "A Python service and browser client already support authentication but draft persistence is incomplete",
        "success_metrics": ["The primary and cross-tenant acceptance journeys pass on the exact candidate SHA"],
        "surfaces": ["web ui"],
    }
    setup["testing"] = {
        "focused_commands": ["pytest tests/articles -q"],
        "integration_commands": ["pytest tests -q"],
        "browser_scenarios": [
            {
                "action": "Create a draft, reload the browser, and reopen it",
                "expected": "The same draft identifier and content remain visible",
            }
        ],
        "held_out_scenarios": [
            {
                "name": "Cross tenant denial",
                "given": "A second tenant has a valid authenticated session",
                "when": "That tenant requests the first tenant's draft identifier",
                "then": "The request is denied without returning protected draft content",
            }
        ],
        "evidence_requirements": ["Record candidate SHA, exit code, environment and artifact digest"],
    }
    setup["security"] = {
        "data_classification": "personal",
        "risk_triggers": ["authentication", "authorization", "tenant isolation", "personal data"],
        "threat_scenarios": [
            {
                "id": "TH-1",
                "name": "Cross tenant identifier guessing",
                "scenario": "A tenant guesses another tenant's protected draft identifier",
                "attack_surface": "Tenant scoped draft read boundary",
                "expected_control": "Authorization denies access without protected content disclosure",
            },
            {
                "id": "TH-2",
                "name": "Interrupted draft persistence",
                "scenario": "A draft write is interrupted between validation and persistence",
                "attack_surface": "Transactional draft persistence boundary",
                "expected_control": "The transaction fails atomically and can be safely retried",
            },
        ],
        "authority_decisions": [
            {
                "id": "D1",
                "statement": "Server-side tenant identity is the only ownership authority",
                "status": "locked",
            }
        ],
    }
    setup["models"] = {
        "integrator": {"provider": "alpha", "model": "integrator"},
        "builder": {"provider": "alpha", "model": "builder"},
        "verifier": {"provider": "beta", "model": "verifier"},
        "adversary": {"provider": "beta", "model": "adversary"},
        "holdout": {"provider": "beta", "model": "holdout"},
    }
    return setup


def catalog() -> dict:
    return {
        "providers": [
            {"slug": "alpha", "authenticated": True, "models": ["integrator", "builder"]},
            {"slug": "beta", "authenticated": True, "models": ["verifier", "adversary", "holdout"]},
            {"slug": "expired", "authenticated": False, "models": ["unavailable"]},
        ]
    }


class IntakeReadinessTests(unittest.TestCase):
    def test_default_setup_allowlists_model_policy_and_beads_execution_settings(self) -> None:
        setup = normalise_setup({
            "model_policy": {"preset": DEFAULT_PRESET_ID},
            "execution": {
                "graph_backend": "beads",
                "graph_mode": "plan",
                "beads_directory": "/tmp/isolated-beads",
                "reasoning_effort": {"orchestrator": "high", "worker": "medium"},
            },
        })
        self.assertEqual(setup["model_policy"], {"preset": DEFAULT_PRESET_ID})
        self.assertEqual(setup["execution"]["graph_backend"], "beads")
        self.assertEqual(setup["execution"]["graph_mode"], "plan")
        self.assertEqual(setup["execution"]["beads_directory"], "/tmp/isolated-beads")
        self.assertEqual(setup["execution"]["reasoning_effort"], {"orchestrator": "high", "worker": "medium"})

    def test_guided_unknown_model_and_execution_fields_fail_without_projection(self) -> None:
        mutations = {
            "unattended-dispatch": lambda value: value["execution"].update({"unattended_dispatch": True}),
            "reviewer-reasoning": lambda value: value["execution"]["reasoning_effort"].update({"reviewer": "high"}),
            "model-temperature": lambda value: value["models"]["integrator"].update({"temperature": 0}),
            "unexpected-model-field": lambda value: value["models"]["integrator"].update({"unexpected": True}),
            "unknown-model-role": lambda value: value["models"].update({"reviewer": {"provider": "beta", "model": "reviewer"}}),
            "unknown-model-policy": lambda value: value["model_policy"].update({"automatic_fallback": True}),
            "unknown-policy": lambda value: value["policy"].update({"unattended_dispatch": True}),
        }
        with tempfile.TemporaryDirectory() as tmp:
            for name, mutate in mutations.items():
                with self.subTest(name=name):
                    setup = ready_setup(tmp)
                    mutate(setup)
                    readiness = validate_intake(setup, model_catalog=catalog())
                    self.assertFalse(readiness["ready"], readiness)
                    self.assertIn("setup.unknown_field", {item["code"] for item in readiness["blockers"]})
                    with self.assertRaisesRegex(FactoryError, "unknown model/execution/policy field"):
                        normalise_setup(setup)
                    with self.assertRaisesRegex(FactoryError, "unknown model/execution/policy field"):
                        compile_manifest(setup, model_catalog=catalog())


    def test_resolver_executes_preset_without_overwriting_explicit_roles(self) -> None:
        setup = default_setup()
        setup["models"]["integrator"] = {"provider": "custom", "model": "chosen"}
        model_catalog = {
            "providers": [{
                "slug": "openai-codex",
                "authenticated": True,
                "models": ["gpt-5.6-sol-900k", "gpt-5.6-luna"],
            }]
        }
        resolved = resolve_setup_models(setup, model_catalog)
        self.assertEqual(resolved["models"]["integrator"], {"provider": "custom", "model": "chosen"})
        self.assertEqual(resolved["models"]["builder"], {"provider": "openai-codex", "model": "gpt-5.6-luna"})
        self.assertEqual(resolved["models"]["verifier"], {"provider": "", "model": ""})

    def test_empty_setup_is_blocked_with_guidance(self) -> None:
        result = validate_intake(default_setup(), model_catalog=catalog())
        self.assertFalse(result["ready"])
        self.assertLess(result["score"], 50)
        codes = {item["code"] for item in result["blockers"]}
        self.assertIn("stories.present", codes)
        self.assertIn("testing.holdout", codes)
        self.assertIn("models.builder", codes)
        self.assertTrue(all(item.get("help") for item in result["blockers"]))

    def test_complete_setup_is_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = validate_intake(ready_setup(tmp), model_catalog=catalog())
        self.assertTrue(result["ready"], result)
        self.assertEqual(result["score"], 100)
        self.assertEqual(result["risk"], "R3")
        self.assertEqual(result["blockers"], [])

    def test_guided_workspace_path_is_stripped_absolute_and_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            setup = ready_setup(f"  {Path(tmp) / 'missing-child' / '..'}  ")
            canonical = normalise_setup(setup)
            expected = str(Path(tmp).resolve())
            self.assertEqual(canonical["workspace_path"], expected)
            readiness = validate_intake(setup, model_catalog=catalog())
            manifest = compile_manifest(setup, model_catalog=catalog())
        self.assertTrue(readiness["ready"], readiness)
        self.assertEqual(manifest["mission"]["workspace_path"], expected)

    def test_guided_acceptance_ids_must_be_nonempty_and_unique(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for name, mutate in {
                "story-duplicate": lambda value: value["user_stories"][0]["acceptance"][1].update({"id": "US1-A1"}),
                "story-blank": lambda value: value["user_stories"][0]["acceptance"][0].update({"id": ""}),
                "story-missing-type": lambda value: value["user_stories"][0]["acceptance"][0].pop("type"),
                "milestone-invalid-type": lambda value: value["milestones"][0]["acceptance"][0].update({"type": "smoke"}),
                "milestone-duplicate": lambda value: value["milestones"][0]["acceptance"].append(
                    {"id": "M1-A1", "type": "happy", "statement": "The integrated journey remains observably safe after reload"}
                ),
            }.items():
                with self.subTest(name=name):
                    setup = ready_setup(tmp)
                    mutate(setup)
                    self.assertFalse(validate_intake(setup, model_catalog=catalog())["ready"])

    def test_local_compatibility_backend_is_ready_without_beads_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            setup = ready_setup(tmp)
            setup["execution"]["graph_backend"] = "local"
            setup["execution"]["graph_mode"] = "apply"
            setup["execution"]["beads_directory"] = "/definitely/not/a/beads/store"
            result = validate_intake(setup, model_catalog=catalog())
        self.assertTrue(result["ready"], result)
        self.assertFalse(any(item["code"] == "execution.beads.preflight" for item in result["blockers"]))
        self.assertEqual(result["risk"], "R3")
        self.assertEqual(result["blockers"], [])

    def test_beads_apply_blank_directory_preflights_workspace_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            setup = ready_setup(tmp)
            setup["execution"]["graph_mode"] = "apply"
            setup["execution"]["beads_directory"] = ""
            with patch("plugin.beads_adapter.preflight_beads", return_value={}) as preflight:
                result = validate_intake(setup, model_catalog=catalog())
        self.assertTrue(result["ready"], result)
        self.assertEqual(preflight.call_args.args[0], str(Path(tmp) / ".beads"))

    def test_execution_settings_are_canonical_across_readiness_compile_and_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            setup = ready_setup(tmp)
            setup["execution"].update({
                "graph_backend": " BeAdS ",
                "graph_mode": " ApPlY ",
                "beads_directory": "   ",
                "beads_isolated_authorized": False,
                "reasoning_effort": {"orchestrator": " HIGH ", "worker": " Medium "},
            })
            setup["models"]["integrator"] = {"provider": " ALPHA ", "model": " integrator "}
            with patch("plugin.beads_adapter.preflight_beads", return_value={}) as preflight:
                readiness = validate_intake(setup, model_catalog=catalog())
                manifest = compile_manifest(setup, model_catalog=catalog())
        from plugin import _beads_settings

        expected_directory = str(Path(tmp) / ".beads")
        runtime_directory, _, runtime_authorized = _beads_settings(manifest, {})
        self.assertTrue(readiness["ready"], readiness)
        self.assertEqual([call.args[0] for call in preflight.call_args_list], [expected_directory, expected_directory])
        self.assertEqual(runtime_directory, expected_directory)
        self.assertFalse(runtime_authorized)
        self.assertEqual(manifest["models"]["integrator"], {"provider": "alpha", "model": "integrator"})
        dispatch = next_actions(manifest, initial_state(manifest))["dispatch"]["startable_milestones"][0]
        self.assertEqual((dispatch["provider"], dispatch["model"]), ("alpha", "integrator"))
        self.assertEqual(manifest["execution"], {
            "graph_backend": "beads",
            "graph_mode": "apply",
            "beads_directory": "",
            "beads_isolated_authorized": False,
            "reasoning_effort": {"orchestrator": "high", "worker": "medium"},
        })

    def test_relative_beads_directory_resolves_against_canonical_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            setup = ready_setup(tmp)
            setup["execution"]["beads_directory"] = "var/../.beads-isolated"
            canonical = normalise_setup(setup)
            manifest = compile_manifest(setup, model_catalog=catalog())
            expected = str((Path(tmp) / ".beads-isolated").resolve())
        self.assertEqual(canonical["execution"]["beads_directory"], expected)
        self.assertEqual(manifest["execution"]["beads_directory"], expected)

    def test_unavailable_active_profile_model_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            setup = ready_setup(tmp)
            setup["models"]["adversary"] = {"provider": "expired", "model": "unavailable"}
            result = validate_intake(setup, model_catalog=catalog())
        self.assertFalse(result["ready"])
        self.assertTrue(any(item["code"] == "models.unavailable" for item in result["blockers"]))

    def test_mixed_inventory_keeps_only_exact_authenticated_refs(self) -> None:
        model_catalog = {
            "providers": [
                {"slug": "alpha", "models": ["integrator"]},
                {"slug": "alpha", "authenticated": None, "models": ["builder"]},
                {"slug": "beta", "authenticated": False, "models": ["verifier"]},
                {"slug": "beta", "authenticated": "true", "models": ["adversary"]},
                {"slug": "beta", "authenticated": True, "models": [{"id": "holdout"}]},
                {"slug": 123, "authenticated": True, "models": ["integrator"]},
                {"slug": "beta", "authenticated": True, "models": [456, {"model": "verifier"}]},
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            result = validate_intake(ready_setup(tmp), model_catalog=model_catalog)
        unavailable_paths = {
            item["path"] for item in result["blockers"] if item["code"] == "models.unavailable"
        }
        self.assertEqual(unavailable_paths, {
            "models.integrator",
            "models.builder",
            "models.verifier",
            "models.adversary",
            "models.holdout",
        })

    def test_builder_cannot_self_certify(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            setup = ready_setup(tmp)
            setup["models"]["verifier"] = copy.deepcopy(setup["models"]["builder"])
            result = validate_intake(setup, model_catalog=catalog())
        self.assertFalse(result["ready"])
        self.assertTrue(any(item["code"] == "models.verifier.independent" for item in result["blockers"]))

    def test_open_authority_decision_blocks_high_risk_launch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            setup = ready_setup(tmp)
            setup["security"]["authority_decisions"][0]["status"] = "open"
            result = validate_intake(setup, model_catalog=catalog())
        self.assertFalse(result["ready"])
        self.assertTrue(any(item["code"] == "security.decisions" for item in result["blockers"]))

    def test_guided_threat_decision_and_policy_bounds_are_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mutations = {
                "one-threat": lambda value: value["security"].update({"threat_scenarios": value["security"]["threat_scenarios"][:1]}),
                "blank-threat-id": lambda value: value["security"]["threat_scenarios"][0].update({"id": ""}),
                "unknown-severity-field": lambda value: value["security"]["threat_scenarios"][0].update({"severity": "high"}),
                "thin-threat-name": lambda value: value["security"]["threat_scenarios"][0].update({"name": "x"}),
                "thin-threat-scenario": lambda value: value["security"]["threat_scenarios"][0].update({"scenario": "y"}),
                "thin-attack-surface": lambda value: value["security"]["threat_scenarios"][0].update({"attack_surface": "x"}),
                "thin-expected-control": lambda value: value["security"]["threat_scenarios"][0].update({"expected_control": "y"}),
                "duplicate-threat": lambda value: value["security"]["threat_scenarios"][1].update({"id": "TH-1"}),
                "copy-threat-new-id": lambda value: value["security"]["threat_scenarios"].__setitem__(
                    1,
                    {**copy.deepcopy(value["security"]["threat_scenarios"][0]), "id": "TH-COPY"},
                ),
                "blank-decision-id": lambda value: value["security"]["authority_decisions"][0].update({"id": ""}),
                "one-character-decision": lambda value: value["security"]["authority_decisions"][0].update({"statement": "x"}),
                "duplicate-decision": lambda value: value["security"]["authority_decisions"].append(copy.deepcopy(value["security"]["authority_decisions"][0])),
                "active-milestones": lambda value: value["policy"].update({"max_active_milestones": 2}),
                "parallel-slices": lambda value: value["policy"].update({"max_parallel_slices": 3}),
                "failure-limit": lambda value: value["policy"].update({"repeated_failure_limit": 3}),
                "remediation-limit": lambda value: value["policy"].update({"max_remediation_cycles": 2}),
                "unknown-policy": lambda value: value["policy"].update({"unattended_dispatch": True}),
            }
            for name, mutate in mutations.items():
                with self.subTest(name=name):
                    setup = ready_setup(tmp)
                    mutate(setup)
                    self.assertFalse(validate_intake(setup, model_catalog=catalog())["ready"])

    def test_guided_threats_reject_unknown_fields_instead_of_projecting_them(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            setup = ready_setup(tmp)
            setup["security"]["threat_scenarios"][0]["severity"] = "high"
            readiness = validate_intake(setup, model_catalog=catalog())
            self.assertFalse(readiness["ready"], readiness)
            self.assertIn(
                "security.threat_scenarios[0].severity",
                {item["path"] for item in readiness["blockers"]},
            )
            with self.assertRaisesRegex(FactoryError, "unknown threat field"):
                normalise_setup(setup)
            with self.assertRaisesRegex(FactoryError, "unknown threat field"):
                compile_manifest(setup, model_catalog=catalog())

    def test_load_setup_migrates_legacy_saved_threat_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "setup.json"
            saved = default_setup()
            saved["security"]["threat_scenarios"] = [
                {
                    "threat": "A tenant guesses another tenant's draft identifier",
                    "severity": "high",
                    "expected_control": "Authorization denies access without protected content disclosure",
                },
                {
                    "description": "A write is interrupted between validation and persistence",
                    "mitigation": "The transaction fails atomically and can be safely retried",
                    "severity": "medium",
                },
            ]
            path.write_text(json.dumps(saved), encoding="utf-8")
            with patch("plugin.intake.setup_path", return_value=path):
                loaded = load_setup()

            self.assertEqual(
                loaded["security"]["threat_scenarios"],
                [
                    {
                        "id": "T1",
                        "name": "",
                        "scenario": "A tenant guesses another tenant's draft identifier",
                        "attack_surface": "",
                        "expected_control": "Authorization denies access without protected content disclosure",
                    },
                    {
                        "id": "T2",
                        "name": "",
                        "scenario": "A write is interrupted between validation and persistence",
                        "attack_surface": "",
                        "expected_control": "The transaction fails atomically and can be safely retried",
                    },
                ],
            )
            persisted = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(persisted, loaded)
            for threat in persisted["security"]["threat_scenarios"]:
                self.assertNotIn("threat", threat)
                self.assertNotIn("severity", threat)

    def test_guided_semantic_micro_mission_and_milestone_outcomes_are_blocked(self) -> None:
        exact_micro_outcomes = (
            "Change only the CSS color in the header for this release",
            "Update only one README sentence in this release",
            "Rename just one local variable in the parser",
            "Change only one source-code comment for this release",
        )
        functional_outcomes = (
            "An editor can change one article label and test that it persists after reload",
            "Allow users to change only their own profile color theme with persisted preferences and accessibility checks",
            "Generate versioned operator documentation from the API schema with validation checks",
            "Refactor parser internals so malformed input is rejected without corrupting durable state",
        )
        with tempfile.TemporaryDirectory() as tmp:
            for exact_micro in exact_micro_outcomes:
                for target in ("mission", "milestone"):
                    with self.subTest(outcome=exact_micro, target=target):
                        setup = ready_setup(tmp)
                        if target == "mission":
                            setup["product"]["outcome"] = exact_micro
                        else:
                            setup["milestones"][0]["outcome"] = exact_micro
                        self.assertFalse(validate_intake(setup, model_catalog=catalog())["ready"])
                        with self.assertRaisesRegex(
                            FactoryError,
                            "(?:micro-remediation|coherent, accepted product increment)",
                        ):
                            compile_manifest(setup, model_catalog=catalog())

            for functional in functional_outcomes:
                with self.subTest(functional=functional):
                    coherent = ready_setup(tmp)
                    coherent["product"]["outcome"] = functional
                    coherent["milestones"][0]["outcome"] = functional
                    readiness = validate_intake(coherent, model_catalog=catalog())
                    self.assertTrue(readiness["ready"], readiness)
                    self.assertTrue(
                        validate_manifest(
                            compile_manifest(coherent, model_catalog=catalog())
                        )["valid"]
                    )

    def test_every_story_requires_positive_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            setup = ready_setup(tmp)
            for criterion in setup["user_stories"][0]["acceptance"]:
                criterion["type"] = "negative"
            result = validate_intake(setup, model_catalog=catalog())
        self.assertFalse(result["ready"])
        self.assertTrue(any(item["code"] == "stories.complete" for item in result["blockers"]))

    def test_every_story_requires_nonempty_path_coordinates_before_compile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for paths in ([], [""], ["   "], [None], "src/articles/**"):
                with self.subTest(paths=paths):
                    setup = ready_setup(tmp)
                    setup["user_stories"][0]["paths"] = paths
                    result = validate_intake(setup, model_catalog=catalog())
                    self.assertFalse(result["ready"])
                    self.assertTrue(any(item["code"] == "story.paths" for item in result["blockers"]))
                    with self.assertRaisesRegex(FactoryError, "intake is not ready"):
                        compile_manifest(setup, model_catalog=catalog())

    def test_every_story_requires_its_own_negative_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            setup = ready_setup(tmp)
            second = copy.deepcopy(setup["user_stories"][0])
            second["id"] = "US2"
            second["acceptance"] = [
                {"id": "US2-A1", "type": "happy", "statement": "The editor reopens the saved draft and sees the expected content"},
                {"id": "US2-A2", "type": "happy", "statement": "The editor continues editing the reopened draft without data loss"},
            ]
            setup["user_stories"].append(second)
            setup["milestones"][0]["story_ids"].append("US2")
            result = validate_intake(setup, model_catalog=catalog())
        self.assertFalse(result["ready"])
        self.assertTrue(any(item["code"] == "stories.negative" for item in result["blockers"]))

    def test_interaction_surface_and_scenario_are_mandatory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            setup = ready_setup(tmp)
            setup["product"]["surfaces"] = []
            setup["testing"]["browser_scenarios"] = []
            result = validate_intake(setup, model_catalog=catalog())
        codes = {item["code"] for item in result["blockers"]}
        self.assertFalse(result["ready"])
        self.assertIn("product.surfaces", codes)
        self.assertIn("testing.browser", codes)

    def test_high_risk_global_text_cannot_be_downgraded_by_empty_security_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            setup = ready_setup(tmp)
            setup["security"]["data_classification"] = "none"
            setup["security"]["risk_triggers"] = []
            setup["security"]["authority_decisions"] = []
            result = validate_intake(setup, model_catalog=catalog())
        self.assertEqual(result["risk"], "R3")
        self.assertFalse(result["ready"])
        self.assertTrue(any(item["code"] == "security.decisions" for item in result["blockers"]))

    def test_benign_and_internal_missions_keep_independent_lower_risk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            setup = ready_setup(tmp)
            setup["product"].update({
                "name": "Reading Lists",
                "problem": "Readers need a dependable way to organize saved articles across ordinary browsing sessions",
                "outcome": "A reader creates and reopens a durable reading list after a page reload",
                "context": "Readers compare the time investment required by each gardening tutorial",
                "existing_system": "A Python service and browser client already store simple public article references",
                "success_metrics": ["The saved reading list reopens with the same article references"],
            })
            setup["personas"][0].update({
                "context": "Uses one ordinary reading workspace during daily research",
                "need": "Save and reopen useful article references across browsing sessions",
            })
            setup["user_stories"][0].update({
                "want": "create and reopen a reading list",
                "so_that": "saved article references remain available after a reload",
                "acceptance": [
                    {"id": "US1-A1", "type": "happy", "statement": "The reader reopens the same durable list after reload"},
                    {"id": "US1-A2", "type": "negative", "statement": "Malformed list input is rejected and existing entries remain unchanged"},
                ],
                "paths": ["src/lists/**", "tests/lists/**"],
            })
            setup["constraints"] = ["Preserve the existing reading list data format"]
            setup["milestones"][0].update({
                "outcome": "A reader creates, reloads, and reopens one durable reading list",
                "acceptance": [
                    {"id": "M1-A1", "type": "happy", "statement": "The complete list journey passes against the integrated candidate SHA"}
                ],
            })
            setup["testing"].update({
                "browser_scenarios": [{
                    "action": "Create a reading list, reload the page, and reopen it",
                    "expected": "The same article references remain visible after reload",
                }],
                "held_out_scenarios": [{
                    "name": "Malformed list input",
                    "given": "A reader has an existing saved list",
                    "when": "The reader submits malformed list input",
                    "then": "The existing list entries remain unchanged",
                }],
            })
            setup["security"].update({
                "data_classification": "none",
                "risk_triggers": [],
                "threat_scenarios": [{
                    "id": "TH-1",
                    "name": "Malformed list input",
                    "scenario": "Malformed list input attempts to alter existing saved entries",
                    "attack_surface": "Reading list input boundary",
                    "expected_control": "The request is rejected and existing list entries remain unchanged",
                }],
                "authority_decisions": [{
                    "id": "D1",
                    "statement": "Stored list entries remain the source for later reloads",
                    "status": "locked",
                }],
            })
            low = validate_intake(setup, model_catalog=catalog())
            high_risk_results = {}
            for label, prose in {
                "health": "Patients schedule medical appointments with clinicians and receive dosage instructions",
                "identity": "Users sign in with passwords and reset access",
                "financial": "Customers review bank balances and transfer money",
                "investment-context": "Reconcile customer financial transactions and investment portfolios.",
            }.items():
                setup["product"]["context"] = prose
                high_risk_results[label] = validate_intake(
                    setup, model_catalog=catalog()
                )
            setup["product"]["context"] = (
                "Readers compare the time investment required by each gardening tutorial"
            )
            setup["security"]["data_classification"] = "internal"
            internal = validate_intake(setup, model_catalog=catalog())
        self.assertTrue(low["ready"], low)
        self.assertEqual(low["risk"], "R1")
        for label, result in high_risk_results.items():
            with self.subTest(label=label):
                self.assertEqual(result["risk"], "R3")
        self.assertTrue(internal["ready"], internal)
        self.assertEqual(internal["risk"], "R2")

    def test_kryptonite_lens_cannot_be_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            setup = ready_setup(tmp)
            setup["security"]["adversarial_lens"] = "none"
            result = validate_intake(setup, model_catalog=catalog())
        self.assertFalse(result["ready"])
        self.assertTrue(any(item["code"] == "security.lens" for item in result["blockers"]))


class IntakeCompilationTests(unittest.TestCase):
    def test_compile_produces_schema_v2_manifest_with_forced_gates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = compile_manifest(ready_setup(tmp), model_catalog=catalog())
        result = validate_manifest(manifest)
        self.assertTrue(result["valid"], result)
        self.assertEqual(manifest["schema_version"], 2)
        self.assertTrue(manifest["security"]["mandatory_adversarial_review"])
        self.assertEqual(manifest["slices"][0]["review_roles"], ["verifier", "adversary"])
        self.assertEqual(manifest["slices"][0]["story_id"], "US1")
        self.assertEqual(manifest["milestones"][0]["story_ids"], ["US1"])
        story_acceptance = manifest["mission"]["user_stories"][0]["acceptance"]
        self.assertEqual(manifest["slices"][0]["acceptance"], story_acceptance)
        self.assertEqual(
            [list(criterion) for criterion in story_acceptance],
            [["id", "type", "statement"], ["id", "type", "statement"]],
        )
        self.assertEqual(
            [list(criterion) for criterion in manifest["milestones"][0]["acceptance"]],
            [["id", "type", "statement"]] * 3,
        )
        self.assertEqual(
            manifest["milestones"][0]["acceptance"],
            [ready_setup(tmp)["milestones"][0]["acceptance"][0], *story_acceptance],
        )
        self.assertEqual([criterion["type"] for criterion in story_acceptance], ["happy", "negative"])
        self.assertEqual(manifest["model_policy"]["roles"]["integrator"], "orchestrator")
        self.assertEqual(manifest["model_policy"]["roles"]["builder"], "worker")
        self.assertEqual(manifest["execution"]["graph_backend"], "beads")
        self.assertEqual(manifest["intake"], {
            "schema_version": 1,
            "readiness_score": 100,
            "user_authored_intent": True,
        })
        self.assertEqual(
            [(item["id"], item["statement"], item["status"]) for item in manifest["security"]["authority_decisions"]],
            [(item["id"], item["statement"], item["status"]) for item in manifest["decisions"]],
        )

    def test_actual_ui_shaped_high_risk_threats_compile_with_exact_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            setup = ready_setup(tmp)
            manifest = compile_manifest(setup, model_catalog=catalog())
        expected = ["id", "name", "scenario", "attack_surface", "expected_control"]
        self.assertEqual(
            [list(threat) for threat in manifest["security"]["threat_scenarios"]],
            [expected, expected],
        )
        self.assertTrue(validate_manifest(manifest)["valid"])

    def test_compile_appends_mapped_story_criteria_once_in_mapping_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            setup = ready_setup(tmp)
            second = copy.deepcopy(setup["user_stories"][0])
            second.update({
                "id": "US2",
                "acceptance": [
                    {"id": "US2-A1", "type": "happy", "statement": "The editor publishes the reviewed draft and sees its durable identifier"},
                    {"id": "US2-A2", "type": "recovery", "statement": "An interrupted publication preserves the prior reviewed draft without partial state"},
                ],
                "paths": ["src/publishing/**", "tests/publishing/**"],
            })
            setup["user_stories"].append(second)
            setup["milestones"][0]["story_ids"].append("US2")
            # One story criterion is already present locally and must not be duplicated.
            setup["milestones"][0]["acceptance"].append(
                copy.deepcopy(setup["user_stories"][0]["acceptance"][0])
            )
            manifest = compile_manifest(setup, model_catalog=catalog())

        local = setup["milestones"][0]["acceptance"]
        expected = [
            *local,
            setup["user_stories"][0]["acceptance"][1],
            *setup["user_stories"][1]["acceptance"],
        ]
        self.assertEqual(manifest["milestones"][0]["acceptance"], expected)
        self.assertEqual(
            len({item["id"] for item in manifest["milestones"][0]["acceptance"]}),
            len(manifest["milestones"][0]["acceptance"]),
        )
        self.assertTrue(validate_manifest(manifest)["valid"])

    def test_compile_to_workspace_writes_manifest_state_and_no_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "project"
            workspace.mkdir()
            with patch.dict("os.environ", {"HERMES_HOME": str(Path(tmp) / "hermes-home")}, clear=False):
                result = compile_to_workspace(ready_setup(str(workspace)), model_catalog=catalog())
            manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
            state = json.loads(Path(result["state_path"]).read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "armed")
        self.assertFalse(result["credentials_stored"])
        self.assertEqual(state["mission_id"], manifest["mission"]["id"])
        self.assertNotIn("api_key", json.dumps(manifest).lower())

    def test_recompile_refuses_to_destroy_non_pristine_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "project"
            workspace.mkdir()
            home = Path(tmp) / "hermes-home"
            with patch.dict("os.environ", {"HERMES_HOME": str(home)}, clear=False):
                first = compile_to_workspace(ready_setup(str(workspace)), model_catalog=catalog())
                state_path = Path(first["state_path"])
                state = json.loads(state_path.read_text(encoding="utf-8"))
                state["milestones"]["M1"]["status"] = "active"
                state["events"].append({"action": "start_milestone"})
                state_path.write_text(json.dumps(state), encoding="utf-8")
                with self.assertRaisesRegex(FactoryError, "attestation|non-pristine"):
                    compile_to_workspace(ready_setup(str(workspace)), model_catalog=catalog())
                preserved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(preserved["milestones"]["M1"]["status"], "active")

    def test_recompile_allows_an_exact_pristine_manifest_state_pair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "project"
            workspace.mkdir()
            with patch.dict("os.environ", {"HERMES_HOME": str(Path(tmp) / "hermes-home")}, clear=False):
                first = compile_to_workspace(ready_setup(str(workspace)), model_catalog=catalog())
                second = compile_to_workspace(ready_setup(str(workspace)), model_catalog=catalog())
            self.assertEqual(
                json.loads(Path(second["manifest_path"]).read_text(encoding="utf-8")),
                first["manifest"],
            )
            self.assertEqual(json.loads(Path(second["state_path"]).read_text())["revision"], 0)

    def test_visible_rollback_with_forged_attestation_cannot_recompile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "project"
            workspace.mkdir()
            with patch.dict("os.environ", {"HERMES_HOME": str(Path(tmp) / "hermes-home")}, clear=False):
                compiled = compile_to_workspace(ready_setup(str(workspace)), model_catalog=catalog())
                manifest_path = Path(compiled["manifest_path"])
                state_path = Path(compiled["state_path"])
                configured = compiled["manifest"]["models"]["integrator"]
                save_transition(
                    manifest_path,
                    state_path,
                    "M1",
                    "start_milestone",
                    trusted_actor={
                        "session_id": "rollback-forgery",
                        "provider": configured["provider"],
                        "model": configured["model"],
                    },
                )
                state = json.loads(state_path.read_text(encoding="utf-8"))
                state["revision"] = 0
                state["integrator_authority"] = None
                state["milestones"]["M1"]["status"] = "pending"
                state["events"] = []
                state["state_attestation"] = "0" * 64
                state_path.write_text(json.dumps(state), encoding="utf-8")
                manifest_before = manifest_path.read_bytes()
                state_before = state_path.read_bytes()
                with self.assertRaisesRegex(FactoryError, "attestation"):
                    compile_to_workspace(ready_setup(str(workspace)), model_catalog=catalog())
                self.assertEqual(manifest_path.read_bytes(), manifest_before)
                self.assertEqual(state_path.read_bytes(), state_before)

    def test_superficially_pristine_state_with_invalid_hmac_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "project"
            workspace.mkdir()
            with patch.dict("os.environ", {"HERMES_HOME": str(Path(tmp) / "hermes-home")}, clear=False):
                compiled = compile_to_workspace(ready_setup(str(workspace)), model_catalog=catalog())
                manifest_path = Path(compiled["manifest_path"])
                state_path = Path(compiled["state_path"])
                state = json.loads(state_path.read_text(encoding="utf-8"))
                state["state_attestation"] = "0" * 64
                state_path.write_text(json.dumps(state), encoding="utf-8")
                manifest_before = manifest_path.read_bytes()
                state_before = state_path.read_bytes()
                with self.assertRaisesRegex(FactoryError, "attestation"):
                    compile_to_workspace(ready_setup(str(workspace)), model_catalog=catalog())
                self.assertEqual(manifest_path.read_bytes(), manifest_before)
                self.assertEqual(state_path.read_bytes(), state_before)

    def test_invalid_existing_pairs_are_rejected_without_changing_either_file(self) -> None:
        mutations = (
            "unreadable-state",
            "unexpected-state-field",
            "omitted-state-field",
            "stale-manifest",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp) / "project"
                workspace.mkdir()
                with patch.dict("os.environ", {"HERMES_HOME": str(Path(tmp) / "hermes-home")}, clear=False):
                    compiled = compile_to_workspace(ready_setup(str(workspace)), model_catalog=catalog())
                    manifest_path = Path(compiled["manifest_path"])
                    state_path = Path(compiled["state_path"])
                    if mutation == "unreadable-state":
                        state_path.write_text("{not-json", encoding="utf-8")
                    elif mutation == "stale-manifest":
                        stale = json.loads(manifest_path.read_text(encoding="utf-8"))
                        stale["mission"]["outcome"] += " with stale semantics"
                        manifest_path.write_text(json.dumps(stale), encoding="utf-8")
                    else:
                        state = json.loads(state_path.read_text(encoding="utf-8"))
                        if mutation == "unexpected-state-field":
                            state["unexpected"] = True
                        else:
                            state.pop("updated_at")
                        _attest_state(state)
                        state_path.write_text(json.dumps(state), encoding="utf-8")
                    manifest_before = manifest_path.read_bytes()
                    state_before = state_path.read_bytes()
                    with self.assertRaises(FactoryError):
                        compile_to_workspace(ready_setup(str(workspace)), model_catalog=catalog())
                    self.assertEqual(manifest_path.read_bytes(), manifest_before)
                    self.assertEqual(state_path.read_bytes(), state_before)

    def test_progressed_pair_with_only_state_deleted_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "project"
            workspace.mkdir()
            home = Path(tmp) / "hermes-home"
            with patch.dict("os.environ", {"HERMES_HOME": str(home)}, clear=False):
                compiled = compile_to_workspace(ready_setup(str(workspace)), model_catalog=catalog())
                manifest_path = Path(compiled["manifest_path"])
                state_path = Path(compiled["state_path"])
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                configured = manifest["models"]["integrator"]
                transition_result = save_transition(
                    manifest_path,
                    state_path,
                    "M1",
                    "start_milestone",
                    trusted_actor={
                        "session_id": "intake-test-integrator",
                        "provider": configured["provider"],
                        "model": configured["model"],
                    },
                )
                self.assertEqual(transition_result["revision"], 1)
                manifest_before = manifest_path.read_bytes()
                state_path.unlink()
                with self.assertRaisesRegex(FactoryError, "recovery|audited reset"):
                    compile_to_workspace(ready_setup(str(workspace)), model_catalog=catalog())
            self.assertEqual(manifest_path.read_bytes(), manifest_before)
            self.assertFalse(state_path.exists())

    def test_manifest_only_and_state_only_factory_pairs_fail_closed(self) -> None:
        for existing_name, missing_name in (
            ("manifest.json", "state.json"),
            ("state.json", "manifest.json"),
        ):
            with self.subTest(existing_name=existing_name), tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp) / "project"
                factory_dir = workspace / ".hermes" / "factory"
                factory_dir.mkdir(parents=True)
                existing_path = factory_dir / existing_name
                missing_path = factory_dir / missing_name
                existing_path.write_text('{"interrupted": true}\n', encoding="utf-8")
                before = existing_path.read_bytes()
                with patch.dict("os.environ", {"HERMES_HOME": str(Path(tmp) / "hermes-home")}, clear=False):
                    with self.assertRaisesRegex(FactoryError, "recovery|audited reset"):
                        compile_to_workspace(ready_setup(str(workspace)), model_catalog=catalog())
                self.assertEqual(existing_path.read_bytes(), before)
                self.assertFalse(missing_path.exists())

    def test_profile_metadata_failure_returns_armed_with_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "project"
            workspace.mkdir()
            with patch.dict("os.environ", {"HERMES_HOME": str(Path(tmp) / "hermes-home")}, clear=False), patch(
                "plugin.intake.save_setup", side_effect=OSError("metadata unavailable")
            ):
                result = compile_to_workspace(ready_setup(str(workspace)), model_catalog=catalog())
            self.assertEqual(result["status"], "armed")
            self.assertTrue(result["warnings"])
            self.assertTrue(Path(result["manifest_path"]).exists())
            self.assertTrue(Path(result["state_path"]).exists())

    def test_invalid_policy_blocks_before_writing_manifest_or_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "project"
            workspace.mkdir()
            setup = ready_setup(str(workspace))
            setup["policy"]["max_parallel_slices"] = 0
            readiness = validate_intake(setup, model_catalog=catalog())
            self.assertFalse(readiness["ready"])
            self.assertTrue(any(item["code"] == "policy.valid" for item in readiness["blockers"]))
            with self.assertRaisesRegex(FactoryError, "intake is not ready"):
                compile_to_workspace(setup, model_catalog=catalog())
            factory_dir = workspace / ".hermes" / "factory"
            self.assertFalse((factory_dir / "manifest.json").exists())
            self.assertFalse((factory_dir / "state.json").exists())

    def test_pair_publication_failure_leaves_no_partial_factory(self) -> None:
        import plugin.intake as intake_module

        with tempfile.TemporaryDirectory() as tmp:
            factory_dir = Path(tmp) / ".hermes" / "factory"
            original_write = intake_module._atomic_write_json

            def fail_manifest(path: Path, value: dict) -> None:
                if path.name == "manifest.json":
                    raise OSError("simulated publication failure")
                original_write(path, value)

            with patch("plugin.intake._atomic_write_json", side_effect=fail_manifest):
                with self.assertRaisesRegex(OSError, "simulated publication failure"):
                    _publish_factory_pair(factory_dir, {"schema_version": 2}, {"mission_id": "M"})
            self.assertFalse(factory_dir.exists())
            self.assertFalse(list(factory_dir.parent.glob(".factory-stage-*")))
            self.assertFalse(list(factory_dir.parent.glob(".factory-backup-*")))

    def test_pair_publication_failure_preserves_existing_pair(self) -> None:
        import plugin.intake as intake_module

        with tempfile.TemporaryDirectory() as tmp:
            factory_dir = Path(tmp) / ".hermes" / "factory"
            factory_dir.mkdir(parents=True)
            (factory_dir / "manifest.json").write_text('{"old": true}\n', encoding="utf-8")
            (factory_dir / "state.json").write_text('{"old": true}\n', encoding="utf-8")
            original_write = intake_module._atomic_write_json

            def fail_manifest(path: Path, value: dict) -> None:
                if path.name == "manifest.json":
                    raise OSError("simulated publication failure")
                original_write(path, value)

            with patch("plugin.intake._atomic_write_json", side_effect=fail_manifest):
                with self.assertRaisesRegex(OSError, "simulated publication failure"):
                    _publish_factory_pair(factory_dir, {"schema_version": 2}, {"mission_id": "M"})
            self.assertEqual(json.loads((factory_dir / "manifest.json").read_text()), {"old": True})
            self.assertEqual(json.loads((factory_dir / "state.json").read_text()), {"old": True})


class DashboardModelOptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        ensure_inventory_module()
        api_path = ROOT / "plugin" / "dashboard" / "plugin_api.py"
        spec = importlib.util.spec_from_file_location("dark_factory_dashboard_api", api_path)
        assert spec and spec.loader
        cls.api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.api)

    def test_model_options_only_return_authenticated_rows_and_no_credentials(self) -> None:
        payload = {
            "provider": "alpha",
            "model": "builder",
            "providers": [
                {"slug": "alpha", "name": "Alpha", "authenticated": True, "models": ["builder"]},
                {"slug": "missing", "name": "Missing", "models": ["missing-model"]},
                {"slug": "null", "name": "Null", "authenticated": None, "models": ["null-model"]},
                {"slug": "expired", "name": "Expired", "authenticated": False, "models": ["old"]},
                {"slug": "string", "name": "String", "authenticated": "true", "models": ["string-model"]},
                {"slug": "integer", "name": "Integer", "authenticated": 1, "models": ["integer-model"]},
            ],
        }
        with patch("hermes_cli.inventory.load_picker_context", return_value=object()), patch(
            "hermes_cli.inventory.build_models_payload", return_value=payload
        ):
            result = self.api._model_options()
        self.assertEqual([row["slug"] for row in result["providers"]], ["alpha"])
        self.assertFalse(result["credentials_included"])
        self.assertNotIn("token", json.dumps(result).lower())
        self.assertNotIn("api_key", json.dumps(result).lower())
        self.assertEqual(result["model_policy"]["default_preset"], DEFAULT_PRESET_ID)
        preset = result["model_policy"]["presets"][0]
        self.assertEqual(preset["roles"]["integrator"]["execution_role"], "orchestrator")

    def test_model_options_skip_malformed_inventory_refs_without_fallback(self) -> None:
        payload = {
            "provider": {"slug": "malformed-current"},
            "model": 42,
            "providers": [
                {"slug": 123, "authenticated": True, "models": ["bad-provider"]},
                {"slug": "wrong-model-container", "authenticated": True, "models": "not-a-list"},
                {
                    "slug": "valid",
                    "authenticated": True,
                    "models": [
                        123,
                        {"id": 456, "model": "fallback-model"},
                        {"name": "fallback-name"},
                        {"id": "canonical-dict-id"},
                        {"model": "fallback-model-string"},
                        "real-shape-string-id",
                        "real-shape-string-id",
                    ],
                },
            ],
        }
        with patch("hermes_cli.inventory.load_picker_context", return_value=object()), patch(
            "hermes_cli.inventory.build_models_payload", return_value=payload
        ):
            result = self.api._model_options()
        self.assertEqual(result["current"], {"provider": "", "model": ""})
        self.assertEqual(result["providers"], [{
            "slug": "valid",
            "label": "valid",
            "authenticated": True,
            "models": ["real-shape-string-id"],
        }])
        self.assertFalse(result["credentials_included"])

    def test_compile_endpoint_fails_closed_on_incomplete_setup(self) -> None:
        from fastapi import HTTPException

        with patch.object(self.api, "_model_options", return_value=catalog()):
            with self.assertRaises(HTTPException) as raised:
                self.api.compile_factory({"setup": default_setup()})
        self.assertEqual(raised.exception.status_code, 422)
        self.assertFalse(raised.exception.detail["readiness"]["ready"])

    def test_compile_accepts_raw_web_payload_and_wrapped_desktop_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            setup = ready_setup(tmp)
            compiled = {
                "status": "armed",
                "manifest_path": str(Path(tmp) / "manifest.json"),
                "state_path": str(Path(tmp) / "state.json"),
                "credentials_stored": False,
            }
            for body in (setup, {"setup": setup}):
                with self.subTest(shape="wrapped" if "setup" in body else "raw"), patch.object(
                    self.api, "_model_options", return_value={"profile": "default", **catalog(), "current": {}}
                ), patch.object(self.api, "compile_to_workspace", return_value=compiled) as compile_call:
                    result = self.api.compile_factory(body)
                self.assertTrue(result["ready"])
                compile_call.assert_called_once()
                self.assertEqual(compile_call.call_args.args[0]["product"]["name"], "Editorial Drafts")

    def test_regulatory_alias_is_canonicalised(self) -> None:
        setup = default_setup()
        setup["security"]["data_classification"] = "regulatory"
        canonical = normalise_setup(setup)
        self.assertEqual(canonical["security"]["data_classification"], "regulated")
        self.assertNotIn("security.classification", [item["code"] for item in validate_intake(canonical)["blockers"]])

    def test_credential_shaped_keys_and_values_are_rejected_without_echo(self) -> None:
        sentinel = "DO-NOT-ECHO-THIS-CREDENTIAL"
        mutations = {
            "key": lambda value: value.update({"api_key": sentinel}),
            "bearer": lambda value: value["product"].update({"context": "Bearer " + "A" * 24}),
            "openai": lambda value: value["constraints"].append("sk-" + "A" * 24),
            "github": lambda value: value["non_goals"].append("gh" + "p_" + "A" * 24),
            "slack": lambda value: value["product"].update({"context": "xoxb-" + "A" * 24}),
            "jwt": lambda value: value["product"].update({"context": "eyJ" + "A" * 12 + "." + "B" * 12 + "." + "C" * 12}),
            "private-key": lambda value: value["product"].update({"context": "-----BEGIN PRIVATE KEY-----\n" + sentinel}),
            "url": lambda value: value["product"].update({"context": "https://user:password@example.test/path"}),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                setup = default_setup()
                mutate(setup)
                with self.assertRaisesRegex(FactoryError, "credential-shaped data") as raised:
                    normalise_setup(setup)
                self.assertNotIn(sentinel, str(raised.exception))

    def test_supported_dashboard_context_survives_allowlisted_normalisation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            setup = ready_setup(tmp)
            setup["milestones"][0]["title"] = "Draft editing milestone"
            setup["milestones"][0]["evidence"] = ["Raw browser artifact and command receipt"]
            setup["security"]["data"] = ["Tenant-scoped draft content"]
            setup["security"]["controls"] = ["Server-side tenant ownership checks"]
            setup["security"]["human_gates"] = ["Human approval before publication"]
            canonical = normalise_setup(setup)
        self.assertEqual(canonical["milestones"][0]["evidence"], ["Raw browser artifact and command receipt"])
        self.assertEqual(canonical["security"]["controls"], ["Server-side tenant ownership checks"])
        self.assertEqual(canonical["security"]["human_gates"], ["Human approval before publication"])

    def test_setup_endpoint_saves_only_normalised_setup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            self.api, "_model_options", return_value={"profile": "validator", **catalog(), "current": {}}
        ), patch.object(self.api, "save_setup", side_effect=lambda value: value) as save:
            result = self.api.put_setup({"setup": ready_setup(tmp)})
        self.assertEqual(result["profile"], "validator")
        self.assertTrue(result["readiness"]["ready"])
        saved = save.call_args.args[0]
        self.assertEqual(saved["models"]["builder"], {"provider": "alpha", "model": "builder"})


if __name__ == "__main__":
    unittest.main()
