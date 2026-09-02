"""Hermes Dark Factory plugin prototype.

Adds deterministic factory-state tools and pre-tool-call guards. Reviewer
isolation is mandatory; the remaining guards activate when strict mode and a
configured manifest are present.
"""

from __future__ import annotations

import ast
import copy
import json
import os
import re
import shlex
from pathlib import Path
from typing import Any

from .engine import (
    BUILDER_SLICE_ACTIONS,
    DEFAULT_MANIFEST,
    DEFAULT_STATE,
    FactoryError,
    INTEGRATOR_SLICE_ACTIONS,
    TRANSITION_AUTHORIZATION_ERROR,
    _canonical_ownership_pattern,
    _entity_map,
    _parse_card_sections,
    _read_json,
    _paths_overlap,
    initial_state,
    issue_review_receipt,
    _state_file_lock,
    _validate_state_compatibility,
    lint_card,
    load_manifest,
    next_actions,
    save_transition,
    validate_manifest,
)
from .intake import (
    compile_to_workspace,
    import_manifest_to_workspace,
    load_setup,
    normalise_setup,
    resolve_setup_models,
    validate_intake,
)
from .beads_adapter import BeadsAdapterError, apply_graph_plan, build_graph_plan


def _json_result(data: dict[str, Any], success: bool = True) -> str:
    return json.dumps({"success": success, **data}, ensure_ascii=False, default=str)


def _resolve_paths(params: dict[str, Any]) -> tuple[str, str]:
    configured_manifest = os.environ.get("HERMES_FACTORY_MANIFEST", "").strip()
    configured_state = os.environ.get("HERMES_FACTORY_STATE", "").strip()
    if configured_manifest and not configured_state:
        configured_state = str(Path(configured_manifest).expanduser().resolve().with_name("state.json"))
    manifest = Path(str(params.get("manifest_path") or configured_manifest or DEFAULT_MANIFEST)).expanduser().resolve()
    state = Path(str(params.get("state_path") or configured_state or DEFAULT_STATE)).expanduser().resolve()
    if configured_manifest and manifest != Path(configured_manifest).expanduser().resolve():
        raise FactoryError("HERMES_FACTORY_MANIFEST pins factory operations to a different path")
    if configured_state and state != Path(configured_state).expanduser().resolve():
        raise FactoryError("HERMES_FACTORY_STATE pins factory operations to a different path")
    return str(manifest), str(state)


_RUNTIME_STATE_UNAVAILABLE = "compiled factory state is unavailable"


def load_state(
    manifest: dict[str, Any], state_path: str | Path
) -> tuple[dict[str, Any], Path]:
    """Load an existing compiled state without ever initializing one."""
    path = Path(state_path).expanduser().resolve()
    if not path.is_file():
        raise FactoryError(_RUNTIME_STATE_UNAVAILABLE)
    try:
        state = _read_json(path)
    except FactoryError as exc:
        if not path.is_file():
            raise FactoryError(_RUNTIME_STATE_UNAVAILABLE) from exc
        raise
    _validate_state_compatibility(manifest, state)
    return state, path


def _handle_validate(params: dict[str, Any], **_: Any) -> str:
    try:
        manifest_path, state_path = _resolve_paths(params)
        requested_state = Path(state_path).expanduser().resolve()
        manifest = load_manifest(manifest_path)
        check = _validate_runtime_manifest(manifest)
        result: dict[str, Any] = {
            "manifest_path": str(Path(manifest_path).expanduser().resolve()),
            **check,
        }
        if check["valid"]:
            if not requested_state.is_file():
                raise FactoryError(_RUNTIME_STATE_UNAVAILABLE)
            with _state_file_lock(requested_state):
                state, resolved_state = load_state(manifest, requested_state)
                result["state_path"] = str(resolved_state)
                result["revision"] = state["revision"]
                result["next"] = next_actions(manifest, state)
        return _json_result(result, success=bool(check["valid"]))
    except FactoryError as exc:
        return _json_result({"error": str(exc)}, success=False)


def _handle_next(params: dict[str, Any], **_: Any) -> str:
    try:
        manifest_path, state_path = _resolve_paths(params)
        requested_state = Path(state_path).expanduser().resolve()
        manifest = load_manifest(manifest_path)
        check = _validate_runtime_manifest(manifest)
        if not check["valid"]:
            return _json_result({"error": "invalid manifest", **check}, success=False)
        if not requested_state.is_file():
            raise FactoryError(_RUNTIME_STATE_UNAVAILABLE)
        with _state_file_lock(requested_state):
            state, resolved_state = load_state(manifest, requested_state)
            following = next_actions(manifest, state)
        return _json_result(
            {
                "manifest_path": str(Path(manifest_path).expanduser().resolve()),
                "state_path": str(resolved_state),
                "revision": state["revision"],
                "next": following,
            }
        )
    except FactoryError as exc:
        return _json_result({"error": str(exc)}, success=False)


def _handle_transition(params: dict[str, Any], **runtime: Any) -> str:
    try:
        manifest_path, state_path = _resolve_paths(params)
        manifest = load_manifest(manifest_path)
        entity_id = str(params.get("entity_id", ""))
        milestone_ids = {
            str(item.get("id", ""))
            for item in manifest.get("milestones", [])
            if isinstance(item, dict) and str(item.get("id", ""))
        }
        slice_ids = {
            str(item.get("id", ""))
            for item in manifest.get("slices", [])
            if isinstance(item, dict) and str(item.get("id", ""))
        }
        action = str(params.get("action", ""))
        expected_role = ""
        if entity_id in milestone_ids:
            expected_role = "integrator"
        elif entity_id in slice_ids and action in BUILDER_SLICE_ACTIONS:
            expected_role = "builder"
        elif entity_id in slice_ids and action in INTEGRATOR_SLICE_ACTIONS:
            expected_role = "integrator"
        else:
            raise FactoryError(TRANSITION_AUTHORIZATION_ERROR)
        configured = manifest.get("models", {}).get(expected_role, {})
        expected_provider = str(configured.get("provider") or "")
        expected_model = str(configured.get("model") or "")
        role = os.environ.get("HERMES_FACTORY_ROLE", "")
        provider = os.environ.get("HERMES_FACTORY_PROVIDER", "")
        model = os.environ.get("HERMES_FACTORY_MODEL", "")
        session_id = runtime.get("session_id")
        if (
            role != expected_role
            or not isinstance(session_id, str)
            or not session_id
            or session_id != session_id.strip()
            or provider != expected_provider
            or model != expected_model
        ):
            raise FactoryError(TRANSITION_AUTHORIZATION_ERROR)
        trusted_actor = {
            "session_id": session_id,
            "provider": provider,
            "model": model,
        }
        check = _validate_runtime_manifest(manifest)
        if not check["valid"]:
            return _json_result({"error": "invalid or unavailable runtime manifest", **check}, success=False)
        result = save_transition(
            manifest_path=manifest_path,
            state_path=state_path,
            entity_id=entity_id,
            action=action,
            evidence=params.get("evidence") if isinstance(params.get("evidence"), dict) else {},
            expected_revision=params.get("expected_revision") if isinstance(params.get("expected_revision"), int) and not isinstance(params.get("expected_revision"), bool) else None,
            trusted_actor=trusted_actor,
        )
        return _json_result(result)
    except FactoryError as exc:
        return _json_result({"error": str(exc)}, success=False)


