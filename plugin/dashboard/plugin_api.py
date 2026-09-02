"""Dark Factory dashboard backend.

Mounted by Hermes at /api/plugins/dark-factory/. The model catalogue is built
from the same authenticated, profile-scoped inventory as Hermes' own model
picker. Only provider/model identifiers cross this API; credentials never do.
"""

from __future__ import annotations

import hashlib
import copy
import importlib.util
import json
import logging
import sys
import types
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_SHARED_PACKAGE = "_hermes_dark_factory_" + hashlib.sha256(str(PLUGIN_ROOT).encode("utf-8")).hexdigest()[:12]
if _SHARED_PACKAGE not in sys.modules:
    package = types.ModuleType(_SHARED_PACKAGE)
    package.__path__ = [str(PLUGIN_ROOT)]  # type: ignore[attr-defined]
    sys.modules[_SHARED_PACKAGE] = package
for _module_name in ("engine", "model_policy", "intake", "project_store"):
    _qualified = f"{_SHARED_PACKAGE}.{_module_name}"
    if _qualified in sys.modules:
        continue
    _spec = importlib.util.spec_from_file_location(_qualified, PLUGIN_ROOT / f"{_module_name}.py")
    if _spec is None or _spec.loader is None:
        raise ImportError(f"cannot load dark-factory {_module_name}")
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[_qualified] = _module
    _spec.loader.exec_module(_module)

_intake = sys.modules[f"{_SHARED_PACKAGE}.intake"]
_engine = sys.modules[f"{_SHARED_PACKAGE}.engine"]
_model_policy = sys.modules[f"{_SHARED_PACKAGE}.model_policy"]
_project_store = sys.modules[f"{_SHARED_PACKAGE}.project_store"]
FactoryError = _intake.FactoryError
compile_to_workspace = _intake.compile_to_workspace
import_manifest_to_workspace = _intake.import_manifest_to_workspace
default_setup = _intake.default_setup
load_setup = _intake.load_setup
normalise_setup = _intake.normalise_setup
resolve_setup_models = _intake.resolve_setup_models
plugin_data_dir = _intake.plugin_data_dir
save_setup = _intake.save_setup
validate_intake = _intake.validate_intake
load_manifest = _engine.load_manifest
validate_manifest = _engine.validate_manifest
default_global_config = _project_store.default_global_config
effective_config = _project_store.effective_config
load_global_config = _project_store.load_global_config
load_project_record = _project_store.load_project_record
native_project = _project_store.native_project
native_projects = _project_store.native_projects
normalise_global_config = _project_store.normalise_global_config
normalise_overrides = _project_store.normalise_overrides
project_detail = _project_store.project_detail
project_summary = _project_store.project_summary
project_workspace = _project_store.project_workspace
save_global_config = _project_store.save_global_config
save_project_record = _project_store.save_project_record
setup_for_project = _project_store.setup_for_project

log = logging.getLogger(__name__)
router = APIRouter()

_INVENTORY_HTTP_DETAIL = {
    "code": "model_inventory_unavailable",
    "message": "Active-profile model inventory is unavailable.",
}


def _required_model_options(refresh: bool = False) -> dict[str, Any]:
    """Fail closed without retaining or logging provider/auth exception text."""
    try:
        return _model_options(refresh=refresh)
    except Exception:
        log.error("dark-factory active-profile model inventory is unavailable")
        raise HTTPException(status_code=503, detail=_INVENTORY_HTTP_DETAIL) from None


