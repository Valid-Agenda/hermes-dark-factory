from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from plugin.engine import initial_state
from plugin.beads_adapter import _canonical_digest, build_graph_plan
from plugin.supervisor import DarkFactorySupervisor, SupervisorError

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = json.loads((ROOT / "templates" / "manifest.example.json").read_text(encoding="utf-8"))


class SupervisorTests(unittest.TestCase):
    def _fixture(self, tmp: str) -> tuple[dict, Path, Path, dict[str, str]]:
        manifest = copy.deepcopy(TEMPLATE)
        manifest["mission"]["workspace_path"] = str(ROOT)
        manifest["execution"]["graph_mode"] = "apply"
        beads_dir = Path(tmp) / ".beads"
        beads_dir.mkdir()
        (beads_dir / "store.marker").write_text("initialized\n", encoding="utf-8")
        manifest["execution"]["beads_directory"] = str(beads_dir)
        state_path = Path(tmp) / ".hermes" / "factory" / "state.json"
        state_path.parent.mkdir(parents=True)
        state_path.write_text(json.dumps(initial_state(manifest), indent=2) + "\n", encoding="utf-8")
        plan = build_graph_plan(manifest)
        ids = {
            str(node["key"]): f"bf-{index}"
            for index, node in enumerate(plan["nodes"], start=1)
        }
        receipt = {
            "manifest_digest": _canonical_digest(manifest),
            "plan_digest": _canonical_digest(plan),
            "ids": ids,
        }
        (state_path.parent / "beads-graph-receipt.json").write_text(
            json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
        )
        entity_to_bead = {}
        for node in plan["nodes"]:
            entity_id = node["metadata"]["dark_factory_entity_id"]
            entity_to_bead[entity_id] = ids[node["key"]]
        return manifest, state_path, beads_dir, entity_to_bead

    def test_constructor_requires_applied_graph_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest, state_path, beads_dir, _ = self._fixture(tmp)
            manifest["execution"]["graph_mode"] = "plan"
            manifest_path = Path(tmp) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
            with self.assertRaises(SupervisorError):
                DarkFactorySupervisor(
                    manifest_path,
                    state_path,
                    profile="solana",
                    bd_executable="bd",
                    allow_unattended=True,
                )

    def test_tick_claims_ready_milestone_from_beads_before_launch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest, state_path, beads_dir, entity_to_bead = self._fixture(tmp)
            manifest_path = Path(tmp) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
            calls: list[list[str]] = []

            def fake_bd(_executable: str, argv: list[str], _directory: Path, **_kwargs):
                calls.append(list(argv))
                if argv and argv[0] == "ready":
                    return [{"id": bead_id} for bead_id in entity_to_bead.values()]
                return {}

            with patch("plugin.supervisor._resolve_bd_executable", return_value="bd"), patch(
                "plugin.supervisor.preflight_beads", return_value={"bd_version": "0.1.0"}
            ), patch("plugin.supervisor._run_bd", side_effect=fake_bd):
                supervisor = DarkFactorySupervisor(
                    manifest_path,
                    state_path,
                    profile="solana",
                    bd_executable="bd",
                    hermes_executable="hermes",
                    poll_seconds=1,
                    worker_timeout_seconds=60,
                    allow_unattended=True,
                )
                supervisor._start_process = MagicMock()  # type: ignore[method-assign]
                result = supervisor.tick()

            self.assertEqual(result["status"], "running")
            self.assertTrue(any(argv and argv[0] == "ready" for argv in calls))
            claims = [argv for argv in calls if "--claim" in argv]
            self.assertEqual(len(claims), 1)
            self.assertIn(entity_to_bead["M1"], claims[0])
            self.assertEqual(supervisor._start_process.call_args.kwargs["phase"], "milestone_start")

    def test_repeated_identical_failure_is_the_circuit_breaker_not_wall_clock(self) -> None:
        supervisor = DarkFactorySupervisor.__new__(DarkFactorySupervisor)
        supervisor.manifest = {"policy": {"repeated_failure_limit": 2}}
        supervisor.meta = {"status": "running", "failure_fingerprints": {}, "history": []}
        supervisor._record_failure({"run_id": "one", "entity_id": "M1", "phase": "block"}, "same", "first")
        self.assertEqual(supervisor.meta["status"], "running")
        supervisor._record_failure({"run_id": "two", "entity_id": "M1", "phase": "block"}, "same", "second")
        self.assertEqual(supervisor.meta["status"], "blocked")
        self.assertIn("repeated identical", supervisor.meta["stop_reason"])

    def test_unattended_authorization_is_mandatory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest, state_path, _beads_dir, _ = self._fixture(tmp)
            manifest_path = Path(tmp) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
            with self.assertRaises(SupervisorError):
                DarkFactorySupervisor(
                    manifest_path,
                    state_path,
                    profile="solana",
                    allow_unattended=False,
                )


if __name__ == "__main__":
    unittest.main()
