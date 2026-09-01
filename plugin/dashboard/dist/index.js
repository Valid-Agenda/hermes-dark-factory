(function () {
  "use strict";

  var SDK = window.__HERMES_PLUGIN_SDK__;
  var registry = window.__HERMES_PLUGINS__;
  if (!SDK || !SDK.React || !SDK.components || !SDK.fetchJSON || !registry) {
    console.error("Dark Factory dashboard requires the Hermes Plugin SDK and registry.");
    return;
  }

  var React = SDK.React;
  var h = React.createElement;
  var hooks = SDK.hooks;
  var useState = hooks.useState;
  var useEffect = hooks.useEffect;
  var useCallback = hooks.useCallback;
  var useMemo = hooks.useMemo;
  var C = SDK.components;
  var Card = C.Card;
  var CardHeader = C.CardHeader;
  var CardTitle = C.CardTitle;
  var CardContent = C.CardContent;
  var Button = C.Button;
  var Badge = C.Badge;

  var API = "/api/plugins/dark-factory";
  var SOL_LUNA_PRESET = "sol-luna";
  var SOL_ORCHESTRATOR = { provider: "openai-codex", model: "gpt-5.6-sol-900k" };
  var LUNA_WORKER = { provider: "openai-codex", model: "gpt-5.6-luna" };
  var ACCEPTANCE_TYPES = ["happy", "negative", "recovery", "boundary", "abuse"];
  var ROLES = [
    { key: "integrator", label: "Orchestrator / Integrator", help: "Owns mission intent, shared contracts, integration, and milestone gates." },
    { key: "builder", label: "Worker / Builder", help: "Implements one bounded functional slice and its focused checks." },
    { key: "verifier", label: "Verifier", help: "Exercises acceptance evidence independently from implementation." },
    { key: "adversary", label: "Adversary", help: "Challenges security, failure, and boundary assumptions." },
    { key: "holdout", label: "Holdout", help: "Judges milestone evidence with a fresh context and external oracle." }
  ];
  var STEPS = [
    { id: "mission", label: "Mission", hint: "Problem and outcome" },
    { id: "users", label: "Users", hint: "Personas and stories" },
    { id: "delivery", label: "Delivery", hint: "Boundaries and milestones" },
    { id: "validation", label: "Validation", hint: "Deterministic test plan" },
    { id: "governance", label: "Governance", hint: "Security, risk, decisions" },
    { id: "models", label: "Model plan", hint: "Role assignments" }
  ];

  function blankPersona(index) {
    return { id: "P" + index, name: "", context: "", needs: "" };
  }

  function blankStory(index) {
    var id = "US" + index;
    return {
      id: id,
      persona_id: "",
      need: "",
      value: "",
      acceptance: [
        { id: id + "-A1", type: "happy", statement: "" },
        { id: id + "-A2", type: "negative", statement: "" }
      ],
      acceptance_criteria: [{ id: id + "-A1", type: "happy", statement: "" }],
      negative_acceptance: [{ id: id + "-A2", type: "negative", statement: "" }],
      paths: [""]
    };
  }

  function blankMilestone(index) {
    var id = "M" + index;
    return {
      id: id,
      title: "",
      outcome: "",
      story_ids: [],
      acceptance: [{ id: id + "-A1", type: "happy", statement: "" }],
      acceptance_criteria: [{ id: id + "-A1", type: "happy", statement: "" }],
      evidence: [""]
    };
  }

  function blankRisk(index) {
    return { id: "T" + index, name: "", scenario: "", attack_surface: "", expected_control: "" };
  }

  function blankDecision(index) {
    return { id: "D" + index, statement: "", status: "open", rationale: "" };
  }

  function blankModels() {
    var result = {};
    ROLES.forEach(function (role) {
      result[role.key] = { provider: "", model: "" };
    });
    return result;
  }

  function initialSetup() {
    return {
      intake_schema_version: 1,
      project_mode: "existing",
      workspace_path: "",
      product: {
        name: "",
        problem: "",
        outcome: "",
        context: "",
        existing_system: "",
        success_metrics: [""],
        surfaces: [""]
      },
      personas: [blankPersona(1)],
      user_stories: [blankStory(1)],
      non_goals: [""],
      constraints: [""],
      milestones: [blankMilestone(1)],
      test_plan: { unit: [""], integration: [""], acceptance: [], recovery: [], evidence: [""] },
      security: {
        data_classification: "none",
        adversarial_lens: "kryptonite",
        risk_triggers: [""],
        data: [""],
        controls: [""],
        human_gates: [""]
      },
      risks: [blankRisk(1)],
      decisions: [blankDecision(1)],
      models: blankModels(),
      model_policy: { preset: SOL_LUNA_PRESET },
      execution: {
        graph_backend: "beads",
        graph_mode: "plan",
        beads_directory: "",
        beads_isolated_authorized: false,
        reasoning_effort: { orchestrator: "high", worker: "medium" }
      }
    };
  }

  function arrayOr(value, fallback) {
    return Array.isArray(value) ? value : fallback;
  }

  function stringOr(value, fallback) {
    return typeof value === "string" ? value : fallback;
  }

  function hasOwn(value, key) {
    return !!value && Object.prototype.hasOwnProperty.call(value, key);
  }

  function normaliseAcceptance(value, defaultId, defaultType) {
    var source = value && typeof value === "object" && !Array.isArray(value) ? value : {};
    var suppliedType = stringOr(source.type, "");
    return {
      id: hasOwn(source, "id") ? stringOr(source.id, "") : defaultId,
      type: hasOwn(source, "type") && ACCEPTANCE_TYPES.indexOf(suppliedType) !== -1 ? suppliedType : defaultType,
      statement: hasOwn(source, "statement") ? stringOr(source.statement, "") : stringOr(value, "")
    };
  }

  function normaliseBrowserScenario(value, index) {
    var source = value && typeof value === "object" && !Array.isArray(value) ? value : {};
    var parts = typeof value === "string" ? splitScenario(value, 2) : ["", ""];
    return {
      name: hasOwn(source, "name") ? stringOr(source.name, "") : "Browser scenario " + (index + 1),
      action: hasOwn(source, "action") ? stringOr(source.action, "") : parts[0],
      expected: hasOwn(source, "expected") ? stringOr(source.expected, "") : parts[1]
    };
  }

  function normaliseHeldOutScenario(value, index) {
    var source = value && typeof value === "object" && !Array.isArray(value) ? value : {};
    var parts = typeof value === "string" ? splitScenario(value, 3) : ["", "", ""];
    return {
      name: hasOwn(source, "name") ? stringOr(source.name, "") : "Held-out scenario " + (index + 1),
      given: hasOwn(source, "given") ? stringOr(source.given, "") : parts[0],
      when: hasOwn(source, "when") ? stringOr(source.when, "") : parts[1],
      then: hasOwn(source, "then") ? stringOr(source.then, "") : parts[2]
    };
  }

  function normaliseThreat(value, index) {
    var source = value && typeof value === "object" && !Array.isArray(value) ? value : {};
    return {
      id: stringOr(source.id, "") || "T" + (index + 1),
      name: stringOr(source.name, ""),
      scenario: stringOr(source.scenario, ""),
      attack_surface: stringOr(source.attack_surface, ""),
      expected_control: stringOr(source.expected_control, "")
    };
  }

  function normaliseModelAssignment(value) {
    if (value && typeof value === "object") {
      return {
        provider: stringOr(value.provider, ""),
        model: stringOr(value.model, "")
      };
    }
    if (typeof value === "string" && value) {
      return { provider: "", model: value };
    }
    return { provider: "", model: "" };
  }

  function authenticatedModelRefs(providers) {
    var refs = [];
    (Array.isArray(providers) ? providers : []).forEach(function (provider) {
      var providerSlug = provider && typeof provider.slug === "string" ? provider.slug.trim() : "";
      if (!provider || provider.authenticated !== true || !providerSlug) return;
      (Array.isArray(provider.models) ? provider.models : []).forEach(function (value) {
        var model = typeof value === "string" ? value.trim() : "";
        if (model) refs.push({ provider: providerSlug, model: model });
      });
    });
    return refs.sort(function (left, right) {
      return (left.provider + "/" + left.model).localeCompare(right.provider + "/" + right.model);
    });
  }

  function modelRefAvailable(providers, reference) {
    return authenticatedModelRefs(providers).some(function (item) {
      return item.provider === reference.provider && item.model === reference.model;
    });
  }

  function applySolLunaPreset(models, providers) {
    var next = Object.assign({}, models || {});
    var integrator = normaliseModelAssignment(next.integrator);
    var builder = normaliseModelAssignment(next.builder);
    if (!hasText(integrator.provider) && !hasText(integrator.model) && modelRefAvailable(providers, SOL_ORCHESTRATOR)) {
      next.integrator = Object.assign({}, SOL_ORCHESTRATOR);
    }
    if (!hasText(builder.provider) && !hasText(builder.model) && modelRefAvailable(providers, LUNA_WORKER)) {
      next.builder = Object.assign({}, LUNA_WORKER);
    }
    return next;
  }

  function applyPreferredExecutionDefaults(models, modelSource, providers) {
    var next = Object.assign({}, models);
    [
      { role: "integrator", preferred: SOL_ORCHESTRATOR },
      { role: "builder", preferred: LUNA_WORKER }
    ].forEach(function (item) {
      var incoming = modelSource[item.role];
      var explicit = incoming && typeof incoming === "object" && (hasText(incoming.provider) || hasText(incoming.model));
      if (!explicit && !hasText(next[item.role].provider) && !hasText(next[item.role].model) && modelRefAvailable(providers, item.preferred)) {
        next[item.role] = Object.assign({}, item.preferred);
      }
    });
    return next;
  }

  function normaliseSetup(raw, modelOptions) {
    var base = initialSetup();
    var source = raw && typeof raw === "object" ? raw : {};
    var productSource = source.product && typeof source.product === "object" ? source.product : {};
    if (typeof source.product === "string") productSource = { name: source.product };
    var canonicalTesting = source.testing && typeof source.testing === "object" ? source.testing : {};
    var testSource = source.test_plan && typeof source.test_plan === "object" ? source.test_plan : {
      unit: canonicalTesting.focused_commands,
      integration: canonicalTesting.integration_commands,
      acceptance: canonicalTesting.browser_scenarios,
      recovery: canonicalTesting.held_out_scenarios,
      evidence: canonicalTesting.evidence_requirements
    };
    var securitySource = source.security && typeof source.security === "object" && !Array.isArray(source.security) ? source.security : {};
    var modelSource = source.models && typeof source.models === "object" ? source.models : {};
    var models = {};
    var modelPolicySource = source.model_policy && typeof source.model_policy === "object" ? source.model_policy : {};
    var executionSource = source.execution && typeof source.execution === "object" ? source.execution : {};
    var reasoningEffortSource = executionSource.reasoning_effort && typeof executionSource.reasoning_effort === "object" ? executionSource.reasoning_effort : {};
    var optionProviders = modelOptions && Array.isArray(modelOptions.providers) ? modelOptions.providers : [];

    ROLES.forEach(function (role) {
      models[role.key] = normaliseModelAssignment(modelSource[role.key]);
    });
    models = applyPreferredExecutionDefaults(models, modelSource, optionProviders);

    return Object.assign({}, base, source, {
      intake_schema_version: source.intake_schema_version || 1,
      project_mode: source.project_mode === "greenfield" ? "greenfield" : "existing",
      workspace_path: stringOr(source.workspace_path, ""),
      product: Object.assign({}, base.product, productSource),
      personas: arrayOr(source.personas, base.personas).map(function (item, index) {
        var persona = Object.assign(blankPersona(index + 1), item || {});
        persona.needs = stringOr(persona.needs, "") || stringOr(persona.need, "");
        return persona;
      }),
      user_stories: arrayOr(source.user_stories, base.user_stories).map(function (item, index) {
        var incomingStory = item || {};
        var story = Object.assign(blankStory(index + 1), incomingStory);
        var storyId = stringOr(story.id, "") || "US" + (index + 1);
        var canonicalAcceptance = arrayOr(incomingStory.acceptance, []).map(function (criterion, criterionIndex) {
          return normaliseAcceptance(criterion, storyId + "-A" + (criterionIndex + 1), "happy");
        });
        var happySource = hasOwn(incomingStory, "acceptance_criteria") ? arrayOr(incomingStory.acceptance_criteria, []) : canonicalAcceptance.filter(function (criterion) {
          return criterion.type === "happy";
        });
        var negativeSource = hasOwn(incomingStory, "negative_acceptance") ? arrayOr(incomingStory.negative_acceptance, []) : canonicalAcceptance.filter(function (criterion) {
          return criterion.type !== "happy";
        });
        story.need = stringOr(story.need, "") || stringOr(story.want, "");
        story.value = stringOr(story.value, "") || stringOr(story.so_that, "");
        story.acceptance_criteria = happySource.map(function (criterion, criterionIndex) {
          return normaliseAcceptance(criterion, storyId + "-H" + (criterionIndex + 1), "happy");
        });
        story.negative_acceptance = negativeSource.map(function (criterion, criterionIndex) {
          return normaliseAcceptance(criterion, storyId + "-N" + (criterionIndex + 1), "negative");
        });
        story.acceptance = canonicalAcceptance.length ? canonicalAcceptance : story.acceptance_criteria.concat(story.negative_acceptance);
        story.paths = arrayOr(story.paths, [""]);
        return story;
      }),
      non_goals: arrayOr(source.non_goals, base.non_goals),
      constraints: arrayOr(source.constraints, base.constraints),
      milestones: arrayOr(source.milestones, base.milestones).map(function (item, index) {
        var incomingMilestone = item || {};
        var milestone = Object.assign(blankMilestone(index + 1), incomingMilestone);
        var milestoneId = stringOr(milestone.id, "") || "M" + (index + 1);
        var acceptanceSource = hasOwn(incomingMilestone, "acceptance_criteria") ? arrayOr(incomingMilestone.acceptance_criteria, []) : arrayOr(incomingMilestone.acceptance, []);
        milestone.story_ids = arrayOr(milestone.story_ids, []);
        milestone.acceptance_criteria = acceptanceSource.map(function (criterion, criterionIndex) {
          return normaliseAcceptance(criterion, milestoneId + "-A" + (criterionIndex + 1), "happy");
        });
        milestone.acceptance = milestone.acceptance_criteria;
        milestone.evidence = arrayOr(milestone.evidence, [""]);
        return milestone;
      }),
      test_plan: {
        unit: arrayOr(testSource.unit, base.test_plan.unit),
        integration: arrayOr(testSource.integration, base.test_plan.integration),
        acceptance: arrayOr(testSource.acceptance, base.test_plan.acceptance).map(normaliseBrowserScenario),
        recovery: arrayOr(testSource.recovery, base.test_plan.recovery).map(normaliseHeldOutScenario),
        evidence: arrayOr(testSource.evidence, base.test_plan.evidence)
      },
      security: {
        data_classification: stringOr(securitySource.data_classification, "none"),
        adversarial_lens: stringOr(securitySource.adversarial_lens, "kryptonite") || "kryptonite",
        risk_triggers: arrayOr(securitySource.risk_triggers, base.security.risk_triggers),
        data: arrayOr(securitySource.data, Array.isArray(source.security) ? source.security : base.security.data),
        controls: arrayOr(securitySource.controls, base.security.controls),
        human_gates: arrayOr(securitySource.human_gates, base.security.human_gates)
      },
      risks: arrayOr(source.risks, arrayOr(securitySource.threat_scenarios, base.risks)).map(normaliseThreat),
      decisions: arrayOr(source.decisions, arrayOr(securitySource.authority_decisions, base.decisions)).map(function (item, index) {
        return Object.assign(blankDecision(index + 1), item || {});
      }),
      models: models,
      model_policy: {
        preset: stringOr(modelPolicySource.preset, base.model_policy.preset) || base.model_policy.preset
      },
      execution: {
        graph_backend: executionSource.graph_backend === "local" || executionSource.backend === "local" ? "local" : "beads",
        graph_mode: stringOr(executionSource.graph_mode, base.execution.graph_mode) || base.execution.graph_mode,
        beads_directory: stringOr(executionSource.beads_directory, stringOr(executionSource.beads_dir, "")),
        beads_isolated_authorized: executionSource.beads_isolated_authorized === true || executionSource.allow_init === true,
        reasoning_effort: {
          orchestrator: stringOr(reasoningEffortSource.orchestrator, base.execution.reasoning_effort.orchestrator) || base.execution.reasoning_effort.orchestrator,
          worker: stringOr(reasoningEffortSource.worker, base.execution.reasoning_effort.worker) || base.execution.reasoning_effort.worker
        }
      }
    });
  }

  function setAtPath(source, path, value) {
    if (!path.length) return value;
    var key = path[0];
    var copy = Array.isArray(source) ? source.slice() : Object.assign({}, source || {});
    copy[key] = setAtPath(source && source[key], path.slice(1), value);
    return copy;
  }

  function hasText(value, minimum) {
    return typeof value === "string" && value.trim().length >= (minimum || 1);
  }

  function hasLine(values) {
    return Array.isArray(values) && values.some(function (value) { return hasText(value); });
  }

  function allMeaningful(values, predicate) {
    var populated = Array.isArray(values) ? values.filter(function (value) {
      if (typeof value === "string") return hasText(value);
      return value && typeof value === "object";
    }) : [];
    return populated.length > 0 && populated.every(predicate);
  }

  function providerFor(providers, slug) {
    return (Array.isArray(providers) ? providers : []).find(function (provider) {
      return provider && provider.authenticated === true && typeof provider.slug === "string" && provider.slug.trim() === slug;
    }) || null;
  }

  function providerModels(provider) {
    var models = [];
    if (!provider || provider.authenticated !== true || !Array.isArray(provider.models)) return models;
    provider.models.forEach(function (value) {
      var model = typeof value === "string" ? value.trim() : "";
      if (model && models.indexOf(model) === -1) models.push(model);
    });
    return models;
  }

  function assessReadiness(setup, providers) {
    var product = setup.product || {};
    var personas = setup.personas || [];
    var stories = setup.user_stories || [];
    var milestones = setup.milestones || [];
    var tests = setup.test_plan || {};
    var security = setup.security || {};
    var models = setup.models || {};
    var personaIds = personas.map(function (persona) { return persona.id; });
    var storyIds = stories.map(function (story) { return story.id; });
    var mappedStoryIds = milestones.reduce(function (all, milestone) { return all.concat(meaningfulLines(milestone.story_ids)); }, []).sort();
    var userFacing = meaningfulLines(product.surfaces).some(function (surface) {
      return ["web ui", "mobile ui", "desktop ui", "public api"].indexOf(surface.toLowerCase()) !== -1;
    });
    var interactionValid = !userFacing || (meaningfulLines(tests.acceptance).length > 0 && meaningfulLines(tests.acceptance).every(function (line) {
      var parts = splitScenario(line, 2);
      return hasText(parts[0], 8) && hasText(parts[1], 8);
    }));
    var holdoutValid = meaningfulLines(tests.recovery).length > 0 && meaningfulLines(tests.recovery).every(function (line) {
      return splitScenario(line, 3).every(function (part) { return hasText(part, 8); });
    });
    var modelValid = ROLES.every(function (role) {
      var assignment = models[role.key] || {};
      var provider = providerFor(providers, assignment.provider);
      return !!(provider && hasText(assignment.model) && providerModels(provider).indexOf(assignment.model) !== -1);
    });
    var builder = models.builder || {};
    var independentModels = ["verifier", "adversary", "holdout"].every(function (role) {
      var assignment = models[role] || {};
      return hasText(assignment.provider) && hasText(assignment.model) && (assignment.provider !== builder.provider || assignment.model !== builder.model);
    });
    var highRisk = ["personal", "sensitive", "regulated"].indexOf(String(security.data_classification || "").toLowerCase()) !== -1 || hasLine(security.risk_triggers);
    var requiredThreats = highRisk ? 2 : 1;
    var rules = [
      { weight: 5, severity: "blocker", label: "Choose a project mode and provide a usable workspace path.", pass: ["existing", "greenfield"].indexOf(setup.project_mode) !== -1 && hasText(setup.workspace_path, 2) },
      { weight: 10, severity: "blocker", label: "Complete the product name, problem, domain context, interaction surfaces, and existing-system description where required.", pass: hasText(product.name, 2) && hasText(product.problem, 30) && hasText(product.context, 30) && hasLine(product.surfaces) && (setup.project_mode !== "existing" || hasText(product.existing_system, 30)) },
      { weight: 8, severity: "blocker", label: "Define an observable mission outcome and at least one measurable success signal.", pass: hasText(product.outcome, 30) && hasLine(product.success_metrics) },
      { weight: 7, severity: "blocker", label: "Give every persona a unique ID, name, operating context, and concrete need.", pass: allMeaningful(personas, function (persona) { return hasText(persona.id) && hasText(persona.name) && hasText(persona.context, 12) && hasText(persona.needs, 12); }) && new Set(personaIds).size === personaIds.length },
      { weight: 9, severity: "blocker", label: "Complete every user story, tie it to a known persona, and include positive plus negative acceptance.", pass: allMeaningful(stories, function (story) { return hasText(story.id) && personaIds.indexOf(story.persona_id) !== -1 && hasText(story.need, 12) && hasText(story.value, 12) && hasLine(story.acceptance_criteria) && hasLine(story.negative_acceptance) && meaningfulLines(story.acceptance_criteria).length + meaningfulLines(story.negative_acceptance).length >= 2; }) },
      { weight: 4, severity: "blocker", label: "Add negative, recovery, boundary, or abuse acceptance to every story.", pass: allMeaningful(stories, function (story) { return hasLine(story.negative_acceptance); }) },
      { weight: 6, severity: "blocker", label: "Declare both non-goals and delivery constraints.", pass: hasLine(setup.non_goals) && hasLine(setup.constraints) },
      { weight: 10, severity: "blocker", label: "Give every milestone an outcome, mapped story IDs, acceptance criteria, and evidence.", pass: allMeaningful(milestones, function (milestone) { return hasText(milestone.id) && hasText(milestone.outcome, 20) && hasLine(milestone.story_ids) && hasLine(milestone.acceptance_criteria) && hasLine(milestone.evidence); }) },
      { weight: 5, severity: "blocker", label: "Map every user story to exactly one milestone.", pass: storyIds.length > 0 && JSON.stringify(mappedStoryIds) === JSON.stringify(storyIds.slice().sort()) },
      { weight: 7, severity: "blocker", label: "Provide focused and integration test commands.", pass: hasLine(tests.unit) && hasLine(tests.integration) },
      { weight: 7, severity: "blocker", label: "Provide valid held-out Given => When => Then scenarios and interaction scenarios for user-facing surfaces.", pass: holdoutValid && interactionValid },
      { weight: 4, severity: "warning", label: "Describe data boundaries, required controls, and human decision gates.", pass: ["none", "internal", "personal", "sensitive", "regulated"].indexOf(security.data_classification) !== -1 && hasLine(security.data) && hasLine(security.controls) && hasLine(security.human_gates) },
      { weight: 4, severity: "blocker", label: "Record enough substantive adversarial threats and observable controls for the derived risk level.", pass: (setup.risks || []).filter(function (risk) { return hasText(risk.name, 3) && hasText(risk.scenario, 12) && hasText(risk.attack_surface, 3) && hasText(risk.expected_control, 12); }).length >= requiredThreats },
      { weight: 4, severity: "blocker", label: "Record and lock at least one authority or product decision.", pass: (setup.decisions || []).some(function (decision) { return hasText(decision.statement, 12) && decision.status === "locked"; }) },
      { weight: 7, severity: "blocker", label: "Assign an authenticated provider and available model to all five factory roles.", pass: modelValid },
      { weight: 3, severity: "blocker", label: "Verifier, adversary, and holdout models must differ from the builder model.", pass: independentModels }
    ];
    var score = rules.reduce(function (total, rule) { return total + (rule.pass ? rule.weight : 0); }, 0);
    return {
      score: score,
      ready: score === 100,
      blockers: rules.filter(function (rule) { return !rule.pass && rule.severity === "blocker"; }).map(function (rule) { return rule.label; }),
      warnings: rules.filter(function (rule) { return !rule.pass && rule.severity === "warning"; }).map(function (rule) { return rule.label; }),
      passed: rules.filter(function (rule) { return rule.pass; }).length,
      total: rules.length
    };
  }

  function parseApiFailure(error) {
    var raw = error && error.message ? String(error.message) : String(error || "Request failed");
    var match = raw.match(/^(\d{3}):\s*(.*)$/s);
    var body = match ? match[2] : raw;
    try {
      var parsed = JSON.parse(body);
      var detail = parsed && parsed.detail;
      if (typeof detail === "string") return { message: detail, readiness: null };
      if (detail && typeof detail === "object") {
        return { message: detail.message || body, readiness: detail.readiness || null };
      }
      if (parsed && typeof parsed.message === "string") return { message: parsed.message, readiness: parsed.readiness || null };
    } catch (_error) {
      // Non-JSON errors are already useful text.
    }
    return { message: body || raw, readiness: null };
  }

  function parseApiError(error) {
    return parseApiFailure(error).message;
  }

  function nextId(prefix, values) {
    var used = {};
    (values || []).forEach(function (value) { used[value && value.id] = true; });
    var index = 1;
    while (used[prefix + index]) index += 1;
    return prefix + index;
  }

  function classNames() {
    return Array.prototype.slice.call(arguments).filter(Boolean).join(" ");
  }

  function lineValue(value) {
    if (value && typeof value === "object" && !Array.isArray(value)) {
      if (hasOwn(value, "statement")) return stringOr(value.statement, "");
      if (hasOwn(value, "action") || hasOwn(value, "expected")) return stringOr(value.action, "") + " => " + stringOr(value.expected, "");
      if (hasOwn(value, "given") || hasOwn(value, "when") || hasOwn(value, "then")) {
        return stringOr(value.given, "") + " => " + stringOr(value.when, "") + " => " + stringOr(value.then, "");
      }
    }
    return String(value || "");
  }

  function meaningfulLines(values) {
    return (Array.isArray(values) ? values : []).map(function (value) { return lineValue(value).trim(); }).filter(Boolean);
  }

  function criterionRows(values, type, prefix) {
    return (Array.isArray(values) ? values : []).map(function (value, index) {
      var row = normaliseAcceptance(value, prefix + (index + 1), type);
      return { id: row.id, type: row.type, statement: row.statement };
    });
  }

  function mergeStoryAcceptance(story) {
    var storyId = stringOr(story && story.id, "") || "US";
    var rows = criterionRows(story && story.acceptance_criteria, "happy", storyId + "-H")
      .concat(criterionRows(story && story.negative_acceptance, "negative", storyId + "-N"));
    var result = [];
    arrayOr(story && story.acceptance, []).forEach(function (original) {
      var source = normaliseAcceptance(original, "", "happy");
      var matchIndex = rows.findIndex(function (row) {
        return row.id === source.id && row.type === source.type;
      });
      if (matchIndex === -1) return;
      result.push(rows[matchIndex]);
      rows.splice(matchIndex, 1);
    });
    return result.concat(rows);
  }

  function splitScenario(value, expectedParts) {
    var parts = String(value || "").split(/\s*(?:=>|→)\s*/).map(function (part) { return part.trim(); });
    while (parts.length < expectedParts) parts.push("");
    return parts.slice(0, expectedParts);
  }

  function updateAcceptanceLines(values, lines, prefix, defaultType) {
    return lines.map(function (statement, index) {
      var row = normaliseAcceptance(arrayOr(values, [])[index], prefix + (index + 1), defaultType);
      row.statement = statement;
      return row;
    });
  }

  function updateBrowserScenarioLines(values, lines) {
    return lines.map(function (line, index) {
      var row = normaliseBrowserScenario(arrayOr(values, [])[index], index);
      var parts = splitScenario(line, 2);
      row.action = parts[0];
      row.expected = parts[1];
      return row;
    });
  }

  function updateHeldOutScenarioLines(values, lines) {
    return lines.map(function (line, index) {
      var row = normaliseHeldOutScenario(arrayOr(values, [])[index], index);
      var parts = splitScenario(line, 3);
      row.given = parts[0];
      row.when = parts[1];
      row.then = parts[2];
      return row;
    });
  }

  function toApiSetup(setup) {
    var payload = Object.assign({}, setup);
    var plan = setup.test_plan || {};
    var security = setup.security || {};
    payload.intake_schema_version = 1;
    payload.project_mode = setup.project_mode === "greenfield" ? "greenfield" : "existing";
    payload.workspace_path = String(setup.workspace_path || "").trim();
    payload.product = Object.assign({}, setup.product || {}, {
      success_metrics: meaningfulLines(setup.product && setup.product.success_metrics),
      surfaces: meaningfulLines(setup.product && setup.product.surfaces)
    });
    payload.personas = (setup.personas || []).map(function (persona) {
      return {
        id: persona.id,
        name: persona.name,
        context: persona.context,
        need: persona.needs
      };
    });
    payload.user_stories = (setup.user_stories || []).map(function (story) {
      return {
        id: story.id,
        persona_id: story.persona_id,
        want: story.need,
        so_that: story.value,
        acceptance: mergeStoryAcceptance(story),
        paths: meaningfulLines(story.paths)
      };
    });
    payload.non_goals = meaningfulLines(setup.non_goals);
    payload.constraints = meaningfulLines(setup.constraints);
    payload.milestones = (setup.milestones || []).map(function (milestone) {
      return {
        id: milestone.id,
        title: milestone.title,
        outcome: milestone.outcome,
        story_ids: meaningfulLines(milestone.story_ids),
        acceptance: criterionRows(milestone.acceptance_criteria, "happy", (stringOr(milestone.id, "") || "M") + "-A"),
        evidence: meaningfulLines(milestone.evidence)
      };
    });
    payload.testing = {
      focused_commands: meaningfulLines(plan.unit),
      integration_commands: meaningfulLines(plan.integration),
      browser_scenarios: arrayOr(plan.acceptance, []).map(function (scenario, index) {
        return normaliseBrowserScenario(scenario, index);
      }),
      held_out_scenarios: arrayOr(plan.recovery, []).map(function (scenario, index) {
        return normaliseHeldOutScenario(scenario, index);
      }),
      evidence_requirements: meaningfulLines(plan.evidence)
    };
    payload.security = Object.assign({}, security, {
      data_classification: security.data_classification || "none",
      risk_triggers: meaningfulLines(security.risk_triggers),
      threat_scenarios: (setup.risks || []).map(function (risk, index) {
        return normaliseThreat(risk, index);
      }),
      authority_decisions: (setup.decisions || []).filter(function (decision) {
        return hasText(decision.statement);
      }).map(function (decision) {
        return { id: decision.id, statement: decision.statement, status: decision.status, rationale: decision.rationale };
      })
    });
    payload.models = {};
    ROLES.forEach(function (role) {
      var assignment = setup.models && setup.models[role.key] || {};
      payload.models[role.key] = {
        provider: stringOr(assignment.provider, ""),
        model: stringOr(assignment.model, "")
      };
    });
    payload.model_policy = {
      preset: stringOr(setup.model_policy && setup.model_policy.preset, SOL_LUNA_PRESET) || SOL_LUNA_PRESET
    };
    payload.execution = {
      graph_backend: setup.execution && setup.execution.graph_backend === "local" ? "local" : "beads",
      graph_mode: stringOr(setup.execution && setup.execution.graph_mode, "plan") || "plan",
      beads_directory: stringOr(setup.execution && setup.execution.beads_directory, ""),
      beads_isolated_authorized: !!(setup.execution && setup.execution.beads_isolated_authorized),
      reasoning_effort: {
        orchestrator: stringOr(setup.execution && setup.execution.reasoning_effort && setup.execution.reasoning_effort.orchestrator, "high") || "high",
        worker: stringOr(setup.execution && setup.execution.reasoning_effort && setup.execution.reasoning_effort.worker, "medium") || "medium"
      }
    };
    delete payload.test_plan;
    delete payload.risks;
    delete payload.decisions;
    delete payload.schema_version;
    return payload;
  }

  function Field(props) {
    return h("div", { className: classNames("df-field", props.className) },
      h("label", { htmlFor: props.id },
        h("span", null, props.label),
        props.required ? h("span", { className: "df-required", "aria-hidden": "true" }, "Required") : null
      ),
      props.help ? h("p", { id: props.id + "-help", className: "df-field-help" }, props.help) : null,
      h("input", {
        id: props.id,
        className: "df-input",
        type: props.type || "text",
        value: props.value || "",
        placeholder: props.placeholder || "",
        disabled: !!props.disabled,
        "aria-describedby": props.help ? props.id + "-help" : undefined,
        onChange: function (event) { props.onChange(event.target.value); }
      })
    );
  }

  function TextAreaField(props) {
    return h("div", { className: classNames("df-field", props.className) },
      h("label", { htmlFor: props.id },
        h("span", null, props.label),
        props.required ? h("span", { className: "df-required", "aria-hidden": "true" }, "Required") : null
      ),
      props.help ? h("p", { id: props.id + "-help", className: "df-field-help" }, props.help) : null,
      h("textarea", {
        id: props.id,
        className: "df-textarea",
        rows: props.rows || 4,
        value: props.value || "",
        placeholder: props.placeholder || "",
        "aria-describedby": props.help ? props.id + "-help" : undefined,
        onChange: function (event) { props.onChange(event.target.value); }
      })
    );
  }

  function LinesField(props) {
    var values = Array.isArray(props.value) ? props.value : [];
    var displayedValues = values.map(lineValue);
    return h(TextAreaField, {
      id: props.id,
      label: props.label,
      help: props.help ? props.help + " Enter one item per line." : "Enter one item per line.",
      required: props.required,
      rows: props.rows || 4,
      placeholder: props.placeholder,
      value: displayedValues.join("\n"),
      onChange: function (value) {
        var lines = value === "" ? [] : value.split(/\r?\n/);
        props.onChange(props.updateLines ? props.updateLines(values, lines) : lines);
      }
    });
  }

  function SelectField(props) {
    return h("div", { className: classNames("df-field", props.className) },
      h("label", { htmlFor: props.id }, props.label),
      props.help ? h("p", { id: props.id + "-help", className: "df-field-help" }, props.help) : null,
      h("select", {
        id: props.id,
        className: "df-select",
        value: props.value || "",
        disabled: !!props.disabled,
        "aria-describedby": props.help ? props.id + "-help" : undefined,
        onChange: function (event) { props.onChange(event.target.value); }
      }, props.children)
    );
  }

  function SectionHeading(props) {
    return h("div", { className: "df-section-heading" },
      h("div", null,
        h("p", { className: "df-eyebrow" }, props.eyebrow),
        h("h2", null, props.title),
        h("p", { className: "df-section-copy" }, props.copy)
      ),
      props.action || null
    );
  }

  function EmptyState(props) {
    return h("div", { className: "df-empty" },
      h("strong", null, props.title),
      h("p", null, props.copy),
      h(Button, { type: "button", variant: "outline", onClick: props.onAdd }, props.action)
    );
  }

  function ItemHeader(props) {
    return h("div", { className: "df-item-header" },
      h("div", null,
        h("span", { className: "df-item-id" }, props.id),
        h("strong", null, props.title)
      ),
      h(Button, {
        type: "button",
        variant: "ghost",
        className: "df-remove",
        onClick: props.onRemove,
        "aria-label": "Remove " + props.title
      }, "Remove")
    );
  }

  function MissionSection(props) {
    var product = props.setup.product || {};
    return h("section", { className: "df-section", "aria-labelledby": "df-mission-title" },
      h(SectionHeading, {
        eyebrow: "01 / Mission contract",
        title: "Anchor the factory to one outcome",
        copy: "Choose the target workspace, state what is being built, explain the current system, and define the observable result that ends the mission."
      }),
      h("div", { className: "df-two-grid" },
        h(SelectField, {
          id: "df-project-mode",
          label: "Project mode",
          value: props.setup.project_mode,
          help: "Existing projects require an existing directory. Greenfield projects require an existing parent directory.",
          onChange: function (value) { props.update(["project_mode"], value); }
        },
          h("option", { value: "existing" }, "Existing product"),
          h("option", { value: "greenfield" }, "Greenfield build")
        ),
        h(Field, {
          id: "df-workspace-path",
          label: "Workspace path",
          required: true,
          value: props.setup.workspace_path,
          placeholder: "/path/to/product",
          help: "The compiled manifest is written under this workspace. Use a local path; no repository is created from this screen.",
          onChange: function (value) { props.update(["workspace_path"], value); }
        })
      ),
      h("div", { className: "df-form-grid" },
        h(Field, {
          id: "df-product-name",
          label: "Product or capability",
          required: true,
          value: product.name,
          placeholder: "Durable project workspace",
          help: "Use a stable name that will make sense in receipts and milestone reports.",
          onChange: function (value) { props.update(["product", "name"], value); }
        }),
        h(TextAreaField, {
          id: "df-product-problem",
          label: "Problem",
          required: true,
          rows: 6,
          value: product.problem,
          placeholder: "Verified users cannot safely create and reopen their work without crossing ownership boundaries…",
          help: "Describe the affected actor, current failure, and consequence. Avoid prescribing the implementation.",
          onChange: function (value) { props.update(["product", "problem"], value); }
        }),
        h(TextAreaField, {
          id: "df-product-outcome",
          label: "Mission outcome",
          required: true,
          rows: 6,
          value: product.outcome,
          placeholder: "A verified user creates, reloads, and reopens the same durable project while a second user is deterministically denied.",
          help: "Write the end state as a user or dependent system capability that can be demonstrated.",
          onChange: function (value) { props.update(["product", "outcome"], value); }
        }),
        h(TextAreaField, {
          id: "df-product-context",
          label: "Domain and operating context",
          required: true,
          rows: 5,
          value: product.context,
          placeholder: "Projects are long-lived owner-scoped records used across browser sessions. Identity is server-authoritative and users may own several projects.",
          help: "Explain vocabulary, workflows, assumptions, and constraints agents must not invent.",
          onChange: function (value) { props.update(["product", "context"], value); }
        }),
        props.setup.project_mode === "existing" ? h(TextAreaField, {
          id: "df-existing-system",
          label: "Existing system and current behavior",
          required: true,
          rows: 5,
          value: product.existing_system,
          placeholder: "The current API creates transient projects and the UI loses selection on reload. Ownership checks exist only on list requests.",
          help: "Point to relevant architecture and current limitations before the factory changes code.",
          onChange: function (value) { props.update(["product", "existing_system"], value); }
        }) : null,
        h("div", { className: "df-two-grid" },
          h(LinesField, {
            id: "df-success-metrics",
            label: "Success metrics",
            required: true,
            value: product.success_metrics,
            rows: 4,
            placeholder: "Create → reload → reopen succeeds in the scripted journey\nCross-owner reads disclose no project content",
            help: "Use measurable behavior or quality signals.",
            onChange: function (value) { props.update(["product", "success_metrics"], value); }
          }),
          h(LinesField, {
            id: "df-product-surfaces",
            label: "Product surfaces",
            value: product.surfaces,
            rows: 4,
            placeholder: "web ui\npublic api",
            help: "Examples: web ui, mobile ui, desktop ui, public api, worker, CLI.",
            onChange: function (value) { props.update(["product", "surfaces"], value); }
          })
        )
      )
    );
  }

  function UsersSection(props) {
    var personas = props.setup.personas || [];
    var stories = props.setup.user_stories || [];

    function addPersona() {
      var id = nextId("P", personas);
      props.update(["personas"], personas.concat([{ id: id, name: "", context: "", needs: "" }]));
    }

    function addStory() {
      var id = nextId("US", stories);
      var story = Object.assign(blankStory(1), {
        id: id,
        acceptance: [
          { id: id + "-A1", type: "happy", statement: "" },
          { id: id + "-A2", type: "negative", statement: "" }
        ],
        acceptance_criteria: [{ id: id + "-A1", type: "happy", statement: "" }],
        negative_acceptance: [{ id: id + "-A2", type: "negative", statement: "" }]
      });
      props.update(["user_stories"], stories.concat([story]));
    }

    return h("section", { className: "df-section", "aria-labelledby": "df-users-title" },
      h(SectionHeading, {
        eyebrow: "02 / User contract",
        title: "Describe who must succeed",
        copy: "Personas establish context. Stories turn that context into independently testable behavior."
      }),
      h("div", { className: "df-subsection" },
        h("div", { className: "df-subsection-title" },
          h("div", null, h("h3", null, "Personas"), h("p", null, "Keep only actors whose goals change acceptance or risk.")),
          h(Button, { type: "button", variant: "outline", onClick: addPersona }, "+ Add persona")
        ),
        personas.length === 0 ? h(EmptyState, {
          title: "No personas yet",
          copy: "Add the primary person or system that receives the outcome.",
          action: "Add persona",
          onAdd: addPersona
        }) : h("div", { className: "df-stack" }, personas.map(function (persona, index) {
          return h(Card, { key: persona.id || index, className: "df-item-card" },
            h(CardContent, { className: "df-item-content" },
              h(ItemHeader, {
                id: persona.id || "P" + (index + 1),
                title: persona.name || "Untitled persona",
                onRemove: function () { props.update(["personas"], personas.filter(function (_item, itemIndex) { return itemIndex !== index; })); }
              }),
              h("div", { className: "df-three-grid" },
                h(Field, {
                  id: "df-persona-name-" + index,
                  label: "Persona name",
                  value: persona.name,
                  placeholder: "Verified workspace owner",
                  onChange: function (value) { props.update(["personas", index, "name"], value); }
                }),
                h(TextAreaField, {
                  id: "df-persona-context-" + index,
                  label: "Context",
                  rows: 3,
                  value: persona.context,
                  placeholder: "Returns across devices and expects work to persist.",
                  onChange: function (value) { props.update(["personas", index, "context"], value); }
                }),
                h(TextAreaField, {
                  id: "df-persona-needs-" + index,
                  label: "Needs and success signal",
                  rows: 3,
                  value: persona.needs,
                  placeholder: "Can reopen exactly the project they created without seeing another owner’s data.",
                  onChange: function (value) { props.update(["personas", index, "needs"], value); }
                })
              )
            )
          );
        }))
      ),
      h("div", { className: "df-subsection" },
        h("div", { className: "df-subsection-title" },
          h("div", null, h("h3", null, "Structured user stories"), h("p", null, "Each story needs an actor, need, value, and observable acceptance.")),
          h(Button, { type: "button", variant: "outline", onClick: addStory }, "+ Add story")
        ),
        stories.length === 0 ? h(EmptyState, {
          title: "No stories yet",
          copy: "Add the smallest coherent behavior that carries user value.",
          action: "Add story",
          onAdd: addStory
        }) : h("div", { className: "df-stack" }, stories.map(function (story, index) {
          var persona = personas.find(function (item) { return item.id === story.persona_id; });
          return h(Card, { key: story.id || index, className: "df-item-card" },
            h(CardContent, { className: "df-item-content" },
              h(ItemHeader, {
                id: story.id || "US" + (index + 1),
                title: persona && persona.name ? persona.name + " story" : "Untitled story",
                onRemove: function () { props.update(["user_stories"], stories.filter(function (_item, itemIndex) { return itemIndex !== index; })); }
              }),
              h("div", { className: "df-story-sentence", "aria-live": "polite" },
                "As ", h("strong", null, persona && persona.name ? persona.name : "a persona"),
                ", I need ", h("strong", null, story.need || "a capability"),
                ", so that ", h("strong", null, story.value || "I receive an outcome"), "."
              ),
              h("div", { className: "df-three-grid" },
                h(SelectField, {
                  id: "df-story-persona-" + index,
                  label: "Persona",
                  value: story.persona_id,
                  onChange: function (value) { props.update(["user_stories", index, "persona_id"], value); }
                },
                  h("option", { value: "" }, "Select persona"),
                  personas.map(function (item) { return h("option", { key: item.id, value: item.id }, item.name || item.id); })
                ),
                h(TextAreaField, {
                  id: "df-story-need-" + index,
                  label: "Need",
                  rows: 3,
                  value: story.need,
                  placeholder: "create and reopen one project",
                  onChange: function (value) { props.update(["user_stories", index, "need"], value); }
                }),
                h(TextAreaField, {
                  id: "df-story-value-" + index,
                  label: "Value",
                  rows: 3,
                  value: story.value,
                  placeholder: "my work survives a reload and remains private",
                  onChange: function (value) { props.update(["user_stories", index, "value"], value); }
                })
              ),
              h(LinesField, {
                id: "df-story-ac-" + index,
                label: "Happy-path acceptance criteria",
                required: true,
                value: story.acceptance_criteria,
                updateLines: function (values, lines) { return updateAcceptanceLines(values, lines, (story.id || "US") + "-H", "happy"); },
                rows: 4,
                placeholder: "Create returns an immutable project identifier\nReloading reopens the same project\nCross-owner read returns a deterministic denial",
                help: "Use visible behavior, not implementation tasks.",
                onChange: function (value) { props.update(["user_stories", index, "acceptance_criteria"], value); }
              }),
              h("div", { className: "df-two-grid" },
                h(LinesField, {
                  id: "df-story-negative-" + index,
                  label: "Negative / boundary acceptance",
                  required: true,
                  value: story.negative_acceptance,
                  updateLines: function (values, lines) { return updateAcceptanceLines(values, lines, (story.id || "US") + "-N", "negative"); },
                  rows: 4,
                  placeholder: "A second owner receives a deterministic denial and no content\nAn interrupted save can retry without creating a duplicate",
                  help: "At least one negative, recovery, boundary, or abuse criterion is required.",
                  onChange: function (value) { props.update(["user_stories", index, "negative_acceptance"], value); }
                }),
                h(LinesField, {
                  id: "df-story-paths-" + index,
                  label: "Likely file boundaries",
                  value: story.paths,
                  rows: 4,
                  placeholder: "src/domain/projects/**\ntests/projects/**",
                  help: "Optional, but useful for producing disjoint implementation slices.",
                  onChange: function (value) { props.update(["user_stories", index, "paths"], value); }
                })
              )
            )
          );
        }))
      )
    );
  }

  function DeliverySection(props) {
    var milestones = props.setup.milestones || [];

    function addMilestone() {
      var id = nextId("M", milestones);
      props.update(["milestones"], milestones.concat([Object.assign(blankMilestone(1), {
        id: id,
        acceptance: [{ id: id + "-A1", type: "happy", statement: "" }],
        acceptance_criteria: [{ id: id + "-A1", type: "happy", statement: "" }]
      })]));
    }

    return h("section", { className: "df-section", "aria-labelledby": "df-delivery-title" },
      h(SectionHeading, {
        eyebrow: "03 / Delivery contract",
        title: "Bound the work and define runnable gates",
        copy: "Non-goals prevent scope growth. Milestones mark meaningful capabilities, not phases such as ‘backend complete’."
      }),
      h("div", { className: "df-two-grid" },
        h(LinesField, {
          id: "df-non-goals",
          label: "Non-goals",
          value: props.setup.non_goals,
          rows: 5,
          placeholder: "Production deployment\nPaid provider usage\nMigration of existing customer data",
          help: "List outcomes the factory must not pursue.",
          onChange: function (value) { props.update(["non_goals"], value); }
        }),
        h(LinesField, {
          id: "df-constraints",
          label: "Constraints",
          value: props.setup.constraints,
          rows: 5,
          placeholder: "No production credentials\nAt most two parallel workers\nAll acceptance runs locally",
          help: "Include budget, timing, platform, policy, data, and human-in-the-loop limits.",
          onChange: function (value) { props.update(["constraints"], value); }
        })
      ),
      h("div", { className: "df-subsection" },
        h("div", { className: "df-subsection-title" },
          h("div", null, h("h3", null, "Milestones"), h("p", null, "Sequence independently demonstrable product capabilities.")),
          h(Button, { type: "button", variant: "outline", onClick: addMilestone }, "+ Add milestone")
        ),
        milestones.length === 0 ? h(EmptyState, {
          title: "No milestone gates yet",
          copy: "Add the first user-visible or system-visible capability the factory can prove.",
          action: "Add milestone",
          onAdd: addMilestone
        }) : h("div", { className: "df-stack" }, milestones.map(function (milestone, index) {
          return h(Card, { key: milestone.id || index, className: "df-item-card" },
            h(CardContent, { className: "df-item-content" },
              h(ItemHeader, {
                id: milestone.id || "M" + (index + 1),
                title: milestone.title || "Untitled milestone",
                onRemove: function () { props.update(["milestones"], milestones.filter(function (_item, itemIndex) { return itemIndex !== index; })); }
              }),
              h("div", { className: "df-two-grid" },
                h(Field, {
                  id: "df-milestone-title-" + index,
                  label: "Milestone name",
                  value: milestone.title,
                  placeholder: "Durable owner-scoped project journey",
                  onChange: function (value) { props.update(["milestones", index, "title"], value); }
                }),
                h(LinesField, {
                  id: "df-milestone-stories-" + index,
                  label: "Mapped user story IDs",
                  rows: 2,
                  value: milestone.story_ids,
                  placeholder: "US1\nUS2",
                  help: "Map each user story to exactly one milestone.",
                  onChange: function (value) { props.update(["milestones", index, "story_ids"], value); }
                })
              ),
              h(TextAreaField, {
                id: "df-milestone-outcome-" + index,
                label: "Runnable outcome",
                required: true,
                rows: 4,
                value: milestone.outcome,
                placeholder: "A verified owner creates, reloads, and reopens one persisted project without crossing an ownership boundary.",
                help: "Describe what can now be demonstrated at this gate.",
                onChange: function (value) { props.update(["milestones", index, "outcome"], value); }
              }),
              h("div", { className: "df-two-grid" },
                h(LinesField, {
                  id: "df-milestone-ac-" + index,
                  label: "Acceptance criteria",
                  required: true,
                  value: milestone.acceptance_criteria,
                  updateLines: function (values, lines) { return updateAcceptanceLines(values, lines, (milestone.id || "M") + "-A", "happy"); },
                  rows: 5,
                  placeholder: "The scripted journey creates and reopens the same project\nA second owner receives no project data",
                  help: "Every line should have a pass/fail observation.",
                  onChange: function (value) { props.update(["milestones", index, "acceptance_criteria"], value); }
                }),
                h(LinesField, {
                  id: "df-milestone-evidence-" + index,
                  label: "Evidence commands or scenarios",
                  required: true,
                  value: milestone.evidence,
                  rows: 5,
                  placeholder: "pytest tests/projects -q\nbrowser: create → reload → reopen → cross-owner denial",
                  help: "Name commands and realistic scenarios that produce raw receipts.",
                  onChange: function (value) { props.update(["milestones", index, "evidence"], value); }
                })
              )
            )
          );
        }))
      )
    );
  }

  function ValidationSection(props) {
    var plan = props.setup.test_plan || {};
    return h("section", { className: "df-section", "aria-labelledby": "df-validation-title" },
      h(SectionHeading, {
        eyebrow: "04 / Evidence contract",
        title: "Define acceptance before implementation",
        copy: "Focused tests guide builders. Integration, scenario, and recovery evidence decide whether a milestone is accepted."
      }),
      h("div", { className: "df-test-map" },
        h("article", { className: "df-test-kind" },
          h("span", { className: "df-test-number" }, "A"),
          h("div", null, h("strong", null, "Focused checks"), h("p", null, "Fast deterministic checks inside a slice. They guide implementation but do not accept a milestone."))
        ),
        h("article", { className: "df-test-kind" },
          h("span", { className: "df-test-number" }, "B"),
          h("div", null, h("strong", null, "Milestone evidence"), h("p", null, "Integrated commands and realistic user/system scenarios against a frozen candidate."))
        ),
        h("article", { className: "df-test-kind" },
          h("span", { className: "df-test-number" }, "C"),
          h("div", null, h("strong", null, "Negative and recovery"), h("p", null, "Prove denial, timeout, retry, rollback, and resume behavior—not only the happy path."))
        )
      ),
      h("div", { className: "df-two-grid" },
        h(LinesField, {
          id: "df-test-unit",
          label: "Focused / unit checks",
          required: true,
          value: plan.unit,
          rows: 6,
          placeholder: "pytest tests/domain/projects -q\nnpm test -- project-store",
          help: "Commands builders can run repeatedly within a slice.",
          onChange: function (value) { props.update(["test_plan", "unit"], value); }
        }),
        h(LinesField, {
          id: "df-test-integration",
          label: "Integration checks",
          required: true,
          value: plan.integration,
          rows: 6,
          placeholder: "pytest tests/integration/projects -q\nnpm run test:api",
          help: "Checks run after slices are integrated at a milestone gate.",
          onChange: function (value) { props.update(["test_plan", "integration"], value); }
        }),
        h(LinesField, {
          id: "df-test-acceptance",
          label: "Interaction scenarios: action => expected",
          required: true,
          value: plan.acceptance,
          updateLines: updateBrowserScenarioLines,
          rows: 6,
          placeholder: "Sign in, create, reload, and reopen the project => The same immutable project is visible after reload\nRequest the project as a second owner => The request is denied and no content is returned",
          help: "Separate each action and expected result with =>. Required when a web UI, mobile UI, desktop UI, or public API surface is declared.",
          onChange: function (value) { props.update(["test_plan", "acceptance"], value); }
        }),
        h(LinesField, {
          id: "df-test-recovery",
          label: "Held-out scenarios: given => when => then",
          required: true,
          value: plan.recovery,
          updateLines: updateHeldOutScenarioLines,
          rows: 6,
          placeholder: "A save is interrupted after persistence => The user retries the same action => Exactly one project exists and it reopens\nThe same evidence failure has repeated => The remediation budget is exhausted => Dispatch stops and requests a replan",
          help: "Separate Given, When, and Then with =>. These scenarios remain outside the builder’s acceptance authority.",
          onChange: function (value) { props.update(["test_plan", "recovery"], value); }
        }),
        h(LinesField, {
          id: "df-test-evidence",
          label: "Receipt requirements",
          value: plan.evidence,
          rows: 5,
          placeholder: "Candidate SHA and exact command\nExit code and raw artifact path\nCriterion IDs proven by the receipt",
          help: "Define what every milestone receipt must preserve.",
          onChange: function (value) { props.update(["test_plan", "evidence"], value); }
        })
      )
    );
  }

  function GovernanceSection(props) {
    var security = props.setup.security || {};
    var risks = props.setup.risks || [];
    var decisions = props.setup.decisions || [];

    function addRisk() {
      var id = nextId("T", risks);
      props.update(["risks"], risks.concat([Object.assign(blankRisk(1), { id: id })]));
    }

    function addDecision() {
      var id = nextId("D", decisions);
      props.update(["decisions"], decisions.concat([Object.assign(blankDecision(1), { id: id })]));
    }

    return h("section", { className: "df-section", "aria-labelledby": "df-governance-title" },
      h(SectionHeading, {
        eyebrow: "05 / Governance contract",
        title: "Make risk and authority explicit",
        copy: "Record sensitive surfaces, required controls, stop points, and decisions that workers are not allowed to reinterpret."
      }),
      h("div", { className: "df-principle" },
        h("strong", null, "Kryptonite adversarial gate — always on"),
        h("p", null, "Every slice requires a fresh verifier and adversary review. The builder cannot disable or self-approve this gate.")
      ),
      h("div", { className: "df-two-grid" },
        h(SelectField, {
          id: "df-data-classification",
          label: "Data classification",
          value: security.data_classification,
          help: "Personal, sensitive, and regulated data raise the mission risk and adversarial evidence requirements.",
          onChange: function (value) { props.update(["security", "data_classification"], value); }
        },
          h("option", { value: "none" }, "None"),
          h("option", { value: "internal" }, "Internal"),
          h("option", { value: "personal" }, "Personal"),
          h("option", { value: "sensitive" }, "Sensitive"),
          h("option", { value: "regulated" }, "Regulated")
        ),
        h(LinesField, {
          id: "df-risk-triggers",
          label: "Risk triggers",
          value: security.risk_triggers,
          rows: 4,
          placeholder: "authentication\ntenant isolation\nproduction deployment",
          help: "Use concrete surfaces such as authorization, personal data, payments, public tokens, migrations, secrets, publishing, or safeguarding.",
          onChange: function (value) { props.update(["security", "risk_triggers"], value); }
        })
      ),
      h("div", { className: "df-three-grid" },
        h(LinesField, {
          id: "df-security-data",
          label: "Data and trust boundaries",
          value: security.data,
          rows: 6,
          placeholder: "Project content is owner-scoped\nNo production customer data\nSession identity is server-authoritative",
          help: "Name personal, confidential, regulated, tenant, and credential boundaries.",
          onChange: function (value) { props.update(["security", "data"], value); }
        }),
        h(LinesField, {
          id: "df-security-controls",
          label: "Required controls",
          value: security.controls,
          rows: 6,
          placeholder: "Server-side ownership on every read\nNo secret values in logs or receipts\nNegative authorization test at each gate",
          help: "State the control and where evidence must prove it.",
          onChange: function (value) { props.update(["security", "controls"], value); }
        }),
        h(LinesField, {
          id: "df-security-gates",
          label: "Human decision gates",
          value: security.human_gates,
          rows: 6,
          placeholder: "Production deployment requires approval\nPublic publishing requires approval\nAny spend above the declared budget stops",
          help: "Include deployment, spend, public, security, and irreversible actions.",
          onChange: function (value) { props.update(["security", "human_gates"], value); }
        })
      ),
      h("div", { className: "df-subsection" },
        h("div", { className: "df-subsection-title" },
          h("div", null, h("h3", null, "Threat scenarios"), h("p", null, "Define the adversarial scenario, exposed surface, and observable control without placeholder authority values.")),
          h(Button, { type: "button", variant: "outline", onClick: addRisk }, "+ Add threat")
        ),
        risks.length === 0 ? h(EmptyState, {
          title: "No threat scenarios recorded",
          copy: "Add a substantive security, privacy, reliability, cost, or delivery threat and its expected control.",
          action: "Add threat",
          onAdd: addRisk
        }) : h("div", { className: "df-stack" }, risks.map(function (risk, index) {
          return h(Card, { key: risk.id || index, className: "df-item-card" },
            h(CardContent, { className: "df-item-content" },
              h(ItemHeader, {
                id: risk.id || "T" + (index + 1),
                title: risk.name || "Unspecified threat",
                onRemove: function () { props.update(["risks"], risks.filter(function (_item, itemIndex) { return itemIndex !== index; })); }
              }),
              h("div", { className: "df-risk-grid" },
                h(TextAreaField, {
                  id: "df-threat-id-" + index,
                  label: "Threat ID",
                  rows: 1,
                  value: risk.id,
                  placeholder: "T1",
                  onChange: function (value) { props.update(["risks", index, "id"], value); }
                }),
                h(TextAreaField, {
                  id: "df-threat-name-" + index,
                  label: "Name",
                  rows: 2,
                  value: risk.name,
                  placeholder: "Cross-owner project disclosure",
                  onChange: function (value) { props.update(["risks", index, "name"], value); }
                }),
                h(TextAreaField, {
                  id: "df-threat-scenario-" + index,
                  label: "Scenario",
                  rows: 3,
                  value: risk.scenario,
                  placeholder: "An authenticated user requests another owner's project by guessing its identifier.",
                  onChange: function (value) { props.update(["risks", index, "scenario"], value); }
                }),
                h(TextAreaField, {
                  id: "df-threat-surface-" + index,
                  label: "Attack surface",
                  rows: 2,
                  value: risk.attack_surface,
                  placeholder: "Project detail API",
                  onChange: function (value) { props.update(["risks", index, "attack_surface"], value); }
                }),
                h(TextAreaField, {
                  id: "df-threat-control-" + index,
                  label: "Expected control",
                  rows: 3,
                  value: risk.expected_control,
                  placeholder: "Server-side ownership rejects the request and a negative API scenario records the denial.",
                  onChange: function (value) { props.update(["risks", index, "expected_control"], value); }
                })
              )
            )
          );
        }))
      ),
      h("div", { className: "df-subsection" },
        h("div", { className: "df-subsection-title" },
          h("div", null, h("h3", null, "Decision log"), h("p", null, "Lock product and architecture authority before workers begin.")),
          h(Button, { type: "button", variant: "outline", onClick: addDecision }, "+ Add decision")
        ),
        decisions.length === 0 ? h(EmptyState, {
          title: "No decisions recorded",
          copy: "Add a decision workers must preserve or an open choice that blocks dispatch.",
          action: "Add decision",
          onAdd: addDecision
        }) : h("div", { className: "df-stack" }, decisions.map(function (decision, index) {
          return h(Card, { key: decision.id || index, className: "df-item-card" },
            h(CardContent, { className: "df-item-content" },
              h(ItemHeader, {
                id: decision.id || "D" + (index + 1),
                title: decision.statement || "Unspecified decision",
                onRemove: function () { props.update(["decisions"], decisions.filter(function (_item, itemIndex) { return itemIndex !== index; })); }
              }),
              h("div", { className: "df-decision-grid" },
                h(SelectField, {
                  id: "df-decision-status-" + index,
                  label: "Status",
                  value: decision.status,
                  onChange: function (value) { props.update(["decisions", index, "status"], value); }
                },
                  h("option", { value: "open" }, "Open — blocks affected work"),
                  h("option", { value: "locked" }, "Locked — must be preserved"),
                  h("option", { value: "superseded" }, "Superseded")
                ),
                h(TextAreaField, {
                  id: "df-decision-statement-" + index,
                  label: "Decision",
                  rows: 3,
                  value: decision.statement,
                  placeholder: "Server-side identity is the only authority for project ownership.",
                  onChange: function (value) { props.update(["decisions", index, "statement"], value); }
                }),
                h(TextAreaField, {
                  id: "df-decision-rationale-" + index,
                  label: "Rationale / consequence",
                  rows: 3,
                  value: decision.rationale,
                  placeholder: "Client-supplied owner IDs are ignored; all ownership tests use authenticated identity.",
                  onChange: function (value) { props.update(["decisions", index, "rationale"], value); }
                })
              )
            )
          );
        }))
      )
    );
  }

  function ModelsSection(props) {
    var providers = (Array.isArray(props.providers) ? props.providers : []).filter(function (provider) {
      return provider && provider.authenticated === true && typeof provider.slug === "string" && provider.slug.trim();
    });
    var models = props.setup.models || {};
    var execution = props.setup.execution || initialSetup().execution;
    var solAvailable = modelRefAvailable(providers, SOL_ORCHESTRATOR);
    var lunaAvailable = modelRefAvailable(providers, LUNA_WORKER);

    return h("section", { className: "df-section", "aria-labelledby": "df-models-title" },
      h(SectionHeading, {
        eyebrow: "06 / Runtime plan",
        title: "Choose the work graph and models by responsibility",
        copy: "Execution roles use authenticated models from the active profile. Independent review roles remain separate and must be chosen explicitly."
      }),
      h("div", { className: "df-subsection" },
        h("div", { className: "df-subsection-title" },
          h("div", null,
            h("h3", null, "Execution graph backend"),
            h("p", null, "Mission → milestone epics → functional-slice tasks. No micro-beads for test fixes or review comments.")
          )
        ),
        h("div", { className: "df-principle" },
          h("strong", null, "Two systems, separate authority"),
          h("p", null, "Beads owns the work graph while the Dark Factory ledger owns acceptance and evidence.")
        ),
        h("div", { className: "df-two-grid" },
          h(SelectField, {
            id: "df-execution-backend",
            label: "Execution backend",
            value: execution.graph_backend,
            help: "Beads is the default durable graph. Local is retained for prototype and compatibility workflows.",
            onChange: function (value) { props.update(["execution", "graph_backend"], value); }
          },
            h("option", { value: "beads" }, "Beads"),
            h("option", { value: "local" }, "Local — prototype / compatibility")
          ),
          h(Field, {
            id: "df-beads-directory",
            label: "Beads directory",
            value: execution.beads_directory,
            placeholder: "<workspace>/.beads",
            disabled: execution.graph_backend !== "beads",
            help: "Leave blank to use <workspace>/.beads, or select an existing isolated Beads directory.",
            onChange: function (value) { props.update(["execution", "beads_directory"], value); }
          })
        ),
        h("div", { className: "df-two-grid" },
          h(SelectField, {
            id: "df-graph-mode",
            label: "Graph mode",
            value: execution.graph_mode,
            help: "Plan is read-only. Apply enables the integrator-only apply tool after fail-closed preflight; compilation itself does not mutate Beads.",
            onChange: function (value) { props.update(["execution", "graph_mode"], value); }
          },
            h("option", { value: "plan" }, "Plan only"),
            h("option", { value: "apply" }, "Apply-enabled — manual tool")
          ),
          h(SelectField, {
            id: "df-orchestrator-reasoning",
            label: "Orchestrator reasoning effort",
            value: execution.reasoning_effort.orchestrator,
            help: "Applied to integrator/orchestrator dispatch descriptors.",
            onChange: function (value) { props.update(["execution", "reasoning_effort", "orchestrator"], value); }
          },
            h("option", { value: "low" }, "Low"),
            h("option", { value: "medium" }, "Medium"),
            h("option", { value: "high" }, "High")
          ),
          h(SelectField, {
            id: "df-worker-reasoning",
            label: "Worker reasoning effort",
            value: execution.reasoning_effort.worker,
            help: "Applied to builder/worker dispatch descriptors.",
            onChange: function (value) { props.update(["execution", "reasoning_effort", "worker"], value); }
          },
            h("option", { value: "low" }, "Low"),
            h("option", { value: "medium" }, "Medium"),
            h("option", { value: "high" }, "High")
          )
        ),
        h("label", { className: "df-principle", htmlFor: "df-beads-isolated-authorized", style: { display: "flex", alignItems: "flex-start", gap: "0.65rem", cursor: "pointer" } },
          h("input", {
            id: "df-beads-isolated-authorized",
            type: "checkbox",
            checked: execution.beads_isolated_authorized === true,
            disabled: execution.graph_backend !== "beads",
            onChange: function (event) { props.update(["execution", "beads_isolated_authorized"], event.target.checked); }
          }),
          h("span", null,
            h("strong", null, "Authorize this isolated Beads directory (off by default)"),
            h("p", null, "Enable only when this mission is explicitly authorized to use the selected isolated directory. Initialize it separately with bd init; this adapter never initializes stores.")
          )
        )
      ),
      h("div", { className: "df-subsection" },
        h("div", { className: "df-subsection-title" },
          h("div", null,
            h("h3", null, "Sol orchestrator + Luna worker preset"),
            h("p", null, "The preset covers execution roles only. Verifier, adversary, and holdout remain independent review selectors and are never changed to the builder model.")
          ),
          h(Button, {
            type: "button",
            variant: "outline",
            disabled: !solAvailable || !lunaAvailable,
            onClick: function () {
              props.update(["models"], applySolLunaPreset(models, providers));
              props.update(["model_policy"], { preset: SOL_LUNA_PRESET });
            }
          }, "Apply Sol orchestrator + Luna worker")
        ),
        (!solAvailable || !lunaAvailable) ? h("div", { className: "df-callout df-callout-warning", role: "status" },
          h("strong", null, "Preferred preset model unavailable"),
          h("p", null,
            !solAvailable ? "Orchestrator preference openai-codex/gpt-5.6-sol-900k is unavailable. Authenticate it or choose another model explicitly. " : "",
            !lunaAvailable ? "Worker preference openai-codex/gpt-5.6-luna is unavailable. Authenticate it or choose another model explicitly." : ""
          )
        ) : h("div", { className: "df-principle", role: "status" },
          h("strong", null, "Preferred execution pair available"),
          h("p", null, "Orchestrator / Integrator: openai-codex/gpt-5.6-sol-900k · Worker / Builder: openai-codex/gpt-5.6-luna")
        )
      ),
      providers.length === 0 ? h("div", { className: "df-callout df-callout-warning", role: "status" },
        h("strong", null, "No model options returned"),
        h("p", null, "Configure a Hermes provider, then reload this setup. No credential fields are collected here.")
      ) : null,
      h("div", { className: "df-model-grid" }, ROLES.map(function (role) {
        var assignment = models[role.key] || { provider: "", model: "" };
        var selectedProvider = providerFor(providers, assignment.provider);
        var availableModels = providerModels(selectedProvider);
        var authenticated = !!selectedProvider;
        return h(Card, { key: role.key, className: "df-model-card" },
          h(CardHeader, { className: "df-model-header" },
            h("div", null,
              h(CardTitle, { className: "df-model-title" }, role.label),
              h("p", null, role.help)
            ),
            assignment.provider ? h(Badge, {
              variant: "outline",
              className: authenticated ? "df-badge-ok" : "df-badge-warn"
            }, authenticated ? "Authenticated" : "Unavailable") : h(Badge, { variant: "outline" }, "Unassigned")
          ),
          h(CardContent, { className: "df-model-content" },
            h(SelectField, {
              id: "df-role-provider-" + role.key,
              label: "Provider",
              value: assignment.provider,
              help: "Only providers authenticated in the active profile are listed.",
              onChange: function (value) {
                var provider = providerFor(providers, value);
                var availableProviderModels = providerModels(provider);
                var firstModel = availableProviderModels[0] || "";
                if (["verifier", "adversary", "holdout"].indexOf(role.key) !== -1) {
                  var builder = models.builder || {};
                  firstModel = availableProviderModels.find(function (model) {
                    return value !== builder.provider || model !== builder.model;
                  }) || "";
                }
                props.update(["models", role.key], { provider: value, model: firstModel });
              }
            },
              h("option", { value: "" }, "Select provider"),
              providers.map(function (provider) {
                return h("option", {
                  key: provider.slug,
                  value: provider.slug,
                  disabled: provider.authenticated !== true
                }, provider.label || provider.slug);
              })
            ),
            h(SelectField, {
              id: "df-role-model-" + role.key,
              label: "Model",
              value: assignment.model,
              disabled: !authenticated || availableModels.length === 0,
              help: selectedProvider && availableModels.length === 0 ? "This provider did not return any models." : "Choose the exact model used for this role.",
              onChange: function (value) { props.update(["models", role.key, "model"], value); }
            },
              h("option", { value: "" }, authenticated ? "Select model" : "Select an authenticated provider first"),
              availableModels.map(function (model) { return h("option", { key: model, value: model }, model); })
            )
          )
        );
      }))
    );
  }

  function StepNavigation(props) {
    return h("nav", { className: "df-steps", "aria-label": "Setup steps" },
      h("ol", null, STEPS.map(function (step, index) {
        var complete = props.completion[step.id];
        return h("li", { key: step.id },
          h("button", {
            type: "button",
            className: classNames("df-step", props.active === index && "is-active", complete && "is-complete"),
            "aria-current": props.active === index ? "step" : undefined,
            onClick: function () { props.onChange(index); }
          },
            h("span", { className: "df-step-index", "aria-hidden": "true" }, complete ? "✓" : String(index + 1).padStart(2, "0")),
            h("span", { className: "df-step-text" },
              h("strong", null, step.label),
              h("small", null, step.hint)
            )
          )
        );
      }))
    );
  }

  function ReadinessPanel(props) {
    var readiness = props.readiness;
    var server = props.serverReadiness;
    var serverScore = server && typeof server.score === "number" ? server.score : null;
    var serverBlockers = server && Array.isArray(server.blockers) ? server.blockers.map(function (item) {
      return item && typeof item === "object" ? item.message || item.help || JSON.stringify(item) : String(item);
    }) : [];
    var serverWarnings = server && Array.isArray(server.warnings) ? server.warnings.map(function (item) {
      return item && typeof item === "object" ? item.message || item.help || JSON.stringify(item) : String(item);
    }) : [];
    return h("aside", { className: "df-readiness", "aria-labelledby": "df-readiness-title" },
      h(Card, { className: "df-readiness-card" },
        h(CardHeader, { className: "df-readiness-header" },
          h("div", null,
            h("p", { className: "df-eyebrow" }, "Deterministic preflight"),
            h(CardTitle, { id: "df-readiness-title" }, "Readiness")
          ),
          h("div", {
            className: classNames("df-score", readiness.ready && "is-ready"),
            role: "progressbar",
            "aria-valuemin": "0",
            "aria-valuemax": "100",
            "aria-valuenow": String(readiness.score),
            "aria-label": "Setup readiness " + readiness.score + " percent"
          }, h("strong", null, readiness.score), h("span", null, "%"))
        ),
        h(CardContent, { className: "df-readiness-content" },
          h("div", { className: "df-progress-track", "aria-hidden": "true" },
            h("div", { className: "df-progress-fill", style: { width: readiness.score + "%" } })
          ),
          h("p", { className: "df-check-count" }, readiness.passed + " of " + readiness.total + " readiness checks satisfied"),
          readiness.blockers.length ? h("div", { className: "df-findings df-findings-blockers" },
            h("div", { className: "df-findings-title" },
              h("strong", null, "Blockers"),
              h("span", null, readiness.blockers.length)
            ),
            h("ul", null, readiness.blockers.map(function (item, index) { return h("li", { key: index }, item); }))
          ) : h("div", { className: "df-clear-state" }, h("span", { "aria-hidden": "true" }, "✓"), h("span", null, "No local blockers")),
          readiness.warnings.length ? h("div", { className: "df-findings df-findings-warnings" },
            h("div", { className: "df-findings-title" },
              h("strong", null, "Warnings"),
              h("span", null, readiness.warnings.length)
            ),
            h("ul", null, readiness.warnings.map(function (item, index) { return h("li", { key: index }, item); }))
          ) : null,
          server ? h("div", { className: "df-server-check" },
            h("span", null, "Last server validation"),
            h("strong", null, serverScore !== null ? serverScore + "%" : (server.ready ? "Ready" : "Checked"))
          ) : null,
          serverBlockers.length ? h("details", { className: "df-server-findings" },
            h("summary", null, "Server blockers (" + serverBlockers.length + ")"),
            h("ul", null, serverBlockers.map(function (item, index) { return h("li", { key: index }, item); }))
          ) : null,
          serverWarnings.length ? h("details", { className: "df-server-findings" },
            h("summary", null, "Server warnings (" + serverWarnings.length + ")"),
            h("ul", null, serverWarnings.map(function (item, index) { return h("li", { key: index }, item); }))
          ) : null,
          props.compileResult && props.compileResult.manifest_path ? h("div", { className: "df-compiled" },
            h("span", null, "Compiled manifest"),
            h("code", null, props.compileResult.manifest_path)
          ) : null
        )
      )
    );
  }

  function completionMap(setup, providers) {
    var product = setup.product || {};
    var tests = setup.test_plan || {};
    var security = setup.security || {};
    return {
      mission: hasText(setup.workspace_path) && hasText(product.name, 2) && hasText(product.problem, 20) && hasText(product.outcome, 20) && hasText(product.context, 20) && hasLine(product.success_metrics) && (setup.project_mode !== "existing" || hasText(product.existing_system, 20)),
      users: (setup.personas || []).some(function (item) { return hasText(item.name) && hasText(item.needs); }) && (setup.user_stories || []).some(function (item) { return hasText(item.persona_id) && hasText(item.need) && hasLine(item.acceptance_criteria) && hasLine(item.negative_acceptance); }),
      delivery: hasLine(setup.non_goals) && hasLine(setup.constraints) && (setup.milestones || []).some(function (item) { return hasText(item.outcome, 12) && hasLine(item.story_ids) && hasLine(item.acceptance_criteria) && hasLine(item.evidence); }),
      validation: hasLine(tests.unit) && hasLine(tests.integration) && hasLine(tests.recovery),
      governance: hasLine(security.data) && hasLine(security.controls) && hasLine(security.human_gates) && (setup.risks || []).some(function (item) { return hasText(item.name) && hasText(item.scenario) && hasText(item.attack_surface) && hasText(item.expected_control); }) && (setup.decisions || []).some(function (item) { return hasText(item.statement) && item.status === "locked"; }),
      models: ROLES.every(function (role) {
        var assignment = setup.models && setup.models[role.key] || {};
        var provider = providerFor(providers, assignment.provider);
        return provider && providerModels(provider).indexOf(assignment.model) !== -1;
      }) && ["verifier", "adversary", "holdout"].every(function (role) {
        var assignment = setup.models && setup.models[role] || {};
        var builder = setup.models && setup.models.builder || {};
        return assignment.provider !== builder.provider || assignment.model !== builder.model;
      })
    };
  }

  function DarkFactoryPage() {
    var setupState = useState(initialSetup());
    var setup = setupState[0];
    var setSetup = setupState[1];
    var providersState = useState([]);
    var providers = providersState[0];
    var setProviders = providersState[1];
    var profileState = useState("");
    var profile = profileState[0];
    var setProfile = profileState[1];
    var serverReadinessState = useState(null);
    var serverReadiness = serverReadinessState[0];
    var setServerReadiness = serverReadinessState[1];
    var compileState = useState(null);
    var compileResult = compileState[0];
    var setCompileResult = compileState[1];
    var loadingState = useState(true);
    var loading = loadingState[0];
    var setLoading = loadingState[1];
    var errorState = useState("");
    var error = errorState[0];
    var setError = errorState[1];
    var statusState = useState(null);
    var status = statusState[0];
    var setStatus = statusState[1];
    var dirtyState = useState(false);
    var dirty = dirtyState[0];
    var setDirty = dirtyState[1];
    var busyState = useState("");
    var busy = busyState[0];
    var setBusy = busyState[1];
    var activeState = useState(0);
    var activeStep = activeState[0];
    var setActiveStep = activeState[1];
    var reloadState = useState(0);
    var reloadKey = reloadState[0];
    var setReloadKey = reloadState[1];

    useEffect(function () {
      var cancelled = false;
      setLoading(true);
      setError("");
      Promise.all([
        SDK.fetchJSON(API + "/setup"),
        SDK.fetchJSON(API + "/model-options")
      ]).then(function (responses) {
        if (cancelled) return;
        var setupResponse = responses[0] || {};
        var modelResponse = responses[1] || {};
        setProviders(Array.isArray(modelResponse.providers) ? modelResponse.providers : []);
        setProfile(setupResponse.profile || modelResponse.profile || "default");
        setServerReadiness(setupResponse.readiness || null);
        setSetup(normaliseSetup(setupResponse.setup || {}, modelResponse));
        setCompileResult(null);
        setDirty(false);
      }).catch(function (requestError) {
        if (!cancelled) setError(parseApiError(requestError));
      }).finally(function () {
        if (!cancelled) setLoading(false);
      });
      return function () { cancelled = true; };
    }, [reloadKey]);

    var update = useCallback(function (path, value) {
      setSetup(function (current) { return setAtPath(current, path, value); });
      setDirty(true);
      setStatus(null);
      setCompileResult(null);
    }, []);

    var readiness = useMemo(function () { return assessReadiness(setup, providers); }, [setup, providers]);
    var completion = useMemo(function () { return completionMap(setup, providers); }, [setup, providers]);

    function saveDraft() {
      setBusy("save");
      setStatus(null);
      SDK.fetchJSON(API + "/setup", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(toApiSetup(setup))
      }).then(function (response) {
        if (response && response.setup) setSetup(normaliseSetup(response.setup, { providers: providers }));
        setServerReadiness(response && response.readiness ? response.readiness : null);
        setProfile(response && response.profile ? response.profile : profile);
        setDirty(false);
        setStatus({ kind: "success", text: "Draft saved to the " + (response && response.profile || profile || "active") + " profile." });
      }).catch(function (requestError) {
        setStatus({ kind: "error", text: parseApiError(requestError) });
      }).finally(function () { setBusy(""); });
    }

    function compileSetup() {
      setBusy("compile");
      setStatus(null);
      setCompileResult(null);
      SDK.fetchJSON(API + "/compile", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(toApiSetup(setup))
      }).then(function (response) {
        setCompileResult(response || {});
        if (response && response.readiness) setServerReadiness(response.readiness);
        if (response && response.ready) {
          setDirty(false);
          setStatus({ kind: "success", text: "Validation passed and the mission manifest was compiled." });
        } else {
          setStatus({ kind: "warning", text: "Validation completed. Resolve the reported blockers before dispatch." });
        }
      }).catch(function (requestError) {
        var failure = parseApiFailure(requestError);
        if (failure.readiness) setServerReadiness(failure.readiness);
        setStatus({ kind: "error", text: failure.message });
      }).finally(function () { setBusy(""); });
    }

    function renderActiveSection() {
      var shared = { setup: setup, update: update };
      if (activeStep === 0) return h(MissionSection, shared);
      if (activeStep === 1) return h(UsersSection, shared);
      if (activeStep === 2) return h(DeliverySection, shared);
      if (activeStep === 3) return h(ValidationSection, shared);
      if (activeStep === 4) return h(GovernanceSection, shared);
      return h(ModelsSection, Object.assign({}, shared, { providers: providers }));
    }

    if (loading) {
      return h("div", { className: "df-page df-loading", "aria-busy": "true" },
        h("div", { className: "df-loading-mark", "aria-hidden": "true" }, "DF"),
        h("div", null, h("strong", null, "Loading factory setup"), h("p", null, "Reading the active profile and authenticated model options…"))
      );
    }

    if (error) {
      return h("div", { className: "df-page" },
        h(Card, { className: "df-load-error" },
          h(CardContent, { className: "df-load-error-content" },
            h("div", { className: "df-error-icon", "aria-hidden": "true" }, "!"),
            h("div", null,
              h("h1", null, "Dark Factory setup could not be loaded"),
              h("p", { role: "alert" }, error),
              h(Button, { type: "button", variant: "outline", onClick: function () { setReloadKey(reloadKey + 1); } }, "Retry")
            )
          )
        )
      );
    }

    var currentStep = STEPS[activeStep];
    return h("div", { className: "df-page", "aria-busy": !!busy },
      h("header", { className: "df-hero" },
        h("div", { className: "df-hero-copy" },
          h("div", { className: "df-hero-meta" },
            h("span", { className: "df-live-dot", "aria-hidden": "true" }),
            h("span", null, "Mission compiler"),
            h("span", { className: "df-meta-separator", "aria-hidden": "true" }, "/"),
            h("span", null, "Profile: " + (profile || "default"))
          ),
          h("h1", null, "Dark Factory"),
          h("p", null, "Turn a product brief into a bounded, evidence-driven mission before any autonomous work begins.")
        ),
        h("div", { className: "df-hero-actions" },
          dirty ? h(Badge, { variant: "outline", className: "df-unsaved" }, "Unsaved changes") : h(Badge, { variant: "outline", className: "df-saved" }, "Draft in sync"),
          h(Button, { type: "button", variant: "outline", disabled: !!busy, onClick: saveDraft }, busy === "save" ? "Saving…" : "Save Draft"),
          h(Button, { type: "button", disabled: !!busy, onClick: compileSetup }, busy === "compile" ? "Validating…" : "Validate / Compile")
        )
      ),
      status ? h("div", {
        className: classNames("df-status", "df-status-" + status.kind),
        role: status.kind === "error" ? "alert" : "status",
        "aria-live": "polite"
      },
        h("span", { className: "df-status-mark", "aria-hidden": "true" }, status.kind === "success" ? "✓" : status.kind === "warning" ? "!" : "×"),
        h("span", null, status.text)
      ) : null,
      h("div", { className: "df-workspace" },
        h("div", { className: "df-step-column" },
          h("div", { className: "df-step-label" }, "Setup sequence"),
          h(StepNavigation, { active: activeStep, onChange: setActiveStep, completion: completion }),
          h("div", { className: "df-principle" },
            h("strong", null, "Gate on evidence"),
            h("p", null, "A green build is an observation. A milestone advances only when every declared acceptance criterion has a receipt.")
          )
        ),
        h("main", { className: "df-main" },
          renderActiveSection(),
          h("div", { className: "df-section-nav" },
            h(Button, {
              type: "button",
              variant: "outline",
              disabled: activeStep === 0,
              onClick: function () { setActiveStep(Math.max(0, activeStep - 1)); }
            }, "← Previous"),
            h("span", null, (activeStep + 1) + " / " + STEPS.length + " · " + currentStep.label),
            activeStep < STEPS.length - 1 ? h(Button, {
              type: "button",
              onClick: function () { setActiveStep(Math.min(STEPS.length - 1, activeStep + 1)); }
            }, "Next →") : h(Button, { type: "button", onClick: compileSetup, disabled: !!busy }, busy === "compile" ? "Validating…" : "Validate / Compile")
          )
        ),
        h(ReadinessPanel, {
          readiness: readiness,
          serverReadiness: serverReadiness,
          compileResult: compileResult
        })
      )
    );
  }

  registry.register("dark-factory", DarkFactoryPage);
})();
