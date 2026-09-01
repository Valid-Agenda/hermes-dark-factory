#!/usr/bin/env python3
"""Local CLI for the Hermes Dark Factory prototype."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
import secrets
import stat
import sys
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugin.engine import (  # noqa: E402
    DEFAULT_MANIFEST,
    DEFAULT_STATE,
    FactoryError,
    cli_attestation_key_context,
    lint_card,
    load_manifest,
    load_or_create_state,
    next_actions,
    validate_manifest,
)


def _print(data: object) -> None:
    print(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, default=str))


OFFLINE_EXECUTION_NOTE = (
    "Offline CLI does not authorize model-bound execution; "
    "use plugin factory_validate/factory_next for authenticated dispatch."
)
OFFLINE_SIGNER_BYTES = 32
OFFLINE_SIGNER_DIRECTORY = Path("plugin-data") / "dark-factory" / "offline-state-keys"
OFFLINE_LOCK_DIRECTORY = Path("plugin-data") / "dark-factory" / "offline-state-locks"
OFFLINE_SIGNER_ERROR = (
    "offline state signer is unavailable or invalid; "
    "discard and revalidate the offline state"
)
OFFLINE_TRANSITION_ERROR = (
    "offline CLI transitions are disabled; use plugin factory_transition"
)


class _SignerMissing(Exception):
    """An offline signer file does not exist."""


class _SignerInvalid(Exception):
    """An offline signer file cannot be trusted."""


class _SignerEscape(_SignerInvalid):
    """An offline signer path resolves outside the trusted profile root."""


def _canonical_state_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def _offline_pair_location(state_path: Path) -> tuple[Path, Path, Path]:
    configured_home = os.environ.get("HERMES_HOME")
    if configured_home is None:
        hermes_home = Path.home() / ".hermes"
    elif configured_home.strip():
        hermes_home = Path(configured_home).expanduser()
    else:
        raise FactoryError(OFFLINE_SIGNER_ERROR)
    identifier = hashlib.sha256(str(state_path).encode("utf-8")).hexdigest()
    try:
        profile_root = hermes_home.resolve()
    except (OSError, RuntimeError):
        raise FactoryError(OFFLINE_SIGNER_ERROR) from None
    return (
        profile_root,
        profile_root / OFFLINE_SIGNER_DIRECTORY / identifier,
        profile_root / OFFLINE_LOCK_DIRECTORY / identifier,
    )


def _require_signer_containment(profile_root: Path, path: Path) -> Path:
    """Resolve every signer path prefix beneath the canonical profile root."""

    try:
        relative = path.relative_to(profile_root)
        if not relative.parts:
            raise ValueError
        current = profile_root
        resolved = profile_root
        for part in relative.parts:
            current /= part
            resolved = current.resolve(strict=False)
            resolved_relative = resolved.relative_to(profile_root)
            if not resolved_relative.parts:
                raise ValueError
        return resolved
    except (OSError, RuntimeError, ValueError):
        raise _SignerEscape from None


def _require_opened_signer(path: Path, metadata: os.stat_result) -> None:
    """Reject a signer path that no longer names the opened regular file."""

    try:
        current = os.stat(path, follow_symlinks=False)
    except OSError:
        raise _SignerInvalid from None
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_dev != metadata.st_dev
        or current.st_ino != metadata.st_ino
    ):
        raise _SignerInvalid


def _remove_created_signer(path: Path, metadata: os.stat_result | None) -> None:
    """Best-effort cleanup without unlinking a path that was replaced."""

    if metadata is None:
        return
    try:
        current = os.stat(path, follow_symlinks=False)
        if (
            stat.S_ISREG(current.st_mode)
            and current.st_dev == metadata.st_dev
            and current.st_ino == metadata.st_ino
        ):
            path.unlink()
    except OSError:
        pass


def _read_signer(profile_root: Path, path: Path) -> bytes:
    _require_signer_containment(profile_root, path)
    flags = os.O_RDONLY
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError as exc:
        raise _SignerMissing from exc
    except OSError as exc:
        raise _SignerInvalid from exc

    metadata: os.stat_result | None = None
    close_failed = False
    try:
        metadata = os.fstat(descriptor)
        _require_signer_containment(profile_root, path)
        _require_opened_signer(path, metadata)
        if not stat.S_ISREG(metadata.st_mode):
            raise _SignerInvalid
        if os.name == "posix" and stat.S_IMODE(metadata.st_mode) != 0o600:
            raise _SignerInvalid
        data = bytearray()
        while len(data) <= OFFLINE_SIGNER_BYTES:
            chunk = os.read(descriptor, OFFLINE_SIGNER_BYTES + 1 - len(data))
            if not chunk:
                break
            data.extend(chunk)
        _require_signer_containment(profile_root, path)
        _require_opened_signer(path, metadata)
    except _SignerInvalid:
        raise
    except OSError as exc:
        raise _SignerInvalid from exc
    finally:
        try:
            os.close(descriptor)
        except OSError:
            close_failed = True

    if close_failed or metadata is None or len(data) != OFFLINE_SIGNER_BYTES:
        raise _SignerInvalid
    _require_signer_containment(profile_root, path)
    _require_opened_signer(path, metadata)
    return bytes(data)


def _new_valid_signer() -> bytes:
    for _attempt in range(16):
        candidate = secrets.token_bytes(OFFLINE_SIGNER_BYTES)
        try:
            with cli_attestation_key_context(candidate):
                pass
        except FactoryError:
            continue
        return candidate
    raise FactoryError(OFFLINE_SIGNER_ERROR)


def _create_signer(
    profile_root: Path, path: Path
) -> tuple[bytes, os.stat_result]:
    key = _new_valid_signer()
    _require_signer_containment(profile_root, path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        raise
    except OSError as exc:
        raise _SignerInvalid from exc

    metadata: os.stat_result | None = None
    failed = False
    try:
        metadata = os.fstat(descriptor)
        _require_signer_containment(profile_root, path)
        _require_opened_signer(path, metadata)
        if not stat.S_ISREG(metadata.st_mode):
            raise _SignerInvalid
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
        remaining = memoryview(key)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("incomplete signer write")
            remaining = remaining[written:]
        os.fsync(descriptor)
        _require_signer_containment(profile_root, path)
        _require_opened_signer(path, metadata)
    except (OSError, _SignerInvalid):
        failed = True
    finally:
        try:
            os.close(descriptor)
        except OSError:
            failed = True
    if failed or metadata is None:
        _remove_created_signer(path, metadata)
        raise _SignerInvalid
    try:
        _require_signer_containment(profile_root, path)
        _require_opened_signer(path, metadata)
    except _SignerInvalid:
        _remove_created_signer(path, metadata)
        raise
    return key, metadata


def _prepare_private_parent(profile_root: Path, path: Path) -> None:
    try:
        _require_signer_containment(profile_root, path.parent)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _require_signer_containment(profile_root, path.parent)
        if os.name == "posix":
            path.parent.chmod(0o700)
        _require_signer_containment(profile_root, path.parent)
        _require_signer_containment(profile_root, path)
    except (OSError, _SignerInvalid):
        raise FactoryError(OFFLINE_SIGNER_ERROR) from None


@contextmanager
def _offline_pair_lock(profile_root: Path, lock_path: Path) -> Iterator[None]:
    """Serialize signer/state pair checks with a persistent OS-locked file."""

    _prepare_private_parent(profile_root, lock_path)
    flags = os.O_RDWR | os.O_CREAT
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    acquired = False
    try:
        descriptor = os.open(lock_path, flags, 0o600)
        metadata = os.fstat(descriptor)
        _require_signer_containment(profile_root, lock_path)
        _require_opened_signer(lock_path, metadata)
        if not stat.S_ISREG(metadata.st_mode):
            raise _SignerInvalid
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
            if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o600:
                raise _SignerInvalid

        if os.name == "nt":  # pragma: no cover - exercised on Windows
            import msvcrt

            if metadata.st_size < 1:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
        acquired = True
        _require_signer_containment(profile_root, lock_path)
        _require_opened_signer(lock_path, metadata)
    except (OSError, _SignerInvalid):
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise FactoryError(OFFLINE_SIGNER_ERROR) from None

    try:
        yield
    finally:
        if descriptor is not None:
            try:
                if acquired:
                    if os.name == "nt":  # pragma: no cover - exercised on Windows
                        import msvcrt

                        os.lseek(descriptor, 0, os.SEEK_SET)
                        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def _offline_state(
    manifest: dict[str, Any], state_path: Path, *, initialize: bool
) -> tuple[dict[str, Any], Path, bytes]:
    profile_root, signer_path, lock_path = _offline_pair_location(state_path)
    try:
        resolved_signer = _require_signer_containment(profile_root, signer_path)
        resolved_lock = _require_signer_containment(profile_root, lock_path)
    except _SignerInvalid:
        raise FactoryError(OFFLINE_SIGNER_ERROR) from None

    workspace = Path(manifest["mission"]["workspace_path"]).expanduser().resolve()
    protected_roots = [workspace]
    for candidate in (workspace, *workspace.parents):
        if (candidate / ".git").exists():
            protected_roots.append(candidate)
            break
    if any(
        protected == root or protected in root.parents
        for root in (resolved_signer, resolved_lock)
        for protected in protected_roots
    ):
        raise FactoryError(OFFLINE_SIGNER_ERROR)

    with _offline_pair_lock(profile_root, lock_path):
        state_exists = state_path.exists()
        signer_exists = os.path.lexists(signer_path)
        if state_exists != signer_exists or (not state_exists and not initialize):
            raise FactoryError(OFFLINE_SIGNER_ERROR)

        if state_exists:
            try:
                key = _read_signer(profile_root, signer_path)
            except (_SignerMissing, _SignerInvalid):
                raise FactoryError(OFFLINE_SIGNER_ERROR) from None
            with cli_attestation_key_context(key):
                state, loaded_path = load_or_create_state(manifest, state_path)
            return state, loaded_path, key

        _prepare_private_parent(profile_root, signer_path)
        try:
            key, metadata = _create_signer(profile_root, signer_path)
        except (FileExistsError, _SignerInvalid):
            raise FactoryError(OFFLINE_SIGNER_ERROR) from None
        try:
            with cli_attestation_key_context(key):
                state, loaded_path = load_or_create_state(manifest, state_path)
            return state, loaded_path, key
        except Exception:
            _remove_created_signer(signer_path, metadata)
            raise


def _offline_actions(value: Any) -> Any:
    """Recursively expose graph/state guidance without model-bound dispatch."""

    if isinstance(value, dict):
        return {
            key: _offline_actions(item)
            for key, item in value.items()
            if key not in {"dispatch", "provider", "model"}
        }
    if isinstance(value, list):
        return [_offline_actions(item) for item in value]
    return value


def _offline_result(value: dict[str, Any]) -> dict[str, Any]:
    result = _offline_actions(value)
    result["execution_authorized"] = False
    result["execution_note"] = OFFLINE_EXECUTION_NOTE
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Milestone-controlled Hermes software factory")
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--state", default=DEFAULT_STATE)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate", help="validate manifest and show next safe actions")
    sub.add_parser("next", help="show next safe actions")

    transition = sub.add_parser("transition", help="apply an atomic state transition")
    transition.add_argument("entity_id")
    transition.add_argument("action")
    transition.add_argument("--evidence", help="JSON object or path to JSON object")

    lint = sub.add_parser("lint-card", help="lint a durable Kanban work order")
    lint.add_argument("title")
    lint.add_argument("body", help="literal body or path to a body file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "transition":
            raise FactoryError(OFFLINE_TRANSITION_ERROR)

        if args.command in {"validate", "next"}:
            manifest = load_manifest(args.manifest)
            check = validate_manifest(manifest)
            result = {"manifest": str(Path(args.manifest).resolve()), **check}
            if check["valid"]:
                requested_state = _canonical_state_path(args.state)
                state, state_path, key = _offline_state(
                    manifest,
                    requested_state,
                    initialize=args.command == "validate",
                )
                result["state"] = str(state_path)
                with cli_attestation_key_context(key):
                    result["next"] = next_actions(manifest, state)
            _print(_offline_result(result))
            return 0 if check["valid"] else 1

        if args.command == "lint-card":
            body_path = Path(args.body)
            body = body_path.read_text(encoding="utf-8") if body_path.exists() else args.body
            result = lint_card(args.title, body)
            _print(result)
            return 0 if result["valid"] else 1

        raise FactoryError(f"unknown command: {args.command}")
    except (FactoryError, json.JSONDecodeError) as exc:
        error = {"success": False, "error": str(exc)}
        _print(error if args.command == "lint-card" else _offline_result(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
