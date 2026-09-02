"""Deterministic state machine for the Hermes Dark Factory prototype.

The engine intentionally treats milestone acceptance as the controlled variable.
Edits, tests, reviews, and retries are evidence-producing activities, not progress
by themselves.
"""

from __future__ import annotations

import builtins
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import stat
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 2
_PROCESS_KEY_ATTR = "_hermes_dark_factory_process_key"


def _load_or_create_attestation_key() -> bytes:
    """Return the profile-scoped key used to attest durable factory state."""

    hermes_home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser()
    key_dir = hermes_home / "plugin-data" / "dark-factory"
    key_path = key_dir / "review-attestation.key"
    key_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    try:
        info = key_path.lstat()
    except FileNotFoundError:
        key = secrets.token_bytes(32)
        try:
            descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return _load_or_create_attestation_key()
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(key)
            handle.flush()
            os.fsync(handle.fileno())
        return key

    if not stat.S_ISREG(info.st_mode) or key_path.is_symlink():
        raise RuntimeError("Dark Factory attestation key must be a regular file")
    if info.st_mode & 0o077:
        raise RuntimeError("Dark Factory attestation key permissions must be 0600")
    key = key_path.read_bytes()
    if len(key) != 32:
        raise RuntimeError("Dark Factory attestation key must contain exactly 32 bytes")
    return key


if not hasattr(builtins, _PROCESS_KEY_ATTR):
    setattr(builtins, _PROCESS_KEY_ATTR, _load_or_create_attestation_key())
_PROCESS_REVIEW_KEY = getattr(builtins, _PROCESS_KEY_ATTR)
_PROCESS_KEY_OVERRIDE_LOCK = threading.RLock()
MIN_ATTESTATION_KEY_BYTES = 32
MIN_ATTESTATION_KEY_ENTROPY = 3.0
DEFAULT_MANIFEST = ".hermes/factory/manifest.json"
DEFAULT_STATE = ".hermes/factory/state.json"
MODEL_ROLES = ("integrator", "builder", "verifier", "adversary", "holdout")
MANIFEST_FIELDS = frozenset({
    "schema_version", "mission", "policy", "decisions", "milestones", "slices",
    "models", "model_policy", "execution", "testing", "security", "intake",
})
MANIFEST_OPTIONAL_FIELDS = MANIFEST_FIELDS | {"system_prompts"}
MISSION_REQUIRED_FIELDS = frozenset({
    "id", "name", "problem", "outcome", "context", "project_mode",
    "workspace_path", "personas", "user_stories", "out_of_scope", "constraints",
    "success_metrics", "surfaces",
})
# Guided compilation retains existing-system context when supplied. It is a
# legitimate optional v2 field; canonical direct manifests need not invent it.
MISSION_FIELDS = MISSION_REQUIRED_FIELDS | {"existing_system"}
PERSONA_FIELDS = frozenset({"id", "name", "context", "need"})
STORY_FIELDS = frozenset({
    "id", "persona_id", "want", "so_that", "acceptance", "paths",
})
ACCEPTANCE_FIELDS = frozenset({"id", "type", "statement"})
MILESTONE_FIELDS = frozenset({
    "id", "outcome", "depends_on", "slices", "acceptance", "story_ids",
})
SLICE_FIELDS = frozenset({
    "id", "story_id", "milestone_id", "outcome", "risk", "risk_triggers",
    "requires_decisions", "depends_on", "paths", "acceptance", "evidence",
    "review_required", "review_roles",
})
DECISION_FIELDS = frozenset({"id", "statement", "status"})
TESTING_FIELDS = frozenset({
    "focused_commands", "integration_commands", "browser_scenarios",
    "held_out_scenarios", "evidence_requirements",
})
BROWSER_SCENARIO_REQUIRED_FIELDS = frozenset({"action", "expected"})
BROWSER_SCENARIO_FIELDS = BROWSER_SCENARIO_REQUIRED_FIELDS | {"name"}
HELD_OUT_SCENARIO_FIELDS = frozenset({"name", "given", "when", "then"})
SECURITY_REQUIRED_FIELDS = frozenset({
    "data_classification", "risk_triggers", "threat_scenarios",
    "authority_decisions", "derived_risk", "mandatory_adversarial_review",
    "adversarial_lens",
})
# These optional guided-intake contracts preserve supplied context without
# widening direct manifests to arbitrary security/backend fields.
SECURITY_FIELDS = SECURITY_REQUIRED_FIELDS | {"data", "controls", "human_gates"}
SECURITY_DECISION_FIELDS = DECISION_FIELDS | {"rationale"}
MODEL_REFERENCE_FIELDS = frozenset({"provider", "model"})
SYSTEM_PROMPT_FIELDS = frozenset(MODEL_ROLES)
MODEL_POLICY_FIELDS = frozenset({
    "preset", "roles", "independent_from_builder", "automatic_fallback",
})
EXECUTION_FIELDS = frozenset({
    "graph_backend", "graph_mode", "beads_directory",
    "beads_isolated_authorized", "reasoning_effort",
})
REASONING_EFFORT_FIELDS = frozenset({"orchestrator", "worker"})
INTAKE_PROVENANCE_FIELDS = frozenset({
    "schema_version", "readiness_score", "user_authored_intent",
})
POLICY_FIELDS = frozenset({
    "max_active_milestones",
    "max_parallel_slices",
    "repeated_failure_limit",
    "max_remediation_cycles",
})
INTEGRATOR_AUTHORITY_FIELDS = frozenset({"session_id", "provider", "model"})
BUILDER_SLICE_ACTIONS = frozenset({"start_slice", "record_failure", "request_review"})
INTEGRATOR_SLICE_ACTIONS = frozenset(
    {"request_changes", "pass_review", "complete_slice", "block", "replan"}
)
MILESTONE_ACTIONS = frozenset(
    {"start_milestone", "validate_milestone", "complete_milestone", "block", "replan"}
)
STATE_FIELDS = frozenset({
    "schema_version", "mission_id", "manifest_digest", "created_at", "updated_at",
    "revision", "integrator_authority", "milestones", "slices", "events",
    "state_attestation",
})
MILESTONE_STATE_FIELDS = frozenset(
    {"status", "acceptance_passed", "scenario_receipts"}
)
COMPLETED_MILESTONE_STATE_FIELDS = MILESTONE_STATE_FIELDS | {
    "integration_sha", "holdout_review",
}
SLICE_STATE_FIELDS = frozenset({
    "status", "builder_authority", "attempt", "remediation_cycles",
    "failure_fingerprints", "candidate_sha", "last_rejected_sha", "checks",
    "acceptance_passed", "review",
})
STATE_EVENT_FIELDS = frozenset({"at", "entity_id", "action", "actor", "evidence"})
STATE_EVENT_ACTOR_FIELDS = INTEGRATOR_AUTHORITY_FIELDS | {"role"}
TRANSITION_AUTHORIZATION_ERROR = "factory transition actor is not authorized"
MODEL_POLICY_PRESET = "sol-luna"
CANONICAL_MODEL_POLICY = {
    "preset": MODEL_POLICY_PRESET,
    "roles": {
        "integrator": "orchestrator",
        "builder": "worker",
        "verifier": "verifier",
        "adversary": "adversary",
        "holdout": "holdout",
    },
    "independent_from_builder": ["verifier", "adversary", "holdout"],
    "automatic_fallback": False,
}
SENSITIVE_KEY_MARKERS = (
    "apikey",
    "token",
    "password",
    "secret",
    "credential",
    "connectionstring",
    "privatekey",
)
CREDENTIAL_VALUE_PATTERNS = (
    re.compile(r"(?i)\bAuthorization\s*:\s*(?:Basic|Bearer)\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"(?i)\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)-----BEGIN(?: [A-Z]+)* PRIVATE KEY-----"),
    re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@"),
    re.compile(
        r"(?i)\b(?:api[_ -]?key|token|oauth[_ -]?token|access[_ -]?token|client[_ -]?secret|password|secret|credential|connection[_ -]?string)"
        r"\s*[:=]\s*(?!\*+\b|\[?redacted\]?\b)\S{8,}"
    ),
)
GRAPH_BACKENDS = {"beads"}
GRAPH_MODES = {"plan", "apply"}
REASONING_EFFORTS = {"low", "medium", "high"}
ACCEPTANCE_TYPES = {"happy", "negative", "recovery", "boundary", "abuse"}
HIGH_RISK_TRIGGERS = {
    "authentication",
    "authorization",
    "authorisation",
    "tenant isolation",
    "personal data",
    "sensitive data",
    "regulated data",
    "regulatory data",
    "payments",
    "billing",
    "public tokens",
    "migrations",
    "secrets",
    "production deployment",
    "external communications",
    "publishing",
    "safeguarding",
    "oauth",
    "oidc",
    "sso",
    "patient records",
    "medical records",
    "clinical records",
    "health records",
    "phi",
    "pii",
    "identity tokens",
    "access tokens",
    "financial data",
    "financial records",
    "banking",
}


def _state_attestation(state: dict[str, Any]) -> str:
    payload = {key: value for key, value in state.items() if key != "state_attestation"}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hmac.new(_PROCESS_REVIEW_KEY, b"state\0" + raw, hashlib.sha256).hexdigest()


def _attest_state(state: dict[str, Any]) -> None:
    state["state_attestation"] = _state_attestation(state)


def _validate_state_attestation(state: Any) -> None:
    if not isinstance(state, dict):
        raise FactoryError("state attestation is missing or invalid; state must be an object")
    supplied = state.get("state_attestation")
    if not isinstance(supplied, str) or not hmac.compare_digest(supplied, _state_attestation(state)):
        raise FactoryError("state attestation is missing or invalid; use factory transitions or re-arm after an audited restart")

SLICE_STATES = {
    "pending",
    "active",
    "review",
    "review_passed",
    "completed",
    "blocked",
    "replan_required",
}
MILESTONE_STATES = {
    "pending",
    "active",
    "validating",
    "completed",
    "blocked",
    "replan_required",
}
TERMINAL_SLICE_STATES = {"completed", "blocked", "replan_required"}
CARD_SECTIONS = (
    ("Factory-Milestone", ("Factory-Milestone",)),
    ("Factory-Slice", ("Factory-Slice",)),
    ("Outcome", ("Outcome",)),
    ("Boundaries", ("Boundaries",)),
    ("Acceptance", ("Acceptance",)),
    ("Evidence", ("Evidence",)),
    ("Forbidden", ("Forbidden",)),
    ("Handoff", ("Handoff",)),
    ("Stop/escalate", ("Stop/escalate", "Stop / escalate", "Stop", "Escalation")),
)
_LEGACY_MICRO_WORK = re.compile(
    r"^(?:"
    r"(?:fix|repair|remediate|resolve|correct|patch) "
    r"(?:the |a |one |single |a single )?(?:failing )?(?:unit )?"
    r"(?:test|lint(?: check)?|typecheck|type check|snapshot|typo(?: fix)?)\b|"
    r"add (?:one|a|single|a single) (?:unit )?"
    r"(?:test|lint(?: check)?|typecheck|type check|snapshot|typo(?: fix)?)\b|"
    r"(?:update|refresh|approve) (?:the |a |one |single |a single )?"
    r"(?:test|lint(?: check)?|typecheck|type check|snapshot|typo(?: fix)?)\b|"
    r"(?:run|retry) (?:the |a (?:single )?|one |single )?"
    r"(?:test|lint(?: check)?|typecheck|type check|snapshot)\b|"
    r"review (?:the )?review\b|tweak\b|small fix\b|minor fix\b"
    r")",
    re.IGNORECASE,
)
_ISOLATED_EDIT_VERB = re.compile(
    r"\b(?:chang(?:e|es|ed|ing)|edit(?:s|ed|ing)?|"
    r"modif(?:y|ies|ied|ying)|mak(?:e|es|ing)|made|"
    r"alter(?:s|ed|ing)?|swap(?:s|ped|ping)?|set(?:s|ting)?|"
    r"replac(?:e|es|ed|ing)|renam(?:e|es|ed|ing)|"
    r"updat(?:e|es|ed|ing)|"
    r"tweak(?:s|ed|ing)?|adjust(?:s|ed|ing)?)\b",
    re.IGNORECASE,
)
_SINGLE_EDIT_SIGNAL = re.compile(
    r"\b(?:only|just|one|single|isolated)\b", re.IGNORECASE
)
_MICRO_EDIT_OBJECT = re.compile(
    r"\b(?:css(?:\s+(?:colou?r|rule|property|class|style))?|"
    r"header\s+(?:colou?r|label|string|text|typo|css)|colou?r|label|string|"
    r"typo|snapshot|assertion|"
    r"(?:readme|docs?|documentation)\s+sentence|local\s+variable|"
    r"source[- ]code\s+comment|comment|"
    r"cosmetic(?:\s+(?:change|edit|detail|style))?|"
    r"(?:(?:unit|integration|browser|snapshot)\s+)?test)\b",
    re.IGNORECASE,
)
_FUNCTIONAL_USER_CAPABILITY = re.compile(
    r"(?:"
    r"\b(?:users?|editors?|admins?|customers?|readers?|operators?|visitors?|members?|people)\b"
    r"\s+(?:can|may|must|successfully|are\s+able\s+to)\s+"
    r"(?:\w+[ -]?){0,5}(?:chang(?:e|es)|edit|modify|make|adjust|alter|swap|set|"
    r"replace|rename|tweak|select|choose|test|preview)\b|"
    r"\b(?:allow|enable|let)s?\s+"
    r"(?:a\s+|an\s+|the\s+)?(?:user|editor|admin|customer|reader|operator|visitor|member|people)"
    r"s?\s+to\b|"
    r"\b(?:a\s+|an\s+|the\s+)?"
    r"(?:user|editor|admin|customer|reader|operator|visitor|member)s?\b"
    r"\s+(?:successfully\s+)?(?:changes?|edits?|modifies|makes?|tweaks?|adjusts?|"
    r"alters?|swaps?|sets?|replaces?|renames?)\b"
    r"[^.!?]{0,100}\b(?:and|then|while|after|before|without)\b|"
    r"\b(?:changing|tweaking|adjusting|altering|swapping|setting|replacing|renaming)\b"
    r"[^.!?]{0,60}\b(?:updates?|persists?|preserves?|shows?|applies|rejects?|denies?)\b"
    r")",
    re.IGNORECASE,
)


def is_semantic_micro_work(value: Any) -> bool:
    """Identify isolated edit chores without rejecting user-facing capabilities."""

    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return False
    if _LEGACY_MICRO_WORK.search(text):
        return True
    if not (
        _SINGLE_EDIT_SIGNAL.search(text)
        and _ISOLATED_EDIT_VERB.search(text)
        and _MICRO_EDIT_OBJECT.search(text)
    ):
        return False
    return _FUNCTIONAL_USER_CAPABILITY.search(text) is None


class _MicroWorkClassifier:
    """Regex-compatible adapter used by intake and durable-work validators."""

    @staticmethod
    def search(value: Any) -> re.Match[str] | None:
        if not is_semantic_micro_work(value):
            return None
        return re.search(r"\S", str(value or ""))