def _handle_attest_review(params: dict[str, Any], **runtime: Any) -> str:
    try:
        manifest_path, _state_path = _resolve_paths(params)
        manifest = load_manifest(manifest_path)
        check = _validate_runtime_manifest(manifest)
        if not check["valid"]:
            return _json_result({"error": "invalid or unavailable runtime manifest", **check}, success=False)
        role = os.environ.get("HERMES_FACTORY_ROLE", "").strip().lower()
        if role not in {"verifier", "adversary", "holdout"}:
            raise FactoryError("review attestation is available only inside a verifier, adversary, or holdout session")
        provider = os.environ.get("HERMES_FACTORY_PROVIDER", "").strip()
        model = os.environ.get("HERMES_FACTORY_MODEL", "").strip()
        expected = manifest.get("models", {}).get(role, {})
        if provider != str(expected.get("provider", "")).strip() or model != str(expected.get("model", "")).strip():
            raise FactoryError("review session provider/model does not match the configured role")
        session_id = str(runtime.get("session_id") or "").strip()
        if not session_id:
            raise FactoryError("Hermes session_id is required for review attestation")
        receipt = issue_review_receipt(
            manifest,
            role=role,
            entity_id=str(params.get("entity_id", "")),
            candidate_sha=str(params.get("candidate_sha", "")),
            reviewer=str(params.get("reviewer") or session_id),
            provider=provider,
            model=model,
            verdict="PASS",
            session_id=session_id,
        )
        return _json_result({"receipt": receipt})
    except (FactoryError, OSError, ValueError) as exc:
        return _json_result({"error": str(exc)}, success=False)


def _handle_lint_card(params: dict[str, Any], **_: Any) -> str:
    result = lint_card(str(params.get("title", "")), str(params.get("body", "")))
    return _json_result(result, success=bool(result["valid"]))


def _active_model_catalog() -> dict[str, Any]:
    """Use Hermes' own authenticated picker inventory for this profile."""
    from hermes_cli.inventory import build_models_payload, load_picker_context

    payload = build_models_payload(
        load_picker_context(),
        explicit_only=True,
        include_unconfigured=False,
        picker_hints=True,
        canonical_order=True,
        pricing=False,
        capabilities=False,
        refresh=False,
        probe_custom_providers=False,
        for_picker=True,
        max_models=200,
    )
    providers: list[dict[str, Any]] = []
    provider_rows = payload.get("providers") if isinstance(payload, dict) else None
    for row in provider_rows if isinstance(provider_rows, list) else []:
        if not isinstance(row, dict) or row.get("authenticated") is not True:
            continue
        slug_value = row.get("slug")
        slug = slug_value.strip().lower() if isinstance(slug_value, str) else ""
        model_rows = row.get("models")
        models = [
            value.strip()
            for value in model_rows
            if isinstance(value, str) and value.strip()
        ] if isinstance(model_rows, list) else []
        if slug and models:
            providers.append(
                {"slug": slug, "authenticated": True, "models": models}
            )
    current_provider = payload.get("provider") if isinstance(payload, dict) else ""
    current_model = payload.get("model") if isinstance(payload, dict) else ""
    return {
        "providers": providers,
        "current": {
            "provider": current_provider.strip().lower() if isinstance(current_provider, str) else "",
            "model": current_model.strip() if isinstance(current_model, str) else "",
        },
        "credentials_included": False,
    }


def _active_catalog_refs(catalog: Any) -> set[tuple[str, str]]:
    """Return credential-free provider/model refs from authenticated rows only."""
    rows = catalog.get("providers", []) if isinstance(catalog, dict) else []
    refs: set[tuple[str, str]] = set()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get("authenticated") is not True:
            continue
        slug_value = row.get("slug")
        provider = slug_value.strip().lower() if isinstance(slug_value, str) else ""
        models = row.get("models") if isinstance(row.get("models"), list) else []
        for value in models:
            model = value.strip() if isinstance(value, str) else ""
            if provider and model:
                refs.add((provider, model))
    return refs


_INVENTORY_UNAVAILABLE = "authenticated active-profile model inventory is unavailable"


def _validate_runtime_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Validate structure and every role ref against this profile's live inventory."""
    check = validate_manifest(manifest)
    errors = list(check.get("errors") or [])
    warnings = list(check.get("warnings") or [])
    inventory_unavailable = False
    if not errors:
        try:
            active_refs = _active_catalog_refs(_active_model_catalog())
        except Exception:  # Hermes inventory failures must not leak provider/auth detail.
            active_refs = set()
            inventory_unavailable = True
            errors.append(_INVENTORY_UNAVAILABLE)
        if not errors:
            models = manifest.get("models") if isinstance(manifest.get("models"), dict) else {}
            for role in ("integrator", "builder", "verifier", "adversary", "holdout"):
                ref = models.get(role) if isinstance(models.get(role), dict) else {}
                provider = str(ref.get("provider") or "").strip().lower()
                model = str(ref.get("model") or "").strip()
                if (provider, model) not in active_refs:
                    errors.append(
                        f"models.{role} provider/model is not present in the authenticated active-profile inventory"
                    )
    result: dict[str, Any] = {"valid": not errors, "errors": errors, "warnings": warnings}
    if inventory_unavailable:
        result["code"] = "model_inventory_unavailable"
    return result


def _inventory_unavailable_result() -> str:
    """Return one credential-free, fail-closed inventory failure shape."""
    return _json_result(
        {
            "error": _INVENTORY_UNAVAILABLE,
            "errors": [_INVENTORY_UNAVAILABLE],
            "code": "model_inventory_unavailable",
        },
        success=False,
    )


def _handle_preflight(params: dict[str, Any], **_: Any) -> str:
    try:
        setup = normalise_setup(params.get("setup") if isinstance(params.get("setup"), dict) else load_setup())
        try:
            catalog = _active_model_catalog()
        except Exception:
            return _inventory_unavailable_result()
        setup = resolve_setup_models(setup, catalog)
        result = validate_intake(setup, model_catalog=catalog)
        return _json_result({"readiness": result, "setup": setup}, success=bool(result["ready"]))
    except (FactoryError, OSError, ValueError) as exc:
        return _json_result({"error": str(exc)}, success=False)


