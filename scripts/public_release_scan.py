#!/usr/bin/env python3
"""Public-release hygiene scan; reports locations and pattern classes, never values."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {".git", ".hermes", "__pycache__", ".venv", "venv", ".mypy_cache", ".ruff_cache"}
PATTERNS = {
    "private-key-block": re.compile(r"-----BEGIN(?: [A-Z]+)* PRIVATE KEY-----"),
    "provider-token": re.compile(r"(?i)(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{16,})"),
    "bearer-token": re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    "credential-url": re.compile(r"(?i)[a-z][a-z0-9+.-]{1,31}://[^\s/@:]+:[^\s/@]+@"),
    "machine-path": re.compile(r"(?:/home/[a-z0-9._-]+|/mnt/[a-z]/|[A-Za-z]:\\\\Users\\\\)"),
    "private-session-link": re.compile(r"@session:[^\s)]+"),
    "private-network-address": re.compile(r"(?<!\d)(?:72\.60\.194\.114|156\.67\.216\.68|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+)(?!\d)"),
}


def files() -> list[Path]:
    raw = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        text=True,
    )
    return [
        ROOT / rel
        for rel in sorted(set(raw.splitlines()))
        if rel and rel != "scripts/public_release_scan.py" and not any(part in SKIP_PARTS for part in Path(rel).parts)
    ]


def main() -> int:
    findings: list[tuple[str, int, str]] = []
    candidate_files = files()
    for path in candidate_files:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = path.relative_to(ROOT).as_posix()
        for line_no, line in enumerate(text.splitlines(), 1):
            for label, pattern in PATTERNS.items():
                if pattern.search(line):
                    findings.append((rel, line_no, label))
    print(f"files_scanned={len(candidate_files)}")
    print(f"findings={len(findings)}")
    for rel, line_no, label in findings:
        print(f"{rel}:{line_no}:{label}")
    # Test fixtures intentionally contain redaction probes; they are findings for
    # review but not live credentials. Never print their values.
    non_test = [item for item in findings if not item[0].startswith("tests/")]
    return 1 if non_test else 0


if __name__ == "__main__":
    sys.exit(main())
