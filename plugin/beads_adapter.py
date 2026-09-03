"""Safe Beads graph projection for Dark Factory mission manifests.

Beads owns the durable work/dependency graph.  The Dark Factory ledger remains
canonical for acceptance, evidence, review attestations, WIP and replan gates.
This adapter never initializes, pushes, pulls, syncs, or edits an existing node.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

try:
    from .engine import _state_file_lock, validate_manifest
except ImportError:  # pragma: no cover - dashboard sibling-module loading
    from engine import _state_file_lock, validate_manifest  # type: ignore


class BeadsAdapterError(RuntimeError):
    """A fail-closed Beads planning or application error."""


SUPPORTED_BEADS_CLI_VERSION = "1.2.2"
_BEADS_VERSION_ERROR = "unsupported Beads CLI version"
_BEADS_VERSION_OUTPUT = re.compile(
    r"^bd version (?P<version>\d+\.\d+\.\d+)(?: \([0-9A-Fa-f]{7,64}\))?$"
)


def _resolve_bd_executable(value: str) -> str:
    """Resolve bd for desktop-launched WSL processes without broad PATH use."""
    requested = str(value or "bd").strip() or "bd"
    candidates: list[Path] = []
    if os.sep in requested or (os.altsep and os.altsep in requested):
        candidates.append(Path(requested).expanduser())
    else:
        found = shutil.which(requested)
        if found:
            candidates.append(Path(found))
        # Hermes desktop may be launched outside the interactive shell that
        # prepends Bun/Go user bins to PATH. Keep this bounded to user-owned,
        # conventional install locations; never search the whole filesystem.
        home = Path.home()
        candidates.extend([
            home / ".bun" / "bin" / requested,
            home / ".local" / "bin" / requested,
            home / "go" / "bin" / requested,
        ])
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if resolved.is_file() and os.access(resolved, os.X_OK):
            return str(resolved)
    return ""


def _canonical_json_text(value: Any) -> str:
    """Return deterministic UTF-8 JSON text suitable for Beads string metadata."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _canonical_digest(value: Any) -> str:
    raw = _canonical_json_text(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _slug(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-") or "item"


def _entity_keys(prefix: str, values: list[str]) -> dict[str, str]:
    """Return readable keys while disambiguating IDs with the same slug.

    Beads graph keys are symbolic external identities. Distinct manifest IDs
    such as ``M1-S1`` and ``M1_S1`` must never collapse to one key.
    """
    if len(set(values)) != len(values):
        raise BeadsAdapterError(f"duplicate {prefix} entity ids cannot be projected")
    bases = {value: f"{prefix}-{_slug(value)}" for value in values}
    counts: dict[str, int] = {}
    for base in bases.values():
        counts[base] = counts.get(base, 0) + 1
    result: dict[str, str] = {}
    for value, base in bases.items():
        if counts[base] == 1:
            result[value] = base
            continue
        # Base32 is a reversible, collision-free encoding of the complete raw
        # UTF-8 ID. A truncated digest can collide under adversarial IDs.
        encoded = base64.b32encode(value.encode("utf-8")).decode("ascii").rstrip("=").lower()
        result[value] = f"{base}-{encoded}"
    return result


def _criteria_text(values: Any, *, include_type: bool = False) -> str:
    rows: list[str] = []
    if isinstance(values, list):
        for item in values:
            if isinstance(item, dict):
                cid = str(item.get("id") or "").strip()
                statement = str(item.get("statement") or "").strip()
                if cid and statement:
                    criterion_type = str(item.get("type") or "").strip().lower()
                    type_marker = f" ({criterion_type})" if include_type else ""
                    rows.append(f"- [{cid}]{type_marker} {statement}")
    return "\n".join(rows)


def _bullet_text(values: Any) -> str:
    if not isinstance(values, list):
        return ""
    return "\n".join(f"- {str(value).strip()}" for value in values if str(value).strip())


def _slice_description(manifest: dict[str, Any], item: dict[str, Any]) -> str:
    sid = str(item.get("id") or "")
    mid = str(item.get("milestone_id") or "")
    milestone = next(
        (
            candidate
            for candidate in manifest.get("milestones", [])
            if isinstance(candidate, dict) and str(candidate.get("id") or "") == mid
        ),
        {},
    )
    decisions = {
        str(decision.get("id") or ""): decision
        for decision in manifest.get("decisions", [])
        if isinstance(decision, dict)
    }
    decision_rows = [
        f"[{decision_id}] {str(decisions.get(str(decision_id), {}).get('statement') or '').strip()}"
        for decision_id in (
            item.get("requires_decisions", [])
            if isinstance(item.get("requires_decisions", []), list)
            else []
        )
    ]
    decision_text = "; ".join(decision_rows) or "None declared for this block; do not infer additional shared decisions."
    story_ids = [
        str(value).strip()
        for value in (item.get("story_ids") if isinstance(item.get("story_ids"), list) else [item.get("story_id")])
        if str(value).strip()
    ]
    paths = [str(path).strip() for path in item.get("paths", []) if str(path).strip()]
    surfaces = [
        str(surface).strip()
        for surface in manifest.get("mission", {}).get("surfaces", [])
        if str(surface).strip()
    ]
    acceptance = _criteria_text(item.get("acceptance"), include_type=True)
    evidence = _bullet_text(item.get("evidence"))
    review_roles = [str(role).strip() for role in item.get("review_roles", []) if str(role).strip()]
    policy = manifest.get("policy", {})
    repeated_failure_limit = policy.get("repeated_failure_limit")
    remediation_limit = policy.get("max_remediation_cycles")

    return (
        f"Factory-Milestone:\n[{mid}] {str(milestone.get('outcome') or '').strip()}\n\n"
        f"Factory-Functional-Block:\n[{sid}] {str(item.get('outcome') or '').strip()}\n\n"
        f"Owned stories:\n{', '.join(story_ids)}\n\n"
        f"Outcome:\n{str(item.get('outcome') or '').strip()}\n\n"
        "Boundaries:\n"
        f"- Allowed paths/interfaces: {', '.join(paths)}\n"
        f"- Declared interaction surfaces: {', '.join(surfaces)}\n"
        f"- Shared locked decisions: {decision_text}\n\n"
        f"Acceptance:\n{acceptance}\n\n"
        f"Evidence:\n{evidence}\n\n"
        "Forbidden:\n"
        "- Do not write outside the allowed paths/interfaces or override a locked decision.\n"
        "- Do not create micro-beads for thin slices, debugging, test fixes, remediation, or review comments; keep that work in this block.\n"
        "- Do not treat card completion, a generic done, or a green build alone as acceptance proof.\n\n"
        "Handoff:\n"
        f"- Submit the candidate SHA, changed allowed paths, and exact receipts mapped to the criterion IDs.\n"
        f"- Return the completed block to the integrator for milestone {mid}; independent review occurs at milestone delivery{(' by ' + ', '.join(review_roles)) if review_roles else ''}.\n\n"
        "Continue:\n"
        f"- After a local review rejection, use the next continuation descriptor for this block and produce a new candidate; do not end the mission while bounded continuation remains.\n\n"
        "Stop / escalate:\n"
        f"- Continue up to {remediation_limit} bounded remediation cycles after the initial candidate. Then escalate instead of retrying unchanged work; materially similar failure {repeated_failure_limit} times also stops the block.\n"
        "- Stop on path overlap, unavailable evidence commands/scenarios, contradictory receipts, or conflict with a shared locked decision."
    )


def _model_hint(manifest: dict[str, Any], role: str, execution_role: str) -> dict[str, str]:
    ref = manifest.get("models", {}).get(role, {})
    return {
        "configured_role": role,
        "execution_role": execution_role,
        "provider": str(ref.get("provider") or ""),
        "model": str(ref.get("model") or ""),
    }


def _node(
    manifest: dict[str, Any],
    *,
    digest: str,
    key: str,
    entity_type: str,
    entity_id: str,
    title: str,
    issue_type: str,
    description: str,
    acceptance: Any,
    parent_key: str | None,
    role: str,
    execution_role: str,
    parallel_group: str,
) -> dict[str, Any]:
    mission_id = str(manifest.get("mission", {}).get("id") or "")
    graph_namespace = _canonical_digest({"mission_id": mission_id})[:24]
    node: dict[str, Any] = {
        "key": key,
        "title": title,
        "type": issue_type,
        "description": description,
        "priority": 1 if issue_type == "epic" else 2,
        "labels": ["dark-factory", f"dark-factory:{entity_type}"],
        "metadata": {
            "dark_factory_manifest_digest": digest,
            "dark_factory_mission_id": mission_id,
            "dark_factory_entity_type": entity_type,
            "dark_factory_entity_id": entity_id,
            # Stable across mutable manifest revisions so a crash after Beads
            # apply but before receipt publication cannot create duplicates.
            # The full manifest digest remains separate evidence metadata.
            "dark_factory_graph_ref": f"dark-factory:{graph_namespace}:{entity_type}:{entity_id}",
            # Beads v1.2.2 declares graph metadata as map[string]string. Keep
            # the full typed rows by storing their canonical JSON encoding,
            # rather than weakening the graph contract to accept JSON arrays.
            "dark_factory_acceptance": _canonical_json_text(acceptance),
            "execution_agent_type": execution_role,
            "execution_configured_role": role,
            "execution_suggested_model": "/".join(
                value for value in (_model_hint(manifest, role, execution_role)["provider"], _model_hint(manifest, role, execution_role)["model"]) if value
            ),
            "execution_provider": _model_hint(manifest, role, execution_role)["provider"],
            "execution_model": _model_hint(manifest, role, execution_role)["model"],
            "execution_reasoning_effort": str(
                manifest.get("execution", {}).get("reasoning_effort", {}).get(execution_role) or
                ("high" if execution_role == "orchestrator" else "medium")
            ),
            "execution_mode": "dark-factory",
            "execution_parallel_group": parallel_group,
        },
    }
    if parent_key:
        node["parent_key"] = parent_key
    return node


def build_graph_plan(manifest: dict[str, Any], *, validate: bool = True) -> dict[str, Any]:
    """Compile one mission/milestone/slice manifest into a deterministic Beads plan."""
    if validate:
        result = validate_manifest(manifest)
        if not result.get("valid"):
            raise BeadsAdapterError("invalid Dark Factory manifest: " + "; ".join(result.get("errors", [])))
    digest = _canonical_digest(manifest)
    mission = manifest.get("mission", {})
    mission_id = str(mission.get("id") or "mission")
    mission_key = f"mission-{_slug(mission_id)}"
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []

    nodes.append(_node(
        manifest,
        digest=digest,
        key=mission_key,
        entity_type="mission",
        entity_id=mission_id,
        title=str(mission.get("name") or mission_id),
        issue_type="epic",
        description=(
            f"Problem:\n{mission.get('problem', '')}\n\n"
            f"Outcome:\n{mission.get('outcome', '')}\n\n"
            f"Acceptance:\n{_bullet_text(mission.get('success_metrics'))}\n\n"
            "Forbidden:\nDo not expand beyond the manifest non-goals and locked authority decisions.\n\n"
            "Stop / escalate:\nStop at human gates, exhausted remediation, or contradictory evidence."
        ),
        acceptance=mission.get("success_metrics", []),
        parent_key=None,
        role="integrator",
        execution_role="orchestrator",
        parallel_group=mission_id,
    ))

    milestone_items = [item for item in manifest.get("milestones", []) if isinstance(item, dict)]
    milestone_keys = _entity_keys("milestone", [str(item.get("id")) for item in milestone_items])
    for item in milestone_items:
        mid = str(item.get("id"))
        key = milestone_keys[mid]
        nodes.append(_node(
            manifest,
            digest=digest,
            key=key,
            entity_type="milestone",
            entity_id=mid,
            title=str(item.get("title") or item.get("outcome") or mid),
            issue_type="epic",
            description=(
                f"Outcome:\n{item.get('outcome', '')}\n\n"
                f"Acceptance:\n{_criteria_text(item.get('acceptance'), include_type=True)}\n\n"
                "Evidence:\nExact integration commit, factory-owned integration checks, scenario artifacts, and holdout receipt.\n\n"
                "Forbidden:\nDo not accept card completion or a green build as milestone proof.\n\n"
                "Stop / escalate:\nStop on missing, stale, substituted, forged, or contradictory evidence."
            ),
            acceptance=item.get("acceptance", []),
            parent_key=mission_key,
            role="integrator",
            execution_role="orchestrator",
            parallel_group=mid,
        ))

    slice_items = [item for item in manifest.get("slices", []) if isinstance(item, dict)]
    slice_keys = _entity_keys("slice", [str(item.get("id")) for item in slice_items])
    for item in slice_items:
        sid = str(item.get("id"))
        mid = str(item.get("milestone_id"))
        key = slice_keys[sid]
        nodes.append(_node(
            manifest,
            digest=digest,
            key=key,
            entity_type="functional_block",
            entity_id=sid,
            title=str(item.get("outcome") or sid),
            issue_type="task",
            description=_slice_description(manifest, item),
            acceptance=item.get("acceptance", []),
            parent_key=milestone_keys.get(mid),
            role="builder",
            execution_role="worker",
            parallel_group=mid,
        ))

    for item in manifest.get("milestones", []):
        current = milestone_keys.get(str(item.get("id")))
        for prerequisite in item.get("depends_on", []):
            target = milestone_keys.get(str(prerequisite))
            if current and target:
                edges.append({"from_key": current, "to_key": target, "type": "blocks"})
    for item in manifest.get("slices", []):
        current = slice_keys.get(str(item.get("id")))
        for prerequisite in item.get("depends_on", []):
            target = slice_keys.get(str(prerequisite))
            if current and target:
                edges.append({"from_key": current, "to_key": target, "type": "blocks"})

    edges.sort(key=lambda row: (row["from_key"], row["to_key"], row["type"]))
    return {
        "commit_message": f"Dark Factory graph: {mission_id} ({digest[:12]})",
        "nodes": nodes,
        "edges": edges,
    }


def graph_plan_json(plan: dict[str, Any]) -> str:
    return _canonical_json_text(plan)


def _validate_graph_plan_metadata(plan: dict[str, Any]) -> None:
    """Fail before Beads sees metadata outside its v1.2.2 string schema."""
    nodes = plan.get("nodes")
    if not isinstance(nodes, list):
        raise BeadsAdapterError("Beads graph plan nodes must be a list")
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise BeadsAdapterError(f"Beads graph plan node {index} is malformed")
        metadata = node.get("metadata")
        if not isinstance(metadata, dict):
            raise BeadsAdapterError(f"Beads graph plan metadata must be an object for node {index}")
        for key, value in metadata.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise BeadsAdapterError(
                    f"Beads graph plan metadata keys and values must be strings for node {index}"
                )


_BD_ENV_ALLOWLIST = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "HOME",
    "USERPROFILE",
    "TMPDIR",
    "TMP",
    "TEMP",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "LANG",
    "LC_ALL",
)
_ERROR_DETAIL_LIMIT = 1024


def _minimal_bd_environment(beads_dir: Path) -> dict[str, str]:
    env = {key: os.environ[key] for key in _BD_ENV_ALLOWLIST if os.environ.get(key)}
    env["BEADS_DIR"] = str(beads_dir)
    env["BD_DISABLE_METRICS"] = "1"
    env["BD_NO_DAEMON"] = "1"
    return env


def _safe_error_detail(value: Any) -> str:
    detail = str(value or "unknown error")
    detail = re.sub(
        r"-----BEGIN(?: [A-Z]+)* PRIVATE KEY-----.*?-----END(?: [A-Z]+)* PRIVATE KEY-----",
        "[REDACTED]",
        detail,
        flags=re.DOTALL,
    )
    detail = re.sub(r"(?i)\bAuthorization\s*:\s*Basic\s+\S+", "Authorization: Basic [REDACTED]", detail)
    detail = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [REDACTED]", detail)
    detail = re.sub(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/:]+:[^\s/@]+@", "[REDACTED]@", detail)
    detail = re.sub(
        r"(?i)(?<![A-Za-z0-9])"
        r"([A-Za-z0-9_.-]*(?:api[_ -]?key|token|password|secret|credential|connection[_ -]?string|private[_ -]?key)[A-Za-z0-9_.-]*)"
        r"(\s*[:=]\s*)"
        r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]",
        detail,
    )
    detail = re.sub(r"(?i)(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{12,}", "[REDACTED]", detail)
    detail = re.sub(r"(?i)\bgh[pousr]_[A-Za-z0-9]{20,}\b", "[REDACTED]", detail)
    if len(detail) > _ERROR_DETAIL_LIMIT:
        detail = detail[:_ERROR_DETAIL_LIMIT] + "…[truncated]"
    return detail


