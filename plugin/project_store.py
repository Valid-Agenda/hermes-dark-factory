"""Project-scoped configuration and observability for Dark Factory.

Hermes owns project identity in its per-profile ``projects.db``.  This module
only stores Dark Factory's global defaults and sparse project overrides, and
reads the factory manifest/state already published inside each project
workspace.  It deliberately does not create a second scheduler or task graph.
"""

from __future__ import annotations

import copy
import datetime as _datetime
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable

try:
    from .intake import (
        FactoryError,
        MODEL_ROLES,
        MODEL_REFERENCE_FIELDS,
        MODEL_POLICY_FIELDS,
        POLICY_FIELDS,
        REASONING_FIELDS,
        _credential_shaped_paths,
        _redact_sensitive_values,
        default_setup,
        normalise_setup,
        plugin_data_dir,
    )
except ImportError:  # Dashboard loader may import the module outside a package.
    from intake import (  # type: ignore
        FactoryError,
        MODEL_ROLES,
        MODEL_REFERENCE_FIELDS,
        MODEL_POLICY_FIELDS,
        POLICY_FIELDS,
        REASONING_FIELDS,
        _credential_shaped_paths,
        _redact_sensitive_values,
        default_setup,
        normalise_setup,
        plugin_data_dir,
    )

CONFIG_SCHEMA_VERSION = 1
PROJECTS_SCHEMA_VERSION = 1
PROMPT_MAX_CHARS = 16000
COORDINATION_MODES = ("beads",)
REASONING_EFFORTS = ("low", "medium", "high")

_GLOBAL_FIELDS = frozenset(
    {
        "schema_version",
        "models",
        "model_policy",
        "system_prompts",
        "coordination",
        "reasoning_effort",
        "policy",
    }
)
_COORDINATION_FIELDS = frozenset(
    {"mode", "beads_directory", "beads_isolated_authorized"}
)
_OVERRIDE_FIELDS = _GLOBAL_FIELDS - {"schema_version"}


def _deep_merge(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _read_object(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _unknown_fields(value: Any, allowed: Iterable[str], prefix: str) -> list[str]:
    if not isinstance(value, dict):
        return []
    allowed_set = set(allowed)
    return [f"{prefix}.{key}" for key in value if str(key) not in allowed_set]


def default_global_config() -> dict[str, Any]:
    setup = default_setup()
    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "models": copy.deepcopy(setup["models"]),
        "model_policy": copy.deepcopy(setup["model_policy"]),
        "system_prompts": copy.deepcopy(setup["system_prompts"]),
        "coordination": {
            "mode": "beads",
            "beads_directory": "",
            "beads_isolated_authorized": False,
        },
        "reasoning_effort": copy.deepcopy(setup["execution"]["reasoning_effort"]),
        "policy": copy.deepcopy(setup["policy"]),
    }


def _normalise_models(value: Any, *, partial: bool) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict):
        if partial:
            return {}
        return copy.deepcopy(default_global_config()["models"])
    unknown = _unknown_fields(value, MODEL_ROLES, "models")
    if unknown:
        raise FactoryError("config contains unknown model role(s): " + ", ".join(unknown))
    result: dict[str, dict[str, str]] = {}
    for role, reference in value.items():
        if not isinstance(reference, dict):
            raise FactoryError(f"models.{role} must be an object")
        fields = _unknown_fields(reference, MODEL_REFERENCE_FIELDS, f"models.{role}")
        if fields:
            raise FactoryError("config contains unknown model field(s): " + ", ".join(fields))
        provider = _text(reference.get("provider")).lower()
        model = _text(reference.get("model"))
        result[str(role)] = {"provider": provider, "model": model}
    if not partial:
        for role in MODEL_ROLES:
            result.setdefault(role, {"provider": "", "model": ""})
    return result


def _normalise_prompts(value: Any, *, partial: bool) -> dict[str, str]:
    if not isinstance(value, dict):
        if partial:
            return {}
        return {role: "" for role in MODEL_ROLES}
    unknown = _unknown_fields(value, MODEL_ROLES, "system_prompts")
    if unknown:
        raise FactoryError("config contains unknown system prompt role(s): " + ", ".join(unknown))
    result: dict[str, str] = {}
    for role, prompt in value.items():
        if not isinstance(prompt, str):
            raise FactoryError(f"system_prompts.{role} must be a string")
        prompt = prompt.strip()
        if len(prompt) > PROMPT_MAX_CHARS:
            raise FactoryError(f"system_prompts.{role} must be at most {PROMPT_MAX_CHARS} characters")
        result[str(role)] = prompt
    if not partial:
        for role in MODEL_ROLES:
            result.setdefault(role, "")
    return result


