from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from plugin import engine
from plugin.beads_adapter import (
    BeadsAdapterError,
    SUPPORTED_BEADS_CLI_VERSION,
    apply_graph_plan,
    build_graph_plan,
    dry_run_graph_plan,
    graph_plan_json,
    preflight_beads,
)

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = json.loads((ROOT / "templates" / "manifest.example.json").read_text(encoding="utf-8"))
# Keep this adapter fixture valid when the shared manifest validator gains
# required threat identity fields before the example is refreshed.
for threat_index, threat in enumerate(TEMPLATE["security"]["threat_scenarios"], start=1):
    threat.setdefault("id", f"T{threat_index}")


FAKE_BD = r'''#!/usr/bin/env python3
import copy
import json
import os
import pathlib
import sys

args = sys.argv[1:]
root = pathlib.Path(__file__).resolve().parent
log = root / "bd-calls.jsonl"
config_path = root / "fake-bd-config.json"
state_path = root / "fake-bd-state.json"
config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
with log.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({
        "argv": args,
        "beads_dir": os.environ.get("BEADS_DIR"),
        "dummy_secret_present": "DARK_FACTORY_DUMMY_SECRET" in os.environ,
        "metrics_disabled": os.environ.get("BD_DISABLE_METRICS"),
        "daemon_disabled": os.environ.get("BD_NO_DAEMON"),
    }) + "\n")


def load_state():
    if not state_path.exists():
        return {"plan": {"nodes": [], "edges": []}, "ids": {}}
    return json.loads(state_path.read_text(encoding="utf-8"))


def require_string_metadata(plan):
    for node in plan.get("nodes", []):
        metadata = node.get("metadata")
        if not isinstance(metadata, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in metadata.items()
        ):
            print("GraphApplyNode.nodes.metadata requires string keys and values", file=sys.stderr)
            sys.exit(4)


def relations(state):
    plan = state["plan"]
    ids = state["ids"]
    result = []
    for node in plan["nodes"]:
        if node.get("parent_key"):
            result.append([ids[node["key"]], ids[node["parent_key"]], "parent-child"])
    for edge in plan["edges"]:
        result.append([ids[edge["from_key"]], ids[edge["to_key"]], edge["type"]])

    if config.get("parent_mismatch"):
        for index, relation in enumerate(result):
            if relation[2] == "parent-child":
                alternatives = [value for value in ids.values() if value not in relation[:2]]
                if alternatives:
                    result[index] = [relation[0], alternatives[0], relation[2]]
                break
    blocker_indexes = [index for index, relation in enumerate(result) if relation[2] == "blocks"]
    if config.get("missing_edge") and blocker_indexes:
        result.pop(blocker_indexes[0])
    elif config.get("reversed_edge") and blocker_indexes:
        index = blocker_indexes[0]
        result[index] = [result[index][1], result[index][0], result[index][2]]
    if config.get("extra_edge"):
        values = list(ids.values())
        candidate = [values[0], values[-1], "blocks"]
        if candidate in result:
            candidate = [values[-1], values[0], "blocks"]
        result.append(candidate)
    return result


def issue_row(state, bead_id):
    ids = state["ids"]
    key = next(key for key, value in ids.items() if value == bead_id)
    node = next(node for node in state["plan"]["nodes"] if node["key"] == key)
    row = {
        "id": bead_id,
        "title": node["title"],
        "description": node["description"],
        "status": "open",
        "priority": node["priority"],
        "issue_type": node["type"],
        "metadata": copy.deepcopy(node["metadata"]),
        "labels": list(node["labels"]),
    }
    parent = next((target for source, target, kind in relations(state) if source == bead_id and kind == "parent-child"), None)
    if parent:
        row["parent"] = parent
    if config.get("acceptance_metadata_loss"):
        row["metadata"].pop("dark_factory_acceptance", None)
    acceptance_mutation = config.get("acceptance_metadata_mutation")
    if acceptance_mutation:
        encoded = row["metadata"]["dark_factory_acceptance"]
        if acceptance_mutation == "malformed":
            row["metadata"]["dark_factory_acceptance"] = encoded[:-1]
        elif acceptance_mutation == "noncanonical":
            row["metadata"]["dark_factory_acceptance"] = json.dumps(
                json.loads(encoded), sort_keys=True, ensure_ascii=False
            )
        elif acceptance_mutation == "lost_type":
            decoded = json.loads(encoded)
            for criterion in decoded:
                if isinstance(criterion, dict) and "type" in criterion:
                    criterion.pop("type")
                    break
            row["metadata"]["dark_factory_acceptance"] = json.dumps(
                decoded, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            )
    if config.get("bad_routing"):
        row["metadata"]["execution_model"] = "wrong-model"
    mismatch = config.get("node_mismatch_field")
    if mismatch == "type":
        row["issue_type"] = "bug"
    elif mismatch == "labels":
        row["labels"] = ["dark-factory"]
    elif mismatch in {"title", "description", "priority"}:
        row[mismatch] = 4 if mismatch == "priority" else "wrong-" + mismatch
    return row


if config.get("force_error"):
    print(config.get("error_detail", "forced error"), file=sys.stderr)
    sys.exit(7)
if args == ["--version"]:
    print(config.get("version_output", "bd version 1.2.2 (6c124203e)"))
elif args[:3] == ["list", "--json", "--limit"]:
    if config.get("list_fail"):
        print("not initialized", file=sys.stderr)
        sys.exit(3)
    if config.get("existing"):
        print(json.dumps([{
            "id": "bd-existing",
            "status": config.get("existing_status", "closed"),
            "metadata": {
                "dark_factory_mission_id": "example-product-v1",
                "dark_factory_graph_ref": config["graph_ref"]
            }
        }]))
    else:
        print("[]")
elif args[:2] == ["create", "--graph"] and "--dry-run" in args:
    plan = json.loads(pathlib.Path(args[2]).read_text(encoding="utf-8"))
    require_string_metadata(plan)
    output = {
        "dry_run": True,
        "edge_count": len(plan["edges"]),
        "node_count": len(plan["nodes"]),
        "nodes": [
            {field: node[field] for field in ("key", "title", "type", "priority", "parent_key") if field in node}
            for node in plan["nodes"]
        ],
        "parent_deps": sum(1 for node in plan["nodes"] if node.get("parent_key")),
        "schema_version": 1,
        "validation_notes": ["structure only"],
    }
    if config.get("malformed_dry_run"):
        output.pop("nodes")
    print(json.dumps(output))
elif args[:2] == ["create", "--graph"]:
    plan = json.loads(pathlib.Path(args[2]).read_text(encoding="utf-8"))
    require_string_metadata(plan)
    ids = {node["key"]: "bd-" + str(index + 1) for index, node in enumerate(plan["nodes"])}
    state_path.write_text(json.dumps({"plan": plan, "ids": ids}), encoding="utf-8")
    print(json.dumps({"ids": ids, "schema_version": 1}))
elif args and args[0] == "show":
    requested_ids = [value for value in args[1:] if value != "--json"]
    state = load_state()
    rows = [issue_row(state, value) for value in requested_ids]
    if config.get("bad_show") and len(rows) > 1:
        rows[0]["metadata"], rows[1]["metadata"] = rows[1]["metadata"], rows[0]["metadata"]
    print(json.dumps(rows))
elif args[:2] == ["dep", "list"]:
    state = load_state()
    requested_id = args[2]
    direction = "up" if "--direction=up" in args else "down"
    rows = []
    for source, target, kind in relations(state):
        if direction == "down" and source == requested_id:
            row = issue_row(state, target)
        elif direction == "up" and target == requested_id:
            row = issue_row(state, source)
        else:
            continue
        row["dependency_type"] = kind
        rows.append(row)
    print(json.dumps(rows))
else:
    print(json.dumps({"error": "unexpected argv", "argv": args}))
    sys.exit(9)
'''


