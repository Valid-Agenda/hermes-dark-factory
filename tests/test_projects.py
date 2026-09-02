from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from hermes_test_stubs import ensure_inventory_module
from plugin.engine import initial_state
from plugin.intake import default_setup
from plugin import project_store

ROOT = Path(__file__).resolve().parents[1]


def catalog() -> dict:
    return {
        "profile": "default",
        "providers": [
            {
                "slug": "openai-codex",
                "label": "OpenAI Codex",
                "authenticated": True,
                "models": ["gpt-5.6-sol-900k", "gpt-5.6-luna", "gpt-5.6-terra"],
            }
        ],
        "current": {"provider": "openai-codex", "model": "gpt-5.6-terra"},
        "credentials_included": False,
    }


def native_project(path: str, project_id: str = "p_test") -> dict:
    return {
        "id": project_id,
        "slug": "demo",
        "name": "Demo",
        "description": "A test project",
        "primary_path": path,
        "board_slug": "demo-board",
        "archived": False,
        "folders": [{"path": path, "label": None, "is_primary": True, "added_at": 1}],
    }


class ProjectStoreTests(unittest.TestCase):
    def test_global_defaults_and_sparse_overrides_are_strict_and_redacted(self) -> None:
        config = project_store.normalise_global_config(
            {
                "coordination": {"mode": "beads"},
                "system_prompts": {"builder": "Use a bounded acceptance contract in the hidden test"},
            }
        )
        self.assertEqual(config["coordination"]["mode"], "beads")
        self.assertEqual(config["system_prompts"]["builder"], "Use a bounded acceptance contract in the hidden test")
        with self.assertRaises(Exception):
            project_store.normalise_global_config(
                {"system_prompts": {"builder": "Use Bearer secret-value-must-not-persist"}}
            )
        overrides = project_store.normalise_overrides(
            {"models": {"builder": {"provider": "openai-codex", "model": "gpt-5.6-luna"}}}
        )
        self.assertEqual(overrides["models"]["builder"]["model"], "gpt-5.6-luna")
        with self.assertRaises(Exception):
            project_store.normalise_global_config({"coordination": {"mode": "unsupported"}})
        with self.assertRaises(Exception):
            project_store.normalise_overrides({"api_key": "not persisted"})
        legacy = project_store.normalise_global_config(
            {"coordination": {"mode": "both", "kanban_board": "legacy"}},
            allow_legacy=True,
        )
        self.assertEqual(legacy["coordination"]["mode"], "beads")
        with self.assertRaises(Exception):
            project_store.normalise_global_config({"coordination": {"mode": "both"}})

    def test_project_records_round_trip_atomically_and_reset_to_empty_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with patch.object(project_store, "plugin_data_dir", return_value=data_dir):
                setup = default_setup()
                setup["workspace_path"] = str(data_dir)
                saved = project_store.save_project_record(
                    "p_test",
                    setup=setup,
                    overrides={"coordination": {"mode": "beads"}},
                )
                self.assertEqual(saved["overrides"]["coordination"]["mode"], "beads")
                loaded = project_store.load_project_record("p_test")
                self.assertEqual(loaded["setup"]["workspace_path"], str(data_dir.resolve()))
                project_store.save_project_record("p_test", setup=setup, overrides={})
                self.assertEqual(project_store.load_project_record("p_test")["overrides"], {})
                self.assertTrue((data_dir / "projects.json").is_file())

    def test_progress_and_logs_use_state_events_without_echoing_sensitive_evidence(self) -> None:
        manifest = {"milestones": [{"id": "M1", "outcome": "Ship a durable project workflow"}], "slices": [{"id": "S1", "outcome": "Build the workflow"}]}
        state = {
            "milestones": {"M1": {"status": "active"}},
            "slices": {"S1": {"status": "completed"}},
            "events": [
                {
                    "at": "2026-09-01T00:00:00Z",
                    "entity_id": "M1",
                    "action": "start_milestone",
                    "actor": {"role": "integrator", "provider": "openai-codex", "model": "gpt-5.6-sol-900k"},
                    "evidence": {"note": "Bearer secret-value-must-not-leak"},
                }
            ],
        }
        snapshot = {"manifest": manifest, "state": state, "factory_dir": "/tmp/factory", "errors": []}
        progress = project_store.progress_snapshot(snapshot)
        self.assertEqual(progress["status"], "active")
        self.assertEqual(progress["completed_slices"], 1)
        logs = project_store.project_logs(snapshot)
        self.assertIn("start_milestone", logs["text"])
        self.assertNotIn("secret-value-must-not-leak", logs["text"])

    def test_project_workspace_rejects_a_path_outside_native_project_folders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            outside = Path(tmp) / "outside"
            root.mkdir()
            outside.mkdir()
            project = native_project(str(root))
            setup = default_setup()
            setup["workspace_path"] = str(outside)
            with self.assertRaises(Exception):
                project_store.setup_for_project(project, {"setup": setup})


class ProjectApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        ensure_inventory_module()
        api_path = ROOT / "plugin" / "dashboard" / "plugin_api.py"
        spec = importlib.util.spec_from_file_location("dark_factory_projects_api", api_path)
        assert spec and spec.loader
        cls.api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.api)

    def test_project_list_reports_native_projects_and_factory_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = native_project(tmp)
            with patch.object(self.api, "native_projects", return_value=[project]), patch.object(
                self.api, "_active_profile_name", return_value="default"
            ):
                payload = self.api.get_projects()
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["projects"][0]["id"], "p_test")
            self.assertEqual(payload["projects"][0]["progress"]["status"], "not_armed")
            self.assertFalse(payload["credentials_included"])

    def test_project_config_can_be_saved_and_reset_without_touching_global_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "plugin-data"
            project = native_project(tmp)
            with patch.object(self.api, "native_project", return_value=project), patch.object(
                self.api, "_required_model_options", return_value=catalog()
            ), patch.object(self.api._project_store, "plugin_data_dir", return_value=data_dir):
                saved = self.api.put_project_config(
                    "p_test", {"overrides": {"coordination": {"mode": "beads"}}}
                )
                self.assertEqual(saved["config"]["effective"]["coordination"]["mode"], "beads")
                self.assertTrue(saved["config"]["has_overrides"])
                reset = self.api.put_project_config("p_test", {"overrides": {}})
            self.assertEqual(reset["config"]["overrides"], {})
            self.assertFalse(reset["config"]["has_overrides"])
            self.assertEqual(json.loads((data_dir / "projects.json").read_text())["projects"]["p_test"]["overrides"], {})

    def test_project_setup_endpoint_fails_closed_for_cross_project_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            outside = Path(tmp) / "outside"
            root.mkdir()
            outside.mkdir()
            project = native_project(str(root))
            setup = default_setup()
            setup["workspace_path"] = str(outside)
            with patch.object(self.api, "native_project", return_value=project):
                with self.assertRaises(HTTPException) as raised:
                    self.api.put_project_detail("p_test", {"setup": setup, "overrides": {}})
            self.assertEqual(raised.exception.status_code, 422)

    def test_project_detail_compiles_prompt_config_into_setup_and_manifest_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = native_project(tmp)
            with patch.object(self.api, "native_project", return_value=project), patch.object(
                self.api, "_required_model_options", return_value=catalog()
            ), patch.object(self.api._project_store, "plugin_data_dir", return_value=Path(tmp) / "data"):
                self.api.put_project_config(
                    "p_test",
                    {
                        "overrides": {
                            "system_prompts": {"builder": "Build only after the acceptance contract passes."},
                            "coordination": {"mode": "beads"},
                        }
                    },
                )
                detail = self.api.get_project_detail("p_test")
            self.assertEqual(detail["setup"]["system_prompts"]["builder"], "Build only after the acceptance contract passes.")
            self.assertEqual(detail["setup"]["execution"]["graph_backend"], "beads")
            self.assertFalse(detail["credentials_included"] if "credentials_included" in detail else False)

    def test_project_compile_requires_verified_beads_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = native_project(tmp)
            with patch.object(self.api, "native_project", return_value=project), patch.object(
                self.api, "_required_model_options", return_value=catalog()
            ), patch.object(self.api._project_store, "plugin_data_dir", return_value=Path(tmp) / "data"), patch.object(
                self.api._project_store, "beads_status", return_value={
                    "required": True,
                    "cli_available": False,
                    "cli_path": "",
                    "version": "",
                    "directory": str(Path(tmp) / ".beads"),
                    "initialized": False,
                    "authorized_for_writes": False,
                    "ready": False,
                    "reason": "Beads CLI is not available to the Hermes runtime",
                }
            ):
                with self.assertRaises(HTTPException) as raised:
                    self.api.compile_project("p_test", {})
            self.assertEqual(raised.exception.status_code, 422)
            self.assertEqual(raised.exception.detail["message"], "Beads is required before a project can be compiled.")
            self.assertFalse(raised.exception.detail["beads"]["cli_available"])


if __name__ == "__main__":
    unittest.main()
