import {
  Button,
  Input,
  PALETTE_AREA,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  Textarea,
  host
} from '@hermes/plugin-sdk'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const PLUGIN_ID = 'dark-factory'
const ROUTE = '/dark-factory'
const PROJECT_ROUTE = `${ROUTE}/project`
const SETTINGS_ROUTE = `${ROUTE}/settings`
const SETUP_ROUTE = `${ROUTE}/setup`
const BEAD_ME_UP_URL = 'https://github.com/brendan-appstart/bead-me-up-scotty'
let selectedProjectId = ''
let openExternal = () => false
const SOL_LUNA_PRESET = 'sol-luna'
const SOL_ORCHESTRATOR = { provider: 'openai-codex', model: 'gpt-5.6-sol-900k' }
const LUNA_WORKER = { provider: 'openai-codex', model: 'gpt-5.6-luna' }
const ROLES = [
  ['integrator', 'Orchestrator / Integrator', 'Owns mission intent, shared contracts, integration, and milestone gates.'],
  ['builder', 'Worker / Builder', 'Implements complete functional blocks and focused checks.'],
  ['verifier', 'Verifier', 'Validates acceptance evidence independently from implementation.'],
  ['adversary', 'Adversary', 'Challenges security, failure, and boundary assumptions.'],
  ['holdout', 'Holdout', 'Judges milestone evidence with a fresh context and external oracle.']
]
const ACCEPTANCE_TYPES = ['happy', 'negative', 'recovery', 'boundary', 'abuse']
const DATA_CLASSES = ['none', 'internal', 'personal', 'sensitive', 'regulated']
const selectClass =
  'h-8 w-full rounded-[3px] border border-(--ui-stroke-secondary) bg-(--ui-bg-primary) px-2 text-xs text-foreground outline-none transition-colors focus:border-ring focus:ring-[0.1875rem] focus:ring-ring/40 disabled:cursor-not-allowed disabled:opacity-50'

const text = value => (typeof value === 'string' ? value : value == null ? '' : String(value))
const list = value => (Array.isArray(value) ? value : [])
const record = value => (value && typeof value === 'object' && !Array.isArray(value) ? value : {})
const cleanStringList = value => list(value).map(text)
const hasOwn = (value, key) => Boolean(value) && Object.prototype.hasOwnProperty.call(value, key)
const canonicalString = value => (typeof value === 'string' ? value.trim() : '')
const canonicalProviderSlug = value => canonicalString(value).toLowerCase()

function defaultPolicy(value) {
  const source = record(value)
  return {
    max_active_milestones: Number(source.max_active_milestones) || 1,
    max_parallel_slices: Number(source.max_parallel_slices) || 2,
    repeated_failure_limit: Number(source.repeated_failure_limit) || 2,
    max_remediation_cycles: Number(source.max_remediation_cycles) || 3
  }
}

function normaliseAcceptance(value, defaultId, defaultType = 'happy') {
  const source = record(value)
  const suppliedType = typeof source.type === 'string' ? source.type : ''
  return {
    id: hasOwn(source, 'id') ? (typeof source.id === 'string' ? source.id : '') : defaultId,
    type: hasOwn(source, 'type') && ACCEPTANCE_TYPES.includes(suppliedType) ? suppliedType : defaultType,
    statement: hasOwn(source, 'statement')
      ? (typeof source.statement === 'string' ? source.statement : '')
      : text(typeof value === 'string' ? value : '')
  }
}

function normaliseThreat(value, index) {
  const item = record(value)
  return {
    id: text(item.id) || `T${index + 1}`,
    name: text(item.name),
    scenario: text(item.scenario),
    attack_surface: text(item.attack_surface),
    expected_control: text(item.expected_control)
  }
}

function catalogModelRefs(catalog) {
  return list(record(catalog).providers)
    .flatMap(value => {
      const provider = record(value)
      const providerSlug = canonicalProviderSlug(provider.slug)
      if (provider.authenticated !== true || !providerSlug) return []
      return list(provider.models)
        .map(model => ({ provider: providerSlug, model: canonicalString(model) }))
        .filter(item => item.model)
    })
    .sort((left, right) => `${left.provider}/${left.model}`.localeCompare(`${right.provider}/${right.model}`))
}

function modelRefAvailable(catalog, reference) {
  return catalogModelRefs(catalog).some(item => item.provider === reference.provider && item.model === reference.model)
}

function applySolLunaPreset(models, catalog) {
  const integrator = record(models.integrator)
  const builder = record(models.builder)
  return {
    ...models,
    integrator:
      !text(integrator.provider) && !text(integrator.model) && modelRefAvailable(catalog, SOL_ORCHESTRATOR)
        ? { ...SOL_ORCHESTRATOR }
        : models.integrator,
    builder:
      !text(builder.provider) && !text(builder.model) && modelRefAvailable(catalog, LUNA_WORKER)
        ? { ...LUNA_WORKER }
        : models.builder
  }
}

function normaliseSetup(value, catalog = {}) {
  const source = record(value)
  const product = record(source.product)
  const testing = record(source.testing)
  const security = record(source.security)
  const models = record(source.models)
  const modelPolicy = record(source.model_policy)
  const systemPrompts = record(source.system_prompts)
  const execution = record(source.execution)
  const reasoningEffort = record(execution.reasoning_effort)
  const exactModels = {}

  for (const [role] of ROLES) {
    const assignment = record(models[role])
    exactModels[role] = { provider: text(assignment.provider).toLowerCase(), model: text(assignment.model) }
  }
  for (const [role, preferred] of [['integrator', SOL_ORCHESTRATOR], ['builder', LUNA_WORKER]]) {
    const incoming = record(models[role])
    const explicit = Boolean(text(incoming.provider) || text(incoming.model))
    if (!explicit && !exactModels[role].provider && !exactModels[role].model && modelRefAvailable(catalog, preferred)) {
      exactModels[role] = { ...preferred }
    }
  }

  return {
    intake_schema_version: 1,
    project_mode: source.project_mode === 'greenfield' ? 'greenfield' : 'existing',
    workspace_path: text(source.workspace_path),
    product: {
      name: text(product.name),
      problem: text(product.problem),
      outcome: text(product.outcome),
      context: text(product.context),
      existing_system: text(product.existing_system),
      success_metrics: cleanStringList(product.success_metrics),
      surfaces: cleanStringList(product.surfaces)
    },
    context: { ...record(source.context) },
    personas: list(source.personas).map((value, index) => {
      const item = record(value)
      return {
        id: text(item.id) || `P${index + 1}`,
        name: text(item.name),
        context: text(item.context),
        need: text(item.need)
      }
    }),
    user_stories: list(source.user_stories).map((value, index) => {
      const item = record(value)
      const storyId = text(item.id) || `US${index + 1}`
      return {
        id: storyId,
        persona_id: text(item.persona_id),
        want: text(item.want),
        so_that: text(item.so_that),
        acceptance: list(item.acceptance).map((criterion, criterionIndex) =>
          normaliseAcceptance(criterion, `${storyId}-A${criterionIndex + 1}`)
        ),
        paths: cleanStringList(item.paths)
      }
    }),
    non_goals: cleanStringList(source.non_goals),
    constraints: cleanStringList(source.constraints),
    milestones: list(source.milestones).map((value, index) => {
      const item = record(value)
      const milestoneId = text(item.id) || `M${index + 1}`
      return {
        id: milestoneId,
        title: text(item.title),
        outcome: text(item.outcome),
        story_ids: cleanStringList(item.story_ids),
        evidence: cleanStringList(item.evidence),
        acceptance: list(item.acceptance).map((criterion, criterionIndex) =>
          normaliseAcceptance(criterion, `${milestoneId}-A${criterionIndex + 1}`)
        )
      }
    }),
    testing: {
      focused_commands: cleanStringList(testing.focused_commands),
      integration_commands: cleanStringList(testing.integration_commands),
      browser_scenarios: list(testing.browser_scenarios).map(value => {
        const item = record(value)
        return { name: text(item.name), action: text(item.action), expected: text(item.expected) }
      }),
      held_out_scenarios: list(testing.held_out_scenarios).map(value => {
        const item = record(value)
        return {
          name: text(item.name),
          given: text(item.given),
          when: text(item.when),
          then: text(item.then)
        }
      }),
      evidence_requirements: cleanStringList(testing.evidence_requirements)
    },
    security: {
      data_classification: DATA_CLASSES.includes(text(security.data_classification).toLowerCase())
        ? text(security.data_classification).toLowerCase()
        : 'none',
      adversarial_lens: text(security.adversarial_lens) || 'kryptonite',
      risk_triggers: cleanStringList(security.risk_triggers),
      data: cleanStringList(security.data),
      controls: cleanStringList(security.controls),
      human_gates: cleanStringList(security.human_gates),
      threat_scenarios: list(security.threat_scenarios).map(normaliseThreat),
      authority_decisions: list(security.authority_decisions).map((value, index) => {
        const item = record(value)
        return {
          id: text(item.id) || `D${index + 1}`,
          statement: text(item.statement),
          status: text(item.status).toLowerCase() === 'locked' ? 'locked' : 'open',
          rationale: text(item.rationale)
        }
      })
    },
    models: exactModels,
    model_policy: { preset: text(modelPolicy.preset) || SOL_LUNA_PRESET },
    system_prompts: Object.fromEntries(ROLES.map(([role]) => [role, text(systemPrompts[role])])),
    execution: {
      graph_backend: 'beads',
      graph_mode: text(execution.graph_mode) || 'plan',
      beads_directory: text(execution.beads_directory || execution.beads_dir),
      beads_isolated_authorized: execution.beads_isolated_authorized === true || execution.allow_init === true,
      reasoning_effort: {
        orchestrator: text(reasoningEffort.orchestrator) || 'high',
        worker: text(reasoningEffort.worker) || 'medium'
      }
    },
    policy: defaultPolicy(source.policy)
  }
}

function serialiseSetup(value, catalog = {}) {
  return normaliseSetup(value, catalog)
}

function normaliseCatalog(value) {
  const source = record(value)
  return {
    profile: text(source.profile) || 'default',
    current: {
      provider: canonicalProviderSlug(record(source.current).provider),
      model: canonicalString(record(source.current).model)
    },
    providers: list(source.providers)
      .filter(value => record(value).authenticated === true)
      .map(value => {
        const item = record(value)
        return {
          slug: canonicalProviderSlug(item.slug),
          label: text(item.label || item.name || item.slug),
          authenticated: item.authenticated,
          models: list(item.models)
            .map(canonicalString)
            .filter(Boolean)
        }
      })
      .filter(provider => provider.slug && provider.models.length)
  }
}

function defaultConfig() {
  return {
    schema_version: 1,
    models: Object.fromEntries(ROLES.map(([role]) => [role, { provider: '', model: '' }])),
    model_policy: { preset: SOL_LUNA_PRESET },
    system_prompts: Object.fromEntries(ROLES.map(([role]) => [role, ''])),
    coordination: {
      mode: 'beads',
      beads_directory: '',
      beads_isolated_authorized: false
    },
    reasoning_effort: { orchestrator: 'high', worker: 'medium' },
    policy: defaultPolicy({})
  }
}

function normaliseConfig(value) {
  const source = record(value)
  const defaults = defaultConfig()
  const models = record(source.models)
  const prompts = record(source.system_prompts)
  const coordination = record(source.coordination)
  const reasoning = record(source.reasoning_effort)
  return {
    schema_version: 1,
    models: Object.fromEntries(ROLES.map(([role]) => {
      const assignment = record(models[role])
      return [role, { provider: canonicalProviderSlug(assignment.provider), model: canonicalString(assignment.model) }]
    })),
    model_policy: { preset: canonicalString(record(source.model_policy).preset) || defaults.model_policy.preset },
    system_prompts: Object.fromEntries(ROLES.map(([role]) => [role, text(prompts[role])])),
    coordination: {
      mode: 'beads',
      beads_directory: text(coordination.beads_directory),
      beads_isolated_authorized: coordination.beads_isolated_authorized === true
    },
    reasoning_effort: {
      orchestrator: ['low', 'medium', 'high'].includes(text(reasoning.orchestrator).toLowerCase()) ? text(reasoning.orchestrator).toLowerCase() : defaults.reasoning_effort.orchestrator,
      worker: ['low', 'medium', 'high'].includes(text(reasoning.worker).toLowerCase()) ? text(reasoning.worker).toLowerCase() : defaults.reasoning_effort.worker
    },
    policy: defaultPolicy(source.policy)
  }
}

function modelValue(assignment) {
  const value = record(assignment)
  return value.provider && value.model ? `${value.provider}::${value.model}` : ''
}

function modelAssignment(value) {
  const [provider, ...modelParts] = text(value).split('::')
  return { provider: provider || '', model: modelParts.join('::') || '' }
}

