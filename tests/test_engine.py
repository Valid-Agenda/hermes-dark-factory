from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugin import _beads_settings, _handle_attest_review, _handle_beads_apply, _handle_next, _handle_validate, _pre_tool_guard
from plugin.engine import (
    FactoryError,
    _attest_state,
    _paths_overlap,
    _validate_state_compatibility,
    _validate_check_receipts,
    _validate_holdout_review,
    _validate_scenario_receipts,
    cli_attestation_key_context,
    derive_independent_mission_risk,
    failure_fingerprint,
    initial_state,
    issue_review_receipt,
    lint_card,
    next_actions,
    save_transition,
    transition,
    validate_manifest,
)

from plugin.model_policy import manifest_model_policy

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = json.loads((ROOT / "templates" / "manifest.example.json").read_text(encoding="utf-8"))
_DEFAULT_ACTOR = object()


def integrator_actor(manifest: dict, session_id: str = "integrator-session") -> dict[str, str]:
    configured = manifest["models"]["integrator"]
    return {
        "session_id": session_id,
        "provider": configured["provider"],
        "model": configured["model"],
    }


def builder_actor(manifest: dict, session_id: str = "builder-session") -> dict[str, str]:
    configured = manifest["models"]["builder"]
    return {
        "session_id": session_id,
        "provider": configured["provider"],
        "model": configured["model"],
    }