def _runtime_manifest_check(manifest: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    """Validate a direct manifest against the active profile without secrets."""
    check = validate_manifest(manifest)
    errors = list(check.get("errors") or [])
    warnings = list(check.get("warnings") or [])
    if not errors:
        active_refs: set[tuple[str, str]] = set()
        provider_rows = catalog.get("providers")
        for provider in provider_rows if isinstance(provider_rows, list) else []:
            if not isinstance(provider, dict):
                continue
            slug = _inventory_text(provider.get("slug")).lower()
            model_rows = provider.get("models")
            models = model_rows if isinstance(model_rows, list) else []
            active_refs.update(
                (slug, model)
                for model in (_inventory_text(value) for value in models)
                if slug and model
            )
        models = manifest.get("models") if isinstance(manifest.get("models"), dict) else {}
        for role in ("integrator", "builder", "verifier", "adversary", "holdout"):
            ref = models.get(role) if isinstance(models.get(role), dict) else {}
            provider = _inventory_text(ref.get("provider")).lower()
            model = _inventory_text(ref.get("model"))
            if (provider, model) not in active_refs:
                errors.append(
                    f"models.{role} provider/model is not present in the authenticated active-profile inventory"
                )
    return {"valid": not errors, "errors": errors, "warnings": warnings}


def _active_profile_name() -> str:
    """Resolve the profile that owns the backend's current HERMES_HOME."""
    try:
        from hermes_constants import get_hermes_home
        from hermes_cli.profiles import get_active_profile_name, list_profiles

        home = get_hermes_home().expanduser().resolve()
        for profile in list_profiles():
            try:
                if Path(profile.path).expanduser().resolve() == home:
                    return str(profile.name or "default")
            except (OSError, RuntimeError):
                continue
        return get_active_profile_name() or "default"
    except Exception:
        return "default"


def _inventory_text(value: Any) -> str:
    """Return a canonical inventory identifier without coercing malformed data."""

    return value.strip() if isinstance(value, str) else ""


def _model_options(refresh: bool = False) -> dict[str, Any]:
    from hermes_cli.inventory import build_models_payload, load_picker_context

    payload = build_models_payload(
        load_picker_context(),
        explicit_only=True,
        include_unconfigured=False,
        picker_hints=True,
        canonical_order=True,
        pricing=False,
        capabilities=False,
        refresh=bool(refresh),
        probe_custom_providers=bool(refresh),
        probe_current_custom_provider=False,
        for_picker=True,
        max_models=200,
    )
    providers: list[dict[str, Any]] = []
    provider_rows = payload.get("providers") if isinstance(payload.get("providers"), list) else []
    for row in provider_rows:
        if not isinstance(row, dict) or row.get("authenticated") is not True:
            continue
        slug = _inventory_text(row.get("slug")).lower()
        models: list[str] = []
        model_rows = row.get("models") if isinstance(row.get("models"), list) else []
        for value in model_rows:
            model = _inventory_text(value)
            if model and model not in models:
                models.append(model)
        if not slug or not models:
            continue
        label = row.get("name") or row.get("label")
        providers.append(
            {
                "slug": slug,
                "label": label.strip() if isinstance(label, str) and label.strip() else slug,
                "authenticated": True,
                "models": models,
            }
        )
    return {
        "profile": _active_profile_name(),
        "providers": providers,
        "current": {
            "provider": _inventory_text(payload.get("provider")).lower(),
            "model": _inventory_text(payload.get("model")),
        },
        "model_policy": _model_policy.preset_catalog({"providers": providers}),
        "credentials_included": False,
    }


def _setup_payload(
    setup: dict[str, Any],
    refresh_models: bool = False,
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    catalog = catalog if catalog is not None else _required_model_options(refresh=refresh_models)
    setup = resolve_setup_models(setup, catalog)
    return {
        "profile": catalog["profile"],
        "setup": setup,
        "readiness": validate_intake(setup, model_catalog=catalog),
        "model_options": catalog,
    }


def _resolved_config_payload(
    config: dict[str, Any],
    catalog: dict[str, Any],
) -> dict[str, Any]:
    """Return config with fill-only model defaults, never credentials."""
    config = effective_config(config)
    setup = default_setup()
    setup["models"] = config["models"]
    setup["model_policy"] = config["model_policy"]
    resolved = resolve_setup_models(setup, catalog)
    config["models"] = resolved["models"]
    return config


def _apply_project_config(setup: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Project config is compiled into the canonical intake without new fields."""
    setup = normalise_setup(setup)
    setup["models"] = config["models"]
    setup["model_policy"] = config["model_policy"]
    setup["system_prompts"] = config["system_prompts"]
    setup["execution"]["reasoning_effort"] = config["reasoning_effort"]
    setup["policy"] = config["policy"]
    coordination = config["coordination"]
    setup["execution"]["graph_backend"] = "beads"
    setup["execution"]["beads_isolated_authorized"] = coordination["beads_isolated_authorized"]
    if coordination.get("beads_directory"):
        setup["execution"]["beads_directory"] = coordination["beads_directory"]
    return normalise_setup(setup)


def _project_or_404(project_id: str) -> dict[str, Any]:
    project = native_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Hermes project was not found.")
    return project


def _project_payload(
    project: dict[str, Any],
    *,
    catalog: dict[str, Any],
    include_logs: bool = True,
) -> dict[str, Any]:
    global_config = load_global_config()
    record = load_project_record(str(project.get("id") or ""))
    payload = project_detail(project, global_config, record, include_logs=include_logs)
    config = _resolved_config_payload(
        effective_config(global_config, payload["config"].get("overrides")),
        catalog,
    )
    payload["config"]["effective"] = config
    payload["setup"] = _apply_project_config(payload["setup"], config)
    payload["readiness"] = validate_intake(payload["setup"], model_catalog=catalog)
    payload["profile"] = catalog["profile"]
    payload["model_options"] = catalog
    return payload


def _project_setup_from_body(
    project: dict[str, Any],
    record: dict[str, Any] | None,
    body: dict[str, Any],
) -> dict[str, Any]:
    nested = body.get("setup")
    if isinstance(nested, dict):
        setup = normalise_setup(nested)
    elif isinstance(record, dict) and isinstance(record.get("setup"), dict):
        setup = normalise_setup(record["setup"])
    else:
        setup = setup_for_project(project, record)
    # Resolve and enforce project ownership after normalisation. A project API
    # must never be able to write a sibling workspace by changing this field.
    setup = setup_for_project(project, {"setup": setup})
    setup["workspace_path"] = setup_for_project(project, None)["workspace_path"]
    return setup


@router.get("/model-options")
def model_options(refresh: bool = Query(False)) -> dict[str, Any]:
    return _required_model_options(refresh=refresh)


@router.get("/setup")
def get_setup() -> dict[str, Any]:
    try:
        catalog = _required_model_options(refresh=False)
        return _setup_payload(load_setup(), catalog=catalog)
    except HTTPException:
        raise
    except Exception:
        log.error("dark-factory setup load failed")
        raise HTTPException(status_code=500, detail="Failed to load factory setup.") from None


def _setup_from_body(body: dict[str, Any], *, fallback_to_saved: bool) -> dict[str, Any]:
    nested = body.get("setup")
    if isinstance(nested, dict):
        return normalise_setup(nested)
    if body:
        return normalise_setup(body)
    return normalise_setup(load_setup() if fallback_to_saved else {})


@router.put("/setup")
def put_setup(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        catalog = _required_model_options(refresh=False)
        setup = _setup_from_body(body, fallback_to_saved=False)
        setup = resolve_setup_models(setup, catalog)
        saved = save_setup(setup)
        return _setup_payload(saved, catalog=catalog)
    except HTTPException:
        raise
    except Exception:
        log.error("dark-factory setup save failed")
        raise HTTPException(status_code=500, detail="Failed to save factory setup.") from None


@router.post("/preflight")
def preflight(body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    try:
        catalog = _required_model_options(refresh=False)
        setup = _setup_from_body(body, fallback_to_saved=True)
        return {
            "profile": catalog["profile"],
            "setup": setup,
            "readiness": validate_intake(setup, model_catalog=catalog),
        }
    except HTTPException:
        raise
    except Exception:
        log.error("dark-factory preflight failed")
        raise HTTPException(status_code=500, detail="Factory preflight failed.") from None


@router.post("/compile")
def compile_factory(body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    try:
        catalog = _required_model_options(refresh=False)
        setup = _setup_from_body(body, fallback_to_saved=True)
        readiness = validate_intake(setup, model_catalog=catalog)
        if not readiness["ready"]:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Factory is not ready. Resolve every blocking preflight item before launch.",
                    "readiness": readiness,
                },
            )
        result = compile_to_workspace(setup, model_catalog=catalog)
        return {
            "ready": True,
            "profile": catalog["profile"],
            "readiness": readiness,
            **result,
        }
    except HTTPException:
        raise
    except FactoryError:
        raise HTTPException(status_code=422, detail="Factory compile request was rejected.") from None
    except Exception:
        log.error("dark-factory compile failed")
        raise HTTPException(status_code=500, detail="Failed to compile factory manifest.") from None


@router.post("/manifest/import")
def import_manifest(body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """Import a canonical manifest into a new or explicitly selected project."""
    try:
        if not isinstance(body, dict):
            raise HTTPException(status_code=422, detail="Manifest import requires a JSON object.")
        manifest_path_value = body.get("manifest_path")
        inline_manifest = body.get("manifest")
        if manifest_path_value and inline_manifest is not None:
            raise HTTPException(status_code=422, detail="Provide either manifest_path or manifest, not both.")
        source_path = ""
        if manifest_path_value:
            source_path = str(Path(str(manifest_path_value)).expanduser().resolve())
            candidate = load_manifest(source_path)
        elif isinstance(inline_manifest, dict):
            candidate = copy.deepcopy(inline_manifest)
        else:
            raise HTTPException(status_code=422, detail="manifest_path or manifest is required.")

        project_id_value = body.get("project_id")
        project_id = str(project_id_value).strip() if project_id_value is not None else ""
        selected_workspace: Path | None = None
        if project_id:
            project = _project_or_404(project_id)
            selected_workspace = project_workspace(project)
            if selected_workspace is None:
                raise HTTPException(status_code=422, detail="Hermes project has no usable workspace path.")
        requested_workspace = body.get("workspace_path")
        if selected_workspace is not None:
            if requested_workspace is not None:
                try:
                    requested_path = Path(str(requested_workspace)).expanduser().resolve()
                except (OSError, RuntimeError, ValueError):
                    raise HTTPException(status_code=422, detail="workspace_path is invalid.") from None
                if requested_path != selected_workspace:
                    raise HTTPException(status_code=422, detail="project workspace is authoritative for manifest import.")
            import_workspace = selected_workspace
        elif requested_workspace is not None:
            if not isinstance(requested_workspace, str) or not requested_workspace.strip():
                raise HTTPException(status_code=422, detail="workspace_path must be a non-empty path.")
            import_workspace = Path(requested_workspace).expanduser().resolve()
        else:
            import_workspace = None
        if import_workspace is not None:
            mission = candidate.get("mission")
            if not isinstance(mission, dict):
                raise HTTPException(status_code=422, detail="Manifest import requires a mission object.")
            candidate["mission"]["workspace_path"] = str(import_workspace)

        catalog = _required_model_options(refresh=False)
        readiness = _runtime_manifest_check(candidate, catalog)
        if not readiness["valid"]:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Manifest import is not ready. Resolve schema, Beads, and active-profile model blockers.",
                    "readiness": readiness,
                    "source_path": source_path,
                },
            )
        result = import_manifest_to_workspace(candidate)
        return {
            "ready": True,
            "imported": True,
            "beads_applied": False,
            "project_id": project_id or None,
            "profile": catalog["profile"],
            "readiness": readiness,
            "next": ["factory_beads_plan", "integrator-authorized factory_beads_apply"],
            "source_path": source_path,
            **result,
        }
    except HTTPException:
        raise
    except FactoryError:
        raise HTTPException(status_code=422, detail="Manifest import was rejected by the Dark Factory contract.") from None
    except Exception:
        log.error("dark-factory manifest import failed")
        raise HTTPException(status_code=500, detail="Failed to import Dark Factory manifest.") from None


@router.get("/launch")
def launch_status() -> dict[str, Any]:
    path = plugin_data_dir() / "launch.json"
    if not path.exists():
        return {"status": "not_armed", "profile": _active_profile_name()}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.error("dark-factory launch state is invalid")
        raise HTTPException(status_code=500, detail="Invalid launch state.") from None
    return {"profile": _active_profile_name(), **value}


@router.get("/global-config")
def get_global_config() -> dict[str, Any]:
    try:
        catalog = _required_model_options(refresh=False)
        config = _resolved_config_payload(load_global_config(), catalog)
        return {
            "profile": catalog["profile"],
            "config": config,
            "model_options": catalog,
            "coordination_modes": list(_project_store.COORDINATION_MODES),
            "credentials_included": False,
        }
    except HTTPException:
        raise
    except Exception:
        log.error("dark-factory global config load failed")
        raise HTTPException(status_code=500, detail="Failed to load Dark Factory defaults.") from None


@router.put("/global-config")
def put_global_config(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        catalog = _required_model_options(refresh=False)
        incoming = body.get("config") if isinstance(body.get("config"), dict) else body
        saved = save_global_config(normalise_global_config(incoming))
        return {
            "profile": catalog["profile"],
            "config": _resolved_config_payload(saved, catalog),
            "model_options": catalog,
            "coordination_modes": list(_project_store.COORDINATION_MODES),
            "credentials_included": False,
        }
    except HTTPException:
        raise
    except FactoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except Exception:
        log.error("dark-factory global config save failed")
        raise HTTPException(status_code=500, detail="Failed to save Dark Factory defaults.") from None


@router.get("/projects")
def get_projects(include_archived: bool = Query(False)) -> dict[str, Any]:
    try:
        global_config = load_global_config()
        projects = [
            project_summary(
                project,
                global_config,
                load_project_record(str(project.get("id") or "")),
            )
            for project in native_projects(include_archived=include_archived)
        ]
        return {
            "profile": _active_profile_name(),
            "projects": projects,
            "count": len(projects),
            "include_archived": bool(include_archived),
            "credentials_included": False,
        }
    except Exception:
        log.error("dark-factory project list load failed")
        raise HTTPException(status_code=500, detail="Failed to load Dark Factory projects.") from None


@router.get("/projects/{project_id}")
def get_project_detail(
    project_id: str,
    include_logs: bool = Query(True),
) -> dict[str, Any]:
    try:
        catalog = _required_model_options(refresh=False)
        return _project_payload(
            _project_or_404(project_id),
            catalog=catalog,
            include_logs=include_logs,
        )
    except HTTPException:
        raise
    except FactoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except Exception:
        log.error("dark-factory project detail load failed")
        raise HTTPException(status_code=500, detail="Failed to load Dark Factory project.") from None


@router.get("/projects/{project_id}/logs")
def get_project_logs(
    project_id: str,
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    try:
        project = _project_or_404(project_id)
        global_config = load_global_config()
        record = load_project_record(str(project.get("id") or ""))
        detail = project_detail(project, global_config, record, include_logs=True)
        logs = detail.get("logs") if isinstance(detail.get("logs"), dict) else {}
        return {
            "project_id": str(project.get("id") or ""),
            "lines": list(logs.get("lines", []))[-limit:],
            "text": "\n".join(list(logs.get("lines", []))[-limit:]),
            "event_count": logs.get("event_count", 0),
            "sources": logs.get("sources", []),
        }
    except HTTPException:
        raise
    except Exception:
        log.error("dark-factory project logs load failed")
        raise HTTPException(status_code=500, detail="Failed to load Dark Factory project logs.") from None


@router.put("/projects/{project_id}/config")
def put_project_config(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        project = _project_or_404(project_id)
        incoming = body.get("overrides") if isinstance(body.get("overrides"), dict) else body
        record = load_project_record(str(project.get("id") or ""))
        overrides = normalise_overrides(incoming)
        setup = record.get("setup") if isinstance(record, dict) else None
        save_project_record(project_id, setup=setup, overrides=overrides)
        catalog = _required_model_options(refresh=False)
        return _project_payload(project, catalog=catalog, include_logs=True)
    except HTTPException:
        raise
    except FactoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except Exception:
        log.error("dark-factory project config save failed")
        raise HTTPException(status_code=500, detail="Failed to save project Dark Factory config.") from None


@router.put("/projects/{project_id}")
def put_project_detail(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        project = _project_or_404(project_id)
        record = load_project_record(str(project.get("id") or ""))
        setup = _project_setup_from_body(project, record, body)
        overrides = (
            normalise_overrides(body.get("overrides"))
            if "overrides" in body
            else normalise_overrides(record.get("overrides") if isinstance(record, dict) else None)
        )
        save_project_record(project_id, setup=setup, overrides=overrides)
        catalog = _required_model_options(refresh=False)
        return _project_payload(project, catalog=catalog, include_logs=True)
    except HTTPException:
        raise
    except FactoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except Exception:
        log.error("dark-factory project setup save failed")
        raise HTTPException(status_code=500, detail="Failed to save Dark Factory project setup.") from None


@router.post("/projects/{project_id}/compile")
def compile_project(project_id: str, body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    try:
        project = _project_or_404(project_id)
        record = load_project_record(str(project.get("id") or ""))
        setup = _project_setup_from_body(project, record, body)
        overrides = (
            normalise_overrides(body.get("overrides"))
            if "overrides" in body
            else normalise_overrides(record.get("overrides") if isinstance(record, dict) else None)
        )
        catalog = _required_model_options(refresh=False)
        config = _resolved_config_payload(
            effective_config(load_global_config(), overrides),
            catalog,
        )
        setup = _apply_project_config(setup, config)
        beads = _project_store.beads_status(
            Path(setup["workspace_path"]),
            config["coordination"],
        )
        if not beads["ready"]:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Beads is required before a project can be compiled.",
                    "beads": beads,
                },
            )
        readiness = validate_intake(setup, model_catalog=catalog)
        if not readiness["ready"]:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Factory is not ready. Resolve every blocking preflight item before launch.",
                    "readiness": readiness,
                },
            )
        save_project_record(project_id, setup=setup, overrides=overrides)
        result = compile_to_workspace(setup, model_catalog=catalog)
        return {
            "ready": True,
            "project_id": project_id,
            "profile": catalog["profile"],
            "coordination": config["coordination"],
            "readiness": readiness,
            **result,
        }
    except HTTPException:
        raise
    except FactoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except Exception:
        log.error("dark-factory project compile failed")
        raise HTTPException(status_code=500, detail="Failed to compile Dark Factory project.") from None
