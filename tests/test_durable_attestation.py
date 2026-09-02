from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DurableAttestationKeyTests(unittest.TestCase):
    def test_attestation_key_survives_process_restart_per_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                **os.environ,
                "HERMES_HOME": tmp,
                "PYTHONPATH": str(ROOT),
            }
            command = [
                sys.executable,
                "-c",
                "from plugin.engine import _PROCESS_REVIEW_KEY; print(_PROCESS_REVIEW_KEY.hex())",
            ]
            first = subprocess.run(command, cwd=ROOT, env=env, check=True, capture_output=True, text=True)
            second = subprocess.run(command, cwd=ROOT, env=env, check=True, capture_output=True, text=True)

            self.assertEqual(first.stdout.strip(), second.stdout.strip())
            key_path = Path(tmp) / "plugin-data" / "dark-factory" / "review-attestation.key"
            self.assertTrue(key_path.is_file())
            self.assertEqual(key_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(len(key_path.read_bytes()), 32)


if __name__ == "__main__":
    unittest.main()