def _normalise_model_policy(value: Any, *, partial: bool) -> dict[str, str]:
    if not isinstance(value, dict):
        return {} if partial else {"preset": "sol-luna"}
    unknown = _unknown_fields(value, MODEL_POLICY_FIELDS, "model_policy")
    if unknown:
        raise FactoryError("config contains unknown model policy field(s): " + ", ".join(unknown))
    preset = _text(value.get("preset")).lower()
    return {"preset": preset} if preset else ({} if partial else {"preset": "sol-luna"})


def _normalise_coordination(
    value: Any,
    *,
    partial: bool,
    allow_legacy: bool = False,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {} if partial else copy.deepcopy(default_global_config()["coordination"])
    allowed_fields = _COORDINATION_FIELDS | ({"kanban_board"} if allow_legacy else set())
    unknown = _unknown_fields(value, allowed_fields, "coordination")
    if unknown:
        raise FactoryError("config contains unknown coordination field(s): " + ", ".join(unknown))
    result: dict[str, Any] = {}
    if "mode" in value:
        mode = _text(value.get("mode")).lower()
        if allow_legacy and mode in {"local", "kanban", "both"}:
            mode = "beads"
        if mode not in COORDINATION_MODES:
            raise FactoryError("coordination.mode must be beads")
        result["mode"] = mode
    if "beads_directory" in value:
        result["beads_directory"] = _text(value.get("beads_directory"))
    if "beads_isolated_authorized" in value:
        if not isinstance(value.get("beads_isolated_authorized"), bool):
            raise FactoryError("coordination.beads_isolated_authorized must be a boolean")
        result["beads_isolated_authorized"] = value["beads_isolated_authorized"] is True
    if not partial:
        result = _deep_merge(default_global_config()["coordination"], result)
    return result


def _normalise_reasoning(value: Any, *, partial: bool) -> dict[str, str]:
    if not isinstance(value, dict):
        return {} if partial else {"orchestrator": "high", "worker": "medium"}
    unknown = _unknown_fields(value, REASONING_FIELDS, "reasoning_effort")
    if unknown:
        raise FactoryError("config contains unknown reasoning field(s): " + ", ".join(unknown))
    result: dict[str, str] = {}
    for role, effort in value.items():
        normalized = _text(effort).lower()
        if normalized not in REASONING_EFFORTS:
            raise FactoryError(f"reasoning_effort.{role} must be low, medium, or high")
        result[str(role)] = normalized
    if not partial:
        result = _deep_merge({"orchestrator": "high", "worker": "medium"}, result)
    return result


def _normalise_policy(value: Any, *, partial: bool) -> dict[str, int]:
    if not isinstance(value, dict):
        return {} if partial else {
            "max_active_milestones": 1,
            "max_parallel_slices": 2,
            "repeated_failure_limit": 2,
            "max_remediation_cycles": 3,
        }
    unknown = _unknown_fields(value, POLICY_FIELDS, "policy")
    if unknown:
        raise FactoryError("config contains unknown policy field(s): " + ", ".join(unknown))
    result: dict[str, int] = {}
    for field, number in value.items():
        if isinstance(number, bool) or not isinstance(number, int) or number < 0:
            raise FactoryError(f"policy.{field} must be a non-negative integer")
        result[str(field)] = number
    return result


def normalise_global_config(value: Any, *, allow_legacy: bool = False) -> dict[str, Any]:
    if not isinstance(value, dict):
        return default_global_config()
    if _credential_shaped_paths(value, "config"):
        raise FactoryError("config contains credential-shaped data; store only provider/model references")
    unknown = _unknown_fields(value, _GLOBAL_FIELDS, "config")
    if unknown:
        raise FactoryError("config contains unknown field(s): " + ", ".join(unknown))
    result = {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "models": _normalise_models(value.get("models"), partial=False),
        "model_policy": _normalise_model_policy(value.get("model_policy"), partial=False),
        "system_prompts": _normalise_prompts(value.get("system_prompts"), partial=False),
        "coordination": _normalise_coordination(
            value.get("coordination"),
            partial=False,
            allow_legacy=allow_legacy,
        ),
        "reasoning_effort": _normalise_reasoning(value.get("reasoning_effort"), partial=False),
        "policy": _normalise_policy(value.get("policy"), partial=False),
    }
    return _redact_sensitive_values(result)


def normalise_overrides(value: Any, *, allow_legacy: bool = False) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise FactoryError("project overrides must be an object")
    if _credential_shaped_paths(value, "project overrides"):
        raise FactoryError("project overrides contain credential-shaped data; store only provider/model references")
    unknown = _unknown_fields(value, _OVERRIDE_FIELDS, "overrides")
    if unknown:
        raise FactoryError("project overrides contain unknown field(s): " + ", ".join(unknown))
    result: dict[str, Any] = {}
    if "models" in value:
        result["models"] = _normalise_models(value.get("models"), partial=True)
    if "model_policy" in value:
        result["model_policy"] = _normalise_model_policy(value.get("model_policy"), partial=True)
    if "system_prompts" in value:
        result["system_prompts"] = _normalise_prompts(value.get("system_prompts"), partial=True)
    if "coordination" in value:
        result["coordination"] = _normalise_coordination(
            value.get("coordination"),
            partial=True,
            allow_legacy=allow_legacy,
        )
    if "reasoning_effort" in value:
        result["reasoning_effort"] = _normalise_reasoning(value.get("reasoning_effort"), partial=True)
    if "policy" in value:
        result["policy"] = _normalise_policy(value.get("policy"), partial=True)
    return _redact_sensitive_values(result)


def effective_config(global_config: Any, overrides: Any = None) -> dict[str, Any]:
    return normalise_global_config(_deep_merge(
        normalise_global_config(global_config),
        normalise_overrides(overrides),
    ))


def _config_file() -> Path:
    return plugin_data_dir() / "global-config.json"


def _projects_file() -> Path:
    return plugin_data_dir() / "projects.json"


def load_global_config() -> dict[str, Any]:
    raw = _read_object(_config_file())
    if raw is None:
        return default_global_config()
    try:
        return normalise_global_config(raw, allow_legacy=True)
    except FactoryError:
        return default_global_config()


def save_global_config(value: Any) -> dict[str, Any]:
    config = normalise_global_config(value)
    _atomic_write(_config_file(), config)
    return config


def load_project_records() -> dict[str, dict[str, Any]]:
    raw = _read_object(_projects_file())
    if raw is None:
        return {}
    if raw.get("schema_version") != PROJECTS_SCHEMA_VERSION or not isinstance(raw.get("projects"), dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for project_id, value in raw["projects"].items():
        if not isinstance(project_id, str) or not isinstance(value, dict):
            continue
        try:
            overrides = normalise_overrides(value.get("overrides"), allow_legacy=True)
            setup = normalise_setup(value["setup"]) if isinstance(value.get("setup"), dict) else None
        except FactoryError:
            continue
        result[project_id] = {
            "schema_version": PROJECTS_SCHEMA_VERSION,
            "overrides": overrides,
            "setup": setup,
            "updated_at": _text(value.get("updated_at")),
        }
    return result


def load_project_record(project_id: str) -> dict[str, Any] | None:
    return load_project_records().get(str(project_id))


def save_project_record(
    project_id: str,
    *,
    setup: Any = None,
    overrides: Any = None,
) -> dict[str, Any]:
    project_id = _text(project_id)
    if not project_id or "/" in project_id or "\\" in project_id or project_id in {".", ".."}:
        raise FactoryError("project id is invalid")
    records = load_project_records()
    current = records.get(project_id, {})
    current_setup = setup if setup is not None else current.get("setup")
    normalized_setup = normalise_setup(current_setup) if isinstance(current_setup, dict) else None
    normalized_overrides = normalise_overrides(overrides if overrides is not None else current.get("overrides"))
    current = {
        "schema_version": PROJECTS_SCHEMA_VERSION,
        "overrides": normalized_overrides,
        "setup": normalized_setup,
        "updated_at": _datetime.datetime.now(_datetime.timezone.utc).isoformat(),
    }
    records[project_id] = current
    _atomic_write(_projects_file(), {"schema_version": PROJECTS_SCHEMA_VERSION, "projects": records})
    return copy.deepcopy(current)


def native_projects(*, include_archived: bool = False) -> list[dict[str, Any]]:
    """Return Hermes' explicit projects without making the feature depend on it in tests."""
    try:
        from hermes_cli import projects_db

        with projects_db.connect_closing() as conn:
            return [project.to_dict() for project in projects_db.list_projects(conn, include_archived=include_archived)]
    except Exception:
        return []


def native_project(project_id: str) -> dict[str, Any] | None:
    try:
        from hermes_cli import projects_db

        with projects_db.connect_closing() as conn:
            project = projects_db.get_project(conn, str(project_id))
            return project.to_dict() if project is not None else None
    except Exception:
        return None


def project_workspace(project: dict[str, Any]) -> Path | None:
    candidates: list[str] = []
    primary = project.get("primary_path")
    if isinstance(primary, str) and primary.strip():
        candidates.append(primary)
    folders = project.get("folders") if isinstance(project.get("folders"), list) else []
    for folder in folders:
        if isinstance(folder, dict) and isinstance(folder.get("path"), str):
            candidates.append(folder["path"])
    for raw in candidates:
        try:
            path = Path(raw).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if path:
            return path
    return None


def _path_is_allowed(path: Path, project: dict[str, Any]) -> bool:
    allowed: list[Path] = []
    primary = project_workspace(project)
    if primary is not None:
        allowed.append(primary)
    for folder in project.get("folders", []) if isinstance(project.get("folders"), list) else []:
        if isinstance(folder, dict) and isinstance(folder.get("path"), str):
            try:
                allowed.append(Path(folder["path"]).expanduser().resolve())
            except (OSError, RuntimeError, ValueError):
                continue
    for root in allowed:
        try:
            if os.path.commonpath((str(root), str(path))) == str(root):
                return True
        except ValueError:
            continue
    return False


def setup_for_project(project: dict[str, Any], record: dict[str, Any] | None) -> dict[str, Any]:
    raw_setup = record.get("setup") if isinstance(record, dict) else None
    if not isinstance(raw_setup, dict):
        raw_setup = default_setup()
    setup = normalise_setup(raw_setup)
    workspace = project_workspace(project)
    if workspace is None:
        raise FactoryError("native project has no usable workspace path")
    requested = _text(setup.get("workspace_path"))
    if requested:
        requested_path = Path(requested).expanduser().resolve()
        if not _path_is_allowed(requested_path, project):
            raise FactoryError("project setup workspace must be inside the native project folders")
        workspace = requested_path
    setup["workspace_path"] = str(workspace)
    return setup


def _factory_json(path: Path) -> dict[str, Any] | None:
    try:
        if path.stat().st_size > 4_000_000:
            return None
    except OSError:
        return None
    return _read_object(path)


def _factory_snapshot(workspace: Path) -> dict[str, Any]:
    factory_dir = workspace / ".hermes" / "factory"
    manifest_path = factory_dir / "manifest.json"
    state_path = factory_dir / "state.json"
    manifest = _factory_json(manifest_path) if manifest_path.is_file() else None
    state = _factory_json(state_path) if state_path.is_file() else None
    errors: list[str] = []
    if manifest_path.exists() and manifest is None:
        errors.append("manifest is unreadable or too large")
    if state_path.exists() and state is None:
        errors.append("state is unreadable or too large")
    if manifest is not None and state is not None:
        mission_id = manifest.get("mission", {}).get("id") if isinstance(manifest.get("mission"), dict) else None
        if state.get("mission_id") != mission_id:
            errors.append("state mission_id does not match manifest")
    return {
        "factory_dir": str(factory_dir),
        "manifest_path": str(manifest_path),
        "state_path": str(state_path),
        "manifest": manifest,
        "state": state,
        "errors": errors,
    }


def _safe_event(event: Any) -> dict[str, Any] | None:
    if not isinstance(event, dict):
        return None
    actor = event.get("actor") if isinstance(event.get("actor"), dict) else {}
    safe = {
        "at": _text(event.get("at")),
        "entity_id": _text(event.get("entity_id")),
        "action": _text(event.get("action")),
        "actor_role": _text(actor.get("role")),
        "revision": event.get("revision") if isinstance(event.get("revision"), int) else None,
    }
    if not safe["at"] and not safe["action"] and not safe["entity_id"]:
        return None
    return safe


def progress_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    manifest = snapshot.get("manifest") if isinstance(snapshot.get("manifest"), dict) else {}
    state = snapshot.get("state") if isinstance(snapshot.get("state"), dict) else {}
    manifest_milestones = manifest.get("milestones") if isinstance(manifest.get("milestones"), list) else []
    manifest_slices = manifest.get("slices") if isinstance(manifest.get("slices"), list) else []
    state_milestones = state.get("milestones") if isinstance(state.get("milestones"), dict) else {}
    state_slices = state.get("slices") if isinstance(state.get("slices"), dict) else {}

    def rows(specs: list[Any], current: dict[str, Any], default_status: str) -> list[dict[str, Any]]:
        result = []
        for spec in specs:
            if not isinstance(spec, dict):
                continue
            entity_id = _text(spec.get("id"))
            live = current.get(entity_id) if isinstance(current.get(entity_id), dict) else {}
            result.append({
                "id": entity_id,
                "title": _text(spec.get("outcome") or spec.get("title")) or entity_id,
                "status": _text(live.get("status")) or default_status,
            })
        return result

    milestones = rows(manifest_milestones, state_milestones, "pending")
    slices = rows(manifest_slices, state_slices, "pending")
    completed_milestones = sum(row["status"] == "completed" for row in milestones)
    completed_slices = sum(row["status"] == "completed" for row in slices)
    total_units = len(milestones) + len(slices)
    completed_units = completed_milestones + completed_slices
    percent = round((completed_units / total_units) * 100) if total_units else 0
    statuses = [row["status"] for row in milestones + slices]
    if snapshot.get("errors"):
        status = "error"
    elif not snapshot.get("manifest"):
        status = "not_armed"
    elif statuses and all(item == "completed" for item in statuses):
        status = "completed"
    elif any(item in {"blocked", "replan_required"} for item in statuses):
        status = "blocked"
    elif any(item in {"active", "review", "review_passed", "validating"} for item in statuses):
        status = "active"
    else:
        status = "armed"
    events_raw = state.get("events") if isinstance(state.get("events"), list) else []
    events = [item for item in (_safe_event(event) for event in events_raw) if item is not None]
    return {
        "status": status,
        "percent": percent,
        "completed_milestones": completed_milestones,
        "total_milestones": len(milestones),
        "completed_slices": completed_slices,
        "total_slices": len(slices),
        "milestones": milestones,
        "slices": slices,
        "event_count": len(events),
        "last_event": events[-1] if events else None,
        "updated_at": _text(state.get("updated_at")),
        "integrity": "structural_only" if state else "absent",
    }


def _tail_text(path: Path, limit: int = 64_000) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit), os.SEEK_SET)
            raw = handle.read(limit)
    except OSError:
        return ""
    return _redact_sensitive_values(raw.decode("utf-8", errors="replace"))


