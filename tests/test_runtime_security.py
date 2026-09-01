from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from plugin import (
    _active_catalog_refs,
    _active_model_catalog,
    _beads_settings,
    _handle_attest_review,
    _handle_beads_apply,
    _handle_beads_plan,
    _handle_compile,
    _handle_next,
    _handle_preflight,
    _handle_transition,
    _handle_validate,
    _pre_tool_guard,
    initial_state,
    register,
)
from plugin.engine import transition

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = json.loads((ROOT / "templates" / "manifest.example.json").read_text(encoding="utf-8"))


def fixture_catalog(manifest: dict | None = None) -> dict:
    source = manifest or TEMPLATE
    providers: dict[str, list[str]] = {}
    for ref in source["models"].values():
        providers.setdefault(ref["provider"], []).append(ref["model"])
    return {
        "providers": [
            {"slug": provider, "authenticated": True, "models": sorted(set(models))}
            for provider, models in providers.items()
        ],
        "credentials_included": False,
    }


class RuntimeModelInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        control = self.root / ".hermes" / "factory"
        control.mkdir(parents=True)
        self.manifest_path = control / "manifest.json"
        self.state_path = control / "state.json"
        self.manifest = copy.deepcopy(TEMPLATE)
        self.manifest["mission"]["workspace_path"] = str(ROOT)
        for index, threat in enumerate(self.manifest["security"]["threat_scenarios"], start=1):
            threat.setdefault("id", f"T{index}")
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        self.params = {
            "manifest_path": str(self.manifest_path),
            "state_path": str(self.state_path),
        }

    def write_initial_state(self) -> None:
        self.state_path.write_text(json.dumps(initial_state(self.manifest)), encoding="utf-8")

    def write_bound_integrator_state(self, session_id: str = "integrator-session") -> dict[str, str]:
        integrator = self.manifest["models"]["integrator"]
        actor = {
            "session_id": session_id,
            "provider": integrator["provider"],
            "model": integrator["model"],
        }
        state = initial_state(self.manifest)
        transition(
            self.manifest,
            state,
            "M1",
            "start_milestone",
            trusted_actor=actor,
        )
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        return actor

    def beads_apply_environment(self) -> dict[str, str]:
        integrator = self.manifest["models"]["integrator"]
        return {
            "HERMES_FACTORY_MANIFEST": str(self.manifest_path),
            "HERMES_FACTORY_STATE": str(self.state_path),
            "HERMES_FACTORY_ROLE": "integrator",
            "HERMES_FACTORY_PROVIDER": integrator["provider"],
            "HERMES_FACTORY_MODEL": integrator["model"],
        }

    def test_active_model_catalog_requires_exact_auth_and_string_model_ids(self) -> None:
        auth_rows = []
        for name, authenticated in (
            ("missing", object()),
            ("null", None),
            ("false", False),
            ("string", "true"),
            ("integer", 1),
        ):
            row = {"slug": f"provider-{name}", "models": [f"model-{name}"]}
            if name != "missing":
                row["authenticated"] = authenticated
            auth_rows.append(row)
        payload = {
            "providers": [
                *auth_rows,
                {
                    "slug": "OpenAI-Codex",
                    "authenticated": True,
                    "models": [
                        "gpt-5.6-sol-900k",
                        "",
                        "   ",
                        {"id": "dict-id"},
                        {"model": "dict-model"},
                        {"name": "dict-name"},
                        7,
                        object(),
                    ],
                },
                {"slug": 123, "authenticated": True, "models": ["object-provider-model"]},
                {"slug": "  ", "authenticated": True, "models": ["blank-provider-model"]},
            ],
            "provider": "OpenAI-Codex",
            "model": "gpt-5.6-sol-900k",
        }
        context = object()
        with mock.patch(
            "hermes_cli.inventory.load_picker_context", return_value=context
        ) as load_context, mock.patch(
            "hermes_cli.inventory.build_models_payload", return_value=payload
        ) as build_payload:
            catalog = _active_model_catalog()

        load_context.assert_called_once_with()
        self.assertIs(build_payload.call_args.args[0], context)
        self.assertEqual(
            catalog,
            {
                "providers": [
                    {
                        "slug": "openai-codex",
                        "authenticated": True,
                        "models": ["gpt-5.6-sol-900k"],
                    }
                ],
                "current": {
                    "provider": "openai-codex",
                    "model": "gpt-5.6-sol-900k",
                },
                "credentials_included": False,
            },
        )

    def test_active_catalog_refs_requires_exact_auth_and_string_model_ids(self) -> None:
        rows = []
        for name, authenticated in (
            ("missing", object()),
            ("null", None),
            ("false", False),
            ("string", "true"),
            ("integer", 1),
        ):
            row = {"slug": f"provider-{name}", "models": [f"model-{name}"]}
            if name != "missing":
                row["authenticated"] = authenticated
            rows.append(row)
        rows.extend(
            [
                {
                    "slug": "OpenAI-Codex",
                    "authenticated": True,
                    "models": [
                        "gpt-5.6-sol-900k",
                        "",
                        "   ",
                        {"id": "dict-id"},
                        {"model": "dict-model"},
                        {"name": "dict-name"},
                        7,
                        object(),
                    ],
                },
                {"slug": 123, "authenticated": True, "models": ["object-provider-model"]},
                {"slug": "  ", "authenticated": True, "models": ["blank-provider-model"]},
                object(),
            ]
        )

        self.assertEqual(
            _active_catalog_refs({"providers": rows}),
            {("openai-codex", "gpt-5.6-sol-900k")},
        )

    def test_validate_requires_compiled_state_then_loads_existing_attested_state(self) -> None:
        with mock.patch("plugin._active_model_catalog", return_value=fixture_catalog(self.manifest)):
            missing = json.loads(_handle_validate(self.params))
        self.assertFalse(missing["success"], missing)
        self.assertIn("unavailable", missing["error"])
        self.assertFalse(self.state_path.exists())
        self.assertFalse((self.root / ".hermes" / ".factory.state.lock").exists())
        exposed = json.dumps(missing)
        self.assertNotIn(str(self.manifest_path), exposed)
        self.assertNotIn(str(self.state_path), exposed)

        self.write_initial_state()
        before = self.state_path.read_bytes()
        with mock.patch("plugin._active_model_catalog", return_value=fixture_catalog(self.manifest)):
            result = json.loads(_handle_validate(self.params))
        self.assertTrue(result["success"], result)
        self.assertTrue(self.state_path.exists())
        self.assertEqual(self.state_path.read_bytes(), before)
        self.assertEqual(result["revision"], 0)
        self.assertEqual(result["next"]["startable_milestones"], ["M1"])

    def test_validate_and_next_fail_closed_after_progressed_state_is_deleted(self) -> None:
        self.write_initial_state()
        integrator = self.manifest["models"]["integrator"]
        environment = {
            "HERMES_FACTORY_ROLE": "integrator",
            "HERMES_FACTORY_PROVIDER": integrator["provider"],
            "HERMES_FACTORY_MODEL": integrator["model"],
        }
        with mock.patch.dict(os.environ, environment, clear=True), mock.patch(
            "plugin._active_model_catalog", return_value=fixture_catalog(self.manifest)
        ):
            validated = json.loads(_handle_validate(self.params))
            started = json.loads(
                _handle_transition(
                    {
                        **self.params,
                        "entity_id": "M1",
                        "action": "start_milestone",
                        "expected_revision": 0,
                    },
                    session_id="integrator-session",
                )
            )
        self.assertTrue(validated["success"], validated)
        self.assertTrue(started["success"], started)
        self.assertEqual(started["revision"], 1)
        progressed = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(progressed["milestones"]["M1"]["status"], "active")
        self.state_path.unlink()

        with mock.patch("plugin._active_model_catalog", return_value=fixture_catalog(self.manifest)):
            results = (
                json.loads(_handle_validate(self.params)),
                json.loads(_handle_next(self.params)),
            )
        for result in results:
            with self.subTest(result=result):
                self.assertFalse(result["success"], result)
                self.assertIn("unavailable", result["error"])
                exposed = json.dumps(result)
                self.assertNotIn(str(self.manifest_path), exposed)
                self.assertNotIn(str(self.state_path), exposed)
                self.assertFalse(self.state_path.exists())

    def test_validate_and_next_preserve_tampered_state_failure(self) -> None:
        self.write_initial_state()
        forged = json.loads(self.state_path.read_text(encoding="utf-8"))
        forged["revision"] = 7
        forged["attacker_secret"] = "runtime-secret-value"
        self.state_path.write_text(json.dumps(forged), encoding="utf-8")
        before = self.state_path.read_bytes()

        with mock.patch("plugin._active_model_catalog", return_value=fixture_catalog(self.manifest)):
            results = (
                json.loads(_handle_validate(self.params)),
                json.loads(_handle_next(self.params)),
            )
        for result in results:
            with self.subTest(result=result):
                self.assertFalse(result["success"], result)
                self.assertIn("attestation", result["error"])
                exposed = json.dumps(result)
                self.assertNotIn(str(self.state_path), exposed)
                self.assertNotIn("runtime-secret-value", exposed)
                self.assertEqual(self.state_path.read_bytes(), before)

    def test_ghost_model_blocks_all_runtime_dispatch_and_mutation_handlers(self) -> None:
        ghost = copy.deepcopy(self.manifest)
        ghost["models"]["builder"] = {"provider": "ghost-provider", "model": "ghost-model"}
        ghost["execution"]["graph_mode"] = "apply"
        self.manifest_path.write_text(json.dumps(ghost), encoding="utf-8")
        catalog = fixture_catalog(self.manifest)
        base = dict(self.params)
        calls = (
            lambda: _handle_validate(base),
            lambda: _handle_next(base),
            lambda: _handle_transition(
                {**base, "entity_id": "M1", "action": "start_milestone", "expected_revision": 0},
                session_id="integrator-session",
            ),
            lambda: _handle_attest_review({**base, "entity_id": "M1-S1", "candidate_sha": "0" * 40}, session_id="review-session"),
            lambda: _handle_beads_plan(base),
            lambda: _handle_beads_apply(base, session_id="integrator-session"),
        )
        environment = {
            "HERMES_FACTORY_MANIFEST": str(self.manifest_path),
            "HERMES_FACTORY_STATE": str(self.state_path),
            "HERMES_FACTORY_ROLE": "integrator",
            "HERMES_FACTORY_PROVIDER": ghost["models"]["integrator"]["provider"],
            "HERMES_FACTORY_MODEL": ghost["models"]["integrator"]["model"],
        }
        with mock.patch.dict(os.environ, environment, clear=True), mock.patch(
            "plugin._active_model_catalog", return_value=catalog
        ), mock.patch("plugin.apply_graph_plan") as apply:
            for call in calls:
                with self.subTest(handler=call):
                    result = json.loads(call())
                    self.assertFalse(result["success"], result)
                    self.assertTrue(any("active-profile inventory" in item for item in result.get("errors", [])), result)
        apply.assert_not_called()
        self.assertFalse(self.state_path.exists())

    def test_inventory_failure_is_generic_for_every_inventory_backed_tool_handler(self) -> None:
        self.manifest["execution"]["graph_mode"] = "apply"
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        credential_error = RuntimeError(
            "Authorization: Bearer sk-dummy-do-not-leak password=also-secret"
        )
        calls = (
            lambda: _handle_validate(self.params),
            lambda: _handle_next(self.params),
            lambda: _handle_transition(
                {
                    **self.params,
                    "entity_id": "M1",
                    "action": "start_milestone",
                    "expected_revision": 0,
                },
                session_id="integrator-session",
            ),
            lambda: _handle_attest_review(
                {**self.params, "entity_id": "M1-S1", "candidate_sha": "0" * 40},
                session_id="review-session",
            ),
            lambda: _handle_beads_plan(self.params),
            lambda: _handle_beads_apply(self.params, session_id="integrator-session"),
            lambda: _handle_preflight({"setup": {}}),
            lambda: _handle_compile({"setup": {}}),
        )
        environment = {
            "HERMES_FACTORY_MANIFEST": str(self.manifest_path),
            "HERMES_FACTORY_STATE": str(self.state_path),
            "HERMES_FACTORY_ROLE": "integrator",
            "HERMES_FACTORY_PROVIDER": self.manifest["models"]["integrator"]["provider"],
            "HERMES_FACTORY_MODEL": self.manifest["models"]["integrator"]["model"],
        }
        with mock.patch.dict(os.environ, environment, clear=True), mock.patch(
            "plugin._active_model_catalog", side_effect=credential_error
        ):
            for call in calls:
                with self.subTest(handler=call):
                    result = json.loads(call())
                    encoded = json.dumps(result)
                    self.assertFalse(result["success"], result)
                    self.assertEqual(result.get("code"), "model_inventory_unavailable", result)
                    self.assertIn("inventory is unavailable", encoded)
                    self.assertNotIn("sk-dummy-do-not-leak", encoded)
                    self.assertNotIn("also-secret", encoded)
        self.assertFalse(self.state_path.exists())

    def test_beads_apply_requires_both_explicit_pins_and_rejects_runtime_substitution(self) -> None:
        self.manifest["execution"]["graph_mode"] = "apply"
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        self.write_bound_integrator_state()
        before = self.state_path.read_bytes()
        environment = self.beads_apply_environment()
        cases = (
            ("missing-manifest-pin", {key: value for key, value in environment.items() if key != "HERMES_FACTORY_MANIFEST"}, self.params),
            ("blank-manifest-pin", {**environment, "HERMES_FACTORY_MANIFEST": "  \t"}, self.params),
            ("missing-state-pin", {key: value for key, value in environment.items() if key != "HERMES_FACTORY_STATE"}, self.params),
            ("blank-state-pin", {**environment, "HERMES_FACTORY_STATE": " \t "}, self.params),
            (
                "runtime-path-substitution",
                environment,
                {
                    "manifest_path": str(self.root / "rogue-manifest.json"),
                    "state_path": str(self.root / "rogue-state.json"),
                },
            ),
        )
        with mock.patch("plugin.apply_graph_plan") as apply, mock.patch(
            "plugin._active_model_catalog", return_value=fixture_catalog(self.manifest)
        ):
            for name, runtime_environment, params in cases:
                with self.subTest(case=name), mock.patch.dict(
                    os.environ, runtime_environment, clear=True
                ):
                    result = json.loads(
                        _handle_beads_apply(params, session_id="integrator-session")
                    )
                encoded = json.dumps(result)
                self.assertFalse(result["success"], result)
                self.assertEqual(result["error"], "Beads graph application is not authorized")
                self.assertNotIn(str(self.manifest_path), encoded)
                self.assertNotIn(str(self.state_path), encoded)
                self.assertNotIn("rogue-manifest", encoded)
                self.assertEqual(self.state_path.read_bytes(), before)
        apply.assert_not_called()

    def test_beads_apply_reproduces_wrong_actor_and_missing_state_review(self) -> None:
        self.manifest["execution"]["graph_mode"] = "apply"
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        environment = self.beads_apply_environment()
        wrong_actor = {
            **environment,
            "HERMES_FACTORY_PROVIDER": "reviewer-wrong-provider",
            "HERMES_FACTORY_MODEL": "reviewer-wrong-model",
        }
        role_only = {
            "HERMES_FACTORY_MANIFEST": str(self.manifest_path),
            "HERMES_FACTORY_STATE": str(self.state_path),
            "HERMES_FACTORY_ROLE": "integrator",
        }
        cases = (
            ("role-only", role_only, None),
            ("wrong-provider-model-unbound-session", wrong_actor, "unbound-session"),
            ("missing-state", environment, "integrator-session"),
        )
        with mock.patch("plugin.apply_graph_plan") as apply, mock.patch(
            "plugin._active_model_catalog", return_value=fixture_catalog(self.manifest)
        ):
            for name, runtime_environment, session_id in cases:
                with self.subTest(case=name), mock.patch.dict(
                    os.environ, runtime_environment, clear=True
                ):
                    result = json.loads(
                        _handle_beads_apply(self.params, session_id=session_id)
                    )
                encoded = json.dumps(result)
                self.assertFalse(result["success"], result)
                self.assertEqual(result["error"], "Beads graph application is not authorized")
                self.assertNotIn(str(self.manifest_path), encoded)
                self.assertNotIn(str(self.state_path), encoded)
                self.assertNotIn("reviewer-wrong", encoded)
        apply.assert_not_called()
        self.assertFalse(self.state_path.exists())

    def test_beads_apply_rejects_absent_mismatched_unreadable_incompatible_and_tampered_state(self) -> None:
        self.manifest["execution"]["graph_mode"] = "apply"
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        environment = self.beads_apply_environment()
        catalog = fixture_catalog(self.manifest)

        def denied(session_id: str) -> None:
            before = self.state_path.read_bytes()
            with mock.patch.dict(os.environ, environment, clear=True), mock.patch(
                "plugin._active_model_catalog", return_value=catalog
            ), mock.patch("plugin.apply_graph_plan") as apply:
                result = json.loads(
                    _handle_beads_apply(self.params, session_id=session_id)
                )
            encoded = json.dumps(result)
            self.assertFalse(result["success"], result)
            self.assertEqual(result["error"], "Beads graph application is not authorized")
            self.assertNotIn(str(self.manifest_path), encoded)
            self.assertNotIn(str(self.state_path), encoded)
            self.assertNotIn("state-file-secret", encoded)
            self.assertEqual(self.state_path.read_bytes(), before)
            apply.assert_not_called()

        self.write_initial_state()
        with self.subTest(case="absent-authority"):
            denied("integrator-session")

        self.write_bound_integrator_state("bound-session")
        with self.subTest(case="mismatched-session"):
            denied("different-session")

        self.state_path.write_text("{not-json state-file-secret", encoding="utf-8")
        with self.subTest(case="unreadable-state"):
            denied("bound-session")

        incompatible_manifest = copy.deepcopy(self.manifest)
        incompatible_manifest["mission"]["id"] = "INCOMPATIBLE-MISSION"
        incompatible_state = initial_state(incompatible_manifest)
        incompatible_actor = {
            "session_id": "bound-session",
            **incompatible_manifest["models"]["integrator"],
        }
        transition(
            incompatible_manifest,
            incompatible_state,
            "M1",
            "start_milestone",
            trusted_actor=incompatible_actor,
        )
        self.state_path.write_text(json.dumps(incompatible_state), encoding="utf-8")
        with self.subTest(case="incompatible-state"):
            denied("bound-session")

        self.write_bound_integrator_state("bound-session")
        forged = json.loads(self.state_path.read_text(encoding="utf-8"))
        forged["revision"] += 1
        forged["state_file_secret"] = "state-file-secret"
        self.state_path.write_text(json.dumps(forged), encoding="utf-8")
        with self.subTest(case="tampered-state"):
            denied("bound-session")

    def test_beads_apply_accepts_only_the_exact_bound_integrator(self) -> None:
        self.manifest["execution"]["graph_mode"] = "apply"
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        self.write_bound_integrator_state("bound-session")
        before = self.state_path.read_bytes()
        adapter_result = {
            "applied": True,
            "idempotent_replay": False,
            "receipt_path": str(self.root / "beads-graph-receipt.json"),
        }
        with mock.patch.dict(
            os.environ, self.beads_apply_environment(), clear=True
        ), mock.patch(
            "plugin._active_model_catalog", return_value=fixture_catalog(self.manifest)
        ), mock.patch(
            "plugin.apply_graph_plan", return_value=adapter_result
        ) as apply:
            result = json.loads(
                _handle_beads_apply(self.params, session_id="bound-session")
            )
        self.assertTrue(result["success"], result)
        self.assertTrue(result["applied"])
        self.assertEqual(self.state_path.read_bytes(), before)
        apply.assert_called_once_with(
            self.manifest,
            mock.ANY,
            bd_executable="bd",
            authorize_isolated=False,
        )

    def test_milestone_transitions_require_and_bind_the_trusted_integrator_actor(self) -> None:
        integrator = self.manifest["models"]["integrator"]
        environment = {
            "HERMES_FACTORY_ROLE": "integrator",
            "HERMES_FACTORY_PROVIDER": integrator["provider"],
            "HERMES_FACTORY_MODEL": integrator["model"],
            "HERMES_HOME": str(self.root),
        }
        with mock.patch.dict(os.environ, environment, clear=True), mock.patch(
            "plugin._active_model_catalog", return_value=fixture_catalog(self.manifest)
        ):
            self.write_initial_state()
            initialized = json.loads(_handle_validate(self.params))
        self.assertTrue(initialized["success"], initialized)
        before = self.state_path.read_bytes()
        self.assertEqual(json.loads(before)["revision"], 0)

        milestone_actions = (
            "start_milestone",
            "validate_milestone",
            "complete_milestone",
            "block",
            "replan",
        )
        denied_roles = ("builder", "verifier", "adversary", "holdout", "reviewer", "", "unknown")
        for role in denied_roles:
            for action in milestone_actions:
                denied_environment = dict(environment)
                if role:
                    denied_environment["HERMES_FACTORY_ROLE"] = role
                else:
                    denied_environment.pop("HERMES_FACTORY_ROLE")
                with self.subTest(role=role or "missing", action=action), mock.patch.dict(
                    os.environ, denied_environment, clear=True
                ):
                    result = json.loads(
                        _handle_transition(
                            {
                                **self.params,
                                "entity_id": "M1",
                                "action": action,
                                "expected_revision": 0,
                            },
                            session_id="integrator-session",
                        )
                    )
                self.assertFalse(result["success"], result)
                self.assertEqual(
                    result["error"],
                    "factory transition actor is not authorized",
                )
                exposed = json.dumps(result)
                self.assertNotIn(str(self.manifest_path), exposed)
                self.assertNotIn(integrator["model"], exposed)
                self.assertEqual(self.state_path.read_bytes(), before)

        invalid_actors = (
            ("missing-session", environment, None),
            ("blank-session", environment, ""),
            (
                "missing-provider",
                {key: value for key, value in environment.items() if key != "HERMES_FACTORY_PROVIDER"},
                "integrator-session",
            ),
            (
                "wrong-provider",
                {**environment, "HERMES_FACTORY_PROVIDER": "wrong-provider"},
                "integrator-session",
            ),
            (
                "noncanonical-provider",
                {**environment, "HERMES_FACTORY_PROVIDER": integrator["provider"].upper()},
                "integrator-session",
            ),
            (
                "missing-model",
                {key: value for key, value in environment.items() if key != "HERMES_FACTORY_MODEL"},
                "integrator-session",
            ),
            (
                "wrong-model",
                {**environment, "HERMES_FACTORY_MODEL": "wrong-model"},
                "integrator-session",
            ),
        )
        for name, actor_environment, session_id in invalid_actors:
            with self.subTest(actor=name), mock.patch.dict(
                os.environ, actor_environment, clear=True
            ), mock.patch("plugin._active_model_catalog", return_value=fixture_catalog(self.manifest)):
                result = json.loads(
                    _handle_transition(
                        {
                            **self.params,
                            "entity_id": "M1",
                            "action": "start_milestone",
                            "expected_revision": 0,
                        },
                        session_id=session_id,
                    )
                )
            self.assertFalse(result["success"], result)
            self.assertEqual(
                result["error"],
                "factory transition actor is not authorized",
            )
            self.assertEqual(self.state_path.read_bytes(), before)

        with mock.patch.dict(os.environ, environment, clear=True), mock.patch(
            "plugin._active_model_catalog", return_value=fixture_catalog(self.manifest)
        ):
            accepted = json.loads(
                _handle_transition(
                    {
                        **self.params,
                        "entity_id": "M1",
                        "action": "start_milestone",
                        "expected_revision": 0,
                    },
                    session_id="integrator-session",
                )
            )
        self.assertTrue(accepted["success"], accepted)
        self.assertEqual(accepted["revision"], 1)
        persisted = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["revision"], 1)
        self.assertEqual(persisted["milestones"]["M1"]["status"], "active")
        self.assertEqual(
            persisted["integrator_authority"],
            {
                "session_id": "integrator-session",
                "provider": integrator["provider"],
                "model": integrator["model"],
            },
        )

        active_state = self.state_path.read_bytes()
        block_evidence = {
            "reason": "operator pause",
            "owner": "integrator",
            "resume_condition": "operator resumes mission",
        }
        with mock.patch.dict(os.environ, environment, clear=True), mock.patch(
            "plugin._active_model_catalog", return_value=fixture_catalog(self.manifest)
        ):
            mismatch = json.loads(
                _handle_transition(
                    {
                        **self.params,
                        "entity_id": "M1",
                        "action": "block",
                        "evidence": block_evidence,
                        "expected_revision": 1,
                    },
                    session_id="different-session",
                )
            )
        self.assertFalse(mismatch["success"], mismatch)
        self.assertEqual(mismatch["error"], "factory transition actor is not authorized")
        self.assertEqual(self.state_path.read_bytes(), active_state)

        with mock.patch.dict(os.environ, environment, clear=True), mock.patch(
            "plugin._active_model_catalog", return_value=fixture_catalog(self.manifest)
        ):
            progressed = json.loads(
                _handle_transition(
                    {
                        **self.params,
                        "entity_id": "M1",
                        "action": "block",
                        "evidence": block_evidence,
                        "expected_revision": 1,
                    },
                    session_id="integrator-session",
                )
            )
        self.assertTrue(progressed["success"], progressed)
        self.assertEqual(progressed["revision"], 2)
        self.assertEqual(
            json.loads(self.state_path.read_text(encoding="utf-8"))["milestones"]["M1"]["status"],
            "blocked",
        )

    def test_slice_transition_handler_binds_builder_and_separates_governance(self) -> None:
        integrator = self.manifest["models"]["integrator"]
        builder = self.manifest["models"]["builder"]
        integrator_environment = {
            "HERMES_FACTORY_ROLE": "integrator",
            "HERMES_FACTORY_PROVIDER": integrator["provider"],
            "HERMES_FACTORY_MODEL": integrator["model"],
            "HERMES_HOME": str(self.root),
        }
        builder_environment = {
            "HERMES_FACTORY_ROLE": "builder",
            "HERMES_FACTORY_PROVIDER": builder["provider"],
            "HERMES_FACTORY_MODEL": builder["model"],
            "HERMES_HOME": str(self.root),
        }
        catalog = fixture_catalog(self.manifest)

        with mock.patch.dict(os.environ, integrator_environment, clear=True), mock.patch(
            "plugin._active_model_catalog", return_value=catalog
        ):
            self.write_initial_state()
            started_milestone = json.loads(
                _handle_transition(
                    {
                        **self.params,
                        "entity_id": "M1",
                        "action": "start_milestone",
                        "expected_revision": 0,
                    },
                    session_id="integrator-session",
                )
            )
        self.assertTrue(started_milestone["success"], started_milestone)
        revision_one = self.state_path.read_bytes()

        denied_starts = (
            ({}, None),
            (integrator_environment, "integrator-session"),
        )
        for environment, session_id in denied_starts:
            with self.subTest(environment=environment), mock.patch.dict(
                os.environ, environment, clear=True
            ), mock.patch("plugin._active_model_catalog", return_value=catalog):
                denied = json.loads(
                    _handle_transition(
                        {
                            **self.params,
                            "entity_id": "M1-S1",
                            "action": "start_slice",
                            "expected_revision": 1,
                        },
                        session_id=session_id,
                    )
                )
            self.assertFalse(denied["success"], denied)
            self.assertEqual(denied["error"], "factory transition actor is not authorized")
            self.assertEqual(self.state_path.read_bytes(), revision_one)

        with mock.patch.dict(os.environ, builder_environment, clear=True), mock.patch(
            "plugin._active_model_catalog", return_value=catalog
        ):
            started_slice = json.loads(
                _handle_transition(
                    {
                        **self.params,
                        "entity_id": "M1-S1",
                        "action": "start_slice",
                        "expected_revision": 1,
                    },
                    session_id="builder-session",
                )
            )
        self.assertTrue(started_slice["success"], started_slice)
        persisted = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(
            persisted["slices"]["M1-S1"]["builder_authority"],
            {
                "session_id": "builder-session",
                "provider": builder["provider"],
                "model": builder["model"],
            },
        )
        revision_two = self.state_path.read_bytes()

        denied_actions = (
            (builder_environment, "different-builder", "record_failure", {}),
            (integrator_environment, "integrator-session", "request_review", {}),
            (
                builder_environment,
                "builder-session",
                "request_changes",
                {"findings": ["must not persist"]},
            ),
        )
        for environment, session_id, action, evidence in denied_actions:
            with self.subTest(action=action, session_id=session_id), mock.patch.dict(
                os.environ, environment, clear=True
            ), mock.patch("plugin._active_model_catalog", return_value=catalog):
                denied = json.loads(
                    _handle_transition(
                        {
                            **self.params,
                            "entity_id": "M1-S1",
                            "action": action,
                            "evidence": evidence,
                            "expected_revision": 2,
                        },
                        session_id=session_id,
                    )
                )
            self.assertFalse(denied["success"], denied)
            self.assertEqual(denied["error"], "factory transition actor is not authorized")
            self.assertEqual(self.state_path.read_bytes(), revision_two)

        candidate_sha = subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
        ).strip()
        artifact = self.root / "builder-check.json"
        artifact.write_text('{"result":"PASS"}\n', encoding="utf-8")
        criterion_ids = [item["id"] for item in self.manifest["slices"][0]["acceptance"]]
        check = {
            "mission_id": self.manifest["mission"]["id"],
            "entity_id": "M1-S1",
            "candidate_sha": candidate_sha,
            "command": "python -m unittest tests.test_runtime_security",
            "exit_code": 0,
            "environment_fingerprint": "runtime-security-test",
            "observed_result": "PASS",
            "timestamp": "2026-08-31T10:00:00Z",
            "path": str(artifact),
            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "criterion_ids": criterion_ids,
        }
        with mock.patch.dict(os.environ, builder_environment, clear=True), mock.patch(
            "plugin._active_model_catalog", return_value=catalog
        ):
            requested = json.loads(
                _handle_transition(
                    {
                        **self.params,
                        "entity_id": "M1-S1",
                        "action": "request_review",
                        "evidence": {"candidate_sha": candidate_sha, "checks": [check]},
                        "expected_revision": 2,
                    },
                    session_id="builder-session",
                )
            )
        self.assertTrue(requested["success"], requested)

        with mock.patch.dict(os.environ, integrator_environment, clear=True), mock.patch(
            "plugin._active_model_catalog", return_value=catalog
        ):
            governed = json.loads(
                _handle_transition(
                    {
                        **self.params,
                        "entity_id": "M1-S1",
                        "action": "request_changes",
                        "evidence": {"findings": ["bounded remediation required"]},
                        "expected_revision": 3,
                    },
                    session_id="integrator-session",
                )
            )
        self.assertTrue(governed["success"], governed)
        final_state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(final_state["revision"], 4)
        self.assertEqual(final_state["slices"]["M1-S1"]["status"], "active")
        self.assertEqual(final_state["events"][-1]["actor"]["role"], "integrator")


