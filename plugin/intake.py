"""Guided mission intake and readiness gates for Dark Factory.

The intake is deliberately more demanding than a chat prompt. It separates
user-authored product intent from generated execution detail and refuses to
compile an executable factory manifest until the product, evidence, risk and
model contracts are complete.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

try:
    from .engine import (
        ACCEPTANCE_TYPES,
        FactoryError,
        MICRO_TITLE,
        SCHEMA_VERSION,
        THREAT_CONTRACT_FIELDS,
        _credential_shaped_paths,
        _manifest_digest,
        _state_file_lock,
        _validate_state_compatibility,
        decision_text_is_substantive,
        derive_independent_mission_risk,
        initial_state,
        threat_contract_is_substantive,
        threat_semantic_identity,
        validate_manifest,
    )
    from .model_policy import (
        DEFAULT_PRESET_ID,
        apply_model_policy_defaults,
        authenticated_model_refs,
        manifest_model_policy,
    )
except ImportError:  # Dashboard backend imports this module from the plugin root.
    from engine import (  # type: ignore
        ACCEPTANCE_TYPES,
        FactoryError,
        MICRO_TITLE,
        SCHEMA_VERSION,
        THREAT_CONTRACT_FIELDS,
        _credential_shaped_paths,
        _manifest_digest,
        _state_file_lock,
        _validate_state_compatibility,
        decision_text_is_substantive,
        derive_independent_mission_risk,
        initial_state,
        threat_contract_is_substantive,
        threat_semantic_identity,
        validate_manifest,
    )
    from model_policy import (  # type: ignore
        DEFAULT_PRESET_ID,
        apply_model_policy_defaults,
        authenticated_model_refs,
        manifest_model_policy,
    )

INTAKE_SCHEMA_VERSION = 1
MODEL_ROLES = ("integrator", "builder", "verifier", "adversary", "holdout")
NON_BUILDER_ROLES = ("verifier", "adversary", "holdout")
MODEL_REFERENCE_FIELDS = frozenset({"provider", "model"})
MODEL_POLICY_FIELDS = frozenset({"preset"})
SYSTEM_PROMPT_FIELDS = frozenset(MODEL_ROLES)
EXECUTION_FIELDS = frozenset({
    "graph_backend",
    "graph_mode",
    "beads_directory",
    "beads_isolated_authorized",
    "reasoning_effort",
})
REASONING_FIELDS = frozenset({"orchestrator", "worker"})
POLICY_FIELDS = frozenset({
    "max_active_milestones",
    "max_parallel_slices",
    "repeated_failure_limit",
    "max_remediation_cycles",
})
SENSITIVE_KEY_MARKERS = (
    "apikey",
    "token",
    "password",
    "secret",
    "credential",
    "connectionstring",
    "privatekey",
)
HIGH_RISK_TRIGGERS = {
    "authentication",
    "authorization",
    "tenant isolation",
    "personal data",
    "sensitive data",
    "payments",
    "billing",
    "public tokens",
    "migrations",
    "secrets",
    "production deployment",
    "external communications",
    "publishing",
    "safeguarding",
}
DATA_CLASSES = {"none", "internal", "personal", "sensitive", "regulated"}


def default_setup() -> dict[str, Any]:
    return {
        "intake_schema_version": INTAKE_SCHEMA_VERSION,
        "project_mode": "existing",
        "workspace_path": "",
        "product": {
            "name": "",
            "problem": "",
            "outcome": "",
            "context": "",
            "existing_system": "",
            "success_metrics": [],
            "surfaces": [],
        },
        "personas": [],
        "user_stories": [],
        "non_goals": [],
        "constraints": [],
        "milestones": [],
        "testing": {
            "focused_commands": [],
            "integration_commands": [],
            "browser_scenarios": [],
            "held_out_scenarios": [],
            "evidence_requirements": [],
        },
        "security": {
            "data_classification": "none",
            "adversarial_lens": "kryptonite",
            "risk_triggers": [],
            "threat_scenarios": [],
            "authority_decisions": [],
        },
        "models": {role: {"provider": "", "model": ""} for role in MODEL_ROLES},
        "model_policy": {"preset": DEFAULT_PRESET_ID},
        "system_prompts": {role: "" for role in MODEL_ROLES},
        "execution": {
            "graph_backend": "beads",
            "graph_mode": "plan",
            "beads_directory": "",
            "beads_isolated_authorized": False,
            "reasoning_effort": {"orchestrator": "high", "worker": "medium"},
        },
        "policy": {
            "max_active_milestones": 1,
            "max_parallel_slices": 2,
            "repeated_failure_limit": 2,
            "max_remediation_cycles": 1,
        },
    }


def _deep_merge(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _without_sensitive_keys(value: Any) -> Any:
    """Drop credential-shaped keys at every depth; values are never inspected or logged."""
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            canonical = re.sub(r"[^a-z0-9]", "", key_text.lower())
            if any(marker in canonical for marker in SENSITIVE_KEY_MARKERS):
                continue
            clean[key_text] = _without_sensitive_keys(item)
        return clean
    if isinstance(value, list):
        return [_without_sensitive_keys(item) for item in value]
    return copy.deepcopy(value)


def _allowed_rows(value: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            rows.append({key: copy.deepcopy(item.get(key)) for key in keys})
    return rows


def _allowed_list(value: Any) -> list[Any]:
    return copy.deepcopy(value) if isinstance(value, list) else []


def _normalised_acceptance_rows(value: Any) -> list[dict[str, str]]:
    return [
        {
            "id": str(item.get("id") or "").strip(),
            "type": str(item.get("type") or "").strip().lower(),
            "statement": str(item.get("statement") or "").strip(),
        }
        for item in value if isinstance(item, dict)
    ] if isinstance(value, list) else []


def _redact_sensitive_values(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redact_sensitive_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_sensitive_values(item) for item in value]
    if not isinstance(value, str):
        return value
    redacted = re.sub(r"(?i)\bAuthorization\s*:\s*Basic\s+[A-Za-z0-9+/=]{8,}", "Authorization: Basic [REDACTED]", value)
    redacted = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", "Bearer [REDACTED]", redacted)
    redacted = re.sub(r"(?i)(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{12,}", "[REDACTED]", redacted)
    redacted = re.sub(r"(?i)\bgh[pousr]_[A-Za-z0-9]{20,}\b", "[REDACTED]", redacted)
    redacted = re.sub(r"\bAKIA[0-9A-Z]{16}\b", "[REDACTED]", redacted)
    redacted = re.sub(r"\bAIza[0-9A-Za-z_-]{20,}\b", "[REDACTED]", redacted)
    redacted = re.sub(r"\bxox[baprs]-[A-Za-z0-9-]{16,}\b", "[REDACTED]", redacted)
    redacted = re.sub(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b", "[REDACTED]", redacted)
    redacted = re.sub(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/:]+:[^\s/@]+@", "[REDACTED]@", redacted)
    redacted = re.sub(r"-----BEGIN(?: [A-Z]+)* PRIVATE KEY-----.*?-----END(?: [A-Z]+)* PRIVATE KEY-----", "[REDACTED]", redacted, flags=re.DOTALL)
    redacted = re.sub(
        r"(?i)\b(api[_ -]?key|token|oauth[_ -]?token|access[_ -]?token|password|secret|credential|connection[_ -]?string)\s*[:=]\s*\S+",
        lambda match: f"{match.group(1)}=[REDACTED]",
        redacted,
    )
    return redacted


def _normalise_workspace_path(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return str(Path(raw).expanduser().resolve())
    except (OSError, RuntimeError, ValueError):
        return raw


def _normalise_beads_directory(value: Any, workspace_path: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = Path(workspace_path) / path
        return str(path.resolve())
    except (OSError, RuntimeError, ValueError):
        return raw


def _guided_schema_issues(value: Any) -> list[tuple[str, str]]:
    """Find unknown guided model, execution, policy, or threat keys before projection."""

    if not isinstance(value, dict):
        return []
    issues: list[tuple[str, str]] = []
    models = value.get("models")
    if isinstance(models, dict):
        for role in models:
            if role not in MODEL_ROLES:
                issues.append((f"models.{role}", f"unknown model role {role!r}"))
        for role in MODEL_ROLES:
            reference = models.get(role)
            if not isinstance(reference, dict):
                continue
            for field in reference:
                if field not in MODEL_REFERENCE_FIELDS:
                    issues.append(
                        (f"models.{role}.{field}", f"models.{role} accepts only provider and model")
                    )

    model_policy = value.get("model_policy")
    if isinstance(model_policy, dict):
        for field in model_policy:
            if field not in MODEL_POLICY_FIELDS:
                issues.append(
                    (f"model_policy.{field}", f"unknown model_policy field {field!r}")
                )

    system_prompts = value.get("system_prompts")
    if system_prompts is not None and not isinstance(system_prompts, dict):
        issues.append(("system_prompts", "system_prompts must be an object keyed by model role"))
    elif isinstance(system_prompts, dict):
        for role in system_prompts:
            if role not in SYSTEM_PROMPT_FIELDS:
                issues.append((f"system_prompts.{role}", f"unknown system prompt role {role!r}"))

    execution = value.get("execution")
    if isinstance(execution, dict):
        for field in execution:
            if field not in EXECUTION_FIELDS:
                issues.append((f"execution.{field}", f"unknown execution field {field!r}"))
        reasoning = execution.get("reasoning_effort")
        if isinstance(reasoning, dict):
            for role in reasoning:
                if role not in REASONING_FIELDS:
                    issues.append(
                        (
                            f"execution.reasoning_effort.{role}",
                            "reasoning_effort accepts only orchestrator and worker",
                        )
                    )
    policy = value.get("policy")
    if isinstance(policy, dict):
        for field in policy:
            if field not in POLICY_FIELDS:
                issues.append((f"policy.{field}", f"unknown policy field {field!r}"))
    security = value.get("security")
    if isinstance(security, dict):
        threats = security.get("threat_scenarios")
        if isinstance(threats, list):
            for index, threat in enumerate(threats):
                if not isinstance(threat, dict):
                    continue
                for field in threat:
                    if field not in THREAT_CONTRACT_FIELDS:
                        issues.append((
                            f"security.threat_scenarios[{index}].{field}",
                            f"unknown threat field {field!r}",
                        ))
    return issues


def _normalise_setup(value: Any, *, reject_unknown: bool) -> dict[str, Any]:
    if not isinstance(value, dict):
        return default_setup()
    schema_issues = _guided_schema_issues(value)
    if reject_unknown and schema_issues:
        paths = ", ".join(path for path, _ in schema_issues)
        if all(path.startswith("security.threat_scenarios[") for path, _ in schema_issues):
            raise FactoryError(f"setup contains unknown threat field(s): {paths}")
        raise FactoryError(f"setup contains unknown model/execution/policy field(s): {paths}")
    if _credential_shaped_paths(value, "setup"):
        raise FactoryError("setup contains credential-shaped data; store only provider/model references")
    merged = _deep_merge(default_setup(), _without_sensitive_keys(value))
    product = merged.get("product") if isinstance(merged.get("product"), dict) else {}
    testing = merged.get("testing") if isinstance(merged.get("testing"), dict) else {}
    security = merged.get("security") if isinstance(merged.get("security"), dict) else {}
    models = merged.get("models") if isinstance(merged.get("models"), dict) else {}
    model_policy = merged.get("model_policy") if isinstance(merged.get("model_policy"), dict) else {}
    system_prompts = merged.get("system_prompts") if isinstance(merged.get("system_prompts"), dict) else {}
    execution = merged.get("execution") if isinstance(merged.get("execution"), dict) else {}
    reasoning_effort = execution.get("reasoning_effort") if isinstance(execution.get("reasoning_effort"), dict) else {}
    policy = merged.get("policy") if isinstance(merged.get("policy"), dict) else {}
    workspace_path = _normalise_workspace_path(merged.get("workspace_path"))
    projected = {
        "intake_schema_version": merged.get("intake_schema_version"),
        "project_mode": merged.get("project_mode"),
        "workspace_path": workspace_path,
        "product": {
            **{key: copy.deepcopy(product.get(key)) for key in (
                "name", "problem", "outcome", "context", "existing_system"
            )},
            "success_metrics": _allowed_list(product.get("success_metrics")),
            "surfaces": _allowed_list(product.get("surfaces")),
        },
        "personas": _allowed_rows(merged.get("personas"), ("id", "name", "context", "need")),
        "user_stories": [
            {
                **{key: copy.deepcopy(item.get(key)) for key in ("id", "persona_id", "want", "so_that")},
                "paths": _allowed_list(item.get("paths")),
                "acceptance": _normalised_acceptance_rows(item.get("acceptance")),
            }
            for item in _allowed_list(merged.get("user_stories"))
            if isinstance(item, dict)
        ],
        "non_goals": _allowed_list(merged.get("non_goals")),
        "constraints": _allowed_list(merged.get("constraints")),
        "milestones": [
            {
                **{key: copy.deepcopy(item.get(key)) for key in ("id", "title", "outcome")},
                "story_ids": _allowed_list(item.get("story_ids")),
                "evidence": _allowed_list(item.get("evidence")),
                "acceptance": _normalised_acceptance_rows(item.get("acceptance")),
            }
            for item in _allowed_list(merged.get("milestones"))
            if isinstance(item, dict)
        ],
        "testing": {
            "focused_commands": _allowed_list(testing.get("focused_commands")),
            "integration_commands": _allowed_list(testing.get("integration_commands")),
            "browser_scenarios": _allowed_rows(testing.get("browser_scenarios"), ("name", "action", "expected")),
            "held_out_scenarios": _allowed_rows(testing.get("held_out_scenarios"), ("name", "given", "when", "then")),
            "evidence_requirements": _allowed_list(testing.get("evidence_requirements")),
        },
        "security": {
            "data_classification": security.get("data_classification"),
            "adversarial_lens": security.get("adversarial_lens"),
            "risk_triggers": _allowed_list(security.get("risk_triggers")),
            "data": _allowed_list(security.get("data")),
            "controls": _allowed_list(security.get("controls")),
            "human_gates": _allowed_list(security.get("human_gates")),
            "threat_scenarios": _allowed_rows(
                security.get("threat_scenarios"),
                THREAT_CONTRACT_FIELDS,
            ),
            "authority_decisions": _allowed_rows(security.get("authority_decisions"), ("id", "statement", "status", "rationale")),
        },
        "models": {
            role: {
                "provider": str(models.get(role, {}).get("provider") or "").strip().lower() if isinstance(models.get(role), dict) else "",
                "model": str(models.get(role, {}).get("model") or "").strip() if isinstance(models.get(role), dict) else "",
            }
            for role in MODEL_ROLES
        },
        "model_policy": {"preset": str(model_policy.get("preset") or "").strip().lower() or DEFAULT_PRESET_ID},
        "system_prompts": {
            role: str(system_prompts.get(role) or "").strip()
            for role in MODEL_ROLES
        },
        "execution": {
            # Persist and compile the same canonical values that readiness
            # evaluates. A whitespace-only directory must remain the empty
            # sentinel so every layer resolves it to <workspace>/.beads.
            "graph_backend": str(execution.get("graph_backend") or "").strip().lower() or "beads",
            "graph_mode": str(execution.get("graph_mode") or "").strip().lower() or "plan",
            "beads_directory": _normalise_beads_directory(execution.get("beads_directory"), workspace_path),
            "beads_isolated_authorized": execution.get("beads_isolated_authorized") is True,
            "reasoning_effort": {
                "orchestrator": str(reasoning_effort.get("orchestrator") or "").strip().lower() or "high",
                "worker": str(reasoning_effort.get("worker") or "").strip().lower() or "medium",
            },
        },
        "policy": {key: copy.deepcopy(policy.get(key)) for key in (
            "max_active_milestones", "max_parallel_slices", "repeated_failure_limit", "max_remediation_cycles"
        )},
    }
    classification = str(projected["security"].get("data_classification") or "").strip().lower()
    if classification == "regulatory":
        projected["security"]["data_classification"] = "regulated"
    return _redact_sensitive_values(projected)


def normalise_setup(value: Any) -> dict[str, Any]:
    return _normalise_setup(value, reject_unknown=True)


def resolve_setup_models(value: Any, model_catalog: Any = None) -> dict[str, Any]:
    """Normalise setup and execute its safe fill-only model preset."""

    return apply_model_policy_defaults(normalise_setup(value), model_catalog)


def _words(value: Any) -> int:
    return len(re.findall(r"\b[\w'-]+\b", str(value or "")))


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _model_ref(value: Any) -> tuple[str, str]:
    if not isinstance(value, dict):
        return "", ""
    return _text(value.get("provider")).lower(), _text(value.get("model"))


def _catalog_pairs(model_catalog: Any) -> set[tuple[str, str]] | None:
    if model_catalog is None:
        return None
    return authenticated_model_refs(model_catalog)


def _issue(code: str, path: str, message: str, help_text: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message, "help": help_text}


def _criterion_statement(value: Any) -> str:
    return _text(value.get("statement") if isinstance(value, dict) else value)


def _criterion_type(value: Any) -> str:
    return _text(value.get("type") if isinstance(value, dict) else "").lower()


def validate_intake(setup_value: Any, model_catalog: Any = None) -> dict[str, Any]:
    """Return deterministic readiness; no LLM judgement is involved."""

    raw_schema_issues = _guided_schema_issues(setup_value)
    setup = apply_model_policy_defaults(
        _normalise_setup(setup_value, reject_unknown=False), model_catalog
    )
    blockers: list[dict[str, str]] = [
        _issue(
            "setup.unknown_field",
            path,
            message,
            "Remove the unknown field; guided setup never drops model, execution, policy, or threat input silently.",
        )
        for path, message in raw_schema_issues
    ]
    warnings: list[dict[str, str]] = []
    checks: list[dict[str, Any]] = []

    def check(section: str, code: str, ok: bool, weight: int, path: str, message: str, help_text: str) -> None:
        checks.append({"section": section, "code": code, "ok": bool(ok), "weight": weight})
        if not ok:
            blockers.append(_issue(code, path, message, help_text))

    if setup.get("intake_schema_version") != INTAKE_SCHEMA_VERSION:
        blockers.append(_issue("intake.schema", "intake_schema_version", f"intake_schema_version must be {INTAKE_SCHEMA_VERSION}", "Reload the form and save it again."))

    mode = _text(setup.get("project_mode")).lower()
    check("scope", "project.mode", mode in {"existing", "greenfield"}, 2, "project_mode", "Choose whether this is an existing product or a greenfield build.", "The choice changes which technical context is mandatory.")

    workspace_raw = _text(setup.get("workspace_path"))
    workspace = Path(workspace_raw).expanduser() if workspace_raw else None
    workspace_ok = False
    if workspace is not None:
        if mode == "existing":
            workspace_ok = workspace.is_dir()
        else:
            workspace_ok = workspace.is_dir() or workspace.parent.is_dir()
    check("scope", "workspace.path", workspace_ok, 5, "workspace_path", "Provide a usable workspace path.", "Existing projects need an existing directory; greenfield paths need an existing parent directory.")
    if workspace and workspace.is_dir() and not (workspace / ".git").exists():
        warnings.append(_issue("workspace.git", "workspace_path", "The workspace is not a Git repository.", "Git checkpoints and exact-SHA evidence will be unavailable until the factory initializes Git."))

    product = setup.get("product") if isinstance(setup.get("product"), dict) else {}
    check("intent", "product.name", _words(product.get("name")) >= 1, 2, "product.name", "Name the product or capability.", "Use a stable name that will identify the factory mission and its evidence.")
    check("intent", "product.problem", _words(product.get("problem")) >= 8, 6, "product.problem", "Explain the user or business problem in enough detail.", "Describe who is affected, the current pain and why solving it matters; aim for at least one concrete sentence.")
    product_outcome = _text(product.get("outcome"))
    check("intent", "product.outcome", _words(product_outcome) >= 8 and not MICRO_TITLE.search(product_outcome), 7, "product.outcome", "Define an observable product outcome, not a micro-remediation.", "Say what a user can successfully do or what system behavior can be observed—not which files or individual tests should be edited.")
    check("intent", "product.context", _words(product.get("context")) >= 8, 3, "product.context", "Provide product and domain context.", "Explain the operating environment, vocabulary, important workflows and assumptions the agents must not invent.")
    if mode == "existing":
        check("intent", "product.existing_system", _words(product.get("existing_system")) >= 8, 4, "product.existing_system", "Describe the existing system and current behavior.", "Point the factory at relevant architecture, conventions and current limitations before it changes code.")
    success_metrics = [_text(item) for item in _list(product.get("success_metrics")) if _text(item)]
    check("intent", "product.metrics", len(success_metrics) >= 1 and all(_words(item) >= 4 for item in success_metrics), 4, "product.success_metrics", "Provide at least one measurable success signal.", "Use behavior or quality measures such as completion rate, latency, error rate, accessibility or a named acceptance journey.")

    personas = [item for item in _list(setup.get("personas")) if isinstance(item, dict)]
    persona_ids = [_text(item.get("id")) for item in personas]
    persona_valid = bool(personas) and all(
        _text(item.get("id")) and _words(item.get("name")) >= 1 and _words(item.get("context")) >= 4 and _words(item.get("need")) >= 4
        for item in personas
    ) and len(set(persona_ids)) == len(persona_ids)
    check("users", "personas.complete", persona_valid, 7, "personas", "Define at least one complete target user/persona.", "For each persona provide a unique ID, name, operating context and concrete need.")

    stories = [item for item in _list(setup.get("user_stories")) if isinstance(item, dict)]
    story_ids = [_text(item.get("id")) for item in stories]
    stories_valid = bool(stories) and len(set(story_ids)) == len(story_ids)
    stories_have_negative = bool(stories)
    for index, story in enumerate(stories):
        sid = _text(story.get("id")) or f"story-{index + 1}"
        criteria = _list(story.get("acceptance"))
        paths = _list(story.get("paths"))
        paths_valid = bool(paths) and all(
            isinstance(path, str) and bool(path.strip()) for path in paths
        )
        criterion_ids = [_text(item.get("id")) for item in criteria if isinstance(item, dict)]
        criteria_valid = (
            len(criteria) >= 2
            and len(criterion_ids) == len(criteria)
            and all(criterion_ids)
            and len(set(criterion_ids)) == len(criterion_ids)
            and all(isinstance(item, dict) for item in criteria)
            and all(_criterion_type(item) in ACCEPTANCE_TYPES for item in criteria)
            and all(_words(_criterion_statement(item)) >= 5 for item in criteria)
        )
        has_positive = any(_criterion_type(item) == "happy" and _criterion_statement(item) for item in criteria)
        has_negative = any(
            _criterion_type(item) in {"negative", "recovery", "boundary", "abuse"} and _criterion_statement(item)
            for item in criteria
        )
        stories_have_negative = stories_have_negative and bool(has_negative)
        story_ok = (
            _text(story.get("id"))
            and _text(story.get("persona_id")) in set(persona_ids)
            and _words(story.get("want")) >= 4
            and _words(story.get("so_that")) >= 4
            and paths_valid
            and criteria_valid
            and has_positive
            and has_negative
        )
        if not story_ok:
            stories_valid = False
            blockers.append(_issue("story.incomplete", f"user_stories[{index}]", f"User story {sid} is incomplete or not tied to a known persona.", "Provide a unique ID, persona, desired capability, value, at least one positive criterion, and at least one negative/recovery/boundary/abuse criterion."))
        if not paths_valid:
            blockers.append(_issue(
                "story.paths", f"user_stories[{index}].paths",
                f"User story {sid} has no durable implementation coordinates.",
                "Provide a non-empty list of non-empty repository path or glob coordinates before compilation.",
            ))
    check("users", "stories.present", bool(stories), 7, "user_stories", "Add at least one structured user story.", "Capture persona, desired capability, value and acceptance criteria separately so the factory does not invent them.")
    check("users", "stories.complete", stories_valid, 8, "user_stories", "Complete every story and keep story IDs unique.", "Each story needs a known persona, concrete want and value, plus at least two observable acceptance criteria including a positive/happy criterion.")
    check("users", "stories.negative", stories_have_negative, 4, "user_stories[].acceptance", "Add a negative, recovery, boundary, or abuse acceptance criterion to every story.", "Each independently implemented story needs its own unsafe, failure, or recovery path; coverage on another story is not enough.")

    non_goals = [_text(item) for item in _list(setup.get("non_goals")) if _text(item)]
    constraints = [_text(item) for item in _list(setup.get("constraints")) if _text(item)]
    check("scope", "scope.non_goals", bool(non_goals), 4, "non_goals", "Name at least one explicit non-goal.", "State what this mission must not expand into; this is the primary scope-creep guard.")
    check("scope", "scope.constraints", bool(constraints), 4, "constraints", "Provide at least one implementation or operating constraint.", "Include required stack, compatibility, data, regulatory, delivery or architectural constraints.")

    milestones = [item for item in _list(setup.get("milestones")) if isinstance(item, dict)]
    mapped_story_ids: list[str] = []
    milestone_ids = [_text(item.get("id")) for item in milestones]
    milestones_valid = bool(milestones) and len(set(milestone_ids)) == len(milestone_ids)
    for index, milestone in enumerate(milestones):
        mid = _text(milestone.get("id")) or f"milestone-{index + 1}"
        mapped = [_text(item) for item in _list(milestone.get("story_ids")) if _text(item)]
        mapped_story_ids.extend(mapped)
        acceptance = _list(milestone.get("acceptance"))
        acceptance_ids = [_text(item.get("id")) for item in acceptance if isinstance(item, dict)]
        milestone_outcome = _text(milestone.get("outcome"))
        milestone_ok = (
            _text(milestone.get("id"))
            and _words(milestone_outcome) >= 7
            and not MICRO_TITLE.search(milestone_outcome)
            and bool(mapped)
            and set(mapped).issubset(set(story_ids))
            and bool(acceptance)
            and len(acceptance_ids) == len(acceptance)
            and all(acceptance_ids)
            and len(set(acceptance_ids)) == len(acceptance_ids)
            and all(isinstance(item, dict) for item in acceptance)
            and all(_criterion_type(item) in ACCEPTANCE_TYPES for item in acceptance)
            and all(_words(_criterion_statement(item)) >= 5 for item in acceptance)
        )
        if not milestone_ok:
            milestones_valid = False
            blockers.append(_issue("milestone.incomplete", f"milestones[{index}]", f"Milestone {mid} is not a coherent, accepted product increment.", "Give it a unique ID, observable outcome, mapped stories and milestone-level acceptance criteria."))
    check("plan", "milestones.present", bool(milestones), 7, "milestones", "Define at least one product milestone.", "A milestone is a user-observable capability, not a code layer or a list of fixes.")
    check("plan", "milestones.complete", milestones_valid, 8, "milestones", "Complete every milestone and keep milestone IDs unique.", "Each milestone needs an observable outcome, mapped stories and milestone-level acceptance criteria.")
    story_mapping_ok = bool(stories) and sorted(mapped_story_ids) == sorted(story_ids)
    check("plan", "milestones.story_mapping", story_mapping_ok, 5, "milestones[].story_ids", "Map every user story to exactly one milestone.", "One owner prevents duplicate implementation and makes milestone progress meaningful.")

    testing = setup.get("testing") if isinstance(setup.get("testing"), dict) else {}
    focused = [_text(item) for item in _list(testing.get("focused_commands")) if _text(item)]
    integration = [_text(item) for item in _list(testing.get("integration_commands")) if _text(item)]
    held_out = [item for item in _list(testing.get("held_out_scenarios")) if isinstance(item, dict)]
    held_out_valid = bool(held_out) and all(
        _words(item.get("name")) >= 2 and _words(item.get("given")) >= 3 and _words(item.get("when")) >= 3 and _words(item.get("then")) >= 3
        for item in held_out
    )
    check("testing", "testing.focused", bool(focused), 5, "testing.focused_commands", "Provide focused test commands.", "These run inside each functional slice before review.")
    check("testing", "testing.integration", bool(integration), 6, "testing.integration_commands", "Provide full or integration test commands.", "These are factory-owned milestone gates, not optional worker suggestions.")
    check("testing", "testing.holdout", held_out_valid, 7, "testing.held_out_scenarios", "Define at least one held-out scenario with Given/When/Then.", "The builder must not be allowed to rewrite the acceptance challenge it is judged against.")
    surfaces = {_text(item).lower() for item in _list(product.get("surfaces"))}
    check("intent", "product.surfaces", bool(surfaces), 3, "product.surfaces", "Declare at least one product interaction surface.", "Name the surfaces users or systems act through, such as web UI, desktop UI, CLI, public API, or internal API.")
    browser = [item for item in _list(testing.get("browser_scenarios")) if isinstance(item, dict)]
    browser_ok = bool(browser) and all(_words(item.get("action")) >= 3 and _words(item.get("expected")) >= 3 for item in browser)
    check("testing", "testing.browser", browser_ok, 4, "testing.browser_scenarios", "Add at least one real interaction scenario for the declared surface.", "Exercise an actual click, form, navigation, CLI operation, or API call and assert the post-action state.")

    security = setup.get("security") if isinstance(setup.get("security"), dict) else {}
    data_class = _text(security.get("data_classification")).lower()
    triggers = {_text(item).lower() for item in _list(security.get("risk_triggers")) if _text(item)}
    risk = derive_independent_mission_risk(
        security,
        {
            "product": product,
            "personas": personas,
            "user_stories": stories,
            "constraints": constraints,
            "milestones": milestones,
            "testing": testing,
        },
    )
    high_risk = risk in {"R3", "R4"}
    check("security", "security.classification", data_class in DATA_CLASSES, 3, "security.data_classification", "Classify the data handled by the product.", "Choose none, internal, personal, sensitive or regulated.")
    lens = _text(security.get("adversarial_lens")).lower()
    check("security", "security.lens", lens == "kryptonite", 3, "security.adversarial_lens", "The mandatory Kryptonite adversarial lens is not configured.", "Use the Kryptonite lens for hostile assumptions, abuse cases, boundary failures, and evidence authenticity; it cannot be disabled.")
    threats = [item for item in _list(security.get("threat_scenarios")) if isinstance(item, dict)]
    required_threats = 2 if high_risk else 1
    threat_ids = [_text(item.get("id")) for item in threats]
    threats_ok = (
        len(threats) >= required_threats
        and len(set(threat_ids)) == len(threat_ids)
        and len({threat_semantic_identity(item) for item in threats}) == len(threats)
        and all(
            _text(item.get("id"))
            and set(item) == set(THREAT_CONTRACT_FIELDS)
            and threat_contract_is_substantive(item)
            for item in threats
        )
    )
    check("security", "security.threats", threats_ok, 6, "security.threat_scenarios", f"Provide at least {required_threats} adversarial threat scenario(s).", "Describe the attempted misuse/failure and the observable control that must stop or contain it.")
    decisions = [item for item in _list(security.get("authority_decisions")) if isinstance(item, dict)]
    decision_ids = [_text(item.get("id")) for item in decisions]
    decisions_ok = (
        bool(decisions)
        and len(set(decision_ids)) == len(decision_ids)
        and all(
            _text(item.get("id"))
            and _text(item.get("status")).lower() == "locked"
            and decision_text_is_substantive(item.get("statement"))
            for item in decisions
        )
    )
    check("security", "security.decisions", decisions_ok, 6, "security.authority_decisions", "Record and lock at least one authority or product decision.", "Resolve product ownership, identity, authorization, data, migration, publication, or external-side-effect authority before implementation; the factory must not invent these decisions.")

    policy = setup.get("policy") if isinstance(setup.get("policy"), dict) else {}
    policy_bounds = {
        "max_active_milestones": (1, 1),
        "max_parallel_slices": (1, 2),
        "repeated_failure_limit": (1, 2),
        "max_remediation_cycles": (1, 1),
    }
    policy_ok = all(
        isinstance(policy.get(key), int)
        and not isinstance(policy.get(key), bool)
        and minimum <= policy[key] <= maximum
        for key, (minimum, maximum) in policy_bounds.items()
    ) and set(policy) == POLICY_FIELDS
    check("plan", "policy.valid", policy_ok, 3, "policy", "Use bounded factory concurrency, retry, and remediation limits.", "Allow one active milestone, at most two parallel slices, at most two identical failures, and one remediation cycle.")

    model_policy = setup.get("model_policy") if isinstance(setup.get("model_policy"), dict) else {}
    check(
        "models", "model_policy.preset", _text(model_policy.get("preset")) == DEFAULT_PRESET_ID, 2,
        "model_policy.preset", "Select the supported orchestrator/worker model preset.",
        "The v0.3 preset maps integrator to orchestrator and builder to worker without inferring review models.",
    )
    execution = setup.get("execution") if isinstance(setup.get("execution"), dict) else {}
    reasoning = execution.get("reasoning_effort") if isinstance(execution.get("reasoning_effort"), dict) else {}
    graph_backend = _text(execution.get("graph_backend"))
    execution_ok = (
        graph_backend == "beads"
        and _text(execution.get("graph_mode")) in {"plan", "apply"}
        and _text(reasoning.get("orchestrator")) in {"low", "medium", "high"}
        and _text(reasoning.get("worker")) in {"low", "medium", "high"}
        and isinstance(execution.get("beads_isolated_authorized"), bool)
    )
    check(
        "plan", "execution.valid", execution_ok, 2, "execution",
        "Configure the graph backend and execution reasoning policy.",
        "Use graph_mode plan or apply; Beads apply performs a fail-closed binary/directory preflight.",
    )
    if execution_ok and graph_backend == "beads" and _text(execution.get("graph_mode")) == "apply":
        try:
            try:
                from .beads_adapter import preflight_beads
            except ImportError:
                from beads_adapter import preflight_beads  # type: ignore
            beads_directory = _text(execution.get("beads_directory")) or str(
                Path(_text(setup.get("workspace_path"))).expanduser() / ".beads"
            )
            preflight_beads(
                beads_directory,
                authorize_isolated=bool(execution.get("beads_isolated_authorized")),
            )
        except Exception as exc:
            blockers.append(_issue(
                "execution.beads.preflight", "execution.beads_directory",
                f"Beads graph application is not ready: {exc}",
                "Install a compatible bd binary and select an existing .beads directory, or explicitly authorize an isolated directory. No database is initialized automatically.",
            ))

    catalog_pairs = _catalog_pairs(model_catalog)
    models = setup.get("models") if isinstance(setup.get("models"), dict) else {}
    refs: dict[str, tuple[str, str]] = {}
    for role in MODEL_ROLES:
        ref = _model_ref(models.get(role))
        refs[role] = ref
        present = bool(ref[0] and ref[1])
        check("models", f"models.{role}", present, 3, f"models.{role}", f"Select an authenticated provider and model for the {role} role.", "Only provider/model references are stored; credentials remain in the active Hermes profile.")
        if present and catalog_pairs is not None and ref not in catalog_pairs:
            blockers.append(_issue("models.unavailable", f"models.{role}", f"The selected {role} model is not authenticated or available in the active profile.", "Refresh model options or authenticate the provider in Hermes, then choose an available model."))
    builder_ref = refs.get("builder", ("", ""))
    for role in NON_BUILDER_ROLES:
        distinct = bool(refs.get(role, ("", ""))[0]) and refs.get(role) != builder_ref
        check("models", f"models.{role}.independent", distinct, 3, f"models.{role}", f"The {role} model must differ from the builder model.", "A fresh context on a different model reduces builder-review correlated blind spots.")
    if high_risk and refs.get("adversary", ("", ""))[0] == builder_ref[0]:
        warnings.append(_issue("models.adversary.provider", "models.adversary", "High-risk work uses the same provider for builder and adversary.", "Prefer a different provider as well as a different model when one is authenticated."))

    total_weight = sum(int(item["weight"]) for item in checks) or 1
    passed_weight = sum(int(item["weight"]) for item in checks if item["ok"])
    score = round(100 * passed_weight / total_weight)
    section_rows: list[dict[str, Any]] = []
    for name in ("intent", "users", "scope", "plan", "testing", "security", "models"):
        rows = [item for item in checks if item["section"] == name]
        section_rows.append({
            "id": name,
            "passed": sum(1 for item in rows if item["ok"]),
            "total": len(rows),
            "ready": bool(rows) and all(item["ok"] for item in rows),
        })

    return {
        "ready": not blockers and all(item["ok"] for item in checks),
        "score": score,
        "risk": risk,
        "blockers": blockers,
        "warnings": warnings,
        "sections": section_rows,
        "checks": checks,
        "requirements": {
            "roles": list(MODEL_ROLES),
            "independent_from_builder": list(NON_BUILDER_ROLES),
            "mandatory_gates": ["focused_tests", "integration_tests", "held_out_scenario", "adversarial_review"],
        },
    }


def _slug(value: Any, fallback: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", _text(value)).strip("-").lower()
    return slug or fallback


def _criterion_rows(values: Any, prefix: str) -> list[dict[str, str]]:
    del prefix  # IDs are validated at intake; compilation must not rewrite them.
    result: list[dict[str, str]] = []
    for item in _list(values):
        if not isinstance(item, dict):
            continue
        result.append({
            "id": _text(item.get("id")),
            "type": _criterion_type(item),
            "statement": _criterion_statement(item),
        })
    return result


def _milestone_acceptance_rows(
    milestone: dict[str, Any],
    story_ids: list[str],
    story_by_id: dict[str, dict[str, Any]],
    prefix: str,
) -> list[dict[str, str]]:
    """Preserve local criteria, then append every owned story criterion once."""

    result = _criterion_rows(milestone.get("acceptance"), prefix)
    seen = {(row["id"], row["type"], row["statement"]) for row in result}
    for story_id in story_ids:
        for row in _criterion_rows(story_by_id[story_id].get("acceptance"), story_id):
            identity = (row["id"], row["type"], row["statement"])
            if identity not in seen:
                result.append(row)
                seen.add(identity)
    return result


def compile_manifest(setup_value: Any, model_catalog: Any = None) -> dict[str, Any]:
    setup = resolve_setup_models(setup_value, model_catalog)
    readiness = validate_intake(setup, model_catalog=model_catalog)
    if not readiness["ready"]:
        raise FactoryError("factory intake is not ready: " + "; ".join(item["message"] for item in readiness["blockers"]))

    product = setup["product"]
    personas = setup["personas"]
    stories = setup["user_stories"]
    milestones_in = setup["milestones"]
    security = setup["security"]
    testing = setup["testing"]
    mission_id = _slug(product.get("name"), "factory-mission")
    risk = readiness["risk"]
    risk_triggers = [_text(item) for item in security.get("risk_triggers", []) if _text(item)]
    if not risk_triggers:
        risk_triggers = ["independent behavioral and adversarial acceptance"]
    decisions = []
    for index, item in enumerate(security.get("authority_decisions", []), start=1):
        decisions.append({
            "id": _text(item.get("id")) or f"D{index}",
            "statement": _text(item.get("statement")),
            "status": _text(item.get("status")).lower() or "locked",
        })
    decision_ids = [item["id"] for item in decisions]

    story_by_id = {_text(item.get("id")): item for item in stories}
    story_to_milestone: dict[str, str] = {}
    milestones: list[dict[str, Any]] = []
    slices: list[dict[str, Any]] = []
    previous_mid = ""
    for m_index, source in enumerate(milestones_in, start=1):
        mid = _text(source.get("id")) or f"M{m_index}"
        story_ids = [_text(item) for item in source.get("story_ids", []) if _text(item)]
        slice_ids: list[str] = []
        previous_sid = ""
        for s_index, story_id in enumerate(story_ids, start=1):
            story = story_by_id[story_id]
            sid = f"{mid}-S{s_index}"
            story_to_milestone[story_id] = mid
            slice_ids.append(sid)
            evidence = list(testing.get("focused_commands", []))
            evidence.extend(
                f"acceptance scenario: {_criterion_statement(item)}"
                for item in story.get("acceptance", [])
                if _criterion_statement(item)
            )
            slices.append({
                "id": sid,
                "story_id": story_id,
                "milestone_id": mid,
                "outcome": f"{_text(story.get('persona_id'))} can {_text(story.get('want'))} so that {_text(story.get('so_that'))}",
                "risk": risk,
                "risk_triggers": risk_triggers,
                "requires_decisions": decision_ids,
                "depends_on": [previous_sid] if previous_sid else [],
                "paths": [_text(item) for item in story.get("paths", []) if _text(item)],
                "acceptance": _criterion_rows(story.get("acceptance"), sid),
                "evidence": evidence,
                "review_required": True,
                "review_roles": ["verifier", "adversary"],
            })
            previous_sid = sid
        milestones.append({
            "id": mid,
            "outcome": _text(source.get("outcome")),
            "depends_on": [previous_mid] if previous_mid else [],
            "slices": slice_ids,
            "story_ids": story_ids,
            "acceptance": _milestone_acceptance_rows(source, story_ids, story_by_id, mid),
        })
        previous_mid = mid

    return {
        "schema_version": SCHEMA_VERSION,
        "mission": {
            "id": mission_id,
            "name": _text(product.get("name")),
            "problem": _text(product.get("problem")),
            "outcome": _text(product.get("outcome")),
            "context": _text(product.get("context")),
            "existing_system": _text(product.get("existing_system")),
            "project_mode": setup["project_mode"],
            "workspace_path": setup["workspace_path"],
            "personas": copy.deepcopy(personas),
            "user_stories": copy.deepcopy(stories),
            "out_of_scope": copy.deepcopy(setup["non_goals"]),
            "constraints": copy.deepcopy(setup["constraints"]),
            "success_metrics": copy.deepcopy(product.get("success_metrics", [])),
            "surfaces": copy.deepcopy(product.get("surfaces", [])),
        },
        "policy": copy.deepcopy(setup["policy"]),
        "models": copy.deepcopy(setup["models"]),
        "model_policy": manifest_model_policy(_text(setup["model_policy"].get("preset"))),
        "system_prompts": copy.deepcopy(setup["system_prompts"]),
        "execution": copy.deepcopy(setup["execution"]),
        "testing": copy.deepcopy(testing),
        "security": {
            **copy.deepcopy(security),
            "derived_risk": risk,
            "mandatory_adversarial_review": True,
        },
        "decisions": decisions,
        "milestones": milestones,
        "slices": slices,
        "intake": {
            "schema_version": INTAKE_SCHEMA_VERSION,
            "readiness_score": readiness["score"],
            "user_authored_intent": True,
        },
    }


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _recover_factory_transaction(factory_dir: Path) -> None:
    parent = factory_dir.parent
    if not parent.exists():
        return
    backups = sorted(parent.glob(".factory-backup-*"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not factory_dir.exists() and backups:
        os.replace(backups[0], factory_dir)
        backups = backups[1:]
    for path in backups:
        if path.is_dir():
            shutil.rmtree(path)
    for path in parent.glob(".factory-stage-*"):
        if path.is_dir():
            shutil.rmtree(path)


def _publish_factory_pair(factory_dir: Path, manifest: dict[str, Any], state: dict[str, Any]) -> None:
    """Publish manifest/state as one directory transaction.

    Both files are fully written in a sibling staging directory. The existing
    factory directory is retained until staging is complete and restored if the
    final directory swap fails, so callers never observe a one-file pair.
    """
    parent = factory_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    _recover_factory_transaction(factory_dir)
    stage = Path(tempfile.mkdtemp(prefix=".factory-stage-", dir=str(parent)))
    backup = parent / f".factory-backup-{stage.name.removeprefix('.factory-stage-')}"
    moved_existing = False
    try:
        if factory_dir.exists():
            if not factory_dir.is_dir():
                raise FactoryError(f"factory path is not a directory: {factory_dir}")
            shutil.copytree(factory_dir, stage, dirs_exist_ok=True)
        _atomic_write_json(stage / "state.json", state)
        _atomic_write_json(stage / "manifest.json", manifest)
        if factory_dir.exists():
            os.replace(factory_dir, backup)
            moved_existing = True
        try:
            os.replace(stage, factory_dir)
        except Exception:
            if moved_existing and backup.exists() and not factory_dir.exists():
                os.replace(backup, factory_dir)
                moved_existing = False
            raise
        if moved_existing and backup.exists():
            shutil.rmtree(backup)
            moved_existing = False
    finally:
        if stage.exists():
            shutil.rmtree(stage)
        if moved_existing and backup.exists() and not factory_dir.exists():
            os.replace(backup, factory_dir)
        elif backup.exists():
            shutil.rmtree(backup)


def plugin_data_dir() -> Path:
    try:
        from plugins.plugin_storage import plugin_data_dir as _plugin_data_dir

        return _plugin_data_dir("dark-factory")
    except Exception:
        try:
            from hermes_constants import get_hermes_home

            home = get_hermes_home()
        except Exception:
            home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
        path = Path(home) / "plugin-data" / "dark-factory"
        path.mkdir(parents=True, exist_ok=True)
        return path


def setup_path() -> Path:
    return plugin_data_dir() / "setup.json"


_LEGACY_THREAT_FIELDS = frozenset({"description", "mitigation", "severity", "threat"})


def _migrate_saved_setup(value: Any) -> Any:
    """Migrate the pre-contract threat rows written by the v0.2 UI.

    The public intake boundary remains strict: new payloads with unknown
    fields are rejected. Saved setup is different because it can outlive the
    UI that wrote it. Preserve the useful legacy text, drop the obsolete
    severity field, and then run the canonical normaliser before returning or
    persisting the result.
    """
    if not isinstance(value, dict):
        return value
    security = value.get("security")
    threats = security.get("threat_scenarios") if isinstance(security, dict) else None
    if not isinstance(threats, list):
        return value

    migrated = copy.deepcopy(value)
    migrated_security = migrated["security"]
    changed = False
    rows: list[Any] = []
    for index, item in enumerate(migrated_security["threat_scenarios"]):
        if not isinstance(item, dict) or not _LEGACY_THREAT_FIELDS.intersection(item):
            rows.append(item)
            continue
        row = dict(item)
        if not str(row.get("scenario") or "").strip():
            for legacy_key in ("threat", "description"):
                legacy_value = row.get(legacy_key)
                if isinstance(legacy_value, str) and legacy_value.strip():
                    row["scenario"] = legacy_value
                    break
        if not str(row.get("expected_control") or "").strip():
            legacy_value = row.get("mitigation")
            if isinstance(legacy_value, str) and legacy_value.strip():
                row["expected_control"] = legacy_value
        if not str(row.get("id") or "").strip():
            row["id"] = f"T{index + 1}"
        row.setdefault("name", "")
        row.setdefault("attack_surface", "")
        for legacy_key in _LEGACY_THREAT_FIELDS:
            row.pop(legacy_key, None)
        rows.append(row)
        changed = True

    if not changed:
        return value
    migrated_security["threat_scenarios"] = rows
    return migrated


def load_setup() -> dict[str, Any]:
    path = setup_path()
    if not path.exists():
        return default_setup()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        migrated = _migrate_saved_setup(raw)
        setup = normalise_setup(migrated)
        if migrated != raw:
            try:
                _atomic_write_json(path, setup)
            except OSError:
                # A read-only data directory should not strand an otherwise
                # valid setup after an in-memory schema migration.
                pass
        return setup
    except (OSError, json.JSONDecodeError):
        return default_setup()


def save_setup(value: Any) -> dict[str, Any]:
    setup = normalise_setup(value)
    _atomic_write_json(setup_path(), setup)
    return setup


def _state_is_pristine(state: Any, manifest: dict[str, Any]) -> bool:
    if not isinstance(state, dict):
        return False
    fresh = initial_state(manifest)
    return (
        state.get("schema_version") == SCHEMA_VERSION
        and state.get("mission_id") == manifest["mission"]["id"]
        and state.get("manifest_digest") == fresh["manifest_digest"]
        and state.get("revision") == 0
        and state.get("integrator_authority") is None
        and state.get("milestones") == fresh["milestones"]
        and state.get("slices") == fresh["slices"]
        and state.get("events") == []
    )


def compile_to_workspace(setup_value: Any, model_catalog: Any = None) -> dict[str, Any]:
    setup = resolve_setup_models(setup_value, model_catalog)
    manifest = compile_manifest(setup, model_catalog=model_catalog)
    workspace = Path(setup["workspace_path"]).expanduser().resolve()
    if not workspace.exists():
        workspace.mkdir(parents=False, exist_ok=False)
    factory_dir = workspace / ".hermes" / "factory"
    manifest_path = factory_dir / "manifest.json"
    state_path = factory_dir / "state.json"
    # Validate and construct both artifacts before publishing either path.
    state = initial_state(manifest)
    with _state_file_lock(state_path):
        manifest_exists = manifest_path.exists()
        state_exists = state_path.exists()
        if manifest_exists != state_exists:
            raise FactoryError(
                "factory manifest/state pair is incomplete; preserve the workspace and use recovery "
                "or an explicit audited reset before recompiling"
            )
        if manifest_exists:
            try:
                existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                existing_state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise FactoryError(
                    "existing factory manifest/state pair is unreadable; preserve it and use recovery "
                    "or an explicit audited reset before recompiling"
                ) from exc
            if not isinstance(existing_manifest, dict) or not isinstance(existing_state, dict):
                raise FactoryError(
                    "existing factory manifest/state pair has an incompatible shape; preserve it and use recovery "
                    "or an explicit audited reset before recompiling"
                )
            existing_manifest_check = validate_manifest(existing_manifest)
            if not existing_manifest_check["valid"]:
                raise FactoryError(
                    "existing factory manifest is invalid; preserve it and use recovery or an explicit audited reset"
                )
            if (
                _manifest_digest(existing_manifest) != _manifest_digest(manifest)
                or existing_manifest != manifest
            ):
                raise FactoryError(
                    "existing factory manifest does not exactly match this compilation; preserve it and use recovery or an explicit audited reset"
                )
            # The signed complete state is authoritative. Validate its HMAC and
            # exact manifest-compatible structure before inspecting visible
            # fields to decide whether the pair is pristine.
            _validate_state_compatibility(existing_manifest, existing_state)
            if not _state_is_pristine(existing_state, existing_manifest):
                raise FactoryError("refusing to overwrite non-pristine factory state; use a new mission/workspace or an explicit audited reset")
        _publish_factory_pair(factory_dir, manifest, state)
    launch = {
        "status": "armed",
        "manifest_path": str(manifest_path),
        "state_path": str(state_path),
        "workspace_path": str(workspace),
        "models": setup["models"],
        "model_policy": setup["model_policy"],
        "execution": setup["execution"],
        "credentials_stored": False,
    }
    metadata_warnings: list[str] = []
    try:
        saved = save_setup(setup)
        launch["models"] = saved["models"]
        _atomic_write_json(plugin_data_dir() / "launch.json", launch)
    except OSError as exc:
        metadata_warnings.append(f"factory armed but profile metadata could not be saved: {exc}")
    if metadata_warnings:
        launch["warnings"] = metadata_warnings
    return {"manifest": manifest, **launch}


def import_manifest_to_workspace(
    manifest_value: Any,
    *,
    workspace_path: str | Path | None = None,
) -> dict[str, Any]:
    """Import one canonical schema-v2 manifest as a pristine factory pair.

    Import is deliberately not compilation: the manifest is preserved as the
    authored execution contract, while the initial state is created locally.
    Beads remains the only graph backend, and an existing/progressed pair is
    never overwritten. Callers must perform active-profile model checks before
    invoking this side-effecting function.
    """
    if not isinstance(manifest_value, dict):
        raise FactoryError("manifest import requires a JSON object")
    manifest = copy.deepcopy(manifest_value)
    if _credential_shaped_paths(manifest):
        raise FactoryError("manifest import contains credential-shaped data; store only provider/model references")
    if workspace_path is not None:
        requested = _normalise_workspace_path(workspace_path)
        if not requested:
            raise FactoryError("manifest import workspace_path must be a non-empty absolute path")
        mission = manifest.get("mission")
        if not isinstance(mission, dict):
            raise FactoryError("manifest import requires a mission object")
        mission["workspace_path"] = requested
    execution = manifest.get("execution")
    if not isinstance(execution, dict) or execution.get("graph_backend") != "beads":
        raise FactoryError("manifest import requires execution.graph_backend=beads")
    check = validate_manifest(manifest)
    if not check["valid"]:
        raise FactoryError("manifest import rejected: " + "; ".join(check.get("errors", [])))

    workspace = Path(manifest["mission"]["workspace_path"]).expanduser().resolve()
    if not workspace.exists():
        workspace.mkdir(parents=False, exist_ok=False)
    if not workspace.is_dir():
        raise FactoryError("manifest import workspace_path is not a directory")
    factory_dir = workspace / ".hermes" / "factory"
    manifest_path = factory_dir / "manifest.json"
    state_path = factory_dir / "state.json"
    state = initial_state(manifest)
    with _state_file_lock(state_path):
        manifest_exists = manifest_path.exists()
        state_exists = state_path.exists()
        if manifest_exists != state_exists:
            raise FactoryError(
                "factory manifest/state pair is incomplete; preserve the workspace and use recovery "
                "or an explicit audited reset before importing"
            )
        if manifest_exists:
            try:
                existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                existing_state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise FactoryError(
                    "existing factory manifest/state pair is unreadable; preserve it and use recovery "
                    "or an explicit audited reset before importing"
                ) from exc
            if not isinstance(existing_manifest, dict) or not isinstance(existing_state, dict):
                raise FactoryError(
                    "existing factory manifest/state pair has an incompatible shape; preserve it and use recovery "
                    "or an explicit audited reset before importing"
                )
            existing_check = validate_manifest(existing_manifest)
            if not existing_check["valid"]:
                raise FactoryError(
                    "existing factory manifest is invalid; preserve it and use recovery or an explicit audited reset"
                )
            if existing_manifest != manifest or _manifest_digest(existing_manifest) != _manifest_digest(manifest):
                raise FactoryError(
                    "existing factory manifest does not exactly match this import; preserve it and use recovery or an explicit audited reset"
                )
            _validate_state_compatibility(existing_manifest, existing_state)
            if not _state_is_pristine(existing_state, existing_manifest):
                raise FactoryError(
                    "refusing to overwrite non-pristine factory state; use a new mission/workspace or an explicit audited reset"
                )
        _publish_factory_pair(factory_dir, manifest, state)

    launch = {
        "status": "armed",
        "source": "manifest_import",
        "manifest_path": str(manifest_path),
        "state_path": str(state_path),
        "workspace_path": str(workspace),
        "models": manifest["models"],
        "model_policy": manifest["model_policy"],
        "execution": manifest["execution"],
        "credentials_stored": False,
    }
    metadata_warnings: list[str] = []
    try:
        _atomic_write_json(plugin_data_dir() / "launch.json", launch)
    except OSError as exc:
        metadata_warnings.append(f"factory imported but profile metadata could not be saved: {exc}")
    if metadata_warnings:
        launch["warnings"] = metadata_warnings
    return {"manifest": manifest, **launch}