# Public compatibility name imported by plugin.intake. Its search method now
# delegates to the shared semantic classifier rather than a prefix-only regex.
MICRO_TITLE = _MicroWorkClassifier()
RISK_RANK = {"R1": 1, "R2": 2, "R3": 3, "R4": 4}
HIGH_RISK_SURFACE = re.compile(
    r"\b(rls|row[- ]level|auth(?:entication|orisation|orization)?|permission|"
    r"tenant|migration|public token|personal data|sensitive data|regulat(?:ed|ory)|"
    r"high[- ]risk|child|custody|safeguard|external communication|"
    r"payment|billing|secret|webhook|publish|deployment|production|"
    r"oauth2?|openid[ -]connect|oidc|single[ -]sign[ -]on|sso|"
    r"health(?:care)?[ -](?:data|records?)|medical[ -](?:data|records?)|"
    r"clinical[ -](?:data|records?)|protected[ -]health[ -]information|phi|pii|"
    r"personally[ -]identifiable[ -]information|identity[ -]tokens?|id[ -]tokens?|"
    r"access[ -]tokens?|refresh[ -]tokens?|financial[ -](?:data|records?|accounts?|transactions?)|"
    r"banking|bank[ -](?:accounts?|balances?|transactions?)|cardholder[ -]data|"
    r"credit[ -]cards?|debit[ -]cards?|"
    r"account[ -](?:data|records?|numbers?|balances?|transactions?)|routing[ -]numbers?|"
    r"investment[ -]portfolios?|financial[ -]portfolios?)\b",
    re.IGNORECASE,
)
HEALTHCARE_CONTEXT = re.compile(
    r"\bpatients?\b.{0,120}\b(?:medical[ -]appointments?|clinicians?|physicians?|"
    r"doctors?|nurses?|dosages?|dose[ -]instructions?|medications?|prescriptions?|"
    r"diagnos(?:is|es|tic)|treatments?|health(?:care)?|clinical[ -]care|medical[ -]care|"
    r"records?|phi|protected[ -]health[ -]information)\b|"
    r"\b(?:medical[ -]appointments?|clinicians?|physicians?|doctors?|nurses?|dosages?|"
    r"dose[ -]instructions?|medications?|prescriptions?|diagnos(?:is|es|tic)|treatments?|"
    r"health(?:care)?|clinical[ -]care|medical[ -]care|records?|phi|"
    r"protected[ -]health[ -]information)\b.{0,120}\bpatients?\b|"
    r"\b(?:medical[ -]appointments?|dosages?|dose[ -]instructions?|prescriptions?)\b",
    re.IGNORECASE,
)
IDENTITY_CONTEXT = re.compile(
    r"\b(?:users?|customers?|members?|people)\b.{0,100}\b(?:sign[ -]in|log[ -]in|"
    r"login|passwords?|reset(?:ting)?[ -](?:access|passwords?))\b|"
    r"\b(?:sign[ -]in|log[ -]in|login|passwords?|reset(?:ting)?[ -](?:access|passwords?))\b"
    r".{0,100}\b(?:users?|customers?|members?|people)\b|"
    r"\b(?:sign[ -]in|log[ -]in|login)\b.{0,80}\b(?:passwords?|reset(?:ting)?[ -]access)\b|"
    r"\b(?:passwords?|reset(?:ting)?[ -]access)\b.{0,80}\b(?:sign[ -]in|log[ -]in|login)\b",
    re.IGNORECASE,
)
FINANCIAL_CONTEXT = re.compile(
    r"\b(?:customers?|users?|clients?|account[ -]holders?)\b.{0,120}\b(?:"
    r"bank[ -]balances?|transferring[ -](?:money|funds)|transfer[ -](?:money|funds)|"
    r"money[ -]transfers?|trading)\b|"
    r"\bbank[ -]balances?\b.{0,100}\b(?:transfers?|money|funds)\b|"
    r"\b(?:transfer(?:ring)?[ -](?:money|funds)|money[ -]transfers?)\b|"
    r"\binvestments?\b.{0,100}\b(?:financ(?:e|ial)|investors?|portfolios?|capital|trading)\b|"
    r"\b(?:financ(?:e|ial)|investors?|portfolios?|capital|trading)\b.{0,100}\binvestments?\b",
    re.IGNORECASE,
)


def _contains_high_risk_surface(value: str) -> bool:
    return bool(
        HIGH_RISK_SURFACE.search(value)
        or HEALTHCARE_CONTEXT.search(value)
        or IDENTITY_CONTEXT.search(value)
        or FINANCIAL_CONTEXT.search(value)
    )


class FactoryError(ValueError):
    """A deterministic factory-contract violation."""