def project_logs(snapshot: dict[str, Any], *, limit: int = 100) -> dict[str, Any]:
    state = snapshot.get("state") if isinstance(snapshot.get("state"), dict) else {}
    events_raw = state.get("events") if isinstance(state.get("events"), list) else []
    events = [item for item in (_safe_event(event) for event in events_raw) if item is not None]
    event_lines = [
        " ".join(
            item
            for item in (
                event.get("at"),
                event.get("actor_role"),
                event.get("action"),
                event.get("entity_id"),
            )
            if item
        )
        for event in events[-max(1, min(int(limit), 500)):]
    ]
    factory_dir = Path(snapshot.get("factory_dir") or ".")
    file_lines: list[str] = []
    for candidate in (factory_dir / "factory.log", factory_dir / "logs" / "factory.log"):
        if candidate.is_file():
            text = _tail_text(candidate)
            if text:
                file_lines.extend(text.splitlines()[-max(1, min(int(limit), 500)):])
    lines = (file_lines + event_lines)[-max(1, min(int(limit), 500)):]
    return {
        "lines": lines,
        "text": "\n".join(lines),
        "event_count": len(events),
        "sources": ["state.events"] + ([".hermes/factory/factory.log"] if file_lines else []),
    }


def beads_status(workspace: Path, coordination: dict[str, Any]) -> dict[str, Any]:
    required = True
    configured = _text(coordination.get("beads_directory"))
    beads_dir = Path(configured).expanduser() if configured else workspace / ".beads"
    if not beads_dir.is_absolute():
        beads_dir = workspace / beads_dir
    try:
        beads_dir = beads_dir.resolve()
    except (OSError, RuntimeError):
        pass
    authorized = coordination.get("beads_isolated_authorized") is True
    executable = ""
    version = ""
    reason = "Beads is required"
    try:
        try:
            from .beads_adapter import _resolve_bd_executable, preflight_beads
        except ImportError:
            from beads_adapter import _resolve_bd_executable, preflight_beads  # type: ignore
        executable = _resolve_bd_executable("bd")
        if executable:
            try:
                ready = preflight_beads(
                    beads_dir,
                    bd_executable=executable,
                    authorize_isolated=authorized,
                )
                version = _text(ready.get("bd_version"))
                reason = "ready"
            except Exception as exc:
                message = str(exc).lower()
                if "version" in message:
                    reason = "Beads CLI version is unsupported"
                elif "readable initialized" in message or "directory" in message:
                    reason = "Beads directory is not initialized"
                elif "authorized" in message:
                    reason = "Beads graph writes require explicit project authorization"
                else:
                    reason = "Beads preflight is not ready"
        else:
            reason = "Beads CLI is not available to the Hermes runtime"
    except Exception:
        reason = "Beads preflight is not ready"
    return {
        "required": required,
        "cli_available": bool(executable),
        "cli_path": executable,
        "version": version,
        "directory": str(beads_dir),
        "initialized": beads_dir.is_dir() and any(beads_dir.iterdir()) if beads_dir.is_dir() else False,
        "authorized_for_writes": authorized,
        "ready": reason == "ready",
        "reason": reason,
    }


