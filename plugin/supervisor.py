"""Durable Beads-backed Dark Factory supervisor.

The supervisor is the missing control-plane half of the factory.  Hermes
sessions are disposable execution attempts; the compiled manifest, signed
factory state, and project-local Beads graph are durable.  A worker timeout,
context exhaustion, or process crash therefore causes a fresh session to be
launched for the same functional block rather than ending the mission.

This module deliberately does not mutate factory acceptance state directly.
Only an authenticated Hermes role may call ``factory_transition``.  The
supervisor claims/requeues/closes the corresponding Beads node and stores
operational run metadata beside the signed factory ledger.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from .beads_adapter import (
        BeadsAdapterError,
        _canonical_digest,
        _resolve_bd_executable,
        _run_bd,
        preflight_beads,
        build_graph_plan,
    )
    from .engine import (
        FactoryError,
        _read_json,
        _state_file_lock,
        _validate_state_compatibility,
        load_manifest,
        next_actions,
    )
except ImportError:  # pragma: no cover - direct script execution
    from beads_adapter import (  # type: ignore
        BeadsAdapterError,
        _canonical_digest,
        _resolve_bd_executable,
        _run_bd,
        preflight_beads,
        build_graph_plan,
    )
    from engine import (  # type: ignore
        FactoryError,
        _read_json,
        _state_file_lock,
        _validate_state_compatibility,
        load_manifest,
        next_actions,
    )


SUPERVISOR_SCHEMA_VERSION = 1
DEFAULT_POLL_SECONDS = 20
DEFAULT_WORKER_TIMEOUT_SECONDS = 20 * 60
MAX_LOG_TAIL = 128 * 1024
MAX_HISTORY = 500


class SupervisorError(RuntimeError):
    """A fail-closed supervisor control-plane error."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact_text(value: Any) -> str:
    """Remove common credential-shaped values before operational persistence."""

    text = str(value or "")
    text = re.sub(
        r"-----BEGIN(?: [A-Z]+)* PRIVATE KEY-----.*?-----END(?: [A-Z]+)* PRIVATE KEY-----",
        "[REDACTED]",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(r"(?i)\bAuthorization\s*:\s*Basic\s+\S+", "Authorization: Basic [REDACTED]", text)
    text = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [REDACTED]", text)
    text = re.sub(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/:]+:[^\s/@]+@", "[REDACTED]@", text)
    text = re.sub(r"(?i)(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{12,}", "[REDACTED]", text)
    text = re.sub(r"(?i)\bgh[pousr]_[A-Za-z0-9]{20,}\b", "[REDACTED]", text)
    text = re.sub(r"\bAKIA[0-9A-Z]{16}\b", "[REDACTED]", text)
    text = re.sub(r"\bAIza[0-9A-Za-z_-]{20,}\b", "[REDACTED]", text)
    text = re.sub(r"\bxox[baprs]-[A-Za-z0-9-]{16,}\b", "[REDACTED]", text)
    text = re.sub(
        r"(?i)\b(api[_ -]?key|token|oauth[_ -]?token|access[_ -]?token|password|secret|credential|connection[_ -]?string)\s*[:=]\s*\S+",
        lambda match: f"{match.group(1)}=[REDACTED]",
        text,
    )
    return text


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(_redact_value(value), handle, indent=2, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise SupervisorError(f"{label} is unavailable")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SupervisorError(f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise SupervisorError(f"{label} must be an object")
    return value


def _slug(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-") or "mission"


def _pid_start_identity(pid: int) -> str:
    """Return Linux process start ticks when available, empty elsewhere."""

    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()
        return fields[21] if len(fields) > 21 else ""
    except (OSError, ValueError, IndexError):
        return ""


def _process_is_alive(pid: Any, identity: str = "") -> bool:
    try:
        numeric = int(pid)
    except (TypeError, ValueError):
        return False
    if numeric <= 0:
        return False
    try:
        os.kill(numeric, 0)
    except (OSError, ValueError):
        return False
    if identity:
        current = _pid_start_identity(numeric)
        if current and current != identity:
            return False
    return True


@contextmanager
def _supervisor_lock(path: Path):
    """Acquire a non-blocking per-mission lock so duplicate daemons fail closed."""

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:  # pragma: no cover - Windows fallback
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except (BlockingIOError, OSError) as exc:
            raise SupervisorError("a Dark Factory supervisor is already active for this mission") from exc
        yield
    finally:
        try:
            try:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except ImportError:  # pragma: no cover - Windows fallback
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            handle.close()


class _OutputCollector:
    """Drain a child Hermes pipe without retaining unsanitised output."""

    def __init__(self, process: subprocess.Popen[str], log_path: Path):
        self.process = process
        self.log_path = log_path
        self.tail = ""
        self.thread = threading.Thread(target=self._drain, name="dark-factory-output", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def join(self, timeout: float = 3.0) -> None:
        self.thread.join(timeout)

    def _drain(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.log_path.open("a", encoding="utf-8") as handle:
                stream = self.process.stdout
                if stream is None:
                    return
                for line in iter(stream.readline, ""):
                    clean = _redact_text(line)
                    handle.write(clean)
                    handle.flush()
                    self.tail = (self.tail + clean)[-MAX_LOG_TAIL:]
        except OSError as exc:
            self.tail = (self.tail + f"supervisor log error: {_redact_text(exc)}")[-MAX_LOG_TAIL:]


class DarkFactorySupervisor:
    """One durable mission supervisor.

    ``tick`` is intentionally public for deterministic tests and external
    service managers.  ``run`` has no mission-wide time or iteration limit;
    only factory state, explicit human gates, or typed circuit breakers stop
    it.
    """

    def __init__(
        self,
        manifest_path: str | Path,
        state_path: str | Path,
        *,
        profile: str,
        bd_executable: str = "bd",
        hermes_executable: str = "hermes",
        poll_seconds: int = DEFAULT_POLL_SECONDS,
        worker_timeout_seconds: int = DEFAULT_WORKER_TIMEOUT_SECONDS,
        allow_unattended: bool = False,
    ):
        if not allow_unattended:
            raise SupervisorError("starting the unattended factory requires explicit allow_unattended authorization")
        self.manifest_path = Path(manifest_path).expanduser().resolve()
        self.state_path = Path(state_path).expanduser().resolve()
        self.profile = str(profile or "").strip()
        if not self.profile:
            raise SupervisorError("a Hermes profile is required for durable factory execution")
        if poll_seconds < 1:
            raise SupervisorError("poll_seconds must be at least 1")
        if worker_timeout_seconds < 1:
            raise SupervisorError("worker_timeout_seconds must be at least 1")
        self.poll_seconds = int(poll_seconds)
        self.worker_timeout_seconds = int(worker_timeout_seconds)
        self.bd_executable = _resolve_bd_executable(bd_executable)
        if not self.bd_executable:
            raise SupervisorError("Beads executable is unavailable")
        self.hermes_executable = shutil.which(hermes_executable) or hermes_executable
        self.manifest = load_manifest(self.manifest_path)
        check = self._manifest_check()
        if not check["valid"]:
            raise SupervisorError("invalid runtime manifest: " + "; ".join(check["errors"]))
        self.workspace = Path(str(self.manifest["mission"]["workspace_path"])).expanduser().resolve()
        self.execution = self.manifest.get("execution", {})
        if self.execution.get("graph_mode") != "apply":
            raise SupervisorError("factory execution requires an applied Beads graph")
        self.beads_dir = Path(
            str(self.execution.get("beads_directory") or (self.workspace / ".beads"))
        ).expanduser().resolve()
        self.factory_dir = self.state_path.parent
        self.supervisor_path = self.factory_dir / "supervisor.json"
        self.supervisor_log = self.factory_dir / "supervisor.log"
        self.run_dir = self.factory_dir / "runs"
        self.lock_path = self.factory_dir / ".supervisor.lock"
        self.actor = f"dark-factory-supervisor-{_slug(self.manifest['mission']['id'])}"
        self.processes: dict[str, subprocess.Popen[str]] = {}
        self.collectors: dict[str, _OutputCollector] = {}
        self._stop_requested = False
        self.meta = self._load_or_create_meta()
        self.entity_to_bead = self._load_and_verify_graph()
        preflight_beads(self.beads_dir, bd_executable=self.bd_executable)

    def _load_or_create_meta(self) -> dict[str, Any]:
        if self.supervisor_path.is_file():
            meta = _load_json(self.supervisor_path, "supervisor state")
            if meta.get("schema_version") != SUPERVISOR_SCHEMA_VERSION:
                raise SupervisorError("supervisor state schema_version is unsupported")
            if meta.get("mission_id") != self.manifest["mission"]["id"]:
                raise SupervisorError("supervisor state mission does not match manifest")
            existing_pid = meta.get("pid")
            if existing_pid and int(existing_pid) != os.getpid() and _process_is_alive(
                existing_pid, str(meta.get("pid_start_identity") or "")
            ):
                raise SupervisorError("a Dark Factory supervisor is already active for this mission")
            meta["pid"] = os.getpid()
            meta["pid_start_identity"] = _pid_start_identity(os.getpid())
            meta["status"] = "running"
            meta["updated_at"] = _utc_now()
            return meta
        return {
            "schema_version": SUPERVISOR_SCHEMA_VERSION,
            "mission_id": str(self.manifest["mission"]["id"]),
            "manifest_path": str(self.manifest_path),
            "state_path": str(self.state_path),
            "workspace_path": str(self.workspace),
            "beads_directory": str(self.beads_dir),
            "profile": self.profile,
            "pid": os.getpid(),
            "pid_start_identity": _pid_start_identity(os.getpid()),
            "started_at": _utc_now(),
            "updated_at": _utc_now(),
            "status": "running",
            "stop_reason": "",
            "runs": {},
            "work": {},
            "failure_fingerprints": {},
            "history": [],
        }

    def _manifest_check(self) -> dict[str, Any]:  # type: ignore[no-redef]
        try:
            from .engine import validate_manifest
        except ImportError:  # pragma: no cover - direct script execution
            from engine import validate_manifest  # type: ignore
        return validate_manifest(self.manifest)

    def _load_and_verify_graph(self) -> dict[str, str]:
        receipt_path = self.factory_dir / "beads-graph-receipt.json"
        receipt = _load_json(receipt_path, "Beads graph receipt")
        plan = build_graph_plan(self.manifest)
        if receipt.get("manifest_digest") != _canonical_digest(self.manifest):
            raise SupervisorError("Beads graph receipt does not match the compiled manifest")
        if receipt.get("plan_digest") != _canonical_digest(plan):
            raise SupervisorError("Beads graph receipt does not match the deterministic graph plan")
        ids = receipt.get("ids")
        if not isinstance(ids, dict):
            raise SupervisorError("Beads graph receipt has no node mapping")
        result: dict[str, str] = {}
        for node in plan.get("nodes", []):
            if not isinstance(node, dict):
                continue
            metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
            entity_id = str(metadata.get("dark_factory_entity_id") or "")
            key = str(node.get("key") or "")
            bead_id = ids.get(key)
            if entity_id and isinstance(bead_id, str) and bead_id.strip():
                result[entity_id] = bead_id.strip()
        expected = {
            str(self.manifest["mission"]["id"]),
            *[str(item["id"]) for item in self.manifest.get("milestones", []) if isinstance(item, dict)],
            *[str(item["id"]) for item in self.manifest.get("slices", []) if isinstance(item, dict)],
        }
        if set(result) != expected:
            raise SupervisorError("Beads graph receipt does not map every factory entity")
        return result

    def _persist(self) -> None:
        self.meta["updated_at"] = _utc_now()
        _write_json_atomic(self.supervisor_path, self.meta)

    def _factory_snapshot(self) -> tuple[dict[str, Any], dict[str, Any]]:
        with _state_file_lock(self.state_path):
            state = _read_json(self.state_path)
            _validate_state_compatibility(self.manifest, state)
            following = next_actions(self.manifest, state)
        return state, following

    def _run_record(self, run_id: str) -> dict[str, Any]:
        runs = self.meta.get("runs")
        if not isinstance(runs, dict) or not isinstance(runs.get(run_id), dict):
            raise SupervisorError("supervisor run record is missing")
        return runs[run_id]

    def _entity_state(self, state: dict[str, Any], entity_id: str, entity_type: str) -> dict[str, Any]:
        key = "milestones" if entity_type == "milestone" else "slices"
        value = state.get(key, {}).get(entity_id)
        if not isinstance(value, dict):
            raise SupervisorError(f"factory state has no {entity_type} {entity_id}")
        return value

    def _bd_ready_ids(self) -> set[str]:
        output = _run_bd(
            self.bd_executable,
            ["ready", "--json", "--limit", "0", "--label", "dark-factory"],
            self.beads_dir,
        )
        rows = output if isinstance(output, list) else []
        return {
            str(row.get("id"))
            for row in rows
            if isinstance(row, dict) and str(row.get("id") or "").strip()
        }

    def _bd_claim(self, bead_id: str, run_id: str) -> None:
        _run_bd(
            self.bd_executable,
            [
                "--actor", self.actor,
                "update", bead_id,
                "--claim",
                "--set-metadata", f"dark_factory_run={run_id}",
                "--json",
            ],
            self.beads_dir,
        )

    def _bd_release(self, bead_id: str) -> None:
        try:
            _run_bd(
                self.bd_executable,
                [
                    "--actor", self.actor,
                    "update", bead_id,
                    "--status", "open",
                    "--assignee", "",
                    "--unset-metadata", "dark_factory_run",
                    "--json",
                ],
                self.beads_dir,
            )
        except BeadsAdapterError as exc:
            self._stop("Beads requeue failed: " + _redact_text(exc))

    def _bd_close(self, entity_id: str, bead_id: str, reason: str) -> None:
        _run_bd(
            self.bd_executable,
            [
                "--actor", self.actor,
                "update", bead_id,
                "--set-metadata", "dark_factory_status=completed",
                "--json",
            ],
            self.beads_dir,
        )
        _run_bd(
            self.bd_executable,
            [
                "--actor", self.actor,
                "close", bead_id,
                "--reason", _redact_text(reason),
                "--json",
            ],
            self.beads_dir,
        )
        self._record_history({"event": "bead_closed", "entity_id": entity_id, "bead_id": bead_id})

    def _record_history(self, row: dict[str, Any]) -> None:
        history = self.meta.setdefault("history", [])
        if not isinstance(history, list):
            history = []
            self.meta["history"] = history
        history.append({"at": _utc_now(), **_redact_value(row)})
        self.meta["history"] = history[-MAX_HISTORY:]

    def _record_failure(self, run: dict[str, Any], fingerprint: str, reason: str) -> None:
        fingerprint = fingerprint or "unknown-launch-failure"
        failures = self.meta.setdefault("failure_fingerprints", {})
        if not isinstance(failures, dict):
            failures = {}
            self.meta["failure_fingerprints"] = failures
        count = int(failures.get(fingerprint, 0)) + 1
        failures[fingerprint] = count
        run["failure_fingerprint"] = fingerprint
        run["failure_count"] = count
        run["failure_reason"] = _redact_text(reason)
        self._record_history({
            "event": "run_failure",
            "run_id": run.get("run_id"),
            "entity_id": run.get("entity_id"),
            "phase": run.get("phase"),
            "fingerprint": fingerprint,
            "count": count,
            "reason": reason,
        })
        if count >= int(self.manifest.get("policy", {}).get("repeated_failure_limit", 2)):
            self._stop(
                f"repeated identical supervisor failure for {run.get('entity_id')}; human intervention required"
            )

    def _stop(self, reason: str) -> None:
        if self.meta.get("status") == "completed":
            return
        self.meta["status"] = "blocked"
        self.meta["stop_reason"] = _redact_text(reason)
        self._record_history({"event": "supervisor_stopped", "reason": reason})

    def request_stop(self, reason: str = "operator requested stop") -> None:
        self._stop_requested = True
        self.meta["status"] = "stopped"
        self.meta["stop_reason"] = _redact_text(reason)
        self._persist()

    def _prompt_for_block(self, descriptor: dict[str, Any], state: dict[str, Any]) -> str:
        entity_id = str(descriptor["entity_id"])
        spec = next(
            (item for item in self.manifest.get("slices", []) if isinstance(item, dict) and str(item.get("id")) == entity_id),
            {},
        )
        action = str(descriptor["action"])
        review_required = bool(spec.get("review_required"))
        transition = (
            f"Call factory_transition for {entity_id} with action {action} and evidence {{\"reason\": \"{action} after a disposable session boundary\"}} before editing."
            if action in {"resume_slice", "continue_slice"}
            else f"If the current state is pending, call factory_transition for {entity_id} with action start_slice before editing; if it is already active, call resume_slice instead."
        )
        finish = (
            "When all block criteria are proven, call factory_transition complete_slice with the real candidate commit SHA, structured check receipts, and acceptance_passed. Do not call request_review; this block is reviewed at milestone delivery."
            if not review_required
            else "When the candidate is coherent, call request_review with the real candidate commit SHA and structured check receipts; do not create a Beads remediation card."
        )
        return f"""You are the Dark Factory builder for functional block {entity_id} in mission {self.manifest['mission']['id']}.

Read the compiled manifest at {self.manifest_path} and the signed state through factory_next before acting. Work in {self.workspace}. This is a complete product-area build, not a thin vertical slice: implement the stated outcome, all required adjacent interfaces/functions and wiring, persistence/runtime behavior, failure/restart behavior, and the declared focused evidence. Do not stop at scaffolding or at one passing unit test.

Block outcome: {spec.get('outcome', '')}
Owned stories: {', '.join(str(value) for value in (spec.get('story_ids') or [spec.get('story_id')]))}
Allowed paths/interfaces: {', '.join(str(value) for value in spec.get('paths', []))}
Acceptance criteria: {json.dumps(spec.get('acceptance', []), ensure_ascii=False)}
Evidence commands/scenarios: {json.dumps(spec.get('evidence', []), ensure_ascii=False)}

{transition}
Make coherent edits inside the allowed boundary. Run focused checks, create the exact raw artifacts required by the compiled contract, and map every criterion ID to positive evidence. Fix ordinary implementation/test failures inside this same block and session. Never modify manifest.json or state.json directly, create micro-beads, publish/deploy, spend money, or contact external systems.

{finish}
If a genuine product ambiguity, security decision, unavailable capability, or repeated identical failure blocks progress, use the typed factory transition block/replan action with an owner and resume condition. End with a concise status and candidate SHA; do not claim success without the factory transition result."""

    def _prompt_for_milestone_start(self, descriptor: dict[str, Any]) -> str:
        entity_id = str(descriptor["entity_id"])
        spec = next(
            (item for item in self.manifest.get("milestones", []) if isinstance(item, dict) and str(item.get("id")) == entity_id),
            {},
        )
        return f"""You are the Dark Factory integrator for mission {self.manifest['mission']['id']} and milestone {entity_id}.

Read {self.manifest_path} and use factory_next. Work in {self.workspace}. If this milestone is pending, call factory_transition with start_milestone before doing work. Establish shared contracts and integration conventions needed by its complete functional blocks, but do not implement a thin scaffold and stop. Do not create micro-beads or modify manifest/state files directly.

Milestone outcome: {spec.get('outcome', '')}
Owned functional blocks: {', '.join(str(value) for value in spec.get('slices', []))}
Shared acceptance: {json.dumps(spec.get('acceptance', []), ensure_ascii=False)}

After the milestone is active, leave block implementation to the builder sessions and finish with a concise handoff. If a real shared contract or product decision is missing, use the typed factory block/replan transition instead of inventing it."""

    def _prompt_for_milestone_gate(self, entity_id: str) -> str:
        spec = next(
            (item for item in self.manifest.get("milestones", []) if isinstance(item, dict) and str(item.get("id")) == entity_id),
            {},
        )
        work = self.meta.setdefault("work", {}).setdefault(entity_id, {})
        gate_path = self.run_dir / "milestones" / f"{_slug(entity_id)}-gate.json"
        return f"""You are the Dark Factory integrator at the delivery gate for milestone {entity_id} in mission {self.manifest['mission']['id']}.

Read the compiled manifest {self.manifest_path}, signed state via factory_next, and workspace {self.workspace}. All functional blocks for this milestone are complete. If state is active, rebind your disposable session with factory_transition resume_milestone and then call validate_milestone. If state is already validating, continue the gate.

Run every declared integration command and the held-out/real interaction scenarios against the exact integrated commit. Do not weaken or rewrite acceptance. Create raw positive artifacts and exact scenario receipts for every milestone criterion. Do not call complete_milestone yet: independent verifier, adversary, and holdout sessions must review this frozen candidate first.

Milestone outcome: {spec.get('outcome', '')}
Acceptance: {json.dumps(spec.get('acceptance', []), ensure_ascii=False)}
Integration commands: {json.dumps(self.manifest.get('testing', {}).get('integration_commands', []), ensure_ascii=False)}
Held-out scenarios: {json.dumps(self.manifest.get('testing', {}).get('held_out_scenarios', []), ensure_ascii=False)}

When the gate candidate is ready, print one final line beginning exactly with DARK_FACTORY_GATE= followed by a JSON object containing integration_sha, acceptance_passed, and scenario_receipts. Save the same object as {gate_path} if convenient. The supervisor will launch independent review sessions from that frozen evidence. Never claim the milestone accepted until factory_transition complete_milestone succeeds."""

    def _prompt_for_milestone_complete(self, entity_id: str, evidence_path: Path) -> str:
        return f"""You are the Dark Factory integrator completing milestone {entity_id} in mission {self.manifest['mission']['id']}.

Read {self.manifest_path}, signed state via factory_next, and the frozen completion evidence at {evidence_path}. Do not change product code or the candidate SHA. If state is active, call resume_milestone; if it is active after that, call validate_milestone. Then call factory_transition complete_milestone with the evidence object from the completion file, including acceptance_passed, scenario_receipts, integration_sha, holdout_review, and independent_reviews. The verifier, adversary, and holdout receipts are independently attested and must remain bound to this exact candidate.

Do not create cards, alter manifest/state files directly, publish, deploy, spend, or contact external systems. Finish only after the factory transition returns success and report its revision and milestone status."""

    def _prompt_for_review(
        self,
        entity_id: str,
        entity_type: str,
        role: str,
        candidate_sha: str,
        gate_path: Path | None,
    ) -> str:
        subject = "milestone delivery" if entity_type == "milestone" else "functional-block candidate"
        source = f" Read the frozen gate evidence at {gate_path}." if gate_path else " Read the compiled state and candidate evidence."
        return f"""You are the independent Dark Factory {role} reviewer for {subject} {entity_id} in mission {self.manifest['mission']['id']}.

You are evidence-only. Read {self.manifest_path}, inspect the workspace files and declared evidence using your read-only tools, and judge the exact candidate SHA {candidate_sha}.{source} Check every acceptance criterion, integration boundary, negative/recovery behavior, and the Kryptonite adversarial concerns relevant to your role. Do not edit files, run shell commands, create cards, or redefine the acceptance contract.

If and only if the candidate passes, call the deferred factory_attest_review tool with entity_id {entity_id}, candidate_sha {candidate_sha}, and your reviewer identity. The configured role/model and Hermes session bind the attestation. Then print one final line beginning exactly with DARK_FACTORY_REVIEW= followed by the receipt JSON returned by that tool. If it does not pass, do not issue a PASS receipt; explain the concrete finding in your final response so the integrator can request changes."""

    def _prompt_for_block_review_complete(self, entity_id: str, evidence_path: Path) -> str:
        return f"""You are the Dark Factory integrator for functional block {entity_id}. Read {self.manifest_path}, signed state via factory_next, and review evidence at {evidence_path}. Call factory_transition pass_review with the exact candidate_sha and the independently attested reviews listed in the file. Do not modify code or create cards. If the reviews do not pass, record request_changes through the factory transition with concrete findings. Report the transition result."""

    def _start_process(
        self,
        *,
        run_id: str,
        entity_id: str,
        entity_type: str,
        phase: str,
        action: str,
        configured_role: str,
        provider: str,
        model: str,
        prompt: str,
        bead_id: str = "",
        candidate_sha: str = "",
        review_role: str = "",
        gate_path: str = "",
    ) -> None:
        log_path = self.run_dir / f"{run_id}.log"
        record = {
            "run_id": run_id,
            "entity_id": entity_id,
            "entity_type": entity_type,
            "phase": phase,
            "action": action,
            "configured_role": configured_role,
            "provider": provider,
            "model": model,
            "bead_id": bead_id,
            "candidate_sha": candidate_sha,
            "review_role": review_role,
            "gate_path": gate_path,
            "log_path": str(log_path),
            "started_at": _utc_now(),
            "started_epoch": time.time(),
            "started_monotonic": time.monotonic(),
            "before_revision": self._factory_snapshot()[0]["revision"],
            "status": "launching",
            "pid": None,
            "pid_start_identity": "",
        }
        self.meta.setdefault("runs", {})[run_id] = record
        self._persist()
        env = os.environ.copy()
        env.update({
            "HERMES_FACTORY_MANIFEST": str(self.manifest_path),
            "HERMES_FACTORY_STATE": str(self.state_path),
            "HERMES_FACTORY_ROLE": configured_role,
            "HERMES_FACTORY_PROVIDER": provider,
            "HERMES_FACTORY_MODEL": model,
            "HERMES_FACTORY_RUN_ID": run_id,
            "HERMES_FACTORY_SUPERVISOR": str(self.supervisor_path),
        })
        current_python_path = env.get("PYTHONPATH", "")
        plugin_root = str(self.manifest_path.parent.parent.parent)
        env["PYTHONPATH"] = os.pathsep.join(item for item in (plugin_root, current_python_path) if item)
        command = [
            self.hermes_executable,
            "--profile", self.profile,
            "--pass-session-id",
            "chat", "-Q", "-q", prompt,
            "--provider", provider,
            "--model", model,
            "--source", "dark-factory",
            "--yolo",
        ]
        try:
            process = subprocess.Popen(
                command,
                cwd=str(self.workspace),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            record["status"] = "launch_failed"
            self._record_failure(record, "launch:" + hashlib.sha256(_redact_text(exc).encode()).hexdigest()[:16], str(exc))
            if bead_id:
                self._bd_release(bead_id)
            self._persist()
            return
        record["pid"] = process.pid
        record["pid_start_identity"] = _pid_start_identity(process.pid)
        record["status"] = "active"
        self.processes[run_id] = process
        collector = _OutputCollector(process, log_path)
        self.collectors[run_id] = collector
        collector.start()
        self._persist()

    def _claim_and_start(self, descriptor: dict[str, Any], state: dict[str, Any], *, phase: str | None = None) -> None:
        entity_id = str(descriptor["entity_id"])
        entity_type = str(descriptor["entity_type"])
        bead_id = self.entity_to_bead.get(entity_id, "")
        if not bead_id:
            self._stop(f"no Beads node mapped for {entity_id}")
            return
        action = str(descriptor["action"])
        if action == "start_milestone" or action == "start_slice":
            if bead_id not in self._bd_ready_ids():
                return
        try:
            run_id = uuid.uuid4().hex
            self._bd_claim(bead_id, run_id)
            if entity_type == "milestone":
                prompt = (
                    self._prompt_for_milestone_start(descriptor)
                    if action == "start_milestone"
                    else self._prompt_for_milestone_gate(entity_id)
                )
                run_phase = phase or ("milestone_start" if action == "start_milestone" else "milestone_gate")
                if run_phase == "milestone_complete":
                    evidence_path = Path(str(self.meta["work"][entity_id]["completion_path"]))
                    prompt = self._prompt_for_milestone_complete(entity_id, evidence_path)
                configured_role = "integrator"
                execution_role = "orchestrator"
            else:
                prompt = self._prompt_for_block(descriptor, state)
                run_phase = phase or "block"
                configured_role = "builder"
                execution_role = "worker"
            self._start_process(
                run_id=run_id,
                entity_id=entity_id,
                entity_type=entity_type,
                phase=run_phase,
                action=action,
                configured_role=configured_role,
                provider=str(descriptor.get("provider") or self.manifest["models"][configured_role]["provider"]),
                model=str(descriptor.get("model") or self.manifest["models"][configured_role]["model"]),
                prompt=prompt,
                bead_id=bead_id,
            )
        except (BeadsAdapterError, SupervisorError) as exc:
            self._stop("Beads claim/launch coordination failed: " + _redact_text(exc))

    def _active_for(self, entity_id: str, *, phase: str | None = None, review_role: str | None = None) -> bool:
        for record in self.meta.get("runs", {}).values() if isinstance(self.meta.get("runs"), dict) else []:
            if not isinstance(record, dict) or record.get("status") not in {"launching", "active"}:
                continue
            if str(record.get("entity_id")) != entity_id:
                continue
            if phase and record.get("phase") != phase:
                continue
            if review_role and record.get("review_role") != review_role:
                continue
            return True
        return False

    def _terminate_process(self, run_id: str) -> None:
        process = self.processes.get(run_id)
        record = self._run_record(run_id)
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass
        else:
            pid = record.get("pid")
            if _process_is_alive(pid, str(record.get("pid_start_identity") or "")):
                try:
                    os.kill(int(pid), signal.SIGTERM)
                except (OSError, ValueError):
                    pass

    def _marker(self, text: str, marker: str) -> Any:
        for line in reversed(str(text or "").splitlines()):
            if line.startswith(marker):
                raw = line[len(marker):].strip()
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return None
        return None

    def _review_receipt(self, output: Any) -> dict[str, Any] | None:
        value = output.get("receipt") if isinstance(output, dict) else output
        if isinstance(value, dict) and value.get("attestation"):
            return value
        return None

    def _finish_review_run(self, run: dict[str, Any], output: str, exit_code: int | None) -> None:
        marker = self._marker(output, "DARK_FACTORY_REVIEW=")
        receipt = self._review_receipt(marker)
        role = str(run.get("review_role") or "")
        entity_id = str(run.get("entity_id") or "")
        if exit_code != 0 or receipt is None:
            fingerprint = "review:" + hashlib.sha256(_redact_text(output).encode()).hexdigest()[:16]
            self._record_failure(run, fingerprint, f"{role} reviewer did not produce an attested PASS receipt")
            run["status"] = "failed"
            return
        review_path = self.run_dir / "reviews" / _slug(entity_id) / f"{_slug(role)}.json"
        _write_json_atomic(review_path, receipt)
        work = self.meta.setdefault("work", {}).setdefault(entity_id, {})
        review_paths = work.setdefault("review_paths", {})
        review_paths[role] = str(review_path)
        run["status"] = "completed"
        run["receipt_path"] = str(review_path)
        self._record_history({"event": "review_receipt", "entity_id": entity_id, "role": role, "path": str(review_path)})

    def _finish_gate_run(self, run: dict[str, Any], output: str, exit_code: int | None) -> None:
        entity_id = str(run.get("entity_id") or "")
        marker = self._marker(output, "DARK_FACTORY_GATE=")
        if exit_code != 0 or not isinstance(marker, dict):
            fingerprint = "gate:" + hashlib.sha256(_redact_text(output).encode()).hexdigest()[:16]
            self._record_failure(run, fingerprint, "milestone gate did not produce frozen evidence")
            run["status"] = "failed"
            return
        required = {"integration_sha", "acceptance_passed", "scenario_receipts"}
        if not required.issubset(marker):
            self._record_failure(run, "gate:missing-fields", "milestone gate evidence is incomplete")
            run["status"] = "failed"
            return
        gate_path = self.run_dir / "milestones" / f"{_slug(entity_id)}-gate.json"
        _write_json_atomic(gate_path, marker)
        work = self.meta.setdefault("work", {}).setdefault(entity_id, {})
        work["gate_path"] = str(gate_path)
        work["candidate_sha"] = str(marker.get("integration_sha"))
        run["status"] = "completed"
        run["receipt_path"] = str(gate_path)
        self._record_history({"event": "milestone_gate", "entity_id": entity_id, "path": str(gate_path)})

    def _finish_run(self, run_id: str, *, timed_out: bool = False) -> None:
        run = self._run_record(run_id)
        collector = self.collectors.get(run_id)
        if collector is not None:
            collector.join()
        process = self.processes.get(run_id)
        exit_code = process.poll() if process is not None else None
        output = collector.tail if collector is not None else ""
        if timed_out:
            self._terminate_process(run_id)
            exit_code = -signal.SIGTERM
        phase = str(run.get("phase") or "")
        if phase == "review":
            self._finish_review_run(run, output, exit_code)
        elif phase == "milestone_gate":
            self._finish_gate_run(run, output, exit_code)
        else:
            try:
                state, _following = self._factory_snapshot()
                current = self._entity_state(state, str(run["entity_id"]), str(run["entity_type"]))
                advanced = int(state["revision"]) > int(run.get("before_revision", -1))
                terminal = current.get("status") in {"completed", "blocked", "replan_required"}
                if terminal and current.get("status") == "completed":
                    bead_id = str(run.get("bead_id") or "")
                    if bead_id:
                        self._bd_close(str(run["entity_id"]), bead_id, f"factory state completed {run['entity_id']}")
                    run["status"] = "completed"
                elif advanced:
                    # An active block/milestone remains claimed. The next tick
                    # selects a fresh resume or continuation descriptor.
                    run["status"] = "completed" if not timed_out else "timed_out_after_transition"
                else:
                    fingerprint = "process:" + hashlib.sha256(
                        _redact_text(output or f"exit={exit_code}").encode()
                    ).hexdigest()[:16]
                    self._record_failure(run, fingerprint, "worker session exited without a factory-state transition")
                    run["status"] = "timed_out" if timed_out else "failed"
                    bead_id = str(run.get("bead_id") or "")
                    if bead_id:
                        self._bd_release(bead_id)
            except (FactoryError, BeadsAdapterError, SupervisorError) as exc:
                self._stop("run reconciliation failed: " + _redact_text(exc))
                run["status"] = "failed"
        run["finished_at"] = _utc_now()
        run["exit_code"] = exit_code
        if collector is not None:
            run["output_tail"] = output[-MAX_LOG_TAIL:]
        self.processes.pop(run_id, None)
        self.collectors.pop(run_id, None)
        self._persist()

    def _reconcile_runs(self) -> None:
        runs = self.meta.get("runs") if isinstance(self.meta.get("runs"), dict) else {}
        for run_id, run in list(runs.items()):
            if not isinstance(run, dict) or run.get("status") not in {"launching", "active"}:
                continue
            process = self.processes.get(run_id)
            if process is not None:
                alive = process.poll() is None
            else:
                alive = _process_is_alive(run.get("pid"), str(run.get("pid_start_identity") or ""))
            started_epoch = str(run.get("started_epoch") or "")
            try:
                age = time.time() - float(started_epoch)
            except (TypeError, ValueError):
                age = time.monotonic() - float(run.get("started_monotonic", time.monotonic()))
            if alive and age > self.worker_timeout_seconds:
                self._finish_run(run_id, timed_out=True)
            elif not alive:
                self._finish_run(run_id)

    def _write_block_review_evidence(self, entity_id: str, candidate_sha: str, paths: dict[str, str]) -> Path:
        evidence_path = self.run_dir / "reviews" / _slug(entity_id) / "completion.json"
        reviews = []
        for role in sorted(paths):
            reviews.append(_load_json(Path(paths[role]), f"{role} review receipt"))
        _write_json_atomic(evidence_path, {"candidate_sha": candidate_sha, "reviews": reviews})
        return evidence_path

    def _write_milestone_completion_evidence(self, entity_id: str, work: dict[str, Any]) -> Path:
        gate = _load_json(Path(str(work["gate_path"])), "milestone gate evidence")
        independent: list[dict[str, Any]] = []
        holdout: dict[str, Any] | None = None
        for role, raw_path in (work.get("review_paths") or {}).items():
            receipt = _load_json(Path(str(raw_path)), f"{role} review receipt")
            if role == "holdout":
                holdout = receipt
            else:
                independent.append(receipt)
        if holdout is None or len(independent) < 2:
            raise SupervisorError("milestone completion evidence is missing independent or holdout reviews")
        evidence = {
            "acceptance_passed": gate.get("acceptance_passed"),
            "scenario_receipts": gate.get("scenario_receipts"),
            "integration_sha": gate.get("integration_sha"),
            "holdout_review": holdout,
            "independent_reviews": independent,
        }
        evidence_path = self.run_dir / "milestones" / f"{_slug(entity_id)}-completion.json"
        _write_json_atomic(evidence_path, evidence)
        work["completion_path"] = str(evidence_path)
        return evidence_path

    def _schedule_review_subject(
        self,
        entity_id: str,
        entity_type: str,
        state: dict[str, Any],
        *,
        candidate_sha: str,
        gate_path: Path | None = None,
    ) -> None:
        work = self.meta.setdefault("work", {}).setdefault(entity_id, {})
        review_paths = work.setdefault("review_paths", {})
        if entity_type == "milestone":
            roles = ["verifier", "adversary", "holdout"]
        else:
            spec = next(
                (item for item in self.manifest.get("slices", []) if isinstance(item, dict) and str(item.get("id")) == entity_id),
                {},
            )
            roles = [str(role) for role in spec.get("review_roles", []) if str(role)]
        for role in roles:
            if role in review_paths or self._active_for(entity_id, phase="review", review_role=role):
                continue
            if role == "holdout" and entity_type == "milestone" and not {"verifier", "adversary"}.issubset(review_paths):
                continue
            ref = self.manifest.get("models", {}).get(role, {})
            run_id = uuid.uuid4().hex
            prompt = self._prompt_for_review(entity_id, entity_type, role, candidate_sha, gate_path)
            self._start_process(
                run_id=run_id,
                entity_id=entity_id,
                entity_type="review",
                phase="review",
                action="attest_review",
                configured_role=role,
                provider=str(ref.get("provider") or ""),
                model=str(ref.get("model") or ""),
                prompt=prompt,
                candidate_sha=candidate_sha,
                review_role=role,
                gate_path=str(gate_path or ""),
            )
        completed_roles = set(review_paths)
        if roles and set(roles).issubset(completed_roles) and not self._active_for(entity_id, phase="milestone_complete" if entity_type == "milestone" else "block_review_complete"):
            if entity_type == "milestone":
                try:
                    evidence_path = self._write_milestone_completion_evidence(entity_id, work)
                except (OSError, SupervisorError) as exc:
                    self._stop("cannot assemble milestone review evidence: " + _redact_text(exc))
                    return
                work["completion_path"] = str(evidence_path)
                descriptor = {
                    "entity_id": entity_id,
                    "entity_type": "milestone",
                    "action": "resume_milestone",
                    "provider": self.manifest["models"]["integrator"]["provider"],
                    "model": self.manifest["models"]["integrator"]["model"],
                }
                self._claim_and_start(descriptor, state, phase="milestone_complete")
            else:
                evidence_path = self._write_block_review_evidence(entity_id, candidate_sha, review_paths)
                self._start_process(
                    run_id=uuid.uuid4().hex,
                    entity_id=entity_id,
                    entity_type="slice",
                    phase="block_review_complete",
                    action="pass_review",
                    configured_role="integrator",
                    provider=self.manifest["models"]["integrator"]["provider"],
                    model=self.manifest["models"]["integrator"]["model"],
                    prompt=self._prompt_for_block_review_complete(entity_id, evidence_path),
                    bead_id=self.entity_to_bead.get(entity_id, ""),
                    candidate_sha=candidate_sha,
                )
        self._persist()

    def _schedule(self, state: dict[str, Any], following: dict[str, Any]) -> None:
        runs = self.meta.get("runs") if isinstance(self.meta.get("runs"), dict) else {}
        # An explicit factory stop/replan/human gate is not silently bypassed.
        if following.get("replan_required"):
            self._stop("factory state requires replan or human intervention")
            return
        if all(value.get("status") == "completed" for value in state.get("milestones", {}).values()):
            mission_id = str(self.manifest["mission"]["id"])
            bead_id = self.entity_to_bead.get(mission_id, "")
            if bead_id:
                try:
                    self._bd_close(mission_id, bead_id, "all factory milestones completed")
                except BeadsAdapterError as exc:
                    self._stop("mission Beads close failed: " + _redact_text(exc))
                    return
            self.meta["status"] = "completed"
            self.meta["stop_reason"] = "mission accepted"
            return

        # Complete one milestone gate at a time before starting another.
        for mid in following.get("resume_milestones", []):
            if self._active_for(mid):
                continue
            current = state["milestones"][mid]
            work = self.meta.setdefault("work", {}).setdefault(mid, {})
            if not work.get("gate_path"):
                descriptor = {
                    "entity_id": mid,
                    "entity_type": "milestone",
                    "action": "resume_milestone",
                    "provider": self.manifest["models"]["integrator"]["provider"],
                    "model": self.manifest["models"]["integrator"]["model"],
                }
                self._claim_and_start(descriptor, state, phase="milestone_gate")
                return
            candidate_sha = str(work.get("candidate_sha") or "")
            self._schedule_review_subject(
                mid,
                "milestone",
                state,
                candidate_sha=candidate_sha,
                gate_path=Path(str(work["gate_path"])),
            )
            return

        for sid, current in state.get("slices", {}).items():
            if isinstance(current, dict) and current.get("status") == "review":
                self._schedule_review_subject(
                    str(sid),
                    "slice",
                    state,
                    candidate_sha=str(current.get("candidate_sha") or ""),
                )
                return

        # Start the next milestone only through its ready Beads node.
        for descriptor in following.get("dispatch", {}).get("startable_milestones", []):
            if not isinstance(descriptor, dict):
                continue
            if not self._active_for(str(descriptor.get("entity_id"))):
                self._claim_and_start(descriptor, state)
                return

        # Blocks may run in parallel only when the factory state and paths say
        # they are disjoint. The engine is authoritative for that selection.
        descriptors: list[dict[str, Any]] = []
        dispatch = following.get("dispatch", {})
        for key in ("startable_slices", "continuation_slices", "resume_slices"):
            rows = dispatch.get(key, [])
            if isinstance(rows, list):
                descriptors.extend(row for row in rows if isinstance(row, dict))
        limit = int(self.manifest.get("policy", {}).get("max_parallel_slices", 1))
        active_block_runs = sum(
            1
            for row in runs.values()
            if isinstance(row, dict)
            and row.get("status") in {"launching", "active"}
            and row.get("entity_type") == "slice"
        )
        for descriptor in descriptors:
            entity_id = str(descriptor.get("entity_id") or "")
            if not entity_id or self._active_for(entity_id):
                continue
            if active_block_runs >= limit:
                break
            self._claim_and_start(descriptor, state)
            active_block_runs += 1

    def tick(self) -> dict[str, Any]:
        if self.meta.get("status") not in {"running", ""}:
            return {"status": self.meta.get("status"), "reason": self.meta.get("stop_reason", "")}
        try:
            self._reconcile_runs()
            state, following = self._factory_snapshot()
            self._schedule(state, following)
            self._persist()
            return {
                "status": self.meta.get("status"),
                "revision": state.get("revision"),
                "next": following,
                "active_runs": [
                    run_id
                    for run_id, run in self.meta.get("runs", {}).items()
                    if isinstance(run, dict) and run.get("status") in {"launching", "active"}
                ],
            }
        except (FactoryError, BeadsAdapterError, SupervisorError, OSError) as exc:
            self._stop("supervisor tick failed: " + _redact_text(exc))
            self._persist()
            return {"status": self.meta.get("status"), "reason": self.meta.get("stop_reason", "")}

    def run(self, *, once: bool = False) -> dict[str, Any]:
        with _supervisor_lock(self.lock_path):
            self._persist()
            result: dict[str, Any] = {}
            try:
                while not self._stop_requested and self.meta.get("status") == "running":
                    result = self.tick()
                    if once or self.meta.get("status") != "running":
                        break
                    time.sleep(self.poll_seconds)
            except KeyboardInterrupt:
                self.request_stop("operator interrupted supervisor")
            finally:
                for run_id in list(self.processes):
                    # Do not terminate active workers on ordinary supervisor
                    # process exit; a restarted supervisor will reconcile them.
                    del run_id
                self._persist()
            return result


def start_supervisor_process(
    manifest_path: str | Path,
    state_path: str | Path,
    *,
    profile: str,
    bd_executable: str = "bd",
    hermes_executable: str = "hermes",
    poll_seconds: int = DEFAULT_POLL_SECONDS,
    worker_timeout_seconds: int = DEFAULT_WORKER_TIMEOUT_SECONDS,
    allow_unattended: bool = False,
) -> dict[str, Any]:
    """Launch a detached supervisor and verify that it remains alive briefly."""

    if not allow_unattended:
        raise SupervisorError("starting the unattended factory requires explicit allow_unattended authorization")
    state = Path(state_path).expanduser().resolve()
    log_path = state.parent / "supervisor.log"
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "run",
        "--manifest", str(Path(manifest_path).expanduser().resolve()),
        "--state", str(state),
        "--profile", str(profile),
        "--bd", str(bd_executable),
        "--hermes", str(hermes_executable),
        "--poll-seconds", str(int(poll_seconds)),
        "--worker-timeout", str(int(worker_timeout_seconds)),
        "--allow-unattended",
    ]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    plugin_root = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = os.pathsep.join(item for item in (plugin_root, env.get("PYTHONPATH", "")) if item)
    with log_path.open("a", encoding="utf-8") as log:
        try:
            process = subprocess.Popen(
                command,
                cwd=str(Path(manifest_path).expanduser().resolve().parent.parent.parent),
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SupervisorError("supervisor process failed to start: " + _redact_text(exc)) from exc
    time.sleep(0.15)
    if process.poll() is not None:
        raise SupervisorError("supervisor exited during startup; inspect the redacted supervisor log")
    return {
        "started": True,
        "pid": process.pid,
        "manifest_path": str(Path(manifest_path).expanduser().resolve()),
        "state_path": str(state),
        "supervisor_path": str(state.parent / "supervisor.json"),
        "supervisor_log": str(log_path),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the durable Dark Factory supervisor")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--manifest", required=True)
    run.add_argument("--state", required=True)
    run.add_argument("--profile", required=True)
    run.add_argument("--bd", default="bd")
    run.add_argument("--hermes", default="hermes")
    run.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS)
    run.add_argument("--worker-timeout", type=int, default=DEFAULT_WORKER_TIMEOUT_SECONDS)
    run.add_argument("--once", action="store_true")
    run.add_argument("--allow-unattended", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        supervisor = DarkFactorySupervisor(
            args.manifest,
            args.state,
            profile=args.profile,
            bd_executable=args.bd,
            hermes_executable=args.hermes,
            poll_seconds=args.poll_seconds,
            worker_timeout_seconds=args.worker_timeout,
            allow_unattended=args.allow_unattended,
        )
        result = supervisor.run(once=args.once)
        print(json.dumps(_redact_value(result), ensure_ascii=False, sort_keys=True))
        return 0 if result.get("status") in {"running", "completed"} else 1
    except (FactoryError, BeadsAdapterError, SupervisorError, OSError) as exc:
        print(json.dumps({"success": False, "error": _redact_text(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":  # pragma: no cover - exercised through CLI integration
    raise SystemExit(main())