@contextmanager
def cli_attestation_key_context(key: bytes):
    """Temporarily use an explicit high-entropy key for one CLI operation.

    The default remains process-local and is never persisted here. The lock is
    held for the whole context so concurrent or nested callers cannot observe a
    partially restored signer.
    """

    if not isinstance(key, bytes):
        raise TypeError("CLI attestation key must be bytes")
    if len(key) < MIN_ATTESTATION_KEY_BYTES:
        raise FactoryError(
            f"CLI attestation key must contain at least {MIN_ATTESTATION_KEY_BYTES} bytes"
        )
    frequencies = {value: key.count(value) for value in set(key)}
    entropy = -sum(
        (count / len(key)) * math.log2(count / len(key))
        for count in frequencies.values()
    )
    if entropy < MIN_ATTESTATION_KEY_ENTROPY:
        raise FactoryError("CLI attestation key does not contain enough entropy")

    global _PROCESS_REVIEW_KEY
    with _PROCESS_KEY_OVERRIDE_LOCK:
        previous = _PROCESS_REVIEW_KEY
        _PROCESS_REVIEW_KEY = bytes(key)
        try:
            yield
        finally:
            _PROCESS_REVIEW_KEY = previous


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FactoryError(f"missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise FactoryError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise FactoryError(f"expected a JSON object in {path}")
    return data


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def _ids(items: Iterable[dict[str, Any]]) -> list[str]:
    return [str(item.get("id", "")).strip() for item in items]


def _acceptance_ids(entity: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for item in entity.get("acceptance", []):
        if isinstance(item, dict):
            value = str(item.get("id", "")).strip()
        else:
            value = str(item).strip()
        if value:
            result.add(value)
    return result


def _acceptance_id_list(entity: dict[str, Any]) -> list[str]:
    rows = entity.get("acceptance", [])
    if not isinstance(rows, list):
        return []
    return [
        str(item.get("id", "")).strip() if isinstance(item, dict) else str(item).strip()
        for item in rows
    ]


def _word_count(value: Any) -> int:
    return len(re.findall(r"\b[\w'-]+\b", str(value or "")))


THREAT_TEXT_MINIMUM_WORDS = {
    "name": 2,
    "scenario": 6,
    "attack_surface": 2,
    "expected_control": 6,
}
THREAT_CONTRACT_FIELDS = (
    "id",
    "name",
    "scenario",
    "attack_surface",
    "expected_control",
)


def threat_contract_is_substantive(value: Any) -> bool:
    """Require an actionable adversarial case, not placeholder labels."""

    if not isinstance(value, dict) or set(value) != set(THREAT_CONTRACT_FIELDS):
        return False
    return all(
        isinstance(value.get(field), str)
        and value[field] == value[field].strip()
        and _word_count(value[field]) >= minimum
        for field, minimum in THREAT_TEXT_MINIMUM_WORDS.items()
    )


def threat_semantic_identity(value: Any) -> tuple[str, str, str, str]:
    """Return normalized adversarial substance while excluding the mutable id."""

    def normalized(field: str) -> str:
        raw = str(value.get(field, "")) if isinstance(value, dict) else ""
        return re.sub(r"[^a-z0-9]+", " ", raw.casefold()).strip()

    return tuple(
        normalized(field)
        for field in ("name", "scenario", "attack_surface", "expected_control")
    )


DECISION_PLACEHOLDERS = frozenset({
    "x", "xx", "xxx", "tbd", "todo", "unknown", "placeholder", "pending",
    "n/a", "na", "none", "undecided", "later", "fixme", "?",
})


def decision_text_is_substantive(value: Any) -> bool:
    """Require a usable authority/product choice rather than a form placeholder."""

    if not isinstance(value, str) or value != value.strip():
        return False
    normalized = re.sub(r"\s+", " ", value.strip()).casefold()
    return (
        normalized not in DECISION_PLACEHOLDERS
        and not re.search(
            r"\b(?:tbd|todo|placeholder|undecided|fixme|unknown|pending)\b|"
            r"\bto be (?:determined|decided|filled)\b",
            normalized,
        )
        and len(normalized) >= 20
        and _word_count(normalized) >= 4
    )


def _iter_text(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_text(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _iter_text(item)
    elif isinstance(value, str) and value.strip():
        yield value.strip()


def derive_independent_mission_risk(
    security: Any, global_context: Any = None
) -> str:
    """Derive the risk floor without trusting slice-local declarations."""

    security_obj = security if isinstance(security, dict) else {}
    classification = str(security_obj.get("data_classification") or "").strip().lower()
    if classification in {"regulatory", "regulation"}:
        classification = "regulated"

    rank = RISK_RANK["R1"]
    if classification == "internal":
        rank = max(rank, RISK_RANK["R2"])
    elif classification in {"personal", "sensitive", "regulated"}:
        rank = max(rank, RISK_RANK["R3"])

    raw_triggers = security_obj.get("risk_triggers")
    triggers = [
        str(item).strip().lower()
        for item in raw_triggers
        if isinstance(item, str) and item.strip()
    ] if isinstance(raw_triggers, list) else []
    if triggers:
        rank = max(rank, RISK_RANK["R2"])
    if any(trigger in HIGH_RISK_TRIGGERS or _contains_high_risk_surface(trigger) for trigger in triggers):
        rank = max(rank, RISK_RANK["R3"])

    # Security controls and mission-wide product/testing/decision text are an
    # independent signal. Slice risks and paths are deliberately not accepted
    # here because they are the declarations this floor must constrain.
    global_text = " ".join(_iter_text({"security": security_obj, "context": global_context}))
    if _contains_high_risk_surface(global_text):
        rank = max(rank, RISK_RANK["R3"])
    return max(RISK_RANK, key=lambda value: (RISK_RANK[value] <= rank, RISK_RANK[value]))


def _is_canonical_absolute_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    path = Path(value).expanduser()
    if not path.is_absolute():
        return False
    try:
        return str(path.resolve()) == value
    except (OSError, RuntimeError, ValueError):
        return False


def _canonical_ownership_pattern(
    value: Any,
    workspace_path: str | Path,
    *,
    relative_only: bool = False,
) -> tuple[str, ...] | None:
    """Return a lexical absolute ownership pattern rooted at the workspace.

    Ownership paths may contain globs, so resolving the complete value through
    the filesystem would be incorrect. Instead, validate every lexical segment
    and prepend the canonical workspace segments to relative coordinates.
    """

    if not isinstance(value, str) or not value or value != value.strip():
        return None
    if (
        "\\" in value
        or "\x00" in value
        or value.count("[") != value.count("]")
        or value.count("{") != value.count("}")
    ):
        return None
    is_absolute = value.startswith("/") or bool(re.match(r"^[A-Za-z]:/", value))
    if relative_only and is_absolute:
        return None
    raw_parts = value.split("/")
    if is_absolute and raw_parts and raw_parts[0] == "":
        raw_parts = raw_parts[1:]
    if not raw_parts or any(part in {"", ".", ".."} for part in raw_parts):
        return None

    # A bare prose token is not an ownership coordinate. Real root-level file
    # names and glob patterns remain valid (README.md, *.toml, .github/**).
    coordinate_shaped = (
        len(raw_parts) > 1
        or any(char in value for char in "*?[]{}")
        or "." in raw_parts[-1]
    )
    if not coordinate_shaped:
        return None

    workspace = Path(workspace_path).expanduser()
    if not workspace.is_absolute():
        return None
    try:
        workspace_parts = tuple(workspace.resolve().parts)
    except (OSError, RuntimeError, ValueError):
        return None
    if is_absolute:
        # pathlib gives drive/root-aware canonical segments for absolute runtime
        # contracts while preserving glob characters verbatim.
        return tuple(Path(value).parts)
    return workspace_parts + tuple(raw_parts)


def _is_workspace_relative_coordinate(value: Any, workspace_path: str | Path) -> bool:
    return _canonical_ownership_pattern(
        value, workspace_path, relative_only=True
    ) is not None


def _credential_shaped_paths(value: Any, path: str = "manifest") -> list[str]:
    """Return only unsafe locations; credential values are never formatted."""

    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            canonical = re.sub(r"[^a-z0-9]", "", key_text.lower())
            child_path = f"{path}.{key_text}"
            if any(marker in canonical for marker in SENSITIVE_KEY_MARKERS):
                found.append(child_path)
                continue
            found.extend(_credential_shaped_paths(item, child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_credential_shaped_paths(item, f"{path}[{index}]"))
    elif isinstance(value, str) and any(pattern.search(value) for pattern in CREDENTIAL_VALUE_PATTERNS):
        found.append(path)
    return found


def _check_exact_fields(
    value: Any,
    path: str,
    required: frozenset[str],
    errors: list[str],
    *,
    allowed: frozenset[str] | None = None,
) -> None:
    """Reject both schema projection and structurally incomplete v2 objects."""

    if not isinstance(value, dict):
        return
    fields = set(value)
    permitted = allowed if allowed is not None else required
    if not required.issubset(fields) or not fields.issubset(permitted):
        # Do not include attacker-controlled key names or values in errors.
        errors.append(f"{path} has unexpected or omitted fields")


def _check_string_list_items(value: Any, path: str, errors: list[str]) -> None:
    """Validate leaf arrays without accepting object-shaped projected contracts."""

    if not isinstance(value, list):
        return
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip() or item != item.strip():
            errors.append(f"{path}[{index}] must be a canonical non-empty string")


def _validate_manifest_structure(manifest: dict[str, Any], errors: list[str]) -> None:
    """Apply schema-v2 key contracts recursively before semantic validation."""

    _check_exact_fields(
        manifest,
        "manifest",
        MANIFEST_FIELDS,
        errors,
        allowed=MANIFEST_OPTIONAL_FIELDS,
    )
    mission = manifest.get("mission")
    _check_exact_fields(
        mission,
        "mission",
        MISSION_REQUIRED_FIELDS,
        errors,
        allowed=MISSION_FIELDS,
    )
    if isinstance(mission, dict):
        for field in ("out_of_scope", "constraints", "success_metrics", "surfaces"):
            _check_string_list_items(mission.get(field), f"mission.{field}", errors)
        personas = mission.get("personas")
        if isinstance(personas, list):
            for index, persona in enumerate(personas):
                _check_exact_fields(
                    persona, f"mission.personas[{index}]", PERSONA_FIELDS, errors
                )
        stories = mission.get("user_stories")
        if isinstance(stories, list):
            for index, story in enumerate(stories):
                path = f"mission.user_stories[{index}]"
                _check_exact_fields(story, path, STORY_FIELDS, errors)
                if not isinstance(story, dict):
                    continue
                _check_string_list_items(story.get("paths"), f"{path}.paths", errors)
                acceptance = story.get("acceptance")
                if isinstance(acceptance, list):
                    for criterion_index, criterion in enumerate(acceptance):
                        _check_exact_fields(
                            criterion,
                            f"{path}.acceptance[{criterion_index}]",
                            ACCEPTANCE_FIELDS,
                            errors,
                        )

    decisions = manifest.get("decisions")
    if isinstance(decisions, list):
        for index, decision in enumerate(decisions):
            _check_exact_fields(decision, f"decisions[{index}]", DECISION_FIELDS, errors)

    milestones = manifest.get("milestones")
    if isinstance(milestones, list):
        for index, milestone in enumerate(milestones):
            path = f"milestones[{index}]"
            _check_exact_fields(milestone, path, MILESTONE_FIELDS, errors)
            if not isinstance(milestone, dict):
                continue
            for field in ("depends_on", "slices", "story_ids"):
                _check_string_list_items(milestone.get(field), f"{path}.{field}", errors)
            acceptance = milestone.get("acceptance")
            if isinstance(acceptance, list):
                for criterion_index, criterion in enumerate(acceptance):
                    _check_exact_fields(
                        criterion,
                        f"{path}.acceptance[{criterion_index}]",
                        ACCEPTANCE_FIELDS,
                        errors,
                    )

    slices = manifest.get("slices")
    if isinstance(slices, list):
        for index, slice_spec in enumerate(slices):
            path = f"slices[{index}]"
            _check_exact_fields(slice_spec, path, SLICE_FIELDS, errors)
            if not isinstance(slice_spec, dict):
                continue
            for field in (
                "risk_triggers", "requires_decisions", "depends_on", "paths",
                "evidence", "review_roles",
            ):
                _check_string_list_items(slice_spec.get(field), f"{path}.{field}", errors)
            acceptance = slice_spec.get("acceptance")
            if isinstance(acceptance, list):
                for criterion_index, criterion in enumerate(acceptance):
                    _check_exact_fields(
                        criterion,
                        f"{path}.acceptance[{criterion_index}]",
                        ACCEPTANCE_FIELDS,
                        errors,
                    )

    _check_exact_fields(manifest.get("policy"), "policy", POLICY_FIELDS, errors)

    models = manifest.get("models")
    _check_exact_fields(models, "models", frozenset(MODEL_ROLES), errors)
    if isinstance(models, dict):
        for role in MODEL_ROLES:
            _check_exact_fields(
                models.get(role), f"models.{role}", MODEL_REFERENCE_FIELDS, errors
            )

    system_prompts = manifest.get("system_prompts")
    if system_prompts is not None:
        _check_exact_fields(system_prompts, "system_prompts", SYSTEM_PROMPT_FIELDS, errors)
        if isinstance(system_prompts, dict):
            for role in MODEL_ROLES:
                if not isinstance(system_prompts.get(role), str):
                    errors.append(f"system_prompts.{role} must be a string")
                elif len(system_prompts[role]) > 16000:
                    errors.append(f"system_prompts.{role} must be at most 16000 characters")

    model_policy = manifest.get("model_policy")
    _check_exact_fields(model_policy, "model_policy", MODEL_POLICY_FIELDS, errors)
    if isinstance(model_policy, dict):
        _check_exact_fields(
            model_policy.get("roles"),
            "model_policy.roles",
            frozenset(MODEL_ROLES),
            errors,
        )
        _check_string_list_items(
            model_policy.get("independent_from_builder"),
            "model_policy.independent_from_builder",
            errors,
        )

    execution = manifest.get("execution")
    _check_exact_fields(execution, "execution", EXECUTION_FIELDS, errors)
    if isinstance(execution, dict):
        _check_exact_fields(
            execution.get("reasoning_effort"),
            "execution.reasoning_effort",
            REASONING_EFFORT_FIELDS,
            errors,
        )

    testing = manifest.get("testing")
    _check_exact_fields(testing, "testing", TESTING_FIELDS, errors)
    if isinstance(testing, dict):
        for field in (
            "focused_commands", "integration_commands", "evidence_requirements",
        ):
            _check_string_list_items(testing.get(field), f"testing.{field}", errors)
        browser_scenarios = testing.get("browser_scenarios")
        if isinstance(browser_scenarios, list):
            for index, scenario in enumerate(browser_scenarios):
                _check_exact_fields(
                    scenario,
                    f"testing.browser_scenarios[{index}]",
                    BROWSER_SCENARIO_REQUIRED_FIELDS,
                    errors,
                    allowed=BROWSER_SCENARIO_FIELDS,
                )
        held_out_scenarios = testing.get("held_out_scenarios")
        if isinstance(held_out_scenarios, list):
            for index, scenario in enumerate(held_out_scenarios):
                _check_exact_fields(
                    scenario,
                    f"testing.held_out_scenarios[{index}]",
                    HELD_OUT_SCENARIO_FIELDS,
                    errors,
                )

    security = manifest.get("security")
    _check_exact_fields(
        security,
        "security",
        SECURITY_REQUIRED_FIELDS,
        errors,
        allowed=SECURITY_FIELDS,
    )
    if isinstance(security, dict):
        for field in ("risk_triggers", "data", "controls", "human_gates"):
            _check_string_list_items(security.get(field), f"security.{field}", errors)
        threats = security.get("threat_scenarios")
        if isinstance(threats, list):
            for index, threat in enumerate(threats):
                _check_exact_fields(
                    threat,
                    f"security.threat_scenarios[{index}]",
                    frozenset(THREAT_CONTRACT_FIELDS),
                    errors,
                )
        authority_decisions = security.get("authority_decisions")
        if isinstance(authority_decisions, list):
            for index, decision in enumerate(authority_decisions):
                _check_exact_fields(
                    decision,
                    f"security.authority_decisions[{index}]",
                    DECISION_FIELDS,
                    errors,
                    allowed=SECURITY_DECISION_FIELDS,
                )

    _check_exact_fields(
        manifest.get("intake"), "intake", INTAKE_PROVENANCE_FIELDS, errors
    )


def _acceptance_rows(
    entity: dict[str, Any], label: str, errors: list[str]
) -> list[dict[str, str]]:
    """Validate and return canonical, ordered, fully typed criteria."""

    rows = entity.get("acceptance")
    if not isinstance(rows, list) or not rows:
        errors.append(f"{label}: at least one acceptance criterion is required")
        return []
    ids: list[str] = []
    canonical: list[dict[str, str]] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            errors.append(f"{label}: acceptance criterion {index} must be an object")
            continue
        criterion_id = str(row.get("id", "")).strip()
        raw_type = row.get("type")
        criterion_type = str(raw_type or "").strip().lower()
        statement = str(row.get("statement", "")).strip()
        ids.append(criterion_id)
        canonical.append({"id": criterion_id, "type": criterion_type, "statement": statement})
        if set(row) != ACCEPTANCE_FIELDS:
            errors.append(
                f"{label}: acceptance criterion {criterion_id or index} must contain exactly id, type, and statement"
            )
        if raw_type != criterion_type or criterion_type not in ACCEPTANCE_TYPES:
            errors.append(
                f"{label}: acceptance criterion {criterion_id or index} type must be one of "
                + ", ".join(sorted(ACCEPTANCE_TYPES))
            )
        if _word_count(statement) < 5:
            errors.append(
                f"{label}: acceptance criterion {criterion_id or index} needs an observable statement of at least five words"
            )
    if len(ids) != len(rows) or any(not value for value in ids) or len(set(ids)) != len(ids):
        errors.append(f"{label}: acceptance criterion ids must be non-empty and unique")
    return canonical


def _find_dependency_cycle(items: Iterable[dict[str, Any]]) -> list[str]:
    graph: dict[str, list[str]] = {}
    for item in items:
        node = str(item.get("id", "")).strip()
        if not node:
            continue
        dependencies = item.get("depends_on", [])
        graph[node] = [str(value) for value in dependencies] if isinstance(dependencies, list) else []
    active: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def visit(node: str) -> list[str]:
        if node in active:
            start = stack.index(node)
            return stack[start:] + [node]
        if node in visited:
            return []
        active.add(node)
        stack.append(node)
        for dependency in graph.get(node, []):
            if dependency in graph:
                cycle = visit(dependency)
                if cycle:
                    return cycle
        stack.pop()
        active.remove(node)
        visited.add(node)
        return []

    for node in graph:
        cycle = visit(node)
        if cycle:
            return cycle
    return []


def _normalise_failure(text: str) -> str:
    value = text.lower().strip()
    value = re.sub(r"0x[0-9a-f]+", "<hex>", value)
    value = re.sub(r"\b\d{4}-\d{2}-\d{2}t[^\s]+", "<timestamp>", value)
    value = re.sub(r"\b\d+\.\d+\b", "<number>", value)
    value = re.sub(r"\b\d+\b", "<number>", value)
    value = re.sub(r"\s+", " ", value)
    return value[:2000]


def failure_fingerprint(text: str) -> str:
    return hashlib.sha256(_normalise_failure(text).encode("utf-8")).hexdigest()[:16]


def load_manifest(path: str | Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    return _read_json(Path(path).expanduser().resolve())


def validate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(manifest, dict):
        return {
            "valid": False,
            "errors": ["manifest must be an object"],
            "warnings": warnings,
        }

    _validate_manifest_structure(manifest, errors)
    credential_paths = _credential_shaped_paths(manifest)
    if credential_paths:
        errors.append(
            f"manifest contains {len(credential_paths)} credential-shaped field(s); store only provider/model references"
        )

    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")

    mission = manifest.get("mission")
    persona_set: set[str] = set()
    story_set: set[str] = set()
    story_acceptance: dict[str, list[dict[str, str]]] = {}
    if not isinstance(mission, dict):
        errors.append("mission must be an object")
    else:
        if not str(mission.get("id", "")).strip():
            errors.append("mission.id is required")
        if not str(mission.get("name", "")).strip():
            errors.append("mission.name is required")
        if len(str(mission.get("problem", "")).split()) < 8:
            errors.append("mission.problem must explain the user/business problem")
        mission_outcome = str(mission.get("outcome", "")).strip()
        if len(mission_outcome.split()) < 5:
            errors.append("mission.outcome must describe an observable product/system result")
        if MICRO_TITLE.search(mission_outcome):
            errors.append(
                "mission.outcome looks like a micro-remediation; require a durable product/system result"
            )
        if len(str(mission.get("context", "")).split()) < 8:
            errors.append("mission.context must provide enough product/domain direction")
        if mission.get("project_mode") not in {"existing", "greenfield"}:
            errors.append("mission.project_mode must be existing or greenfield")
        if not _is_canonical_absolute_path(mission.get("workspace_path")):
            errors.append("mission.workspace_path must be a canonical absolute resolved path")
        if not isinstance(mission.get("out_of_scope"), list) or not mission.get("out_of_scope"):
            errors.append("mission.out_of_scope must name at least one non-goal")
        if not isinstance(mission.get("constraints"), list) or not mission.get("constraints"):
            errors.append("mission.constraints must name at least one constraint")
        if not isinstance(mission.get("success_metrics"), list) or not mission.get("success_metrics"):
            errors.append("mission.success_metrics must name at least one measurable signal")
        if not isinstance(mission.get("surfaces"), list) or not mission.get("surfaces"):
            errors.append("mission.surfaces must name at least one interaction surface")

        personas = mission.get("personas")
        if not isinstance(personas, list) or not personas:
            errors.append("mission.personas must define at least one target user")
        else:
            persona_ids = [str(item.get("id", "")).strip() for item in personas if isinstance(item, dict)]
            persona_set = {item for item in persona_ids if item}
            if len(persona_ids) != len(personas) or len(persona_set) != len(personas):
                errors.append("mission.personas require unique non-empty ids")
            for persona in personas:
                if not isinstance(persona, dict):
                    continue
                pid = str(persona.get("id", "")).strip() or "<unknown>"
                if not str(persona.get("name", "")).strip() or len(str(persona.get("context", "")).split()) < 4 or len(str(persona.get("need", "")).split()) < 4:
                    errors.append(f"{pid}: persona requires name, context, and need")

        stories = mission.get("user_stories")
        if not isinstance(stories, list) or not stories:
            errors.append("mission.user_stories must define at least one structured story")
        else:
            story_ids = [str(item.get("id", "")).strip() for item in stories if isinstance(item, dict)]
            story_set = {item for item in story_ids if item}
            if len(story_ids) != len(stories) or len(story_set) != len(stories):
                errors.append("mission.user_stories require unique non-empty ids")
            for story in stories:
                if not isinstance(story, dict):
                    errors.append("every mission.user_story must be an object")
                    continue
                sid = str(story.get("id", "")).strip() or "<unknown>"
                if str(story.get("persona_id", "")).strip() not in persona_set:
                    errors.append(f"{sid}: persona_id must reference mission.personas")
                if len(str(story.get("want", "")).split()) < 4 or len(str(story.get("so_that", "")).split()) < 4:
                    errors.append(f"{sid}: story requires a concrete want and user value")
                acceptance_rows = _acceptance_rows(story, sid, errors)
                if len(acceptance_rows) < 2:
                    errors.append(f"{sid}: story requires at least two acceptance criteria")
                story_acceptance[sid] = acceptance_rows
                criterion_types = {criterion["type"] for criterion in acceptance_rows}
                if "happy" not in criterion_types:
                    errors.append(f"{sid}: story requires a positive/happy acceptance criterion")
                if not criterion_types.intersection({"negative", "recovery", "boundary", "abuse"}):
                    errors.append(f"{sid}: story requires negative, recovery, boundary, or abuse acceptance")

    milestones = manifest.get("milestones")
    slices = manifest.get("slices")
    if not isinstance(milestones, list) or not milestones:
        errors.append("milestones must be a non-empty list")
        milestones = []
    if not isinstance(slices, list) or not slices:
        errors.append("slices must be a non-empty list")
        slices = []

    milestone_ids = _ids([x for x in milestones if isinstance(x, dict)])
    slice_ids = _ids([x for x in slices if isinstance(x, dict)])
    for kind, values in (("milestone", milestone_ids), ("slice", slice_ids)):
        blanks = values.count("")
        if blanks:
            errors.append(f"{blanks} {kind}(s) have no id")
        duplicates = sorted({item for item in values if item and values.count(item) > 1})
        if duplicates:
            errors.append(f"duplicate {kind} ids: {', '.join(duplicates)}")

    milestone_set = set(milestone_ids)
    slice_set = set(slice_ids)

    decisions = manifest.get("decisions", [])
    if not isinstance(decisions, list) or not decisions:
        errors.append("decisions must contain at least one locked authority/product decision")
        decisions = []
    decision_ids = _ids([x for x in decisions if isinstance(x, dict)])
    decision_set = set(decision_ids)
    if len(decision_set) != len([item for item in decision_ids if item]):
        errors.append("decision ids must be non-empty and unique")
    for decision in decisions:
        if not isinstance(decision, dict):
            errors.append("every decision must be an object")
            continue
        did = str(decision.get("id", "")).strip() or "<unknown>"
        if decision.get("status") != "locked":
            errors.append(f"{did}: decision status must be locked before execution")
        if not decision_text_is_substantive(decision.get("statement")):
            errors.append(f"{did}: decision statement must be a substantive authority/product choice")

    milestone_story_ids: dict[str, set[str]] = {}
    for milestone in milestones:
        if not isinstance(milestone, dict):
            errors.append("every milestone must be an object")
            continue
        mid = str(milestone.get("id", "")).strip() or "<unknown>"
        owned_story_ids = milestone.get("story_ids")
        if not isinstance(owned_story_ids, list) or not owned_story_ids:
            errors.append(f"{mid}: story_ids must list at least one mission.user_story")
            milestone_story_ids[mid] = set()
        else:
            owned = [str(value).strip() for value in owned_story_ids]
            milestone_story_ids[mid] = {value for value in owned if value}
            if any(not value for value in owned) or len(set(owned)) != len(owned):
                errors.append(f"{mid}: story_ids must be non-empty and unique")
            unknown_stories = sorted(set(owned) - story_set)
            if unknown_stories:
                errors.append(f"{mid}: unknown story_ids: {', '.join(unknown_stories)}")
        milestone_outcome = str(milestone.get("outcome", "")).strip()
        if len(milestone_outcome.split()) < 5:
            errors.append(f"{mid}: outcome is too vague")
        if MICRO_TITLE.search(milestone_outcome):
            errors.append(
                f"{mid}: outcome looks like a micro-remediation; keep it inside a functional milestone"
            )
        milestone_acceptance = _acceptance_rows(milestone, mid, errors)
        for story_id in milestone_story_ids.get(mid, set()):
            for criterion in story_acceptance.get(story_id, []):
                if criterion not in milestone_acceptance:
                    errors.append(
                        f"{mid}: acceptance must include the exact id, type, and statement of "
                        f"story {story_id} criterion {criterion['id']}"
                    )
        listed_slices = milestone.get("slices", [])
        if not isinstance(listed_slices, list) or not listed_slices:
            errors.append(f"{mid}: slices must list at least one slice id")
        else:
            unknown = sorted(set(map(str, listed_slices)) - slice_set)
            if unknown:
                errors.append(f"{mid}: unknown slices: {', '.join(unknown)}")
        unknown_parents = sorted(set(map(str, milestone.get("depends_on", []))) - milestone_set)
        if unknown_parents:
            errors.append(f"{mid}: unknown milestone dependencies: {', '.join(unknown_parents)}")

    slice_to_milestone: dict[str, str] = {}
    for item in slices:
        if not isinstance(item, dict):
            errors.append("every slice must be an object")
            continue
        sid = str(item.get("id", "")).strip() or "<unknown>"
        mid = str(item.get("milestone_id", "")).strip()
        story_id = str(item.get("story_id", "")).strip()
        slice_to_milestone[sid] = mid
        if mid not in milestone_set:
            errors.append(f"{sid}: unknown milestone_id {mid!r}")
        if story_id not in story_set:
            errors.append(f"{sid}: story_id must reference mission.user_stories")
        elif story_id not in milestone_story_ids.get(mid, set()):
            errors.append(f"{sid}: story_id must be owned by milestone {mid or '<unknown>'}")
        outcome = str(item.get("outcome", "")).strip()
        if len(outcome.split()) < 5:
            errors.append(f"{sid}: outcome must be a coherent observable result, not an edit")
        if MICRO_TITLE.search(outcome):
            errors.append(f"{sid}: outcome looks like a micro-remediation; keep it inside its parent slice")

        risk = str(item.get("risk", "")).upper().strip()
        if risk not in RISK_RANK:
            errors.append(f"{sid}: risk must be one of R1, R2, R3, R4")
        surface_text = " ".join(
            [outcome]
            + [str(path) for path in item.get("paths", []) if isinstance(path, str)]
            + [str(trigger) for trigger in item.get("risk_triggers", []) if isinstance(trigger, str)]
        )
        if _contains_high_risk_surface(surface_text) and RISK_RANK.get(risk, 0) < RISK_RANK["R3"]:
            errors.append(f"{sid}: declared {risk or 'no risk'} conflicts with a high-risk surface; use R3/R4")
        if item.get("review_required") is not True:
            errors.append(f"{sid}: every slice requires independent review by verifier and adversary")
        review_roles = item.get("review_roles")
        if not isinstance(review_roles, list) or not {"verifier", "adversary"}.issubset(set(map(str, review_roles))):
            errors.append(f"{sid}: review_roles must include verifier and adversary")
        if RISK_RANK.get(risk, 0) >= RISK_RANK["R3"] and not item.get("risk_triggers"):
            errors.append(f"{sid}: R3/R4 slices must name risk_triggers")

        required_decisions = item.get("requires_decisions", [])
        if not isinstance(required_decisions, list):
            errors.append(f"{sid}: requires_decisions must be a list")
        else:
            unknown_decisions = sorted(set(map(str, required_decisions)) - decision_set)
            if unknown_decisions:
                errors.append(f"{sid}: unknown decisions: {', '.join(unknown_decisions)}")

        slice_acceptance = _acceptance_rows(item, sid, errors)
        linked_acceptance = story_acceptance.get(story_id, [])
        if linked_acceptance and slice_acceptance != linked_acceptance:
            errors.append(
                f"{sid}: acceptance must be the full exact ordered criterion contract of story {story_id}"
            )
        evidence = item.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"{sid}: at least one deterministic evidence command/scenario is required")
        paths = item.get("paths")
        workspace_path = manifest.get("mission", {}).get("workspace_path", "")
        if (
            not isinstance(paths, list)
            or not paths
            or any(
                not _is_workspace_relative_coordinate(path, workspace_path)
                for path in paths
            )
        ):
            errors.append(
                f"{sid}: paths must be a non-empty list of canonical workspace-relative path/glob coordinates"
            )
        unknown_parents = sorted(set(map(str, item.get("depends_on", []))) - slice_set)
        if unknown_parents:
            errors.append(f"{sid}: unknown slice dependencies: {', '.join(unknown_parents)}")
        if sid in set(map(str, item.get("depends_on", []))):
            errors.append(f"{sid}: slice cannot depend on itself")

    listed_slice_ids: list[str] = []
    for milestone in milestones:
        if not isinstance(milestone, dict):
            continue
        mid = str(milestone.get("id", ""))
        listed = [str(value) for value in milestone.get("slices", [])] if isinstance(milestone.get("slices", []), list) else []
        listed_slice_ids.extend(listed)
        for sid in listed:
            if slice_to_milestone.get(sid) != mid:
                errors.append(f"{mid}: slice {sid} points to milestone {slice_to_milestone.get(sid)!r}")
    if sorted(listed_slice_ids) != sorted(slice_ids):
        errors.append("every slice must be listed exactly once by its owning milestone")

    milestone_cycle = _find_dependency_cycle([item for item in milestones if isinstance(item, dict)])
    if milestone_cycle:
        errors.append("milestone dependency cycle: " + " -> ".join(milestone_cycle))
    slice_cycle = _find_dependency_cycle([item for item in slices if isinstance(item, dict)])
    if slice_cycle:
        errors.append("slice dependency cycle: " + " -> ".join(slice_cycle))

    policy = manifest.get("policy", {})
    if not isinstance(policy, dict):
        errors.append("policy must be an object")
    else:
        if set(policy) != POLICY_FIELDS:
            errors.append(
                "policy must contain exactly max_active_milestones, max_parallel_slices, "
                "repeated_failure_limit, and max_remediation_cycles"
            )
        policy_bounds = {
            "max_active_milestones": (1, 1),
            "max_parallel_slices": (1, 2),
            "repeated_failure_limit": (1, 2),
            "max_remediation_cycles": (1, 1),
        }
        for key, (minimum, maximum) in policy_bounds.items():
            value = policy.get(key)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not minimum <= value <= maximum
            ):
                errors.append(f"policy.{key} must be an integer between {minimum} and {maximum}")

    mapped_stories = [
        str(story_id)
        for milestone in milestones
        if isinstance(milestone, dict)
        for story_id in (
            milestone.get("story_ids", [])
            if isinstance(milestone.get("story_ids"), list)
            else []
        )
    ]
    if story_set and sorted(mapped_stories) != sorted(story_set):
        errors.append("every mission.user_story must map to exactly one milestone")

    models = manifest.get("models")
    model_refs: dict[str, tuple[str, str]] = {}
    if not isinstance(models, dict):
        errors.append("models must assign every factory role")
    else:
        if set(models) != set(MODEL_ROLES):
            errors.append(
                "models must contain exactly integrator, builder, verifier, adversary, and holdout"
            )
        for role in MODEL_ROLES:
            value = models.get(role)
            if not isinstance(value, dict):
                errors.append(f"models.{role} requires provider and model")
                model_refs[role] = ("", "")
                continue
            if set(value) != MODEL_REFERENCE_FIELDS:
                errors.append(f"models.{role} must contain exactly provider and model")
            raw_provider = value.get("provider", "")
            raw_model = value.get("model", "")
            ref = (str(raw_provider).strip().lower(), str(raw_model).strip())
            model_refs[role] = ref
            if (
                not isinstance(raw_provider, str)
                or not isinstance(raw_model, str)
                or not all(ref)
            ):
                errors.append(f"models.{role} requires provider and model")
            if raw_provider != ref[0] or raw_model != ref[1]:
                errors.append(f"models.{role} provider/model references must be canonical")
        builder_ref = model_refs.get("builder", ("", ""))
        for role in ("verifier", "adversary", "holdout"):
            if builder_ref == model_refs.get(role):
                errors.append(f"models.{role} must differ from models.builder")

    model_policy = manifest.get("model_policy")
    if model_policy != CANONICAL_MODEL_POLICY:
        errors.append("model_policy must exactly match the canonical sol-luna execution policy")

    execution = manifest.get("execution")
    if not isinstance(execution, dict):
        errors.append("execution must define canonical graph and reasoning settings")
    else:
        if set(execution) != EXECUTION_FIELDS:
            errors.append(
                "execution must contain exactly graph_backend, graph_mode, beads_directory, "
                "beads_isolated_authorized, and reasoning_effort"
            )
        if execution.get("graph_backend") not in GRAPH_BACKENDS:
            errors.append("execution.graph_backend must be beads")
        if execution.get("graph_mode") not in GRAPH_MODES:
            errors.append("execution.graph_mode must be plan or apply")
        beads_directory = execution.get("beads_directory")
        if not isinstance(beads_directory, str) or (
            beads_directory != "" and not _is_canonical_absolute_path(beads_directory)
        ):
            errors.append("execution.beads_directory must be empty or a canonical absolute resolved path")
        if not isinstance(execution.get("beads_isolated_authorized"), bool):
            errors.append("execution.beads_isolated_authorized must be a boolean")
        reasoning = execution.get("reasoning_effort")
        if not isinstance(reasoning, dict):
            errors.append("execution.reasoning_effort must define orchestrator and worker")
        else:
            if set(reasoning) != REASONING_EFFORT_FIELDS:
                errors.append("execution.reasoning_effort must contain exactly orchestrator and worker")
            for role in ("orchestrator", "worker"):
                if reasoning.get(role) not in REASONING_EFFORTS:
                    errors.append(f"execution.reasoning_effort.{role} must be low, medium, or high")

    testing = manifest.get("testing")
    if not isinstance(testing, dict):
        errors.append("testing must define forced factory gates")
    else:
        if not testing.get("focused_commands"):
            errors.append("testing.focused_commands requires at least one command")
        if not testing.get("integration_commands"):
            errors.append("testing.integration_commands requires at least one command")
        scenarios = testing.get("held_out_scenarios")
        if not isinstance(scenarios, list) or not scenarios:
            errors.append("testing.held_out_scenarios requires at least one scenario")
        else:
            for index, scenario in enumerate(scenarios, start=1):
                if not isinstance(scenario, dict) or any(
                    not str(scenario.get(key, "")).strip() for key in ("name", "given", "when", "then")
                ):
                    errors.append(f"held-out scenario {index} requires name, given, when, and then")
        browser_scenarios = testing.get("browser_scenarios")
        if not isinstance(browser_scenarios, list) or not browser_scenarios:
            errors.append("testing.browser_scenarios requires at least one real interaction scenario")
        else:
            for index, scenario in enumerate(browser_scenarios, start=1):
                if not isinstance(scenario, dict) or any(
                    not str(scenario.get(key, "")).strip() for key in ("action", "expected")
                ):
                    errors.append(f"interaction scenario {index} requires action and expected post-state")

    security = manifest.get("security")
    if not isinstance(security, dict):
        errors.append("security must define classification and adversarial gates")
    else:
        if security.get("mandatory_adversarial_review") is not True:
            errors.append("security.mandatory_adversarial_review must be true")
        if str(security.get("adversarial_lens", "")).strip().lower() != "kryptonite":
            errors.append("security.adversarial_lens must be kryptonite")
        if str(security.get("data_classification", "")).strip().lower() not in {
            "none", "internal", "personal", "sensitive", "regulated"
        }:
            errors.append("security.data_classification is invalid")
        derived_risk_value = security.get("derived_risk")
        derived_risk = derived_risk_value if isinstance(derived_risk_value, str) else ""
        declared_slice_risks = [
            str(item.get("risk", "")).strip().upper()
            for item in slices
            if isinstance(item, dict) and str(item.get("risk", "")).strip().upper() in RISK_RANK
        ]
        mission_context = {
            "mission": {
                key: value
                for key, value in mission.items()
                if key != "out_of_scope"
            } if isinstance(mission, dict) else {},
            "testing": testing,
            "decisions": decisions,
        }
        independent_risk = derive_independent_mission_risk(security, mission_context)
        expected_risk = max(
            [independent_risk, *declared_slice_risks], key=RISK_RANK.get
        )
        if derived_risk not in RISK_RANK:
            errors.append("security.derived_risk must be one of R1, R2, R3, R4")
        elif derived_risk != expected_risk:
            errors.append(
                "security.derived_risk must equal the maximum independent mission risk and declared slice risk"
            )
        threats = security.get("threat_scenarios")
        required_threats = 2 if RISK_RANK.get(expected_risk, 0) >= RISK_RANK["R3"] else 1
        if not isinstance(threats, list) or len(threats) < required_threats:
            errors.append(f"security.threat_scenarios requires at least {required_threats} complete adversarial cases")
        else:
            threat_ids: list[str] = []
            threat_identities: list[tuple[str, str, str, str]] = []
            for index, threat in enumerate(threats, start=1):
                if not isinstance(threat, dict):
                    errors.append(f"security.threat_scenarios[{index}] must be an object")
                    continue
                threat_id = str(threat.get("id", "")).strip()
                threat_ids.append(threat_id)
                threat_identities.append(threat_semantic_identity(threat))
                if not threat_id:
                    errors.append(f"security.threat_scenarios[{index}].id is required")
                if set(threat) != set(THREAT_CONTRACT_FIELDS):
                    errors.append(
                        f"security.threat_scenarios[{index}] must contain exactly "
                        "id, name, scenario, attack_surface, and expected_control"
                    )
                if not threat_contract_is_substantive(threat):
                    errors.append(
                        f"security.threat_scenarios[{index}] requires substantive canonical "
                        "name, scenario, attack_surface, and expected_control text"
                    )
            if any(not value for value in threat_ids) or len(set(threat_ids)) != len(threat_ids):
                errors.append("security.threat_scenarios ids must be non-empty and unique")
            if len(set(threat_identities)) != len(threat_identities):
                errors.append(
                    "security.threat_scenarios must be semantically unique; changing only an id does not add a case"
                )

        authority_decisions = security.get("authority_decisions")
        if not isinstance(authority_decisions, list) or not authority_decisions:
            errors.append("security.authority_decisions requires locked authority decisions")
        else:
            security_by_id: dict[str, tuple[str, str]] = {}
            for index, decision in enumerate(authority_decisions, start=1):
                if not isinstance(decision, dict):
                    errors.append(f"security.authority_decisions[{index}] must be an object")
                    continue
                did = str(decision.get("id", "")).strip()
                statement = str(decision.get("statement", "")).strip()
                status = str(decision.get("status", "")).strip().lower()
                if not did or not decision_text_is_substantive(statement) or status != "locked":
                    errors.append(
                        f"security.authority_decisions[{index}] must have a non-empty id, "
                        "substantive statement, and locked status"
                    )
                if did in security_by_id:
                    errors.append(f"duplicate security authority decision id: {did}")
                security_by_id[did] = (statement, status)
            top_level_by_id = {
                str(item.get("id", "")).strip(): (
                    str(item.get("statement", "")).strip(),
                    str(item.get("status", "")).strip().lower(),
                )
                for item in decisions
                if isinstance(item, dict)
            }
            if security_by_id != top_level_by_id:
                errors.append("security.authority_decisions must correspond exactly to top-level locked decisions")

    intake = manifest.get("intake")
    if (
        not isinstance(intake, dict)
        or set(intake) != INTAKE_PROVENANCE_FIELDS
        or not isinstance(intake.get("schema_version"), int)
        or isinstance(intake.get("schema_version"), bool)
        or intake.get("schema_version") != 1
        or not isinstance(intake.get("readiness_score"), int)
        or isinstance(intake.get("readiness_score"), bool)
        or intake.get("readiness_score") != 100
        or intake.get("user_authored_intent") is not True
    ):
        errors.append("intake must exactly prove schema_version=1, readiness_score=100, and user_authored_intent=true")

    return {"valid": not errors, "errors": errors, "warnings": warnings}


def _validated_role_authority(
    manifest: dict[str, Any], value: Any, role: str, label: str
) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != INTEGRATOR_AUTHORITY_FIELDS:
        raise FactoryError(TRANSITION_AUTHORIZATION_ERROR)
    authority: dict[str, str] = {}
    for field in ("session_id", "provider", "model"):
        raw = value.get(field)
        if not isinstance(raw, str) or not raw or raw != raw.strip():
            raise FactoryError(TRANSITION_AUTHORIZATION_ERROR)
        authority[field] = raw
    configured = manifest.get("models", {}).get(role, {})
    if (
        authority["provider"] != configured.get("provider")
        or authority["model"] != configured.get("model")
    ):
        raise FactoryError(TRANSITION_AUTHORIZATION_ERROR)
    return authority


def _validated_integrator_authority(
    manifest: dict[str, Any], value: Any, label: str
) -> dict[str, str]:
    return _validated_role_authority(manifest, value, "integrator", label)


def _authorize_milestone_actor(
    manifest: dict[str, Any],
    state: dict[str, Any],
    action: str,
    trusted_actor: Any,
) -> dict[str, str]:
    actor = _validated_integrator_authority(
        manifest, trusted_actor, "trusted integrator actor"
    )
    bound = state.get("integrator_authority")
    if bound is None:
        if action != "start_milestone":
            raise FactoryError(TRANSITION_AUTHORIZATION_ERROR)
    elif actor != bound:
        raise FactoryError(TRANSITION_AUTHORIZATION_ERROR)
    return actor


def _authorize_slice_actor(
    manifest: dict[str, Any],
    state: dict[str, Any],
    current: dict[str, Any],
    action: str,
    trusted_actor: Any,
) -> tuple[str, dict[str, str]]:
    if action in BUILDER_SLICE_ACTIONS:
        actor = _validated_role_authority(
            manifest, trusted_actor, "builder", "trusted slice actor"
        )
        bound = current.get("builder_authority")
        if bound is None:
            if action != "start_slice":
                raise FactoryError(TRANSITION_AUTHORIZATION_ERROR)
        elif actor != bound:
            raise FactoryError(TRANSITION_AUTHORIZATION_ERROR)
        return "builder", actor
    if action in INTEGRATOR_SLICE_ACTIONS:
        actor = _validated_integrator_authority(
            manifest, trusted_actor, "trusted slice actor"
        )
        bound = state.get("integrator_authority")
        if bound is None or actor != bound:
            raise FactoryError(TRANSITION_AUTHORIZATION_ERROR)
        return "integrator", actor
    raise FactoryError(f"unsupported slice action: {action}")


def initial_state(manifest: dict[str, Any]) -> dict[str, Any]:
    check = validate_manifest(manifest)
    if not check["valid"]:
        raise FactoryError("invalid manifest: " + "; ".join(check["errors"]))
    state = {
        "schema_version": SCHEMA_VERSION,
        "mission_id": manifest["mission"]["id"],
        "manifest_digest": _manifest_digest(manifest),
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "revision": 0,
        "integrator_authority": None,
        "milestones": {
            item["id"]: {
                "status": "pending",
                "acceptance_passed": [],
                "scenario_receipts": [],
            }
            for item in manifest["milestones"]
        },
        "slices": {
            item["id"]: {
                "status": "pending",
                "builder_authority": None,
                "attempt": 0,
                "remediation_cycles": 0,
                "failure_fingerprints": {},
                "candidate_sha": None,
                "last_rejected_sha": None,
                "checks": [],
                "acceptance_passed": [],
                "review": None,
            }
            for item in manifest["slices"]
        },
        "events": [],
    }
    _attest_state(state)
    return state


def _validate_state_compatibility(manifest: dict[str, Any], state: dict[str, Any]) -> None:
    _validate_state_attestation(state)
    if set(state) != STATE_FIELDS:
        raise FactoryError("state has unexpected or omitted top-level fields")
    if state.get("schema_version") != SCHEMA_VERSION:
        raise FactoryError("state schema_version does not match factory schema")
    if state.get("mission_id") != manifest.get("mission", {}).get("id"):
        raise FactoryError("state mission_id does not match manifest mission.id")
    if state.get("manifest_digest") != _manifest_digest(manifest):
        raise FactoryError("state manifest_digest does not match manifest")
    expected_milestones = {str(item["id"]) for item in manifest.get("milestones", [])}
    expected_slices = {str(item["id"]) for item in manifest.get("slices", [])}
    if not isinstance(state.get("milestones"), dict) or set(state["milestones"]) != expected_milestones:
        raise FactoryError("state milestone set does not match manifest")
    if not isinstance(state.get("slices"), dict) or set(state["slices"]) != expected_slices:
        raise FactoryError("state slice set does not match manifest")
    revision = state.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise FactoryError("state revision must be an integer >= 0")
    if not isinstance(state.get("events"), list):
        raise FactoryError("state events must be a list")
    for timestamp_field in ("created_at", "updated_at"):
        _normalise_receipt_timestamp(state.get(timestamp_field), f"state {timestamp_field}")
    if "integrator_authority" not in state:
        raise FactoryError("state integrator_authority attestation field is required")
    authority = state["integrator_authority"]
    if authority is not None:
        canonical_authority = _validated_integrator_authority(
            manifest, authority, "state integrator_authority"
        )
        if authority != canonical_authority:
            raise FactoryError("state integrator_authority is not canonical")
    elif any(
        isinstance(current, dict) and current.get("status") != "pending"
        for current in state["milestones"].values()
    ):
        raise FactoryError(
            "state integrator_authority cannot be null after a milestone transition"
        )

    milestone_specs = _entity_map(manifest, "milestones")
    slice_specs = _entity_map(manifest, "slices")
    milestone_statuses = {"pending", "active", "validating", "blocked", "replan_required", "completed"}
    slice_statuses = {"pending", "active", "review", "review_passed", "blocked", "replan_required", "completed"}
    for mid, current in state["milestones"].items():
        if not isinstance(current, dict) or current.get("status") not in milestone_statuses:
            raise FactoryError(f"state milestone {mid} has invalid status/shape")
        expected_fields = (
            COMPLETED_MILESTONE_STATE_FIELDS
            if current["status"] == "completed"
            else MILESTONE_STATE_FIELDS
        )
        if set(current) != expected_fields:
            raise FactoryError(f"state milestone {mid} has unexpected or omitted fields")
        if not isinstance(current.get("acceptance_passed"), list) or not isinstance(current.get("scenario_receipts"), list):
            raise FactoryError(f"state milestone {mid} has invalid evidence fields")
        if current["status"] == "completed":
            if set(map(str, current["acceptance_passed"])) != _acceptance_ids(milestone_specs[mid]):
                raise FactoryError(f"completed milestone {mid} lacks exact acceptance evidence")
            if not current["scenario_receipts"] or not current.get("integration_sha") or not current.get("holdout_review"):
                raise FactoryError(f"completed milestone {mid} lacks scenario/SHA/holdout evidence")
            integration_sha = _require_git_commit(manifest, current.get("integration_sha"), "integration_sha")
            scenario_receipts = _validate_scenario_receipts(
                current["scenario_receipts"],
                workspace_path=str(manifest.get("mission", {}).get("workspace_path", "")),
                expected_criteria=_acceptance_ids(milestone_specs[mid]),
                mission_id=str(manifest.get("mission", {}).get("id", "")),
                entity_id=mid,
                integration_sha=integration_sha,
            )
            if current["scenario_receipts"] != scenario_receipts:
                raise FactoryError(f"completed milestone {mid} scenario receipts are not normalized")
            _validate_holdout_review(manifest, current["holdout_review"], integration_sha, mid)
            if any(state["slices"][sid].get("status") != "completed" for sid in milestone_specs[mid].get("slices", [])):
                raise FactoryError(f"completed milestone {mid} has incomplete slices")
    for sid, current in state["slices"].items():
        if not isinstance(current, dict) or current.get("status") not in slice_statuses:
            raise FactoryError(f"state slice {sid} has invalid status/shape")
        if set(current) != SLICE_STATE_FIELDS:
            raise FactoryError(f"state slice {sid} has unexpected or omitted fields")
        if "builder_authority" not in current:
            raise FactoryError(f"state slice {sid} builder_authority attestation field is required")
        builder_authority = current["builder_authority"]
        if builder_authority is not None:
            canonical_builder = _validated_role_authority(
                manifest,
                builder_authority,
                "builder",
                f"state slice {sid} builder_authority",
            )
            if builder_authority != canonical_builder:
                raise FactoryError(f"state slice {sid} builder_authority is not canonical")
        if current["status"] == "pending" and builder_authority is not None:
            raise FactoryError(f"pending slice {sid} cannot have bound builder_authority")
        if current["status"] != "pending" and builder_authority is None:
            raise FactoryError(f"state slice {sid} builder_authority cannot be null after transition")
        for key in ("attempt", "remediation_cycles"):
            if not isinstance(current.get(key), int) or isinstance(current.get(key), bool) or current[key] < 0:
                raise FactoryError(f"state slice {sid}.{key} must be an integer >= 0")
        if not isinstance(current.get("failure_fingerprints"), dict) or not isinstance(current.get("checks"), list) or not isinstance(current.get("acceptance_passed"), list):
            raise FactoryError(f"state slice {sid} has invalid evidence fields")
        checks_required = current["status"] in {"review", "review_passed", "completed"}
        if checks_required and not current["checks"]:
            raise FactoryError(f"state slice {sid} lacks structured check receipts")
        if current["checks"]:
            normalised_checks = _validate_check_receipts(
                current["checks"],
                workspace_path=str(manifest.get("mission", {}).get("workspace_path", "")),
                expected_criteria=_acceptance_ids(slice_specs[sid]),
                mission_id=str(manifest.get("mission", {}).get("id", "")),
                entity_id=sid,
                candidate_sha=str(current.get("candidate_sha") or ""),
            )
            if current["checks"] != normalised_checks:
                raise FactoryError(f"state slice {sid} check receipts are not normalized")
        if current["status"] in {"review_passed", "completed"}:
            review = current.get("review")
            if not isinstance(review, dict) or review.get("verdict") != "pass" or not review.get("reviews"):
                raise FactoryError(f"state slice {sid} lacks passed independent review evidence")
            candidate_sha = _require_git_commit(manifest, current.get("candidate_sha"), "candidate_sha")
            _validate_reviews(manifest, slice_specs[sid], review["reviews"], candidate_sha)
        if current["status"] == "completed":
            if set(map(str, current["acceptance_passed"])) != _acceptance_ids(slice_specs[sid]):
                raise FactoryError(f"completed slice {sid} lacks exact acceptance evidence")
            if not current.get("candidate_sha") or not current.get("checks"):
                raise FactoryError(f"completed slice {sid} lacks candidate/check evidence")

    for index, event in enumerate(state["events"]):
        label = f"state event {index}"
        if not isinstance(event, dict) or set(event) != STATE_EVENT_FIELDS:
            raise FactoryError(f"{label} has unexpected or omitted fields")
        _normalise_receipt_timestamp(event.get("at"), f"{label}.at")
        entity_id = event.get("entity_id")
        action = event.get("action")
        actor = event.get("actor")
        if not isinstance(event.get("evidence"), dict):
            raise FactoryError(f"{label} evidence must be an object")
        if not isinstance(actor, dict) or set(actor) != STATE_EVENT_ACTOR_FIELDS:
            raise FactoryError(f"{label} actor has unexpected or omitted fields")
        if entity_id in expected_milestones:
            expected_role = "integrator"
            allowed_actions = MILESTONE_ACTIONS
            expected_actor = state["integrator_authority"]
        elif entity_id in expected_slices:
            expected_role = "builder" if action in BUILDER_SLICE_ACTIONS else "integrator"
            allowed_actions = BUILDER_SLICE_ACTIONS | INTEGRATOR_SLICE_ACTIONS
            expected_actor = (
                state["slices"][entity_id]["builder_authority"]
                if expected_role == "builder"
                else state["integrator_authority"]
            )
        else:
            raise FactoryError(f"{label} entity_id does not match manifest")
        if action not in allowed_actions:
            raise FactoryError(f"{label} action is not valid for its entity")
        if actor.get("role") != expected_role:
            raise FactoryError(f"{label} actor role does not match action authority")
        event_authority = {key: actor.get(key) for key in INTEGRATOR_AUTHORITY_FIELDS}
        if expected_actor is None or event_authority != expected_actor:
            raise FactoryError(f"{label} actor does not match bound state authority")


def load_or_create_state(
    manifest: dict[str, Any], state_path: str | Path = DEFAULT_STATE
) -> tuple[dict[str, Any], Path]:
    path = Path(state_path).expanduser().resolve()
    if path.exists():
        state = _read_json(path)
    else:
        state = initial_state(manifest)
        _write_json_atomic(path, state)
    _validate_state_compatibility(manifest, state)
    return state, path


def _entity_map(manifest: dict[str, Any], key: str) -> dict[str, dict[str, Any]]:
    return {str(item["id"]): item for item in manifest.get(key, [])}


def _policy(manifest: dict[str, Any], key: str) -> int:
    return int(manifest.get("policy", {}).get(key, 1))


def _paths_overlap(
    left: list[str],
    right: list[str],
    workspace_path: str | Path | None = None,
) -> bool:
    """Return whether path/glob ownership sets may intersect.

    Relative coordinates and equivalent absolute runtime contracts are compared
    in the same canonical workspace coordinate system. A pair is only disjoint
    when aligned literal segments prove it cannot intersect; malformed or
    uncertain patterns serialize.
    """

    if not left or not right:
        return True
    workspace_base = workspace_path if workspace_path is not None else Path.cwd()

    def could_intersect(a: Any, b: Any) -> bool:
        left_segments = _canonical_ownership_pattern(a, workspace_base)
        right_segments = _canonical_ownership_pattern(b, workspace_base)
        if left_segments is None or right_segments is None:
            return True
        for left_segment, right_segment in zip(left_segments, right_segments):
            left_glob = any(char in left_segment for char in "*?[{")
            right_glob = any(char in right_segment for char in "*?[{")
            if not left_glob and not right_glob and left_segment != right_segment:
                return False
            if left_segment == "**" or right_segment == "**":
                return True
        return True

    return any(could_intersect(a, b) for a in left for b in right)


def _dispatch_descriptor(manifest: dict[str, Any], entity_id: str, entity_type: str) -> dict[str, Any]:
    is_milestone = entity_type == "milestone"
    configured_role = "integrator" if is_milestone else "builder"
    execution_role = "orchestrator" if is_milestone else "worker"
    model_ref = manifest.get("models", {}).get(configured_role, {})
    execution = manifest.get("execution") if isinstance(manifest.get("execution"), dict) else {}
    reasoning = execution.get("reasoning_effort") if isinstance(execution.get("reasoning_effort"), dict) else {}
    return {
        "entity_id": entity_id,
        "entity_type": entity_type,
        "action": "start_milestone" if is_milestone else "start_slice",
        "configured_role": configured_role,
        "execution_role": execution_role,
        "provider": str(model_ref.get("provider", "")),
        "model": str(model_ref.get("model", "")),
        "reasoning_effort": str(reasoning.get(execution_role) or ("high" if is_milestone else "medium")),
        "execution_mode": "orchestration" if is_milestone else "functional_slice",
        "auto_launch": False,
    }


def next_actions(manifest: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    _validate_state_compatibility(manifest, state)
    milestones = _entity_map(manifest, "milestones")
    slices = _entity_map(manifest, "slices")
    workspace_path = str(manifest.get("mission", {}).get("workspace_path", ""))
    decisions = {
        str(item.get("id")): str(item.get("status"))
        for item in manifest.get("decisions", [])
        if isinstance(item, dict)
    }
    active_milestones = [
        mid
        for mid, value in state["milestones"].items()
        if value["status"] in {"active", "validating"}
    ]
    active_slices = [
        sid
        for sid, value in state["slices"].items()
        if value["status"] in {"active", "review", "review_passed"}
    ]

    startable_milestones: list[str] = []
    if len(active_milestones) < _policy(manifest, "max_active_milestones"):
        for mid, spec in milestones.items():
            if state["milestones"][mid]["status"] != "pending":
                continue
            if all(state["milestones"][dep]["status"] == "completed" for dep in spec.get("depends_on", [])):
                startable_milestones.append(mid)

    startable_slices: list[str] = []
    available_slots = max(
        0, _policy(manifest, "max_parallel_slices") - len(active_slices)
    )
    if available_slots:
        for sid, spec in slices.items():
            if len(startable_slices) >= available_slots:
                break
            if state["slices"][sid]["status"] != "pending":
                continue
            mid = spec["milestone_id"]
            if state["milestones"][mid]["status"] != "active":
                continue
            if not all(state["slices"][dep]["status"] == "completed" for dep in spec.get("depends_on", [])):
                continue
            if not all(decisions.get(str(did)) == "locked" for did in spec.get("requires_decisions", [])):
                continue
            overlaps_active = any(
                _paths_overlap(
                    spec.get("paths", []),
                    slices[active].get("paths", []),
                    workspace_path,
                )
                for active in active_slices
            )
            overlaps_selected = any(
                _paths_overlap(
                    spec.get("paths", []),
                    slices[selected].get("paths", []),
                    workspace_path,
                )
                for selected in startable_slices
            )
            if not overlaps_active and not overlaps_selected:
                startable_slices.append(sid)

    gates: list[str] = []
    for mid, spec in milestones.items():
        if state["milestones"][mid]["status"] != "active":
            continue
        if all(state["slices"][sid]["status"] == "completed" for sid in spec.get("slices", [])):
            gates.append(f"validate_milestone:{mid}")

    replan = [
        f"slice:{sid}"
        for sid, value in state["slices"].items()
        if value["status"] == "replan_required"
    ] + [
        f"milestone:{mid}"
        for mid, value in state["milestones"].items()
        if value["status"] == "replan_required"
    ]

    return {
        "active_milestones": active_milestones,
        "active_slices": active_slices,
        "startable_milestones": startable_milestones,
        "startable_slices": startable_slices,
        "gates": gates,
        "replan_required": replan,
        "dispatch": {
            "startable_milestones": [
                _dispatch_descriptor(manifest, mid, "milestone") for mid in startable_milestones
            ],
            "startable_slices": [
                _dispatch_descriptor(manifest, sid, "slice") for sid in startable_slices
            ],
        },
    }


def _require_keys(evidence: dict[str, Any], keys: Iterable[str], action: str) -> None:
    missing = [key for key in keys if evidence.get(key) in (None, "", [])]
    if missing:
        raise FactoryError(f"{action} requires evidence fields: {', '.join(missing)}")


def _require_git_commit(manifest: dict[str, Any], value: Any, label: str) -> str:
    sha = str(value or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{7,64}", sha):
        raise FactoryError(f"{label} must be a hexadecimal Git commit SHA")
    workspace = Path(str(manifest.get("mission", {}).get("workspace_path", ""))).expanduser()
    try:
        result = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "--verify", f"{sha}^{{commit}}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FactoryError(f"cannot verify {label} in workspace Git repository") from exc
    if result.returncode != 0:
        raise FactoryError(f"{label} is not a commit in the workspace repository")
    return result.stdout.strip().lower()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


_PASS_BEFORE_EXCEPT = re.compile(
    r"(?i)\bpass(?:ed|es)?\b.*\bexcept\b"
)
_NONZERO_BEFORE_PROBLEM = re.compile(
    r"(?i)\b[1-9]\d*\b.*\b(?:fail(?:s|ed)?|failures?|errors?|exceptions?)\b"
)
_ZERO_PROBLEM_COUNTER = re.compile(
    r"(?i)(?:\b(?:fail(?:s|ed)?|failures?|errors?|exceptions?)\b[\"']?\s*[:=]\s*[\"']?0\b|"
    r"\b0\b(?:(?!\b[1-9]\d*\b).)*?\b(?:fail(?:s|ed)?|failures?|errors?|exceptions?)\b)"
)
_EXPLICIT_EPISTEMIC_OUTCOME = re.compile(
    r"""(?ix)(?:
        ^(?:unknown|uncertain|indeterminate)[.!]?$
        |
        \b(?:unknown|uncertain|indeterminate)\b.*\b(?:whether|if)\b
        |
        \b(?:it|this|that|outcome|result|status|verdict|verification|validation|evidence|proof|
            auth(?:entication|orisation|orization)?|authorization|ownership|tenant[ -]isolation|
            access[ -]control)\b.*\b(?:unknown|uncertain|indeterminate)\b
        |
        \bnot\b(?!\s+only\b).*\b(?:verified|established)\b
    )"""
)
_EXPECTED_PROBLEM_CONTEXT = re.compile(
    r"(?i)\b(?:expected|expect(?:ed|s)?|should\s+(?:produce|raise|report)|"
    r"designed\s+to\s+(?:produce|raise|report))\b"
)
_EXPECTED_NEGATIVE_SUCCESS = re.compile(
    r"(?i)\b(?:expected|intentional|negative|denied|rejected)\b"
)
_SUCCESSFUL_NEGATIVE_OUTCOME = re.compile(
    r"(?i)\b(?:pass(?:ed|es)?|succeed(?:ed|s)?|as\s+(?:designed|expected)|"
    r"handled\s+correctly)\b"
)


def _normalised_evidence_clauses(text: str) -> Iterable[str]:
    """Yield whitespace-normalized evidence lines as unbounded semantic clauses."""

    for raw_line in text.splitlines():
        clause = re.sub(r"\s+", " ", raw_line).strip()
        if clause:
            yield clause


def _problem_count_is_expected(clause: str, match: re.Match[str]) -> bool:
    before = clause[:match.start()]
    after = clause[match.end():]
    if _EXPECTED_PROBLEM_CONTEXT.search(before):
        return True
    if re.match(r"(?i)\s+(?:is|are|was|were)\s+expected\b", after):
        return True
    if re.match(
        r"(?i)\s+(?:handling|paths?|modes?|cases?|scenarios?|examples?|conditions?|"
        r"recovery|retries?)\b",
        after,
    ):
        return True
    return bool(
        _EXPECTED_NEGATIVE_SUCCESS.search(clause)
        and _SUCCESSFUL_NEGATIVE_OUTCOME.search(clause)
    )


def _clause_has_unbounded_adverse_evidence(clause: str) -> tuple[bool, bool]:
    """Return (failure, indeterminate) for semantic signals on one whole line."""

    if _PASS_BEFORE_EXCEPT.search(clause):
        return True, False
    count_clause = _ZERO_PROBLEM_COUNTER.sub(" ", clause)
    for match in _NONZERO_BEFORE_PROBLEM.finditer(count_clause):
        if not _problem_count_is_expected(count_clause, match):
            return True, False
    if _EXPLICIT_EPISTEMIC_OUTCOME.search(clause):
        return False, True
    return False, False


def _artifact_outcome(path: Path) -> str:
    """Classify a raw artifact as success, failure, or indeterminate.

    Success is positive evidence, not merely the absence of an explicit error.
    Only recognized structured result fields or standalone PASS/SUCCESS markers
    can establish it. Explicit contradictions always fail closed.
    """

    try:
        text = path.read_bytes().decode("utf-8", errors="replace")
    except OSError as exc:
        raise FactoryError("receipt artifact cannot be read") from exc

    positive_values = {"pass", "passed", "success", "successful", "ok"}
    failure_values = {
        "fail", "failed", "failure", "error", "errored", "unsuccessful",
        "not_ok", "aborted", "fatal", "rejected",
    }
    boolean_fields = {"success", "ok", "pass", "passed"}
    code_fields = {"exit_code", "exitcode", "return_code", "returncode"}
    status_fields = {"result", "status", "outcome", "verdict"}
    error_fields = {
        "error", "errors", "failure", "failures", "failed", "exception", "exceptions",
    }
    flags = {"positive": False, "failure": False, "indeterminate": False}

    def normalise_key(value: Any) -> str:
        return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")

    def normalise_value(value: Any) -> str:
        return re.sub(r"[\s-]+", "_", str(value).strip().lower())

    def inspect_field(key: str, value: Any) -> None:
        if key in boolean_fields:
            if value is True:
                flags["positive"] = True
            elif value is False:
                flags["failure"] = True
            elif isinstance(value, str) and normalise_value(value) in positive_values | {"true", "yes"}:
                flags["positive"] = True
            elif isinstance(value, str) and normalise_value(value) in failure_values | {"false", "no", "0"}:
                flags["failure"] = True
            else:
                flags["indeterminate"] = True
            return
        if key in code_fields:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if value != 0:
                    flags["failure"] = True
            elif isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
                if int(value.strip()) != 0:
                    flags["failure"] = True
            else:
                flags["indeterminate"] = True
            return
        if key in status_fields:
            if isinstance(value, str) and normalise_value(value) in positive_values:
                flags["positive"] = True
            elif isinstance(value, str) and normalise_value(value) in failure_values:
                flags["failure"] = True
            else:
                # A declared result/status with an unrecognized value cannot be
                # rescued by a separate success=true assertion.
                flags["indeterminate"] = True
            return
        if key in error_fields:
            if value in (None, False, "", [], {}):
                return
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if value != 0:
                    flags["failure"] = True
                return
            if isinstance(value, str) and normalise_value(value) in {
                "0", "false", "none", "null", "[]", "{}",
            }:
                return
            flags["failure"] = True

    def inspect_structured(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                inspect_structured(item)
        elif isinstance(value, dict):
            for raw_key, item in value.items():
                inspect_field(normalise_key(raw_key), item)
                inspect_structured(item)

    parsed_values: list[Any] = []
    try:
        parsed_values.append(json.loads(text))
    except json.JSONDecodeError:
        pass

    plain_positive_marker = re.compile(
        r"(?i)^\s*(?:={2,}\s*)?(?:PASS|PASSED|SUCCESS|SUCCESSFUL|OK)"
        r"(?:\s*$|\s*[:.!-]\s*.*$)"
    )

    # PyYAML is optional. The bounded safe loader has no custom constructors;
    # explicit line markers below retain the contract when it is unavailable.
    # A line such as ``PASS: journey completed`` is plain evidence, not a YAML
    # boolean field whose prose value should make the artifact indeterminate.
    if len(text) <= 1024 * 1024 and not any(
        plain_positive_marker.match(line) for line in text.splitlines()
    ):
        try:
            import yaml  # type: ignore

            parsed_values.append(yaml.safe_load(text))
        except (ImportError, ValueError, TypeError):
            pass
        except Exception:
            pass
    for value in parsed_values:
        inspect_structured(value)

    # Parse XML elements and attributes when possible. Malformed documents still
    # receive the conservative attribute and line checks below.
    try:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(text)
    except (ET.ParseError, ValueError):
        root = None
    if root is not None:
        for element in root.iter():
            inspect_field(normalise_key(element.tag.rsplit("}", 1)[-1]), element.text.strip() if element.text else None)
            for raw_key, value in element.attrib.items():
                inspect_field(normalise_key(raw_key.rsplit("}", 1)[-1]), value)

    field_line = re.compile(
        r"^\s*(?:[-*]\s*)?([A-Za-z][A-Za-z0-9 _-]*?)\s*[:=]\s*(.*?)\s*$"
    )
    positive_line = plain_positive_marker
    failure_line = re.compile(
        r"(?i)^\s*(?:={2,}\s*)?(?:FAIL|FAILED|FAILURE|ERROR|EXCEPTION|FATAL)"
        r"(?:\s*$|\s*[:.!-]\s*.*$)"
    )
    contradicted_positive_line = re.compile(
        r"(?i)^\s*(?:={2,}\s*)?(?:PASS|PASSED|SUCCESS|SUCCESSFUL|OK)\s*[:=]\s*"
        r"(?:false|no|0|fail|failed|failure|error|unsuccessful|rejected)\s*$"
    )
    adverse_clause = re.compile(
        r"(?i)(?:"
        r"\b(?:observed behavior|actual result|observation)\s*:\s*.*\b"
        r"(?:failed(?!\s+closed)|fails(?!\s+closed)|errored|aborted|fatal|unsuccessful|error)\b|"
        r"\b(?:auth(?:entication|orisation|orization)?|permission|ownership|tenant[ -]isolation|"
        r"access[ -]control)(?:\s+check)?\b.*\b(?:failed(?!\s+closed)|"
        r"fails(?!\s+closed)|errored|"
        r"was bypassed|did not (?:deny|reject|enforce|protect))\b|"
        r"\b(?:test|suite|build|command|process|journey|scenario)\b"
        r".*\b(?:failed(?!\s+closed)|fails(?!\s+closed)|errored|aborted|"
        r"was unsuccessful|(?:did not|could not|cannot) (?:pass|complete|succeed))\b|"
        r"\b(?:but|however|unfortunately|actually|yet)\b.*\b"
        r"(?:failed(?!\s+closed)|errored|aborted|fatal|unsuccessful|error)\b|"
        r"\b(?:failed|fails|failure)\s+open\b|"
        r"\b(?:unexpectedly|incorrectly)\s+(?:allowed|accepted|exposed|leaked|published|deployed)\b"
        r")"
    )
    for line in _normalised_evidence_clauses(text):
        semantic_failure, semantic_indeterminate = (
            _clause_has_unbounded_adverse_evidence(line)
        )
        if semantic_failure:
            flags["failure"] = True
        if semantic_indeterminate:
            flags["indeterminate"] = True
        explicit_positive = positive_line.match(line)
        match = field_line.match(line)
        if match and not explicit_positive:
            key = normalise_key(match.group(1))
            raw_value = re.sub(r"\s+#.*$", "", match.group(2)).strip()
            inspect_field(key, raw_value.strip("'\""))
        if failure_line.match(line) or re.match(r"(?i)^\s*not\s+ok(?:\s+\d+)?\b", line):
            flags["failure"] = True
        if re.match(r"(?i)^\s*[1-9]\d*\s+(?:failed|errors?)\b", line):
            flags["failure"] = True
        if re.search(r"(?i)\bprocess completed with (?:exit|return) code\s+[+-]?[1-9]\d*\b", line):
            flags["failure"] = True
        if contradicted_positive_line.match(line):
            flags["failure"] = True
        elif explicit_positive:
            flags["positive"] = True
        if adverse_clause.search(line):
            flags["failure"] = True
        if re.search(
            r"(?i)\b(?:unhandled\s+)?exceptions?\s+"
            r"(?:occurred|was\s+raised|were\s+raised|was\s+thrown|were\s+thrown|encountered)\b",
            line,
        ):
            flags["failure"] = True


    for match in re.finditer(
        r"(?i)\b(success|ok|pass|passed|exit[_ -]?code|return[_ -]?code|"
        r"result|status|outcome|verdict|failures?|errors?)\s*=\s*['\"]([^'\"]*)['\"]",
        text,
    ):
        inspect_field(normalise_key(match.group(1)), match.group(2))

    if flags["failure"]:
        return "failure"
    if flags["indeterminate"]:
        return "indeterminate"
    if flags["positive"]:
        return "success"
    return "indeterminate"


def _artifact_reports_failure(path: Path) -> bool:
    """Compatibility predicate for callers that only need contradiction state."""

    return _artifact_outcome(path) == "failure"


def _normalise_receipt_timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FactoryError(f"{label} requires timestamp")
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
    except ValueError as exc:
        raise FactoryError(f"{label} timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FactoryError(f"{label} timestamp must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _validate_artifact_receipts(
    receipts: Any,
    *,
    receipt_kind: str,
    command_field: str,
    revision_field: str,
    workspace_path: str = "",
    expected_criteria: set[str] | None = None,
    mission_id: str | None = None,
    entity_id: str | None = None,
    revision_sha: str | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(receipts, list) or not receipts:
        raise FactoryError(f"{receipt_kind}s must be a non-empty list")
    normalised: list[dict[str, Any]] = []
    covered: set[str] = set()
    workspace = Path(workspace_path).expanduser() if workspace_path else Path.cwd()
    for index, receipt in enumerate(receipts, start=1):
        label = f"{receipt_kind} {index}"
        if not isinstance(receipt, dict):
            raise FactoryError(f"{label} must be an object")
        allowed_fields = {
            "mission_id", "entity_id", revision_field, command_field, "exit_code",
            "environment_fingerprint", "observed_result", "timestamp", "path",
            "sha256", "criterion_ids",
        }
        unknown_fields = sorted(set(map(str, receipt)) - allowed_fields)
        if unknown_fields:
            raise FactoryError(f"{label} contains unknown or contradictory fields")
        receipt_mission = receipt.get("mission_id")
        receipt_entity = receipt.get("entity_id")
        receipt_revision = receipt.get(revision_field)
        if not isinstance(receipt_mission, str) or not receipt_mission:
            raise FactoryError(f"{label} requires mission_id")
        if not isinstance(receipt_entity, str) or not receipt_entity:
            raise FactoryError(f"{label} requires entity_id")
        if not isinstance(receipt_revision, str) or not receipt_revision:
            raise FactoryError(f"{label} requires {revision_field}")
        if mission_id is not None and receipt_mission != mission_id:
            raise FactoryError(f"{label} mission_id does not match transition")
        if entity_id is not None and receipt_entity != entity_id:
            raise FactoryError(f"{label} entity_id does not match transition")
        if revision_sha is not None and receipt_revision != revision_sha:
            raise FactoryError(f"{label} {revision_field} does not match transition")
        command = receipt.get(command_field)
        if not isinstance(command, str) or not command.strip():
            raise FactoryError(f"{label} requires {command_field}")
        exit_code = receipt.get("exit_code")
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            raise FactoryError(f"{label} exit_code must be an integer")
        if exit_code != 0:
            raise FactoryError(f"{label} exit_code must be zero")
        environment = receipt.get("environment_fingerprint")
        if not isinstance(environment, str) or not environment.strip():
            raise FactoryError(f"{label} requires environment_fingerprint")
        if receipt.get("observed_result") != "PASS":
            raise FactoryError(f"{label} observed_result must exactly be PASS")
        timestamp = _normalise_receipt_timestamp(receipt.get("timestamp"), label)
        path = receipt.get("path")
        if not isinstance(path, str) or not path.strip():
            raise FactoryError(f"{label} requires path")
        digest = str(receipt.get("sha256", "")).strip().lower()
        criterion_ids = receipt.get("criterion_ids")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise FactoryError(f"{label} requires a 64-character sha256")
        if not isinstance(criterion_ids, list) or not criterion_ids:
            raise FactoryError(f"{label} requires criterion_ids")
        criteria = [str(value).strip() for value in criterion_ids]
        if any(not value for value in criteria) or len(set(criteria)) != len(criteria):
            raise FactoryError(f"{label} criterion_ids must be non-empty and unique")
        artifact = Path(path.strip()).expanduser()
        if not artifact.is_absolute():
            artifact = workspace / artifact
        try:
            artifact = artifact.resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            raise FactoryError(f"{label} artifact path is invalid") from exc
        if not artifact.is_file():
            raise FactoryError(f"{label} artifact does not exist")
        actual_digest = _sha256_file(artifact)
        if actual_digest != digest:
            raise FactoryError(f"{label} sha256 does not match artifact")
        artifact_outcome = _artifact_outcome(artifact)
        if artifact_outcome == "failure":
            raise FactoryError(f"{label} artifact explicitly reports failure")
        if artifact_outcome != "success":
            raise FactoryError(f"{label} artifact does not report a recognized positive outcome")
        if expected_criteria is not None:
            unknown = set(criteria) - expected_criteria
            if unknown:
                raise FactoryError(f"{label} has criterion_ids outside the acceptance contract")
        covered.update(criteria)
        normalised.append({
            "mission_id": receipt_mission,
            "entity_id": receipt_entity,
            revision_field: receipt_revision,
            command_field: command.strip(),
            "exit_code": exit_code,
            "environment_fingerprint": environment.strip(),
            "observed_result": "PASS",
            "timestamp": timestamp,
            "path": str(artifact),
            "sha256": digest,
            "criterion_ids": criteria,
        })
    if expected_criteria is not None and covered != expected_criteria:
        missing = sorted(expected_criteria - covered)
        raise FactoryError(f"{receipt_kind}s do not cover criteria: " + ", ".join(missing))
    return normalised


def _validate_scenario_receipts(
    receipts: Any,
    *,
    workspace_path: str = "",
    expected_criteria: set[str] | None = None,
    mission_id: str | None = None,
    entity_id: str | None = None,
    integration_sha: str | None = None,
) -> list[dict[str, Any]]:
    return _validate_artifact_receipts(
        receipts,
        receipt_kind="scenario receipt",
        command_field="command_or_scenario",
        revision_field="integration_sha",
        workspace_path=workspace_path,
        expected_criteria=expected_criteria,
        mission_id=mission_id,
        entity_id=entity_id,
        revision_sha=integration_sha,
    )


def _validate_check_receipts(
    receipts: Any,
    *,
    workspace_path: str = "",
    expected_criteria: set[str] | None = None,
    mission_id: str | None = None,
    entity_id: str | None = None,
    candidate_sha: str | None = None,
) -> list[dict[str, Any]]:
    return _validate_artifact_receipts(
        receipts,
        receipt_kind="check receipt",
        command_field="command",
        revision_field="candidate_sha",
        workspace_path=workspace_path,
        expected_criteria=expected_criteria,
        mission_id=mission_id,
        entity_id=entity_id,
        revision_sha=candidate_sha,
    )


def _review_key(*, create: bool) -> bytes:
    # Process-local by design: agent-accessible filesystem tools cannot read or
    # forge the signer. Pending receipts fail closed after a Hermes restart and
    # must be re-issued by their original reviewer sessions.
    return _PROCESS_REVIEW_KEY


def _manifest_digest(manifest: dict[str, Any]) -> str:
    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _receipt_signature(payload: dict[str, Any], *, create_key: bool) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hmac.new(_review_key(create=create_key), body, hashlib.sha256).hexdigest()


def issue_review_receipt(
    manifest: dict[str, Any],
    *,
    role: str,
    entity_id: str,
    candidate_sha: str,
    reviewer: str,
    provider: str,
    model: str,
    verdict: str,
    session_id: str,
) -> dict[str, str]:
    payload = {
        "mission_id": str(manifest.get("mission", {}).get("id", "")),
        "manifest_digest": _manifest_digest(manifest),
        "entity_id": str(entity_id).strip(),
        "role": str(role).strip().lower(),
        "reviewer": str(reviewer).strip(),
        "provider": str(provider).strip(),
        "model": str(model).strip(),
        "verdict": str(verdict).strip().upper(),
        "candidate_sha": _require_git_commit(manifest, candidate_sha, "candidate_sha"),
        "session_id": str(session_id).strip(),
    }
    if any(not value for value in payload.values()):
        raise FactoryError("review attestation requires mission, entity, role, reviewer, provider, model, verdict, SHA, and session")
    payload["attestation"] = _receipt_signature(payload, create_key=True)
    return payload


def _verify_review_receipt(manifest: dict[str, Any], receipt: dict[str, Any]) -> None:
    signature = str(receipt.get("attestation", "")).strip().lower()
    payload = {key: value for key, value in receipt.items() if key != "attestation"}
    expected = _receipt_signature(payload, create_key=False)
    if not hmac.compare_digest(signature, expected):
        raise FactoryError("review receipt attestation is invalid")
    if str(payload.get("mission_id", "")) != str(manifest.get("mission", {}).get("id", "")):
        raise FactoryError("review receipt mission_id does not match manifest")
    if str(payload.get("manifest_digest", "")) != _manifest_digest(manifest):
        raise FactoryError("review receipt manifest_digest does not match manifest")
    if not str(payload.get("session_id", "")).strip():
        raise FactoryError("review receipt requires a trusted session_id")


def _validate_reviews(
    manifest: dict[str, Any], spec: dict[str, Any], reviews: Any, candidate_sha: str
) -> list[dict[str, str]]:
    if not isinstance(reviews, list) or not reviews:
        raise FactoryError("reviews must be a non-empty list")
    required_roles = {str(role) for role in spec.get("review_roles", [])}
    models = manifest.get("models", {})
    normalised: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, review in enumerate(reviews, start=1):
        if not isinstance(review, dict):
            raise FactoryError(f"review {index} must be an object")
        _verify_review_receipt(manifest, review)
        if str(review.get("entity_id", "")) != str(spec.get("id", "")):
            raise FactoryError(f"review {index} entity_id does not match reviewed slice")
        if str(review.get("candidate_sha", "")) != str(candidate_sha):
            raise FactoryError(f"review {index} candidate_sha does not match requested candidate")
        role = str(review.get("role", "")).strip()
        reviewer = str(review.get("reviewer", "")).strip()
        provider = str(review.get("provider", "")).strip()
        model = str(review.get("model", "")).strip()
        verdict = str(review.get("verdict", "")).strip().upper()
        if role not in required_roles:
            raise FactoryError(f"review {index} has unexpected role {role!r}")
        expected = models.get(role, {}) if isinstance(models, dict) else {}
        if provider != str(expected.get("provider", "")).strip() or model != str(expected.get("model", "")).strip():
            raise FactoryError(f"review {role} did not use its configured provider/model")
        if not reviewer:
            raise FactoryError(f"review {role} requires reviewer identity")
        if verdict != "PASS":
            raise FactoryError(f"review {role} verdict must be PASS")
        if role in seen:
            raise FactoryError(f"duplicate review role: {role}")
        seen.add(role)
        normalised.append({
            "mission_id": str(review.get("mission_id", "")),
            "manifest_digest": str(review.get("manifest_digest", "")),
            "entity_id": str(review.get("entity_id", "")),
            "role": role,
            "reviewer": reviewer,
            "provider": provider,
            "model": model,
            "verdict": verdict,
            "candidate_sha": str(review.get("candidate_sha", "")),
            "session_id": str(review.get("session_id", "")),
            "attestation": str(review.get("attestation", "")),
        })
    missing = sorted(required_roles - seen)
    if missing:
        raise FactoryError("missing required review roles: " + ", ".join(missing))
    return normalised


def _validate_holdout_review(
    manifest: dict[str, Any], review: Any, integration_sha: str, entity_id: str = ""
) -> dict[str, str]:
    if not isinstance(review, dict):
        raise FactoryError("holdout_review must be an object")
    _verify_review_receipt(manifest, review)
    if str(review.get("role", "")).strip() != "holdout":
        raise FactoryError("holdout review receipt must have holdout role")
    if entity_id and str(review.get("entity_id", "")) != entity_id:
        raise FactoryError("holdout review entity_id does not match milestone")
    expected = manifest.get("models", {}).get("holdout", {})
    provider = str(review.get("provider", "")).strip()
    model = str(review.get("model", "")).strip()
    reviewer = str(review.get("reviewer", "")).strip()
    verdict = str(review.get("verdict", "")).strip().upper()
    candidate_sha = str(review.get("candidate_sha", "")).strip()
    if provider != str(expected.get("provider", "")).strip() or model != str(expected.get("model", "")).strip():
        raise FactoryError("holdout review did not use its configured provider/model")
    if not reviewer:
        raise FactoryError("holdout review requires reviewer identity")
    if verdict != "PASS":
        raise FactoryError("holdout review verdict must be PASS")
    if candidate_sha != str(integration_sha).strip():
        raise FactoryError("holdout review candidate_sha must match integration_sha")
    return {
        "mission_id": str(review.get("mission_id", "")),
        "manifest_digest": str(review.get("manifest_digest", "")),
        "entity_id": str(review.get("entity_id", "")),
        "role": "holdout",
        "reviewer": reviewer,
        "provider": provider,
        "model": model,
        "verdict": verdict,
        "candidate_sha": candidate_sha,
        "session_id": str(review.get("session_id", "")),
        "attestation": str(review.get("attestation", "")),
    }


def _append_event(
    state: dict[str, Any],
    entity_id: str,
    action: str,
    evidence: dict[str, Any],
    *,
    actor_role: str,
    actor: dict[str, str],
) -> None:
    state["events"].append(
        {
            "at": utc_now(),
            "entity_id": entity_id,
            "action": action,
            "actor": {"role": actor_role, **actor},
            "evidence": evidence,
        }
    )
    state["events"] = state["events"][-500:]
    state["updated_at"] = utc_now()


def transition(
    manifest: dict[str, Any],
    state: dict[str, Any],
    entity_id: str,
    action: str,
    evidence: dict[str, Any] | None = None,
    *,
    trusted_actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    evidence = dict(evidence or {})
    _validate_state_compatibility(manifest, state)
    milestones = _entity_map(manifest, "milestones")
    slices = _entity_map(manifest, "slices")
    milestone_actor = None
    transition_actor: dict[str, str] | None = None
    transition_actor_role = ""
    if entity_id in milestones:
        milestone_actor = _authorize_milestone_actor(
            manifest, state, action, trusted_actor
        )
        transition_actor = milestone_actor
        transition_actor_role = "integrator"

    if entity_id in slices:
        current = state["slices"][entity_id]
        spec = slices[entity_id]
        status = current["status"]
        transition_actor_role, transition_actor = _authorize_slice_actor(
            manifest, state, current, action, trusted_actor
        )

        if action == "start_slice":
            if status != "pending":
                raise FactoryError(f"{entity_id} is {status}, not pending")
            allowed = next_actions(manifest, state)["startable_slices"]
            if entity_id not in allowed:
                raise FactoryError(f"{entity_id} is not startable: dependency, WIP, milestone, or path-overlap gate")
            current["builder_authority"] = transition_actor
            current["status"] = "active"
            current["attempt"] += 1

        elif action == "record_failure":
            if status not in {"active", "review", "review_passed"}:
                raise FactoryError(f"cannot record failure while {entity_id} is {status}")
            _require_keys(evidence, ("failure_signature",), action)
            fingerprint = failure_fingerprint(str(evidence["failure_signature"]))
            counts = current["failure_fingerprints"]
            counts[fingerprint] = int(counts.get(fingerprint, 0)) + 1
            evidence["failure_fingerprint"] = fingerprint
            if counts[fingerprint] >= _policy(manifest, "repeated_failure_limit"):
                current["status"] = "replan_required"
                evidence["circuit_breaker"] = "repeated_failure"
            else:
                current["status"] = "active"
                current["review"] = None

        elif action == "request_review":
            if status != "active":
                raise FactoryError(f"cannot request review while {entity_id} is {status}")
            _require_keys(evidence, ("candidate_sha", "checks"), action)
            candidate_sha = _require_git_commit(manifest, evidence["candidate_sha"], "candidate_sha")
            evidence["candidate_sha"] = candidate_sha
            checks = _validate_check_receipts(
                evidence["checks"],
                workspace_path=str(manifest.get("mission", {}).get("workspace_path", "")),
                expected_criteria=_acceptance_ids(spec),
                mission_id=str(manifest.get("mission", {}).get("id", "")),
                entity_id=entity_id,
                candidate_sha=candidate_sha,
            )
            evidence["checks"] = checks
            if candidate_sha == str(current.get("last_rejected_sha") or ""):
                raise FactoryError("request_review requires a different candidate_sha after changes were requested")
            current["candidate_sha"] = candidate_sha
            current["checks"] = checks
            current["status"] = "review"

        elif action == "request_changes":
            if status != "review":
                raise FactoryError(f"cannot request changes while {entity_id} is {status}")
            _require_keys(evidence, ("findings",), action)
            current["remediation_cycles"] += 1
            current["last_rejected_sha"] = current.get("candidate_sha")
            current["review"] = {"verdict": "changes", "findings": evidence["findings"]}
            if current["remediation_cycles"] > _policy(manifest, "max_remediation_cycles"):
                current["status"] = "replan_required"
                evidence["circuit_breaker"] = "remediation_budget"
            else:
                current["status"] = "active"

        elif action == "pass_review":
            if status != "review":
                raise FactoryError(f"cannot pass review while {entity_id} is {status}")
            _require_keys(evidence, ("reviews", "candidate_sha"), action)
            reviewed_sha = _require_git_commit(manifest, evidence["candidate_sha"], "candidate_sha")
            evidence["candidate_sha"] = reviewed_sha
            if reviewed_sha != current.get("candidate_sha"):
                raise FactoryError("review candidate_sha does not match the requested-review candidate")
            reviews = _validate_reviews(manifest, spec, evidence["reviews"], reviewed_sha)
            current["review"] = {
                "verdict": "pass",
                "reviews": reviews,
                "candidate_sha": evidence["candidate_sha"],
            }
            evidence["reviews"] = reviews
            current["status"] = "review_passed"

        elif action == "complete_slice":
            review_required = bool(spec.get("review_required", False))
            allowed_states = {"review_passed"} if review_required else {"active", "review_passed"}
            if status not in allowed_states:
                raise FactoryError(f"cannot complete {entity_id} from {status}; review_required={review_required}")
            _require_keys(evidence, ("candidate_sha", "checks", "acceptance_passed"), action)
            expected = _acceptance_ids(spec)
            passed = set(map(str, evidence["acceptance_passed"]))
            missing = sorted(expected - passed)
            unknown = sorted(passed - expected)
            if missing:
                raise FactoryError(f"slice acceptance not proven: {', '.join(missing)}")
            if unknown:
                raise FactoryError(f"slice acceptance includes unknown criteria: {', '.join(unknown)}")
            completion_sha = _require_git_commit(manifest, evidence["candidate_sha"], "candidate_sha")
            evidence["candidate_sha"] = completion_sha
            checks = _validate_check_receipts(
                evidence["checks"],
                workspace_path=str(manifest.get("mission", {}).get("workspace_path", "")),
                expected_criteria=expected,
                mission_id=str(manifest.get("mission", {}).get("id", "")),
                entity_id=entity_id,
                candidate_sha=completion_sha,
            )
            evidence["checks"] = checks
            if current.get("candidate_sha") and current["candidate_sha"] != completion_sha:
                raise FactoryError("completion candidate_sha differs from reviewed candidate_sha")
            current["candidate_sha"] = completion_sha
            current["checks"] = checks
            current["acceptance_passed"] = sorted(passed)
            current["status"] = "completed"

        elif action == "block":
            if status not in {"active", "review", "review_passed"}:
                raise FactoryError(f"cannot block {entity_id} from {status}")
            _require_keys(evidence, ("reason", "owner", "resume_condition"), action)
            current["status"] = "blocked"

        elif action == "replan":
            if status not in {"active", "review", "review_passed", "blocked", "replan_required"}:
                raise FactoryError(f"cannot replan {entity_id} from {status}")
            _require_keys(evidence, ("reason", "decision"), action)
            current["status"] = "replan_required"

        else:
            raise FactoryError(f"unsupported slice action: {action}")

    elif entity_id in milestones:
        current = state["milestones"][entity_id]
        spec = milestones[entity_id]
        status = current["status"]

        if action == "start_milestone":
            if status != "pending":
                raise FactoryError(f"{entity_id} is {status}, not pending")
            if entity_id not in next_actions(manifest, state)["startable_milestones"]:
                raise FactoryError(f"{entity_id} is not startable")
            if state["integrator_authority"] is None:
                state["integrator_authority"] = milestone_actor
            current["status"] = "active"

        elif action == "validate_milestone":
            if status != "active":
                raise FactoryError(f"cannot validate {entity_id} from {status}")
            incomplete = [sid for sid in spec.get("slices", []) if state["slices"][sid]["status"] != "completed"]
            if incomplete:
                raise FactoryError(f"milestone has incomplete slices: {', '.join(incomplete)}")
            current["status"] = "validating"

        elif action == "complete_milestone":
            if status != "validating":
                raise FactoryError(f"cannot complete {entity_id} from {status}")
            _require_keys(evidence, ("acceptance_passed", "scenario_receipts", "integration_sha", "holdout_review"), action)
            expected = _acceptance_ids(spec)
            passed = set(map(str, evidence["acceptance_passed"]))
            missing = sorted(expected - passed)
            unknown = sorted(passed - expected)
            if missing:
                raise FactoryError(f"milestone acceptance not proven: {', '.join(missing)}")
            if unknown:
                raise FactoryError(f"milestone acceptance includes unknown criteria: {', '.join(unknown)}")
            integration_sha = _require_git_commit(manifest, evidence["integration_sha"], "integration_sha")
            evidence["integration_sha"] = integration_sha
            scenario_receipts = _validate_scenario_receipts(
                evidence["scenario_receipts"],
                workspace_path=str(manifest.get("mission", {}).get("workspace_path", "")),
                expected_criteria=expected,
                mission_id=str(manifest.get("mission", {}).get("id", "")),
                entity_id=entity_id,
                integration_sha=integration_sha,
            )
            evidence["scenario_receipts"] = scenario_receipts
            holdout_review = _validate_holdout_review(
                manifest, evidence["holdout_review"], integration_sha, entity_id
            )
            evidence["holdout_review"] = holdout_review
            current["acceptance_passed"] = sorted(passed)
            current["scenario_receipts"] = scenario_receipts
            current["integration_sha"] = integration_sha
            current["holdout_review"] = holdout_review
            current["status"] = "completed"

        elif action == "block":
            if status not in {"active", "validating"}:
                raise FactoryError(f"cannot block {entity_id} from {status}")
            _require_keys(evidence, ("reason", "owner", "resume_condition"), action)
            current["status"] = "blocked"

        elif action == "replan":
            if status not in {"active", "validating", "blocked", "replan_required"}:
                raise FactoryError(f"cannot replan {entity_id} from {status}")
            _require_keys(evidence, ("reason", "decision"), action)
            current["status"] = "replan_required"

        else:
            raise FactoryError(f"unsupported milestone action: {action}")

    else:
        raise FactoryError(f"unknown factory entity: {entity_id}")

    if transition_actor is None or not transition_actor_role:
        raise FactoryError(TRANSITION_AUTHORIZATION_ERROR)
    _append_event(
        state,
        entity_id,
        action,
        evidence,
        actor_role=transition_actor_role,
        actor=transition_actor,
    )
    _attest_state(state)
    return {"entity_id": entity_id, "action": action, "next": next_actions(manifest, state)}


def _card_section_has_coordinate(lines: Iterable[str]) -> bool:
    for line in lines:
        for raw_token in re.split(r"\s+", line):
            token = raw_token.strip("-*`'\"(),;:")
            if _is_workspace_relative_coordinate(token, "/"):
                return True
    return False


def _parse_card_sections(body: str) -> dict[str, list[str]]:
    """Parse canonical work-card sections using the linter's heading semantics."""
    alias_to_name = {
        alias.lower().replace(" ", ""): name
        for name, aliases in CARD_SECTIONS
        for alias in aliases
    }
    heading_pattern = re.compile(r"^\s*([A-Za-z][A-Za-z /-]*?)\s*:\s*(.*)$")
    content: dict[str, list[str]] = {name: [] for name, _ in CARD_SECTIONS}
    current_section = ""
    for line in str(body or "").splitlines():
        heading = heading_pattern.match(line)
        if heading:
            canonical = alias_to_name.get(heading.group(1).lower().replace(" ", ""))
            if canonical:
                current_section = canonical
                if heading.group(2).strip():
                    content[canonical].append(heading.group(2).strip())
                continue
            current_section = ""
        elif current_section and line.strip():
            content[current_section].append(line.strip())
    return content


def lint_card(title: str, body: str) -> dict[str, Any]:
    content = _parse_card_sections(body)
    errors = [
        f"missing or empty required section {name}:"
        for name, _ in CARD_SECTIONS
        if not any(value.strip(" -*\t") for value in content[name])
    ]
    warnings: list[str] = []
    if MICRO_TITLE.search(title.strip()):
        errors.append("title looks like a micro-remediation; keep it inside the active functional slice")
    outcome = " ".join(content["Outcome"]).strip(" -*\t")
    if MICRO_TITLE.search(outcome):
        errors.append(
            "Outcome content looks like a micro-remediation; require a durable functional result"
        )
    if content["Boundaries"] and not _card_section_has_coordinate(
        content["Boundaries"]
    ):
        errors.append(
            "Boundaries must contain at least one parseable workspace-relative path/glob coordinate"
        )
    if len(body.split()) < 45:
        warnings.append("card body is unusually thin for a durable factory work order")
    return {"valid": not errors, "errors": errors, "warnings": warnings}


@contextmanager
def _state_file_lock(state_path: Path):
    lock_path = state_path.parent.parent / f".{state_path.parent.name}.state.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write("0")
        handle.flush()
    handle.seek(0)
    try:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except ImportError:  # pragma: no cover - Windows fallback
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        yield
    finally:
        try:
            try:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except ImportError:  # pragma: no cover - Windows fallback
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            handle.close()


def save_transition(
    manifest_path: str | Path,
    state_path: str | Path,
    entity_id: str,
    action: str,
    evidence: dict[str, Any] | None = None,
    expected_revision: int | None = None,
    *,
    trusted_actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = Path(state_path).expanduser().resolve()
    with _state_file_lock(path):
        if not path.is_file():
            raise FactoryError(
                "active factory state is unavailable; validate the factory and re-arm if initialization is required"
            )
        manifest = load_manifest(manifest_path)
        check = validate_manifest(manifest)
        if not check["valid"]:
            raise FactoryError("invalid manifest: " + "; ".join(check["errors"]))
        state = _read_json(path)
        _validate_state_compatibility(manifest, state)
        if expected_revision is not None and state["revision"] != expected_revision:
            raise FactoryError(f"state revision conflict: expected {expected_revision}, found {state['revision']}")
        result = transition(
            manifest,
            state,
            entity_id,
            action,
            evidence,
            trusted_actor=trusted_actor,
        )
        state["revision"] += 1
        _attest_state(state)
        _write_json_atomic(path, state)
    result["revision"] = state["revision"]
    result["state_path"] = str(path)
    return result