def _handle_compile(params: dict[str, Any], **_: Any) -> str:
    try:
        setup = normalise_setup(params.get("setup") if isinstance(params.get("setup"), dict) else load_setup())
        try:
            catalog = _active_model_catalog()
        except Exception:
            return _inventory_unavailable_result()
        setup = resolve_setup_models(setup, catalog)
        readiness = validate_intake(setup, model_catalog=catalog)
        if not readiness["ready"]:
            return _json_result({"error": "factory intake is not ready", "readiness": readiness}, success=False)
        result = compile_to_workspace(setup, model_catalog=catalog)
        return _json_result({"readiness": readiness, **result})
    except (FactoryError, OSError, ValueError) as exc:
        return _json_result({"error": str(exc)}, success=False)


def _handle_import_manifest(params: dict[str, Any], **_: Any) -> str:
    """Import a validated manifest without applying its Beads graph."""
    try:
        source_path = ""
        manifest_path_value = params.get("manifest_path")
        inline_manifest = params.get("manifest")
        if manifest_path_value and inline_manifest is not None:
            raise FactoryError("provide either manifest_path or manifest, not both")
        if manifest_path_value:
            source_path = str(Path(str(manifest_path_value)).expanduser().resolve())
            candidate = load_manifest(source_path)
        elif isinstance(inline_manifest, dict):
            candidate = copy.deepcopy(inline_manifest)
        else:
            raise FactoryError("manifest_path or manifest is required")
        workspace_override = params.get("workspace_path")
        if workspace_override is not None:
            if not isinstance(workspace_override, str) or not workspace_override.strip():
                raise FactoryError("workspace_path must be a non-empty path")
            mission = candidate.get("mission")
            if not isinstance(mission, dict):
                raise FactoryError("manifest import requires a mission object")
            mission["workspace_path"] = str(Path(workspace_override).expanduser().resolve())
        try:
            catalog = _active_model_catalog()
        except Exception:
            return _inventory_unavailable_result()
        check = _validate_runtime_manifest(candidate)
        if not check["valid"]:
            return _json_result(
                {
                    "error": "manifest import is not ready",
                    "readiness": check,
                    "source_path": source_path,
                },
                success=False,
            )
        result = import_manifest_to_workspace(
            candidate,
            workspace_path=None,
        )
        return _json_result(
            {
                "readiness": check,
                "source_path": source_path,
                "model_catalog_profile": catalog.get("profile") if isinstance(catalog, dict) else "",
                **result,
            }
        )
    except (FactoryError, OSError, ValueError) as exc:
        return _json_result({"error": str(exc)}, success=False)


def _beads_settings(manifest: dict[str, Any], params: dict[str, Any]) -> tuple[str, str, bool]:
    execution = manifest.get("execution") if isinstance(manifest.get("execution"), dict) else {}
    workspace = Path(str(manifest.get("mission", {}).get("workspace_path") or ".")).expanduser().resolve()
    beads_directory = str(execution.get("beads_directory") or (workspace / ".beads"))
    # Tool callers cannot select a different executable. preflight_beads resolves
    # this pinned command from PATH before any graph operation.
    executable = "bd"
    authorized = execution.get("beads_isolated_authorized") is True
    return beads_directory, executable, authorized


def _handle_beads_plan(params: dict[str, Any], **_: Any) -> str:
    try:
        manifest_path, _state_path = _resolve_paths(params)
        manifest = load_manifest(manifest_path)
        check = _validate_runtime_manifest(manifest)
        if not check["valid"]:
            return _json_result({"error": "invalid or unavailable runtime manifest", **check}, success=False)
        return _json_result({"manifest_path": manifest_path, "graph_plan": build_graph_plan(manifest, validate=False)})
    except (FactoryError, BeadsAdapterError, OSError, ValueError) as exc:
        return _json_result({"error": str(exc)}, success=False)


_BEADS_APPLY_AUTHORIZATION_ERROR = "Beads graph application is not authorized"


def _pinned_beads_apply_paths(params: dict[str, Any]) -> tuple[str, str]:
    """Resolve apply artifacts only from explicit runtime pins."""
    if not os.environ.get("HERMES_FACTORY_MANIFEST", "").strip() or not os.environ.get(
        "HERMES_FACTORY_STATE", ""
    ).strip():
        raise FactoryError(_BEADS_APPLY_AUTHORIZATION_ERROR)
    try:
        return _resolve_paths(params)
    except (FactoryError, OSError, RuntimeError, ValueError) as exc:
        raise FactoryError(_BEADS_APPLY_AUTHORIZATION_ERROR) from exc


def _handle_beads_apply(params: dict[str, Any], **runtime: Any) -> str:
    try:
        manifest_path, state_path = _pinned_beads_apply_paths(params)
        try:
            manifest = load_manifest(manifest_path)
        except (FactoryError, OSError, RuntimeError, ValueError) as exc:
            raise FactoryError(_BEADS_APPLY_AUTHORIZATION_ERROR) from exc

        execution = manifest.get("execution") if isinstance(manifest.get("execution"), dict) else {}
        if execution.get("graph_backend") != "beads":
            raise FactoryError("manifest execution.graph_backend is not beads")
        if execution.get("graph_mode") != "apply":
            raise FactoryError("manifest execution.graph_mode is not apply; plan mode is read-only")
        check = _validate_runtime_manifest(manifest)
        if not check["valid"]:
            return _json_result({"error": "invalid or unavailable runtime manifest", **check}, success=False)

        configured = manifest.get("models", {}).get("integrator", {})
        session_id = runtime.get("session_id")
        actor = {
            "session_id": session_id,
            "provider": os.environ.get("HERMES_FACTORY_PROVIDER", ""),
            "model": os.environ.get("HERMES_FACTORY_MODEL", ""),
        }
        if (
            os.environ.get("HERMES_FACTORY_ROLE", "") != "integrator"
            or not isinstance(session_id, str)
            or not session_id
            or session_id != session_id.strip()
            or actor["provider"] != configured.get("provider")
            or actor["model"] != configured.get("model")
        ):
            raise FactoryError(_BEADS_APPLY_AUTHORIZATION_ERROR)

        try:
            requested_state = Path(state_path).expanduser().resolve()
            if not requested_state.is_file():
                raise FactoryError(_BEADS_APPLY_AUTHORIZATION_ERROR)
        except (FactoryError, OSError, RuntimeError, ValueError) as exc:
            raise FactoryError(_BEADS_APPLY_AUTHORIZATION_ERROR) from exc
        try:
            with _state_file_lock(requested_state):
                state, _resolved_state = load_state(manifest, requested_state)
                if state.get("integrator_authority") != actor:
                    raise FactoryError(_BEADS_APPLY_AUTHORIZATION_ERROR)
                beads_directory, executable, authorized = _beads_settings(manifest, params)
                result = apply_graph_plan(
                    manifest,
                    beads_directory,
                    bd_executable=executable,
                    authorize_isolated=authorized,
                )
        except BeadsAdapterError:
            raise
        except (FactoryError, OSError, RuntimeError, ValueError) as exc:
            raise FactoryError(_BEADS_APPLY_AUTHORIZATION_ERROR) from exc
        return _json_result({"manifest_path": manifest_path, **result})
    except (FactoryError, BeadsAdapterError, OSError, ValueError) as exc:
        return _json_result({"error": str(exc)}, success=False)


