from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = json.loads(
    (ROOT / "templates" / "manifest.example.json").read_text(encoding="utf-8")
)
SIGNER_RELATIVE_DIRECTORY = (
    Path("plugin-data") / "dark-factory" / "offline-state-keys"
)
LOCK_RELATIVE_DIRECTORY = (
    Path("plugin-data") / "dark-factory" / "offline-state-locks"
)
FORBIDDEN_OFFLINE_KEYS = {"dispatch", "provider", "model"}
TRANSITION_ERROR = (
    "offline CLI transitions are disabled; use plugin factory_transition"
)


def nested_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {
            key
            for item in value.values()
            for key in nested_keys(item)
        }
    if isinstance(value, list):
        return {key for item in value for key in nested_keys(item)}
    return set()


def valid_manifest(workspace: Path) -> dict[str, object]:
    manifest = copy.deepcopy(TEMPLATE)
    manifest["mission"]["workspace_path"] = str(workspace.resolve())
    stories = {
        story["id"]: story
        for story in manifest["mission"]["user_stories"]
    }
    for milestone in manifest["milestones"]:
        inherited = [
            copy.deepcopy(criterion)
            for story_id in milestone["story_ids"]
            for criterion in stories[story_id]["acceptance"]
        ]
        inherited_ids = {criterion["id"] for criterion in inherited}
        milestone["acceptance"] = [
            criterion
            for criterion in milestone["acceptance"]
            if criterion.get("id") not in inherited_ids
        ] + inherited
    for index, threat in enumerate(manifest["security"]["threat_scenarios"], start=1):
        threat.setdefault("id", f"THREAT-{index}")
    return manifest


def initialize_workspace(workspace: Path) -> None:
    workspace.mkdir()
    subprocess.run(
        ["git", "init", "-q"],
        cwd=workspace,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "CLI Contract Test"],
        cwd=workspace,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "cli-contract@example.invalid"],
        cwd=workspace,
        check=True,
        capture_output=True,
    )
    (workspace / "README.md").write_text("# fixture\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "README.md"],
        cwd=workspace,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-qm", "fixture"],
        cwd=workspace,
        check=True,
        capture_output=True,
    )


