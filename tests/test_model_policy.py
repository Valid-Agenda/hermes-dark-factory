from __future__ import annotations

import copy
import unittest

from plugin.model_policy import (
    DEFAULT_PRESET_ID,
    apply_model_policy_defaults,
    authenticated_model_refs,
    preset_catalog,
)


def inventory(*models: str) -> dict:
    return {
        "profile": "default",
        "current": {"provider": "other", "model": "current-model"},
        "providers": [
            {"slug": "openai-codex", "authenticated": True, "models": list(models)},
            {"slug": "other", "authenticated": True, "models": ["current-model"]},
        ],
    }


class ModelPolicyTests(unittest.TestCase):
    def test_preset_advertises_explicit_orchestrator_worker_contract(self) -> None:
        catalog = preset_catalog(inventory("gpt-5.6-sol-900k", "gpt-5.6-luna"))
        preset = catalog["presets"][0]
        self.assertEqual(preset["id"], DEFAULT_PRESET_ID)
        self.assertEqual(preset["roles"]["integrator"]["execution_role"], "orchestrator")
        self.assertEqual(preset["roles"]["builder"]["execution_role"], "worker")
        self.assertEqual(
            preset["roles"]["integrator"]["preferred"],
            {"provider": "openai-codex", "model": "gpt-5.6-sol-900k"},
        )
        self.assertTrue(preset["roles"]["integrator"]["preferred_available"])
        self.assertEqual(preset["independent_from_builder"], ["verifier", "adversary", "holdout"])
        self.assertFalse(preset["automatic_reviewer_fallback"])

    def test_defaults_fill_only_empty_integrator_and_builder_from_authenticated_inventory(self) -> None:
        setup = {
            "model_policy": {"preset": DEFAULT_PRESET_ID},
            "models": {
                role: {"provider": "", "model": ""}
                for role in ("integrator", "builder", "verifier", "adversary", "holdout")
            },
        }
        resolved = apply_model_policy_defaults(
            setup, inventory("gpt-5.6-sol-900k", "gpt-5.6-luna")
        )
        self.assertEqual(
            resolved["models"]["integrator"],
            {"provider": "openai-codex", "model": "gpt-5.6-sol-900k"},
        )
        self.assertEqual(
            resolved["models"]["builder"],
            {"provider": "openai-codex", "model": "gpt-5.6-luna"},
        )
        for role in ("verifier", "adversary", "holdout"):
            self.assertEqual(resolved["models"][role], {"provider": "", "model": ""})

    def test_inventory_requires_exact_boolean_authenticated_true(self) -> None:
        auth_cases = (
            ("missing", object()),
            ("null", None),
            ("false", False),
            ("string", "true"),
            ("integer", 1),
            ("true", True),
        )
        for name, authenticated in auth_cases:
            with self.subTest(name=name):
                row = {
                    "slug": "openai-codex",
                    "models": ["gpt-5.6-sol-900k", "gpt-5.6-luna"],
                }
                if name != "missing":
                    row["authenticated"] = authenticated
                setup = {
                    "model_policy": {"preset": DEFAULT_PRESET_ID},
                    "models": {
                        role: {"provider": "", "model": ""}
                        for role in ("integrator", "builder", "verifier", "adversary", "holdout")
                    },
                }
                resolved = apply_model_policy_defaults(setup, {"providers": [row]})
                expected = name == "true"
                self.assertEqual(bool(resolved["models"]["integrator"]["model"]), expected)
                self.assertEqual(bool(resolved["models"]["builder"]["model"]), expected)

    def test_inventory_skips_malformed_refs_without_alias_fallback(self) -> None:
        catalog = {
            "providers": [
                {"slug": 123, "authenticated": True, "models": ["gpt-5.6-sol-900k"]},
                {
                    "slug": "openai-codex",
                    "authenticated": True,
                    "models": [
                        456,
                        {"id": 789, "model": "gpt-5.6-sol-900k"},
                        {"name": "gpt-5.6-luna"},
                        {"id": "object-model"},
                        {"model": "gpt-5.6-sol-900k"},
                    ],
                },
            ]
        }
        self.assertEqual(authenticated_model_refs(catalog), set())
        policy = preset_catalog(catalog)["presets"][0]["roles"]
        self.assertFalse(policy["integrator"]["preferred_available"])
        self.assertFalse(policy["builder"]["preferred_available"])

    def test_explicit_user_selections_are_never_overwritten(self) -> None:
        setup = {
            "model_policy": {"preset": DEFAULT_PRESET_ID},
            "models": {
                "integrator": {"provider": "custom", "model": "integrator-x"},
                "builder": {"provider": "custom", "model": "builder-x"},
                "verifier": {"provider": "custom", "model": "verify-x"},
                "adversary": {"provider": "custom", "model": "attack-x"},
                "holdout": {"provider": "custom", "model": "hold-x"},
            },
        }
        original = copy.deepcopy(setup)
        resolved = apply_model_policy_defaults(
            setup, inventory("gpt-5.6-sol-900k", "gpt-5.6-luna")
        )
        self.assertEqual(resolved["models"], original["models"])
        self.assertEqual(setup, original)

    def test_missing_preference_stays_empty_without_current_model_fallback(self) -> None:
        setup = {
            "model_policy": {"preset": DEFAULT_PRESET_ID},
            "models": {
                role: {"provider": "", "model": ""}
                for role in ("integrator", "builder", "verifier", "adversary", "holdout")
            },
        }
        resolved = apply_model_policy_defaults(setup, inventory())
        self.assertEqual(resolved["models"]["integrator"], {"provider": "", "model": ""})
        self.assertEqual(resolved["models"]["builder"], {"provider": "", "model": ""})

    def test_partial_explicit_reference_is_preserved_and_not_repaired(self) -> None:
        setup = {
            "model_policy": {"preset": DEFAULT_PRESET_ID},
            "models": {
                role: {"provider": "", "model": ""}
                for role in ("integrator", "builder", "verifier", "adversary", "holdout")
            },
        }
        setup["models"]["integrator"] = {"provider": "custom", "model": ""}
        resolved = apply_model_policy_defaults(
            setup, inventory("gpt-5.6-sol-900k", "gpt-5.6-luna")
        )
        self.assertEqual(resolved["models"]["integrator"], {"provider": "custom", "model": ""})


if __name__ == "__main__":
    unittest.main()
