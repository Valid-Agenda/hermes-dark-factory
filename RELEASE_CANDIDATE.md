# Release-candidate status

This repository is a technical release candidate for Hermes Dark Factory v0.4.0.

## Verified on the candidate tree

- `python3 -m unittest discover -s tests -v` — **225 tests passed**.
- Python compilation and dashboard/desktop JavaScript syntax checks passed.
- All repository JSON fixtures parsed successfully.
- `python3 scripts/public_release_scan.py` scanned 40 files and reported only the 12 intentionally credential-shaped test-fixture findings; it exited successfully with no non-test findings.
- The candidate contains an explicit [MIT LICENSE](LICENSE).
- `hermes plugins doctor ./plugin --ci` passed with 10 tools and 1 hook.
- The current source plugin security scan returned `safe`, with 11 non-blocking code findings (subprocess/persistence patterns) and no dangerous or credential-exfiltration finding; the install decision was allowed.
- A real disposable Beads v1.2.2 adapter smoke initialized a project-local `.beads` store, dry-ran 4 nodes/1 edge, applied 4 nodes with read-back verification, wrote a receipt, and replayed idempotently without duplicates.
- The public tree contains no non-test machine paths, private session links, credential URLs, bearer tokens, or private-key material.
- The dashboard bundle is intentionally vendored under `plugin/dashboard/dist/`; this repository validates its syntax but does not contain the upstream dashboard build source or lockfile. Hermes supplies the desktop host SDK/React runtime.
- The desktop plugin contributes a Projects overview, project workspace, global defaults page, and project-aware intake route. Project identity remains Hermes-native; factory progress/logs are derived from the manifest/state pair and bounded workspace artifacts.

## Verified fresh-Hermes deployment

Beads CLI v1.2.2 is a separate required runtime dependency. The Dark Factory plugin does not install `bd` and does not run `bd init`. The exact operator sequence is documented in [README.md](README.md) and the packaged [plugin/README.md](plugin/README.md): install/verify `bd` in the Hermes runtime environment, initialize each target project with project-local `bd init`, then install and enable the plugin.

The exact public GitHub install was exercised in an isolated fresh Hermes home. The copied package reported plugin v0.4.0 with manifest v1, contained the packaged setup guide, matched **14/14 tracked plugin files**, passed Plugin Doctor with 10 tools and 1 hook, and registered the plugin skill for the `dark-factory:dark-factory` namespace. A real Beads v1.2.2 disposable-workspace smoke also passed: project-local initialization, 4-node/1-edge dry-run, 4-node apply with read-back, receipt creation, and exact idempotent replay.

CI intentionally does not install the external Beads CLI; the dependency-only workflow validates the rest of the package. The public workflow run `33580801246` passed on Python 3.11, 3.12, and 3.13 plus dashboard/desktop syntax. GitHub reports only upstream Node 20 deprecation annotations for the pinned actions; they did not fail the run. Browser smoke still requires a Hermes dashboard host and is not part of this dependency-only CI job. Beads remains the required coordination dependency; Hermes Kanban is intentionally not used.

## Publication record

- Clean public `master` was pushed to `https://github.com/Valid-Agenda/hermes-dark-factory.git`.
- The local `private-history` branch was not pushed, and `git push --all` was not used.
- The repository includes an explicit [MIT LICENSE](LICENSE).

The repository is intentionally a bounded dogfood prototype. It must not be used as an unattended production deployer, spend authority, or external communications agent without an explicit human gate.