class RuntimeGuardTests(unittest.TestCase):
    def test_every_reviewer_role_is_read_only_without_strict_mode_or_manifest(self) -> None:
        for role in ("reviewer", "verifier", "adversary", "holdout"):
            with self.subTest(role=role), mock.patch.dict(
                os.environ, {"HERMES_FACTORY_ROLE": role}, clear=True
            ):
                verdict = _pre_tool_guard(
                    tool_name="write_file",
                    args={"path": "src/product.py", "content": "changed"},
                )
            self.assertIsNotNone(verdict)
            self.assertEqual(verdict["action"], "block")
            self.assertIn("evidence-only", verdict["message"])

    def test_reviewer_secret_alias_is_blocked_without_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret_dir = root / "plugin-data" / "dark-factory"
            secret_dir.mkdir(parents=True)
            secret = secret_dir / "review-attestation.key"
            secret.write_text("secret", encoding="utf-8")
            alias = root / "secret-alias"
            alias.symlink_to(secret)
            with mock.patch.dict(
                os.environ,
                {"HERMES_FACTORY_ROLE": "verifier", "HERMES_HOME": str(root)},
                clear=True,
            ):
                verdict = _pre_tool_guard(tool_name="read_file", args={"path": str(alias)})
            self.assertEqual(verdict["action"], "block")
            self.assertIn("not readable", verdict["message"])

    def test_strict_guard_resolves_symlink_and_dotdot_aliases_but_allows_product_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            control = workspace / ".hermes" / "factory"
            control.mkdir(parents=True)
            manifest = control / "manifest.json"
            state = control / "state.json"
            manifest.write_text(json.dumps(TEMPLATE), encoding="utf-8")
            state.write_text("{}", encoding="utf-8")
            alias = Path(tmp) / "factory-alias"
            alias.symlink_to(control, target_is_directory=True)
            environment = {
                "HERMES_FACTORY_STRICT": "1",
                "HERMES_FACTORY_ROLE": "builder",
                "HERMES_FACTORY_MANIFEST": str(manifest),
                "HERMES_FACTORY_STATE": str(state),
            }
            protected_aliases = (
                alias / "state.json",
                workspace / ".hermes" / "factory" / ".." / "factory" / "manifest.json",
                alias / "beads-graph-receipt.json",
            )
            with mock.patch.dict(os.environ, environment, clear=True):
                for target in protected_aliases:
                    with self.subTest(target=target):
                        verdict = _pre_tool_guard(
                            tool_name="write_file",
                            args={"path": str(target), "content": "{}"},
                        )
                        self.assertEqual(verdict["action"], "block")
                allowed = _pre_tool_guard(
                    tool_name="write_file",
                    args={
                        "path": str(workspace / "src" / "product.py"),
                        "content": "document .hermes/factory without writing control state",
                    },
                )
            self.assertIsNone(allowed)

    def test_strict_guard_blocks_shell_and_code_alias_probes_but_allows_product_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            control = workspace / ".hermes" / "factory"
            control.mkdir(parents=True)
            manifest = control / "manifest.json"
            state = control / "state.json"
            manifest.write_text(json.dumps(TEMPLATE), encoding="utf-8")
            state.write_text("{}", encoding="utf-8")
            alias = Path(tmp) / "control-alias"
            alias.symlink_to(control, target_is_directory=True)
            environment = {
                "HERMES_FACTORY_STRICT": "1",
                "HERMES_FACTORY_ROLE": "builder",
                "HERMES_FACTORY_MANIFEST": str(manifest),
                "HERMES_FACTORY_STATE": str(state),
            }
            probes = (
                (
                    "terminal",
                    {
                        "command": 'd=.hermes; f=factory; printf changed > "$PWD/$d/$f/state.json"',
                        "workdir": str(workspace),
                    },
                ),
                (
                    "terminal",
                    {
                        "command": (
                            'd=.hermes; f=$d/factory; target=$PWD/$f/state.json; '
                            'printf changed > "$target"'
                        ),
                        "workdir": str(workspace),
                    },
                ),
                (
                    "terminal",
                    {"command": f"cd {workspace}/.hermes && printf corrupt > factory/state.json"},
                ),
                (
                    "execute_code",
                    {
                        "code": (
                            f'(Path("{workspace}/.hermes") / "factory" / "state.json")'
                            '.write_text("corrupt")'
                        )
                    },
                ),
                (
                    "terminal",
                    {"command": f"root='{workspace}/.hermes'; cd \"$root\"; printf corrupt > factory/state.json"},
                ),
                (
                    "execute_code",
                    {
                        "code": 'Path.cwd() / ".hermes" / "factory" / "state.json"',
                        "workdir": str(workspace),
                    },
                ),
                (
                    "execute_code",
                    {
                        "code": 'Path(".hermes").resolve() / "factory" / "state.json"',
                        "workdir": str(workspace),
                    },
                ),
                (
                    "execute_code",
                    {
                        "code": (
                            'from pathlib import Path\n'
                            '(Path.cwd() / ".hermes" / "factory" / "state.json").write_text("changed")'
                        ),
                        "workdir": str(workspace),
                    },
                ),
                (
                    "execute_code",
                    {
                        "code": (
                            'from pathlib import Path\n'
                            '(Path(".hermes").resolve() / "factory" / "state.json").write_text("changed")'
                        ),
                        "workdir": str(workspace),
                    },
                ),
                (
                    "execute_code",
                    {
                        "code": (
                            'from pathlib import Path\n'
                            'Path(".hermes").absolute().joinpath("factory", "state.json").write_text("changed")'
                        ),
                        "workdir": str(workspace),
                    },
                ),
                (
                    "execute_code",
                    {
                        "code": "from pathlib import Path as P; (P.cwd()/'.hermes'/'factory'/'state.json').unlink()",
                        "workdir": str(workspace),
                    },
                ),
                (
                    "execute_code",
                    {
                        "code": (
                            "import pathlib as pl; "
                            "pl.Path('.hermes').absolute().joinpath('factory', 'state.json').unlink()"
                        ),
                        "workdir": str(workspace),
                    },
                ),
                (
                    "execute_code",
                    {
                        "code": (
                            "from pathlib import Path as P; Q=P; "
                            "(Q('.hermes')/'factory'/'state.json').unlink()"
                        ),
                        "workdir": str(workspace),
                    },
                ),
                (
                    "execute_code",
                    {
                        "code": (
                            "import pathlib as pl; Q=R=pl.Path; "
                            "(R('.hermes')/'factory'/'state.json').unlink()"
                        ),
                        "workdir": str(workspace),
                    },
                ),
                (
                    "execute_code",
                    {
                        "code": (
                            "import pathlib; p=pathlib; q=p; "
                            "(q.Path('.hermes')/'factory'/'state.json').unlink()"
                        ),
                        "workdir": str(workspace),
                    },
                ),
                (
                    "execute_code",
                    {
                        "code": (
                            "import pathlib as pl; p=pl; q=p; "
                            "(q.Path('.hermes')/'factory'/'state.json').unlink()"
                        ),
                        "workdir": str(workspace),
                    },
                ),
                (
                    "execute_code",
                    {
                        "code": (
                            f'root = Path("{alias}")\n'
                            'target = root / "state.json"\n'
                            'target.write_text("corrupt")'
                        )
                    },
                ),
            )
            with mock.patch.dict(os.environ, environment, clear=True):
                for tool_name, args in probes:
                    with self.subTest(tool_name=tool_name, args=args):
                        verdict = _pre_tool_guard(tool_name=tool_name, args=args)
                        self.assertIsNotNone(verdict)
                        self.assertEqual(verdict["action"], "block")

                allowed_shell = _pre_tool_guard(
                    tool_name="terminal",
                    args={
                        "command": (
                            'd=src; f=state.json; target=$PWD/$d/$f; '
                            'python -m unittest tests.test_product'
                        ),
                        "workdir": str(workspace),
                    },
                )
                allowed_code = _pre_tool_guard(
                    tool_name="execute_code",
                    args={
                        "code": 'Path("src/state.json").write_text("normal product fixture")',
                        "workdir": str(workspace),
                    },
                )
                allowed_alias_code = _pre_tool_guard(
                    tool_name="execute_code",
                    args={
                        "code": (
                            "from pathlib import Path as P; "
                            "(P.cwd()/'src'/'state.json').write_text('normal product fixture')"
                        ),
                        "workdir": str(workspace),
                    },
                )
                allowed_module_alias_code = _pre_tool_guard(
                    tool_name="execute_code",
                    args={
                        "code": (
                            "import pathlib as pl; "
                            "pl.Path('tests').resolve().joinpath('state.json').unlink()"
                        ),
                        "workdir": str(workspace),
                    },
                )
                allowed_class_alias_code = _pre_tool_guard(
                    tool_name="execute_code",
                    args={
                        "code": (
                            "from pathlib import Path as P; Q=P; "
                            "(Q('src')/'state.json').write_text('normal product fixture')"
                        ),
                        "workdir": str(workspace),
                    },
                )
                allowed_chained_module_alias_code = _pre_tool_guard(
                    tool_name="execute_code",
                    args={
                        "code": (
                            "import pathlib as pl; p=pl; q=p; "
                            "(q.Path('src')/'state.json').write_text('normal product fixture')"
                        ),
                        "workdir": str(workspace),
                    },
                )
            self.assertIsNone(allowed_shell)
            self.assertIsNone(allowed_code)
            self.assertIsNone(allowed_alias_code)
            self.assertIsNone(allowed_module_alias_code)
            self.assertIsNone(allowed_class_alias_code)
            self.assertIsNone(allowed_chained_module_alias_code)

    def test_strict_guard_resolves_python_string_paths_for_open_and_os_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            control = workspace / ".hermes" / "factory"
            control.mkdir(parents=True)
            manifest = control / "manifest.json"
            state = control / "state.json"
            manifest.write_text(json.dumps(TEMPLATE), encoding="utf-8")
            state.write_text("{}", encoding="utf-8")
            environment = {
                "HERMES_FACTORY_STRICT": "1",
                "HERMES_FACTORY_ROLE": "builder",
                "HERMES_FACTORY_MANIFEST": str(manifest),
                "HERMES_FACTORY_STATE": str(state),
            }
            probes = (
                "base='.hermes'; target=base+'/factory/state.json'; open(target,'w').write('corrupt')",
                (
                    "import builtins\n"
                    "root = base = '.hermes'\n"
                    "leaf = 'state.json'\n"
                    "target = f'{root}/factory/{leaf}'\n"
                    "builtins.open(target, 'a')"
                ),
                (
                    "import os\n"
                    "first = second = '.hermes'\n"
                    "target = os.path.join(second, 'factory', 'state.json')\n"
                    "os.remove(target)"
                ),
                (
                    "import os as operating_system\n"
                    "base = '.hermes'\n"
                    "target = base + '/factory/' + 'state.json'\n"
                    "operating_system.unlink(target)"
                ),
                (
                    "from os import unlink as discard\n"
                    "base = '.hermes'\n"
                    "target = base + '/factory/state.json'\n"
                    "discard(target)"
                ),
                (
                    "from pathlib import Path as P\n"
                    "base = '.hermes'\n"
                    "target = base + '/factory/state.json'\n"
                    "P(target).open('x')"
                ),
                (
                    "import io\n"
                    "base = '.hermes'\n"
                    "target = base + '/factory/state.json'\n"
                    "io.open(target, 'r')"
                ),
            )
            with mock.patch.dict(os.environ, environment, clear=True):
                for source in probes:
                    with self.subTest(source=source):
                        verdict = _pre_tool_guard(
                            tool_name="execute_code",
                            args={"code": source, "workdir": str(workspace)},
                        )
                        self.assertIsNotNone(verdict)
                        self.assertEqual(verdict["action"], "block")
                        self.assertEqual(
                            verdict["message"],
                            "dark-factory state and manifest may be changed only through factory tools",
                        )
                        self.assertNotIn(str(control), verdict["message"])

                benign_sources = (
                    (
                        "base = 'src'\n"
                        "target = base + '/product.json'\n"
                        "open(target, 'w').write('product')"
                    ),
                    (
                        "import os\n"
                        "base = 'build'\n"
                        "target = os.path.join(base, 'factory', 'state.json')\n"
                        "os.unlink(target)"
                    ),
                    (
                        "base = '.hermes'\n"
                        "target = base + '/factory/state.json'\n"
                        "target = choose_runtime_path()\n"
                        "open(target, 'w')"
                    ),
                    (
                        "base = choose_runtime_path()\n"
                        "target = f'{base}/factory/state.json'\n"
                        "open(target, 'a')"
                    ),
                )
                for source in benign_sources:
                    with self.subTest(benign_source=source):
                        self.assertIsNone(
                            _pre_tool_guard(
                                tool_name="execute_code",
                                args={"code": source, "workdir": str(workspace)},
                            )
                        )
            self.assertEqual(state.read_text(encoding="utf-8"), "{}")

    def test_strict_delegation_requires_nonempty_complete_contract_and_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = root / "manifest.json"
            state_path = root / "state.json"
            manifest = copy.deepcopy(TEMPLATE)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            manifest_only_environment = {
                "HERMES_FACTORY_STRICT": "1",
                "HERMES_FACTORY_ROLE": "integrator",
                "HERMES_FACTORY_MANIFEST": str(manifest_path),
            }
            weak_goal = """Factory-Milestone:
Factory-Slice: M1-S1
Outcome: Acceptance: marker text only
Evidence: test
Forbidden: publish
"""
            complete_goal = """Factory-Milestone: M1
Factory-Slice: M1-S1
Outcome: The server persists a verified user's project and enforces ownership on every read
Boundaries: Edit only src/domain/projects/**, src/server/projects/**, and tests/projects/**.
Acceptance: M1-S1-A1 exact shell and code probes are blocked.
Evidence: Run the focused runtime security test module.
Forbidden: Do not edit engine, intake, UI, CLI, or docs.
Handoff: Return changed files, test command, and observed result to the integrator.
Stop: Escalate on scope conflict, repeated failure, or unavailable prerequisites.
"""
            manifest_only_s2_goal = """Factory-Milestone: M1
Factory-Slice: M1-S2
Outcome: The browser presents create and reopen states through one accessible project journey
Boundaries: Edit only src/ui/projects/** and tests/browser/projects/**.
Acceptance: M1-S2-A1 the bounded browser journey passes.
Evidence: Run the focused deterministic browser scenario.
Forbidden: Do not edit factory control state or publish artifacts.
Handoff: Return changed coordinates and observed checks to the integrator.
Stop: Escalate on scope conflict, repeated failure, or unavailable prerequisites.
"""
            with mock.patch.dict(os.environ, manifest_only_environment, clear=True):
                manifest_only = _pre_tool_guard(
                    tool_name="delegate_task",
                    args={
                        "tasks": [
                            {"title": "Implement M1-S2", "goal": manifest_only_s2_goal}
                        ]
                    },
                )
            self.assertEqual(manifest_only["action"], "block")
            self.assertEqual(
                manifest_only["message"],
                "dark-factory delegation requires exact compiled startable slice contracts",
            )
            self.assertNotIn(str(manifest_path), manifest_only["message"])
            self.assertNotIn(str(state_path), manifest_only["message"])

            state = initial_state(manifest)
            transition(
                manifest,
                state,
                "M1",
                "start_milestone",
                trusted_actor={
                    "session_id": "integrator-session",
                    **manifest["models"]["integrator"],
                },
            )
            state_path.write_text(json.dumps(state), encoding="utf-8")
            pinned_environment = {
                **manifest_only_environment,
                "HERMES_FACTORY_STATE": str(state_path),
            }
            with mock.patch.dict(os.environ, pinned_environment, clear=True):
                blocked = _pre_tool_guard(
                    tool_name="delegate_task",
                    args={"tasks": [{"title": "Weak worker", "goal": weak_goal}]},
                )
                allowed = _pre_tool_guard(
                    tool_name="delegate_task",
                    args={"tasks": [{"title": "Runtime hardening", "goal": complete_goal}]},
                )
            self.assertEqual(blocked["action"], "block")
            self.assertIn("exact compiled", blocked["message"])
            self.assertIsNone(allowed)

    def test_strict_delegation_requires_coordinates_and_disjoint_worker_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            manifest = Path(tmp) / "manifest.json"
            state_path = Path(tmp) / "state.json"
            runtime_manifest = copy.deepcopy(TEMPLATE)
            runtime_manifest["mission"]["workspace_path"] = str(workspace.resolve())
            by_id = {item["id"]: item for item in runtime_manifest["slices"]}
            by_id["M1-S2"]["depends_on"] = []

            def write_startable_state() -> None:
                manifest.write_text(json.dumps(runtime_manifest), encoding="utf-8")
                state = initial_state(runtime_manifest)
                transition(
                    runtime_manifest,
                    state,
                    "M1",
                    "start_milestone",
                    trusted_actor={
                        "session_id": "integrator-session",
                        **runtime_manifest["models"]["integrator"],
                    },
                )
                state_path.write_text(json.dumps(state), encoding="utf-8")

            write_startable_state()
            environment = {
                "HERMES_FACTORY_STRICT": "1",
                "HERMES_FACTORY_ROLE": "integrator",
                "HERMES_FACTORY_MANIFEST": str(manifest),
                "HERMES_FACTORY_STATE": str(state_path),
            }

            def task(
                slice_id: str,
                *,
                milestone_id: str | None = None,
                outcome: str | None = None,
                paths: list[str] | None = None,
            ) -> dict[str, str]:
                spec = by_id.get(slice_id, by_id["M1-S1"])
                declared_paths = paths if paths is not None else list(spec["paths"])
                return {
                    "title": f"Implement {slice_id}",
                    "goal": f"""Factory-Milestone: {milestone_id or 'M1'}
Factory-Slice: {slice_id}
Outcome: {outcome if outcome is not None else spec['outcome']}
Boundaries: Edit only {', '.join(declared_paths)}.
Acceptance: {slice_id}-A1 the bounded product behavior passes its scenario.
Evidence: Run the focused deterministic scenario for this slice.
Forbidden: Do not edit factory control state or publish artifacts.
Handoff: Return changed coordinates and observed checks to the integrator.
Stop: Escalate on scope conflict, repeated failure, or unavailable prerequisites.
""",
                }

            coordinate_free = task("M1-S1", paths=[])
            unknown = task("M404")
            rogue = task("M1-S1", paths=[*by_id["M1-S1"]["paths"], "rogue/**"])
            wrong_paths = task("M1-S1", paths=[by_id["M1-S1"]["paths"][0]])
            wrong_outcome = task("M1-S1", outcome="A different durable outcome is delivered.")
            wrong_milestone = task("M1-S1", milestone_id="M404")
            exact_one = task("M1-S1")
            exact_two = [task("M1-S1"), task("M1-S2")]
            duplicate = [task("M1-S1"), task("M1-S1")]
            with mock.patch.dict(os.environ, environment, clear=True):
                missing = _pre_tool_guard(
                    tool_name="delegate_task", args={"tasks": [coordinate_free]}
                )
                rejected = [
                    _pre_tool_guard(tool_name="delegate_task", args={"tasks": [probe]})
                    for probe in (unknown, rogue, wrong_paths, wrong_outcome, wrong_milestone)
                ]
                duplicate_result = _pre_tool_guard(
                    tool_name="delegate_task", args={"tasks": duplicate}
                )
                allowed_one = _pre_tool_guard(
                    tool_name="delegate_task", args={"tasks": [exact_one]}
                )
                allowed_two = _pre_tool_guard(
                    tool_name="delegate_task", args={"tasks": exact_two}
                )

            self.assertEqual(missing["action"], "block")
            self.assertIn("exact compiled", missing["message"])
            for verdict in (*rejected, duplicate_result):
                self.assertEqual(verdict["action"], "block")
                self.assertIn("exact compiled", verdict["message"])
                self.assertNotIn("src/", verdict["message"])
                self.assertNotIn("M404", verdict["message"])
                self.assertNotIn("rogue", verdict["message"])
            self.assertIsNone(allowed_one)
            self.assertIsNone(allowed_two)

            original_paths = list(by_id["M1-S2"]["paths"])
            by_id["M1-S2"]["paths"] = list(by_id["M1-S1"]["paths"])
            by_id["M1-S2"]["depends_on"] = ["M1-S1"]
            write_startable_state()
            with mock.patch.dict(os.environ, environment, clear=True):
                overlap = _pre_tool_guard(
                    tool_name="delegate_task",
                    args={"tasks": [task("M1-S1"), task("M1-S2")]},
                )
            self.assertEqual(overlap["action"], "block")
            self.assertIn("exact compiled", overlap["message"])

            by_id["M1-S2"]["paths"] = original_paths
            by_id["M1-S2"]["depends_on"] = []
            runtime_manifest["policy"]["max_parallel_slices"] = 1
            write_startable_state()
            with mock.patch.dict(os.environ, environment, clear=True):
                capped = _pre_tool_guard(
                    tool_name="delegate_task",
                    args={"tasks": [task("M1-S1"), task("M1-S2")]},
                )
            self.assertEqual(capped["action"], "block")
            self.assertIn("at most 1", capped["message"])

    def test_strict_delegation_requires_startable_slice_when_state_is_pinned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = root / "manifest.json"
            state_path = root / "state.json"
            manifest = copy.deepcopy(TEMPLATE)
            manifest["mission"]["workspace_path"] = str((root / "workspace").resolve())
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            state = initial_state(manifest)
            integrator = {
                "session_id": "integrator-session",
                **manifest["models"]["integrator"],
            }
            builder = {
                "session_id": "builder-session",
                **manifest["models"]["builder"],
            }
            transition(
                manifest,
                state,
                "M1",
                "start_milestone",
                trusted_actor=integrator,
            )
            state_path.write_text(json.dumps(state), encoding="utf-8")

            def task(slice_id: str) -> dict[str, str]:
                spec = next(item for item in manifest["slices"] if item["id"] == slice_id)
                return {
                    "title": f"Implement {slice_id}",
                    "goal": f"""Factory-Milestone: {spec['milestone_id']}
Factory-Slice: {slice_id}
Outcome: {spec['outcome']}
Boundaries: Edit only {', '.join(spec['paths'])}.
Acceptance: The compiled acceptance criteria pass against the bounded candidate.
Evidence: Run the focused deterministic scenario and preserve raw receipts.
Forbidden: Do not edit factory control state or publish artifacts.
Handoff: Return changed coordinates and observed checks to the integrator.
Stop: Escalate on scope conflict, repeated failure, or unavailable prerequisites.
""",
                }

            environment = {
                "HERMES_FACTORY_STRICT": "1",
                "HERMES_FACTORY_ROLE": "integrator",
                "HERMES_FACTORY_MANIFEST": str(manifest_path),
                "HERMES_FACTORY_STATE": str(state_path),
            }
            with mock.patch.dict(os.environ, environment, clear=True):
                startable = _pre_tool_guard(
                    tool_name="delegate_task", args={"tasks": [task("M1-S1")]}
                )
                blocked_dependency = _pre_tool_guard(
                    tool_name="delegate_task", args={"tasks": [task("M1-S2")]}
                )
            self.assertIsNone(startable)
            self.assertEqual(blocked_dependency["action"], "block")

            transition(
                manifest,
                state,
                "M1-S1",
                "start_slice",
                trusted_actor=builder,
            )
            state_path.write_text(json.dumps(state), encoding="utf-8")
            with mock.patch.dict(os.environ, environment, clear=True):
                already_active = _pre_tool_guard(
                    tool_name="delegate_task", args={"tasks": [task("M1-S1")]}
                )
            self.assertEqual(already_active["action"], "block")
            self.assertNotIn("M1-S1", already_active["message"])

            missing_environment = {**environment, "HERMES_FACTORY_STATE": str(root / "missing.json")}
            with mock.patch.dict(os.environ, missing_environment, clear=True):
                missing_state = _pre_tool_guard(
                    tool_name="delegate_task", args={"tasks": [task("M1-S1")]}
                )
            self.assertEqual(missing_state["action"], "block")

    def test_strict_delegation_rejects_every_missing_or_invalid_pinned_state_generically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = root / "manifest.json"
            state_path = root / "state.json"
            manifest = copy.deepcopy(TEMPLATE)
            manifest["mission"]["workspace_path"] = str((root / "workspace").resolve())
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            spec = next(item for item in manifest["slices"] if item["id"] == "M1-S1")
            task = {
                "title": "Implement M1-S1",
                "goal": f"""Factory-Milestone: M1
Factory-Slice: M1-S1
Outcome: {spec['outcome']}
Boundaries: Edit only {', '.join(spec['paths'])}.
Acceptance: The compiled acceptance criteria pass against the bounded candidate.
Evidence: Run the focused deterministic scenario and preserve raw receipts.
Forbidden: Do not edit factory control state or publish artifacts.
Handoff: Return changed coordinates and observed checks to the integrator.
Stop: Escalate on scope conflict, repeated failure, or unavailable prerequisites.
""",
            }
            base_environment = {
                "HERMES_FACTORY_STRICT": "1",
                "HERMES_FACTORY_ROLE": "integrator",
                "HERMES_FACTORY_MANIFEST": str(manifest_path),
            }
            pinned_environment = {
                **base_environment,
                "HERMES_FACTORY_STATE": str(state_path),
            }

            verdicts = []
            for environment in (
                base_environment,
                {**base_environment, "HERMES_FACTORY_STATE": "   \t"},
                pinned_environment,
            ):
                with mock.patch.dict(os.environ, environment, clear=True):
                    verdicts.append(
                        _pre_tool_guard(
                            tool_name="delegate_task", args={"tasks": [task]}
                        )
                    )

            state_path.write_text("{not-json", encoding="utf-8")
            with mock.patch.dict(os.environ, pinned_environment, clear=True):
                verdicts.append(
                    _pre_tool_guard(tool_name="delegate_task", args={"tasks": [task]})
                )

            state_path.write_text("{}", encoding="utf-8")
            with mock.patch.dict(os.environ, pinned_environment, clear=True):
                verdicts.append(
                    _pre_tool_guard(tool_name="delegate_task", args={"tasks": [task]})
                )

            incompatible_manifest = copy.deepcopy(manifest)
            incompatible_manifest["mission"]["id"] = "other-compiled-mission"
            state_path.write_text(
                json.dumps(initial_state(incompatible_manifest)), encoding="utf-8"
            )
            with mock.patch.dict(os.environ, pinned_environment, clear=True):
                verdicts.append(
                    _pre_tool_guard(tool_name="delegate_task", args={"tasks": [task]})
                )

            state_path.write_text(json.dumps(initial_state(manifest)), encoding="utf-8")
            with mock.patch.dict(os.environ, pinned_environment, clear=True):
                verdicts.append(
                    _pre_tool_guard(tool_name="delegate_task", args={"tasks": [task]})
                )

            with mock.patch.dict(os.environ, pinned_environment, clear=True), mock.patch(
                "plugin._read_json", side_effect=PermissionError("private state path")
            ) as read_state:
                verdicts.append(
                    _pre_tool_guard(tool_name="delegate_task", args={"tasks": [task]})
                )
            read_state.assert_called_once()

            with mock.patch.dict(os.environ, pinned_environment, clear=True), mock.patch(
                "plugin.load_state", side_effect=RuntimeError("loader internals")
            ):
                verdicts.append(
                    _pre_tool_guard(tool_name="delegate_task", args={"tasks": [task]})
                )

            for verdict in verdicts:
                self.assertEqual(
                    verdict,
                    {
                        "action": "block",
                        "message": (
                            "dark-factory delegation requires exact compiled "
                            "startable slice contracts"
                        ),
                    },
                )
                exposed = json.dumps(verdict)
                self.assertNotIn(str(manifest_path), exposed)
                self.assertNotIn(str(state_path), exposed)
                self.assertNotIn("attestation", exposed.lower())
                self.assertNotIn("loader internals", exposed)