def project_summary(project: dict[str, Any], global_config: dict[str, Any], record: dict[str, Any] | None) -> dict[str, Any]:
    workspace = project_workspace(project)
    if workspace is None:
        snapshot = {"manifest": None, "state": None, "errors": ["native project has no workspace"], "factory_dir": ""}
        setup = default_setup()
    else:
        try:
            setup = setup_for_project(project, record)
        except FactoryError:
            setup = default_setup()
            setup["workspace_path"] = str(workspace)
        snapshot = _factory_snapshot(workspace)
    overrides = normalise_overrides(record.get("overrides") if isinstance(record, dict) else None)
    config = effective_config(global_config, overrides)
    progress = progress_snapshot(snapshot)
    coordination = copy.deepcopy(config["coordination"])
    return {
        "id": _text(project.get("id")),
        "slug": _text(project.get("slug")),
        "name": _text(project.get("name")) or _text(project.get("slug")) or "Unnamed project",
        "description": _text(project.get("description")),
        "primary_path": str(workspace) if workspace else "",
        "folders": copy.deepcopy(project.get("folders") if isinstance(project.get("folders"), list) else []),
        "archived": project.get("archived") is True,
        "configured": isinstance(record, dict) and isinstance(record.get("setup"), dict),
        "updated_at": _text(record.get("updated_at")) if isinstance(record, dict) else "",
        "config": {
            "overrides": overrides,
            "effective": config,
            "has_overrides": bool(overrides),
        },
        "coordination": coordination,
        "progress": progress,
        "beads": beads_status(workspace or Path.cwd(), coordination),
        "snapshot": {
            "manifest_path": snapshot.get("manifest_path", ""),
            "state_path": snapshot.get("state_path", ""),
            "errors": snapshot.get("errors", []),
        },
        "setup": setup,
        "readiness": None,
    }


def project_detail(project: dict[str, Any], global_config: dict[str, Any], record: dict[str, Any] | None, *, include_logs: bool = True) -> dict[str, Any]:
    detail = project_summary(project, global_config, record)
    workspace = project_workspace(project)
    snapshot = _factory_snapshot(workspace) if workspace else {"manifest": None, "state": None, "factory_dir": "", "errors": []}
    if include_logs:
        detail["logs"] = project_logs(snapshot)
    return detail