def run_cli(
    manifest_path: Path,
    state_path: Path,
    hermes_home: Path,
    *command: str,
    environment_updates: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    environment = os.environ.copy()
    for name in ("HERMES_FACTORY_ROLE", "HERMES_FACTORY_STRICT"):
        environment.pop(name, None)
    environment["HERMES_HOME"] = str(hermes_home)
    if environment_updates:
        environment.update(environment_updates)
    return subprocess.run(
        [
            sys.executable,
            "scripts/factory.py",
            "--manifest",
            str(manifest_path),
            "--state",
            str(state_path),
            *command,
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        check=False,
    )


def signer_path_for(hermes_home: Path, state_path: Path) -> Path:
    identifier = hashlib.sha256(
        str(state_path.expanduser().resolve()).encode("utf-8")
    ).hexdigest()
    profile_root = hermes_home.expanduser().resolve()
    return profile_root / SIGNER_RELATIVE_DIRECTORY / identifier


def lock_path_for(hermes_home: Path, state_path: Path) -> Path:
    identifier = hashlib.sha256(
        str(state_path.expanduser().resolve()).encode("utf-8")
    ).hexdigest()
    profile_root = hermes_home.expanduser().resolve()
    return profile_root / LOCK_RELATIVE_DIRECTORY / identifier


class OfflineCliContractTests(unittest.TestCase):
    def assert_success_payload(
        self, result: subprocess.CompletedProcess[bytes]
    ) -> dict[str, object]:
        self.assertEqual(
            result.returncode,
            0,
            (result.stderr or result.stdout).decode("utf-8", errors="replace"),
        )
        payload = json.loads(result.stdout.decode("utf-8"))
        self.assertFalse(payload["execution_authorized"])
        self.assertTrue(FORBIDDEN_OFFLINE_KEYS.isdisjoint(nested_keys(payload)))
        return payload

    def assert_signer_failure(
        self, result: subprocess.CompletedProcess[bytes]
    ) -> dict[str, object]:
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout.decode("utf-8"))
        self.assertFalse(payload["success"])
        self.assertFalse(payload["execution_authorized"])
        self.assertEqual(
            payload["error"],
            "offline state signer is unavailable or invalid; "
            "discard and revalidate the offline state",
        )
        self.assertTrue(FORBIDDEN_OFFLINE_KEYS.isdisjoint(nested_keys(payload)))
        return payload

    def assert_transition_failure(
        self, result: subprocess.CompletedProcess[bytes]
    ) -> dict[str, object]:
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout.decode("utf-8"))
        self.assertFalse(payload["success"])
        self.assertFalse(payload["execution_authorized"])
        self.assertEqual(payload["error"], TRANSITION_ERROR)
        self.assertTrue(FORBIDDEN_OFFLINE_KEYS.isdisjoint(nested_keys(payload)))
        return payload

    def symlink_or_skip(
        self, target: Path, link: Path, *, target_is_directory: bool
    ) -> None:
        try:
            link.symlink_to(target, target_is_directory=target_is_directory)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symlinks unavailable: {type(exc).__name__}")

    def create_fixture(
        self, root: Path
    ) -> tuple[Path, Path, Path, Path]:
        workspace = root / "workspace"
        initialize_workspace(workspace)
        factory_directory = workspace / ".hermes" / "factory"
        factory_directory.mkdir(parents=True)
        manifest_path = factory_directory / "manifest.json"
        state_path = factory_directory / "state.json"
        hermes_home = root / "profile"
        manifest_path.write_text(
            json.dumps(valid_manifest(workspace)), encoding="utf-8"
        )
        return workspace, manifest_path, state_path, hermes_home

    def test_validate_never_emits_authenticated_dispatch_descriptors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, manifest_path, state_path, hermes_home = self.create_fixture(root)
            result = run_cli(manifest_path, state_path, hermes_home, "validate")
            payload = self.assert_success_payload(result)

            self.assertEqual(payload["next"]["startable_milestones"], ["M1"])
            self.assertIn(
                "plugin factory_validate/factory_next", payload["execution_note"]
            )
            self.assertNotIn(signer_path_for(hermes_home, state_path), workspace.parents)

    def test_next_before_validate_never_initializes_state_or_signer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, manifest_path, state_path, hermes_home = self.create_fixture(root)

            result = run_cli(manifest_path, state_path, hermes_home, "next")
            self.assert_signer_failure(result)

            self.assertFalse(state_path.exists())
            self.assertFalse(signer_path_for(hermes_home, state_path).exists())
            self.assertTrue(lock_path_for(hermes_home, state_path).is_file())

    def test_concurrent_validate_race_creates_exactly_one_signer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, manifest_path, state_path, hermes_home = self.create_fixture(root)
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [
                    executor.submit(
                        run_cli, manifest_path, state_path, hermes_home, "validate"
                    )
                    for _ in range(4)
                ]
                results = [future.result() for future in futures]

            for result in results:
                self.assert_success_payload(result)
            signer_directory = hermes_home / SIGNER_RELATIVE_DIRECTORY
            signers = list(signer_directory.iterdir())
            expected_signer = signer_path_for(hermes_home, state_path)
            self.assertEqual(signers, [expected_signer])
            lock_directory = hermes_home / LOCK_RELATIVE_DIRECTORY
            expected_lock = lock_path_for(hermes_home, state_path)
            self.assertEqual(list(lock_directory.iterdir()), [expected_lock])
            key = expected_signer.read_bytes()
            self.assertEqual(len(key), 32)
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(expected_signer.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(expected_lock.stat().st_mode), 0o600)
            for result in results:
                self.assertNotIn(key, result.stdout)
                self.assertNotIn(key, result.stderr)
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["revision"], 0)

    def test_signer_storage_inside_workspace_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, manifest_path, state_path, _ = self.create_fixture(root)
            unsafe_home = workspace / ".profile"

            result = run_cli(manifest_path, state_path, unsafe_home, "validate")
            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout.decode("utf-8"))
            self.assertFalse(payload["success"])
            self.assertFalse(payload["execution_authorized"])
            self.assertIn("offline state signer", payload["error"])
            self.assertTrue(FORBIDDEN_OFFLINE_KEYS.isdisjoint(nested_keys(payload)))
            self.assertFalse(state_path.exists())
            self.assertFalse((unsafe_home / SIGNER_RELATIVE_DIRECTORY).exists())

    def test_profile_home_symlink_uses_its_resolved_target_as_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, manifest_path, state_path, profile_link = self.create_fixture(root)
            profile_target = root / "profile-target"
            profile_target.mkdir()
            self.symlink_or_skip(
                profile_target, profile_link, target_is_directory=True
            )

            result = run_cli(manifest_path, state_path, profile_link, "validate")
            self.assert_success_payload(result)

            signer_path = signer_path_for(profile_link, state_path)
            self.assertEqual(signer_path.parents[3], profile_target.resolve())
            self.assertTrue(signer_path.is_file())
            self.assertEqual(len(signer_path.read_bytes()), 32)
            lock_path = lock_path_for(profile_link, state_path)
            self.assertEqual(lock_path.parents[3], profile_target.resolve())
            self.assertTrue(lock_path.is_file())
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(signer_path.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(lock_path.stat().st_mode), 0o600)

    def test_plugin_data_symlink_escape_creates_no_external_signer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, manifest_path, state_path, hermes_home = self.create_fixture(root)
            hermes_home.mkdir()
            external = root / "external-plugin-data"
            external.mkdir()
            sentinel = external / "sentinel"
            sentinel.write_bytes(b"unchanged")
            plugin_data = hermes_home / "plugin-data"
            self.symlink_or_skip(external, plugin_data, target_is_directory=True)
            link_target = plugin_data.readlink()
            external_signer = (
                external
                / "dark-factory"
                / "offline-state-keys"
                / signer_path_for(hermes_home, state_path).name
            )

            result = run_cli(manifest_path, state_path, hermes_home, "validate")
            self.assert_signer_failure(result)

            self.assertFalse(state_path.exists())
            self.assertFalse(external_signer.exists())
            self.assertEqual(sentinel.read_bytes(), b"unchanged")
            self.assertTrue(plugin_data.is_symlink())
            self.assertEqual(plugin_data.readlink(), link_target)
            for secret in (str(external), str(external_signer), "unchanged"):
                self.assertNotIn(secret.encode("utf-8"), result.stdout)
                self.assertNotIn(secret.encode("utf-8"), result.stderr)

    def test_nested_signer_directory_symlink_escape_creates_no_external_signer(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, manifest_path, state_path, hermes_home = self.create_fixture(root)
            signer_parent = hermes_home / "plugin-data" / "dark-factory"
            signer_parent.mkdir(parents=True)
            external = root / "external-signer-directory"
            external.mkdir()
            sentinel = external / "sentinel"
            sentinel.write_bytes(b"unchanged")
            signer_directory = signer_parent / "offline-state-keys"
            self.symlink_or_skip(
                external, signer_directory, target_is_directory=True
            )
            link_target = signer_directory.readlink()
            external_signer = external / signer_path_for(hermes_home, state_path).name

            result = run_cli(manifest_path, state_path, hermes_home, "validate")
            self.assert_signer_failure(result)

            self.assertFalse(state_path.exists())
            self.assertFalse(external_signer.exists())
            self.assertEqual(sentinel.read_bytes(), b"unchanged")
            self.assertTrue(signer_directory.is_symlink())
            self.assertEqual(signer_directory.readlink(), link_target)

    def test_nested_lock_directory_symlink_escape_creates_no_external_lock(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, manifest_path, state_path, hermes_home = self.create_fixture(root)
            lock_parent = hermes_home / "plugin-data" / "dark-factory"
            lock_parent.mkdir(parents=True)
            external = root / "external-lock-directory"
            external.mkdir()
            sentinel = external / "sentinel"
            sentinel.write_bytes(b"unchanged")
            lock_directory = lock_parent / "offline-state-locks"
            self.symlink_or_skip(external, lock_directory, target_is_directory=True)
            link_target = lock_directory.readlink()
            external_lock = external / lock_path_for(hermes_home, state_path).name

            result = run_cli(manifest_path, state_path, hermes_home, "validate")
            self.assert_signer_failure(result)

            self.assertFalse(state_path.exists())
            self.assertFalse(signer_path_for(hermes_home, state_path).exists())
            self.assertFalse(external_lock.exists())
            self.assertEqual(sentinel.read_bytes(), b"unchanged")
            self.assertTrue(lock_directory.is_symlink())
            self.assertEqual(lock_directory.readlink(), link_target)
            for secret in (str(external), str(external_lock), "unchanged"):
                self.assertNotIn(secret.encode("utf-8"), result.stdout)
                self.assertNotIn(secret.encode("utf-8"), result.stderr)

    def test_lock_file_symlink_escape_is_not_followed_or_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, manifest_path, state_path, hermes_home = self.create_fixture(root)
            lock_path = lock_path_for(hermes_home, state_path)
            lock_path.parent.mkdir(parents=True)
            external_lock = root / "external-lock"
            external_value = b"external lock remains unchanged"
            external_lock.write_bytes(external_value)
            if os.name == "posix":
                external_lock.chmod(0o600)
            self.symlink_or_skip(external_lock, lock_path, target_is_directory=False)
            link_target = lock_path.readlink()

            result = run_cli(manifest_path, state_path, hermes_home, "validate")
            self.assert_signer_failure(result)

            self.assertFalse(state_path.exists())
            self.assertFalse(signer_path_for(hermes_home, state_path).exists())
            self.assertEqual(external_lock.read_bytes(), external_value)
            self.assertTrue(lock_path.is_symlink())
            self.assertEqual(lock_path.readlink(), link_target)
            for secret in (str(external_lock).encode("utf-8"), external_value):
                self.assertNotIn(secret, result.stdout)
                self.assertNotIn(secret, result.stderr)

    def test_signer_file_symlink_escape_is_not_followed_or_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, manifest_path, state_path, hermes_home = self.create_fixture(root)
            signer_path = signer_path_for(hermes_home, state_path)
            signer_path.parent.mkdir(parents=True)
            external_signer = root / "external-signer"
            external_value = b"external signer remains unchanged"
            external_signer.write_bytes(external_value)
            if os.name == "posix":
                external_signer.chmod(0o600)
            self.symlink_or_skip(
                external_signer, signer_path, target_is_directory=False
            )
            link_target = signer_path.readlink()

            result = run_cli(manifest_path, state_path, hermes_home, "validate")
            self.assert_signer_failure(result)

            self.assertFalse(state_path.exists())
            self.assertEqual(external_signer.read_bytes(), external_value)
            self.assertTrue(signer_path.is_symlink())
            self.assertEqual(signer_path.readlink(), link_target)
            for secret in (str(external_signer).encode("utf-8"), external_value):
                self.assertNotIn(secret, result.stdout)
                self.assertNotIn(secret, result.stderr)

    def test_validate_then_next_survives_separate_processes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, manifest_path, state_path, hermes_home = self.create_fixture(root)

            validate_result = run_cli(
                manifest_path, state_path, hermes_home, "validate"
            )
            validate_payload = self.assert_success_payload(validate_result)
            state_before = state_path.read_bytes()
            initial = json.loads(state_before)
            self.assertEqual(initial["revision"], 0)
            self.assertEqual(initial["milestones"]["M1"]["status"], "pending")
            self.assertEqual(validate_payload["next"]["startable_milestones"], ["M1"])

            next_result = run_cli(manifest_path, state_path, hermes_home, "next")
            next_payload = self.assert_success_payload(next_result)
            self.assertEqual(next_payload["next"], validate_payload["next"])
            self.assertEqual(state_path.read_bytes(), state_before)

            expected_signer = signer_path_for(hermes_home, state_path)
            expected_lock = lock_path_for(hermes_home, state_path)
            self.assertEqual(
                list((hermes_home / SIGNER_RELATIVE_DIRECTORY).iterdir()),
                [expected_signer],
            )
            self.assertEqual(
                list((hermes_home / LOCK_RELATIVE_DIRECTORY).iterdir()),
                [expected_lock],
            )
            self.assertNotIn(expected_signer, [workspace, *workspace.parents])
            self.assertNotIn(workspace, [expected_signer, *expected_signer.parents])
            key = expected_signer.read_bytes()
            self.assertEqual(len(key), 32)
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(expected_signer.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(expected_lock.stat().st_mode), 0o600)

            for result in (validate_result, next_result):
                self.assertNotIn(key, result.stdout)
                self.assertNotIn(key, result.stderr)
            self.assertNotIn(key, state_path.read_bytes())
            self.assertNotIn(key, manifest_path.read_bytes())

            git_files = subprocess.run(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                cwd=workspace,
                check=True,
                capture_output=True,
            ).stdout
            self.assertNotIn(expected_signer.name.encode("ascii"), git_files)
            self.assertNotIn(key, git_files)

    def test_transition_is_disabled_for_every_role_action_and_strict_mode(self) -> None:
        actions = (
            "start_slice",
            "record_failure",
            "request_review",
            "request_changes",
            "pass_review",
            "complete_slice",
            "block",
            "replan",
            "start_milestone",
            "validate_milestone",
            "complete_milestone",
        )
        roles = (None, "builder", "integrator")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, manifest_path, state_path, hermes_home = self.create_fixture(root)
            initialized = run_cli(
                manifest_path, state_path, hermes_home, "validate"
            )
            self.assert_success_payload(initialized)
            state_before = state_path.read_bytes()
            signer_path = signer_path_for(hermes_home, state_path)
            signer_before = signer_path.read_bytes()
            missing_evidence = root / "evidence-must-not-be-read.json"

            for strict in (None, "1"):
                for role in roles:
                    for action in actions:
                        environment = {}
                        if role is not None:
                            environment["HERMES_FACTORY_ROLE"] = role
                        if strict is not None:
                            environment["HERMES_FACTORY_STRICT"] = strict
                        entity_id = "M1" if "milestone" in action else "M1-S1"
                        with self.subTest(role=role, strict=strict, action=action):
                            result = run_cli(
                                manifest_path,
                                state_path,
                                hermes_home,
                                "transition",
                                entity_id,
                                action,
                                "--evidence",
                                str(missing_evidence),
                                environment_updates=environment,
                            )
                            self.assert_transition_failure(result)
                            self.assertEqual(state_path.read_bytes(), state_before)
                            self.assertEqual(signer_path.read_bytes(), signer_before)
                            persisted = json.loads(state_path.read_bytes())
                            self.assertEqual(persisted["revision"], 0)
                            self.assertEqual(
                                persisted["milestones"]["M1"]["status"], "pending"
                            )
                            self.assertEqual(
                                persisted["slices"]["M1-S1"]["status"], "pending"
                            )
                            self.assertFalse(missing_evidence.exists())
                            self.assertNotIn(signer_before, result.stdout)
                            self.assertNotIn(signer_before, result.stderr)

            missing_manifest = root / "manifest-must-not-be-read.json"
            result = run_cli(
                missing_manifest,
                state_path,
                hermes_home,
                "transition",
                "M1",
                "start_milestone",
                "--evidence",
                str(missing_evidence),
            )
            self.assert_transition_failure(result)
            self.assertEqual(state_path.read_bytes(), state_before)
            self.assertEqual(signer_path.read_bytes(), signer_before)
            self.assertFalse(missing_manifest.exists())
            self.assertFalse(missing_evidence.exists())

    def test_deleted_state_makes_next_and_validate_fail_without_reset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, manifest_path, state_path, hermes_home = self.create_fixture(root)
            created = run_cli(manifest_path, state_path, hermes_home, "validate")
            self.assert_success_payload(created)
            signer_path = signer_path_for(hermes_home, state_path)
            key = signer_path.read_bytes()
            state_path.unlink()

            for command in ("next", "validate"):
                with self.subTest(command=command):
                    result = run_cli(
                        manifest_path, state_path, hermes_home, command
                    )
                    self.assert_signer_failure(result)
                    self.assertFalse(state_path.exists())
                    self.assertEqual(signer_path.read_bytes(), key)
                    self.assertNotIn(key, result.stdout)
                    self.assertNotIn(key, result.stderr)

    def test_missing_signer_fails_closed_without_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, manifest_path, state_path, hermes_home = self.create_fixture(root)
            created = run_cli(manifest_path, state_path, hermes_home, "validate")
            self.assert_success_payload(created)
            signer_path = signer_path_for(hermes_home, state_path)
            key = signer_path.read_bytes()
            state_before = state_path.read_bytes()
            signer_path.unlink()

            for command in ("next", "validate"):
                with self.subTest(command=command):
                    result = run_cli(
                        manifest_path, state_path, hermes_home, command
                    )
                    self.assert_signer_failure(result)
                    self.assertFalse(signer_path.exists())
                    self.assertEqual(state_path.read_bytes(), state_before)
                    self.assertNotIn(key, result.stdout)
                    self.assertNotIn(key, result.stderr)

    def test_corrupt_signer_fails_closed_without_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, manifest_path, state_path, hermes_home = self.create_fixture(root)
            created = run_cli(manifest_path, state_path, hermes_home, "validate")
            self.assert_success_payload(created)
            signer_path = signer_path_for(hermes_home, state_path)
            original_key = signer_path.read_bytes()
            state_before = state_path.read_bytes()
            corrupt_key = b"corrupt offline signer"
            signer_path.write_bytes(corrupt_key)

            for command in ("next", "validate"):
                with self.subTest(command=command):
                    result = run_cli(
                        manifest_path, state_path, hermes_home, command
                    )
                    self.assert_signer_failure(result)
                    self.assertEqual(signer_path.read_bytes(), corrupt_key)
                    self.assertEqual(state_path.read_bytes(), state_before)
                    self.assertNotIn(original_key, result.stdout)
                    self.assertNotIn(original_key, result.stderr)
                    self.assertNotIn(corrupt_key, result.stdout)
                    self.assertNotIn(corrupt_key, result.stderr)


if __name__ == "__main__":
    unittest.main()
