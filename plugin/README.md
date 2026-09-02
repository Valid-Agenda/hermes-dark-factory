# Hermes Dark Factory plugin

This directory is the installable `dark-factory` plugin package for Hermes Agent. It provides the Beads-backed tools, dashboard API, native desktop workspace, and namespaced chat skill.

## Fresh Hermes setup

Dark Factory has one required coordination backend: **Beads CLI v1.2.2**. Installing this plugin does **not** install the Beads CLI and does **not** run `bd init`. Install and verify Beads first, then install this directory as a Hermes plugin.

### 1. Install Beads in the same environment as Hermes

For WSL/Linux/macOS with Node.js/npm:

```bash
npm install -g @beads/bd@1.2.2
command -v bd
bd --version
```

The final command must report Beads `1.2.2`. Run these commands inside the same WSL distribution and user environment that runs Hermes. If `bd` is installed in a user bin directory that is not on the non-interactive Hermes `PATH`, add that directory to the environment used to launch Hermes and verify again.

The official Beads project documents other supported installation methods: [Beads installation](https://github.com/gastownhall/beads/blob/main/docs/getting-started/installation.md). If you use an unpinned installer, still verify that the installed executable reports `1.2.2` before using this release.

### 2. Initialize Beads per project/workspace

The normal `bd init` scope is the current project: it creates a project-local `.beads/` directory. Repeat this for every Dark Factory workspace that should have an independent graph:

```bash
cd /absolute/path/to/your/project
bd init
test -d .beads
```

Do not use `bd init --global` or `bd init --shared-server` for the normal Dark Factory setup. Those are explicit shared-database modes; they are not the default project isolation model. Dark Factory preflight may inspect a store, but it never initializes one implicitly.

### 3. Install the plugin

From a fresh Hermes profile, install the plugin subdirectory from GitHub:

```bash
hermes plugins install Valid-Agenda/hermes-dark-factory/plugin --enable
```

For a reproducible test, pin the commit after it is published:

```bash
hermes plugins install Valid-Agenda/hermes-dark-factory/plugin \
  --ref <40-character-commit-sha> \
  --enable
```

Hermes scans plugins before installation. Review the result and install only from a trusted source. Start a new Hermes process/session after installation so plugin discovery is refreshed.

### 4. Verify the installed plugin

```bash
hermes plugins show dark-factory
hermes plugins capabilities dark-factory
hermes plugins doctor "$HOME/.hermes/plugins/dark-factory" --ci
```

If `bd` works in an interactive terminal but Dark Factory reports it unavailable, compare the `PATH` of the process launching Hermes with the shell where `command -v bd` succeeds. Do not work around this by adding a second coordination backend: fix the environment or configure the Beads executable path through the supported project settings.

### 5. Use Dark Factory from chat or desktop

Chat does not require opening the desktop UI:

```text
/skill dark-factory:dark-factory
/skill dark-factory:dark-factory import /absolute/path/to/manifest.json
/skill dark-factory:dark-factory preflight
```

The skill drives the guarded preflight, compile/import, Beads plan, evidence, independent review, and authorized graph-apply flow. Manifest import creates only a pristine `manifest.json`/`state.json` pair; it does not apply a Beads graph.

The optional desktop surface is available under **Dark Factory** after enabling the plugin. It shows project-local Beads readiness and can link to [Bead Me Up Scotty](https://github.com/brendan-appstart/bead-me-up-scotty), an optional visual viewer over the same Beads store.

## Runtime contract

- Beads CLI `1.2.2` is required for compilation and graph writes.
- Each target workspace must have an initialized, readable project-local `.beads/` store.
- Explicit isolated-write authorization is required before `factory_beads_apply`.
- Hermes Kanban, local coordination, and mirrored backends are intentionally not supported.
- Credentials, API keys, OAuth tokens, passwords, and connection strings must remain in Hermes/provider auth storage; they are never placed in manifests, state, or Beads cards.
- The plugin fails closed when Beads, project readiness, model availability, authorization, or evidence gates are missing.

See the repository [README](https://github.com/Valid-Agenda/hermes-dark-factory/blob/master/README.md), [LICENSE](https://github.com/Valid-Agenda/hermes-dark-factory/blob/master/LICENSE), and bundled [Dark Factory skill](skills/dark-factory/SKILL.md) for the full operating contract.