function modelEditor({ models, catalog, onChange }) {
  const refs = catalogModelRefs(catalog)
  const options = [
    jsx('option', { value: '', children: 'Automatic / preset default' }),
    ...refs.map(item => jsx('option', { value: `${item.provider}::${item.model}`, children: `${item.provider} / ${item.model}` }))
  ]
  return jsxs('div', {
    className: 'grid gap-3 md:grid-cols-2',
    children: ROLES.map(([role, label, hint]) => jsx(Field, {
      label,
      hint,
      children: jsx('select', {
        className: selectClass,
        value: modelValue(record(models)[role]),
        onChange: event => onChange({ ...record(models), [role]: modelAssignment(event.target.value) }),
        children: options
      })
    }))
  })
}

function promptEditor({ prompts, onChange }) {
  return jsxs('div', {
    className: 'grid gap-3 lg:grid-cols-2',
    children: ROLES.map(([role, label, hint]) => jsx(Field, {
      label: `${label} system prompt`,
      hint: `${hint} Leave blank to use the role's runtime default.`,
      children: jsx(Textarea, {
        className: 'min-h-24 resize-y',
        value: text(record(prompts)[role]),
        maxLength: 16000,
        onChange: event => onChange({ ...record(prompts), [role]: event.target.value })
      })
    }))
  })
}

function coordinationEditor({ coordination, onChange }) {
  const current = record(coordination)
  const update = (key, value) => onChange({ ...current, [key]: value })
  return jsxs('div', {
    className: 'grid gap-3 md:grid-cols-2',
    children: [
      jsx(Field, {
        label: 'Coordination mode',
        hint: 'Beads is the required canonical project ledger for Dark Factory graph writes.',
        children: jsx('select', {
          className: selectClass,
          value: 'beads',
          disabled: true,
          'aria-label': 'Coordination mode',
          children: [jsx('option', { value: 'beads', children: 'Beads graph (required)' })]
        })
      }),
      jsx(Field, {
        label: 'Beads directory',
        hint: 'Blank resolves to <workspace>/.beads. Relative paths are workspace-scoped.',
        children: jsx(Input, { value: text(current.beads_directory), placeholder: '.beads', 'aria-label': 'Beads directory', onChange: event => update('beads_directory', event.target.value) })
      }),
      jsx(Field, {
        label: 'Authorize Beads graph writes',
        hint: 'Required before any project can compile or initialize its Beads graph.',
        children: jsxs('label', {
          className: 'flex h-8 items-center gap-2 text-xs text-(--ui-text-secondary)',
          children: [
            jsx('input', { type: 'checkbox', checked: current.beads_isolated_authorized === true, 'aria-label': 'Authorize Beads graph writes', onChange: event => update('beads_isolated_authorized', event.target.checked) }),
            'I explicitly authorize Beads graph writes'
          ]
        })
      })
    ]
  })
}

function setAtPath(source, path, value) {
  if (!path.length) return value
  const [key, ...rest] = path
  const clone = Array.isArray(source) ? source.slice() : { ...record(source) }
  clone[key] = setAtPath(source == null ? undefined : source[key], rest, value)
  return clone
}

function nextId(rows, prefix) {
  const used = new Set(list(rows).map(row => text(record(row).id)))
  let index = 1
  while (used.has(`${prefix}${index}`)) index += 1
  return `${prefix}${index}`
}

function errorText(error) {
  if (error instanceof Error && error.message) return error.message
  if (typeof error === 'string') return error
  return 'The Dark Factory backend did not complete the request.'
}

function Field({ label, hint, children, className = '' }) {
  return jsxs('label', {
    className: `flex min-w-0 flex-col gap-1 ${className}`,
    children: [
      jsx('span', { className: 'text-xs font-medium text-(--ui-text-primary)', children: label }),
      hint ? jsx('span', { className: 'text-[0.6875rem] leading-4 text-(--ui-text-tertiary)', children: hint }) : null,
      children
    ]
  })
}

function Section({ id, step, title, description, children }) {
  return jsxs('section', {
    id,
    className: 'scroll-mt-4 rounded-[6px] border border-(--ui-stroke-secondary) bg-(--ui-bg-secondary) p-4',
    children: [
      jsxs('div', {
        className: 'mb-4 flex items-start gap-3',
        children: [
          jsx('span', {
            className:
              'inline-flex size-6 shrink-0 items-center justify-center rounded-full bg-primary text-[0.6875rem] font-semibold text-primary-foreground',
            children: step
          }),
          jsxs('div', {
            className: 'min-w-0',
            children: [
              jsx('h2', { className: 'text-sm font-semibold text-(--ui-text-primary)', children: title }),
              jsx('p', { className: 'mt-0.5 text-xs leading-5 text-(--ui-text-tertiary)', children: description })
            ]
          })
        ]
      }),
      children
    ]
  })
}

function EmptyRows({ children }) {
  return jsx('div', {
    className: 'rounded-[4px] border border-dashed border-(--ui-stroke-secondary) px-3 py-4 text-center text-xs text-(--ui-text-tertiary)',
    children
  })
}

function RowHeader({ title, onRemove, removeLabel }) {
  return jsxs('div', {
    className: 'mb-3 flex items-center justify-between gap-3',
    children: [
      jsx('h3', { className: 'text-xs font-semibold text-(--ui-text-secondary)', children: title }),
      jsx(Button, { type: 'button', variant: 'ghost', size: 'xs', onClick: onRemove, children: removeLabel || 'Remove' })
    ]
  })
}

function StringList({ values, onChange, addLabel, placeholder, multiline = false }) {
  const rows = list(values)
  const Control = multiline ? Textarea : Input
  return jsxs('div', {
    className: 'flex flex-col gap-2',
    children: [
      rows.length
        ? rows.map((value, index) =>
            jsxs(
              'div',
              {
                className: 'flex items-start gap-2',
                children: [
                  jsx(Control, {
                    className: multiline ? 'min-h-16 flex-1 resize-y' : 'flex-1',
                    value: text(value),
                    placeholder,
                    onChange: event => {
                      const next = rows.slice()
                      next[index] = event.target.value
                      onChange(next)
                    }
                  }),
                  jsx(Button, {
                    type: 'button',
                    variant: 'ghost',
                    size: 'xs',
                    onClick: () => onChange(rows.filter((_, rowIndex) => rowIndex !== index)),
                    children: 'Remove'
                  })
                ]
              },
              index
            )
          )
        : jsx(EmptyRows, { children: 'No entries yet.' }),
      jsx(Button, {
        type: 'button',
        variant: 'secondary',
        size: 'sm',
        className: 'self-start',
        onClick: () => onChange([...rows, '']),
        children: addLabel
      })
    ]
  })
}

function ModelAssignment({ role, label, help, assignment, catalog, builder, onChange }) {
  const providers = catalog.providers
  const provider = providers.find(item => item.slug === assignment.provider)
  const duplicateBuilder =
    role !== 'builder' &&
    ['verifier', 'adversary', 'holdout'].includes(role) &&
    assignment.provider &&
    assignment.provider === builder.provider &&
    assignment.model &&
    assignment.model === builder.model

  return jsxs('div', {
    className: 'rounded-[4px] border border-(--ui-stroke-secondary) bg-(--ui-bg-primary) p-3',
    children: [
      jsxs('div', {
        className: 'mb-3',
        children: [
          jsx('h3', { className: 'text-xs font-semibold text-(--ui-text-primary)', children: label }),
          jsx('p', { className: 'mt-0.5 text-[0.6875rem] leading-4 text-(--ui-text-tertiary)', children: help })
        ]
      }),
      jsxs('div', {
        className: 'grid gap-3 md:grid-cols-2',
        children: [
          jsx(Field, {
            label: 'Authenticated provider',
            children: jsxs('select', {
              className: selectClass,
              value: assignment.provider,
              onChange: event => {
                const nextProvider = providers.find(item => item.slug === event.target.value)
                const nextModels = nextProvider?.models || []
                const nextModel = ['verifier', 'adversary', 'holdout'].includes(role)
                  ? nextModels.find(model => event.target.value !== builder.provider || model !== builder.model) || ''
                  : nextModels[0] || ''
                onChange({ provider: event.target.value, model: nextModel })
              },
              children: [
                jsx('option', { value: '', children: 'Choose provider' }),
                ...providers.map(item =>
                  jsx('option', { value: item.slug, children: item.label || item.slug }, item.slug)
                )
              ]
            })
          }),
          jsx(Field, {
            label: 'Model',
            children: jsxs('select', {
              className: selectClass,
              value: assignment.model,
              disabled: !provider,
              onChange: event => onChange({ ...assignment, model: event.target.value }),
              children: [
                jsx('option', { value: '', children: provider ? 'Choose model' : 'Choose a provider first' }),
                ...(provider?.models || []).map(model => jsx('option', { value: model, children: model }, model))
              ]
            })
          })
        ]
      }),
      duplicateBuilder
        ? jsx('p', {
            className: 'mt-2 text-[0.6875rem] leading-4 text-destructive',
            children: `${label} must use a different model from Builder.`
          })
        : null
    ]
  })
}

function ReadinessPanel({ readiness, dirty, compileResult }) {
  if (!readiness) {
    return jsx('aside', {
      className: 'rounded-[6px] border border-(--ui-stroke-secondary) bg-(--ui-bg-secondary) p-4 text-xs text-(--ui-text-tertiary)',
      children: 'Readiness appears after the setup loads.'
    })
  }

  const blockers = list(readiness.blockers)
  const warnings = list(readiness.warnings)
  const score = Math.max(0, Math.min(100, Number(readiness.score) || 0))

  return jsxs('aside', {
    className: 'flex flex-col gap-3 xl:sticky xl:top-3 xl:max-h-[calc(100vh-1.5rem)] xl:overflow-y-auto',
    children: [
      jsxs('div', {
        className: 'rounded-[6px] border border-(--ui-stroke-secondary) bg-(--ui-bg-secondary) p-4',
        children: [
          jsxs('div', {
            className: 'flex items-start justify-between gap-3',
            children: [
              jsxs('div', {
                children: [
                  jsx('p', { className: 'text-[0.6875rem] font-medium uppercase tracking-wide text-(--ui-text-tertiary)', children: 'Readiness' }),
                  jsx('p', { className: 'mt-1 text-3xl font-semibold tabular-nums text-(--ui-text-primary)', children: `${score}%` })
                ]
              }),
              jsx('span', {
                className: readiness.ready
                  ? 'rounded-full bg-primary/10 px-2 py-1 text-[0.6875rem] font-medium text-primary'
                  : 'rounded-full bg-destructive/10 px-2 py-1 text-[0.6875rem] font-medium text-destructive',
                children: readiness.ready ? 'Ready' : `${blockers.length} blocker${blockers.length === 1 ? '' : 's'}`
              })
            ]
          }),
          jsx('div', {
            className: 'mt-3 h-1.5 overflow-hidden rounded-full bg-(--ui-bg-quaternary)',
            children: jsx('div', { className: 'h-full rounded-full bg-primary transition-[width]', style: { width: `${score}%` } })
          }),
          jsxs('div', {
            className: 'mt-3 flex items-center justify-between text-[0.6875rem] text-(--ui-text-tertiary)',
            children: [jsx('span', { children: `Risk ${text(readiness.risk) || '—'}` }), jsx('span', { children: dirty ? 'Unsaved changes' : 'Server-validated draft' })]
          }),
          dirty
            ? jsx('p', {
                className: 'mt-3 rounded-[4px] bg-(--ui-bg-quaternary) p-2 text-[0.6875rem] leading-4 text-(--ui-text-secondary)',
                children: 'Save Draft to validate these changes and refresh the readiness score.'
              })
            : null
        ]
      }),
      list(readiness.sections).length
        ? jsxs('div', {
            className: 'rounded-[6px] border border-(--ui-stroke-secondary) bg-(--ui-bg-secondary) p-3',
            children: [
              jsx('h3', { className: 'mb-2 text-xs font-semibold text-(--ui-text-primary)', children: 'Gate coverage' }),
              ...list(readiness.sections).map(value => {
                const item = record(value)
                return jsxs(
                  'div',
                  {
                    className: 'flex items-center justify-between gap-3 border-t border-(--ui-stroke-secondary) py-2 first:border-t-0',
                    children: [
                      jsx('span', { className: 'text-xs capitalize text-(--ui-text-secondary)', children: text(item.id) }),
                      jsx('span', {
                        className: item.ready ? 'text-[0.6875rem] text-primary' : 'text-[0.6875rem] text-(--ui-text-tertiary)',
                        children: `${Number(item.passed) || 0}/${Number(item.total) || 0}`
                      })
                    ]
                  },
                  text(item.id)
                )
              })
            ]
          })
        : null,
      blockers.length
        ? jsxs('div', {
            className: 'rounded-[6px] border border-destructive/30 bg-destructive/5 p-3',
            children: [
              jsx('h3', { className: 'mb-2 text-xs font-semibold text-destructive', children: 'Blocking preflight items' }),
              ...blockers.map((value, index) => {
                const item = record(value)
                return jsxs(
                  'div',
                  {
                    className: 'border-t border-destructive/20 py-2 first:border-t-0',
                    children: [
                      jsx('p', { className: 'text-xs font-medium text-(--ui-text-primary)', children: text(item.message) }),
                      item.help ? jsx('p', { className: 'mt-1 text-[0.6875rem] leading-4 text-(--ui-text-tertiary)', children: text(item.help) }) : null,
                      item.path ? jsx('code', { className: 'mt-1 block break-all text-[0.625rem] text-(--ui-text-tertiary)', children: text(item.path) }) : null
                    ]
                  },
                  text(item.code) || index
                )
              })
            ]
          })
        : null,
      warnings.length
        ? jsxs('div', {
            className: 'rounded-[6px] border border-(--ui-stroke-secondary) bg-(--ui-bg-secondary) p-3',
            children: [
              jsx('h3', { className: 'mb-2 text-xs font-semibold text-(--ui-text-primary)', children: 'Warnings' }),
              ...warnings.map((value, index) => {
                const item = record(value)
                return jsxs(
                  'div',
                  {
                    className: 'border-t border-(--ui-stroke-secondary) py-2 first:border-t-0',
                    children: [
                      jsx('p', { className: 'text-xs text-(--ui-text-secondary)', children: text(item.message) }),
                      item.help ? jsx('p', { className: 'mt-1 text-[0.6875rem] leading-4 text-(--ui-text-tertiary)', children: text(item.help) }) : null
                    ]
                  },
                  text(item.code) || index
                )
              })
            ]
          })
        : null,
      compileResult
        ? jsxs('div', {
            className: 'rounded-[6px] border border-primary/30 bg-primary/5 p-3',
            children: [
              jsx('h3', { className: 'text-xs font-semibold text-primary', children: 'Factory armed' }),
              jsx('p', { className: 'mt-2 break-all text-[0.6875rem] leading-4 text-(--ui-text-secondary)', children: `Manifest: ${text(compileResult.manifest_path)}` }),
              jsx('p', { className: 'mt-1 break-all text-[0.6875rem] leading-4 text-(--ui-text-secondary)', children: `State: ${text(compileResult.state_path)}` })
            ]
          })
        : null
    ]
  })
}