def _run_bd(
    executable: str,
    argv: list[str],
    beads_dir: Path,
    *,
    timeout: int = 20,
) -> Any:
    env = _minimal_bd_environment(beads_dir)
    try:
        result = subprocess.run(
            [executable, *argv],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BeadsAdapterError(f"Beads command failed to start: {_safe_error_detail(exc)}") from exc
    if result.returncode != 0:
        detail = _safe_error_detail((result.stderr or result.stdout or "unknown error").strip())
        command_hint = _safe_error_detail(" ".join(argv[:2]))
        raise BeadsAdapterError(f"Beads command failed ({command_hint}): {detail}")
    text = result.stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def preflight_beads(
    beads_dir: str | Path,
    *,
    bd_executable: str = "bd",
    authorize_isolated: bool = False,
) -> dict[str, Any]:
    path = Path(beads_dir).expanduser().resolve()
    executable = _resolve_bd_executable(bd_executable)
    if not executable or not Path(executable).is_file() or not os.access(executable, os.X_OK):
        raise BeadsAdapterError("Beads executable is unavailable")
    # A directory name is not proof of an initialized store.  In particular,
    # an empty directory named `.beads` must still require explicit isolated
    # authorization; the subsequent graph dry-run is the authoritative Beads
    # compatibility check and no `bd init` is ever invoked here.
    existing_store = path.is_dir() and any(path.iterdir())
    if not existing_store and not authorize_isolated:
        raise BeadsAdapterError("target is not an existing .beads directory; explicitly authorize an isolated directory")
    if not path.exists():
        raise BeadsAdapterError("Beads directory does not exist; initialize it explicitly before applying a graph")
    if not path.is_dir():
        raise BeadsAdapterError("Beads directory is not a directory")
    version_output = _run_bd(executable, ["--version"], path)
    match = _BEADS_VERSION_OUTPUT.fullmatch(str(version_output).strip())
    if not match or match.group("version") != SUPPORTED_BEADS_CLI_VERSION:
        raise BeadsAdapterError(_BEADS_VERSION_ERROR)
    try:
        _run_bd(executable, ["list", "--json", "--limit", "0", "--all"], path)
    except BeadsAdapterError as exc:
        raise BeadsAdapterError(
            "target is not a readable initialized Beads store; run bd init explicitly outside the adapter"
        ) from exc
    return {
        "beads_dir": str(path),
        "bd_executable": executable,
        "bd_version": SUPPORTED_BEADS_CLI_VERSION,
    }


def _with_plan_file(plan: dict[str, Any], callback: Any) -> Any:
    _validate_graph_plan_metadata(plan)
    fd, raw = tempfile.mkstemp(prefix="dark-factory-beads-", suffix=".json")
    path = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(plan, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return callback(path)
    finally:
        path.unlink(missing_ok=True)


def _validate_dry_run_output(plan: dict[str, Any], output: Any) -> None:
    """Validate the graph coverage exposed by Beads 1.2.2 dry-run JSON.

    Beads 1.2.2 exposes every node's key/title/type/priority/parent_key and
    aggregate edge/parent counts, but not individual explicit edge identities,
    descriptions, labels, or metadata. Those unexposed fields are verified
    from the live store after apply instead of being inferred here.
    """
    if not isinstance(output, dict) or output.get("dry_run") is not True or output.get("schema_version") != 1:
        raise BeadsAdapterError("Beads dry-run response is malformed or has an unsupported schema")

    expected_nodes = {
        str(node.get("key") or ""): node
        for node in plan.get("nodes", [])
        if isinstance(node, dict) and str(node.get("key") or "")
    }
    rows = output.get("nodes")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise BeadsAdapterError("Beads dry-run response is missing planned node coverage")
    actual_nodes: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("key") or "")
        if not key or key in actual_nodes:
            raise BeadsAdapterError("Beads dry-run response has malformed or duplicate node keys")
        actual_nodes[key] = row
    if set(actual_nodes) != set(expected_nodes):
        raise BeadsAdapterError("Beads dry-run node coverage does not exactly match the plan")

    for key, expected in expected_nodes.items():
        actual = actual_nodes[key]
        for field in ("title", "type", "priority"):
            if field not in actual or actual[field] != expected.get(field):
                raise BeadsAdapterError(f"Beads dry-run node contract mismatch for key {key}: {field}")
        expected_parent = expected.get("parent_key")
        actual_parent = actual.get("parent_key")
        if actual_parent != expected_parent:
            raise BeadsAdapterError(f"Beads dry-run parent coverage mismatch for key {key}")

    expected_edges = [edge for edge in plan.get("edges", []) if isinstance(edge, dict)]
    expected_parent_count = sum(1 for node in expected_nodes.values() if node.get("parent_key"))
    counts = {
        "node_count": len(expected_nodes),
        "edge_count": len(expected_edges),
        "parent_deps": expected_parent_count,
    }
    for field, expected in counts.items():
        if type(output.get(field)) is not int or output[field] != expected:
            raise BeadsAdapterError(f"Beads dry-run response has invalid {field}")

    # Some compatible implementations may return edge identities in addition
    # to the v1.2.2 counts. If present, consume them rather than discarding them.
    if "edges" in output:
        rows = output["edges"]
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise BeadsAdapterError("Beads dry-run response has malformed edge coverage")
        expected_set = {
            (str(edge.get("from_key") or ""), str(edge.get("to_key") or ""), str(edge.get("type") or ""))
            for edge in expected_edges
        }
        actual_set = {
            (str(edge.get("from_key") or ""), str(edge.get("to_key") or ""), str(edge.get("type") or ""))
            for edge in rows
        }
        if len(actual_set) != len(rows) or actual_set != expected_set:
            raise BeadsAdapterError("Beads dry-run edge coverage does not exactly match the plan")


def dry_run_graph_plan(
    plan: dict[str, Any],
    beads_dir: str | Path,
    *,
    bd_executable: str = "bd",
    authorize_isolated: bool = False,
) -> dict[str, Any]:
    _validate_graph_plan_metadata(plan)
    preflight = preflight_beads(beads_dir, bd_executable=bd_executable, authorize_isolated=authorize_isolated)
    path = Path(preflight["beads_dir"])
    output = _with_plan_file(
        plan,
        lambda plan_path: _run_bd(preflight["bd_executable"], ["create", "--graph", str(plan_path), "--dry-run", "--json"], path),
    )
    _validate_dry_run_output(plan, output)
    return {**preflight, "dry_run": output, "plan_digest": _canonical_digest(plan)}


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(raw, path)
    finally:
        Path(raw).unlink(missing_ok=True)


def _verify_graph(
    executable: str,
    beads_dir: Path,
    ids: dict[str, str],
    nodes: list[dict[str, Any]],
    edges: list[dict[str, str]],
) -> None:
    if not ids or any(not isinstance(value, str) or not value.strip() for value in ids.values()):
        raise BeadsAdapterError("Beads apply returned an incomplete ID mapping")
    expected_keys = {str(node.get("key") or "") for node in nodes if isinstance(node, dict)}
    if set(ids) != expected_keys:
        raise BeadsAdapterError("Beads graph ID mapping does not exactly cover the planned nodes")
    if len(set(ids.values())) != len(ids):
        raise BeadsAdapterError("Beads graph ID mapping does not assign a unique ID to every planned node")
    rows = _run_bd(executable, ["show", *ids.values(), "--json"], beads_dir)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise BeadsAdapterError("Beads graph verification returned malformed node read-back")
    found_rows: dict[str, dict[str, Any]] = {}
    for row in rows:
        bead_id = str(row.get("id") or "")
        if not bead_id or bead_id in found_rows:
            raise BeadsAdapterError("Beads graph verification returned missing or duplicate node IDs")
        found_rows[bead_id] = row
    if set(found_rows) != set(ids.values()):
        raise BeadsAdapterError("Beads graph verification does not exactly cover the applied IDs")

    nodes_by_key = {str(node.get("key") or ""): node for node in nodes if isinstance(node, dict)}
    identity_metadata_fields = (
        "dark_factory_manifest_digest",
        "dark_factory_graph_ref",
        "dark_factory_mission_id",
        "dark_factory_entity_type",
        "dark_factory_entity_id",
    )
    for key, bead_id in ids.items():
        expected = nodes_by_key[key]
        actual = found_rows[bead_id]
        scalar_fields = {
            "title": "title",
            "type": "issue_type",
            "description": "description",
            "priority": "priority",
        }
        for expected_field, actual_field in scalar_fields.items():
            if actual.get(actual_field) != expected.get(expected_field):
                raise BeadsAdapterError(
                    f"Beads graph verification node contract mismatch for key {key}: {expected_field}"
                )

        expected_labels = expected.get("labels")
        actual_labels = actual.get("labels")
        if (
            not isinstance(expected_labels, list)
            or not isinstance(actual_labels, list)
            or sorted(actual_labels) != sorted(expected_labels)
        ):
            raise BeadsAdapterError(f"Beads graph verification node contract mismatch for key {key}: labels")

        expected_metadata = expected.get("metadata")
        actual_metadata = actual.get("metadata")
        if not isinstance(expected_metadata, dict) or not isinstance(actual_metadata, dict):
            raise BeadsAdapterError(f"Beads graph verification metadata mismatch for key {key}")
        if any(actual_metadata.get(field) != expected_metadata.get(field) for field in identity_metadata_fields):
            raise BeadsAdapterError(f"Beads graph verification identity mismatch for key {key} and ID {bead_id}")
        expected_acceptance = expected_metadata.get("dark_factory_acceptance")
        actual_acceptance = actual_metadata.get("dark_factory_acceptance")
        for source, encoded in (("planned", expected_acceptance), ("read-back", actual_acceptance)):
            if not isinstance(encoded, str):
                raise BeadsAdapterError(
                    f"Beads graph verification {source} acceptance metadata is not a string for key {key}"
                )
            try:
                decoded = json.loads(encoded)
            except json.JSONDecodeError as exc:
                raise BeadsAdapterError(
                    f"Beads graph verification {source} acceptance metadata is malformed for key {key}"
                ) from exc
            if not isinstance(decoded, list) or _canonical_json_text(decoded) != encoded:
                raise BeadsAdapterError(
                    f"Beads graph verification {source} acceptance metadata is noncanonical for key {key}"
                )
        if actual_acceptance != expected_acceptance:
            raise BeadsAdapterError(f"Beads graph verification acceptance metadata mismatch for key {key}")
        if actual_metadata != expected_metadata:
            raise BeadsAdapterError(
                f"Beads graph verification metadata mismatch / routing metadata mismatch for key {key} and ID {bead_id}"
            )

        parent_key = expected.get("parent_key")
        expected_parent = ids.get(str(parent_key)) if parent_key else None
        if actual.get("parent") != expected_parent:
            raise BeadsAdapterError(f"Beads graph verification parent relationship mismatch for key {key}")

    expected_relations = {
        (ids[str(edge.get("from_key"))], ids[str(edge.get("to_key"))], str(edge.get("type") or ""))
        for edge in edges
    }
    expected_relations.update(
        (ids[str(node["key"])], ids[str(node["parent_key"])], "parent-child")
        for node in nodes
        if node.get("parent_key")
    )
    expected_parent_relations = {row for row in expected_relations if row[2] == "parent-child"}

    actual_by_direction: dict[str, set[tuple[str, str, str]]] = {}
    for direction in ("down", "up"):
        observed: list[tuple[str, str, str]] = []
        for bead_id in sorted(ids.values()):
            dependency_rows = _run_bd(
                executable,
                ["dep", "list", bead_id, f"--direction={direction}", "--json"],
                beads_dir,
            )
            if not isinstance(dependency_rows, list) or any(not isinstance(row, dict) for row in dependency_rows):
                raise BeadsAdapterError("Beads graph verification returned malformed dependency read-back")
            for row in dependency_rows:
                other_id = row.get("id")
                dependency_type = row.get("dependency_type")
                if not isinstance(other_id, str) or not other_id or not isinstance(dependency_type, str) or not dependency_type:
                    raise BeadsAdapterError("Beads graph verification returned malformed dependency records")
                if direction == "down":
                    observed.append((bead_id, other_id, dependency_type))
                else:
                    observed.append((other_id, bead_id, dependency_type))
        observed_set = set(observed)
        if len(observed_set) != len(observed):
            raise BeadsAdapterError("Beads graph verification returned duplicate dependency records")
        actual_by_direction[direction] = observed_set

    for observed in actual_by_direction.values():
        observed_parents = {row for row in observed if row[2] == "parent-child"}
        if observed_parents != expected_parent_relations:
            raise BeadsAdapterError("Beads graph verification parent relationship mismatch")
        if observed != expected_relations:
            raise BeadsAdapterError("Beads graph verification blocker/dependency edge mismatch")


def apply_graph_plan(
    manifest: dict[str, Any],
    beads_dir: str | Path,
    *,
    bd_executable: str = "bd",
    authorize_isolated: bool = False,
    validate: bool = True,
) -> dict[str, Any]:
    plan = build_graph_plan(manifest, validate=validate)
    _validate_graph_plan_metadata(plan)
    manifest_digest = _canonical_digest(manifest)
    plan_digest = _canonical_digest(plan)
    preflight = preflight_beads(beads_dir, bd_executable=bd_executable, authorize_isolated=authorize_isolated)
    target = Path(preflight["beads_dir"])
    workspace = Path(str(manifest.get("mission", {}).get("workspace_path") or "")).expanduser().resolve()
    receipt_path = workspace / ".hermes" / "factory" / "beads-graph-receipt.json"

    with _state_file_lock(receipt_path):
        if receipt_path.exists():
            try:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise BeadsAdapterError(f"Beads receipt is unreadable: {exc}") from exc
            if (
                receipt.get("manifest_digest") != manifest_digest
                or receipt.get("plan_digest") != plan_digest
                or receipt.get("beads_dir") != str(target)
            ):
                raise BeadsAdapterError("Beads graph receipt does not match this manifest, plan, or directory")
            ids = receipt.get("ids") if isinstance(receipt.get("ids"), dict) else {}
            _verify_graph(preflight["bd_executable"], target, ids, plan["nodes"], plan["edges"])
            return {**preflight, "applied": False, "idempotent_replay": True, "ids": ids, "receipt_path": str(receipt_path)}

        # Closed nodes still own their external identities. Include them so a
        # lost receipt cannot make a completed mission safe to recreate.
        existing = _run_bd(preflight["bd_executable"], ["list", "--json", "--limit", "0", "--all"], target)
        refs = {node["metadata"]["dark_factory_graph_ref"] for node in plan["nodes"]}
        collisions = [
            row
            for row in existing
            if isinstance(row, dict)
            and isinstance(row.get("metadata"), dict)
            and str(row["metadata"].get("dark_factory_graph_ref") or "") in refs
        ] if isinstance(existing, list) else []
        if collisions:
            raise BeadsAdapterError("Beads directory already contains Dark Factory graph nodes but no matching receipt")

        dry_run_output = _with_plan_file(
            plan,
            lambda plan_path: _run_bd(preflight["bd_executable"], ["create", "--graph", str(plan_path), "--dry-run", "--json"], target),
        )
        _validate_dry_run_output(plan, dry_run_output)
        output = _with_plan_file(
            plan,
            lambda plan_path: _run_bd(preflight["bd_executable"], ["create", "--graph", str(plan_path), "--json"], target),
        )
        ids = output.get("ids") if isinstance(output, dict) and isinstance(output.get("ids"), dict) else {}
        expected = {node["key"] for node in plan["nodes"]}
        if not isinstance(output, dict) or output.get("schema_version") != 1 or set(ids) != expected:
            raise BeadsAdapterError("Beads apply returned an unexpected graph ID mapping")
        _verify_graph(preflight["bd_executable"], target, ids, plan["nodes"], plan["edges"])
        receipt = {
            "schema_version": 1,
            "manifest_digest": manifest_digest,
            "plan_digest": plan_digest,
            "beads_dir": str(target),
            "ids": ids,
        }
        _atomic_write(receipt_path, receipt)
        return {**preflight, "applied": True, "idempotent_replay": False, "ids": ids, "receipt_path": str(receipt_path)}
