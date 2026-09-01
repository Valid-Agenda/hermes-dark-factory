"""Dark Factory dashboard backend.

Mounted by Hermes at /api/plugins/dark-factory/. The model catalogue is built
from the same authenticated, profile-scoped inventory as Hermes' own model
picker. Only provider/model identifiers cross this API; credentials never do.
"""

from __future__ import annotations

import hashlib
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
for _module_name in ("engine", "model_policy", "intake"):
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
_model_policy = sys.modules[f"{_SHARED_PACKAGE}.model_policy"]
FactoryError = _intake.FactoryError
compile_to_workspace = _intake.compile_to_workspace
load_setup = _intake.load_setup
normalise_setup = _intake.normalise_setup
resolve_setup_models = _intake.resolve_setup_models
plugin_data_dir = _intake.plugin_data_dir
save_setup = _intake.save_setup
validate_intake = _intake.validate_intake

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