function statusBadge(status) {
  const value = text(status) || 'unknown'
  const tone = value === 'completed'
    ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
    : value === 'active'
      ? 'border-sky-500/30 bg-sky-500/10 text-sky-300'
      : value === 'blocked' || value === 'error'
        ? 'border-destructive/30 bg-destructive/10 text-destructive'
        : 'border-(--ui-stroke-secondary) bg-(--ui-bg-secondary) text-(--ui-text-secondary)'
  return jsx('span', { className: `inline-flex items-center rounded-full border px-2 py-0.5 text-[0.6875rem] font-medium uppercase tracking-wide ${tone}`, children: value.replaceAll('_', ' ') })
}

function metric(label, value, hint) {
  return jsxs('div', {
    className: 'rounded-[5px] border border-(--ui-stroke-secondary) bg-(--ui-bg-secondary) p-3',
    children: [
      jsx('div', { className: 'text-[0.6875rem] uppercase tracking-wide text-(--ui-text-tertiary)', children: label }),
      jsx('div', { className: 'mt-1 text-lg font-semibold text-(--ui-text-primary)', children: value }),
      hint ? jsx('div', { className: 'mt-1 text-[0.6875rem] text-(--ui-text-tertiary)', children: hint }) : null
    ]
  })
}

function FactoryProjectsPage({ rest }) {
  const [projects, setProjects] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [newName, setNewName] = useState('')
  const [newPath, setNewPath] = useState('')
  const [creating, setCreating] = useState(false)
  const [reloadToken, setReloadToken] = useState(0)

  useEffect(() => {
    let live = true
    setLoading(true)
    rest('/projects')
      .then(payload => {
        if (!live) return
        const value = record(payload)
        setProjects(list(value.projects))
        setError('')
      })
      .catch(cause => {
        if (live) setError(errorText(cause))
      })
      .finally(() => {
        if (live) setLoading(false)
      })
    return () => { live = false }
  }, [rest, reloadToken])

  const createProject = async () => {
    const name = text(newName).trim()
    const path = text(newPath).trim()
    if (!name || !path) {
      setError('Provide both a project name and an existing workspace path.')
      return
    }
    setCreating(true)
    setError('')
    try {
      const profile = text(host.state.profile?.get?.()) || 'default'
      const response = record(await host.request('projects.create', {
        name,
        folders: [path],
        primary_path: path,
        use: true,
        profile
      }))
      const created = record(response.project || response)
      if (!text(created.id)) throw new Error('Hermes did not return the created project id.')
      selectedProjectId = text(created.id)
      host.notify({ kind: 'success', message: `Project ${text(created.name) || name} is ready for Dark Factory configuration.` })
      host.navigate(PROJECT_ROUTE)
    } catch (cause) {
      const message = errorText(cause)
      setError(message)
      host.notify({ kind: 'error', title: 'Could not create Hermes project', message })
    } finally {
      setCreating(false)
    }
  }

  const openProject = project => {
    selectedProjectId = text(record(project).id)
    host.navigate(PROJECT_ROUTE)
  }

  if (loading) {
    return jsxs('div', { className: 'flex h-full items-center justify-center p-8 text-sm text-(--ui-text-tertiary)', children: [jsx('span', { className: 'mr-2 inline-block size-3 animate-spin rounded-full border-2 border-(--ui-stroke-secondary) border-t-primary' }), 'Loading Dark Factory projects…'] })
  }

  return jsxs('div', {
    className: 'h-full overflow-y-auto bg-(--ui-bg-primary) text-foreground',
    children: [
      jsxs('header', {
        className: 'sticky top-0 z-10 border-b border-(--ui-stroke-secondary) bg-(--ui-bg-primary)/95 px-5 py-3 backdrop-blur',
        children: [
          jsxs('div', { className: 'mx-auto flex max-w-[92rem] flex-wrap items-center justify-between gap-3', children: [
            jsxs('div', { children: [jsx('h1', { className: 'text-base font-semibold text-(--ui-text-primary)', children: 'Dark Factory projects' }), jsx('p', { className: 'mt-0.5 text-xs text-(--ui-text-tertiary)', children: 'Return here for every build. Each Hermes project keeps its own intake, coordination choices, progress, and evidence.' })] }),
            jsxs('div', { className: 'flex items-center gap-2', children: [jsx(Button, { type: 'button', variant: 'secondary', onClick: () => host.navigate(SETTINGS_ROUTE), children: 'Global defaults' }), jsx(Button, { type: 'button', variant: 'ghost', onClick: () => setReloadToken(value => value + 1), children: 'Refresh' })] })
          ] }),
          error ? jsx('div', { className: 'mx-auto mt-3 max-w-[92rem] rounded-[4px] border border-destructive/30 bg-destructive/5 p-2 text-xs text-destructive', children: error }) : null
        ]
      }),
      jsxs('main', { className: 'mx-auto grid max-w-[92rem] gap-4 p-5 xl:grid-cols-[minmax(0,1fr)_20rem]', children: [
        jsxs('section', { className: 'flex min-w-0 flex-col gap-3', children: [
          projects.length
            ? projects.map(project => {
                const value = record(project)
                const progress = record(value.progress)
                return jsxs('button', { type: 'button', className: 'w-full rounded-[6px] border border-(--ui-stroke-secondary) bg-(--ui-bg-secondary) p-4 text-left transition-colors hover:border-ring', onClick: () => openProject(value), children: [
                  jsxs('div', { className: 'flex flex-wrap items-start justify-between gap-3', children: [
                    jsxs('div', { children: [jsx('div', { className: 'text-sm font-semibold text-(--ui-text-primary)', children: text(value.name) || text(value.slug) || 'Unnamed project' }), jsx('div', { className: 'mt-1 break-all text-[0.6875rem] text-(--ui-text-tertiary)', children: text(value.primary_path) || 'No workspace path' })] }),
                    statusBadge(progress.status)
                  ] }),
                  jsxs('div', { className: 'mt-4 flex items-center gap-3', children: [jsx('div', { className: 'h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-(--ui-stroke-secondary)', children: jsx('div', { className: 'h-full rounded-full bg-primary transition-all', style: { width: `${Math.max(0, Math.min(100, Number(progress.percent) || 0))}%` } }) }), jsx('span', { className: 'w-10 text-right text-xs font-medium text-(--ui-text-secondary)', children: `${Number(progress.percent) || 0}%` })] }),
                  jsxs('div', { className: 'mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[0.6875rem] text-(--ui-text-tertiary)', children: [jsx('span', { children: `${Number(progress.completed_milestones) || 0}/${Number(progress.total_milestones) || 0} milestones` }), jsx('span', { children: `${Number(progress.completed_slices) || 0}/${Number(progress.total_slices) || 0} slices` }), jsx('span', { children: `${text(record(value.coordination).mode) || 'beads'} coordination` }), value.config?.has_overrides ? jsx('span', { className: 'text-primary', children: 'project overrides' }) : null] })
                ] }, text(value.id))
              })
            : jsxs('div', { className: 'rounded-[6px] border border-dashed border-(--ui-stroke-secondary) p-8 text-center', children: [jsx('div', { className: 'text-sm font-medium text-(--ui-text-primary)', children: 'No Hermes projects yet' }), jsx('div', { className: 'mx-auto mt-2 max-w-md text-xs leading-5 text-(--ui-text-tertiary)', children: 'Create a named project here or use the native Hermes project picker. The factory will keep configuration and evidence separate for every project.' })] }),
          jsx('div', { className: 'rounded-[6px] border border-(--ui-stroke-secondary) bg-(--ui-bg-secondary) p-4 text-xs leading-5 text-(--ui-text-secondary)', children: 'Project identity and folder ownership come from Hermes. Dark Factory only adds per-project defaults, acceptance setup, and read-only progress/log views.' })
        ] }),
        jsxs('aside', { className: 'flex flex-col gap-3', children: [
          jsxs('section', { className: 'rounded-[6px] border border-(--ui-stroke-secondary) bg-(--ui-bg-secondary) p-4', children: [jsx('h2', { className: 'text-sm font-semibold text-(--ui-text-primary)', children: 'New project' }), jsx('p', { className: 'mt-1 text-[0.6875rem] leading-4 text-(--ui-text-tertiary)', children: 'This creates a native Hermes project; it does not initialize Beads or write factory state.' }), jsx(Field, { label: 'Name', className: 'mt-3', children: jsx(Input, { value: newName, placeholder: 'Payments portal', onChange: event => setNewName(event.target.value) }) }), jsx(Field, { label: 'Workspace path', hint: 'The folder must already exist.', className: 'mt-3', children: jsx(Input, { value: newPath, placeholder: '/path/to/workspace', 'aria-label': 'Workspace path', onChange: event => setNewPath(event.target.value) }) }), jsx(Button, { type: 'button', className: 'mt-4 w-full', disabled: creating, onClick: createProject, children: creating ? 'Creating…' : 'Create project' })] }),
          jsx('div', { className: 'rounded-[6px] border border-(--ui-stroke-secondary) p-4 text-[0.6875rem] leading-4 text-(--ui-text-tertiary)', children: 'A project can inherit global model/prompt/coordination defaults or save a sparse override. Resetting overrides returns it to the global policy.' })
        ] })
      ] })
    ]
  })
}

