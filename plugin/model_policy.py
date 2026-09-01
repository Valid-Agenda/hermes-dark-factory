"""Executable, credential-free model policy for Dark Factory roles."""

from __future__ import annotations

import copy
from typing import Any, Iterable

DEFAULT_PRESET_ID = "sol-luna"
ORCHESTRATOR_ROLE = "integrator"
WORKER_ROLE = "builder"
INDEPENDENT_ROLES = ("verifier", "adversary", "holdout")

_PREFERENCES = {
    ORCHESTRATOR_ROLE: {
        "provider": "openai-codex",
        "model": "gpt-5.6-sol-900k",
        "execution_role": "orchestrator",
    },
    WORKER_ROLE: {
        "provider": "openai-codex",
        "model": "gpt-5.6-luna",
        "execution_role": "worker",
    },
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _inventory_text(value: Any) -> str:
    """Return a canonical inventory identifier without coercing malformed data."""

    return value.strip() if isinstance(value, str) else ""


def authenticated_model_refs(model_catalog: Any) -> set[tuple[str, str]]:
    """Return only canonical refs from explicitly authenticated provider rows."""

    rows: Iterable[Any]
    if isinstance(model_catalog, dict):
        provider_rows = model_catalog.get("providers")
        rows = provider_rows if isinstance(provider_rows, list) else []
    else:
        rows = model_catalog if isinstance(model_catalog, list) else []
    pairs: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("authenticated") is not True:
            continue
        provider = _inventory_text(row.get("slug")).lower()
        for value in row.get("models", []) if isinstance(row.get("models"), list) else []:
            model = _inventory_text(value)
            if provider and model:
                pairs.add((provider, model))
    return pairs


def preset_catalog(model_catalog: Any = None) -> dict[str, Any]:
    """Describe the preset against the authenticated active-profile inventory."""

    pairs = authenticated_model_refs(model_catalog)
    roles: dict[str, Any] = {}
    for role, preference in _PREFERENCES.items():
        preferred = {
            "provider": preference["provider"],
            "model": preference["model"],
        }
        roles[role] = {
            "execution_role": preference["execution_role"],
            "preferred": preferred,
            "preferred_available": (preferred["provider"], preferred["model"]) in pairs,
            "selection_behavior": "fill_only_when_empty_and_available",
        }
    for role in INDEPENDENT_ROLES:
        roles[role] = {
            "execution_role": role,
            "preferred": None,
            "preferred_available": False,
            "selection_behavior": "explicit_required_no_fallback",
        }
    return {
        "default_preset": DEFAULT_PRESET_ID,
        "presets": [
            {
                "id": DEFAULT_PRESET_ID,
                "label": "Integrator orchestrator + builder worker",
                "description": (
                    "Integrator orchestrates milestones; builder executes functional slices. "
                    "Verifier, adversary, and holdout remain independently selected."
                ),
                "roles": roles,
                "independent_from_builder": list(INDEPENDENT_ROLES),
                "automatic_reviewer_fallback": False,
                "credentials_included": False,
            }
        ],
    }


def apply_model_policy_defaults(setup: Any, model_catalog: Any = None) -> dict[str, Any]:
    """Fill only blank integrator/builder refs when preferred models are available.

    Any provider or model text is treated as an explicit selection and is left
    untouched, including incomplete or unavailable references. Review roles are
    never inferred.
    """

    resolved = copy.deepcopy(setup) if isinstance(setup, dict) else {}
    policy = resolved.get("model_policy") if isinstance(resolved.get("model_policy"), dict) else {}
    if _text(policy.get("preset")) != DEFAULT_PRESET_ID:
        return resolved
    models = resolved.get("models") if isinstance(resolved.get("models"), dict) else {}
    resolved["models"] = models
    pairs = authenticated_model_refs(model_catalog)
    for role, preference in _PREFERENCES.items():
        current = models.get(role) if isinstance(models.get(role), dict) else {}
        provider = _text(current.get("provider"))
        model = _text(current.get("model"))
        if provider or model:
            continue
        preferred_pair = (preference["provider"], preference["model"])
        if preferred_pair in pairs:
            models[role] = {"provider": preferred_pair[0], "model": preferred_pair[1]}
    return resolved


def manifest_model_policy(preset_id: str = DEFAULT_PRESET_ID) -> dict[str, Any]:
    """Return the additive schema-v2 execution semantics for a compiled manifest."""

    return {
        "preset": preset_id,
        "roles": {
            ORCHESTRATOR_ROLE: "orchestrator",
            WORKER_ROLE: "worker",
            **{role: role for role in INDEPENDENT_ROLES},
        },
        "independent_from_builder": list(INDEPENDENT_ROLES),
        "automatic_fallback": False,
    }
