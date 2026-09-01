from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

from plugin.intake import normalise_setup

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "plugin" / "dashboard"
DASHBOARD_BUNDLE = DASHBOARD / "dist" / "index.js"
DESKTOP = ROOT / "plugin" / "desktop" / "plugin.js"
ROLES = ("integrator", "builder", "verifier", "adversary", "holdout")
THREAT_FIELDS = ("id", "name", "scenario", "attack_surface", "expected_control")
PRESET_MODELS = (
    "openai-codex/gpt-5.6-sol-900k",
    "openai-codex/gpt-5.6-luna",
)
GRAPH_COPY = (
    "Mission → milestone epics → functional-slice tasks",
    "No micro-beads for test fixes or review comments",
    "Beads owns the work graph while the Dark Factory ledger owns acceptance and evidence",
)


def assert_no_credential_fields(test: unittest.TestCase, source: str) -> None:
    test.assertIsNone(
        re.search(
            r'''(?ix)(?:type|name|id|label)\s*[:=]\s*["'](?:password|token|api[_ -]?key|secret|credential)["']''',
            source,
        )
    )
    test.assertNotIn("localStorage", source)
    test.assertNotIn("sessionStorage", source)


def _run_node(script: str, bundle: Path, payload: dict) -> dict:
    completed = subprocess.run(
        ["node", "-e", script, str(bundle)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def dashboard_round_trip(payload: dict) -> dict:
    script = r"""
const fs = require('fs');
const vm = require('vm');
const bundlePath = process.argv[1];
let source = fs.readFileSync(bundlePath, 'utf8');
source = source.replace(
  'registry.register("dark-factory", DarkFactoryPage);',
  'globalThis.__darkFactoryContract = { normaliseSetup: normaliseSetup, toApiSetup: toApiSetup };'
);
const sandbox = {
  console,
  window: {
    __HERMES_PLUGIN_SDK__: {
      React: { createElement: function () { return null; } },
      hooks: {
        useState: function () {}, useEffect: function () {},
        useCallback: function () {}, useMemo: function () {}
      },
      components: {},
      fetchJSON: function () {}
    },
    __HERMES_PLUGINS__: { register: function () {} }
  }
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: bundlePath });
if (!sandbox.__darkFactoryContract) throw new Error('dashboard contract functions were not exposed');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const normalised = sandbox.__darkFactoryContract.normaliseSetup(input, { providers: [] });
const saved = sandbox.__darkFactoryContract.toApiSetup(normalised);
const compiled = sandbox.__darkFactoryContract.toApiSetup(normalised);
process.stdout.write(JSON.stringify({ normalised, saved, compiled }));
"""
    return _run_node(script, DASHBOARD_BUNDLE, payload)


def dashboard_inventory_probe(payload: dict) -> dict:
    script = r"""
const fs = require('fs');
const vm = require('vm');
const bundlePath = process.argv[1];
let source = fs.readFileSync(bundlePath, 'utf8');
source = source.replace(
  'registry.register("dark-factory", DarkFactoryPage);',
  'globalThis.__darkFactoryInventory = { normaliseSetup, authenticatedModelRefs, applySolLunaPreset, ModelsSection };'
);
const components = new Proxy({}, { get: function (_target, key) { return String(key); } });
const sandbox = {
  console,
  window: {
    __HERMES_PLUGIN_SDK__: {
      React: {
        createElement: function (type, props) {
          return {
            type: type,
            props: props || {},
            children: Array.prototype.slice.call(arguments, 2)
          };
        }
      },
      hooks: {
        useState: function () {}, useEffect: function () {},
        useCallback: function () {}, useMemo: function () {}
      },
      components: components,
      fetchJSON: function () {}
    },
    __HERMES_PLUGINS__: { register: function () {} }
  }
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: bundlePath });
const contract = sandbox.__darkFactoryInventory;
if (!contract) throw new Error('dashboard inventory functions were not exposed');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const providers = input.providers;
const normalised = contract.normaliseSetup(input.setup || {}, { providers: providers });
const applied = contract.applySolLunaPreset(normalised.models, providers);
const tree = contract.ModelsSection({
  providers: providers,
  setup: normalised,
  update: function () {}
});
function nodeText(node) {
  if (typeof node === 'string') return node;
  if (!node || typeof node !== 'object') return '';
  if (Array.isArray(node)) return node.map(nodeText).join('');
  return nodeText(node.children || []);
}
function findPresetButton(node) {
  if (!node || typeof node !== 'object') return null;
  if (Array.isArray(node)) {
    for (const item of node) {
      const found = findPresetButton(item);
      if (found) return found;
    }
    return null;
  }
  if (node.type === 'Button' && nodeText(node) === 'Apply Sol orchestrator + Luna worker') return node;
  return findPresetButton(node.children || []);
}
const presetButton = findPresetButton(tree);
if (!presetButton) throw new Error('Sol/Luna preset button was not rendered');
process.stdout.write(JSON.stringify({
  refs: contract.authenticatedModelRefs(providers),
  normalisedModels: normalised.models,
  appliedModels: applied,
  presetDisabled: presetButton.props.disabled === true,
  roles: Object.keys(normalised.models).sort()
}));
"""
    return _run_node(script, DASHBOARD_BUNDLE, payload)


def desktop_round_trip(payload: dict) -> dict:
    script = r"""
const fs = require('fs');
const vm = require('vm');
const pluginPath = process.argv[1];
let source = fs.readFileSync(pluginPath, 'utf8');
source = source.replace(/^import[\s\S]*?from\s+['"][^'"]+['"]\s*;?\s*$/gm, '');
source = source.replace(
  /\nexport default \{[\s\S]*$/,
  '\nglobalThis.__darkFactoryContract = { normaliseSetup, serialiseSetup };\n'
);
const sandbox = { console };
vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: pluginPath });
if (!sandbox.__darkFactoryContract) throw new Error('desktop contract functions were not exposed');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const normalised = sandbox.__darkFactoryContract.normaliseSetup(input, { providers: [] });
const saved = sandbox.__darkFactoryContract.serialiseSetup(normalised, { providers: [] });
const compiled = sandbox.__darkFactoryContract.serialiseSetup(normalised, { providers: [] });
process.stdout.write(JSON.stringify({ normalised, saved, compiled }));
"""
    return _run_node(script, DESKTOP, payload)


def desktop_inventory_probe(payload: dict) -> dict:
    script = r"""
const fs = require('fs');
const vm = require('vm');
const pluginPath = process.argv[1];
let source = fs.readFileSync(pluginPath, 'utf8');
source = source.replace(/^import[\s\S]*?from\s+['"][^'"]+['"]\s*;?\s*$/gm, '');
source = source.replace(
  /\nexport default \{[\s\S]*$/,
  '\nglobalThis.__darkFactoryInventory = { normaliseCatalog, catalogModelRefs, modelRefAvailable, applySolLunaPreset, normaliseSetup, serialiseSetup };\n'
);
const sandbox = { console };
vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: pluginPath });
const contract = sandbox.__darkFactoryInventory;
if (!contract) throw new Error('desktop inventory functions were not exposed');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const rawCatalog = input.catalog || {};
const catalog = contract.normaliseCatalog(rawCatalog);
const normalised = contract.normaliseSetup(input.setup || {}, catalog);
const appliedModels = contract.applySolLunaPreset(normalised.models, catalog);
const saved = contract.serialiseSetup(normalised, catalog);
const compiled = contract.serialiseSetup(normalised, catalog);
process.stdout.write(JSON.stringify({
  catalog,
  rawRefs: contract.catalogModelRefs(rawCatalog),
  refs: contract.catalogModelRefs(catalog),
  presetAvailable: {
    sol: contract.modelRefAvailable(catalog, { provider: 'openai-codex', model: 'gpt-5.6-sol-900k' }),
    luna: contract.modelRefAvailable(catalog, { provider: 'openai-codex', model: 'gpt-5.6-luna' })
  },
  normalisedModels: normalised.models,
  appliedModels,
  savedModels: saved.models,
  compiledModels: compiled.models,
  roles: Object.keys(normalised.models).sort()
}));
"""
    return _run_node(script, DESKTOP, payload)


def canonical_payload() -> dict:
    return {
        "intake_schema_version": 1,
        "project_mode": "existing",
        "workspace_path": "/tmp/dark-factory",
        "product": {
            "name": "Cross-client factory",
            "problem": "Owners need durable access without cross-owner disclosure.",
            "outcome": "Owners can resume work while unauthorized requests remain denied.",
            "context": "Identity is server-authoritative and project content is owner scoped.",
            "existing_system": "The existing service exposes project detail and session APIs.",
            "success_metrics": ["Every owner journey has a raw acceptance receipt"],
            "surfaces": ["web UI", "desktop UI"],
        },
        "context": {
            "include": ["src/auth/**"],
            "exclude": ["secrets/**"],
            "max_files": 120,
        },
        "personas": [
            {
                "id": "persona/owner",
                "name": "Project owner",
                "context": "Works across browser and desktop sessions",
                "need": "Resume only projects they own",
            }
        ],
        "user_stories": [
            {
                "id": "story/session",
                "persona_id": "persona/owner",
                "want": "resume a durable session",
                "so_that": "work remains available without weakening ownership",
                "acceptance": [
                    {"id": "story/session/recovery", "type": "recovery", "statement": " Retry preserves the original request "},
                    {"id": "story/session/happy", "type": "happy", "statement": "The owner resumes the session"},
                    {"id": "story/session/boundary", "type": "boundary", "statement": "Expiry is enforced at the boundary"},
                    {"id": "story/session/abuse", "type": "abuse", "statement": "A forged owner is denied"},
                    {"id": "story/session/negative", "type": "negative", "statement": "A revoked session is denied"},
                ],
                "paths": ["src/auth/session.js"],
            }
        ],
        "non_goals": ["Changing the identity authority"],
        "constraints": ["No credentials in setup payloads"],
        "milestones": [
            {
                "id": "milestone/auth",
                "title": "Durable authentication",
                "outcome": "Authentication remains durable and owner scoped.",
                "story_ids": ["story/session"],
                "acceptance": [
                    {"id": "milestone/auth/recovery", "type": "recovery", "statement": "An interrupted login can resume"},
                    {"id": "milestone/auth/boundary", "type": "boundary", "statement": "Expiry remains enforced"},
                    {"id": "milestone/auth/abuse", "type": "abuse", "statement": "Replay is rejected"},
                ],
                "evidence": ["pytest tests/auth -q"],
            }
        ],
        "testing": {
            "focused_commands": ["pytest tests/auth/unit -q"],
            "integration_commands": ["pytest tests/auth/integration -q"],
            "browser_scenarios": [
                {
                    "name": "Owner resumes the durable session",
                    "action": "Reload after authentication",
                    "expected": "The same owner session is restored",
                }
            ],
            "held_out_scenarios": [
                {
                    "name": "Revoked session cannot recover authority",
                    "given": "A previously valid session is revoked",
                    "when": "The owner retries the protected request",
                    "then": "The request is denied without disclosing content",
                }
            ],
            "evidence_requirements": ["raw receipt"],
        },
        "security": {
            "data_classification": "internal",
            "adversarial_lens": "kryptonite",
            "risk_triggers": ["authorization"],
            "data": ["Project content is owner scoped"],
            "controls": ["Server-side ownership on every read"],
            "human_gates": ["Production deployment requires approval"],
            "threat_scenarios": [
                {
                    "id": "threat/cross-owner-read",
                    "name": "Cross-owner project disclosure",
                    "scenario": "An authenticated user guesses another owner's project identifier and requests its content.",
                    "attack_surface": "Project detail API",
                    "expected_control": "Server-side ownership rejects the request and records deterministic negative evidence.",
                }
            ],
            "authority_decisions": [
                {
                    "id": "decision/identity",
                    "statement": "Server-side identity is the only ownership authority.",
                    "status": "locked",
                    "rationale": "Client-supplied owner identifiers are ignored.",
                }
            ],
        },
        "models": {
            "integrator": {"provider": "openai-codex", "model": "gpt-5.6-sol-900k"},
            "builder": {"provider": "openai-codex", "model": "gpt-5.6-luna"},
            "verifier": {"provider": "review", "model": "verifier-v1"},
            "adversary": {"provider": "review", "model": "adversary-v1"},
            "holdout": {"provider": "review", "model": "holdout-v1"},
        },
        "model_policy": {"preset": "sol-luna"},
        "execution": {
            "graph_backend": "beads",
            "graph_mode": "apply",
            "beads_directory": "/tmp/canonical-beads",
            "beads_isolated_authorized": True,
            "reasoning_effort": {"orchestrator": "high", "worker": "medium"},
        },
        "policy": {
            "max_active_milestones": 1,
            "max_parallel_slices": 2,
            "repeated_failure_limit": 2,
            "max_remediation_cycles": 1,
        },
    }


class BundleContractMixin:
    round_trip = staticmethod(lambda _payload: {})

    def assert_backend_accepts(self, setup: dict) -> dict:
        try:
            return normalise_setup(setup)
        except Exception as cause:  # pragma: no cover - assertion includes backend detail
            self.fail(f"backend normalise_setup rejected client output: {cause}")

    def assert_exact_threat(self, setup: dict, expected: dict) -> None:
        threat = setup["security"]["threat_scenarios"][0]
        self.assertEqual(tuple(threat), THREAT_FIELDS)
        self.assertEqual(threat, expected)

    def test_empty_and_raw_payloads_normalise_then_serialize_without_schema_errors(self) -> None:
        raw = {
            "model_policy": {
                "preset": "sol-luna",
                "orchestrator_role": "integrator",
                "worker_roles": ["builder"],
            },
            "execution": {
                "graph_backend": "beads",
                "desktop_authored": True,
                "reasoning_effort": {"orchestrator": "high", "worker": "medium", "review": "high"},
            },
            "security": {
                "threat_scenarios": [
                    {
                        "severity": "critical",
                        "threat": "Legacy thin threat",
                        "description": "Legacy description",
                        "mitigation": "Legacy mitigation",
                    }
                ]
            },
        }
        for label, payload in (("empty", {}), ("raw", raw)):
            with self.subTest(payload=label):
                result = self.round_trip(payload)
                self.assertEqual(result["normalised"]["model_policy"], {"preset": "sol-luna"})
                for stage in ("saved", "compiled"):
                    setup = result[stage]
                    self.assertEqual(setup["model_policy"], {"preset": "sol-luna"})
                    self.assertEqual(
                        setup["execution"]["reasoning_effort"],
                        {"orchestrator": "high", "worker": "medium"},
                    )
                    self.assert_backend_accepts(setup)
                if label == "raw":
                    expected = {
                        "id": "T1",
                        "name": "",
                        "scenario": "",
                        "attack_surface": "",
                        "expected_control": "",
                    }
                    self.assert_exact_threat(result["saved"], expected)
                    self.assert_exact_threat(result["compiled"], expected)

    def test_canonical_cross_client_payload_survives_normalise_save_and_compile(self) -> None:
        payload = canonical_payload()
        expected_threat = payload["security"]["threat_scenarios"][0]
        expected_story_acceptance = payload["user_stories"][0]["acceptance"]
        expected_milestone_acceptance = payload["milestones"][0]["acceptance"]
        expected_browser = payload["testing"]["browser_scenarios"]
        expected_held_out = payload["testing"]["held_out_scenarios"]

        result = self.round_trip(payload)

        for stage in ("saved", "compiled"):
            with self.subTest(stage=stage):
                setup = result[stage]
                self.assertEqual(setup["user_stories"][0]["acceptance"], expected_story_acceptance)
                self.assertEqual(setup["milestones"][0]["acceptance"], expected_milestone_acceptance)
                self.assertEqual(setup["testing"]["browser_scenarios"], expected_browser)
                self.assertEqual(setup["testing"]["held_out_scenarios"], expected_held_out)
                self.assertEqual(setup["models"], payload["models"])
                self.assertEqual(setup["model_policy"], {"preset": "sol-luna"})
                self.assertEqual(setup["execution"], payload["execution"])
                self.assertEqual(setup["product"]["context"], payload["product"]["context"])
                self.assertEqual(setup["context"], payload["context"])
                self.assert_exact_threat(setup, expected_threat)
                backend_setup = self.assert_backend_accepts(setup)
                backend_threat = backend_setup["security"]["threat_scenarios"][0]
                self.assertEqual(
                    {field: backend_threat[field] for field in THREAT_FIELDS},
                    expected_threat,
                )


class DashboardBundleContractTests(BundleContractMixin, unittest.TestCase):
    round_trip = staticmethod(dashboard_round_trip)

    def test_manifest_exposes_dark_factory_page_and_backend(self) -> None:
        manifest = json.loads((DASHBOARD / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "dark-factory")
        self.assertEqual(manifest["tab"]["path"], "/dark-factory")
        self.assertEqual(manifest["api"], "plugin_api.py")
        self.assertTrue((DASHBOARD / manifest["entry"]).is_file())
        self.assertTrue((DASHBOARD / manifest["css"]).is_file())

    def test_dashboard_bundle_has_guided_contract_and_real_serializers(self) -> None:
        source = DASHBOARD_BUNDLE.read_text(encoding="utf-8")
        self.assertIn('registry.register("dark-factory"', source)
        for endpoint in ('API + "/setup"', 'API + "/model-options"', 'API + "/compile"'):
            self.assertIn(endpoint, source)
        self.assertEqual(source.count("JSON.stringify(toApiSetup(setup))"), 2)
        self.assertNotIn("orchestrator_role", source)
        self.assertNotIn("worker_roles", source)
        for label in ("Name", "Scenario", "Attack surface", "Expected control"):
            self.assertIn(f'label: "{label}"', source)
        for field in THREAT_FIELDS:
            self.assertIn(field, source)
        for marker in (
            "Orchestrator / Integrator",
            "Worker / Builder",
            "Apply Sol orchestrator + Luna worker",
            "The preset covers execution roles only",
            "Preferred preset model unavailable",
            'model_policy: { preset: SOL_LUNA_PRESET }',
            'graph_backend: "beads"',
            'graph_mode: "plan"',
            'beads_directory: ""',
            'beads_isolated_authorized: false',
            'reasoning_effort: { orchestrator: "high", worker: "medium" }',
        ) + PRESET_MODELS + GRAPH_COPY:
            self.assertIn(marker, source)

    def test_dashboard_contains_no_credential_fields(self) -> None:
        source = DASHBOARD_BUNDLE.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"(?i)((?<![a-z0-9])sk-[a-z0-9_-]{16,}|bearer\s+[a-z0-9._-]{16,})", source))
        assert_no_credential_fields(self, source)

    def test_actual_bundle_requires_boolean_auth_and_string_models_for_sol_luna(self) -> None:
        review_models = {
            "verifier": {"provider": "review", "model": "verify-v1"},
            "adversary": {"provider": "review", "model": "attack-v1"},
            "holdout": {"provider": "review", "model": "hold-v1"},
        }
        model_ids = ["gpt-5.6-sol-900k", "gpt-5.6-luna"]
        rejected = dashboard_inventory_probe({
            "setup": {"models": review_models},
            "providers": [
                {"slug": "openai-codex", "models": model_ids},
                {"slug": "openai-codex", "authenticated": None, "models": model_ids},
                {"slug": "openai-codex", "authenticated": False, "models": model_ids},
                {"slug": "openai-codex", "authenticated": "true", "models": model_ids},
                {"slug": "openai-codex", "authenticated": 1, "models": model_ids},
            ],
        })
        self.assertEqual(rejected["refs"], [])
        self.assertEqual(rejected["normalisedModels"]["integrator"], {"provider": "", "model": ""})
        self.assertEqual(rejected["normalisedModels"]["builder"], {"provider": "", "model": ""})
        self.assertEqual(rejected["appliedModels"], rejected["normalisedModels"])
        self.assertTrue(rejected["presetDisabled"])

        alias_only = dashboard_inventory_probe({
            "setup": {"models": review_models},
            "providers": [{
                "slug": "openai-codex",
                "authenticated": True,
                "models": [
                    {"id": "gpt-5.6-sol-900k"},
                    {"model": "gpt-5.6-luna"},
                    {"name": "object-model"},
                ],
            }],
        })
        self.assertEqual(alias_only["refs"], [])
        self.assertEqual(alias_only["normalisedModels"]["integrator"], {"provider": "", "model": ""})
        self.assertEqual(alias_only["normalisedModels"]["builder"], {"provider": "", "model": ""})
        self.assertTrue(alias_only["presetDisabled"])

        accepted = dashboard_inventory_probe({
            "setup": {"models": review_models},
            "providers": [{
                "slug": "openai-codex",
                "authenticated": True,
                "models": model_ids,
            }],
        })
        self.assertEqual(accepted["refs"], [
            {"provider": "openai-codex", "model": "gpt-5.6-luna"},
            {"provider": "openai-codex", "model": "gpt-5.6-sol-900k"},
        ])
        self.assertEqual(
            accepted["normalisedModels"]["integrator"],
            {"provider": "openai-codex", "model": "gpt-5.6-sol-900k"},
        )
        self.assertEqual(
            accepted["normalisedModels"]["builder"],
            {"provider": "openai-codex", "model": "gpt-5.6-luna"},
        )
        for role, selection in review_models.items():
            self.assertEqual(accepted["normalisedModels"][role], selection)
        self.assertEqual(accepted["roles"], sorted(ROLES))
        self.assertFalse(accepted["presetDisabled"])


class DesktopBundleContractTests(BundleContractMixin, unittest.TestCase):
    round_trip = staticmethod(desktop_round_trip)

    def test_unified_desktop_page_is_opt_in_and_uses_real_serializers(self) -> None:
        source = DESKTOP.read_text(encoding="utf-8")
        self.assertIn("@hermes/plugin-sdk", source)
        self.assertIn("ROUTES_AREA", source)
        self.assertIn("SIDEBAR_NAV_AREA", source)
        self.assertIn("defaultEnabled: false", source)
        self.assertIn("/dark-factory", source)
        self.assertIn("body: { setup: serialiseSetup(setup, catalog) }", source)
        self.assertEqual(source.count("body: { setup: serialiseSetup(setup, catalog) }"), 2)
        self.assertNotIn("orchestrator_role", source)
        self.assertNotIn("worker_roles", source)
        for aria_label in ("Threat name", "Threat scenario", "Attack surface", "Expected control"):
            self.assertIn(f"'aria-label': '{aria_label}'", source)
        for marker in (
            "Orchestrator / Integrator",
            "Worker / Builder",
            "Apply Sol orchestrator + Luna worker",
            "The preset covers execution roles only",
            "Preferred preset model unavailable",
            "model_policy: { preset: text(modelPolicy.preset) || SOL_LUNA_PRESET }",
            "graph_backend: execution.graph_backend === 'local' || execution.backend === 'local' ? 'local' : 'beads'",
            "graph_mode: text(execution.graph_mode) || 'plan'",
            "beads_directory: text(execution.beads_directory || execution.beads_dir)",
            "beads_isolated_authorized: execution.beads_isolated_authorized === true || execution.allow_init === true",
        ) + PRESET_MODELS + GRAPH_COPY:
            self.assertIn(marker, source)

    def test_desktop_page_uses_no_jsx_or_credential_fields(self) -> None:
        source = DESKTOP.read_text(encoding="utf-8")
        self.assertNotRegex(source, r"return\s*<")
        self.assertIsNone(re.search(r"(?i)((?<![a-z0-9])sk-[a-z0-9_-]{16,}|bearer\s+[a-z0-9._-]{16,})", source))
        assert_no_credential_fields(self, source)

    def test_actual_desktop_bundle_requires_exact_auth_and_canonical_string_models(self) -> None:
        review_models = {
            "verifier": {"provider": "review", "model": "verify-v1"},
            "adversary": {"provider": "review", "model": "attack-v1"},
            "holdout": {"provider": "review", "model": "hold-v1"},
        }
        model_ids = ["gpt-5.6-sol-900k", "gpt-5.6-luna"]
        rejected = desktop_inventory_probe({
            "setup": {"models": review_models},
            "catalog": {
                "current": {"provider": {"slug": "bad-current"}, "model": 42},
                "providers": [
                    {"slug": "openai-codex", "models": model_ids},
                    {"slug": "openai-codex", "authenticated": None, "models": model_ids},
                    {"slug": "openai-codex", "authenticated": False, "models": model_ids},
                    {"slug": "openai-codex", "authenticated": "true", "models": model_ids},
                    {"slug": "openai-codex", "authenticated": 1, "models": model_ids},
                    {"slug": 123, "authenticated": True, "models": model_ids},
                    {"slug": "   ", "authenticated": True, "models": model_ids},
                ],
            },
        })
        self.assertEqual(rejected["catalog"]["current"], {"provider": "", "model": ""})
        self.assertEqual(rejected["catalog"]["providers"], [])
        self.assertEqual(rejected["rawRefs"], [])
        self.assertEqual(rejected["refs"], [])
        self.assertEqual(rejected["presetAvailable"], {"sol": False, "luna": False})
        self.assertEqual(rejected["normalisedModels"]["integrator"], {"provider": "", "model": ""})
        self.assertEqual(rejected["normalisedModels"]["builder"], {"provider": "", "model": ""})
        self.assertEqual(rejected["appliedModels"], rejected["normalisedModels"])
        self.assertEqual(rejected["savedModels"], rejected["normalisedModels"])
        self.assertEqual(rejected["compiledModels"], rejected["normalisedModels"])

        alias_only = desktop_inventory_probe({
            "setup": {"models": review_models},
            "catalog": {"providers": [{
                "slug": "openai-codex",
                "authenticated": True,
                "models": [
                    {"id": "gpt-5.6-sol-900k"},
                    {"model": "gpt-5.6-luna"},
                    {"name": "gpt-5.6-luna"},
                    900000,
                    {},
                    None,
                    "   ",
                ],
            }]},
        })
        self.assertEqual(alias_only["catalog"]["providers"], [])
        self.assertEqual(alias_only["rawRefs"], [])
        self.assertEqual(alias_only["refs"], [])
        self.assertEqual(alias_only["presetAvailable"], {"sol": False, "luna": False})
        self.assertEqual(alias_only["normalisedModels"]["integrator"], {"provider": "", "model": ""})
        self.assertEqual(alias_only["normalisedModels"]["builder"], {"provider": "", "model": ""})
        self.assertEqual(alias_only["savedModels"], alias_only["normalisedModels"])
        self.assertEqual(alias_only["compiledModels"], alias_only["normalisedModels"])

        accepted = desktop_inventory_probe({
            "setup": {"models": review_models},
            "catalog": {"providers": [{
                "slug": " OpenAI-Codex ",
                "authenticated": True,
                "models": [" gpt-5.6-sol-900k ", "gpt-5.6-luna"],
            }]},
        })
        expected_models = {
            "integrator": {"provider": "openai-codex", "model": "gpt-5.6-sol-900k"},
            "builder": {"provider": "openai-codex", "model": "gpt-5.6-luna"},
            **review_models,
        }
        self.assertEqual(len(accepted["catalog"]["providers"]), 1)
        provider = accepted["catalog"]["providers"][0]
        self.assertEqual(provider["slug"], "openai-codex")
        self.assertIs(provider["authenticated"], True)
        self.assertEqual(provider["models"], model_ids)
        self.assertEqual(accepted["refs"], [
            {"provider": "openai-codex", "model": "gpt-5.6-luna"},
            {"provider": "openai-codex", "model": "gpt-5.6-sol-900k"},
        ])
        self.assertEqual(accepted["presetAvailable"], {"sol": True, "luna": True})
        self.assertEqual(accepted["normalisedModels"], expected_models)
        self.assertEqual(accepted["appliedModels"], expected_models)
        self.assertEqual(accepted["savedModels"], expected_models)
        self.assertEqual(accepted["compiledModels"], expected_models)
        self.assertEqual(accepted["roles"], sorted(ROLES))

        explicit_models = {
            "integrator": {"provider": "custom", "model": "integrator-x"},
            "builder": {"provider": "custom", "model": "builder-x"},
            "verifier": {"provider": "custom", "model": "verify-x"},
            "adversary": {"provider": "custom", "model": "attack-x"},
            "holdout": {"provider": "custom", "model": "hold-x"},
        }
        explicit = desktop_inventory_probe({
            "setup": {"models": explicit_models},
            "catalog": {"providers": [{
                "slug": "openai-codex",
                "authenticated": True,
                "models": model_ids,
            }]},
        })
        for stage in ("normalisedModels", "appliedModels", "savedModels", "compiledModels"):
            self.assertEqual(explicit[stage], explicit_models)
        self.assertEqual(explicit["roles"], sorted(ROLES))


class CrossClientParityTests(unittest.TestCase):
    def test_clients_emit_identical_canonical_contract_fields(self) -> None:
        payload = canonical_payload()
        web = dashboard_round_trip(payload)["compiled"]
        desktop = desktop_round_trip(payload)["compiled"]
        for field in (
            "product",
            "context",
            "user_stories",
            "milestones",
            "testing",
            "security",
            "models",
            "model_policy",
            "execution",
        ):
            with self.subTest(field=field):
                self.assertEqual(web[field], desktop[field])

    def test_clients_emit_identical_non_authoritative_raw_threat_defaults(self) -> None:
        raw = {
            "model_policy": {"orchestrator_role": "integrator", "worker_roles": ["builder"]},
            "security": {"threat_scenarios": [{"severity": "critical", "threat": "legacy"}]},
        }
        web = dashboard_round_trip(raw)["saved"]
        desktop = desktop_round_trip(raw)["saved"]
        expected = {
            "id": "T1",
            "name": "",
            "scenario": "",
            "attack_surface": "",
            "expected_control": "",
        }
        self.assertEqual(web["model_policy"], desktop["model_policy"])
        self.assertEqual(web["security"]["threat_scenarios"], [expected])
        self.assertEqual(desktop["security"]["threat_scenarios"], [expected])
        normalise_setup(web)
        normalise_setup(desktop)


if __name__ == "__main__":
    unittest.main()