function FactorySettingsPage({ rest }) {
  const [config, setConfig] = useState(() => defaultConfig())
  const [catalog, setCatalog] = useState(() => normaliseCatalog({}))
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let live = true
    setLoading(true)
    rest('/global-config')
      .then(payload => {
        if (!live) return
        const value = record(payload)
        setConfig(normaliseConfig(value.config))
        setCatalog(normaliseCatalog(value.model_options))
        setDirty(false)
        setError('')
      })
      .catch(cause => { if (live) setError(errorText(cause)) })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [rest])

  const update = (path, value) => {
    setConfig(current => setAtPath(current, path, value))
    setDirty(true)
  }

  const save = async () => {
    setSaving(true)
    setError('')
    try {
      const payload = record(await rest('/global-config', { method: 'PUT', body: { config } }))
      setConfig(normaliseConfig(payload.config || config))
      if (payload.model_options) setCatalog(normaliseCatalog(payload.model_options))
      setDirty(false)
      host.notify({ kind: 'success', message: 'Dark Factory global defaults saved.' })
    } catch (cause) {
      const message = errorText(cause)
      setError(message)
      host.notify({ kind: 'error', title: 'Could not save global defaults', message })
    } finally {
      setSaving(false)
    }
  }

  if (loading) return jsxs('div', { className: 'flex h-full items-center justify-center p-8 text-sm text-(--ui-text-tertiary)', children: [jsx('span', { className: 'mr-2 inline-block size-3 animate-spin rounded-full border-2 border-(--ui-stroke-secondary) border-t-primary' }), 'Loading global defaults…'] })

  return jsxs('div', { className: 'h-full overflow-y-auto bg-(--ui-bg-primary) text-foreground', children: [
    jsxs('header', { className: 'sticky top-0 z-10 border-b border-(--ui-stroke-secondary) bg-(--ui-bg-primary)/95 px-5 py-3 backdrop-blur', children: [jsxs('div', { className: 'mx-auto flex max-w-[92rem] items-center justify-between gap-3', children: [jsxs('div', { children: [jsx('h1', { className: 'text-base font-semibold text-(--ui-text-primary)', children: 'Dark Factory global defaults' }), jsx('p', { className: 'mt-0.5 text-xs text-(--ui-text-tertiary)', children: 'Defaults apply to projects that have no matching override. Credentials are never part of this configuration.' })] }), jsxs('div', { className: 'flex items-center gap-2', children: [jsx(Button, { type: 'button', variant: 'ghost', onClick: () => host.navigate(ROUTE), children: 'Projects' }), jsx(Button, { type: 'button', disabled: saving, onClick: save, children: saving ? 'Saving…' : dirty ? 'Save defaults •' : 'Save defaults' })] })] }), error ? jsx('div', { className: 'mx-auto mt-3 max-w-[92rem] rounded-[4px] border border-destructive/30 bg-destructive/5 p-2 text-xs text-destructive', children: error }) : null] }),
    jsxs('main', { className: 'mx-auto flex max-w-[92rem] flex-col gap-4 p-5', children: [
      jsx(Section, { id: 'global-coordination', step: '1', title: 'Coordination and bounded policy', description: 'Choose the default coordination surfaces for repeated builds. Project settings can override this without changing other projects.', children: jsx(coordinationEditor, { coordination: config.coordination, onChange: value => update(['coordination'], value) }) }),
      jsx(Section, { id: 'global-models', step: '2', title: 'Model defaults', description: 'Only authenticated provider/model references are selectable. Blank assignments use the fill-only Sol/Luna preset where available.', children: jsx(modelEditor, { models: config.models, catalog, onChange: value => update(['models'], value) }) }),
      jsx(Section, { id: 'global-prompts', step: '3', title: 'System prompts', description: 'Role-specific prompt defaults are retained in the compiled mission manifest and can be overridden per project.', children: jsx(promptEditor, { prompts: config.system_prompts, onChange: value => update(['system_prompts'], value) }) }),
      jsx(Section, { id: 'global-reasoning', step: '4', title: 'Reasoning and retry bounds', description: 'Keep the factory bounded by default. These values are copied into each project only when it inherits global policy.', children: jsxs('div', { className: 'grid gap-3 md:grid-cols-2', children: [jsx(Field, { label: 'Orchestrator reasoning', children: jsx('select', { className: selectClass, value: config.reasoning_effort.orchestrator, onChange: event => update(['reasoning_effort', 'orchestrator'], event.target.value), children: ['low', 'medium', 'high'].map(value => jsx('option', { value, children: value })) }) }), jsx(Field, { label: 'Worker reasoning', children: jsx('select', { className: selectClass, value: config.reasoning_effort.worker, onChange: event => update(['reasoning_effort', 'worker'], event.target.value), children: ['low', 'medium', 'high'].map(value => jsx('option', { value, children: value })) }) })] }) })
    ] })
  ] })
}