class ManifestTests(unittest.TestCase):
    def test_example_is_valid(self) -> None:
        result = validate_manifest(TEMPLATE)
        self.assertTrue(result["valid"], result)

    def test_micro_outcome_is_an_error_but_functional_outcome_passes(self) -> None:
        manifest = copy.deepcopy(TEMPLATE)
        manifest["slices"][0]["outcome"] = "Fix the test quickly right now"
        result = validate_manifest(manifest)
        self.assertFalse(result["valid"])
        self.assertTrue(any("micro-remediation" in item for item in result["errors"]))

        functional = copy.deepcopy(TEMPLATE)
        functional["slices"][0]["outcome"] = (
            "An authorized editor reopens the durable project after a browser reload"
        )
        self.assertTrue(validate_manifest(functional)["valid"])

        repair = copy.deepcopy(TEMPLATE)
        repair["slices"][0]["outcome"] = "Repair one failing unit test for login"
        self.assertFalse(validate_manifest(repair)["valid"])

        functional_repair = copy.deepcopy(TEMPLATE)
        functional_repair["slices"][0]["outcome"] = (
            "Repair login persistence so verified users can reopen durable projects"
        )
        self.assertTrue(validate_manifest(functional_repair)["valid"])

    def test_semantic_micro_outcomes_are_rejected_across_every_durable_level(self) -> None:
        exact_micro_outcomes = (
            "Change only the CSS color in the header for this release",
            "Update only one README sentence in this release",
            "Rename just one local variable in the parser",
            "Change only one source-code comment for this release",
        )
        for exact_micro in exact_micro_outcomes:
            for target in ("mission", "milestone", "slice"):
                with self.subTest(outcome=exact_micro, target=target):
                    manifest = copy.deepcopy(TEMPLATE)
                    if target == "mission":
                        manifest["mission"]["outcome"] = exact_micro
                    elif target == "milestone":
                        manifest["milestones"][0]["outcome"] = exact_micro
                    else:
                        manifest["slices"][0]["outcome"] = exact_micro
                    result = validate_manifest(manifest)
                    self.assertFalse(result["valid"], result)
                    self.assertTrue(any("micro-remediation" in item for item in result["errors"]))

        micro_variants = (
            "Edit just the header label for this release",
            "Modify one string in the footer for this release",
            "Make a single cosmetic CSS edit for this release",
            "Alter an isolated snapshot assertion for this release",
            "Swap only the header color for this release",
            "Set just one CSS property for this release",
            "Replace one typo string in the header for this release",
            "Rename only the header label in this release",
            "Tweak just one test assertion for this release",
            "Adjust an isolated CSS rule for this release",
        )
        for outcome in micro_variants:
            with self.subTest(outcome=outcome):
                manifest = copy.deepcopy(TEMPLATE)
                manifest["slices"][0]["outcome"] = outcome
                result = validate_manifest(manifest)
                self.assertFalse(result["valid"], result)
                self.assertTrue(any("micro-remediation" in item for item in result["errors"]))

        functional_outcomes = (
            "An editor can change one article label and test that it persists after reload",
            "Allow users to change only their own profile color theme with persisted preferences and accessibility checks",
            "Generate versioned operator documentation from the API schema with validation checks",
            "Refactor parser internals so malformed input is rejected without corrupting durable state",
        )
        for functional_outcome in functional_outcomes:
            with self.subTest(functional_outcome=functional_outcome):
                coherent = copy.deepcopy(TEMPLATE)
                coherent["mission"]["outcome"] = functional_outcome
                coherent["milestones"][0]["outcome"] = functional_outcome
                coherent["slices"][0]["outcome"] = functional_outcome
                self.assertTrue(validate_manifest(coherent)["valid"])

    def test_missing_evidence_fails(self) -> None:
        manifest = copy.deepcopy(TEMPLATE)
        manifest["slices"][0]["evidence"] = []
        result = validate_manifest(manifest)
        self.assertFalse(result["valid"])
        self.assertTrue(any("evidence" in item for item in result["errors"]))

    def test_high_risk_surface_cannot_be_declared_low_risk(self) -> None:
        manifest = copy.deepcopy(TEMPLATE)
        manifest["slices"][0]["risk"] = "R2"
        result = validate_manifest(manifest)
        self.assertFalse(result["valid"])
        self.assertTrue(any("high-risk surface" in item for item in result["errors"]))

    def test_high_risk_block_can_use_milestone_scoped_review(self) -> None:
        manifest = copy.deepcopy(TEMPLATE)
        manifest["slices"][0]["review_required"] = False
        manifest["slices"][0]["review_roles"] = []
        result = validate_manifest(manifest)
        self.assertTrue(result["valid"], result)

    def test_schema_v2_cannot_bypass_required_intake_controls(self) -> None:
        mutations = {
            "surfaces": lambda value: value["mission"].update({"surfaces": []}),
            "interaction": lambda value: value["testing"].update({"browser_scenarios": []}),
            "decisions": lambda value: value.update({"decisions": []}),
            "boolean-policy": lambda value: value["policy"].update({"max_parallel_slices": True}),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                manifest = copy.deepcopy(TEMPLATE)
                mutate(manifest)
                self.assertFalse(validate_manifest(manifest)["valid"])

    def test_schema_v2_requires_exact_canonical_model_policy_and_execution(self) -> None:
        self.assertEqual(TEMPLATE["model_policy"], manifest_model_policy("sol-luna"))
        mutations = {
            "missing-policy": lambda value: value.pop("model_policy"),
            "wrong-preset": lambda value: value["model_policy"].update({"preset": "other"}),
            "extra-policy-field": lambda value: value["model_policy"].update({"fallback_model": "other"}),
            "missing-role-mapping": lambda value: value["model_policy"]["roles"].pop("holdout"),
            "wrong-independent-roles": lambda value: value["model_policy"].update({"independent_from_builder": ["verifier"]}),
            "automatic-fallback": lambda value: value["model_policy"].update({"automatic_fallback": True}),
            "missing-execution": lambda value: value.pop("execution"),
            "provider-whitespace": lambda value: value["models"]["integrator"].update({"provider": " ALPHA "}),
            "model-whitespace": lambda value: value["models"]["integrator"].update({"model": " integrator-model "}),
            "backend-whitespace": lambda value: value["execution"].update({"graph_backend": " BEADS "}),
            "mode-whitespace": lambda value: value["execution"].update({"graph_mode": " APPLY "}),
            "directory-whitespace": lambda value: value["execution"].update({"beads_directory": " /tmp/beads "}),
            "string-authorization": lambda value: value["execution"].update({"beads_isolated_authorized": "false"}),
            "invalid-reasoning": lambda value: value["execution"]["reasoning_effort"].update({"orchestrator": "TURBO"}),
            "unknown-execution": lambda value: value["execution"].update({"unattended_dispatch": True}),
            "unknown-reasoning": lambda value: value["execution"]["reasoning_effort"].update({"reviewer": "high"}),
            "unknown-model-role": lambda value: value["models"].update({"reviewer": {"provider": "beta", "model": "reviewer"}}),
            "unexpected-model-field": lambda value: value["models"]["integrator"].update({"unexpected": True}),
            "temperature-model-field": lambda value: value["models"]["integrator"].update({"temperature": 0}),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                manifest = copy.deepcopy(TEMPLATE)
                mutate(manifest)
                self.assertFalse(validate_manifest(manifest)["valid"])

    def test_schema_v2_rejects_unknown_fields_recursively_with_path_only_errors(self) -> None:
        sentinel = "DO-NOT-ECHO-UNKNOWN-VALUE"
        mutations = (
            ("manifest", lambda value: value.update({"unexpected": sentinel})),
            ("mission", lambda value: value["mission"].update({"unexpected": sentinel})),
            ("mission.personas[0]", lambda value: value["mission"]["personas"][0].update({"unexpected": sentinel})),
            ("mission.user_stories[0]", lambda value: value["mission"]["user_stories"][0].update({"unexpected": sentinel})),
            ("mission.user_stories[0].acceptance[0]", lambda value: value["mission"]["user_stories"][0]["acceptance"][0].update({"unexpected": sentinel})),
            ("milestones[0]", lambda value: value["milestones"][0].update({"unexpected": sentinel})),
            ("milestones[0].acceptance[0]", lambda value: value["milestones"][0]["acceptance"][0].update({"unexpected": sentinel})),
            ("slices[0]", lambda value: value["slices"][0].update({"unexpected": sentinel})),
            ("slices[0].acceptance[0]", lambda value: value["slices"][0]["acceptance"][0].update({"unexpected": sentinel})),
            ("slices[0].evidence[0]", lambda value: value["slices"][0]["evidence"].__setitem__(0, {"unexpected": sentinel})),
            ("decisions[0]", lambda value: value["decisions"][0].update({"unexpected": sentinel})),
            ("testing", lambda value: value["testing"].update({"unexpected": sentinel})),
            ("testing.browser_scenarios[0]", lambda value: value["testing"]["browser_scenarios"][0].update({"unexpected": sentinel})),
            ("testing.held_out_scenarios[0]", lambda value: value["testing"]["held_out_scenarios"][0].update({"unexpected": sentinel})),
            ("testing.evidence_requirements[0]", lambda value: value["testing"]["evidence_requirements"].__setitem__(0, {"unexpected": sentinel})),
            ("security", lambda value: value["security"].update({"unexpected": sentinel})),
            ("security.threat_scenarios[0]", lambda value: value["security"]["threat_scenarios"][0].update({"unexpected": sentinel})),
            ("security.authority_decisions[0]", lambda value: value["security"]["authority_decisions"][0].update({"unexpected": sentinel})),
            ("policy", lambda value: value["policy"].update({"unexpected": sentinel})),
            ("models", lambda value: value["models"].update({"unexpected": sentinel})),
            ("models.integrator", lambda value: value["models"]["integrator"].update({"unexpected": sentinel})),
            ("model_policy", lambda value: value["model_policy"].update({"unexpected": sentinel})),
            ("model_policy.roles", lambda value: value["model_policy"]["roles"].update({"unexpected": sentinel})),
            ("execution", lambda value: value["execution"].update({"unexpected": sentinel})),
            ("execution.reasoning_effort", lambda value: value["execution"]["reasoning_effort"].update({"unexpected": sentinel})),
            ("intake", lambda value: value["intake"].update({"unexpected": sentinel})),
        )
        for path, mutate in mutations:
            with self.subTest(path=path):
                manifest = copy.deepcopy(TEMPLATE)
                mutate(manifest)
                result = validate_manifest(manifest)
                self.assertFalse(result["valid"], result)
                self.assertTrue(any(path in error for error in result["errors"]), result)
                self.assertNotIn(sentinel, json.dumps(result))

    def test_schema_v2_rejects_missing_required_fields_at_every_object_contract(self) -> None:
        mutations = (
            ("manifest", lambda value: value.pop("intake")),
            ("mission", lambda value: value["mission"].pop("surfaces")),
            ("mission.personas[0]", lambda value: value["mission"]["personas"][0].pop("need")),
            ("mission.user_stories[0]", lambda value: value["mission"]["user_stories"][0].pop("paths")),
            ("mission.user_stories[0].acceptance[0]", lambda value: value["mission"]["user_stories"][0]["acceptance"][0].pop("type")),
            ("milestones[0]", lambda value: value["milestones"][0].pop("depends_on")),
            ("milestones[0].acceptance[0]", lambda value: value["milestones"][0]["acceptance"][0].pop("statement")),
            ("slices[0]", lambda value: value["slices"][0].pop("review_roles")),
            ("slices[0].acceptance[0]", lambda value: value["slices"][0]["acceptance"][0].pop("statement")),
            ("decisions[0]", lambda value: value["decisions"][0].pop("status")),
            ("testing", lambda value: value["testing"].pop("evidence_requirements")),
            ("testing.browser_scenarios[0]", lambda value: value["testing"]["browser_scenarios"][0].pop("expected")),
            ("testing.held_out_scenarios[0]", lambda value: value["testing"]["held_out_scenarios"][0].pop("then")),
            ("security", lambda value: value["security"].pop("adversarial_lens")),
            ("security.threat_scenarios[0]", lambda value: value["security"]["threat_scenarios"][0].pop("expected_control")),
            ("security.authority_decisions[0]", lambda value: value["security"]["authority_decisions"][0].pop("status")),
            ("policy", lambda value: value["policy"].pop("max_remediation_cycles")),
            ("models", lambda value: value["models"].pop("holdout")),
            ("models.integrator", lambda value: value["models"]["integrator"].pop("model")),
            ("model_policy", lambda value: value["model_policy"].pop("automatic_fallback")),
            ("model_policy.roles", lambda value: value["model_policy"]["roles"].pop("holdout")),
            ("execution", lambda value: value["execution"].pop("graph_mode")),
            ("execution.reasoning_effort", lambda value: value["execution"]["reasoning_effort"].pop("worker")),
            ("intake", lambda value: value["intake"].pop("user_authored_intent")),
        )
        for path, mutate in mutations:
            with self.subTest(path=path):
                manifest = copy.deepcopy(TEMPLATE)
                mutate(manifest)
                result = validate_manifest(manifest)
                self.assertFalse(result["valid"], result)
                self.assertTrue(any(path in error for error in result["errors"]), result)

    def test_direct_manifest_rejects_nested_credential_keys_without_echoing_values(self) -> None:
        sentinel = "DO-NOT-ECHO-THIS-VALUE"
        for key in ("api_key", "access-token", "password", "client_secret", "credential", "connection_string", "private_key"):
            with self.subTest(key=key):
                manifest = copy.deepcopy(TEMPLATE)
                manifest["mission"]["nested"] = [{"deeper": {key: sentinel}}]
                result = validate_manifest(manifest)
                self.assertFalse(result["valid"])
                self.assertTrue(any("credential-shaped" in item for item in result["errors"]))
                self.assertNotIn(sentinel, json.dumps(result))

    def test_direct_manifest_rejects_nested_credential_values_without_echoing_values(self) -> None:
        sentinel = "sk-" + "Z" * 32
        manifest = copy.deepcopy(TEMPLATE)
        manifest["mission"]["constraints"].append({"nested": ["Bearer " + "A" * 24, sentinel]})
        result = validate_manifest(manifest)
        self.assertFalse(result["valid"])
        self.assertTrue(any("credential-shaped" in item for item in result["errors"]))
        self.assertNotIn(sentinel, json.dumps(result))

    def test_direct_manifest_requires_canonical_workspace_and_complete_threats(self) -> None:
        for name, mutate in {
            "relative-workspace": lambda value: value["mission"].update({"workspace_path": "."}),
            "workspace-whitespace": lambda value: value["mission"].update({"workspace_path": " /tmp "}),
            "blank-threat": lambda value: value["security"]["threat_scenarios"][0].update({"scenario": "  "}),
            "blank-control": lambda value: value["security"]["threat_scenarios"][0].update({"expected_control": ""}),
            "non-object-threat": lambda value: value["security"].update({"threat_scenarios": ["claimed safe"]}),
            "relative-beads": lambda value: value["execution"].update({"beads_directory": ".beads"}),
            "one-r3-threat": lambda value: value["security"].update({"threat_scenarios": value["security"]["threat_scenarios"][:1]}),
        }.items():
            with self.subTest(name=name):
                manifest = copy.deepcopy(TEMPLATE)
                mutate(manifest)
                self.assertFalse(validate_manifest(manifest)["valid"])

    def test_direct_threat_contracts_reject_placeholders_and_duplicate_ids(self) -> None:
        mutations = {
            "backend-severity": lambda value: value["security"]["threat_scenarios"][0].update({"severity": "high"}),
            "unknown-field": lambda value: value["security"]["threat_scenarios"][0].update({"notes": "claimed complete"}),
            "thin-name": lambda value: value["security"]["threat_scenarios"][0].update({"name": "x"}),
            "thin-scenario": lambda value: value["security"]["threat_scenarios"][0].update({"scenario": "y"}),
            "thin-surface": lambda value: value["security"]["threat_scenarios"][0].update({"attack_surface": "x"}),
            "thin-control": lambda value: value["security"]["threat_scenarios"][0].update({"expected_control": "y"}),
            "duplicate-id": lambda value: value["security"]["threat_scenarios"][1].update({
                "id": value["security"]["threat_scenarios"][0]["id"]
            }),
            "copy-with-new-id": lambda value: value["security"]["threat_scenarios"].__setitem__(
                1,
                {**copy.deepcopy(value["security"]["threat_scenarios"][0]), "id": "TH-COPY"},
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                manifest = copy.deepcopy(TEMPLATE)
                mutate(manifest)
                result = validate_manifest(manifest)
                self.assertFalse(result["valid"], result)

    def test_direct_threat_contract_is_exactly_five_canonical_fields(self) -> None:
        expected = ["id", "name", "scenario", "attack_surface", "expected_control"]
        self.assertEqual(
            [list(threat) for threat in TEMPLATE["security"]["threat_scenarios"]],
            [expected, expected],
        )
        self.assertTrue(validate_manifest(copy.deepcopy(TEMPLATE))["valid"])

    def test_independent_sensitive_surface_vocabulary_sets_r3_floor(self) -> None:
        probes = (
            "Patients schedule medical appointments with clinicians and receive dosage instructions",
            "Users sign in with passwords and reset access",
            "Customers review bank balances and transfer money",
            "patients OAuth private medical records",
            "OIDC access tokens for clinical records",
            "SSO identity tokens linked to PHI",
            "PII stored with banking and financial account records",
            "Reconcile customer financial transactions and investment portfolios.",
        )
        security = {"data_classification": "none", "risk_triggers": []}
        for probe in probes:
            with self.subTest(probe=probe):
                self.assertEqual(
                    derive_independent_mission_risk(security, {"reviewer_probe": probe}),
                    "R3",
                )

    def test_benign_patient_and_investment_language_stays_low_risk(self) -> None:
        security = {"data_classification": "none", "risk_triggers": []}
        probes = (
            "Use a patient retry strategy for eventually consistent public article reads.",
            "Readers compare the time investment required by each gardening tutorial",
        )
        for probe in probes:
            with self.subTest(probe=probe):
                self.assertEqual(
                    derive_independent_mission_risk(security, {"reviewer_probe": probe}),
                    "R1",
                )

    def test_direct_manifest_requires_exact_provenance_decisions_risk_and_bounds(self) -> None:
        mutations = {
            "intake-score": lambda value: value["intake"].update({"readiness_score": 99}),
            "intake-extra": lambda value: value["intake"].update({"generated": True}),
            "security-open": lambda value: value["security"]["authority_decisions"][0].update({"status": "open"}),
            "security-mismatch": lambda value: value["security"]["authority_decisions"][0].update({"statement": "Different authority"}),
            "derived-risk": lambda value: value["security"].update({"derived_risk": "R2"}),
            "active-milestones": lambda value: value["policy"].update({"max_active_milestones": 2}),
            "parallel-slices": lambda value: value["policy"].update({"max_parallel_slices": 3}),
            "failure-limit": lambda value: value["policy"].update({"repeated_failure_limit": 3}),
            "remediation-limit": lambda value: value["policy"].update({"max_remediation_cycles": 4}),
            "unknown-policy": lambda value: value["policy"].update({"unattended_dispatch": True}),
            "one-character-decision": lambda value: value["decisions"][0].update({"statement": "x"}),
            "one-character-security-decision": lambda value: value["security"]["authority_decisions"][0].update({"statement": "x"}),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                manifest = copy.deepcopy(TEMPLATE)
                mutate(manifest)
                self.assertFalse(validate_manifest(manifest)["valid"])

    def test_independent_regulated_risk_cannot_be_downgraded_by_slices(self) -> None:
        manifest = copy.deepcopy(TEMPLATE)
        manifest["security"]["data_classification"] = "regulated"
        manifest["security"]["risk_triggers"] = ["regulated records"]
        manifest["security"]["derived_risk"] = "R1"
        manifest["security"]["threat_scenarios"] = manifest["security"]["threat_scenarios"][:1]
        for slice_spec in manifest["slices"]:
            slice_spec["risk"] = "R1"
            slice_spec["risk_triggers"] = ["routine presentation"]
        result = validate_manifest(manifest)
        self.assertFalse(result["valid"])
        self.assertTrue(any("maximum independent mission risk" in item for item in result["errors"]))
        self.assertTrue(any("at least 2 complete adversarial cases" in item for item in result["errors"]))

    def test_milestone_slice_acceptance_and_story_linkage_are_substantive(self) -> None:
        mutations = {
            "thin-milestone": lambda value: value["milestones"][0]["acceptance"][0].update({"statement": "It works now"}),
            "missing-milestone-type": lambda value: value["milestones"][0]["acceptance"][0].pop("type"),
            "unsupported-story-type": lambda value: value["mission"]["user_stories"][0]["acceptance"][0].update({"type": "smoke"}),
            "blank-slice-id": lambda value: value["slices"][0]["acceptance"][0].update({"id": ""}),
            "thin-slice": lambda value: value["slices"][0]["acceptance"][0].update({"statement": "It works"}),
            "omitted-story-criterion": lambda value: value["slices"][0]["acceptance"].pop(),
            "missing-story": lambda value: value["slices"][0].pop("story_id"),
            "unknown-story": lambda value: value["slices"][0].update({"story_id": "US-404"}),
            "story-criterion-mismatch": lambda value: value["slices"][0]["acceptance"][0].update({
                "statement": "A different observable result appears for this slice"
            }),
            "milestone-omits-story-criterion": lambda value: value["milestones"][0]["acceptance"].pop(),
            "milestone-story-semantics-mismatch": lambda value: next(
                item for item in value["milestones"][0]["acceptance"] if item["id"] == "M1-S1-A1"
            ).update({"type": "boundary"}),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                manifest = copy.deepcopy(TEMPLATE)
                mutate(manifest)
                self.assertFalse(validate_manifest(manifest)["valid"])

    def test_each_story_requires_positive_and_negative_acceptance(self) -> None:
        for criterion_type in ("happy", "negative"):
            with self.subTest(criterion_type=criterion_type):
                manifest = copy.deepcopy(TEMPLATE)
                for criterion in manifest["mission"]["user_stories"][0]["acceptance"]:
                    criterion["type"] = criterion_type
                self.assertFalse(validate_manifest(manifest)["valid"])

    def test_orphan_slices_cycles_and_duplicate_acceptance_are_rejected(self) -> None:
        orphan = copy.deepcopy(TEMPLATE)
        orphan["milestones"][0]["slices"] = orphan["milestones"][0]["slices"][:-1]
        self.assertFalse(validate_manifest(orphan)["valid"])

        milestone_cycle = copy.deepcopy(TEMPLATE)
        milestone_cycle["milestones"][0]["depends_on"] = ["M1"]
        self.assertFalse(validate_manifest(milestone_cycle)["valid"])

        slice_cycle = copy.deepcopy(TEMPLATE)
        slice_cycle["slices"][0]["depends_on"] = ["M1-S2"]
        slice_cycle["slices"][1]["depends_on"] = ["M1-S1"]
        self.assertFalse(validate_manifest(slice_cycle)["valid"])

        duplicate = copy.deepcopy(TEMPLATE)
        duplicate["slices"][0]["acceptance"][1]["id"] = duplicate["slices"][0]["acceptance"][0]["id"]
        self.assertFalse(validate_manifest(duplicate)["valid"])

    def test_every_slice_requires_nonempty_path_coordinates(self) -> None:
        for paths in ([], [""], ["   "], [None], "src/**"):
            with self.subTest(paths=paths):
                manifest = copy.deepcopy(TEMPLATE)
                manifest["slices"][0]["paths"] = paths
                result = validate_manifest(manifest)
                self.assertFalse(result["valid"])
                self.assertTrue(any("path/glob coordinates" in item for item in result["errors"]))

    def test_direct_manifest_paths_are_canonical_workspace_relative_coordinates(self) -> None:
        workspace = TEMPLATE["mission"]["workspace_path"]
        for path in (
            f"{workspace}/src/auth/**",
            "/src/auth/**",
            "../src/auth/**",
            "src/../auth/**",
            "./src/auth/**",
            "src//auth/**",
            "src/[auth/**",
            "x",
        ):
            with self.subTest(rejected=path):
                manifest = copy.deepcopy(TEMPLATE)
                manifest["slices"][0]["paths"] = [path]
                result = validate_manifest(manifest)
                self.assertFalse(result["valid"])
                self.assertTrue(any("workspace-relative path/glob" in item for item in result["errors"]))

        for path in ("README.md", "src/auth/**", "*.toml", ".github/**"):
            with self.subTest(accepted=path):
                manifest = copy.deepcopy(TEMPLATE)
                manifest["slices"][0]["paths"] = [path]
                self.assertTrue(validate_manifest(manifest)["valid"])


class StateMachineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.key_home = tempfile.TemporaryDirectory()
        self.addCleanup(self.key_home.cleanup)
        self.env_patch = patch.dict("os.environ", {"HERMES_HOME": self.key_home.name}, clear=False)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.manifest = copy.deepcopy(TEMPLATE)
        self.manifest["mission"]["workspace_path"] = str(ROOT)
        self.sha = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
        self.sha2 = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD^"], text=True).strip()
        self.artifact_home = tempfile.TemporaryDirectory()
        self.addCleanup(self.artifact_home.cleanup)
        self.artifact_counter = 0
        self.state = initial_state(self.manifest)
        self.integrator_actor = integrator_actor(self.manifest)
        self.builder_actor = builder_actor(self.manifest)

    def milestone_transition(
        self,
        entity_id: str,
        action: str,
        evidence: dict | None = None,
        *,
        actor: dict | None = None,
    ) -> dict:
        return self.state_transition(
            entity_id,
            action,
            evidence,
            trusted_actor=self.integrator_actor if actor is None else actor,
        )

    def state_transition(
        self,
        entity_id: str,
        action: str,
        evidence: dict | None = None,
        *,
        trusted_actor: dict | None | object = _DEFAULT_ACTOR,
    ) -> dict:
        actor = trusted_actor
        if actor is _DEFAULT_ACTOR:
            actor = (
                self.builder_actor
                if action in {"start_slice", "resume_slice", "continue_slice", "record_failure", "request_review"}
                else self.integrator_actor
            )
        return transition(
            self.manifest,
            self.state,
            entity_id,
            action,
            evidence,
            trusted_actor=actor if isinstance(actor, dict) else None,
        )

    def check_receipts(
        self,
        entity_id: str = "M1-S1",
        criterion_ids: list[str] | None = None,
        *,
        candidate_sha: str | None = None,
        observed_result: str = "PASS",
        exit_code: int = 0,
    ) -> list[dict]:
        self.artifact_counter += 1
        artifact = Path(self.artifact_home.name) / f"check-{self.artifact_counter}.json"
        artifact.write_text(
            json.dumps({"entity_id": entity_id, "result": observed_result}) + "\n",
            encoding="utf-8",
        )
        if criterion_ids is None:
            spec = next(item for item in self.manifest["slices"] if item["id"] == entity_id)
            criterion_ids = [item["id"] for item in spec["acceptance"]]
        return [{
            "mission_id": self.manifest["mission"]["id"],
            "entity_id": entity_id,
            "candidate_sha": candidate_sha or self.sha,
            "command": f"python -m unittest checks.{entity_id}",
            "exit_code": exit_code,
            "environment_fingerprint": "tests:python-unittest:linux",
            "observed_result": observed_result,
            "timestamp": "2026-08-31T10:00:00Z",
            "path": str(artifact),
            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "criterion_ids": criterion_ids,
        }]

    def scenario_receipt(
        self,
        artifact: Path,
        criterion_ids: list[str],
        *,
        entity_id: str = "M1",
        integration_sha: str | None = None,
    ) -> dict:
        return {
            "mission_id": self.manifest["mission"]["id"],
            "entity_id": entity_id,
            "integration_sha": integration_sha or self.sha2,
            "command_or_scenario": "exercise integrated held-out journey",
            "exit_code": 0,
            "environment_fingerprint": "tests:python-unittest:linux",
            "observed_result": "PASS",
            "timestamp": "2026-08-31T10:05:00+00:00",
            "path": str(artifact),
            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "criterion_ids": criterion_ids,
        }

    def review_evidence(self, candidate_sha: str, entity_id: str = "M1-S1") -> dict:
        return {
            "candidate_sha": candidate_sha,
            "reviews": [
                issue_review_receipt(
                    self.manifest,
                    role=role,
                    entity_id=entity_id,
                    candidate_sha=candidate_sha,
                    reviewer=f"independent-{role}",
                    provider=self.manifest["models"][role]["provider"],
                    model=self.manifest["models"][role]["model"],
                    verdict="PASS",
                    session_id=f"session-{role}",
                )
                for role in ("verifier", "adversary")
            ],
        }

    def holdout_evidence(self, candidate_sha: str, entity_id: str = "M1") -> dict:
        return issue_review_receipt(
            self.manifest,
            role="holdout",
            entity_id=entity_id,
            candidate_sha=candidate_sha,
            reviewer="fresh-holdout",
            provider=self.manifest["models"]["holdout"]["provider"],
            model=self.manifest["models"]["holdout"]["model"],
            verdict="PASS",
            session_id="session-holdout",
        )

    def test_resume_slice_rebinds_a_fresh_worker_without_spending_remediation(self) -> None:
        self.milestone_transition("M1", "start_milestone")
        self.state_transition("M1-S1", "start_slice")
        fresh_builder = builder_actor(self.manifest, session_id="fresh-worker-session")
        self.state_transition(
            "M1-S1",
            "resume_slice",
            {"reason": "the disposable worker session timed out before producing a candidate"},
            trusted_actor=fresh_builder,
        )
        self.assertEqual(self.state["slices"]["M1-S1"]["attempt"], 1)
        self.assertEqual(
            self.state["slices"]["M1-S1"]["builder_authority"]["session_id"],
            "fresh-worker-session",
        )
        self.assertIn("M1-S1", next_actions(self.manifest, self.state)["resume_slices"])

    def test_milestone_scoped_reviews_are_required_after_complete_blocks(self) -> None:
        self.manifest = copy.deepcopy(TEMPLATE)
        self.manifest["mission"]["workspace_path"] = str(ROOT)
        for slice_spec in self.manifest["slices"]:
            slice_spec["review_required"] = False
            slice_spec["review_roles"] = []
        self.state = initial_state(self.manifest)
        self.integrator_actor = integrator_actor(self.manifest)
        self.builder_actor = builder_actor(self.manifest)
        self.milestone_transition("M1", "start_milestone")
        self.state_transition("M1-S1", "start_slice")
        self.state_transition(
            "M1-S1",
            "complete_slice",
            {
                "candidate_sha": self.sha,
                "checks": self.check_receipts("M1-S1"),
                "acceptance_passed": [item["id"] for item in self.manifest["slices"][0]["acceptance"]],
            },
        )
        self.state_transition("M1-S2", "start_slice")
        self.state_transition(
            "M1-S2",
            "complete_slice",
            {
                "candidate_sha": self.sha2,
                "checks": self.check_receipts("M1-S2", candidate_sha=self.sha2),
                "acceptance_passed": [item["id"] for item in self.manifest["slices"][1]["acceptance"]],
            },
        )
        self.milestone_transition("M1", "validate_milestone")
        milestone_criteria = [item["id"] for item in self.manifest["milestones"][0]["acceptance"]]
        with tempfile.TemporaryDirectory() as tmp:
            scenario = Path(tmp) / "milestone.json"
            scenario.write_text('{"result":"pass"}\n', encoding="utf-8")
            reviews = [
                issue_review_receipt(
                    self.manifest,
                    role=role,
                    entity_id="M1",
                    candidate_sha=self.sha2,
                    reviewer=f"milestone-{role}",
                    provider=self.manifest["models"][role]["provider"],
                    model=self.manifest["models"][role]["model"],
                    verdict="PASS",
                    session_id=f"milestone-{role}-session",
                )
                for role in ("verifier", "adversary")
            ]
            self.milestone_transition(
                "M1",
                "complete_milestone",
                {
                    "integration_sha": self.sha2,
                    "acceptance_passed": milestone_criteria,
                    "scenario_receipts": [self.scenario_receipt(scenario, milestone_criteria)],
                    "holdout_review": self.holdout_evidence(self.sha2),
                    "independent_reviews": reviews,
                },
            )
        self.assertEqual(self.state["milestones"]["M1"]["status"], "completed")

    def test_review_transition_is_rejected_for_milestone_scoped_block(self) -> None:
        manifest = copy.deepcopy(TEMPLATE)
        manifest["mission"]["workspace_path"] = str(ROOT)
        manifest["slices"][0]["review_required"] = False
        manifest["slices"][0]["review_roles"] = []
        state = initial_state(manifest)
        transition(manifest, state, "M1", "start_milestone", trusted_actor=integrator_actor(manifest))
        transition(manifest, state, "M1-S1", "start_slice", trusted_actor=builder_actor(manifest))
        with self.assertRaises(FactoryError):
            transition(
                manifest,
                state,
                "M1-S1",
                "request_review",
                {"candidate_sha": self.sha, "checks": self.check_receipts("M1-S1")},
                trusted_actor=builder_actor(manifest),
            )

    def test_happy_path(self) -> None:
        self.milestone_transition("M1", "start_milestone")
        self.assertIn("M1-S1", next_actions(self.manifest, self.state)["startable_slices"])

        self.state_transition("M1-S1", "start_slice")
        self.state_transition(
            "M1-S1",
            "request_review",
            {"candidate_sha": self.sha, "checks": self.check_receipts()},
        )
        self.state_transition(
            "M1-S1",
            "pass_review",
            self.review_evidence(self.sha),
        )
        self.state_transition(
            "M1-S1",
            "complete_slice",
            {
                "candidate_sha": self.sha,
                "checks": self.check_receipts(),
                "acceptance_passed": ["M1-S1-A1", "M1-S1-A2"],
            },
        )
        self.assertIn("M1-S2", next_actions(self.manifest, self.state)["startable_slices"])

        self.state_transition("M1-S2", "start_slice")
        self.state_transition(
            "M1-S2",
            "request_review",
            {"candidate_sha": self.sha2, "checks": self.check_receipts("M1-S2", candidate_sha=self.sha2)},
        )
        self.state_transition(
            "M1-S2",
            "pass_review",
            self.review_evidence(self.sha2, "M1-S2"),
        )
        self.state_transition(
            "M1-S2",
            "complete_slice",
            {
                "candidate_sha": self.sha2,
                "checks": self.check_receipts("M1-S2", candidate_sha=self.sha2),
                "acceptance_passed": ["M1-S2-A1", "M1-S2-US-NEG"],
            },
        )
        self.assertIn("validate_milestone:M1", next_actions(self.manifest, self.state)["gates"])
        self.milestone_transition("M1", "validate_milestone")
        with tempfile.TemporaryDirectory() as tmp:
            journey = Path(tmp) / "journey.json"
            denial = Path(tmp) / "cross-owner-denial.json"
            journey.write_text('{"result":"pass"}\n', encoding="utf-8")
            denial.write_text('{"result":"pass","observed":"cross-owner access denied"}\n', encoding="utf-8")
            milestone_criteria = [item["id"] for item in self.manifest["milestones"][0]["acceptance"]]
            self.milestone_transition(
                "M1",
                "complete_milestone",
                {
                    "integration_sha": self.sha2,
                    "holdout_review": self.holdout_evidence(self.sha2),
                    "acceptance_passed": milestone_criteria,
                    "scenario_receipts": [
                        self.scenario_receipt(journey, milestone_criteria[:3]),
                        self.scenario_receipt(denial, milestone_criteria[3:]),
                    ],
                },
            )
        self.assertEqual(self.state["milestones"]["M1"]["status"], "completed")
        stored_scenario = self.state["milestones"]["M1"]["scenario_receipts"][0]
        self.assertEqual(stored_scenario["observed_result"], "PASS")
        self.assertEqual(stored_scenario["exit_code"], 0)
        self.assertIn("command_or_scenario", stored_scenario)
        self.assertIn("environment_fingerprint", stored_scenario)
        self.assertIn("timestamp", stored_scenario)

    def test_next_actions_adds_executable_orchestrator_worker_dispatch_descriptors(self) -> None:
        before = next_actions(self.manifest, self.state)
        self.assertEqual(
            before["dispatch"]["startable_milestones"],
            [{
                "entity_id": "M1",
                "entity_type": "milestone",
                "action": "start_milestone",
                "configured_role": "integrator",
                "execution_role": "orchestrator",
                "provider": self.manifest["models"]["integrator"]["provider"],
                "model": self.manifest["models"]["integrator"]["model"],
                "reasoning_effort": "high",
                "execution_mode": "orchestration",
                "auto_launch": False,
            }],
        )
        self.assertEqual(before["dispatch"]["startable_slices"], [])
        for legacy_key in (
            "active_milestones", "active_slices", "startable_milestones",
            "startable_slices", "gates", "replan_required",
        ):
            self.assertIn(legacy_key, before)

        self.milestone_transition("M1", "start_milestone")
        after = next_actions(self.manifest, self.state)
        first = after["dispatch"]["startable_slices"][0]
        self.assertEqual(first["entity_id"], "M1-S1")
        self.assertEqual(first["configured_role"], "builder")
        self.assertEqual(first["execution_role"], "worker")
        self.assertEqual(first["provider"], self.manifest["models"]["builder"]["provider"])
        self.assertEqual(first["model"], self.manifest["models"]["builder"]["model"])
        self.assertEqual(first["reasoning_effort"], "medium")
        self.assertEqual(first["execution_mode"], "functional_block")
        self.assertFalse(first["auto_launch"])

    def test_first_milestone_start_binds_attested_integrator_authority(self) -> None:
        self.assertIsNone(self.state["integrator_authority"])
        baseline = copy.deepcopy(self.state)
        with self.assertRaisesRegex(FactoryError, "not authorized"):
            self.state_transition("M1", "start_milestone", trusted_actor=None)
        self.assertEqual(self.state, baseline)
        self.milestone_transition("M1", "start_milestone")
        self.assertEqual(self.state["integrator_authority"], self.integrator_actor)
        self.milestone_transition(
            "M1",
            "block",
            {
                "reason": "operator pause",
                "owner": "integrator",
                "resume_condition": "operator resumes mission",
            },
        )
        self.assertEqual(self.state["milestones"]["M1"]["status"], "blocked")
        self.assertEqual(next_actions(self.manifest, self.state)["active_milestones"], [])

    def test_milestone_actor_is_exact_required_and_rejected_before_mutation(self) -> None:
        self.milestone_transition("M1", "start_milestone")
        baseline = copy.deepcopy(self.state)
        mismatches = {
            "missing": None,
            "session": {**self.integrator_actor, "session_id": "different-session"},
            "provider": {**self.integrator_actor, "provider": "different-provider"},
            "model": {**self.integrator_actor, "model": "different-model"},
            "blank": {**self.integrator_actor, "session_id": ""},
            "extra": {**self.integrator_actor, "role": "integrator"},
        }
        for name, actor in mismatches.items():
            with self.subTest(name=name), self.assertRaises(FactoryError):
                self.state_transition(
                    "M1",
                    "block",
                    {
                        "reason": "must not persist",
                        "owner": "integrator",
                        "resume_condition": "never",
                    },
                    trusted_actor=actor,
                )
            self.assertEqual(self.state, baseline)

    def test_slice_actions_bind_exact_builder_and_require_integrator_governance(self) -> None:
        self.milestone_transition("M1", "start_milestone")
        self.assertIsNone(self.state["slices"]["M1-S1"]["builder_authority"])
        baseline = copy.deepcopy(self.state)
        for actor in (None, self.integrator_actor):
            with self.subTest(actor=actor), self.assertRaisesRegex(FactoryError, "not authorized"):
                transition(
                    self.manifest,
                    self.state,
                    "M1-S1",
                    "start_slice",
                    trusted_actor=actor,
                )
            self.assertEqual(self.state, baseline)

        self.state_transition("M1-S1", "start_slice")
        self.assertEqual(
            self.state["slices"]["M1-S1"]["builder_authority"], self.builder_actor
        )
        self.assertEqual(
            self.state["events"][-1]["actor"], {"role": "builder", **self.builder_actor}
        )
        active = copy.deepcopy(self.state)
        for action, actor, evidence in (
            (
                "request_review",
                builder_actor(self.manifest, "different-builder-session"),
                {},
            ),
            ("request_review", self.integrator_actor, {}),
            (
                "replan",
                self.builder_actor,
                {"reason": "must not persist", "decision": "must not persist"},
            ),
        ):
            with self.subTest(action=action, actor=actor), self.assertRaisesRegex(
                FactoryError, "not authorized"
            ):
                transition(
                    self.manifest,
                    self.state,
                    "M1-S1",
                    action,
                    evidence,
                    trusted_actor=actor,
                )
            self.assertEqual(self.state, active)

    def test_slice_builder_authority_is_hmac_attested_and_manifest_compatible(self) -> None:
        self.milestone_transition("M1", "start_milestone")
        self.state_transition("M1-S1", "start_slice")
        self.state["slices"]["M1-S1"]["builder_authority"]["session_id"] = "forged"
        with self.assertRaisesRegex(FactoryError, "attestation"):
            next_actions(self.manifest, self.state)

        self.state = initial_state(self.manifest)
        self.milestone_transition("M1", "start_milestone")
        self.state_transition("M1-S1", "start_slice")
        self.state["slices"]["M1-S1"]["builder_authority"]["model"] = "wrong-model"
        _attest_state(self.state)
        with self.assertRaisesRegex(FactoryError, "not authorized"):
            next_actions(self.manifest, self.state)

    def test_stored_integrator_authority_is_attested_and_manifest_compatible(self) -> None:
        self.milestone_transition("M1", "start_milestone")
        self.state["integrator_authority"]["session_id"] = "forged-session"
        with self.assertRaisesRegex(FactoryError, "attestation"):
            next_actions(self.manifest, self.state)

        self.state = initial_state(self.manifest)
        self.integrator_actor = integrator_actor(self.manifest)
        self.milestone_transition("M1", "start_milestone")
        self.state["integrator_authority"]["model"] = "different-model"
        _attest_state(self.state)
        with self.assertRaisesRegex(FactoryError, "not authorized"):
            next_actions(self.manifest, self.state)

        self.state["integrator_authority"] = None
        _attest_state(self.state)
        with self.assertRaisesRegex(FactoryError, "cannot be null"):
            next_actions(self.manifest, self.state)

    def test_attested_state_requires_exact_top_entity_authority_and_event_shapes(self) -> None:
        mutations = {
            "unexpected-top": lambda value: value.update({"unexpected": True}),
            "omitted-top": lambda value: value.pop("updated_at"),
            "unexpected-milestone": lambda value: value["milestones"]["M1"].update({"unexpected": True}),
            "omitted-milestone": lambda value: value["milestones"]["M1"].pop("scenario_receipts"),
            "unexpected-slice": lambda value: value["slices"]["M1-S1"].update({"unexpected": True}),
            "omitted-slice": lambda value: value["slices"]["M1-S1"].pop("checks"),
            "malformed-authority": lambda value: value.update({
                "integrator_authority": {**self.integrator_actor, "unexpected": True}
            }),
            "malformed-event": lambda value: value["events"].append({"unexpected": True}),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                state = initial_state(self.manifest)
                mutate(state)
                _attest_state(state)
                with self.assertRaises(FactoryError):
                    _validate_state_compatibility(self.manifest, state)

    def test_cli_attestation_key_context_is_explicit_strong_and_restored(self) -> None:
        key = hashlib.sha256(b"audited CLI state attestation test key").digest()
        default_state = initial_state(self.manifest)
        with cli_attestation_key_context(key):
            keyed_state = initial_state(self.manifest)
            self.assertEqual(next_actions(self.manifest, keyed_state)["startable_milestones"], ["M1"])

        with self.assertRaisesRegex(FactoryError, "attestation"):
            next_actions(self.manifest, keyed_state)
        self.assertEqual(next_actions(self.manifest, default_state)["startable_milestones"], ["M1"])

        spec = importlib.util.spec_from_file_location(
            "dark_factory_engine_restart", ROOT / "plugin" / "engine.py"
        )
        self.assertIsNotNone(spec)
        restarted = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(restarted)
        with restarted.cli_attestation_key_context(key):
            self.assertEqual(
                restarted.next_actions(self.manifest, keyed_state)["startable_milestones"],
                ["M1"],
            )

        with self.assertRaises(TypeError):
            with cli_attestation_key_context("not-bytes"):  # type: ignore[arg-type]
                pass
        for weak_key in (b"short", b"x" * 32):
            with self.subTest(weak_key=weak_key), self.assertRaises(FactoryError):
                with cli_attestation_key_context(weak_key):
                    pass

        with self.assertRaisesRegex(RuntimeError, "simulated failure"):
            with cli_attestation_key_context(key):
                raise RuntimeError("simulated failure")
        self.assertEqual(next_actions(self.manifest, default_state)["startable_milestones"], ["M1"])

    def test_holdout_review_must_use_configured_model_and_frozen_sha(self) -> None:
        review = self.holdout_evidence(self.sha)
        validated = _validate_holdout_review(self.manifest, review, self.sha)
        self.assertEqual(validated["role"], "holdout")
        changed_manifest = copy.deepcopy(self.manifest)
        changed_manifest["milestones"][0]["acceptance"][0]["statement"] += " changed"
        with self.assertRaisesRegex(FactoryError, "manifest_digest"):
            _validate_holdout_review(changed_manifest, review, self.sha)

        wrong_model = issue_review_receipt(
            self.manifest,
            role="holdout",
            entity_id="M1",
            candidate_sha=self.sha,
            reviewer="fresh-holdout",
            provider=self.manifest["models"]["holdout"]["provider"],
            model="builder-model",
            verdict="PASS",
            session_id="session-holdout",
        )
        with self.assertRaisesRegex(FactoryError, "configured provider/model"):
            _validate_holdout_review(self.manifest, wrong_model, self.sha, "M1")

        wrong_sha = issue_review_receipt(
            self.manifest,
            role="holdout",
            entity_id="M1",
            candidate_sha=self.sha2,
            reviewer="fresh-holdout",
            provider=self.manifest["models"]["holdout"]["provider"],
            model=self.manifest["models"]["holdout"]["model"],
            verdict="PASS",
            session_id="session-holdout",
        )
        with self.assertRaisesRegex(FactoryError, "match integration_sha"):
            _validate_holdout_review(self.manifest, wrong_sha, self.sha, "M1")

    def test_scenario_receipts_require_real_success_artifacts_and_exact_criterion_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "journey.json"
            artifact.write_text('{"result":"pass"}\n', encoding="utf-8")
            receipt = self.scenario_receipt(artifact, ["M1-A1"])
            validated = _validate_scenario_receipts(
                [receipt],
                expected_criteria={"M1-A1"},
                mission_id=self.manifest["mission"]["id"],
                entity_id="M1",
                integration_sha=self.sha2,
            )
            self.assertEqual(validated[0]["sha256"], receipt["sha256"])
            self.assertEqual(validated[0]["observed_result"], "PASS")
            self.assertIn("command_or_scenario", validated[0])

            with self.assertRaisesRegex(FactoryError, "does not exist"):
                _validate_scenario_receipts([{**receipt, "path": str(Path(tmp) / "missing.json")}])
            with self.assertRaisesRegex(FactoryError, "does not match"):
                _validate_scenario_receipts([{**receipt, "sha256": "a" * 64}])
            with self.assertRaisesRegex(FactoryError, "do not cover"):
                _validate_scenario_receipts([receipt], expected_criteria={"M1-A1", "M1-A2"})
            with self.assertRaisesRegex(FactoryError, "exit_code must be zero"):
                _validate_scenario_receipts([{**receipt, "exit_code": 1}])
            with self.assertRaisesRegex(FactoryError, "observed_result"):
                _validate_scenario_receipts([{**receipt, "observed_result": "FAIL"}])
            with self.assertRaisesRegex(FactoryError, "observed_result"):
                _validate_scenario_receipts([{**receipt, "observed_result": "success"}])
            for field, value in (
                ("mission_id", "old-mission"),
                ("entity_id", "M2"),
                ("integration_sha", self.sha),
            ):
                with self.subTest(mismatched=field), self.assertRaisesRegex(FactoryError, f"{field} does not match"):
                    _validate_scenario_receipts(
                        [{**receipt, field: value}],
                        mission_id=self.manifest["mission"]["id"],
                        entity_id="M1",
                        integration_sha=self.sha2,
                    )
            with self.assertRaisesRegex(FactoryError, "unknown or contradictory"):
                _validate_scenario_receipts([{**receipt, "success": True}])
            for field in ("command_or_scenario", "environment_fingerprint", "timestamp"):
                with self.subTest(missing=field), self.assertRaisesRegex(FactoryError, field):
                    incomplete = dict(receipt)
                    incomplete.pop(field)
                    _validate_scenario_receipts([incomplete])
            with self.assertRaisesRegex(FactoryError, "exit_code must be an integer"):
                _validate_scenario_receipts([{**receipt, "exit_code": True}])

            artifact.write_text('{"success": false, "result": "pass"}\n', encoding="utf-8")
            contradictory = self.scenario_receipt(artifact, ["M1-A1"])
            with self.assertRaisesRegex(FactoryError, "artifact explicitly reports failure"):
                _validate_scenario_receipts([contradictory])
            artifact.write_text("ERROR: held-out journey failed\n", encoding="utf-8")
            plain_failure = self.scenario_receipt(artifact, ["M1-A1"])
            with self.assertRaisesRegex(FactoryError, "artifact explicitly reports failure"):
                _validate_scenario_receipts([plain_failure])

            artifact.write_text(
                "PASS: integrated journey completed\n"
                "Observed behavior: the authorization check failed open.\n",
                encoding="utf-8",
            )
            prose_contradiction = self.scenario_receipt(artifact, ["M1-A1"])
            with self.assertRaisesRegex(FactoryError, "artifact explicitly reports failure"):
                _validate_scenario_receipts([prose_contradiction])

            artifact.write_text(
                "PASS: integrated journey completed, but the holdout scenario did not complete.\n",
                encoding="utf-8",
            )
            inline_contradiction = self.scenario_receipt(artifact, ["M1-A1"])
            with self.assertRaisesRegex(FactoryError, "artifact explicitly reports failure"):
                _validate_scenario_receipts([inline_contradiction])

            structured_failures = (
                "review:\n  verdict: FAILURE\n  passed: true\n",
                "success: FALSE\nstatus: complete\n",
                "ok = false\n",
                "passed: no\n",
                "return code: 7\n",
                "exit_code: -1\n",
                "outcome: unsuccessful\n",
                '<testsuite tests="2" failures="1" errors="0"/>\n',
            )
            for payload in structured_failures:
                with self.subTest(payload=payload):
                    artifact.write_text(payload, encoding="utf-8")
                    structured = self.scenario_receipt(artifact, ["M1-A1"])
                    with self.assertRaisesRegex(FactoryError, "artifact explicitly reports failure"):
                        _validate_scenario_receipts([structured])

            indeterminate_payloads = (
                '{"success": true, "status": "unknown"}\n',
                '{"tests": 2, "failures": 0, "errors": 0}\n',
                "status: complete\nexit_code: 0\n",
                "The test verifies failure is denied without exposing protected state.\n",
                "The failure-path test should pass after authorization denies access.\n",
            )
            for payload in indeterminate_payloads:
                with self.subTest(indeterminate=payload):
                    artifact.write_text(payload, encoding="utf-8")
                    receipt_without_proof = self.scenario_receipt(artifact, ["M1-A1"])
                    with self.assertRaisesRegex(FactoryError, "recognized positive outcome"):
                        _validate_scenario_receipts([receipt_without_proof])

            positive_payloads = (
                '{"success": true}\n',
                '{"result": "passed", "exit_code": 0}\n',
                "status: successful\n",
                '<report status="ok" failures="0" errors="0"/>\n',
                "PASS: failure-path access was denied without protected disclosure\n",
                "PASS: failure-handling and retry documentation remains accurate\n",
                "PASS: the operation fails atomically and retry handling preserves state\n",
                "SUCCESS\n",
            )
            for payload in positive_payloads:
                with self.subTest(positive=payload):
                    artifact.write_text(payload, encoding="utf-8")
                    positive = self.scenario_receipt(artifact, ["M1-A1"])
                    self.assertEqual(
                        _validate_scenario_receipts([positive])[0]["observed_result"],
                        "PASS",
                    )

    def test_every_artifact_receipt_rejects_generic_failure_and_indeterminate_evidence(self) -> None:
        exact_mixed = (
            "PASS: browser journey completed\n"
            "All checks pass except ownership isolation\n"
            "2 failures occurred\n"
            "unknown whether authorization held"
        )
        adverse_payloads = (
            exact_mixed,
            "PASS\nAll checks passed, except ownership isolation\n",
            "PASS\nAll checks pass — except ownership isolation\n",
            "PASS\nThe ownership check passes; except tenant isolation\n",
            "PASS\n2 test failures occurred\n",
            "PASS\n1 integration test failure occurred\n",
            "PASS\n3 unexpected integration test errors occurred\n",
            "PASS\n4 runtime exceptions occurred\n",
            "PASS: browser journey completed\n1 failure\n",
            "PASS: browser journey completed\nerrors: 3\n",
            "PASS: browser journey completed\n2 exceptions occurred\n",
            "PASS: browser journey completed\nAll checks pass except ownership isolation\n",
            "PASS: browser journey completed\nuncertain whether ownership isolation held\n",
            "PASS: browser journey completed\nThe authorization result is indeterminate\n",
            "PASS: browser journey completed\nAuthorization was not verified\n",
            "PASS: browser journey completed\nOwnership isolation was not established\n",
            (
                "PASS: browser journey completed\n"
                "All checks pass -- after every preliminary browser, API, storage, audit, tenant, "
                "session, policy, and recovery probe was reviewed in detail -- except ownership isolation\n"
            ),
            (
                "PASS: browser journey completed\n"
                "17 deeply nested integration and browser authorization boundary regression suite "
                "failures were observed\n"
            ),
            "PASS: browser journey completed\nIt remains unknown, at present, whether authorization held.\n",
        )
        benign = (
            "PASS: documentation remains accurate\n"
            "The guide covers failure handling and retry on failure.\n"
            "The denied-access scenario expects 1 failure from the rejected request.\n"
            "Expected negative behavior passed: 1 rejected-request failure occurred as designed.\n"
            "2 expected test failures occurred as designed and the negative behavior passed.\n"
            "0 test failures occurred\n0 failures occurred\nerrors: 0\nexceptions: 0\n"
            "Unknown users are denied without exposing protected state.\n"
            "PASS: 0 deeply nested integration and browser authorization boundary failures occurred.\n"
            "PASS: the failure handling guide documents retry-on-failure behavior.\n"
            "Expected-negative authorization scenario PASSED: 7 deliberately generated request failures were handled correctly.\n"
        )

        with tempfile.TemporaryDirectory() as tmp:
            scenario_artifact = Path(tmp) / "scenario.txt"
            for receipt_kind in ("scenario", "check"):
                for payload in adverse_payloads:
                    with self.subTest(receipt_kind=receipt_kind, adverse=payload):
                        if receipt_kind == "scenario":
                            scenario_artifact.write_text(payload, encoding="utf-8")
                            receipt = self.scenario_receipt(scenario_artifact, ["M1-A1"])
                            validator = _validate_scenario_receipts
                        else:
                            receipt = self.check_receipts()[0]
                            check_artifact = Path(receipt["path"])
                            check_artifact.write_text(payload, encoding="utf-8")
                            receipt["sha256"] = hashlib.sha256(check_artifact.read_bytes()).hexdigest()
                            validator = _validate_check_receipts
                        with self.assertRaisesRegex(
                            FactoryError,
                            "artifact (?:explicitly reports failure|does not report a recognized positive outcome)",
                        ):
                            validator([receipt])

                with self.subTest(receipt_kind=receipt_kind, benign=True):
                    if receipt_kind == "scenario":
                        scenario_artifact.write_text(benign, encoding="utf-8")
                        receipt = self.scenario_receipt(scenario_artifact, ["M1-A1"])
                        validated = _validate_scenario_receipts([receipt])
                    else:
                        receipt = self.check_receipts()[0]
                        check_artifact = Path(receipt["path"])
                        check_artifact.write_text(benign, encoding="utf-8")
                        receipt["sha256"] = hashlib.sha256(check_artifact.read_bytes()).hexdigest()
                        validated = _validate_check_receipts([receipt])
                    self.assertEqual(validated[0]["observed_result"], "PASS")

    def test_check_receipt_envelope_pass_is_insufficient_without_positive_artifact(self) -> None:
        check = self.check_receipts()[0]
        artifact = Path(check["path"])
        artifact.write_text("No explicit result was emitted by this test run.\n", encoding="utf-8")
        check["sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
        with self.assertRaisesRegex(FactoryError, "recognized positive outcome"):
            _validate_check_receipts([check])

        artifact.write_text('{"verdict":"ok"}\n', encoding="utf-8")
        check["sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
        self.assertEqual(_validate_check_receipts([check])[0]["observed_result"], "PASS")

    def test_slice_checks_reject_plain_claims_bad_digests_and_criterion_gaps(self) -> None:
        self.milestone_transition("M1", "start_milestone")
        self.state_transition("M1-S1", "start_slice")
        with self.assertRaisesRegex(FactoryError, "must be an object"):
            self.state_transition(
                "M1-S1",
                "request_review",
                {"candidate_sha": self.sha, "checks": ["claimed pass"]},
            )
        for field, value in (
            ("mission_id", "old-mission"),
            ("entity_id", "M1-S2"),
            ("candidate_sha", self.sha2),
        ):
            receipts = self.check_receipts()
            receipts[0][field] = value
            with self.subTest(mismatched=field), self.assertRaisesRegex(FactoryError, f"{field} does not match"):
                self.state_transition(
                    "M1-S1",
                    "request_review",
                    {"candidate_sha": self.sha, "checks": receipts},
                )
        bad_digest = self.check_receipts()
        bad_digest[0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(FactoryError, "does not match"):
            self.state_transition(
                "M1-S1",
                "request_review",
                {"candidate_sha": self.sha, "checks": bad_digest},
            )
        with self.assertRaisesRegex(FactoryError, "do not cover"):
            self.state_transition(
                "M1-S1",
                "request_review",
                {
                    "candidate_sha": self.sha,
                    "checks": self.check_receipts(criterion_ids=["M1-S1-A1"]),
                },
            )

    def test_slice_checks_reject_unknown_fields_and_revalidate_stored_identity(self) -> None:
        self.milestone_transition("M1", "start_milestone")
        self.state_transition("M1-S1", "start_slice")
        checks = self.check_receipts()
        checks[0]["untrusted_summary"] = "claimed pass"
        with self.assertRaisesRegex(FactoryError, "unknown or contradictory"):
            self.state_transition(
                "M1-S1",
                "request_review",
                {"candidate_sha": self.sha, "checks": checks},
            )
        self.state_transition(
            "M1-S1",
            "request_review",
            {"candidate_sha": self.sha, "checks": self.check_receipts()},
        )
        stored = self.state["slices"]["M1-S1"]["checks"]
        self.assertEqual(stored[0]["mission_id"], self.manifest["mission"]["id"])
        self.assertEqual(stored[0]["entity_id"], "M1-S1")
        self.assertEqual(stored[0]["candidate_sha"], self.sha)
        self.state_transition(
            "M1-S1",
            "pass_review",
            self.review_evidence(self.sha),
        )
        with self.assertRaisesRegex(FactoryError, "do not cover"):
            self.state_transition(
                "M1-S1",
                "complete_slice",
                {
                    "candidate_sha": self.sha,
                    "checks": self.check_receipts(criterion_ids=["M1-S1-A1"]),
                    "acceptance_passed": ["M1-S1-A1", "M1-S1-A2"],
                },
            )
        self.state["slices"]["M1-S1"]["checks"][0]["entity_id"] = "M1-S2"
        _attest_state(self.state)
        with self.assertRaisesRegex(FactoryError, "entity_id does not match"):
            next_actions(self.manifest, self.state)

    def test_review_cannot_pass_without_both_configured_models(self) -> None:
        self.milestone_transition("M1", "start_milestone")
        self.state_transition("M1-S1", "start_slice")
        self.state_transition(
            "M1-S1",
            "request_review",
            {"candidate_sha": self.sha, "checks": self.check_receipts()},
        )
        evidence = self.review_evidence(self.sha)
        evidence["reviews"] = evidence["reviews"][:1]
        with self.assertRaisesRegex(FactoryError, "missing required review roles"):
            self.state_transition("M1-S1", "pass_review", evidence)

    def test_review_model_must_match_configured_role(self) -> None:
        self.milestone_transition("M1", "start_milestone")
        self.state_transition("M1-S1", "start_slice")
        self.state_transition(
            "M1-S1",
            "request_review",
            {"candidate_sha": self.sha, "checks": self.check_receipts()},
        )
        evidence = self.review_evidence(self.sha)
        evidence["reviews"][1] = issue_review_receipt(
            self.manifest,
            role="adversary",
            entity_id="M1-S1",
            candidate_sha=self.sha,
            reviewer="independent-adversary",
            provider=self.manifest["models"]["adversary"]["provider"],
            model="builder-model",
            verdict="PASS",
            session_id="session-adversary",
        )
        with self.assertRaisesRegex(FactoryError, "configured provider/model"):
            self.state_transition("M1-S1", "pass_review", evidence)

    def test_candidate_sha_must_be_real_workspace_commit(self) -> None:
        self.milestone_transition("M1", "start_milestone")
        self.state_transition("M1-S1", "start_slice")
        with self.assertRaisesRegex(FactoryError, "not a commit"):
            self.state_transition("M1-S1", "request_review",
                {"candidate_sha": "deadbeefdeadbeef", "checks": self.check_receipts()},
            )

    def test_rejected_sha_and_completed_entities_cannot_regress(self) -> None:
        self.milestone_transition("M1", "start_milestone")
        self.state_transition("M1-S1", "start_slice")
        self.state_transition("M1-S1", "request_review",
            {"candidate_sha": self.sha, "checks": self.check_receipts()},
        )
        self.state_transition("M1-S1", "request_changes",
            {"findings": ["negative path missing"]},
        )
        with self.assertRaisesRegex(FactoryError, "different candidate_sha"):
            self.state_transition("M1-S1", "request_review",
                {"candidate_sha": self.sha, "checks": self.check_receipts()},
            )
        self.state_transition("M1-S1", "request_review",
            {"candidate_sha": self.sha2, "checks": self.check_receipts(candidate_sha=self.sha2)},
        )
        self.state_transition("M1-S1", "pass_review",
            self.review_evidence(self.sha2),
        )
        self.state_transition("M1-S1", "complete_slice",
            {
                "candidate_sha": self.sha2,
                "checks": self.check_receipts(candidate_sha=self.sha2),
                "acceptance_passed": ["M1-S1-A1", "M1-S1-A2"],
            },
        )
        with self.assertRaisesRegex(FactoryError, "cannot block"):
            self.state_transition("M1-S1", "block",
                {"reason": "late", "owner": "x", "resume_condition": "never"},
            )

    def test_same_failure_twice_forces_replan(self) -> None:
        self.milestone_transition("M1", "start_milestone")
        self.state_transition("M1-S1", "start_slice")
        failure = "pytest failed at 2026-08-31T09:00:00Z: expected 200 got 500"
        self.state_transition("M1-S1", "record_failure", {"failure_signature": failure})
        self.assertEqual(self.state["slices"]["M1-S1"]["status"], "active")
        self.state_transition(
            "M1-S1",
            "record_failure",
            {"failure_signature": "pytest failed at 2026-08-31T10:00:00Z: expected 200 got 500"},
        )
        self.assertEqual(self.state["slices"]["M1-S1"]["status"], "replan_required")
        self.assertEqual(
            failure_fingerprint(failure),
            failure_fingerprint("pytest failed at 2026-08-31T10:00:00Z: expected 200 got 500"),
        )

    def test_review_rejection_exposes_bounded_continuation_and_new_candidate(self) -> None:
        self.milestone_transition("M1", "start_milestone")
        self.state_transition("M1-S1", "start_slice")
        self.state_transition(
            "M1-S1",
            "request_review",
            {"candidate_sha": self.sha, "checks": self.check_receipts(candidate_sha=self.sha)},
        )
        self.state_transition("M1-S1", "request_changes", {"findings": ["finding-0"]})

        following = next_actions(self.manifest, self.state)
        self.assertEqual(following["continuation_slices"], ["M1-S1"])
        self.assertEqual(
            following["dispatch"]["continuation_slices"][0]["action"],
            "continue_slice",
        )
        self.assertEqual(
            following["dispatch"]["continuation_slices"][0]["execution_mode"],
            "remediation",
        )

        replacement_builder = builder_actor(self.manifest, "remediation-builder-session")
        self.state_transition(
            "M1-S1",
            "continue_slice",
            {"reason": "Replace the rejected approach with an atomic state update."},
            trusted_actor=replacement_builder,
        )
        self.assertEqual(self.state["slices"]["M1-S1"]["attempt"], 2)
        self.assertIsNone(self.state["slices"]["M1-S1"]["candidate_sha"])
        self.assertEqual(self.state["slices"]["M1-S1"]["last_rejected_sha"], self.sha)
        self.assertEqual(
            self.state["slices"]["M1-S1"]["builder_authority"], replacement_builder
        )
        self.assertEqual(next_actions(self.manifest, self.state)["continuation_slices"], [])

        with self.assertRaisesRegex(FactoryError, "different candidate_sha"):
            self.state_transition(
                "M1-S1",
                "request_review",
                {"candidate_sha": self.sha, "checks": self.check_receipts(candidate_sha=self.sha)},
                trusted_actor=replacement_builder,
            )
        self.state_transition(
            "M1-S1",
            "request_review",
            {"candidate_sha": self.sha2, "checks": self.check_receipts(candidate_sha=self.sha2)},
            trusted_actor=replacement_builder,
        )
        self.assertEqual(self.state["slices"]["M1-S1"]["status"], "review")

    def test_remediation_budget_still_forces_replan_after_continuation(self) -> None:
        self.manifest["policy"]["max_remediation_cycles"] = 1
        self.state = initial_state(self.manifest)
        self.milestone_transition("M1", "start_milestone")
        self.state_transition("M1-S1", "start_slice")
        self.state_transition(
            "M1-S1",
            "request_review",
            {"candidate_sha": self.sha, "checks": self.check_receipts(candidate_sha=self.sha)},
        )
        self.state_transition("M1-S1", "request_changes", {"findings": ["finding-0"]})
        self.state_transition("M1-S1", "continue_slice", {"reason": "Try the bounded fix."})
        self.state_transition(
            "M1-S1",
            "request_review",
            {"candidate_sha": self.sha2, "checks": self.check_receipts(candidate_sha=self.sha2)},
        )
        self.state_transition("M1-S1", "request_changes", {"findings": ["finding-1"]})
        self.assertEqual(self.state["slices"]["M1-S1"]["status"], "replan_required")

    def test_default_budget_allows_three_distinct_remediation_cycles(self) -> None:
        candidates = [
            subprocess.check_output(
                ["git", "-C", str(ROOT), "rev-parse", f"HEAD~{index}"], text=True
            ).strip()
            for index in range(4)
        ]
        self.milestone_transition("M1", "start_milestone")
        self.state_transition("M1-S1", "start_slice")
        for index, candidate_sha in enumerate(candidates):
            self.state_transition(
                "M1-S1",
                "request_review",
                {"candidate_sha": candidate_sha, "checks": self.check_receipts(candidate_sha=candidate_sha)},
            )
            self.state_transition(
                "M1-S1",
                "request_changes",
                {"findings": [f"finding-{index}"]},
            )
            if index < 3:
                self.assertEqual(self.state["slices"]["M1-S1"]["status"], "active")
                self.assertEqual(next_actions(self.manifest, self.state)["continuation_slices"], ["M1-S1"])
                self.state_transition("M1-S1", "continue_slice", {"reason": f"Try approach {index + 1}."})
            else:
                self.assertEqual(self.state["slices"]["M1-S1"]["status"], "replan_required")

    def test_missing_acceptance_cannot_complete(self) -> None:
        self.milestone_transition("M1", "start_milestone")
        self.state_transition("M1-S1", "start_slice")
        self.state_transition(
            "M1-S1",
            "request_review",
            {"candidate_sha": self.sha, "checks": self.check_receipts()},
        )
        self.state_transition(
            "M1-S1",
            "pass_review",
            self.review_evidence(self.sha),
        )
        with self.assertRaisesRegex(FactoryError, "acceptance not proven"):
            self.state_transition(
                "M1-S1",
                "complete_slice",
                {
                    "candidate_sha": self.sha,
                    "checks": self.check_receipts(),
                    "acceptance_passed": ["M1-S1-A1"],
                },
            )

    def test_overlap_blocks_parallel_slice(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["slices"][1]["depends_on"] = []
        manifest["slices"][1]["paths"] = ["src/server/projects/handlers.py"]
        state = initial_state(manifest)
        transition(
            manifest,
            state,
            "M1",
            "start_milestone",
            trusted_actor=integrator_actor(manifest),
        )
        transition(
            manifest,
            state,
            "M1-S1",
            "start_slice",
            trusted_actor=builder_actor(manifest),
        )
        self.assertNotIn("M1-S2", next_actions(manifest, state)["startable_slices"])

    def test_startable_slice_set_is_pairwise_disjoint_and_capacity_bounded(self) -> None:
        overlapping = copy.deepcopy(self.manifest)
        third = copy.deepcopy(overlapping["slices"][1])
        third["id"] = "M1-S3"
        overlapping["slices"].append(third)
        overlapping["milestones"][0]["slices"].append("M1-S3")
        for slice_spec in overlapping["slices"]:
            slice_spec["depends_on"] = []
            slice_spec["paths"] = ["src/shared/**"]
        state = initial_state(overlapping)
        transition(
            overlapping,
            state,
            "M1",
            "start_milestone",
            trusted_actor=integrator_actor(overlapping),
        )
        self.assertEqual(next_actions(overlapping, state)["startable_slices"], ["M1-S1"])

        disjoint = copy.deepcopy(self.manifest)
        for slice_spec, path in zip(disjoint["slices"], ("src/one/**", "src/two/**")):
            slice_spec["depends_on"] = []
            slice_spec["paths"] = [path]
        state = initial_state(disjoint)
        transition(
            disjoint,
            state,
            "M1",
            "start_milestone",
            trusted_actor=integrator_actor(disjoint),
        )
        self.assertEqual(next_actions(disjoint, state)["startable_slices"], ["M1-S1", "M1-S2"])

    def test_glob_overlap_and_uncertain_glob_intersections_serialize(self) -> None:
        for first, second in (
            ("src/*/models.py", "src/catalog/models.py"),
            ("src/**/models.py", "src/*/models.py"),
        ):
            with self.subTest(first=first, second=second):
                manifest = copy.deepcopy(self.manifest)
                manifest["slices"][0]["paths"] = [first]
                manifest["slices"][1]["depends_on"] = []
                manifest["slices"][1]["paths"] = [second]
                state = initial_state(manifest)
                transition(
                    manifest,
                    state,
                    "M1",
                    "start_milestone",
                    trusted_actor=integrator_actor(manifest),
                )
                transition(
                    manifest,
                    state,
                    "M1-S1",
                    "start_slice",
                    trusted_actor=builder_actor(manifest),
                )
                self.assertNotIn("M1-S2", next_actions(manifest, state)["startable_slices"])

    def test_reviewer_probe_canonicalizes_relative_and_absolute_workspace_paths(self) -> None:
        absolute_shared = str(ROOT / "src" / "shared" / "**")
        absolute_other = str(ROOT / "src" / "other" / "**")
        self.assertTrue(
            _paths_overlap(["src/shared/**"], [absolute_shared], ROOT)
        )
        self.assertFalse(
            _paths_overlap(["src/shared/**"], [absolute_other], ROOT)
        )

    def test_open_decision_is_rejected_before_dispatch(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["decisions"][0]["status"] = "open"
        result = validate_manifest(manifest)
        self.assertFalse(result["valid"])
        self.assertTrue(any("locked before execution" in item for item in result["errors"]))


class FixtureScenarioTests(unittest.TestCase):
    def load_fixture(self, name: str) -> dict:
        return json.loads((ROOT / "fixtures" / name).read_text(encoding="utf-8"))

    def test_all_project_fixtures_validate(self) -> None:
        for name in ("sites-by-agents.json", "validator.json", "megillah.json"):
            with self.subTest(name=name):
                result = validate_manifest(self.load_fixture(name))
                self.assertTrue(result["valid"], result)

    def test_sites_by_agents_allows_only_two_disjoint_hosted_slices(self) -> None:
        manifest = self.load_fixture("sites-by-agents.json")
        state = initial_state(manifest)
        transition(
            manifest,
            state,
            "SBA-M1",
            "start_milestone",
            trusted_actor=integrator_actor(manifest),
        )
        self.assertEqual(
            set(next_actions(manifest, state)["startable_slices"]),
            {"SBA-M1-S1", "SBA-M1-S2"},
        )

    def test_integrator_authority_is_mission_wide_across_milestones(self) -> None:
        manifest = self.load_fixture("sites-by-agents.json")
        manifest["milestones"][1]["depends_on"] = []
        state = initial_state(manifest)
        actor = integrator_actor(manifest, "mission-integrator")
        transition(
            manifest,
            state,
            "SBA-M1",
            "start_milestone",
            trusted_actor=actor,
        )
        transition(
            manifest,
            state,
            "SBA-M1",
            "replan",
            {"reason": "scope changed", "decision": "replan milestone one"},
            trusted_actor=actor,
        )
        baseline = copy.deepcopy(state)
        with self.assertRaisesRegex(FactoryError, "not authorized"):
            transition(
                manifest,
                state,
                "SBA-M2",
                "start_milestone",
                trusted_actor={**actor, "session_id": "replacement-session"},
            )
        self.assertEqual(state, baseline)

    def test_slice_story_must_be_owned_by_its_milestone(self) -> None:
        manifest = self.load_fixture("sites-by-agents.json")
        manifest["slices"][2]["story_id"] = "US-1"
        result = validate_manifest(manifest)
        self.assertFalse(result["valid"])
        self.assertTrue(any("story_id must be owned by milestone SBA-M2" in item for item in result["errors"]))

    def test_bundled_milestone_cannot_omit_owned_story_criterion(self) -> None:
        manifest = self.load_fixture("sites-by-agents.json")
        milestone = manifest["milestones"][0]
        milestone["acceptance"] = [
            item for item in milestone["acceptance"] if item["id"] != "SBA-M1-S2-US-NEG"
        ]
        result = validate_manifest(manifest)
        self.assertFalse(result["valid"])
        self.assertTrue(any(
            "story US-2 criterion SBA-M1-S2-US-NEG" in item for item in result["errors"]
        ))

    def test_validator_serialises_domain_before_disjoint_ui_and_export(self) -> None:
        manifest = self.load_fixture("validator.json")
        state = initial_state(manifest)
        transition(
            manifest,
            state,
            "VAL-M1",
            "start_milestone",
            trusted_actor=integrator_actor(manifest),
        )
        self.assertEqual(next_actions(manifest, state)["startable_slices"], ["VAL-M1-S1"])
        by_id = {item["id"]: item for item in manifest["slices"]}
        self.assertEqual(by_id["VAL-M1-S2"]["depends_on"], ["VAL-M1-S1"])
        self.assertEqual(by_id["VAL-M1-S3"]["depends_on"], ["VAL-M1-S1"])
        self.assertTrue(set(by_id["VAL-M1-S2"]["paths"]).isdisjoint(by_id["VAL-M1-S3"]["paths"]))

    def test_megillah_requires_locked_communications_decision(self) -> None:
        manifest = self.load_fixture("megillah.json")
        manifest["decisions"][0]["status"] = "open"
        result = validate_manifest(manifest)
        self.assertFalse(result["valid"])
        self.assertTrue(any("locked before execution" in item for item in result["errors"]))


class PluginSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sha = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
        by_provider: dict[str, list[str]] = {}
        for ref in TEMPLATE["models"].values():
            by_provider.setdefault(ref["provider"], []).append(ref["model"])
        runtime_catalog = {
            "providers": [
                {"slug": provider, "authenticated": True, "models": models}
                for provider, models in by_provider.items()
            ]
        }
        catalog_patch = patch("plugin._active_model_catalog", return_value=runtime_catalog)
        catalog_patch.start()
        self.addCleanup(catalog_patch.stop)

    def test_validate_handler_loads_existing_attested_state_and_rejects_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.json"
            state_path = Path(tmp) / "state.json"
            manifest_path.write_text(json.dumps(TEMPLATE), encoding="utf-8")
            existing = initial_state(TEMPLATE)
            state_path.write_text(json.dumps(existing), encoding="utf-8")
            result = json.loads(
                _handle_validate(
                    {"manifest_path": str(manifest_path), "state_path": str(state_path)}
                )
            )
            self.assertTrue(result["success"], result)
            self.assertTrue(state_path.exists())
            self.assertEqual(result["revision"], 0)
            self.assertEqual(result["next"]["startable_milestones"], ["M1"])
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), existing)
            state_path.unlink()
            missing = json.loads(
                _handle_validate(
                    {"manifest_path": str(manifest_path), "state_path": str(state_path)}
                )
            )
            self.assertFalse(missing["success"])
            self.assertFalse(state_path.exists())
            with patch.dict(
                "os.environ",
                {"HERMES_FACTORY_MANIFEST": str(manifest_path)},
                clear=False,
            ):
                os.environ.pop("HERMES_FACTORY_STATE", None)
                rogue = json.loads(_handle_next({"manifest_path": str(manifest_path), "state_path": str(Path(tmp) / "rogue.json")}))
            self.assertFalse(rogue["success"])
            self.assertIn("pins factory operations", rogue["error"])
            spec = importlib.util.spec_from_file_location("dark_factory_engine_alias", ROOT / "plugin" / "engine.py")
            self.assertIsNotNone(spec)
            alias = importlib.util.module_from_spec(spec)
            assert spec and spec.loader
            spec.loader.exec_module(alias)
            self.assertEqual(alias.next_actions(TEMPLATE, initial_state(TEMPLATE))["startable_milestones"], ["M1"])

    def test_transition_revision_cas_prevents_stale_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.json"
            state_path = Path(tmp) / "state.json"
            manifest_path.write_text(json.dumps(TEMPLATE), encoding="utf-8")
            state_path.write_text(json.dumps(initial_state(TEMPLATE)), encoding="utf-8")
            first = save_transition(
                manifest_path,
                state_path,
                "M1",
                "start_milestone",
                expected_revision=0,
                trusted_actor=integrator_actor(TEMPLATE),
            )
            self.assertEqual(first["revision"], 1)
            with self.assertRaisesRegex(FactoryError, "revision conflict"):
                save_transition(manifest_path, state_path, "M1-S1", "start_slice", expected_revision=0)
            forged = json.loads(state_path.read_text(encoding="utf-8"))
            forged["slices"]["M1-S1"]["status"] = "completed"
            state_path.write_text(json.dumps(forged), encoding="utf-8")
            validation = json.loads(_handle_validate({"manifest_path": str(manifest_path), "state_path": str(state_path)}))
            self.assertFalse(validation["success"])
            self.assertIn("attestation", validation["error"])

    def test_rejected_milestone_actor_does_not_change_saved_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.json"
            state_path = Path(tmp) / "state.json"
            manifest_path.write_text(json.dumps(TEMPLATE), encoding="utf-8")
            state_path.write_text(json.dumps(initial_state(TEMPLATE)), encoding="utf-8")
            actor = integrator_actor(TEMPLATE)
            save_transition(
                manifest_path,
                state_path,
                "M1",
                "start_milestone",
                expected_revision=0,
                trusted_actor=actor,
            )
            before = state_path.read_bytes()
            for rejected_actor in (None, {**actor, "session_id": "other-session"}):
                with self.subTest(actor=rejected_actor), self.assertRaises(FactoryError):
                    save_transition(
                        manifest_path,
                        state_path,
                        "M1",
                        "block",
                        {
                            "reason": "must not persist",
                            "owner": "integrator",
                            "resume_condition": "never",
                        },
                        expected_revision=1,
                        trusted_actor=rejected_actor,
                    )
                self.assertEqual(state_path.read_bytes(), before)
                self.assertEqual(json.loads(before)["revision"], 1)

    def test_transition_fails_closed_if_active_state_was_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.json"
            state_path = Path(tmp) / "state.json"
            manifest_path.write_text(json.dumps(TEMPLATE), encoding="utf-8")
            state_path.write_text(json.dumps(initial_state(TEMPLATE)), encoding="utf-8")
            save_transition(
                manifest_path,
                state_path,
                "M1",
                "start_milestone",
                expected_revision=0,
                trusted_actor=integrator_actor(TEMPLATE),
            )
            state_path.unlink()

            with self.assertRaisesRegex(FactoryError, "validate the factory and re-arm"):
                save_transition(
                    manifest_path, state_path, "M1-S1", "start_slice", expected_revision=1
                )
            self.assertFalse(state_path.exists())

    def test_review_attestation_binds_session_role_model_entity_and_sha(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.json"
            manifest = copy.deepcopy(TEMPLATE)
            manifest["mission"]["workspace_path"] = str(ROOT)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            verifier = manifest["models"]["verifier"]
            with patch.dict(
                "os.environ",
                {
                    "HERMES_HOME": str(Path(tmp) / "home"),
                    "HERMES_FACTORY_MANIFEST": str(manifest_path),
                    "HERMES_FACTORY_ROLE": "verifier",
                    "HERMES_FACTORY_PROVIDER": verifier["provider"],
                    "HERMES_FACTORY_MODEL": verifier["model"],
                },
                clear=False,
            ):
                result = json.loads(_handle_attest_review(
                    {"entity_id": "M1-S1", "candidate_sha": self.sha}, session_id="trusted-session"
                ))
            self.assertTrue(result["success"], result)
            self.assertEqual(result["receipt"]["session_id"], "trusted-session")
            self.assertTrue(result["receipt"]["attestation"])

    def test_beads_apply_is_integrator_only_even_without_strict_mode(self) -> None:
        manifest = copy.deepcopy(TEMPLATE)
        manifest["execution"]["graph_mode"] = "apply"
        integrator = manifest["models"]["integrator"]
        base_environment = {
            "HERMES_FACTORY_MANIFEST": "manifest.json",
            "HERMES_FACTORY_STATE": "state.json",
            "HERMES_FACTORY_PROVIDER": integrator["provider"],
            "HERMES_FACTORY_MODEL": integrator["model"],
        }
        with patch("plugin._resolve_paths", return_value=("manifest.json", "state.json")), patch(
            "plugin.load_manifest", return_value=manifest
        ), patch("plugin.apply_graph_plan") as apply:
            for role in ("", "builder", "verifier", "adversary", "holdout", "Integrator"):
                environment = {**base_environment, "HERMES_FACTORY_ROLE": role}
                with self.subTest(role=role), patch.dict("os.environ", environment, clear=True):
                    result = json.loads(
                        _handle_beads_apply({}, session_id="integrator-session")
                    )
                    self.assertFalse(result["success"])
                    self.assertEqual(
                        result["error"], "Beads graph application is not authorized"
                    )
        apply.assert_not_called()

    def test_beads_apply_rejects_plan_mode_as_read_only(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "HERMES_FACTORY_MANIFEST": "manifest.json",
                "HERMES_FACTORY_STATE": "state.json",
                "HERMES_FACTORY_ROLE": "integrator",
            },
            clear=True,
        ), patch(
            "plugin._resolve_paths", return_value=("manifest.json", "state.json")
        ), patch("plugin.load_manifest", return_value=copy.deepcopy(TEMPLATE)):
            result = json.loads(_handle_beads_apply({}))
        self.assertFalse(result["success"])
        self.assertIn("graph_mode is not apply", result["error"])

    def test_beads_apply_settings_are_pinned_to_manifest(self) -> None:
        manifest = copy.deepcopy(TEMPLATE)
        manifest["execution"]["beads_directory"] = "/approved/store"
        manifest["execution"]["beads_isolated_authorized"] = False
        directory, executable, authorized = _beads_settings(
            manifest,
            {
                "beads_directory": "/unapproved/store",
                "beads_isolated_authorized": True,
                "bd_executable": "/opt/bin/bd",
            },
        )
        self.assertEqual(directory, "/approved/store")
        self.assertEqual(executable, "bd")
        self.assertFalse(authorized)

    def test_strict_guard_blocks_uncontracted_kanban_card(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.json"
            manifest_path.write_text(json.dumps(TEMPLATE), encoding="utf-8")
            with patch.dict(
                "os.environ",
                {
                    "HERMES_FACTORY_STRICT": "1",
                    "HERMES_FACTORY_MANIFEST": str(manifest_path),
                },
                clear=False,
            ):
                verdict = _pre_tool_guard(
                    tool_name="kanban_create",
                    args={"title": "fix test", "body": "make it green"},
                )
            self.assertEqual(verdict["action"], "block")

    def test_strict_guard_caps_delegation_at_two_workers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.json"
            manifest_path.write_text(json.dumps(TEMPLATE), encoding="utf-8")
            tasks = [
                {"goal": "Acceptance: pass\nEvidence: test\nForbidden: publish"}
                for _ in range(3)
            ]
            with patch.dict(
                "os.environ",
                {
                    "HERMES_FACTORY_STRICT": "1",
                    "HERMES_FACTORY_MANIFEST": str(manifest_path),
                },
                clear=False,
            ):
                verdict = _pre_tool_guard(tool_name="delegate_task", args={"tasks": tasks})
            self.assertEqual(verdict["action"], "block")

    def test_strict_reviewer_cannot_write_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.json"
            manifest_path.write_text(json.dumps(TEMPLATE), encoding="utf-8")
            with patch.dict(
                "os.environ",
                {
                    "HERMES_FACTORY_STRICT": "1",
                    "HERMES_FACTORY_MANIFEST": str(manifest_path),
                    "HERMES_FACTORY_ROLE": "reviewer",
                },
                clear=False,
            ):
                verdict = _pre_tool_guard(
                    tool_name="write_file", args={"path": "src/app.py", "content": "changed"}
                )
            self.assertEqual(verdict["action"], "block")
            self.assertIn("evidence-only", verdict["message"])

    def test_strict_named_review_roles_cannot_use_shell_or_code_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.json"
            manifest_path.write_text(json.dumps(TEMPLATE), encoding="utf-8")
            for role, tool_name, args in (
                ("builder", "write_file", {"path": ".hermes/factory/state.json", "content": "{}"}),
                ("verifier", "write_file", {"path": "src/app.py", "content": "changed"}),
                ("adversary", "terminal", {"command": "printf changed > src/app.py"}),
                ("holdout", "execute_code", {"code": "open('src/app.py','w').write('changed')"}),
            ):
                with self.subTest(role=role), patch.dict(
                    "os.environ",
                    {"HERMES_FACTORY_STRICT": "1", "HERMES_FACTORY_MANIFEST": str(manifest_path), "HERMES_FACTORY_ROLE": role},
                    clear=False,
                ):
                    verdict = _pre_tool_guard(tool_name=tool_name, args=args)
                    self.assertEqual(verdict["action"], "block")

    def test_strict_guard_blocks_inline_heredoc_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.json"
            manifest_path.write_text(json.dumps(TEMPLATE), encoding="utf-8")
            with patch.dict(
                "os.environ",
                {
                    "HERMES_FACTORY_STRICT": "1",
                    "HERMES_FACTORY_MANIFEST": str(manifest_path),
                },
                clear=False,
            ):
                verdict = _pre_tool_guard(
                    tool_name="terminal", args={"command": "python3 - <<'PY'\nprint('x')\nPY"}
                )
            self.assertEqual(verdict["action"], "block")
            self.assertIn("heredoc", verdict["message"])


class CardContractTests(unittest.TestCase):
    def test_contract_markers_are_required(self) -> None:
        result = lint_card("fix the test", "please make it green")
        self.assertFalse(result["valid"])
        self.assertTrue(result["warnings"])

    def test_full_contract_passes(self) -> None:
        body = """Factory-Milestone: M1
Factory-Slice: M1-S1
Outcome: A verified user can create and reopen one project.
Acceptance:
- M1-S1-A1 create and reopen passes
Evidence:
- pytest tests/projects -q
Boundaries:
- src/projects only
Forbidden:
- Do not change identity authority or deployment configuration
Handoff:
- Return the candidate SHA and raw receipt paths to the integrator
Stop/escalate:
- second similar failure returns to integrator
This is a durable work order with exact scope, evidence, and terminal conditions for the assigned worker.
"""
        result = lint_card("Implement durable project ownership", body)
        self.assertTrue(result["valid"], result)

        for title in (
            "Fix lint",
            "Add one unit test",
            "Run a single typecheck",
            "Update the snapshot",
            "Fix one typo",
            "Repair one failing unit test for login",
            "Remediate a failing lint check in login",
            "Resolve one failing typecheck for login",
            "Correct a failing snapshot for login",
            "Patch one typo in login",
        ):
            with self.subTest(title=title):
                micro = lint_card(title, body)
                self.assertFalse(micro["valid"])
                self.assertTrue(any("micro-remediation" in item for item in micro["errors"]))

        functional = lint_card("Add durable project reopening for verified users", body)
        self.assertTrue(functional["valid"], functional)

        exact_title = lint_card("Change one CSS color", body)
        self.assertFalse(exact_title["valid"])
        self.assertTrue(any("micro-remediation" in item for item in exact_title["errors"]))

        release_micro_titles = (
            "Change only the CSS color in the header for this release",
            "Update only one README sentence in this release",
            "Rename just one local variable in the parser",
            "Change only one source-code comment for this release",
        )
        for title in release_micro_titles:
            with self.subTest(title=title):
                release_micro_title = lint_card(title, body)
                self.assertFalse(release_micro_title["valid"])
                self.assertTrue(
                    any("micro-remediation" in item for item in release_micro_title["errors"])
                )

        functional_title = lint_card(
            "Enable users to change one profile color and retain it after reload",
            body,
        )
        self.assertTrue(functional_title["valid"], functional_title)

        capability_title = lint_card(
            "Allow users to change only their own profile color theme with persisted preferences and accessibility checks",
            body,
        )
        self.assertTrue(capability_title["valid"], capability_title)

        for durable_title in (
            "Generate versioned operator documentation from the API schema with validation checks",
            "Refactor parser internals so malformed input is rejected without corrupting durable state",
        ):
            with self.subTest(durable_title=durable_title):
                self.assertTrue(lint_card(durable_title, body)["valid"])

        micro_outcome = body.replace(
            "Outcome: A verified user can create and reopen one project.",
            "Outcome: Add one unit test for project reopening.",
        )
        result = lint_card("Implement durable project reopening", micro_outcome)
        self.assertFalse(result["valid"])
        self.assertTrue(any("Outcome content" in item for item in result["errors"]))

        repair_outcome = body.replace(
            "Outcome: A verified user can create and reopen one project.",
            "Outcome: Repair one failing unit test for login",
        )
        result = lint_card("Implement durable project reopening", repair_outcome)
        self.assertFalse(result["valid"])
        self.assertTrue(any("Outcome content" in item for item in result["errors"]))

        semantic_micro_outcomes = (
            "Change only the CSS color in the header for this release",
            "Update only one README sentence in this release",
            "Rename just one local variable in the parser",
            "Change only one source-code comment for this release",
        )
        for outcome in semantic_micro_outcomes:
            with self.subTest(outcome=outcome):
                semantic_micro_outcome = body.replace(
                    "Outcome: A verified user can create and reopen one project.",
                    f"Outcome: {outcome}",
                )
                result = lint_card("Implement durable project reopening", semantic_micro_outcome)
                self.assertFalse(result["valid"])
                self.assertTrue(any("Outcome content" in item for item in result["errors"]))

        functional_color_outcome = body.replace(
            "Outcome: A verified user can create and reopen one project.",
            "Outcome: A user can change one profile color and retain it after reload",
        )
        result = lint_card("Implement durable project reopening", functional_color_outcome)
        self.assertTrue(result["valid"], result)

        capability_outcome = body.replace(
            "Outcome: A verified user can create and reopen one project.",
            "Outcome: Allow users to change only their own profile color theme with persisted preferences and accessibility checks",
        )
        result = lint_card("Implement durable profile themes", capability_outcome)
        self.assertTrue(result["valid"], result)

        for title, outcome in (
            (
                "Generate operator documentation",
                "Generate versioned operator documentation from the API schema with validation checks",
            ),
            (
                "Refactor parser behavior",
                "Refactor parser internals so malformed input is rejected without corrupting durable state",
            ),
        ):
            with self.subTest(outcome=outcome):
                durable_outcome = body.replace(
                    "Outcome: A verified user can create and reopen one project.",
                    f"Outcome: {outcome}",
                )
                self.assertTrue(lint_card(title, durable_outcome)["valid"])

        coordinate_free = body.replace(
            "- src/projects only", "- project backend implementation only"
        )
        result = lint_card("Implement durable project reopening", coordinate_free)
        self.assertFalse(result["valid"])
        self.assertTrue(any("parseable" in item for item in result["errors"]))

    def test_empty_or_substring_only_sections_fail(self) -> None:
        empty = """Factory-Milestone:
Factory-Slice: M1-S1
Outcome: A complete outcome mentions Acceptance: and Evidence: in prose.
Boundaries:
Acceptance:
Evidence:
Forbidden:
Handoff:
Stop/escalate:
"""
        result = lint_card("Implement durable outcome", empty)
        self.assertFalse(result["valid"])
        self.assertTrue(any("Factory-Milestone" in item for item in result["errors"]))
        self.assertTrue(any("Acceptance" in item for item in result["errors"]))

        incomplete = """Factory-Milestone: M1
Factory-Slice: M1-S1
Outcome: A durable observable product result is delivered.
Boundaries: src/projects only
Acceptance: M1-S1-A1 passes against the candidate
Evidence: pytest tests/projects -q
Forbidden: do not deploy
Handoff:
Stop/escalate: stop after a repeated failure
"""
        self.assertFalse(lint_card("Implement durable outcome", incomplete)["valid"])


if __name__ == "__main__":
    unittest.main()