def _strict_enabled() -> bool:
    return os.environ.get("HERMES_FACTORY_STRICT", "").lower() in {"1", "true", "yes", "on"}


def _strict_manifest_active() -> bool:
    path = os.environ.get("HERMES_FACTORY_MANIFEST", "").strip()
    return bool(path and Path(path).expanduser().exists())


FACTORY_ROLES = {"integrator", "builder", "reviewer", "verifier", "adversary", "holdout"}
REVIEW_ROLES = {"reviewer", "verifier", "adversary", "holdout"}
REVIEW_READ_ONLY_TOOLS = {
    "read_file", "search_files", "web_search", "web_extract", "browser_snapshot",
    "browser_get_images", "vision_analyze", "factory_attest_review", "tool_search",
    "tool_describe",
}
_FACTORY_WRITE_TOOLS = {"write_file", "patch", "terminal", "execute_code"}
_PATH_ARG_NAMES = {"path", "file_path", "output_path", "destination", "target", "workdir"}


def _is_within(path: Path, directory: Path) -> bool:
    return path == directory or directory in path.parents


def _explicit_tool_paths(tool_name: str, args: dict[str, Any]) -> list[Path]:
    """Resolve only path-bearing arguments, never product content or source text."""
    values: list[str] = []
    for key, value in args.items():
        canonical_key = str(key).strip().lower()
        if canonical_key in _PATH_ARG_NAMES or canonical_key.endswith("_path"):
            if isinstance(value, (str, os.PathLike)) and str(value).strip():
                values.append(str(value))
            elif isinstance(value, list):
                values.extend(str(item) for item in value if isinstance(item, (str, os.PathLike)) and str(item).strip())
    if tool_name == "patch" and isinstance(args.get("patch"), str):
        values.extend(
            match.group(1).strip()
            for match in re.finditer(
                r"(?m)^\*\*\* (?:Add|Update|Delete) File:\s*(.+?)\s*$", args["patch"]
            )
        )
    paths: list[Path] = []
    for value in values:
        try:
            paths.append(Path(value).expanduser().resolve())
        except (OSError, RuntimeError, ValueError):
            continue
    return paths


def _protected_factory_paths() -> tuple[set[Path], set[Path]]:
    configured_manifest = os.environ.get("HERMES_FACTORY_MANIFEST", "").strip()
    configured_state = os.environ.get("HERMES_FACTORY_STATE", "").strip()
    if configured_manifest and not configured_state:
        configured_state = str(Path(configured_manifest).expanduser().resolve().with_name("state.json"))
    raw_paths = [value for value in (configured_manifest, configured_state) if value]
    protected_files: set[Path] = set()
    protected_directories = {Path(DEFAULT_MANIFEST).expanduser().resolve().parent}
    for value in raw_paths:
        resolved = Path(value).expanduser().resolve()
        protected_files.add(resolved)
        # A pinned manifest/state makes its containing control directory part of
        # the protected surface even outside the default .hermes/factory path.
        # resolve() folds existing symlink aliases into the same surface.
        protected_directories.add(resolved.parent)
    return protected_files, protected_directories


def _resolved_source_path(value: str, base: Path) -> Path | None:
    """Resolve one lexically recovered shell/code path without requiring existence."""
    candidate = value.strip().strip("'\"")
    if not candidate or candidate.startswith("-") or "\x00" in candidate:
        return None
    try:
        path = Path(os.path.expandvars(candidate)).expanduser()
        if not path.is_absolute():
            path = base / path
        return path.resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def _is_protected_source_path(
    value: str,
    bases: set[Path],
    protected_files: set[Path],
    protected_directories: set[Path],
) -> bool:
    for base in bases:
        target = _resolved_source_path(value, base)
        if target is not None and (
            target in protected_files
            or any(_is_within(target, directory) for directory in protected_directories)
        ):
            return True
    return False


_SHELL_VARIABLE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
_SHELL_ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.DOTALL)


def _expand_shell_variables(value: str, variables: dict[str, str]) -> str:
    expanded = value
    # Resolve ordinary compositions (including forward aliases that became
    # known later) without interpreting command substitutions or executing a
    # shell. The bounded loop also makes self/cyclic references harmless.
    for _ in range(len(variables) + 1):
        previous = expanded
        expanded = _SHELL_VARIABLE.sub(
            lambda match: variables.get(match.group(1) or match.group(2) or "", match.group(0)),
            expanded,
        )
        if expanded == previous:
            break
    return expanded