function FactoryProjectPage({ rest }) {
  const projectId = selectedProjectId
  const [detail, setDetail] = useState(null)
  const [config, setConfig] = useState(() => defaultConfig())
  const [overrides, setOverrides] = useState({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [error, setError] = useState('')
  const [reloadToken, setReloadToken] = useState(0)

  useEffect(() => {
    if (!projectId) {
      setLoading(false)
      return undefined
    }
    let live = true
    const load = () => {
      rest(`/projects/${encodeURIComponent(projectId)}`)
        .then(payload => {
          if (!live) return
          const value = record(payload)
          setDetail(value)
          setConfig(normaliseConfig(record(value.config).effective))
          setOverrides(record(value.config).overrides || {})
          setDirty(false)
          setError('')
        })
        .catch(cause => { if (live) setError(errorText(cause)) })
        .finally(() => { if (live) setLoading(false) })
    }
    load()
    return () => { live = false }
  }, [rest, projectId, reloadToken])

  const update = (section, value) => {
    setConfig(current => ({ ...current, [section]: value }))
    setOverrides(current => ({ ...record(current), [section]: value }))
    setDirty(true)
  }

  const save = async () => {
    setSaving(true)
    setError('')
    try {
      const payload = await rest(`/projects/${encodeURIComponent(projectId)}/config`, { method: 'PUT', body: { overrides } })
      const value = record(payload)
      setDetail(value)
      setConfig(normaliseConfig(record(value.config).effective))
      setOverrides(record(value.config).overrides || {})
      setDirty(false)
      host.notify({ kind: 'success', message: 'Project Dark Factory overrides saved.' })
    } catch (cause) {
      const message = errorText(cause)
      setError(message)
      host.notify({ kind: 'error', title: 'Could not save project overrides', message })
    } finally {
      setSaving(false)
    }
  }

  const reset = async () => {
    setSaving(true)
    try {
      const payload = await rest(`/projects/${encodeURIComponent(projectId)}/config`, { method: 'PUT', body: { overrides: {} } })
      const value = record(payload)
      setDetail(value)
      setConfig(normaliseConfig(record(value.config).effective))
      setOverrides({})
      setDirty(false)
      host.notify({ kind: 'success', message: 'Project now inherits global defaults.' })
    } catch (cause) {
      const message = errorText(cause)
      setError(message)
      host.notify({ kind: 'error', title: 'Could not reset project overrides', message })
    } finally {
      setSaving(false)
    }
  }

  if (!projectId) return jsxs('div', { className: 'flex h-full items-center justify-center p-8', children: [jsxs('div', { className: 'max-w-lg text-center', children: [jsx('h1', { className: 'text-base font-semibold text-(--ui-text-primary)', children: 'Select a Dark Factory project' }), jsx('p', { className: 'mt-2 text-xs text-(--ui-text-tertiary)', children: 'Return to the projects list and choose a build workspace.' }), jsx(Button, { type: 'button', className: 'mt-4', onClick: () => host.navigate(ROUTE), children: 'Open projects' })] })] })
  if (loading && !detail) return jsxs('div', { className: 'flex h-full items-center justify-center p-8 text-sm text-(--ui-text-tertiary)', children: [jsx('span', { className: 'mr-2 inline-block size-3 animate-spin rounded-full border-2 border-(--ui-stroke-secondary) border-t-primary' }), 'Loading project…'] })
  if (!detail) return jsxs('div', { className: 'flex h-full items-center justify-center p-8', children: [jsxs('div', { className: 'max-w-lg rounded-[6px] border border-destructive/30 p-5', children: [jsx('h1', { className: 'text-sm font-semibold text-destructive', children: 'Project is unavailable' }), jsx('p', { className: 'mt-2 text-xs text-(--ui-text-secondary)', children: error || 'The Hermes project could not be read.' }), jsx(Button, { type: 'button', className: 'mt-4', onClick: () => host.navigate(ROUTE), children: 'Back to projects' })] })] })

  const progress = record(detail.progress)
  const coordination = record(config.coordination)
  const beads = record(detail.beads)
  const logs = record(detail.logs)
  return jsxs('div', { className: 'h-full overflow-y-auto bg-(--ui-bg-primary) text-foreground', children: [
    jsxs('header', { className: 'sticky top-0 z-10 border-b border-(--ui-stroke-secondary) bg-(--ui-bg-primary)/95 px-5 py-3 backdrop-blur', children: [
      jsxs('div', { className: 'mx-auto flex max-w-[92rem] flex-wrap items-center justify-between gap-3', children: [jsxs('div', { children: [jsx(Button, { type: 'button', variant: 'ghost', size: 'xs', onClick: () => host.navigate(ROUTE), children: '← Projects' }), jsx('h1', { className: 'mt-1 text-base font-semibold text-(--ui-text-primary)', children: text(detail.name) }), jsx('p', { className: 'mt-0.5 break-all text-xs text-(--ui-text-tertiary)', children: text(detail.primary_path) })] }), jsxs('div', { className: 'flex items-center gap-2', children: [statusBadge(progress.status), jsx(Button, { type: 'button', variant: 'secondary', onClick: () => host.navigate(SETUP_ROUTE), children: 'Configure mission' }), jsx(Button, { type: 'button', variant: 'ghost', onClick: () => setReloadToken(value => value + 1), children: 'Refresh' })] })] }), error ? jsx('div', { className: 'mx-auto mt-3 max-w-[92rem] rounded-[4px] border border-destructive/30 bg-destructive/5 p-2 text-xs text-destructive', children: error }) : null] }),
    jsxs('main', { className: 'mx-auto grid max-w-[92rem] gap-4 p-5 xl:grid-cols-[minmax(0,1fr)_20rem]', children: [
      jsxs('div', { className: 'flex min-w-0 flex-col gap-4', children: [
        jsxs('section', { className: 'grid gap-3 sm:grid-cols-4', children: [metric('Progress', `${Number(progress.percent) || 0}%`, `${Number(progress.completed_milestones) || 0}/${Number(progress.total_milestones) || 0} milestones`), metric('Slices', `${Number(progress.completed_slices) || 0}/${Number(progress.total_slices) || 0}`, 'accepted slices'), metric('Events', Number(progress.event_count) || 0, 'state transitions'), metric('Coordination', text(coordination.mode) || 'beads', beads.ready ? `Beads ${text(beads.version) || 'ready'}` : text(beads.reason) || 'status unavailable')] }),
        jsx('section', { className: 'rounded-[6px] border border-(--ui-stroke-secondary) bg-(--ui-bg-secondary) p-4', children: [jsx('h2', { className: 'text-sm font-semibold text-(--ui-text-primary)', children: 'Milestone progress' }), jsx('div', { className: 'mt-3 flex flex-col gap-2', children: list(progress.milestones).map(item => { const value = record(item); return jsxs('div', { className: 'flex items-center gap-3 rounded-[4px] border border-(--ui-stroke-secondary) p-2', children: [jsx('div', { className: 'min-w-0 flex-1 truncate text-xs text-(--ui-text-secondary)', children: text(value.title) || text(value.id) }), statusBadge(value.status)] }, text(value.id)) }) })] }),
        jsxs('section', { className: 'rounded-[6px] border border-(--ui-stroke-secondary) bg-(--ui-bg-secondary) p-4', children: [jsx('div', { className: 'flex items-center justify-between gap-3', children: [jsx('h2', { className: 'text-sm font-semibold text-(--ui-text-primary)', children: 'Factory log' }), jsx('span', { className: 'text-[0.6875rem] text-(--ui-text-tertiary)', children: list(logs.sources).join(', ') || 'state.events' })] }), jsx('pre', { className: 'mt-3 max-h-72 overflow-auto whitespace-pre-wrap rounded-[4px] border border-(--ui-stroke-secondary) p-3 font-mono text-[0.6875rem] leading-5 text-(--ui-text-secondary)', children: text(logs.text) || 'No factory events have been recorded yet.' })] })
      ] }),
      jsxs('aside', { className: 'flex flex-col gap-4', children: [
        jsxs('section', { className: 'rounded-[6px] border border-(--ui-stroke-secondary) bg-(--ui-bg-secondary) p-4', children: [jsxs('div', { className: 'flex items-center justify-between gap-2', children: [jsx('h2', { className: 'text-sm font-semibold text-(--ui-text-primary)', children: 'Project choices' }), jsx('span', { className: 'text-[0.6875rem] text-(--ui-text-tertiary)', children: Object.keys(record(overrides)).length ? 'custom' : 'inherited' })] }), jsx('div', { className: 'mt-3 text-[0.6875rem] text-(--ui-text-tertiary)', children: 'Changes here apply only to this project. Mission intake remains separately gated.' }), jsx('div', { className: 'mt-3', children: jsx(coordinationEditor, { coordination, onChange: value => update('coordination', value) }) }), jsx('div', { className: 'mt-4', children: jsx(modelEditor, { models: config.models, catalog: record(detail.model_options), onChange: value => update('models', value) }) }), jsx('div', { className: 'mt-4', children: jsx(promptEditor, { prompts: config.system_prompts, onChange: value => update('system_prompts', value) }) }), jsxs('div', { className: 'mt-4 flex gap-2', children: [jsx(Button, { type: 'button', disabled: saving || !dirty, onClick: save, children: saving ? 'Saving…' : 'Save project choices' }), jsx(Button, { type: 'button', variant: 'ghost', disabled: saving || !Object.keys(record(overrides)).length, onClick: reset, children: 'Use global' })] })] }),
        jsxs('section', { className: 'rounded-[6px] border border-(--ui-stroke-secondary) bg-(--ui-bg-secondary) p-4', children: [jsx('h2', { className: 'text-sm font-semibold text-(--ui-text-primary)', children: 'Beads visibility' }), jsx('p', { className: 'mt-2 text-[0.6875rem] leading-4 text-(--ui-text-tertiary)', children: beads.ready ? `Connected: ${text(beads.version) || 'bd'}` : text(beads.reason) || 'Beads status unavailable.' }), jsx(Button, { type: 'button', variant: 'ghost', className: 'mt-3 w-full justify-center', onClick: () => openExternal(BEAD_ME_UP_URL), children: 'View Beads with Bead Me Up Scotty ↗' })] }),
        jsx('div', { className: 'rounded-[6px] border border-(--ui-stroke-secondary) p-4 text-[0.6875rem] leading-4 text-(--ui-text-tertiary)', children: text(detail.snapshot?.manifest_path) ? `Manifest: ${text(detail.snapshot.manifest_path)}` : 'No compiled manifest yet. Complete intake before arming.' })
      ] })
    ] })
  ] })
}

function DarkFactoryPage({ rest, projectId = '' }) {
  const [setup, setSetup] = useState(() => normaliseSetup({}))
  const [catalog, setCatalog] = useState(() => normaliseCatalog({}))
  const [profile, setProfile] = useState('default')
  const [readiness, setReadiness] = useState(null)
  const [compileResult, setCompileResult] = useState(null)
  const [loading, setLoading] = useState(true)
  const [refreshingModels, setRefreshingModels] = useState(false)
  const [saving, setSaving] = useState(false)
  const [arming, setArming] = useState(false)
  const [importing, setImporting] = useState(false)
  const [importPath, setImportPath] = useState('')
  const [dirty, setDirty] = useState(false)
  const [error, setError] = useState('')
  const [reloadToken, setReloadToken] = useState(0)
  const [projectOverrides, setProjectOverrides] = useState({})
  const projectRoute = projectId ? `/projects/${encodeURIComponent(projectId)}` : ''

  useEffect(() => {
    let live = true
    setLoading(true)
    setError('')

    Promise.all([rest(projectRoute || '/setup'), rest('/model-options')])
      .then(([setupPayload, modelPayload]) => {
        if (!live) return
        const setupResponse = record(setupPayload)
        const options = normaliseCatalog(modelPayload || setupResponse.model_options)
        setSetup(normaliseSetup(setupResponse.setup, options))
        setCatalog(options)
        setProfile(text(setupResponse.profile || options.profile) || 'default')
        setReadiness(setupResponse.readiness || null)
        setProjectOverrides(record(setupResponse.config).overrides || {})
        setDirty(false)
      })
      .catch(cause => {
        if (!live) return
        const message = errorText(cause)
        setError(message)
        host.notify({ kind: 'error', title: 'Dark Factory setup failed to load', message })
      })
      .finally(() => {
        if (live) setLoading(false)
      })

    return () => {
      live = false
    }
  }, [rest, projectRoute, reloadToken])

  const change = useCallback((path, value) => {
    setSetup(current => {
      const next = setAtPath(current, path, value)
      if (projectId && ['models', 'model_policy', 'system_prompts', 'policy'].includes(path[0])) {
        setProjectOverrides(currentOverrides => ({
          ...record(currentOverrides),
          models: next.models,
          model_policy: next.model_policy,
          system_prompts: next.system_prompts,
          policy: next.policy
        }))
      }
      if (projectId && path[0] === 'execution' && path[1] === 'reasoning_effort') {
        setProjectOverrides(currentOverrides => ({
          ...record(currentOverrides),
          reasoning_effort: next.execution.reasoning_effort
        }))
      }
      return next
    })
    setDirty(true)
    setCompileResult(null)
  }, [])

  const refreshModels = async () => {
    setRefreshingModels(true)
    try {
      const payload = normaliseCatalog(await rest('/model-options?refresh=true'))
      setCatalog(payload)
      setProfile(payload.profile)
      host.notify({ kind: 'success', message: `Refreshed authenticated models for profile ${payload.profile}.` })
    } catch (cause) {
      const message = errorText(cause)
      host.notify({ kind: 'error', title: 'Could not refresh model options', message })
    } finally {
      setRefreshingModels(false)
    }
  }

  const saveDraft = async () => {
    setSaving(true)
    setError('')
    try {
      const request = { body: { setup: serialiseSetup(setup, catalog) } }
      const body = request.body
      if (projectId) body.overrides = projectOverrides
      const payload = record(await rest(projectRoute || '/setup', { method: 'PUT', body }))
      const saved = normaliseSetup(payload.setup || setup, catalog)
      setSetup(saved)
      setReadiness(payload.readiness || readiness)
      setProfile(text(payload.profile) || profile)
      if (payload.model_options) setCatalog(normaliseCatalog(payload.model_options))
      setDirty(false)
      host.notify({
        kind: 'success',
        message: payload.readiness?.ready
          ? 'Draft saved. Every readiness gate passes.'
          : `Draft saved with ${list(payload.readiness?.blockers).length} blocking item(s).`
      })
    } catch (cause) {
      const message = errorText(cause)
      setError(message)
      host.notify({ kind: 'error', title: 'Could not save Dark Factory draft', message })
    } finally {
      setSaving(false)
    }
  }

  const armFactory = async () => {
    setArming(true)
    setError('')
    try {
      const request = { body: { setup: serialiseSetup(setup, catalog) } }
      const body = request.body
      if (projectId) body.overrides = projectOverrides
      const payload = record(await rest(projectRoute ? `${projectRoute}/compile` : '/compile', { method: 'POST', body }))
      if (payload.readiness) setReadiness(payload.readiness)
      setCompileResult(payload)
      setDirty(false)
      host.notify({ kind: 'success', title: 'Dark Factory armed', message: `Manifest written to ${text(payload.manifest_path)}` })
    } catch (cause) {
      const message = errorText(cause)
      setError(message)
      host.notify({ kind: 'error', title: 'Dark Factory could not be armed', message })
    } finally {
      setArming(false)
    }
  }

  const importManifest = async () => {
    if (!text(importPath).trim()) {
      host.notify({ kind: 'error', title: 'Manifest path required', message: 'Enter the absolute path to a canonical JSON manifest.' })
      return
    }
    setImporting(true)
    setError('')
    try {
      const body = { manifest_path: text(importPath).trim() }
      if (projectId) body.project_id = projectId
      const payload = record(await rest('/manifest/import', { method: 'POST', body }))
      setCompileResult(payload)
      setDirty(false)
      setImportPath('')
      host.notify({ kind: 'success', title: 'Manifest imported', message: `Factory pair written to ${text(payload.manifest_path)}. Beads graph remains unapplied.` })
      setReloadToken(value => value + 1)
    } catch (cause) {
      const message = errorText(cause)
      setError(message)
      host.notify({ kind: 'error', title: 'Manifest import rejected', message })
    } finally {
      setImporting(false)
    }
  }

  const personaOptions = useMemo(() => setup.personas.map(item => ({ id: item.id, label: item.name || item.id })), [setup.personas])
  const storyOptions = useMemo(() => setup.user_stories.map(item => ({ id: item.id, label: item.want || item.id })), [setup.user_stories])
  const busy = saving || arming || importing
  const canArm = Boolean(readiness?.ready) && !dirty && !busy
  const solAvailable = modelRefAvailable(catalog, SOL_ORCHESTRATOR)
  const lunaAvailable = modelRefAvailable(catalog, LUNA_WORKER)


  if (loading) {
    return jsxs('div', {
      className: 'flex h-full items-center justify-center p-8 text-sm text-(--ui-text-tertiary)',
      children: [
        jsx('span', { className: 'mr-2 inline-block size-3 animate-spin rounded-full border-2 border-(--ui-stroke-secondary) border-t-primary' }),
        'Loading Dark Factory setup…'
      ]
    })
  }

  if (error && !readiness) {
    return jsxs('div', {
      className: 'flex h-full items-center justify-center p-8',
      children: [
        jsxs('div', {
          className: 'max-w-lg rounded-[6px] border border-destructive/30 bg-(--ui-bg-secondary) p-5',
          children: [
            jsx('h1', { className: 'text-sm font-semibold text-destructive', children: 'Dark Factory setup is unavailable' }),
            jsx('p', { className: 'mt-2 text-xs leading-5 text-(--ui-text-secondary)', children: error }),
            jsx(Button, { type: 'button', className: 'mt-4', onClick: () => setReloadToken(value => value + 1), children: 'Retry' })
          ]
        })
      ]
    })
  }

  return jsxs('div', {
    className: 'h-full overflow-y-auto bg-(--ui-bg-primary) text-foreground',
    children: [
      jsxs('header', {
        className: 'sticky top-0 z-10 border-b border-(--ui-stroke-secondary) bg-(--ui-bg-primary)/95 px-5 py-3 backdrop-blur',
        children: [
          jsxs('div', {
            className: 'mx-auto flex max-w-[92rem] flex-wrap items-center justify-between gap-3',
            children: [
              jsxs('div', {
                children: [
                  jsx('h1', { className: 'text-base font-semibold text-(--ui-text-primary)', children: 'Dark Factory setup' }),
                  jsx('p', {
                    className: 'mt-0.5 text-xs text-(--ui-text-tertiary)',
                    children: 'Define the product contract, independent evidence, risk controls, and model roles before arming.'
                  })
                ]
              }),
              jsxs('div', {
                className: 'flex items-center gap-2',
                children: [
                  jsx(Input, {
                    className: 'w-64 max-w-[35vw]',
                    value: importPath,
                    placeholder: '/absolute/path/to/manifest.json',
                    title: 'Import a canonical schema-v2 Beads-backed manifest',
                    onChange: event => setImportPath(event.target.value),
                    disabled: busy
                  }),
                  jsx(Button, {
                    type: 'button',
                    variant: 'ghost',
                    disabled: busy || !text(importPath).trim(),
                    title: 'Import a validated manifest without applying the Beads graph.',
                    onClick: importManifest,
                    children: importing ? 'Importing…' : 'Import Manifest'
                  }),
                  jsx(Button, {
                    type: 'button',
                    variant: 'secondary',
                    disabled: busy,
                    onClick: saveDraft,
                    children: saving ? 'Saving…' : dirty ? 'Save Draft •' : 'Save Draft'
                  }),
                  jsx(Button, {
                    type: 'button',
                    disabled: !canArm,
                    title: dirty ? 'Save and validate the current draft before arming.' : readiness?.ready ? 'Compile the factory manifest and initial state.' : 'Resolve every blocker before arming.',
                    onClick: armFactory,
                    children: arming ? 'Arming…' : 'Arm Factory'
                  })
                ]
              })
            ]
          }),
          error
            ? jsxs('div', {
                className: 'mx-auto mt-3 flex max-w-[92rem] items-start justify-between gap-3 rounded-[4px] border border-destructive/30 bg-destructive/5 p-2 text-xs text-destructive',
                children: [jsx('span', { children: error }), jsx(Button, { type: 'button', variant: 'ghost', size: 'xs', onClick: () => setError(''), children: 'Dismiss' })]
              })
            : null
        ]
      }),
      jsxs('main', {
        className: 'mx-auto grid max-w-[92rem] gap-4 p-5 xl:grid-cols-[minmax(0,1fr)_20rem]',
        children: [
          jsxs('div', {
            className: 'flex min-w-0 flex-col gap-4',
            children: [
              jsx(Section, {
                id: 'mission',
                step: '1',
                title: 'Product mission and boundaries',
                description: 'Describe observable user value and the operating context. Implementation details come later.',
                children: jsxs('div', {
                  className: 'flex flex-col gap-4',
                  children: [
                    jsxs('div', {
                      className: 'grid gap-3 md:grid-cols-2',
                      children: [
                        jsx(Field, {
                          label: 'Project mode',
                          children: jsxs('select', {
                            className: selectClass,
                            value: setup.project_mode,
                            onChange: event => change(['project_mode'], event.target.value),
                            children: [jsx('option', { value: 'existing', children: 'Existing product' }), jsx('option', { value: 'greenfield', children: 'Greenfield build' })]
                          })
                        }),
                        jsx(Field, {
                          label: 'Workspace path',
                          hint: 'Existing projects need an existing directory; greenfield paths need an existing parent.',
                          children: jsx(Input, { value: setup.workspace_path, placeholder: '/absolute/path/to/workspace', onChange: event => change(['workspace_path'], event.target.value) })
                        }),
                        jsx(Field, {
                          label: 'Product or capability name',
                          children: jsx(Input, { value: setup.product.name, placeholder: 'Stable mission name', onChange: event => change(['product', 'name'], event.target.value) })
                        })
                      ]
                    }),
                    jsx(Field, {
                      label: 'Problem',
                      hint: 'Who is affected, what hurts today, and why it matters.',
                      children: jsx(Textarea, { className: 'min-h-24 resize-y', value: setup.product.problem, onChange: event => change(['product', 'problem'], event.target.value) })
                    }),
                    jsx(Field, {
                      label: 'Observable outcome',
                      hint: 'State what a user can do or what behavior can be observed—not which files to edit.',
                      children: jsx(Textarea, { className: 'min-h-24 resize-y', value: setup.product.outcome, onChange: event => change(['product', 'outcome'], event.target.value) })
                    }),
                    jsx(Field, {
                      label: 'Product and domain context',
                      children: jsx(Textarea, { className: 'min-h-24 resize-y', value: setup.product.context, onChange: event => change(['product', 'context'], event.target.value) })
                    }),
                    setup.project_mode === 'existing'
                      ? jsx(Field, {
                          label: 'Existing system and current behavior',
                          children: jsx(Textarea, { className: 'min-h-24 resize-y', value: setup.product.existing_system, onChange: event => change(['product', 'existing_system'], event.target.value) })
                        })
                      : null,
                    jsxs('div', {
                      className: 'grid gap-4 lg:grid-cols-2',
                      children: [
                        jsx(Field, {
                          label: 'Success metrics',
                          children: jsx(StringList, { values: setup.product.success_metrics, onChange: value => change(['product', 'success_metrics'], value), addLabel: 'Add success metric', placeholder: 'Observable, measurable success signal' })
                        }),
                        jsx(Field, {
                          label: 'User-facing surfaces',
                          hint: 'Examples: web UI, desktop UI, public API.',
                          children: jsx(StringList, { values: setup.product.surfaces, onChange: value => change(['product', 'surfaces'], value), addLabel: 'Add surface', placeholder: 'Surface' })
                        }),
                        jsx(Field, {
                          label: 'Explicit non-goals',
                          children: jsx(StringList, { values: setup.non_goals, onChange: value => change(['non_goals'], value), addLabel: 'Add non-goal', placeholder: 'This mission will not…' })
                        }),
                        jsx(Field, {
                          label: 'Constraints',
                          children: jsx(StringList, { values: setup.constraints, onChange: value => change(['constraints'], value), addLabel: 'Add constraint', placeholder: 'Stack, compatibility, data, delivery, or architecture constraint' })
                        })
                      ]
                    })
                  ]
                })
              }),
              jsx(Section, {
                id: 'users',
                step: '2',
                title: 'Personas and structured stories',
                description: 'Tie every story to a target user and give it positive and failure-path acceptance criteria.',
                children: jsxs('div', {
                  className: 'flex flex-col gap-5',
                  children: [
                    jsxs('div', {
                      className: 'flex flex-col gap-3',
                      children: [
                        jsx('h3', { className: 'text-xs font-semibold text-(--ui-text-primary)', children: 'Personas' }),
                        setup.personas.length
                          ? setup.personas.map((persona, index) =>
                              jsxs(
                                'div',
                                {
                                  className: 'rounded-[4px] border border-(--ui-stroke-secondary) bg-(--ui-bg-primary) p-3',
                                  children: [
                                    jsx(RowHeader, { title: persona.name || persona.id || `Persona ${index + 1}`, removeLabel: 'Remove persona', onRemove: () => change(['personas'], setup.personas.filter((_, itemIndex) => itemIndex !== index)) }),
                                    jsxs('div', {
                                      className: 'grid gap-3 md:grid-cols-2',
                                      children: [
                                        jsx(Field, { label: 'ID', children: jsx(Input, { value: persona.id, onChange: event => change(['personas', index, 'id'], event.target.value) }) }),
                                        jsx(Field, { label: 'Name', children: jsx(Input, { value: persona.name, onChange: event => change(['personas', index, 'name'], event.target.value) }) }),
                                        jsx(Field, { className: 'md:col-span-2', label: 'Operating context', children: jsx(Textarea, { className: 'resize-y', value: persona.context, onChange: event => change(['personas', index, 'context'], event.target.value) }) }),
                                        jsx(Field, { className: 'md:col-span-2', label: 'Concrete need', children: jsx(Textarea, { className: 'resize-y', value: persona.need, onChange: event => change(['personas', index, 'need'], event.target.value) }) })
                                      ]
                                    })
                                  ]
                                },
                                persona.id || index
                              )
                            )
                          : jsx(EmptyRows, { children: 'Add at least one target user.' }),
                        jsx(Button, {
                          type: 'button',
                          variant: 'secondary',
                          size: 'sm',
                          className: 'self-start',
                          onClick: () => change(['personas'], [...setup.personas, { id: nextId(setup.personas, 'P'), name: '', context: '', need: '' }]),
                          children: 'Add persona'
                        })
                      ]
                    }),
                    jsxs('div', {
                      className: 'flex flex-col gap-3 border-t border-(--ui-stroke-secondary) pt-4',
                      children: [
                        jsx('h3', { className: 'text-xs font-semibold text-(--ui-text-primary)', children: 'User stories' }),
                        setup.user_stories.length
                          ? setup.user_stories.map((story, storyIndex) =>
                              jsxs(
                                'div',
                                {
                                  className: 'rounded-[4px] border border-(--ui-stroke-secondary) bg-(--ui-bg-primary) p-3',
                                  children: [
                                    jsx(RowHeader, { title: story.want || story.id || `Story ${storyIndex + 1}`, removeLabel: 'Remove story', onRemove: () => change(['user_stories'], setup.user_stories.filter((_, index) => index !== storyIndex)) }),
                                    jsxs('div', {
                                      className: 'grid gap-3 md:grid-cols-2',
                                      children: [
                                        jsx(Field, { label: 'Story ID', children: jsx(Input, { value: story.id, onChange: event => change(['user_stories', storyIndex, 'id'], event.target.value) }) }),
                                        jsx(Field, {
                                          label: 'Persona',
                                          children: jsxs('select', {
                                            className: selectClass,
                                            value: story.persona_id,
                                            onChange: event => change(['user_stories', storyIndex, 'persona_id'], event.target.value),
                                            children: [jsx('option', { value: '', children: 'Choose persona' }), ...personaOptions.map(item => jsx('option', { value: item.id, children: item.label }, item.id))]
                                          })
                                        }),
                                        jsx(Field, { className: 'md:col-span-2', label: 'I want…', children: jsx(Textarea, { className: 'resize-y', value: story.want, onChange: event => change(['user_stories', storyIndex, 'want'], event.target.value) }) }),
                                        jsx(Field, { className: 'md:col-span-2', label: 'So that…', children: jsx(Textarea, { className: 'resize-y', value: story.so_that, onChange: event => change(['user_stories', storyIndex, 'so_that'], event.target.value) }) })
                                      ]
                                    }),
                                    jsxs('div', {
                                      className: 'mt-4 flex flex-col gap-2',
                                      children: [
                                        jsx('p', { className: 'text-xs font-medium text-(--ui-text-primary)', children: 'Acceptance criteria' }),
                                        story.acceptance.length
                                          ? story.acceptance.map((criterion, criterionIndex) =>
                                              jsxs(
                                                'div',
                                                {
                                                  className: 'grid gap-2 rounded-[4px] bg-(--ui-bg-secondary) p-2 md:grid-cols-[8rem_9rem_minmax(0,1fr)_auto]',
                                                  children: [
                                                    jsx(Input, { value: criterion.id, 'aria-label': 'Criterion ID', onChange: event => change(['user_stories', storyIndex, 'acceptance', criterionIndex, 'id'], event.target.value) }),
                                                    jsxs('select', {
                                                      className: selectClass,
                                                      value: criterion.type,
                                                      'aria-label': 'Criterion type',
                                                      onChange: event => change(['user_stories', storyIndex, 'acceptance', criterionIndex, 'type'], event.target.value),
                                                      children: ACCEPTANCE_TYPES.map(type => jsx('option', { value: type, children: type }, type))
                                                    }),
                                                    jsx(Input, { value: criterion.statement, 'aria-label': 'Criterion statement', placeholder: 'Observable behavior', onChange: event => change(['user_stories', storyIndex, 'acceptance', criterionIndex, 'statement'], event.target.value) }),
                                                    jsx(Button, { type: 'button', variant: 'ghost', size: 'xs', onClick: () => change(['user_stories', storyIndex, 'acceptance'], story.acceptance.filter((_, index) => index !== criterionIndex)), children: 'Remove' })
                                                  ]
                                                },
                                                criterion.id || criterionIndex
                                              )
                                            )
                                          : jsx(EmptyRows, { children: 'Add at least two criteria, including a negative, recovery, boundary, or abuse case.' }),
                                        jsx(Button, {
                                          type: 'button',
                                          variant: 'secondary',
                                          size: 'sm',
                                          className: 'self-start',
                                          onClick: () => {
                                            const id = nextId(story.acceptance, `${story.id || `US${storyIndex + 1}`}-A`)
                                            change(['user_stories', storyIndex, 'acceptance'], [...story.acceptance, { id, type: story.acceptance.length ? 'negative' : 'happy', statement: '' }])
                                          },
                                          children: 'Add criterion'
                                        })
                                      ]
                                    }),
                                    jsx(Field, {
                                      className: 'mt-4',
                                      label: 'Owned paths (optional)',
                                      hint: 'Use path boundaries when slices must avoid overlapping files.',
                                      children: jsx(StringList, { values: story.paths, onChange: value => change(['user_stories', storyIndex, 'paths'], value), addLabel: 'Add path', placeholder: 'src/feature/**' })
                                    })
                                  ]
                                },
                                story.id || storyIndex
                              )
                            )
                          : jsx(EmptyRows, { children: 'Add at least one structured user story.' }),
                        jsx(Button, {
                          type: 'button',
                          variant: 'secondary',
                          size: 'sm',
                          className: 'self-start',
                          onClick: () => {
                            const id = nextId(setup.user_stories, 'US')
                            change(['user_stories'], [
                              ...setup.user_stories,
                              {
                                id,
                                persona_id: setup.personas[0]?.id || '',
                                want: '',
                                so_that: '',
                                acceptance: [
                                  { id: `${id}-A1`, type: 'happy', statement: '' },
                                  { id: `${id}-A2`, type: 'negative', statement: '' }
                                ],
                                paths: []
                              }
                            ])
                          },
                          children: 'Add user story'
                        })
                      ]
                    })
                  ]
                })
              }),
              jsx(Section, {
                id: 'milestones',
                step: '3',
                title: 'Product milestones',
                description: 'Map every story to exactly one user-observable milestone and define milestone-level acceptance.',
                children: jsxs('div', {
                  className: 'flex flex-col gap-3',
                  children: [
                    setup.milestones.length
                      ? setup.milestones.map((milestone, milestoneIndex) =>
                          jsxs(
                            'div',
                            {
                              className: 'rounded-[4px] border border-(--ui-stroke-secondary) bg-(--ui-bg-primary) p-3',
                              children: [
                                jsx(RowHeader, { title: milestone.outcome || milestone.id || `Milestone ${milestoneIndex + 1}`, removeLabel: 'Remove milestone', onRemove: () => change(['milestones'], setup.milestones.filter((_, index) => index !== milestoneIndex)) }),
                                jsxs('div', {
                                  className: 'grid gap-3 md:grid-cols-[10rem_minmax(0,1fr)]',
                                  children: [
                                    jsx(Field, { label: 'Milestone ID', children: jsx(Input, { value: milestone.id, onChange: event => change(['milestones', milestoneIndex, 'id'], event.target.value) }) }),
                                    jsx(Field, { label: 'Observable outcome', children: jsx(Textarea, { className: 'resize-y', value: milestone.outcome, onChange: event => change(['milestones', milestoneIndex, 'outcome'], event.target.value) }) })
                                  ]
                                }),
                                jsxs('div', {
                                  className: 'mt-4',
                                  children: [
                                    jsx('p', { className: 'mb-2 text-xs font-medium text-(--ui-text-primary)', children: 'Mapped stories' }),
                                    storyOptions.length
                                      ? jsx('div', {
                                          className: 'grid gap-2 md:grid-cols-2',
                                          children: storyOptions.map(story =>
                                            jsxs(
                                              'label',
                                              {
                                                className: 'flex cursor-pointer items-start gap-2 rounded-[4px] border border-(--ui-stroke-secondary) p-2 text-xs text-(--ui-text-secondary)',
                                                children: [
                                                  jsx('input', {
                                                    type: 'checkbox',
                                                    className: 'mt-0.5 accent-primary',
                                                    checked: milestone.story_ids.includes(story.id),
                                                    onChange: event => change(
                                                      ['milestones', milestoneIndex, 'story_ids'],
                                                      event.target.checked
                                                        ? [...milestone.story_ids, story.id]
                                                        : milestone.story_ids.filter(id => id !== story.id)
                                                    )
                                                  }),
                                                  jsxs('span', { children: [jsx('strong', { className: 'font-medium text-(--ui-text-primary)', children: story.id }), ` — ${story.label}`] })
                                                ]
                                              },
                                              story.id
                                            )
                                          )
                                        })
                                      : jsx(EmptyRows, { children: 'Create user stories before mapping a milestone.' })
                                  ]
                                }),
                                jsxs('div', {
                                  className: 'mt-4 flex flex-col gap-2',
                                  children: [
                                    jsx('p', { className: 'text-xs font-medium text-(--ui-text-primary)', children: 'Milestone acceptance' }),
                                    milestone.acceptance.length
                                      ? milestone.acceptance.map((criterion, criterionIndex) =>
                                          jsxs(
                                            'div',
                                            {
                                              className: 'grid gap-2 md:grid-cols-[9rem_minmax(0,1fr)_auto]',
                                              children: [
                                                jsx(Input, { value: criterion.id, 'aria-label': 'Milestone criterion ID', onChange: event => change(['milestones', milestoneIndex, 'acceptance', criterionIndex, 'id'], event.target.value) }),
                                                jsx(Input, { value: criterion.statement, 'aria-label': 'Milestone criterion statement', placeholder: 'Observable product increment', onChange: event => change(['milestones', milestoneIndex, 'acceptance', criterionIndex, 'statement'], event.target.value) }),
                                                jsx(Button, { type: 'button', variant: 'ghost', size: 'xs', onClick: () => change(['milestones', milestoneIndex, 'acceptance'], milestone.acceptance.filter((_, index) => index !== criterionIndex)), children: 'Remove' })
                                              ]
                                            },
                                            criterion.id || criterionIndex
                                          )
                                        )
                                      : jsx(EmptyRows, { children: 'Add at least one milestone acceptance criterion.' }),
                                    jsx(Button, {
                                      type: 'button',
                                      variant: 'secondary',
                                      size: 'sm',
                                      className: 'self-start',
                                      onClick: () => {
                                        const id = nextId(milestone.acceptance, `${milestone.id || `M${milestoneIndex + 1}`}-A`)
                                        change(['milestones', milestoneIndex, 'acceptance'], [...milestone.acceptance, { id, statement: '' }])
                                      },
                                      children: 'Add milestone criterion'
                                    })
                                  ]
                                })
                              ]
                            },
                            milestone.id || milestoneIndex
                          )
                        )
                      : jsx(EmptyRows, { children: 'Add at least one product milestone.' }),
                    jsx(Button, {
                      type: 'button',
                      variant: 'secondary',
                      size: 'sm',
                      className: 'self-start',
                      onClick: () => {
                        const id = nextId(setup.milestones, 'M')
                        change(['milestones'], [...setup.milestones, { id, outcome: '', story_ids: [], acceptance: [{ id: `${id}-A1`, statement: '' }] }])
                      },
                      children: 'Add milestone'
                    })
                  ]
                })
              }),
              jsx(Section, {
                id: 'testing',
                step: '4',
                title: 'Independent testing and evidence',
                description: 'Give workers fast checks, milestone gates, real interaction scenarios, and a held-out oracle they cannot rewrite.',
                children: jsxs('div', {
                  className: 'flex flex-col gap-5',
                  children: [
                    jsxs('div', {
                      className: 'grid gap-4 lg:grid-cols-2',
                      children: [
                        jsx(Field, { label: 'Focused commands', hint: 'Run inside each functional slice.', children: jsx(StringList, { values: setup.testing.focused_commands, onChange: value => change(['testing', 'focused_commands'], value), addLabel: 'Add focused command', placeholder: 'pytest tests/feature -q' }) }),
                        jsx(Field, { label: 'Integration commands', hint: 'Factory-owned milestone gates.', children: jsx(StringList, { values: setup.testing.integration_commands, onChange: value => change(['testing', 'integration_commands'], value), addLabel: 'Add integration command', placeholder: 'npm test' }) }),
                        jsx(Field, { className: 'lg:col-span-2', label: 'Evidence requirements', children: jsx(StringList, { values: setup.testing.evidence_requirements, onChange: value => change(['testing', 'evidence_requirements'], value), addLabel: 'Add evidence requirement', placeholder: 'Raw artifact path, digest, criterion ID, command, and exit code' }) })
                      ]
                    }),
                    jsxs('div', {
                      className: 'flex flex-col gap-3 border-t border-(--ui-stroke-secondary) pt-4',
                      children: [
                        jsx('h3', { className: 'text-xs font-semibold text-(--ui-text-primary)', children: 'Browser / interaction scenarios' }),
                        setup.testing.browser_scenarios.length
                          ? setup.testing.browser_scenarios.map((scenario, index) =>
                              jsxs(
                                'div',
                                {
                                  className: 'grid gap-2 rounded-[4px] bg-(--ui-bg-primary) p-2 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]',
                                  children: [
                                    jsx(Textarea, { className: 'resize-y', value: scenario.action, 'aria-label': 'Browser action', placeholder: 'Action: navigate, click, type, submit…', onChange: event => change(['testing', 'browser_scenarios', index, 'action'], event.target.value) }),
                                    jsx(Textarea, { className: 'resize-y', value: scenario.expected, 'aria-label': 'Expected browser result', placeholder: 'Expected post-action state', onChange: event => change(['testing', 'browser_scenarios', index, 'expected'], event.target.value) }),
                                    jsx(Button, { type: 'button', variant: 'ghost', size: 'xs', onClick: () => change(['testing', 'browser_scenarios'], setup.testing.browser_scenarios.filter((_, itemIndex) => itemIndex !== index)), children: 'Remove' })
                                  ]
                                },
                                index
                              )
                            )
                          : jsx(EmptyRows, { children: 'Required when the product declares a user-facing UI or public API.' }),
                        jsx(Button, { type: 'button', variant: 'secondary', size: 'sm', className: 'self-start', onClick: () => change(['testing', 'browser_scenarios'], [...setup.testing.browser_scenarios, { action: '', expected: '' }]), children: 'Add interaction scenario' })
                      ]
                    }),
                    jsxs('div', {
                      className: 'flex flex-col gap-3 border-t border-(--ui-stroke-secondary) pt-4',
                      children: [
                        jsx('h3', { className: 'text-xs font-semibold text-(--ui-text-primary)', children: 'Held-out scenarios' }),
                        setup.testing.held_out_scenarios.length
                          ? setup.testing.held_out_scenarios.map((scenario, index) =>
                              jsxs(
                                'div',
                                {
                                  className: 'rounded-[4px] border border-(--ui-stroke-secondary) bg-(--ui-bg-primary) p-3',
                                  children: [
                                    jsx(RowHeader, { title: scenario.name || `Held-out scenario ${index + 1}`, onRemove: () => change(['testing', 'held_out_scenarios'], setup.testing.held_out_scenarios.filter((_, itemIndex) => itemIndex !== index)) }),
                                    jsx(Field, { label: 'Scenario name', children: jsx(Input, { value: scenario.name, onChange: event => change(['testing', 'held_out_scenarios', index, 'name'], event.target.value) }) }),
                                    jsxs('div', {
                                      className: 'mt-3 grid gap-3 md:grid-cols-3',
                                      children: [
                                        jsx(Field, { label: 'Given', children: jsx(Textarea, { className: 'resize-y', value: scenario.given, onChange: event => change(['testing', 'held_out_scenarios', index, 'given'], event.target.value) }) }),
                                        jsx(Field, { label: 'When', children: jsx(Textarea, { className: 'resize-y', value: scenario.when, onChange: event => change(['testing', 'held_out_scenarios', index, 'when'], event.target.value) }) }),
                                        jsx(Field, { label: 'Then', children: jsx(Textarea, { className: 'resize-y', value: scenario.then, onChange: event => change(['testing', 'held_out_scenarios', index, 'then'], event.target.value) }) })
                                      ]
                                    })
                                  ]
                                },
                                index
                              )
                            )
                          : jsx(EmptyRows, { children: 'Add an external acceptance challenge hidden from the builder context.' }),
                        jsx(Button, { type: 'button', variant: 'secondary', size: 'sm', className: 'self-start', onClick: () => change(['testing', 'held_out_scenarios'], [...setup.testing.held_out_scenarios, { name: '', given: '', when: '', then: '' }]), children: 'Add held-out scenario' })
                      ]
                    })
                  ]
                })
              }),
              jsx(Section, {
                id: 'security',
                step: '5',
                title: 'Security, risk, and authority',
                description: 'Classify data, name misuse and failure scenarios, and lock authority decisions before implementation.',
                children: jsxs('div', {
                  className: 'flex flex-col gap-5',
                  children: [
                    jsxs('div', {
                      className: 'rounded-[4px] border border-primary/25 bg-primary/5 p-3',
                      children: [
                        jsx('p', { className: 'text-xs font-semibold text-(--ui-text-primary)', children: 'Kryptonite adversarial gate — always on' }),
                        jsx('p', { className: 'mt-1 text-[0.6875rem] leading-4 text-(--ui-text-tertiary)', children: 'Every functional block is completed with focused evidence; verifier, adversary, and holdout review run at the milestone delivery gate.' })
                      ]
                    }),
                    jsx(Field, {
                      label: 'Data classification',
                      children: jsx('select', {
                        className: selectClass,
                        value: setup.security.data_classification,
                        onChange: event => change(['security', 'data_classification'], event.target.value),
                        children: DATA_CLASSES.map(value => jsx('option', { value, children: value }, value))
                      })
                    }),
                    jsx(Field, {
                      label: 'Risk triggers',
                      hint: 'Examples: authentication, personal data, payments, migrations, secrets, production deployment, publishing.',
                      children: jsx(StringList, { values: setup.security.risk_triggers, onChange: value => change(['security', 'risk_triggers'], value), addLabel: 'Add risk trigger', placeholder: 'Risk trigger' })
                    }),
                    jsxs('div', {
                      className: 'flex flex-col gap-3 border-t border-(--ui-stroke-secondary) pt-4',
                      children: [
                        jsx('h3', { className: 'text-xs font-semibold text-(--ui-text-primary)', children: 'Threat scenarios' }),
                        setup.security.threat_scenarios.length
                          ? setup.security.threat_scenarios.map((scenario, index) =>
                              jsxs(
                                'div',
                                {
                                  className: 'grid gap-2 rounded-[4px] bg-(--ui-bg-primary) p-2 md:grid-cols-2',
                                  children: [
                                    jsx(Input, { value: scenario.id, 'aria-label': 'Threat ID', placeholder: 'T1', onChange: event => change(['security', 'threat_scenarios', index, 'id'], event.target.value) }),
                                    jsx(Input, { value: scenario.name, 'aria-label': 'Threat name', placeholder: 'Cross-owner project disclosure', onChange: event => change(['security', 'threat_scenarios', index, 'name'], event.target.value) }),
                                    jsx(Textarea, { className: 'min-h-20 resize-y', value: scenario.scenario, 'aria-label': 'Threat scenario', placeholder: "An authenticated user requests another owner's project by guessing its identifier.", onChange: event => change(['security', 'threat_scenarios', index, 'scenario'], event.target.value) }),
                                    jsx(Textarea, { className: 'min-h-20 resize-y', value: scenario.attack_surface, 'aria-label': 'Attack surface', placeholder: 'Project detail API', onChange: event => change(['security', 'threat_scenarios', index, 'attack_surface'], event.target.value) }),
                                    jsx(Textarea, { className: 'min-h-20 resize-y md:col-span-2', value: scenario.expected_control, 'aria-label': 'Expected control', placeholder: 'Observable control that stops or contains it', onChange: event => change(['security', 'threat_scenarios', index, 'expected_control'], event.target.value) }),
                                    jsx(Button, { type: 'button', variant: 'ghost', size: 'xs', onClick: () => change(['security', 'threat_scenarios'], setup.security.threat_scenarios.filter((_, itemIndex) => itemIndex !== index)), children: 'Remove' })
                                  ]
                                },
                                index
                              )
                            )
                          : jsx(EmptyRows, { children: 'Add at least one threat; high-risk missions require two.' }),
                        jsx(Button, { type: 'button', variant: 'secondary', size: 'sm', className: 'self-start', onClick: () => change(['security', 'threat_scenarios'], [...setup.security.threat_scenarios, normaliseThreat({}, setup.security.threat_scenarios.length)]), children: 'Add threat scenario' })
                      ]
                    }),
                    jsxs('div', {
                      className: 'flex flex-col gap-3 border-t border-(--ui-stroke-secondary) pt-4',
                      children: [
                        jsx('h3', { className: 'text-xs font-semibold text-(--ui-text-primary)', children: 'Authority decisions' }),
                        setup.security.authority_decisions.length
                          ? setup.security.authority_decisions.map((decision, index) =>
                              jsxs(
                                'div',
                                {
                                  className: 'grid gap-2 rounded-[4px] bg-(--ui-bg-primary) p-2 md:grid-cols-[8rem_minmax(0,1fr)_8rem_auto]',
                                  children: [
                                    jsx(Input, { value: decision.id, 'aria-label': 'Decision ID', onChange: event => change(['security', 'authority_decisions', index, 'id'], event.target.value) }),
                                    jsx(Textarea, { className: 'resize-y', value: decision.statement, 'aria-label': 'Authority decision', placeholder: 'Who owns identity, authorization, data, migrations, publication, or side effects?', onChange: event => change(['security', 'authority_decisions', index, 'statement'], event.target.value) }),
                                    jsxs('select', {
                                      className: selectClass,
                                      value: decision.status,
                                      'aria-label': 'Decision status',
                                      onChange: event => change(['security', 'authority_decisions', index, 'status'], event.target.value),
                                      children: [jsx('option', { value: 'open', children: 'open' }), jsx('option', { value: 'locked', children: 'locked' })]
                                    }),
                                    jsx(Button, { type: 'button', variant: 'ghost', size: 'xs', onClick: () => change(['security', 'authority_decisions'], setup.security.authority_decisions.filter((_, itemIndex) => itemIndex !== index)), children: 'Remove' })
                                  ]
                                },
                                decision.id || index
                              )
                            )
                          : jsx(EmptyRows, { children: 'Add at least one authority or product decision; every decision must be locked before launch.' }),
                        jsx(Button, {
                          type: 'button',
                          variant: 'secondary',
                          size: 'sm',
                          className: 'self-start',
                          onClick: () => change(['security', 'authority_decisions'], [...setup.security.authority_decisions, { id: nextId(setup.security.authority_decisions, 'D'), statement: '', status: 'open' }]),
                          children: 'Add authority decision'
                        })
                      ]
                    })
                  ]
                })
              }),
              jsx(Section, {
                id: 'models',
                step: '6',
                title: 'Execution graph and profile-scoped model plan',
                description: 'Choose the graph backend and assign execution and review models from the active Hermes profile. Only provider and model identifiers are saved.',
                children: jsxs('div', {
                  className: 'flex flex-col gap-3',
                  children: [
                    jsxs('div', {
                      className: 'rounded-[4px] border border-(--ui-stroke-secondary) bg-(--ui-bg-primary) p-3',
                      children: [
                        jsx('h3', { className: 'text-xs font-semibold text-(--ui-text-primary)', children: 'Execution graph backend' }),
                        jsx('p', {
                          className: 'mt-1 text-[0.6875rem] leading-4 text-(--ui-text-tertiary)',
                          children: 'Mission → milestone epics → complete functional-block tasks. Thin slices, test fixes, and review comments stay inside the block.'
                        }),
                        jsx('p', {
                          className: 'mt-2 rounded-[4px] bg-(--ui-bg-secondary) p-2 text-[0.6875rem] leading-4 text-(--ui-text-secondary)',
                          children: 'Beads owns the work graph while the Dark Factory ledger owns acceptance and evidence.'
                        }),
                        jsxs('div', {
                          className: 'mt-3 grid gap-3 md:grid-cols-2',
                          children: [
                            jsx(Field, {
                              label: 'Execution backend',
                              hint: 'Beads is the default durable graph. Local is retained for prototype and compatibility workflows.',
                              children: jsxs('select', {
                                className: selectClass,
                                value: setup.execution.graph_backend,
                                onChange: event => change(['execution', 'graph_backend'], event.target.value),
                                children: [
                                  jsx('option', { value: 'beads', children: 'Beads' }),
                                  jsx('option', { value: 'local', children: 'Local — prototype / compatibility' })
                                ]
                              })
                            }),
                            jsx(Field, {
                              label: 'Beads directory',
                              hint: 'Leave blank to use <workspace>/.beads, or select an existing isolated Beads directory.',
                              children: jsx(Input, {
                                value: setup.execution.beads_directory,
                                placeholder: '<workspace>/.beads',
                                disabled: setup.execution.graph_backend !== 'beads',
                                onChange: event => change(['execution', 'beads_directory'], event.target.value)
                              })
                            })
                          ]
                        }),
                        jsxs('div', {
                          className: 'mt-3 grid gap-3 md:grid-cols-3',
                          children: [
                            jsx(Field, {
                              label: 'Graph mode',
                              hint: 'Plan is read-only. Apply enables the integrator-only apply tool after fail-closed preflight; compilation itself does not mutate Beads.',
                              children: jsxs('select', {
                                className: selectClass,
                                value: setup.execution.graph_mode,
                                onChange: event => change(['execution', 'graph_mode'], event.target.value),
                                children: [
                                  jsx('option', { value: 'plan', children: 'Plan only' }),
                                  jsx('option', { value: 'apply', children: 'Apply-enabled — manual tool' })
                                ]
                              })
                            }),
                            jsx(Field, {
                              label: 'Orchestrator reasoning effort',
                              hint: 'Applied to integrator/orchestrator dispatch descriptors.',
                              children: jsxs('select', {
                                className: selectClass,
                                value: setup.execution.reasoning_effort.orchestrator,
                                onChange: event => change(['execution', 'reasoning_effort', 'orchestrator'], event.target.value),
                                children: ['low', 'medium', 'high'].map(value => jsx('option', { value, children: value[0].toUpperCase() + value.slice(1) }, value))
                              })
                            }),
                            jsx(Field, {
                              label: 'Worker reasoning effort',
                              hint: 'Applied to builder/worker dispatch descriptors.',
                              children: jsxs('select', {
                                className: selectClass,
                                value: setup.execution.reasoning_effort.worker,
                                onChange: event => change(['execution', 'reasoning_effort', 'worker'], event.target.value),
                                children: ['low', 'medium', 'high'].map(value => jsx('option', { value, children: value[0].toUpperCase() + value.slice(1) }, value))
                              })
                            })
                          ]
                        }),
                        jsxs('label', {
                          className: 'mt-3 flex cursor-pointer items-start gap-2 rounded-[4px] border border-(--ui-stroke-secondary) p-2',
                          children: [
                            jsx('input', {
                              type: 'checkbox',
                              className: 'mt-0.5 accent-primary',
                              checked: setup.execution.beads_isolated_authorized,
                              disabled: setup.execution.graph_backend !== 'beads',
                              onChange: event => change(['execution', 'beads_isolated_authorized'], event.target.checked)
                            }),
                            jsxs('span', {
                              children: [
                                jsx('strong', { className: 'block text-xs font-medium text-(--ui-text-primary)', children: 'Authorize this isolated Beads directory (off by default)' }),
                                jsx('span', { className: 'mt-0.5 block text-[0.6875rem] leading-4 text-(--ui-text-tertiary)', children: 'Enable only with explicit authorization to use this isolated directory. Initialize it separately with bd init; this adapter never initializes stores.' })
                              ]
                            })
                          ]
                        })
                      ]
                    }),
                    jsxs('div', {
                      className: 'rounded-[4px] border border-(--ui-stroke-secondary) bg-(--ui-bg-primary) p-3',
                      children: [
                        jsxs('div', {
                          className: 'flex flex-wrap items-start justify-between gap-3',
                          children: [
                            jsxs('div', {
                              className: 'max-w-3xl',
                              children: [
                                jsx('h3', { className: 'text-xs font-semibold text-(--ui-text-primary)', children: 'Sol orchestrator + Luna worker preset' }),
                                jsx('p', { className: 'mt-1 text-[0.6875rem] leading-4 text-(--ui-text-tertiary)', children: 'The preset covers execution roles only. Verifier, adversary, and holdout remain independent review selectors and are never changed to the builder model.' })
                              ]
                            }),
                            jsx(Button, {
                              type: 'button',
                              variant: 'secondary',
                              size: 'sm',
                              disabled: !solAvailable || !lunaAvailable,
                              onClick: () => {
                                change(['models'], applySolLunaPreset(setup.models, catalog))
                                change(['model_policy'], { preset: SOL_LUNA_PRESET })
                              },
                              children: 'Apply Sol orchestrator + Luna worker'
                            })
                          ]
                        }),
                        !solAvailable || !lunaAvailable
                          ? jsxs('div', {
                              className: 'mt-3 rounded-[4px] border border-amber-500/30 bg-amber-500/5 p-2',
                              children: [
                                jsx('strong', { className: 'block text-xs font-medium text-(--ui-text-primary)', children: 'Preferred preset model unavailable' }),
                                jsx('p', {
                                  className: 'mt-1 text-[0.6875rem] leading-4 text-(--ui-text-secondary)',
                                  children: `${!solAvailable ? 'Orchestrator preference openai-codex/gpt-5.6-sol-900k is unavailable. Authenticate it or choose another model explicitly. ' : ''}${!lunaAvailable ? 'Worker preference openai-codex/gpt-5.6-luna is unavailable. Authenticate it or choose another model explicitly.' : ''}`
                                })
                              ]
                            })
                          : jsx('p', {
                              className: 'mt-3 rounded-[4px] bg-primary/5 p-2 text-[0.6875rem] leading-4 text-(--ui-text-secondary)',
                              children: 'Orchestrator / Integrator: openai-codex/gpt-5.6-sol-900k · Worker / Builder: openai-codex/gpt-5.6-luna'
                            })
                      ]
                    }),
                    jsxs('div', {
                      className: 'flex flex-wrap items-center justify-between gap-3 rounded-[4px] bg-(--ui-bg-primary) p-3',
                      children: [
                        jsxs('div', {
                          children: [
                            jsx('p', { className: 'text-xs font-medium text-(--ui-text-primary)', children: `Active profile: ${profile}` }),
                            jsx('p', { className: 'mt-0.5 text-[0.6875rem] leading-4 text-(--ui-text-tertiary)', children: `${catalog.providers.length} authenticated provider${catalog.providers.length === 1 ? '' : 's'} available. Credentials never enter this form or its saved setup.` })
                          ]
                        }),
                        jsx(Button, { type: 'button', variant: 'secondary', size: 'sm', disabled: refreshingModels, onClick: refreshModels, children: refreshingModels ? 'Refreshing…' : 'Refresh models' })
                      ]
                    }),
                    catalog.providers.length
                      ? ROLES.map(([role, label, help]) =>
                          jsx(ModelAssignment, {
                            role,
                            label,
                            help,
                            assignment: setup.models[role],
                            builder: setup.models.builder,
                            catalog,
                            onChange: value => change(['models', role], value)
                          }, role)
                        )
                      : jsx(EmptyRows, { children: `No authenticated model providers are available in profile ${profile}. Authenticate a provider in Hermes, then refresh.` })
                  ]
                })
              }),
              jsxs('div', {
                className: 'flex flex-wrap items-center justify-between gap-3 rounded-[6px] border border-(--ui-stroke-secondary) bg-(--ui-bg-secondary) p-4',
                children: [
                  jsxs('div', {
                    children: [
                      jsx('p', { className: 'text-xs font-semibold text-(--ui-text-primary)', children: readiness?.ready && !dirty ? 'All gates pass. The factory can be armed.' : 'Save the draft to run deterministic readiness gates.' }),
                      jsx('p', { className: 'mt-1 text-[0.6875rem] text-(--ui-text-tertiary)', children: 'Arming writes the manifest and initial state in the selected workspace; it never copies credentials.' })
                    ]
                  }),
                  jsxs('div', {
                    className: 'flex gap-2',
                    children: [
                      jsx(Button, { type: 'button', variant: 'secondary', disabled: busy, onClick: saveDraft, children: saving ? 'Saving…' : 'Save Draft' }),
                      jsx(Button, { type: 'button', disabled: !canArm, onClick: armFactory, children: arming ? 'Arming…' : 'Arm Factory' })
                    ]
                  })
                ]
              })
            ]
          }),
          jsx(ReadinessPanel, { readiness, dirty, compileResult })
        ]
      })
    ]
  })
}

export default {
  id: PLUGIN_ID,
  name: 'Dark Factory',
  description: 'Repeatable project workspaces for bounded autonomous software factories.',
  defaultEnabled: false,
  register(ctx) {
    openExternal = typeof ctx.os?.openExternal === 'function' ? ctx.os.openExternal : () => false
    ctx.registerMany([
      {
        id: 'projects-page',
        area: ROUTES_AREA,
        title: 'Dark Factory projects',
        data: { path: ROUTE },
        render: () => jsx(FactoryProjectsPage, { rest: ctx.rest })
      },
      {
        id: 'project-page',
        area: ROUTES_AREA,
        title: 'Dark Factory project',
        data: { path: PROJECT_ROUTE },
        render: () => jsx(FactoryProjectPage, { rest: ctx.rest })
      },
      {
        id: 'settings-page',
        area: ROUTES_AREA,
        title: 'Dark Factory global defaults',
        data: { path: SETTINGS_ROUTE },
        render: () => jsx(FactorySettingsPage, { rest: ctx.rest })
      },
      {
        id: 'setup-page',
        area: ROUTES_AREA,
        title: 'Dark Factory mission setup',
        data: { path: SETUP_ROUTE },
        render: () => jsx(DarkFactoryPage, { rest: ctx.rest, projectId: selectedProjectId })
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        order: 55,
        data: { codicon: 'beaker', label: 'Dark Factory', path: ROUTE }
      },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: {
          id: 'dark-factory.open',
          label: 'Dark Factory: Open projects',
          keywords: ['dark factory', 'factory', 'projects', 'mission', 'setup', 'autonomous development'],
          run: () => host.navigate(ROUTE)
        }
      },
      {
        id: 'settings',
        area: PALETTE_AREA,
        data: {
          id: 'dark-factory.settings',
          label: 'Dark Factory: Open global defaults',
          keywords: ['dark factory', 'defaults', 'models', 'prompts', 'beads'],
          run: () => host.navigate(SETTINGS_ROUTE)
        }
      }
    ])
  }
}