class DashboardInventoryErrorTests(unittest.TestCase):
    def test_inventory_errors_are_generic_in_http_details_and_logs(self) -> None:
        from fastapi import HTTPException
        from plugin.dashboard import plugin_api

        credential_error = RuntimeError(
            "inventory loader exploded at /home/master/.hermes/config.yaml: "
            "Authorization: Bearer sk-dashboard-do-not-leak password=dashboard-secret"
        )
        calls = (
            (lambda: plugin_api.model_options(refresh=False), "_active_profile_name"),
            (lambda: plugin_api.get_setup(), "load_setup"),
            (lambda: plugin_api.put_setup({"setup": {}}), "normalise_setup"),
            (lambda: plugin_api.preflight({"setup": {}}), "normalise_setup"),
            (lambda: plugin_api.compile_factory({"setup": {}}), "normalise_setup"),
        )
        for call, forbidden_operation in calls:
            with self.subTest(endpoint=call), mock.patch.object(
                plugin_api, "_model_options", side_effect=credential_error
            ), mock.patch.object(
                plugin_api,
                forbidden_operation,
                side_effect=AssertionError("setup processing must not run"),
            ) as setup_processing, self.assertLogs(
                plugin_api.log, level="ERROR"
            ) as captured:
                with self.assertRaises(HTTPException) as raised:
                    call()
            setup_processing.assert_not_called()
            exposed = json.dumps(raised.exception.detail) + " " + " ".join(captured.output)
            self.assertEqual(raised.exception.status_code, 503)
            self.assertIn("model_inventory_unavailable", exposed)
            self.assertNotIn("sk-dashboard-do-not-leak", exposed)
            self.assertNotIn("dashboard-secret", exposed)
            self.assertNotIn("/home/master/.hermes/config.yaml", exposed)
            self.assertNotIn("inventory loader exploded", exposed)


class BeadsToolSurfaceTests(unittest.TestCase):
    def test_runtime_pins_bd_and_schema_has_no_executable_override(self) -> None:
        manifest = copy.deepcopy(TEMPLATE)
        directory, executable, authorized = _beads_settings(
            manifest,
            {"bd_executable": "/tmp/attacker-controlled-bd"},
        )
        self.assertEqual(executable, "bd")
        self.assertFalse(authorized)
        self.assertTrue(directory.endswith(".beads"))

        class Context:
            def __init__(self) -> None:
                self.tools: dict[str, dict] = {}

            def register_tool(self, **kwargs: object) -> None:
                self.tools[str(kwargs["name"])] = kwargs["schema"]  # type: ignore[assignment]

            def register_hook(self, *_args: object) -> None:
                pass

            def register_skill(self, *_args: object) -> None:
                pass

        ctx = Context()
        register(ctx)
        properties = ctx.tools["factory_beads_apply"]["parameters"]["properties"]
        self.assertNotIn("bd_executable", properties)


if __name__ == "__main__":
    unittest.main()