class BeadsGraphPlanTests(unittest.TestCase):
    def test_builds_deterministic_mission_milestone_slice_graph_without_micro_beads(self) -> None:
        first = build_graph_plan(TEMPLATE)
        second = build_graph_plan(copy.deepcopy(TEMPLATE))
        self.assertEqual(first, second)
        self.assertEqual(graph_plan_json(first), graph_plan_json(second))
        self.assertEqual(set(first), {"commit_message", "nodes", "edges"})
        self.assertEqual(len(first["nodes"]), 4)
        self.assertEqual(
            [node["type"] for node in first["nodes"]],
            ["epic", "epic", "task", "task"],
        )

        slice_nodes = [node for node in first["nodes"] if node["type"] == "task"]
        self.assertEqual(
            [node["title"] for node in slice_nodes],
            [item["outcome"] for item in TEMPLATE["slices"]],
        )
        for node, source_slice in zip(slice_nodes, TEMPLATE["slices"], strict=True):
            lint = engine.lint_card(node["title"], node["description"])
            self.assertTrue(lint["valid"], lint)
            self.assertEqual(lint["errors"], [])
            self.assertNotIn(
                "title looks like a micro-remediation; keep it inside the active functional slice",
                lint["warnings"],
            )
            self.assertIsNone(engine.MICRO_TITLE.search(node["title"]))
            self.assertTrue(all(isinstance(value, str) for value in node["metadata"].values()))
            encoded_acceptance = node["metadata"]["dark_factory_acceptance"]
            self.assertIsInstance(encoded_acceptance, str)
            self.assertEqual(json.loads(encoded_acceptance), source_slice["acceptance"])
            self.assertEqual(
                encoded_acceptance,
                json.dumps(
                    source_slice["acceptance"],
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ),
            )
            self.assertEqual(
                [
                    (criterion["id"], criterion["type"], criterion["statement"])
                    for criterion in json.loads(encoded_acceptance)
                ],
                [
                    (criterion["id"], criterion["type"], criterion["statement"])
                    for criterion in source_slice["acceptance"]
                ],
            )

        mission, milestone, first_slice, second_slice = first["nodes"]
        self.assertEqual(milestone["parent_key"], mission["key"])
        self.assertEqual(first_slice["parent_key"], milestone["key"])
        self.assertEqual(second_slice["parent_key"], milestone["key"])
        self.assertIn("M1-S1-A1", first_slice["description"])
        self.assertIn("Evidence:", first_slice["description"])
        self.assertIn("Forbidden:", first_slice["description"])
        self.assertIn("Stop / escalate:", first_slice["description"])
        self.assertEqual(first_slice["metadata"]["dark_factory_entity_type"], "functional_slice")
        self.assertEqual(first_slice["metadata"]["execution_configured_role"], "builder")
        self.assertEqual(first_slice["metadata"]["execution_agent_type"], "worker")
        self.assertEqual(
            first_slice["metadata"]["execution_suggested_model"],
            TEMPLATE["models"]["builder"]["provider"] + "/" + TEMPLATE["models"]["builder"]["model"],
        )
        self.assertEqual(
            milestone["metadata"]["execution_agent_type"],
            "orchestrator",
        )

        self.assertEqual(
            first["edges"],
            [{"from_key": second_slice["key"], "to_key": first_slice["key"], "type": "blocks"}],
        )
        parent_pairs = {(node["key"], node.get("parent_key")) for node in first["nodes"] if node.get("parent_key")}
        edge_pairs = {(edge["from_key"], edge["to_key"]) for edge in first["edges"]}
        self.assertTrue(parent_pairs.isdisjoint(edge_pairs))

        self.assertEqual(
            json.loads(first_slice["metadata"]["dark_factory_acceptance"]),
            TEMPLATE["slices"][0]["acceptance"],
        )
        self.assertIn("[M1-S1-A1] (happy)", first_slice["description"])
        self.assertIn("[M1-S1-A2] (negative)", first_slice["description"])

    def test_acceptance_metadata_uses_unescaped_utf8_canonical_json(self) -> None:
        manifest = copy.deepcopy(TEMPLATE)
        statement = "Café résumé ✓ remains typed"
        manifest["slices"][0]["acceptance"][0]["statement"] = statement
        plan = build_graph_plan(manifest, validate=False)
        encoded = next(
            node["metadata"]["dark_factory_acceptance"]
            for node in plan["nodes"]
            if node["metadata"]["dark_factory_entity_id"] == manifest["slices"][0]["id"]
        )
        self.assertIn(statement, encoded)
        self.assertNotIn("\\u", encoded)
        self.assertEqual(json.loads(encoded), manifest["slices"][0]["acceptance"])

    def test_milestone_blocker_edges_point_from_dependent_to_prerequisite(self) -> None:
        manifest = copy.deepcopy(TEMPLATE)
        second = copy.deepcopy(manifest["milestones"][0])
        second["id"] = "M2"
        second["depends_on"] = ["M1"]
        second["slices"] = []
        second["story_ids"] = []
        second["acceptance"][0]["id"] = "M2-A1"
        manifest["milestones"].append(second)
        # Keep this adapter-only fixture structurally coherent enough for graph construction.
        manifest["mission"]["user_stories"] = manifest["mission"]["user_stories"][:2]
        plan = build_graph_plan(manifest, validate=False)
        by_entity = {
            node["metadata"]["dark_factory_entity_id"]: node["key"]
            for node in plan["nodes"]
        }
        self.assertIn(
            {"from_key": by_entity["M2"], "to_key": by_entity["M1"], "type": "blocks"},
            plan["edges"],
        )

    def test_distinct_entity_ids_with_same_slug_get_unique_stable_keys(self) -> None:
        manifest = copy.deepcopy(TEMPLATE)
        # These IDs share both the same slug and the same first eight SHA-256
        # hex characters; key uniqueness must not depend on a truncated hash.
        first_id = "M1?~|S1"
        second_id = "M1.|(+S1"
        manifest["slices"][0]["id"] = first_id
        manifest["slices"][0]["depends_on"] = []
        manifest["slices"][1]["id"] = second_id
        manifest["slices"][1]["depends_on"] = [first_id]
        manifest["milestones"][0]["slices"] = [first_id, second_id]
        first = build_graph_plan(manifest)
        second = build_graph_plan(copy.deepcopy(manifest))
        keys = [node["key"] for node in first["nodes"]]
        self.assertEqual(first, second)
        self.assertEqual(len(keys), len(set(keys)))
        by_entity = {
            node["metadata"]["dark_factory_entity_id"]: node["key"]
            for node in first["nodes"]
        }
        self.assertNotEqual(by_entity[first_id], by_entity[second_id])
        self.assertTrue(by_entity[first_id].startswith("slice-m1-s1-"))
        self.assertTrue(by_entity[second_id].startswith("slice-m1-s1-"))

    def test_invalid_schema_v2_manifest_fails_before_plan_generation(self) -> None:
        manifest = copy.deepcopy(TEMPLATE)
        manifest["models"]["builder"] = copy.deepcopy(manifest["models"]["verifier"])
        with self.assertRaisesRegex(BeadsAdapterError, "invalid Dark Factory manifest"):
            build_graph_plan(manifest)


class BeadsApplySafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fake_bd = self.root / "fake-bd"
        self.fake_bd.write_text(FAKE_BD, encoding="utf-8")
        self.fake_bd.chmod(0o755)
        self.log = self.root / "bd-calls.jsonl"
        self.config_path = self.root / "fake-bd-config.json"
        self.isolated = self.root / "isolated-beads"
        self.isolated.mkdir()
        self.workspace = self.root / "workspace"
        (self.workspace / ".hermes" / "factory").mkdir(parents=True)
        self.manifest = copy.deepcopy(TEMPLATE)
        self.manifest["mission"]["workspace_path"] = str(self.workspace)
        self.config: dict[str, object] = {}
        self.write_config()
        self.env: dict[str, str] = {}

    def write_config(self, **updates: object) -> None:
        self.config.update(updates)
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def apply(self) -> dict:
        with mock.patch.dict(os.environ, self.env, clear=False):
            return apply_graph_plan(
                self.manifest,
                self.isolated,
                bd_executable=str(self.fake_bd),
                authorize_isolated=True,
            )

    def test_preflight_fails_closed_when_bd_is_missing(self) -> None:
        with self.assertRaisesRegex(BeadsAdapterError, "executable is unavailable"):
            preflight_beads(self.isolated, bd_executable=str(self.root / "missing-bd"), authorize_isolated=True)

    def test_uninitialized_directory_requires_explicit_isolated_authorization(self) -> None:
        with self.assertRaisesRegex(BeadsAdapterError, "not an existing .beads directory"):
            with mock.patch.dict(os.environ, self.env, clear=False):
                preflight_beads(self.isolated, bd_executable=str(self.fake_bd))

        empty_named_store = self.root / ".beads"
        empty_named_store.mkdir()
        with self.assertRaisesRegex(BeadsAdapterError, "not an existing .beads directory"):
            with mock.patch.dict(os.environ, self.env, clear=False):
                preflight_beads(empty_named_store, bd_executable=str(self.fake_bd))

    def test_preflight_rejects_non_beads_directory_even_when_authorized(self) -> None:
        arbitrary = self.root / "not-a-store"
        arbitrary.mkdir()
        (arbitrary / "unrelated.txt").write_text("not beads", encoding="utf-8")
        self.write_config(list_fail=True)
        with self.assertRaisesRegex(BeadsAdapterError, "not a readable initialized Beads store"):
            with mock.patch.dict(os.environ, self.env, clear=False):
                preflight_beads(arbitrary, bd_executable=str(self.fake_bd), authorize_isolated=True)

    def test_preflight_accepts_only_pinned_beads_cli_version(self) -> None:
        self.assertEqual(SUPPORTED_BEADS_CLI_VERSION, "1.2.2")
        for version_output in (
            "bd version 1.2.2",
            "bd version 1.2.2 (6c124203e)",
        ):
            with self.subTest(version_output=version_output):
                self.write_config(version_output=version_output)
                result = preflight_beads(
                    self.isolated,
                    bd_executable=str(self.fake_bd),
                    authorize_isolated=True,
                )
                self.assertEqual(result["bd_version"], SUPPORTED_BEADS_CLI_VERSION)

    def test_preflight_rejects_unpinned_or_malformed_beads_cli_versions(self) -> None:
        for version_output in (
            "bd version 1.2.1",
            "bd version 1.3.0",
            "bd version 2.0.0",
            "bd version 9.9.9",
            "bd version 1.2",
            "1.2.2",
            "bd version 1.2.2 unexpected",
            "not a version",
        ):
            with self.subTest(version_output=version_output):
                self.write_config(version_output=version_output)
                with self.assertRaises(BeadsAdapterError) as caught:
                    preflight_beads(
                        self.isolated,
                        bd_executable=str(self.fake_bd),
                        authorize_isolated=True,
                    )
                self.assertEqual(str(caught.exception), "unsupported Beads CLI version")

    def test_subprocess_gets_only_allowlisted_environment_with_forced_safety_flags(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "DARK_FACTORY_DUMMY_SECRET": "must-not-be-inherited",
                "BD_NO_DAEMON": "0",
            },
            clear=False,
        ):
            preflight_beads(self.isolated, bd_executable=str(self.fake_bd), authorize_isolated=True)
        self.assertTrue(self.calls())
        self.assertTrue(all(not call["dummy_secret_present"] for call in self.calls()))
        self.assertTrue(all(call["metrics_disabled"] == "1" for call in self.calls()))
        self.assertTrue(all(call["daemon_disabled"] == "1" for call in self.calls()))

    def test_subprocess_error_is_redacted_and_bounded(self) -> None:
        secret = "correct-horse-battery-staple"
        self.write_config(
            force_error=True,
            error_detail=f"password={secret} connection_string=postgres://user:other-secret@db " + ("x" * 5000),
        )
        with self.assertRaises(BeadsAdapterError) as caught:
            preflight_beads(self.isolated, bd_executable=str(self.fake_bd), authorize_isolated=True)
        detail = str(caught.exception)
        self.assertNotIn(secret, detail)
        self.assertNotIn("other-secret", detail)
        self.assertIn("[REDACTED]", detail)
        self.assertIn("[truncated]", detail)
        self.assertLess(len(detail), 1200)

    def test_dry_run_uses_json_argv_and_has_no_apply_side_effect(self) -> None:
        with mock.patch.dict(os.environ, self.env, clear=False):
            result = dry_run_graph_plan(
                build_graph_plan(self.manifest),
                self.isolated,
                bd_executable=str(self.fake_bd),
                authorize_isolated=True,
            )
        self.assertEqual(result["bd_version"], "1.2.2")
        calls = self.calls()
        self.assertEqual(calls[0]["argv"], ["--version"])
        self.assertEqual(calls[1]["argv"], ["list", "--json", "--limit", "0", "--all"])
        self.assertEqual(calls[2]["argv"][0:2], ["create", "--graph"])
        self.assertIn("--dry-run", calls[2]["argv"])
        self.assertIn("--json", calls[2]["argv"])
        self.assertEqual(calls[2]["beads_dir"], str(self.isolated.resolve()))
        self.assertFalse(any(call["argv"][:2] == ["create", "--graph"] and "--dry-run" not in call["argv"] for call in calls))
        plan = build_graph_plan(self.manifest)
        self.assertEqual(result["dry_run"]["node_count"], len(plan["nodes"]))
        self.assertEqual(result["dry_run"]["edge_count"], len(plan["edges"]))

    def test_non_string_array_or_object_metadata_is_rejected_before_any_beads_call(self) -> None:
        for invalid in ([], {}):
            with self.subTest(invalid=type(invalid).__name__):
                plan = build_graph_plan(self.manifest)
                plan["nodes"][0]["metadata"]["invalid"] = invalid
                with self.assertRaisesRegex(BeadsAdapterError, "metadata keys and values must be strings"):
                    dry_run_graph_plan(
                        plan,
                        self.isolated,
                        bd_executable=str(self.fake_bd),
                        authorize_isolated=True,
                    )
        self.assertEqual(self.calls(), [])

    def test_malformed_dry_run_fails_closed_before_apply(self) -> None:
        self.write_config(malformed_dry_run=True)
        with self.assertRaisesRegex(BeadsAdapterError, "dry-run response is missing planned node coverage"):
            self.apply()
        self.assertFalse(any(
            call["argv"][:2] == ["create", "--graph"] and "--dry-run" not in call["argv"]
            for call in self.calls()
        ))

    def test_apply_dry_runs_applies_verifies_and_writes_atomic_receipt(self) -> None:
        with mock.patch.dict(os.environ, self.env, clear=False):
            result = apply_graph_plan(
                self.manifest,
                self.isolated,
                bd_executable=str(self.fake_bd),
                authorize_isolated=True,
            )
        self.assertTrue(result["applied"])
        self.assertFalse(result["idempotent_replay"])
        self.assertEqual(set(result["ids"]), {node["key"] for node in build_graph_plan(self.manifest)["nodes"]})
        receipt_path = self.workspace / ".hermes" / "factory" / "beads-graph-receipt.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["ids"], result["ids"])
        self.assertEqual(receipt["beads_dir"], str(self.isolated.resolve()))
        calls = [call["argv"] for call in self.calls()]
        self.assertIn(["--version"], calls)
        self.assertIn(["list", "--json", "--limit", "0", "--all"], calls)
        self.assertTrue(any(call[:2] == ["create", "--graph"] and "--dry-run" in call for call in calls))
        self.assertTrue(any(call[:2] == ["create", "--graph"] and "--dry-run" not in call for call in calls))
        self.assertTrue(any(call[0] == "show" for call in calls))
        self.assertTrue(any(call[:2] == ["dep", "list"] and "--direction=down" in call for call in calls))
        self.assertTrue(any(call[:2] == ["dep", "list"] and "--direction=up" in call for call in calls))
        self.assertFalse(any("init" in call or "push" in call or "sync" in call for call in calls))
        fake_state = json.loads((self.root / "fake-bd-state.json").read_text(encoding="utf-8"))
        self.assertTrue(all(
            isinstance(value, str)
            for node in fake_state["plan"]["nodes"]
            for value in node["metadata"].values()
        ))
        source_by_id = {item["id"]: item["acceptance"] for item in self.manifest["slices"]}
        for node in fake_state["plan"]["nodes"]:
            entity_id = node["metadata"]["dark_factory_entity_id"]
            if entity_id in source_by_id:
                self.assertEqual(
                    json.loads(node["metadata"]["dark_factory_acceptance"]),
                    source_by_id[entity_id],
                )

    def test_exact_retry_verifies_receipt_ids_and_does_not_create_again(self) -> None:
        with mock.patch.dict(os.environ, self.env, clear=False):
            first = apply_graph_plan(
                self.manifest,
                self.isolated,
                bd_executable=str(self.fake_bd),
                authorize_isolated=True,
            )
            before = len([c for c in self.calls() if c["argv"][:2] == ["create", "--graph"] and "--dry-run" not in c["argv"]])
            second = apply_graph_plan(
                self.manifest,
                self.isolated,
                bd_executable=str(self.fake_bd),
                authorize_isolated=True,
            )
        after = len([c for c in self.calls() if c["argv"][:2] == ["create", "--graph"] and "--dry-run" not in c["argv"]])
        self.assertEqual(before, after)
        self.assertEqual(second["ids"], first["ids"])
        self.assertFalse(second["applied"])
        self.assertTrue(second["idempotent_replay"])

    def test_exact_retry_reverifies_dependency_graph(self) -> None:
        first = self.apply()
        self.assertTrue(Path(first["receipt_path"]).exists())
        self.write_config(missing_edge=True)
        with self.assertRaisesRegex(BeadsAdapterError, "blocker/dependency edge mismatch"):
            self.apply()

    def test_exact_retry_rejects_incomplete_receipt_mapping(self) -> None:
        with mock.patch.dict(os.environ, self.env, clear=False):
            first = apply_graph_plan(
                self.manifest,
                self.isolated,
                bd_executable=str(self.fake_bd),
                authorize_isolated=True,
            )
            receipt_path = Path(first["receipt_path"])
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["ids"].pop(next(iter(receipt["ids"])))
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            with self.assertRaisesRegex(BeadsAdapterError, "does not exactly cover"):
                apply_graph_plan(
                    self.manifest,
                    self.isolated,
                    bd_executable=str(self.fake_bd),
                    authorize_isolated=True,
                )

    def test_closed_existing_graph_without_receipt_fails_closed_before_create(self) -> None:
        plan = build_graph_plan(self.manifest)
        self.write_config(
            existing=True,
            existing_status="closed",
            graph_ref=plan["nodes"][0]["metadata"]["dark_factory_graph_ref"],
        )
        with mock.patch.dict(os.environ, self.env, clear=False):
            with self.assertRaisesRegex(BeadsAdapterError, "already contains Dark Factory graph nodes"):
                apply_graph_plan(
                    self.manifest,
                    self.isolated,
                    bd_executable=str(self.fake_bd),
                    authorize_isolated=True,
                )
        self.assertFalse(any(call["argv"][:2] == ["create", "--graph"] for call in self.calls()))

    def test_graph_refs_are_stable_and_changed_manifest_collision_fails_closed(self) -> None:
        original_plan = build_graph_plan(self.manifest)
        changed = copy.deepcopy(self.manifest)
        changed["mission"]["outcome"] += " revised"
        changed_plan = build_graph_plan(changed, validate=False)
        self.assertEqual(
            [node["metadata"]["dark_factory_graph_ref"] for node in original_plan["nodes"]],
            [node["metadata"]["dark_factory_graph_ref"] for node in changed_plan["nodes"]],
        )
        self.write_config(existing=True, graph_ref=original_plan["nodes"][0]["metadata"]["dark_factory_graph_ref"])
        with mock.patch.dict(os.environ, self.env, clear=False):
            with self.assertRaisesRegex(BeadsAdapterError, "already contains Dark Factory graph nodes"):
                apply_graph_plan(
                    changed,
                    self.isolated,
                    bd_executable=str(self.fake_bd),
                    authorize_isolated=True,
                    validate=False,
                )

    def test_apply_rejects_key_to_id_identity_mismatch_before_receipt(self) -> None:
        self.write_config(bad_show=True)
        with mock.patch.dict(os.environ, self.env, clear=False):
            with self.assertRaisesRegex(BeadsAdapterError, "identity mismatch"):
                apply_graph_plan(
                    self.manifest,
                    self.isolated,
                    bd_executable=str(self.fake_bd),
                    authorize_isolated=True,
                )
        self.assertFalse((self.workspace / ".hermes" / "factory" / "beads-graph-receipt.json").exists())

    def test_apply_rejects_routing_metadata_mismatch_before_receipt(self) -> None:
        self.write_config(bad_routing=True)
        with mock.patch.dict(os.environ, self.env, clear=False):
            with self.assertRaisesRegex(BeadsAdapterError, "routing metadata mismatch"):
                apply_graph_plan(
                    self.manifest,
                    self.isolated,
                    bd_executable=str(self.fake_bd),
                    authorize_isolated=True,
                )
        self.assertFalse((self.workspace / ".hermes" / "factory" / "beads-graph-receipt.json").exists())

    def test_apply_rejects_complete_node_contract_mismatches_before_receipt(self) -> None:
        for field in ("title", "type", "description", "priority", "labels"):
            with self.subTest(field=field):
                self.write_config(node_mismatch_field=field)
                with self.assertRaisesRegex(BeadsAdapterError, f"node contract mismatch.*{field}"):
                    self.apply()
                self.assertFalse((self.workspace / ".hermes" / "factory" / "beads-graph-receipt.json").exists())

    def test_apply_rejects_acceptance_metadata_loss_before_receipt(self) -> None:
        self.write_config(acceptance_metadata_loss=True)
        with self.assertRaisesRegex(BeadsAdapterError, "acceptance metadata is not a string"):
            self.apply()
        self.assertFalse((self.workspace / ".hermes" / "factory" / "beads-graph-receipt.json").exists())

    def test_apply_rejects_malformed_noncanonical_or_type_lost_acceptance_metadata(self) -> None:
        for mutation, detail in (
            ("malformed", "malformed"),
            ("noncanonical", "noncanonical"),
            ("lost_type", "acceptance metadata mismatch"),
        ):
            with self.subTest(mutation=mutation):
                self.write_config(acceptance_metadata_mutation=mutation)
                with self.assertRaisesRegex(BeadsAdapterError, detail):
                    self.apply()
                self.assertFalse(
                    (self.workspace / ".hermes" / "factory" / "beads-graph-receipt.json").exists()
                )

    def test_apply_rejects_parent_mismatch_before_receipt(self) -> None:
        self.write_config(parent_mismatch=True)
        with self.assertRaisesRegex(BeadsAdapterError, "parent relationship mismatch"):
            self.apply()
        self.assertFalse((self.workspace / ".hermes" / "factory" / "beads-graph-receipt.json").exists())

    def test_apply_rejects_missing_dependency_edge_before_receipt(self) -> None:
        self.write_config(missing_edge=True)
        with self.assertRaisesRegex(BeadsAdapterError, "blocker/dependency edge mismatch"):
            self.apply()
        self.assertFalse((self.workspace / ".hermes" / "factory" / "beads-graph-receipt.json").exists())

    def test_apply_rejects_reversed_dependency_edge_before_receipt(self) -> None:
        self.write_config(reversed_edge=True)
        with self.assertRaisesRegex(BeadsAdapterError, "blocker/dependency edge mismatch"):
            self.apply()
        self.assertFalse((self.workspace / ".hermes" / "factory" / "beads-graph-receipt.json").exists())

    def test_apply_rejects_extra_dependency_edge_before_receipt(self) -> None:
        self.write_config(extra_edge=True)
        with self.assertRaisesRegex(BeadsAdapterError, "blocker/dependency edge mismatch"):
            self.apply()
        self.assertFalse((self.workspace / ".hermes" / "factory" / "beads-graph-receipt.json").exists())

    def test_receipt_digest_or_directory_mismatch_fails_closed(self) -> None:
        with mock.patch.dict(os.environ, self.env, clear=False):
            apply_graph_plan(
                self.manifest,
                self.isolated,
                bd_executable=str(self.fake_bd),
                authorize_isolated=True,
            )
            changed = copy.deepcopy(self.manifest)
            changed["mission"]["outcome"] += " changed"
            with self.assertRaisesRegex(BeadsAdapterError, "receipt does not match"):
                apply_graph_plan(
                    changed,
                    self.isolated,
                    bd_executable=str(self.fake_bd),
                    authorize_isolated=True,
                    validate=False,
                )


if __name__ == "__main__":
    unittest.main()
