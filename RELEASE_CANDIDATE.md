# Release-candidate status

This repository is a technical release candidate for Hermes Dark Factory v0.4.0.

## Verified on the candidate tree

- `python3 -m unittest discover -s tests -v` — **225 tests passed**.
- Python compilation and dashboard/desktop JavaScript syntax checks passed.
- All repository JSON fixtures parsed successfully.
- `python3 scripts/public_release_scan.py` scanned 40 files and reported only the 12 intentionally credential-shaped test-fixture findings; it exited successfully with no non-test findings.
- The candidate contains an explicit [MIT LICENSE](LICENSE).
- `hermes plugins doctor ./plugin --ci` passed with 10 tools and 1 hook.
- The current source plugin security scan returned `safe`, with 0 findings, and the install decision was allowed.
- A real disposable Beads v1.2.2 adapter smoke initialized a project-local `.beads` store, dry-ran 4 nodes/1 edge, applied 4 nodes with read-back verification, wrote a receipt, and replayed idempotently without duplicates.
- The public tree contains no non-test machine paths, private session links, credential URLs, bearer tokens, or private-key material.
- The dashboard bundle is intentionally vendored under `plugin/dashboard/dist/`; this repository validates its syntax but does not contain the upstream dashboard build source or lockfile. Hermes supplies the desktop host SDK/React runtime.
- The desktop plugin contributes a Projects overview, project workspace, global defaults page, and project-aware intake route. Project identity remains Hermes-native; factory progress/logs are derived from the manifest/state pair and bounded workspace artifacts.

## Required fresh-Hermes setup

Beads CLI v1.2.2 is a separate required runtime dependency. The Dark Factory plugin does not install `bd` and does not run `bd init`. Install/verify `bd` in the Hermes runtime environment, initialize each target project with a project-local `bd init`, then install and enable the plugin. The exact commands are in [README.md](README.md) and the packaged [plugin/README.md](plugin/README.md).

The repository does not currently run a real Beads CLI smoke in dependency-only CI; exercise Beads CLI v1.2.2 separately in an isolated temporary project before enabling graph writes. Beads is the required coordination dependency; Hermes Kanban is intentionally not used. Legacy `local`, `kanban`, and `both` persisted settings migrate to Beads, while new writes reject those modes. Browser smoke requires a Hermes dashboard host and is not run on the dependency-only CI job.

## Before public distribution

1. Rerun the release gates after any further source or metadata change.
2. Push only the clean public `master` branch. Do not push the local `private-history` backup branch or use `git push --all`.
3. Create and publish an archive only after the final human-visible release decision is recorded.

The repository is intentionally a bounded dogfood prototype. It must not be used as an unattended production deployer, spend authority, or external communications agent without an explicit human gate.
