# Release-candidate status

This repository is a technical release candidate for Hermes Dark Factory v0.3.0.

## Verified on the candidate tree

- `python3 -m unittest discover -s tests -v` — 213 tests passed after installing `requirements-dev.txt`.
- Python compilation and dashboard/desktop JavaScript syntax checks passed.
- All repository JSON fixtures parsed successfully.
- `hermes plugins doctor ./plugin --ci` passed with 9 tools and 1 hook.
- The plugin security scan returned `SAFE` and `ALLOWED`.
- The installed dashboard setup/model-options HTTP smoke passed without credential fields.
- A real loopback browser smoke rendered `/dark-factory`, navigated the setup steps, saved a draft, and reported no console errors.
- The public tree contains no non-test machine paths, private session links, credential URLs, bearer tokens, or private-key material.

The dashboard bundle is intentionally vendored under `plugin/dashboard/dist/`; this repository validates its syntax but does not contain the upstream dashboard build source or lockfile. Hermes supplies the desktop host SDK/React runtime.

The repository does not currently run a real Beads CLI smoke in CI because the optional `bd` executable is not bundled; exercise Beads CLI v1.2.2 separately before enabling graph writes. The browser smoke requires a Hermes dashboard host and is intentionally not run on the dependency-only CI job.

## Before public distribution

1. Add the maintainer-selected `LICENSE` file. This candidate intentionally grants no reuse rights yet.
2. Rerun the release gates after adding the license.
3. Push only the clean public `master` branch. Do not push the local `private-history` backup branch or use `git push --all`.
4. Create and publish an archive only after the license and the final human-visible release decision are recorded.

The repository is intentionally a bounded dogfood prototype. It must not be used as an unattended production deployer, spend authority, or external communications agent without an explicit human gate.