def _shell_source_targets_control_path(
    source: str,
    args: dict[str, Any],
    protected_files: set[Path],
    protected_directories: set[Path],
) -> bool:
    """Conservatively recover paths and simple aliases from shell source."""
    try:
        lexer = shlex.shlex(source, posix=True, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        # Unparseable direct references still reach the marker fallback below.
        return False

    initial = _resolved_source_path(str(args.get("workdir") or Path.cwd()), Path.cwd())
    initial = initial or Path.cwd().resolve()
    bases: set[Path] = {initial}
    current = initial
    # shlex does not supply a shell environment. Seed only the deterministic
    # directory variables whose values are known from the tool invocation.
    variables: dict[str, str] = {"PWD": str(initial), "OLDPWD": str(initial)}
    expect_cd_path = False
    for token in tokens:
        if not token or set(token) <= set(";&|<>"):
            continue
        assignment = _SHELL_ASSIGNMENT.match(token)
        if assignment:
            value = _expand_shell_variables(assignment.group(2), variables)
            variables[assignment.group(1)] = value
            if _is_protected_source_path(value, bases, protected_files, protected_directories):
                return True
            continue
        expanded = _expand_shell_variables(token, variables)
        if expect_cd_path:
            destination = _resolved_source_path(expanded, current)
            if destination is not None:
                variables["OLDPWD"] = str(current)
                current = destination
                bases.add(destination)
                variables["PWD"] = str(destination)
            expect_cd_path = False
            continue
        if expanded == "cd":
            expect_cd_path = True
            continue
        if _is_protected_source_path(expanded, bases, protected_files, protected_directories):
            return True
    return False


_OS_PATH_CALLS = {
    "access", "chmod", "chown", "lchown", "link", "listdir", "lstat",
    "makedirs", "mkdir", "open", "readlink", "remove", "removedirs",
    "rename", "renames", "replace", "rmdir", "scandir", "stat", "symlink",
    "truncate", "unlink", "utime", "walk",
}


class _PythonPathProbe(ast.NodeVisitor):
    """Recover ordinary literal paths from Python AST without executing code."""

    def __init__(
        self,
        base: Path,
        protected_files: set[Path],
        protected_directories: set[Path],
    ) -> None:
        self.base = base
        self.protected_files = protected_files
        self.protected_directories = protected_directories
        self.aliases: dict[str, str] = {}
        self.string_aliases: set[str] = set()
        self.path_aliases: set[str] = set()
        # Common names are seeded because execute_code uses a persistent Python
        # kernel. Imports and ordinary aliases below extend these classifications.
        self.path_classes: set[str] = {"Path"}
        self.pathlib_modules: set[str] = set()
        self.os_modules: set[str] = {"os"}
        self.os_path_modules: set[str] = set()
        self.builtin_modules: set[str] = {"builtins"}
        self.io_modules: set[str] = {"io"}
        self.open_functions: set[str] = {"open"}
        self.os_path_functions: set[str] = set()
        self.join_functions: set[str] = set()
        self.blocked = False

    def _is_path_reference(self, node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Name) and node.id in self.path_classes
        ) or (
            isinstance(node, ast.Attribute)
            and node.attr == "Path"
            and isinstance(node.value, ast.Name)
            and node.value.id in self.pathlib_modules
        )

    def _is_pathlib_module_reference(self, node: ast.AST) -> bool:
        return isinstance(node, ast.Name) and node.id in self.pathlib_modules

    def _is_os_module_reference(self, node: ast.AST) -> bool:
        return isinstance(node, ast.Name) and node.id in self.os_modules

    def _is_os_path_reference(self, node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Name) and node.id in self.os_path_modules
        ) or (
            isinstance(node, ast.Attribute)
            and node.attr == "path"
            and self._is_os_module_reference(node.value)
        )

    def _is_open_reference(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id in self.open_functions
        return (
            isinstance(node, ast.Attribute)
            and node.attr == "open"
            and isinstance(node.value, ast.Name)
            and (
                node.value.id in self.builtin_modules
                or node.value.id in self.io_modules
            )
        )

    def _is_os_path_function_reference(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id in self.os_path_functions
        return (
            isinstance(node, ast.Attribute)
            and node.attr in _OS_PATH_CALLS
            and self._is_os_module_reference(node.value)
        )

    def _is_join_reference(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id in self.join_functions
        return (
            isinstance(node, ast.Attribute)
            and node.attr == "join"
            and self._is_os_path_reference(node.value)
        )

    def _reference_kind(self, node: ast.AST) -> str | None:
        if self._is_path_reference(node):
            return "path_class"
        if self._is_pathlib_module_reference(node):
            return "pathlib_module"
        if self._is_os_module_reference(node):
            return "os_module"
        if self._is_os_path_reference(node):
            return "os_path_module"
        if self._is_open_reference(node):
            return "open_function"
        if self._is_os_path_function_reference(node):
            return "os_path_function"
        if self._is_join_reference(node):
            return "join_function"
        if isinstance(node, ast.Name) and node.id in self.builtin_modules:
            return "builtin_module"
        if isinstance(node, ast.Name) and node.id in self.io_modules:
            return "io_module"
        return None

    def _clear_name(self, name: str) -> None:
        for names in (
            self.path_classes,
            self.pathlib_modules,
            self.os_modules,
            self.os_path_modules,
            self.builtin_modules,
            self.io_modules,
            self.open_functions,
            self.os_path_functions,
            self.join_functions,
        ):
            names.discard(name)
        self.aliases.pop(name, None)
        self.string_aliases.discard(name)
        self.path_aliases.discard(name)

    def _record_assignment(self, target: ast.AST, value_node: ast.AST | None) -> None:
        if not isinstance(target, ast.Name):
            return
        name = target.id
        reference_sets = {
            "path_class": self.path_classes,
            "pathlib_module": self.pathlib_modules,
            "os_module": self.os_modules,
            "os_path_module": self.os_path_modules,
            "builtin_module": self.builtin_modules,
            "io_module": self.io_modules,
            "open_function": self.open_functions,
            "os_path_function": self.os_path_functions,
            "join_function": self.join_functions,
        }
        reference_kind = self._reference_kind(value_node) if value_node is not None else None
        path_value = (
            self._path_value(value_node)
            if value_node is not None and reference_kind is None
            else None
        )
        string_value = (
            self._string_value(value_node)
            if value_node is not None and reference_kind is None and path_value is None
            else None
        )
        self._clear_name(name)
        if value_node is None:
            return
        if reference_kind is not None:
            reference_sets[reference_kind].add(name)
            return
        if path_value is not None:
            self.aliases[name] = path_value
            self.path_aliases.add(name)
            return
        if string_value is not None:
            self.aliases[name] = string_value
            self.string_aliases.add(name)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802 - ast API
        for imported in node.names:
            bound = imported.asname or imported.name.split(".", 1)[0]
            self._clear_name(bound)
            if imported.name == "pathlib":
                self.pathlib_modules.add(bound)
            elif imported.name == "os":
                self.os_modules.add(bound)
            elif imported.name == "os.path":
                if imported.asname:
                    self.os_path_modules.add(bound)
                else:
                    self.os_modules.add(bound)
            elif imported.name == "builtins":
                self.builtin_modules.add(bound)
            elif imported.name == "io":
                self.io_modules.add(bound)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802 - ast API
        if node.level != 0:
            return
        for imported in node.names:
            if imported.name == "*":
                continue
            bound = imported.asname or imported.name
            self._clear_name(bound)
            if node.module == "pathlib" and imported.name == "Path":
                self.path_classes.add(bound)
            elif node.module == "os" and imported.name == "path":
                self.os_path_modules.add(bound)
            elif node.module == "os" and imported.name in _OS_PATH_CALLS:
                self.os_path_functions.add(bound)
            elif node.module == "os.path" and imported.name == "join":
                self.join_functions.add(bound)
            elif node.module in {"builtins", "io"} and imported.name == "open":
                self.open_functions.add(bound)

    def _string_value(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name) and node.id in self.string_aliases:
            return self.aliases.get(node.id)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self._string_value(node.left)
            right = self._string_value(node.right)
            if left is not None and right is not None:
                return left + right
        if isinstance(node, ast.JoinedStr):
            parts: list[str] = []
            for item in node.values:
                if isinstance(item, ast.Constant) and isinstance(item.value, str):
                    parts.append(item.value)
                elif (
                    isinstance(item, ast.FormattedValue)
                    and item.conversion == -1
                    and item.format_spec is None
                ):
                    value = self._string_value(item.value)
                    if value is None:
                        return None
                    parts.append(value)
                else:
                    return None
            return "".join(parts)
        if isinstance(node, ast.Call) and self._is_join_reference(node.func):
            parts = [self._string_value(item) for item in node.args]
            if parts and all(part is not None for part in parts):
                return os.path.join(*(part or "" for part in parts))
        return None

    def _path_value(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name) and node.id in self.path_aliases:
            return self.aliases.get(node.id)
        if isinstance(node, ast.Call):
            if self._is_path_reference(node.func) and len(node.args) <= 1:
                return self._value(node.args[0]) if node.args else "."
            if isinstance(node.func, ast.Attribute):
                attribute = node.func.attr
                if (
                    attribute == "cwd"
                    and self._is_path_reference(node.func.value)
                    and not node.args
                ):
                    return str(self.base)
                if attribute in {"resolve", "absolute"}:
                    value = self._path_value(node.func.value)
                    if value is not None:
                        resolved = _resolved_source_path(value, self.base)
                        return str(resolved) if resolved is not None else value
                if attribute == "joinpath":
                    value = self._path_value(node.func.value)
                    parts = [self._value(item) for item in node.args]
                    if value is not None and all(part is not None for part in parts):
                        return str(Path(value).joinpath(*(part or "" for part in parts)))
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            left = self._path_value(node.left)
            right = self._value(node.right)
            if left is not None and right is not None:
                return str(Path(left) / right)
        return None

    def _value(self, node: ast.AST) -> str | None:
        path_value = self._path_value(node)
        return path_value if path_value is not None else self._string_value(node)

    def _check(self, value: str | None) -> None:
        if value is not None and _is_protected_source_path(
            value,
            {self.base},
            self.protected_files,
            self.protected_directories,
        ):
            self.blocked = True

    def _check_call_path_arguments(self, node: ast.Call) -> None:
        if not (
            self._is_open_reference(node.func)
            or self._is_os_path_function_reference(node.func)
        ):
            return
        if node.args:
            self._check(self._value(node.args[0]))
        # rename/replace/link/symlink have a second path coordinate.
        if len(node.args) > 1 and (
            isinstance(node.func, ast.Name) and node.func.id in self.os_path_functions
            or isinstance(node.func, ast.Attribute) and node.func.attr in {"rename", "renames", "replace", "link", "symlink"}
        ):
            self._check(self._value(node.args[1]))
        for keyword in node.keywords:
            if keyword.arg in {"file", "path", "src", "dst", "source", "destination"}:
                self._check(self._value(keyword.value))

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802 - ast API
        # Every chained target receives the same known value. Unknown rebinding
        # deliberately clears stale knowledge instead of causing a false block.
        for target in node.targets:
            self._record_assignment(target, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802 - ast API
        self._record_assignment(node.target, node.value)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:  # noqa: N802 - ast API
        if isinstance(node.target, ast.Name):
            name = node.target.id
            current = self.aliases.get(name) if name in self.string_aliases else None
            suffix = self._string_value(node.value)
            self._clear_name(name)
            if isinstance(node.op, ast.Add) and current is not None and suffix is not None:
                self.aliases[name] = current + suffix
                self.string_aliases.add(name)
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:  # noqa: N802 - ast API
        self._record_assignment(node.target, node.value)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 - ast API
        if isinstance(node.func, ast.Attribute):
            self._check(self._path_value(node.func.value))
        self._check_call_path_arguments(node)
        self.generic_visit(node)

    def visit_Expr(self, node: ast.Expr) -> None:  # noqa: N802 - ast API
        # Preserve the existing strict guard for a bare reconstructed pathlib
        # target while avoiding eager blocking of aliases later rebound unknown.
        self._check(self._path_value(node.value))
        self.generic_visit(node)


def _python_source_targets_control_path(
    source: str,
    args: dict[str, Any],
    protected_files: set[Path],
    protected_directories: set[Path],
) -> bool:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return False
    base = _resolved_source_path(str(args.get("workdir") or Path.cwd()), Path.cwd())
    probe = _PythonPathProbe(base or Path.cwd().resolve(), protected_files, protected_directories)
    probe.visit(tree)
    return probe.blocked


def _targets_factory_control_path(tool_name: str, args: dict[str, Any]) -> bool:
    protected_files, protected_directories = _protected_factory_paths()
    canonical_target = any(
        target in protected_files or any(_is_within(target, directory) for directory in protected_directories)
        for target in _explicit_tool_paths(tool_name, args)
    )
    if canonical_target:
        return True

    source_key = "command" if tool_name == "terminal" else "code" if tool_name == "execute_code" else ""
    source = str(args.get(source_key, "")) if source_key else ""
    if tool_name == "terminal" and _shell_source_targets_control_path(
        source, args, protected_files, protected_directories
    ):
        return True
    if tool_name == "execute_code" and _python_source_targets_control_path(
        source, args, protected_files, protected_directories
    ):
        return True

    # Preserve the strict guard for direct shell/code references without
    # treating ordinary file content as a path (which would block product docs).
    normalized_source = source.replace("\\", "/").lower()
    direct_markers = {
        ".hermes/factory",
        os.environ.get("HERMES_FACTORY_MANIFEST", "").replace("\\", "/").lower(),
        os.environ.get("HERMES_FACTORY_STATE", "").replace("\\", "/").lower(),
    } - {""}
    # This is a lexical pre-execution hook, not a sandbox: deliberately
    # obfuscated arbitrary code can exceed its guarantees. Runtime state HMACs
    # remain the independent tamper detector; the hook must not claim more.
    return bool(normalized_source and any(marker in normalized_source for marker in direct_markers))


_PATH_COORDINATE = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"(?:[A-Za-z]:[\\/]|/|\.{1,2}[\\/])?"
    r"(?:[A-Za-z0-9_.*?\[\]{}-]+[\\/])+[A-Za-z0-9_.*?\[\]{}-]+"
    r"|(?:\.?[A-Za-z0-9_*?\[\]{}-]+(?:\.[A-Za-z0-9_*?\[\]{}-]+)+)"
    r")"
)


def _delegation_boundary_paths(goal: str) -> list[str]:
    """Extract explicit path/glob coordinates using the canonical card parser."""
    boundary_lines = _parse_card_sections(str(goal or ""))["Boundaries"]
    paths: list[str] = []
    for match in _PATH_COORDINATE.finditer("\n".join(boundary_lines)):
        value = match.group(0).rstrip(".,;:").replace("\\", "/")
        if value and value not in paths:
            paths.append(value)
    return paths


def _normalised_card_value(lines: list[str]) -> str:
    return " ".join(" ".join(lines).split())


def _canonical_path_contract(
    paths: list[str], workspace_path: str | Path
) -> frozenset[tuple[str, ...]] | None:
    canonical: set[tuple[str, ...]] = set()
    for value in paths:
        coordinate = _canonical_ownership_pattern(value, workspace_path)
        if coordinate is None:
            return None
        canonical.add(coordinate)
    return frozenset(canonical)


_DELEGATION_CONTRACT_ERROR = (
    "dark-factory delegation requires exact compiled startable slice contracts"
)


def _delegation_block(message: str = _DELEGATION_CONTRACT_ERROR) -> dict[str, str]:
    return {"action": "block", "message": message}


def _is_review_secret_path(value: str) -> bool:
    if not value.strip():
        return False
    normalized = value.replace("\\", "/").lower()
    if "review-attestation.key" in normalized or "plugin-data/dark-factory" in normalized:
        return True
    try:
        target = Path(value).expanduser().resolve()
        hermes_home = Path(os.environ.get("HERMES_HOME") or "~/.hermes").expanduser().resolve()
        secret_directory = (hermes_home / "plugin-data" / "dark-factory").resolve()
        return target.name == "review-attestation.key" or _is_within(target, secret_directory)
    except (OSError, RuntimeError, ValueError):
        return False


def _pre_tool_guard(tool_name: str = "", args: Any = None, **_: Any) -> dict[str, str] | None:
    """Enforce reviewer isolation always and other factory controls in strict mode."""
    role = os.environ.get("HERMES_FACTORY_ROLE", "").strip().lower()
    safe_args = args if isinstance(args, dict) else {}
    if role in REVIEW_ROLES and tool_name in {"read_file", "search_files"}:
        if _is_review_secret_path(str(safe_args.get("path", ""))):
            return {"action": "block", "message": "dark-factory review attestation secrets are not readable by reviewer roles"}
    if role in REVIEW_ROLES and tool_name == "tool_call":
        if safe_args.get("name") == "factory_attest_review":
            return None
        return {
            "action": "block",
            "message": f"dark-factory {role} may use deferred tools only for factory_attest_review",
        }
    if role in REVIEW_ROLES and tool_name not in REVIEW_READ_ONLY_TOOLS:
        return {
            "action": "block",
            "message": f"dark-factory {role} is evidence-only; this tool is outside the read-only reviewer allowlist",
        }

    if not (_strict_enabled() and _strict_manifest_active() and isinstance(args, dict)):
        return None

    if role in FACTORY_ROLES and tool_name in _FACTORY_WRITE_TOOLS and _targets_factory_control_path(tool_name, args):
        return {"action": "block", "message": "dark-factory state and manifest may be changed only through factory tools"}

    if tool_name == "factory_beads_apply" and role != "integrator":
        return {
            "action": "block",
            "message": "only the Dark Factory integrator/orchestrator may apply the Beads graph",
        }

    if tool_name == "terminal":
        command = str(args.get("command", ""))
        if "<<" in command:
            return {
                "action": "block",
                "message": "dark-factory guard blocks inline heredoc scripts; materialise a reviewed script file and invoke it explicitly",
            }

    if tool_name == "kanban_create":
        result = lint_card(str(args.get("title", "")), str(args.get("body", "")))
        if not result["valid"]:
            return {
                "action": "block",
                "message": "dark-factory guard refused an uncontracted Kanban card: " + "; ".join(result["errors"]),
            }

    if tool_name == "delegate_task":
        tasks = args.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            return _delegation_block()
        try:
            manifest_path = os.environ.get("HERMES_FACTORY_MANIFEST", "")
            manifest = load_manifest(manifest_path)
            manifest_check = validate_manifest(manifest)
            if not manifest_check["valid"]:
                return _delegation_block()
            configured_limit = int(manifest.get("policy", {}).get("max_parallel_slices", 2))
            worker_limit = min(2, configured_limit)
            workspace_path = str(manifest.get("mission", {}).get("workspace_path") or "")
            slice_specs = _entity_map(manifest, "slices")
            milestone_specs = _entity_map(manifest, "milestones")

            configured_state = os.environ.get("HERMES_FACTORY_STATE", "").strip()
            if not configured_state:
                return _delegation_block()
            state_candidate = Path(configured_state).expanduser().resolve()
            state, _ = load_state(manifest, state_candidate)
            startable_slices = set(next_actions(manifest, state)["startable_slices"])
        except (FactoryError, OSError, ValueError, TypeError, RuntimeError, KeyError):
            return _delegation_block()

        if len(tasks) > worker_limit:
            return _delegation_block(
                f"dark-factory guard allows at most {worker_limit} concurrent workers from manifest policy"
            )

        worker_paths: list[list[str]] = []
        delegated_slice_ids: set[str] = set()
        for task in tasks:
            if not isinstance(task, dict):
                return _delegation_block()
            goal = str(task.get("goal", ""))
            title = str(task.get("title") or task.get("name") or "Delegated worker")
            contract = lint_card(title, goal)
            sections = _parse_card_sections(goal)
            if not contract["valid"]:
                return _delegation_block()

            milestone_id = _normalised_card_value(sections["Factory-Milestone"])
            slice_id = _normalised_card_value(sections["Factory-Slice"])
            outcome = _normalised_card_value(sections["Outcome"])
            if (
                not milestone_id
                or not slice_id
                or not outcome
                or milestone_id not in milestone_specs
                or slice_id not in slice_specs
                or slice_id in delegated_slice_ids
            ):
                return _delegation_block()
            slice_spec = slice_specs[slice_id]
            milestone_spec = milestone_specs[milestone_id]
            if (
                str(slice_spec.get("milestone_id") or "") != milestone_id
                or slice_id not in list(milestone_spec.get("slices") or [])
                or outcome != " ".join(str(slice_spec.get("outcome") or "").split())
                or slice_id not in startable_slices
            ):
                return _delegation_block()

            declared_paths = _delegation_boundary_paths(goal)
            compiled_paths = [str(value) for value in slice_spec.get("paths", [])]
            declared_contract = _canonical_path_contract(declared_paths, workspace_path)
            compiled_contract = _canonical_path_contract(compiled_paths, workspace_path)
            if (
                not declared_paths
                or declared_contract is None
                or compiled_contract is None
                or declared_contract != compiled_contract
            ):
                return _delegation_block()
            delegated_slice_ids.add(slice_id)
            worker_paths.append(compiled_paths)

        for left_index, left_paths in enumerate(worker_paths):
            for right_paths in worker_paths[left_index + 1:]:
                if _paths_overlap(left_paths, right_paths, workspace_path):
                    return _delegation_block(
                        "dark-factory worker-set path ownership overlaps or cannot be proven disjoint"
                    )

    if tool_name == "kanban_complete":
        metadata = args.get("metadata")
        required = ("factory_slice_id", "candidate_sha", "checks", "acceptance_results")
        if not isinstance(metadata, dict) or any(metadata.get(key) in (None, "", []) for key in required):
            return {
                "action": "block",
                "message": "dark-factory completion requires metadata.factory_slice_id, candidate_sha, checks, and acceptance_results",
            }

    return None


COMMON_PATH_PROPERTIES = {
    "manifest_path": {
        "type": "string",
        "description": "Factory manifest path. Defaults to HERMES_FACTORY_MANIFEST or .hermes/factory/manifest.json.",
    },
    "state_path": {
        "type": "string",
        "description": "Factory state path. Defaults to HERMES_FACTORY_STATE or .hermes/factory/state.json.",
    },
}


def register(ctx: Any) -> None:
    ctx.register_tool(
        name="factory_preflight",
        toolset="dark_factory",
        schema={
            "name": "factory_preflight",
            "description": "Fail-closed readiness check for product context, personas, user stories, milestones, testing, security decisions, and active-profile model assignments. Run before creating or starting any factory mission.",
            "parameters": {
                "type": "object",
                "properties": {
                    "setup": {
                        "type": "object",
                        "description": "Optional guided setup payload. Omit to check the active profile's saved Dark Factory setup.",
                        "additionalProperties": True,
                    }
                },
                "required": [],
            },
        },
        handler=_handle_preflight,
    )
    ctx.register_tool(
        name="factory_compile",
        toolset="dark_factory",
        schema={
            "name": "factory_compile",
            "description": "Compile a preflight-ready guided setup into a versioned manifest and initial state. Refuses missing context, unavailable models, self-review assignments, or incomplete test/security gates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "setup": {
                        "type": "object",
                        "description": "Optional guided setup payload. Omit to compile the active profile's saved setup.",
                        "additionalProperties": True,
                    }
                },
                "required": [],
            },
        },
        handler=_handle_compile,
    )
    ctx.register_tool(
        name="factory_import_manifest",
        toolset="dark_factory",
        schema={
            "name": "factory_import_manifest",
            "description": "Import a canonical schema-v2 Dark Factory manifest into a new or pristine workspace. Requires Beads as the graph backend, validates active-profile models, writes manifest/state atomically, and never applies the Beads graph.",
            "parameters": {
                "type": "object",
                "properties": {
                    "manifest_path": {
                        "type": "string",
                        "description": "Absolute or user-relative path to a JSON manifest. Mutually exclusive with manifest.",
                    },
                    "manifest": {
                        "type": "object",
                        "description": "Inline canonical schema-v2 manifest. Mutually exclusive with manifest_path.",
                        "additionalProperties": True,
                    },
                    "workspace_path": {
                        "type": "string",
                        "description": "Optional workspace override. The manifest mission workspace_path is used when omitted.",
                    },
                },
                "required": [],
            },
        },
        handler=_handle_import_manifest,
    )
    ctx.register_tool(
        name="factory_validate",
        toolset="dark_factory",
        schema={
            "name": "factory_validate",
            "description": "Validate an already compiled dark-factory mission and report the next milestone-level actions. Requires existing attested state and never initializes it.",
            "parameters": {
                "type": "object",
                "properties": dict(COMMON_PATH_PROPERTIES),
                "required": [],
            },
        },
        handler=_handle_validate,
    )
    ctx.register_tool(
        name="factory_next",
        toolset="dark_factory",
        schema={
            "name": "factory_next",
            "description": "Inspect existing attested state and return only factory actions that are safe under dependency, WIP, overlap, and replan gates. Never initializes state.",
            "parameters": {
                "type": "object",
                "properties": dict(COMMON_PATH_PROPERTIES),
                "required": [],
            },
        },
        handler=_handle_next,
    )
    ctx.register_tool(
        name="factory_transition",
        toolset="dark_factory",
        schema={
            "name": "factory_transition",
            "description": "Apply one deterministic milestone/slice transition and atomically record its evidence. Invalid transitions and exhausted retry budgets are refused.",
            "parameters": {
                "type": "object",
                "properties": {
                    **COMMON_PATH_PROPERTIES,
                    "entity_id": {"type": "string"},
                    "action": {
                        "type": "string",
                        "enum": [
                            "start_milestone",
                            "start_slice",
                            "record_failure",
                            "request_review",
                            "request_changes",
                            "pass_review",
                            "complete_slice",
                            "validate_milestone",
                            "complete_milestone",
                            "block",
                            "replan",
                        ],
                    },
                    "evidence": {"type": "object", "additionalProperties": True},
                    "expected_revision": {"type": "integer", "minimum": 0, "description": "Optional compare-and-swap revision returned by factory_next/transition."},
                },
                "required": ["entity_id", "action"],
            },
        },
        handler=_handle_transition,
    )
    ctx.register_tool(
        name="factory_attest_review",
        toolset="dark_factory",
        schema={
            "name": "factory_attest_review",
            "description": "Issue a signed review receipt bound to the current Hermes reviewer session, configured role model, entity, and candidate SHA. Available only to verifier/adversary/holdout sessions.",
            "parameters": {
                "type": "object",
                "properties": {
                    **COMMON_PATH_PROPERTIES,
                    "entity_id": {"type": "string"},
                    "candidate_sha": {"type": "string"},
                    "reviewer": {"type": "string", "description": "Optional human-readable reviewer label; session ID remains authoritative."},
                },
                "required": ["entity_id", "candidate_sha"],
            },
        },
        handler=_handle_attest_review,
    )
    ctx.register_tool(
        name="factory_lint_card",
        toolset="dark_factory",
        schema={
            "name": "factory_lint_card",
            "description": "Check that a proposed durable Kanban card represents a contracted functional slice rather than an unscoped micro-task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["title", "body"],
            },
        },
        handler=_handle_lint_card,
    )
    ctx.register_tool(
        name="factory_beads_plan",
        toolset="dark_factory",
        schema={
            "name": "factory_beads_plan",
            "description": "Project the validated mission into a deterministic Beads mission/milestone/functional-slice graph without mutating Beads.",
            "parameters": {"type": "object", "properties": dict(COMMON_PATH_PROPERTIES), "required": []},
        },
        handler=_handle_beads_plan,
    )
    ctx.register_tool(
        name="factory_beads_apply",
        toolset="dark_factory",
        schema={
            "name": "factory_beads_apply",
            "description": "Integrator-only: dry-run, atomically create, verify, and receipt the configured Beads graph. Never initializes or syncs a Beads store.",
            "parameters": {
                "type": "object",
                "properties": {
                    **COMMON_PATH_PROPERTIES,
                },
                "required": [],
            },
        },
        handler=_handle_beads_apply,
    )
    ctx.register_hook("pre_tool_call", _pre_tool_guard)

    skill_path = Path(__file__).resolve().parent / "skills" / "dark-factory" / "SKILL.md"
    if skill_path.exists():
        ctx.register_skill("dark-factory", skill_path)
