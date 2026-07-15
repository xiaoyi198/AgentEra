import { useMemo, useRef, useState } from 'react'
import { buildCausalDryRunDraft, buildCausalEventCatalog, expandCausalRuleDraft, normalizeCausalReplayFixture } from './DLCStudioTools.js'
import { effectiveRelationshipTypes } from './RelationshipTypeTools.js'
import {
  CAUSAL_AGGREGATES, CAUSAL_AGGREGATE_SOURCES, CAUSAL_CONDITION_KINDS, CAUSAL_CONDITION_OPERATORS, CAUSAL_VALUE_TYPES,
  defaultCausalCondition, normalizeCausalConditionForEditor, serializeCausalConditions,
} from './CausalConditionTools.js'
import { WORLD_EFFECT_TYPES, defaultWorldEffect, normalizeWorldEffectsForEditor, serializeWorldEffects } from './CausalEffectTools.js'

const T = {
  add: '\uff0b \u65b0\u5efa',
  anchoredValue: '\u7edf\u4e00\u951a\u5b9a\u4ef7\u503c',
  assetOnly: '\u5e93\u5b58\u53ea\u80fd\u5f15\u7528 asset \u8d44\u6e90\uff1b\u9528\u5b9a\u4ef7\u503c = \u6570\u91cf \u00d7 anchor_rate\u3002',
  cancel: '\u53d6\u6d88',
  category: '\u5206\u7c7b',
  causalEvents: '\u56e0\u679c\u4e8b\u4ef6\u89c4\u5219',
  causalConditionGroups: '\u56e0\u679c\u6761\u4ef6\u7ec4',
  causalRuleTemplates: '\u56e0\u679c\u89c4\u5219\u6a21\u677f',
  child: '\u5b50\u5b9e\u4f53',
  delete: '\u5220\u9664',
  description: '\u63cf\u8ff0',
  edit: '\u7f16\u8f91',
  endTime: '\u7ed3\u675f\u6a21\u62df\u65f6\u95f4',
  entity: '\u5b9e\u4f53',
  eventId: '\u4e8b\u4ef6 ID',
  eventType: '\u4e8b\u4ef6\u7c7b\u578b',
  childEventType: '\u540e\u7ee7\u4e8b\u4ef6\u7c7b\u578b',
  expectedResult: '\u9884\u671f\u7ed3\u679c JSON',
  generatedAt: '\u751f\u6210\u6a21\u62df\u65f6\u95f4',
  initialEvents: '\u521d\u59cb\u4e8b\u4ef6',
  initialPayload: '\u521d\u59cb Payload JSON',
  inventory: '\u5e93\u5b58',
  jsonObjectOnly: '\u5fc5\u987b\u662f JSON \u5bf9\u8c61',
  kind: '\u7c7b\u578b',
  maxChars: '\u6700\u5927\u5b57\u7b26\u6570',
  metadata: 'Metadata JSON',
  name: '\u540d\u79f0',
  note: '\u5907\u6ce8',
  ongoingEvents: '\u6301\u7eed\u4e8b\u4ef6',
  randomEvents: '\u968f\u673a\u4e8b\u4ef6\u6a21\u677f',
  timeRules: '\u884c\u52a8\u8017\u65f6\u89c4\u5219',
  templateId: '\u6a21\u677f ID',
  ruleId: '\u89c4\u5219 ID',
  actionTypes: 'Action types (comma separated)',
  enabled: '\u542f\u7528',
  severityMin: '\u6700\u4f4e\u4e25\u91cd\u5ea6',
  severityMax: '\u6700\u9ad8\u4e25\u91cd\u5ea6',
  intervalMin: '\u6700\u77ed\u95f4\u9694\uff08\u5929\uff09',
  intervalMax: '\u6700\u957f\u95f4\u9694\uff08\u5929\uff09',
  probability: '\u89e6\u53d1\u6982\u7387',
  maxOccurrences: '\u6700\u5927\u6210\u529f\u89e6\u53d1\u6b21\u6570\uff080 = \u65e0\u9650\uff09',
  parent: '\u7236\u5b9e\u4f53',
  persona: 'Persona',
  personaText: '\u4eba\u683c\u63d0\u793a\u8bcd',
  personas: 'Personas',
  prompt: 'Prompt',
  promptBlocks: 'Prompt blocks',
  promptPreview: '\u7ec4\u5408\u9884\u89c8',
  prompts: 'Prompts',
  quantity: '\u6570\u91cf',
  relationType: '\u5173\u7cfb\u7c7b\u578b',
  strength: '\u5173\u7cfb\u5f3a\u5ea6',
  visibility: '\u53ef\u89c1\u6027',
  startsAt: '\u5f00\u59cb\u6a21\u62df\u65f6\u95f4',
  expiresAt: '\u5230\u671f\u6a21\u62df\u65f6\u95f4',
  dimensionValues: '\u591a\u7ef4\u5173\u7cfb\u503c',
  relations: '\u5173\u7cfb',
  requiredVariables: '\u5fc5\u9700\u53d8\u91cf\uff08\u9017\u53f7\u5206\u9694\uff09',
  resource: '\u8d44\u6e90',
  save: '\u4fdd\u5b58',
  severity: '\u4e25\u91cd\u5ea6',
  sourceAction: '\u6765\u6e90 Action ID',
  startTime: '\u5f00\u59cb\u6a21\u62df\u65f6\u95f4',
  targetAgents: '\u76ee\u6807 Agent',
  template: '\u6a21\u677f',
  value: '\u6570\u503c',
  values: '\u5b9e\u4f53\u6307\u6807',
  version: '\u7248\u672c',
}

const CAUSAL_RESERVED_FIELDS = [
  'causal_chain_id', 'causal_parent_event_id', 'causal_rule_id', 'causal_depth',
]

const EMPTY = {
  inventory: { entity_id: '', item_name: '', category: '', quantity: 0, description: '' },
  entity_values: { entity_id: '', name: '', kind: 'metric', value: 0, description: '' },
  relations: { parent_id: '', child_id: '', rel_type: 'subordinate', strength: 100, visibility: 'public', starts_at: 0, expires_at: '', values: {}, note: '', metadata: {} },
  initial_events: { event_id: '', event_type: 'world_event', severity: 5, target_agents: [], initial_payload: {}, generated_at: 0 },
  ongoing_events: { event_id: '', entity_id: '', event_type: 'state_change', description: '', start_time: 0, end_time: 0, expected_result: {}, source_action_id: '' },
  random_events: { template_id: '', event_type: 'world_event', target_agents: [], initial_payload: {}, severity_min: 3, severity_max: 7, min_interval_days: 30, max_interval_days: 90, probability: 1, max_occurrences: 0, enabled: true },
  causal_condition_groups: { group_id: '', name: '', description: '', conditions: [] },
  causal_rule_templates: { template_id: '', name: '', description: '', rule: { delay_days: 1, probability: 1, max_depth: 4, max_fires_per_chain: 1, priority: 0, enabled: true } },
  causal_event_rules: { rule_id: '', source_event_types: ['*'], child_event_type: 'follow_up', target_agents: [], delay_days: 1, probability: 1, severity_delta: 0, initial_payload: {}, inherit_payload_keys: [], conditions: [], world_effects: [], max_depth: 4, max_fires_per_chain: 1, priority: 0, enabled: true },
  time_rules: { rule_id: '', action_types: ['*'], base_days: 0, min_days: 0.1, max_days: 365, severity_factor: 0, workload_factor: 1, resource_factor: 0, distance_factor: 1, success_factor: 1, priority: 0, enabled: true },
  actions: { action_id: '', display_name: '', description: '', parameters: [], target: { kind: 'none' }, permissions: {}, preconditions: [], costs: [], duration: {}, cooldown: {}, result: { mode: 'world_commands', effects: [] }, enabled: true },
  capabilities: { capability_id: '', display_name: '', description: '', handler: 'message', audiences: ['player', 'agent', 'router'], aliases: [], target: { kind: 'agent', mode: 'optional', allowed_ids: [] }, parameters: [], action_id: '', default_urgency: 'normal', input: { content_required: true, content_label: '\u5185\u5bb9', content_placeholder: '' }, enabled: true },
  issue_types: { issue_type_id: '', display_name: '', description: '', category: 'report', default_urgency: 'normal', default_deadline_days: 3, source: { allowed_kinds: ['agent', 'system', 'event', 'player'] }, match: { payload_types: [], source_ids: [], default_for_player_reports: false }, evidence: [], decision_requests: [], metadata: {}, enabled: true },
}

const TITLES = {
  inventory: T.inventory,
  entity_values: T.values,
  relations: T.relations,
  initial_events: T.initialEvents,
  ongoing_events: T.ongoingEvents,
  random_events: T.randomEvents,
  causal_condition_groups: T.causalConditionGroups,
  causal_rule_templates: T.causalRuleTemplates,
  causal_event_rules: T.causalEvents,
  time_rules: T.timeRules,
  actions: '行动',
  capabilities: '\u80fd\u529b',
  issue_types: 'Issue 类型',
}

function deepClone(value) {
  return JSON.parse(JSON.stringify(value))
}

function jsonText(value) {
  return JSON.stringify(value && typeof value === 'object' && !Array.isArray(value) ? value : {}, null, 2)
}

function parseObject(value, label) {
  let parsed
  try {
    parsed = JSON.parse(value || '{}')
  } catch (error) {
    throw new Error(`${label}: ${error.message}`)
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error(`${label}: ${T.jsonObjectOnly}`)
  return parsed
}

function parseArray(value, label) {
  let parsed
  try {
    parsed = JSON.parse(value || '[]')
  } catch (error) {
    throw new Error(`${label}: ${error.message}`)
  }
  if (!Array.isArray(parsed)) throw new Error(`${label}: JSON array required`)
  return parsed
}


function normalizedListForm(kind, value) {
  const source = value ? deepClone(value) : deepClone(EMPTY[kind])
  if (kind === 'initial_events' || kind === 'random_events' || kind === 'causal_event_rules') source._payload_text = jsonText(source.initial_payload)
  if (kind === 'causal_condition_groups') source.conditions = (Array.isArray(source.conditions) ? source.conditions : []).map(normalizeCausalConditionForEditor)
  if (kind === 'causal_rule_templates') source._rule_text = JSON.stringify(source.rule && typeof source.rule === 'object' && !Array.isArray(source.rule) ? source.rule : {}, null, 2)
  if (kind === 'causal_event_rules') {
    source._source_types_text = (source.source_event_types || ['*']).join(', ')
    source.condition_group_ids = Array.isArray(source.condition_group_ids) ? source.condition_group_ids : []
    source._inherit_template_defaults = Boolean(source.template_id) && !['delay_days', 'probability', 'severity_delta', 'initial_payload', 'inherit_payload_keys', 'world_effects', 'max_depth', 'max_fires_per_chain', 'priority', 'enabled', 'target_agents'].some(key => Object.hasOwn(source, key))
    source._inherit_keys_text = (source.inherit_payload_keys || []).join(', ')
    source.conditions = (Array.isArray(source.conditions) ? source.conditions : []).map(normalizeCausalConditionForEditor)
    source.world_effects = normalizeWorldEffectsForEditor(source.world_effects)
  }
  if (kind === 'relations') {
    source._values_text = jsonText(source.values || source.dimension_values)
    source._metadata_text = jsonText(source.metadata)
    source.expires_at = source.expires_at ?? ''
  }
  if (kind === 'ongoing_events') source._result_text = jsonText(source.expected_result)
  if (kind === 'time_rules') source._action_types_text = (source.action_types || ['*']).join(', ')
  if (kind === 'issue_types') {
    for (const field of ['source', 'match', 'metadata']) source[`_${field}_text`] = jsonText(source[field])
    for (const field of ['evidence', 'decision_requests']) source[`_${field}_text`] = JSON.stringify(Array.isArray(source[field]) ? source[field] : [], null, 2)
  }
  if (kind === 'actions') {
    for (const field of ['parameters', 'preconditions', 'costs']) source[`_${field}_text`] = JSON.stringify(Array.isArray(source[field]) ? source[field] : [], null, 2)
    for (const field of ['target', 'permissions', 'duration', 'cooldown', 'result']) source[`_${field}_text`] = jsonText(source[field])
  }
  if (kind === 'capabilities') {
    source.audiences = Array.isArray(source.audiences) ? source.audiences : []
    source.aliases = Array.isArray(source.aliases) ? source.aliases : []
    source.target = source.target && typeof source.target === 'object' && !Array.isArray(source.target) ? source.target : { kind: 'none', mode: 'none', allowed_ids: [] }
    source.target.allowed_ids = Array.isArray(source.target.allowed_ids) ? source.target.allowed_ids : []
    source.input = source.input && typeof source.input === 'object' && !Array.isArray(source.input) ? source.input : {}
    source.input.content_required = Boolean(source.input.content_required)
    source.input.content_label = String(source.input.content_label || '\u5185\u5bb9')
    source.input.content_placeholder = String(source.input.content_placeholder || '')
    source._aliases_text = source.aliases.join(', ')
    source._allowed_ids_text = source.target.allowed_ids.join(', ')
    source._parameters_text = JSON.stringify(Array.isArray(source.parameters) ? source.parameters : [], null, 2)
  }
  return source
}

function serializeListForm(kind, form) {
  const data = { ...form }
  if (kind === 'initial_events') {
    data.initial_payload = parseObject(data._payload_text, T.initialPayload)
    delete data._payload_text
    const reserved = CAUSAL_RESERVED_FIELDS.filter(key => Object.hasOwn(data.initial_payload, key))
    if (reserved.length) throw new Error(`Reserved payload fields: ${reserved.join(', ')}`)
    data.target_agents = [...new Set(data.target_agents || [])]
    data.severity = Number(data.severity)
    data.generated_at = Number(data.generated_at || 0)
  }
  if (kind === 'random_events') {
    data.initial_payload = parseObject(data._payload_text, T.initialPayload)
    delete data._payload_text
    const reserved = ['random_event_template_id', 'occurrence', ...CAUSAL_RESERVED_FIELDS].filter(key => Object.hasOwn(data.initial_payload, key))
    if (reserved.length) throw new Error(`Reserved payload fields: ${reserved.join(', ')}`)
    data.target_agents = [...new Set(data.target_agents || [])]
    data.severity_min = Number(data.severity_min)
    data.severity_max = Number(data.severity_max)
    data.min_interval_days = Number(data.min_interval_days)
    data.max_interval_days = Number(data.max_interval_days)
    data.probability = Number(data.probability)
    data.max_occurrences = Number(data.max_occurrences)
    data.enabled = Boolean(data.enabled)
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(data.template_id || '')
      || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(data.event_type || '')) throw new Error('Template ID and event type use letters, numbers, dot, underscore, or hyphen (1-64 chars).')
    if (!data.target_agents.length) throw new Error('Select at least one target Agent.')
    if (!Number.isInteger(data.severity_min) || !Number.isInteger(data.severity_max)
      || data.severity_min < 1 || data.severity_max > 10 || data.severity_min > data.severity_max) throw new Error('Severity range must use integers from 1 to 10.')
    if (!Number.isFinite(data.min_interval_days) || !Number.isFinite(data.max_interval_days)
      || data.min_interval_days <= 0 || data.min_interval_days > data.max_interval_days) throw new Error('Interval days must be positive and min cannot exceed max.')
    if (!Number.isFinite(data.probability) || data.probability < 0 || data.probability > 1) throw new Error('Probability must be from 0 to 1.')
    if (!Number.isInteger(data.max_occurrences) || data.max_occurrences < 0) throw new Error('Max occurrences must be a non-negative integer.')
  }
  if (kind === 'causal_condition_groups') {
    data.group_id = String(data.group_id || '').trim()
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(data.group_id)) throw new Error('Condition group ID must be a safe identifier.')
    data.conditions = serializeCausalConditions(data.conditions, { requireOne: true })
  }
  if (kind === 'causal_rule_templates') {
    data.template_id = String(data.template_id || '').trim()
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(data.template_id)) throw new Error('Template ID must be a safe identifier.')
    data.rule = parseObject(data._rule_text, 'Template rule defaults')
    delete data._rule_text
    if (Object.hasOwn(data.rule, 'template_id') || Object.hasOwn(data.rule, 'condition_group_ids')) throw new Error('Templates cannot reference another template or condition groups.')
  }
  if (kind === 'causal_event_rules') {
    const inheritTemplateDefaults = Boolean(data.template_id && data._inherit_template_defaults)
    data.initial_payload = parseObject(data._payload_text, T.initialPayload)
    data.conditions = serializeCausalConditions(data.conditions)
    data.world_effects = serializeWorldEffects(data.world_effects)
    delete data._payload_text
    data.source_event_types = [...new Set(String(data._source_types_text || '').split(',').map(value => value.trim()).filter(Boolean))]
    data.inherit_payload_keys = [...new Set(String(data._inherit_keys_text || '').split(',').map(value => value.trim()).filter(Boolean))]
    delete data._source_types_text
    delete data._inherit_keys_text
    data.target_agents = [...new Set(data.target_agents || [])]
    for (const field of ['delay_days', 'probability', 'severity_delta', 'max_depth', 'max_fires_per_chain', 'priority']) data[field] = Number(data[field])
    data.enabled = Boolean(data.enabled)
    const safeId = value => /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(value || '')
    if (!safeId(data.rule_id) || !safeId(data.child_event_type)) throw new Error('Rule ID and child event type must be safe identifiers.')
    if (!data.source_event_types.length || data.source_event_types.some(value => value !== '*' && !safeId(value))) throw new Error('Source event types must be safe identifiers or *.')
    if (!Number.isFinite(data.delay_days) || data.delay_days < 0) throw new Error('Delay days must be finite and non-negative.')
    if (!Number.isFinite(data.probability) || data.probability < 0 || data.probability > 1) throw new Error('Probability must be from 0 to 1.')
    if (!Number.isInteger(data.severity_delta) || data.severity_delta < -9 || data.severity_delta > 9) throw new Error('Severity delta must be an integer from -9 to 9.')
    if (!Number.isInteger(data.max_depth) || data.max_depth < 1 || data.max_depth > 16) throw new Error('Max depth must be an integer from 1 to 16.')
    if (!Number.isInteger(data.max_fires_per_chain) || data.max_fires_per_chain < 1 || data.max_fires_per_chain > 64) throw new Error('Max fires per chain must be an integer from 1 to 64.')
    if (!Number.isInteger(data.priority)) throw new Error('Priority must be an integer.')
    if (CAUSAL_RESERVED_FIELDS.some(key => Object.hasOwn(data.initial_payload, key) || data.inherit_payload_keys.includes(key))) throw new Error('Causal metadata fields are reserved.')
    delete data._inherit_template_defaults
    if (inheritTemplateDefaults) {
      for (const field of ['target_agents', 'delay_days', 'probability', 'severity_delta', 'initial_payload', 'inherit_payload_keys', 'world_effects', 'max_depth', 'max_fires_per_chain', 'priority', 'enabled']) delete data[field]
    }
  }
  if (kind === 'issue_types') {
    data.issue_type_id = String(data.issue_type_id || '').trim()
    data.display_name = String(data.display_name || '').trim()
    if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(data.issue_type_id)) throw new Error('Issue type ID must be a safe identifier.')
    if (!data.display_name) throw new Error('Issue display name is required.')
    for (const field of ['source', 'match', 'metadata']) { data[field] = parseObject(data[`_${field}_text`], field); delete data[`_${field}_text`] }
    for (const field of ['evidence', 'decision_requests']) { data[field] = parseArray(data[`_${field}_text`], field); delete data[`_${field}_text`] }
    data.default_deadline_days = data.default_deadline_days === '' ? null : Number(data.default_deadline_days)
    data.enabled = Boolean(data.enabled)
  }
  if (kind === 'actions') {
    data.action_id = String(data.action_id || '').trim()
    data.display_name = String(data.display_name || '').trim()
    if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$/.test(data.action_id)) throw new Error('Action ID must be a safe identifier.')
    if (!data.display_name) throw new Error('Action display name is required.')
    for (const field of ['parameters', 'preconditions', 'costs']) {
      data[field] = parseArray(data[`_${field}_text`], field)
      delete data[`_${field}_text`]
    }
    for (const field of ['target', 'permissions', 'duration', 'cooldown', 'result']) {
      data[field] = parseObject(data[`_${field}_text`], field)
      delete data[`_${field}_text`]
    }
    data.enabled = Boolean(data.enabled)
  }
  if (kind === 'capabilities') {
    data.capability_id = String(data.capability_id || '').trim()
    data.display_name = String(data.display_name || '').trim()
    data.description = String(data.description || '').trim()
    data.handler = String(data.handler || 'noop').trim()
    data.audiences = [...new Set((data.audiences || []).map(value => String(value).trim()).filter(Boolean))]
    data.aliases = [...new Set(String(data._aliases_text || '').split(',').map(value => value.trim()).filter(Boolean))]
    data.parameters = parseArray(data._parameters_text, '\u80fd\u529b\u53c2\u6570')
    data.action_id = String(data.action_id || '').trim()
    data.default_urgency = String(data.default_urgency || 'normal').trim()
    data.target = {
      kind: String(data.target?.kind || 'none').trim(),
      mode: String(data.target?.mode || 'none').trim(),
      allowed_ids: [...new Set(String(data._allowed_ids_text || '').split(',').map(value => value.trim()).filter(Boolean))],
    }
    data.input = {
      content_required: Boolean(data.input?.content_required),
      content_label: String(data.input?.content_label || '\u5185\u5bb9').trim(),
      content_placeholder: String(data.input?.content_placeholder || '').trim(),
    }
    data.enabled = Boolean(data.enabled)
    for (const field of ['_aliases_text', '_allowed_ids_text', '_parameters_text']) delete data[field]
    const safeId = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$/
    if (!safeId.test(data.capability_id)) throw new Error('\u80fd\u529b ID \u5fc5\u987b\u662f 1-64 \u4f4d\u5b89\u5168\u6807\u8bc6\u7b26\uff0c\u4ec5\u4f7f\u7528\u5b57\u6bcd\u3001\u6570\u5b57\u3001. _ : -\u3002')
    if (!data.display_name) throw new Error('\u663e\u793a\u540d\u79f0\u4e0d\u80fd\u4e3a\u7a7a\u3002')
    if (!['noop', 'message', 'broadcast', 'world_action', 'contract_action'].includes(data.handler)) throw new Error('\u8bf7\u9009\u62e9\u6846\u67b6\u652f\u6301\u7684 handler\u3002')
    if (!data.audiences.length || data.audiences.some(value => !['player', 'agent', 'router'].includes(value))) throw new Error('\u81f3\u5c11\u9009\u62e9\u4e00\u4e2a\u53ef\u7528\u5bf9\u8c61\u3002')
    if (data.aliases.some(value => !safeId.test(value)) || data.aliases.includes(data.capability_id)) throw new Error('\u522b\u540d\u5fc5\u987b\u662f\u5b89\u5168\u6807\u8bc6\u7b26\uff0c\u4e14\u4e0d\u80fd\u4e0e\u80fd\u529b ID \u76f8\u540c\u3002')
    if (!['none', 'agent', 'entity'].includes(data.target.kind) || !['none', 'optional', 'required', 'multiple'].includes(data.target.mode)) throw new Error('\u76ee\u6807\u7c7b\u578b\u6216\u9009\u62e9\u6a21\u5f0f\u65e0\u6548\u3002')
    if ((data.target.kind === 'none') !== (data.target.mode === 'none')) throw new Error('\u65e0\u76ee\u6807\u7c7b\u578b\u5fc5\u987b\u642d\u914d none \u6a21\u5f0f\uff1bAgent/\u5b9e\u4f53\u76ee\u6807\u4e0d\u80fd\u4f7f\u7528 none \u6a21\u5f0f\u3002')
    if (data.handler === 'noop' && (data.target.kind !== 'none' || data.input.content_required || data.parameters.length)) throw new Error('\u7a7a\u64cd\u4f5c\u80fd\u529b\u4e0d\u80fd\u8981\u6c42\u76ee\u6807\u3001\u5185\u5bb9\u6216\u53c2\u6570\u3002')
    if (['message', 'broadcast'].includes(data.handler) && data.target.kind !== 'agent') throw new Error('\u6d88\u606f\u4e0e\u5e7f\u64ad\u80fd\u529b\u7684\u76ee\u6807\u7c7b\u578b\u5fc5\u987b\u662f Agent\u3002')
    if (data.handler === 'contract_action' && !safeId.test(data.action_id)) throw new Error('\u5951\u7ea6 Action \u80fd\u529b\u5fc5\u987b\u9009\u62e9\u6709\u6548 Action ID\u3002')
    if (data.handler !== 'contract_action') data.action_id = ''
    if (!['normal', 'urgent', 'critical'].includes(data.default_urgency)) throw new Error('\u9ed8\u8ba4\u7d27\u6025\u5ea6\u65e0\u6548\u3002')
    if (!data.input.content_label) throw new Error('\u5185\u5bb9\u5b57\u6bb5\u6807\u7b7e\u4e0d\u80fd\u4e3a\u7a7a\u3002')
  }
  if (kind === 'time_rules') {
    data.action_types = [...new Set(String(data._action_types_text || '').split(',').map(value => value.trim()).filter(Boolean))]
    delete data._action_types_text
    for (const field of ['base_days', 'min_days', 'max_days', 'severity_factor', 'workload_factor', 'resource_factor', 'distance_factor', 'success_factor']) data[field] = Number(data[field])
    data.priority = Number(data.priority)
    data.enabled = Boolean(data.enabled)
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(data.rule_id || '')) throw new Error('Rule ID uses letters, numbers, dot, underscore, or hyphen (1-64 chars).')
    if (!data.action_types.length || data.action_types.some(value => !/^(?:\*|[A-Za-z0-9][A-Za-z0-9._:-]{0,63})$/.test(value))) throw new Error('Action types must be safe identifiers or *.')
    const numbers = ['base_days', 'min_days', 'max_days', 'severity_factor', 'workload_factor', 'resource_factor', 'distance_factor', 'success_factor']
    if (numbers.some(field => !Number.isFinite(data[field]) || data[field] < 0)) throw new Error('Time factors must be finite non-negative numbers.')
    if (data.max_days < data.min_days) throw new Error('max_days must be greater than or equal to min_days.')
    if (!Number.isInteger(data.priority)) throw new Error('Priority must be an integer.')
  }
  if (kind === 'relations') {
    data.parent_id = String(data.parent_id || '').trim()
    data.child_id = String(data.child_id || '').trim()
    data.rel_type = String(data.rel_type || '').trim()
    data.strength = Number(data.strength)
    data.visibility = String(data.visibility || 'public')
    data.starts_at = Number(data.starts_at || 0)
    data.expires_at = data.expires_at === '' || data.expires_at == null ? null : Number(data.expires_at)
    data.values = parseObject(data._values_text, T.dimensionValues)
    data.metadata = parseObject(data._metadata_text, T.metadata)
    data.note = String(data.note || '')
    delete data._values_text
    delete data._metadata_text
    if (!Number.isFinite(data.strength)) throw new Error('Relationship strength must be a finite number.')
    if (!Number.isFinite(data.starts_at)) throw new Error('starts_at must be a finite number.')
    if (data.expires_at !== null && (!Number.isFinite(data.expires_at) || data.expires_at <= data.starts_at)) throw new Error('expires_at must be later than starts_at.')
  }
  if (kind === 'ongoing_events') {
    data.expected_result = parseObject(data._result_text, T.expectedResult)
    delete data._result_text
    data.start_time = Number(data.start_time || 0)
    data.end_time = Number(data.end_time || 0)
  }
  if (kind === 'inventory') {
    data.quantity = Number(data.quantity || 0)
    data.category = String(data.category || data.item_name || '')
    delete data.anchor_rate
  }
  if (kind === 'entity_values') data.value = Number(data.value || 0)
  return data
}

function listLabel(kind, item) {
  if (kind === 'inventory') return [item.item_name || T.resource, item.entity_id]
  if (kind === 'entity_values') return [item.name || T.value, item.entity_id]
  if (kind === 'relations') return [`${item.parent_id || '?'} \u2192 ${item.child_id || '?'}`, item.rel_type]
  if (kind === 'initial_events') return [item.event_id || T.eventId, `${item.event_type || ''} \u00b7 ${item.target_agents?.length || 0} agents`]
  if (kind === 'random_events') return [item.template_id || T.templateId, `${item.event_type || ''} \u00b7 ${item.target_agents?.length || 0} agents`]
  if (kind === 'causal_condition_groups') return [item.group_id || 'Group ID', `${item.conditions?.length || 0} conditions`]
  if (kind === 'causal_rule_templates') return [item.template_id || T.templateId, item.name || 'Reusable defaults']
  if (kind === 'causal_event_rules') return [item.rule_id || T.ruleId, `${(item.source_event_types || []).join(', ')} \u2192 ${item.child_event_type || ''}`]
  if (kind === 'time_rules') return [item.rule_id || T.ruleId, `${(item.action_types || []).join(', ')} \u00b7 p${item.priority || 0}`]
  if (kind === 'actions') return [item.display_name || item.action_id || '行动', item.action_id || '']
  if (kind === 'capabilities') return [item.display_name || item.capability_id || '\u80fd\u529b', `${item.capability_id || ''} \u00b7 ${item.handler || 'noop'}`]
  if (kind === 'issue_types') return [item.display_name || item.issue_type_id || 'Issue 类型', `${item.issue_type_id || ''} · ${item.default_urgency || 'normal'}`]
  return [item.event_id || T.eventId, `${item.entity_id || ''} \u00b7 ${item.event_type || ''}`]
}

function requiredKey(kind, form) {
  if (kind === 'inventory') return form.entity_id && form.item_name
  if (kind === 'entity_values') return form.entity_id && form.name
  if (kind === 'relations') return form.parent_id && form.child_id && form.rel_type
  if (kind === 'initial_events') return form.event_id
  if (kind === 'ongoing_events') return form.event_id && form.entity_id
  if (kind === 'random_events') return form.template_id && form.event_type && (form.target_agents || []).length
  if (kind === 'causal_condition_groups') return form.group_id && (form.conditions || []).length
  if (kind === 'causal_rule_templates') return form.template_id
  if (kind === 'causal_event_rules') return form.rule_id && form.child_event_type && String(form._source_types_text || '').trim()
  if (kind === 'time_rules') return form.rule_id && String(form._action_types_text || '').trim()
  if (kind === 'actions') return form.action_id && form.display_name
  if (kind === 'capabilities') return form.capability_id && form.display_name && (form.audiences || []).length && (form.handler !== 'contract_action' || form.action_id)
  if (kind === 'issue_types') return form.issue_type_id && form.display_name
  return false
}

function EntitySelect({ value, entities, onChange, label = T.entity }) {
  return <label>{label}<select value={value || ''} onChange={event => onChange(event.target.value)}><option value="">--</option>{entities.map(item => <option key={item.id} value={item.id}>{item.name || item.id} ({item.id})</option>)}</select></label>
}

function AgentTargets({ value, agents, onChange }) {
  const selected = new Set(value || [])
  const toggle = id => {
    const next = new Set(selected)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    onChange([...next])
  }
  return <fieldset className="dlc-targets"><legend>{T.targetAgents}</legend>{agents.length ? agents.map(agent => <label key={agent.id}><input type="checkbox" checked={selected.has(agent.id)} onChange={() => toggle(agent.id)} /><span>{agent.name || agent.id}</span><code>{agent.id}</code></label>) : <small>No agents</small>}</fieldset>
}

function EditorError({ children }) {
  return children ? <div className="dlc-editor-error">{children}</div> : null
}

function resourcesForCondition(resources, kind) {
  const resourceKind = kind === 'anchored_inventory' ? 'asset' : kind
  return resources.filter(item => item.kind === resourceKind)
}

function effectiveActorOptions(actors, agents) {
  const byId = new Map()
  for (const actor of actors || []) {
    const actorId = actor.actor_id || actor.id
    if (actorId) byId.set(actorId, { ...actor, actor_id: actorId, _projected_agent: false })
  }
  for (const agent of agents || []) {
    const actorId = agent.actor_id || agent.id
    if (actorId && !byId.has(actorId)) byId.set(actorId, {
      actor_id: actorId,
      display_name: agent.name || agent.display_name || actorId,
      _projected_agent: true,
    })
  }
  return [...byId.values()]
}

function actorComponentNames(actor, kind) {
  if (!actor) return []
  if (kind === 'actor_attribute') return Object.keys(actor.attributes || {})
  if (kind === 'actor_state') return Object.keys(actor.states || {})
  if (kind === 'actor_trait') return (actor.traits || []).map(item => item.trait_id).filter(Boolean)
  if (kind === 'actor_membership') return (actor.memberships || []).map(item => item.organization_id).filter(Boolean)
  return []
}

function conditionNameLabel(kind) {
  return {
    anchored_inventory: 'Asset 资源', metric: 'Metric 指标', parameter: 'Parameter 参数',
    entity_tag: '标签', actor_attribute: 'Attribute 名称', actor_state: 'State 名称',
    actor_trait: 'Trait ID', actor_membership: 'Organization ID', relationship: '关系类型',
    ongoing_event: '事件类型（可选）', event_history: '事件类型（可选）',
    player_knowledge: '知识键',
  }[kind] || '名称'
}

function CausalConditionBuilder({ value, entities, resources, actors, agents, relationshipTypes, objectives, ongoingEvents, eventTypes, onChange }) {
  const conditions = (Array.isArray(value) ? value : []).map(normalizeCausalConditionForEditor)
  const actorOptions = effectiveActorOptions(actors, agents)
  const catalogs = { entities, resources, actors: actorOptions, relationshipTypes, objectives, ongoingEvents, eventTypes }
  const update = (index, name, nextValue) => {
    const next = conditions.map(item => ({ ...item }))
    next[index][name] = nextValue
    onChange(next)
  }
  const updateMany = (index, values) => {
    const next = conditions.map(item => ({ ...item }))
    next[index] = { ...next[index], ...values }
    onChange(next)
  }
  const changeKind = (index, kind) => {
    const next = conditions.map(item => ({ ...item }))
    next[index] = defaultCausalCondition(kind, catalogs)
    onChange(next)
  }
  const add = () => onChange([...conditions, defaultCausalCondition('anchored_inventory', catalogs)])
  const remove = index => onChange(conditions.filter((_, itemIndex) => itemIndex !== index))

  return <section className="dlc-condition-builder">
    <div className="dlc-condition-header"><div><strong>通用因果条件</strong><small>同一组内所有条件必须同时成立；编辑器与运行时、Dry Run 使用相同语义，无需手写完整条件 JSON。</small></div><button type="button" onClick={add}>＋ 添加条件</button></div>
    {!conditions.length && <div className="dlc-condition-empty">当前没有附加条件：事件类型匹配后会直接进入概率与调度判断。</div>}
    {conditions.map((condition, index) => {
      const kindDefinition = CAUSAL_CONDITION_KINDS.find(item => item.key === condition.kind)
      const resourceCandidates = resourcesForCondition(resources, condition.kind)
      const selectedResource = resourceCandidates.find(item => item.name === condition.name)
      const selectedActor = actorOptions.find(item => item.actor_id === condition.entity_id)
      const componentNames = actorComponentNames(selectedActor, condition.kind)
      const needsValue = !['exists', 'not_exists'].includes(condition.operator)
      const aggregateSource = condition.field || 'entity_count'
      const aggregateResources = ['anchored_inventory', 'metric', 'parameter'].includes(aggregateSource) ? resourcesForCondition(resources, aggregateSource) : []
      const nameOptions = aggregateSource === 'relation'
        ? relationshipTypes.map(item => ({ key: item.relationship_type_id, label: item.display_name || item.relationship_type_id }))
        : aggregateSource === 'ongoing_event'
          ? [...new Set((ongoingEvents || []).map(item => item.event_type).filter(Boolean))].map(key => ({ key, label: key }))
          : aggregateResources.map(item => ({ key: item.name, label: `${item.name}${item.unit ? `（${item.unit}）` : ''}` }))
      return <article className="dlc-condition-card" key={`condition-${index}`}>
        <div className="dlc-condition-toolbar">
          <strong>条件 #{index + 1}</strong>
          <label>条件来源<select value={condition.kind} onChange={event => changeKind(index, event.target.value)}>{CAUSAL_CONDITION_KINDS.map(item => <option key={item.key} value={item.key}>{item.label}</option>)}</select></label>
          <label>比较方式<select value={condition.operator} onChange={event => update(index, 'operator', event.target.value)}>{CAUSAL_CONDITION_OPERATORS.map(item => <option key={item.key} value={item.key}>{item.label}</option>)}</select></label>
          <button type="button" className="danger" onClick={() => remove(index)}>删除</button>
        </div>
        <p className="dlc-condition-help">{kindDefinition?.help}</p>
        <div className="dlc-condition-grid">
          {['anchored_inventory', 'metric', 'parameter'].includes(condition.kind) && <>
            <EntitySelect value={condition.entity_id} entities={entities} onChange={next => update(index, 'entity_id', next)} />
            <label>{conditionNameLabel(condition.kind)}<select value={condition.name} onChange={event => update(index, 'name', event.target.value)}><option value="">-- 请选择 --</option>{resourceCandidates.map(item => <option key={item.name} value={item.name}>{item.name}{item.unit ? `（${item.unit}）` : ''}</option>)}</select><small>资源类型必须与条件来源一致，Registry 会再次校验跨引用。</small></label>
          </>}

          {condition.kind === 'entity_field' && <><EntitySelect value={condition.entity_id} entities={entities} onChange={next => update(index, 'entity_id', next)} /><label>字段路径<input value={condition.field || condition.name} onChange={event => updateMany(index, { field: event.target.value, name: '' })} placeholder="name 或 metadata.population" /><small>直接字段示例：name、type；Metadata 示例：metadata.population。</small></label></>}
          {condition.kind === 'entity_tag' && <><EntitySelect value={condition.entity_id} entities={entities} onChange={next => update(index, 'entity_id', next)} /><label>标签<input value={condition.name} onChange={event => update(index, 'name', event.target.value)} placeholder="frontier" /><small>实际值为“实体是否含此标签”的布尔值，通常与“等于 true”搭配。</small></label></>}
          {condition.kind === 'entity_state' && <><EntitySelect value={condition.entity_id} entities={entities} onChange={next => update(index, 'entity_id', next)} /><label>读取字段<input value="current_status" disabled /><small>实体状态固定读取 current_status。</small></label></>}

          {condition.kind.startsWith('actor_') && <>
            <label>Actor<select value={condition.entity_id} onChange={event => update(index, 'entity_id', event.target.value)}><option value="">-- 请选择 --</option>{actorOptions.map(actor => <option key={actor.actor_id} value={actor.actor_id}>{actor.display_name || actor.actor_id}（{actor.actor_id}{actor._projected_agent ? ' · Agent 兼容投影' : ''}）</option>)}</select><small>优先使用显式 Actor；未定义同名 Actor 的 Agent 会作为兼容选项出现。</small></label>
            <label>{conditionNameLabel(condition.kind)}<input list={`causal-actor-components-${index}`} value={condition.name} onChange={event => update(index, 'name', event.target.value)} placeholder={condition.kind === 'actor_membership' ? 'organization_id' : '组件名称'} /><datalist id={`causal-actor-components-${index}`}>{componentNames.map(name => <option key={name} value={name} />)}</datalist><small>列表来自当前 Actor 定义，也允许输入之后准备新增的组件 ID。</small></label>
            {condition.kind === 'actor_membership' && <label>成员关系字段（可选）<input list="causal-membership-fields" value={condition.field} onChange={event => update(index, 'field', event.target.value)} placeholder="留空表示判断成员关系存在" /><datalist id="causal-membership-fields"><option value="role" /><option value="status" /><option value="title" /><option value="weight" /></datalist><small>留空时实际值为 true；填写后读取该 Membership 对象中的字段。</small></label>}
          </>}

          {condition.kind === 'relationship' && <>
            <EntitySelect label="来源实体" value={condition.entity_id} entities={entities} onChange={next => update(index, 'entity_id', next)} />
            <EntitySelect label="目标实体" value={condition.target_entity_id} entities={entities} onChange={next => update(index, 'target_entity_id', next)} />
            <label>关系类型<select value={condition.name} onChange={event => update(index, 'name', event.target.value)}><option value="">-- 请选择 --</option>{relationshipTypes.map(item => <option key={item.relationship_type_id} value={item.relationship_type_id}>{item.display_name || item.relationship_type_id}（{item.relationship_type_id}）</option>)}</select><small>无向关系会自动接受反向端点；有向关系严格按来源 → 目标匹配。</small></label>
            <label>读取字段<input list={`causal-relation-fields-${index}`} value={condition.field} onChange={event => update(index, 'field', event.target.value)} placeholder="strength" /><datalist id={`causal-relation-fields-${index}`}><option value="strength" /><option value="exists" /><option value="active" />{relationshipTypes.find(item => item.relationship_type_id === condition.name)?.dimensions?.map(dimension => <option key={dimension.dimension_id || dimension.id} value={`values.${dimension.dimension_id || dimension.id}`} />)}</datalist><small>支持 strength、exists、active、values.&lt;dimension_id&gt;、metadata.&lt;key&gt;。</small></label>
          </>}

          {condition.kind === 'ongoing_event' && <>
            <EntitySelect label="关联实体（可选）" value={condition.entity_id} entities={entities} onChange={next => update(index, 'entity_id', next)} />
            <label>事件类型（可选）<input list={`causal-ongoing-types-${index}`} value={condition.name} onChange={event => update(index, 'name', event.target.value)} placeholder="relief_operation" /><datalist id={`causal-ongoing-types-${index}`}>{[...new Set((ongoingEvents || []).map(item => item.event_type).filter(Boolean))].map(name => <option key={name} value={name} />)}</datalist><small>留空统计全部持续事件。</small></label>
            <label>读取字段<select value={condition.field || 'count'} onChange={event => update(index, 'field', event.target.value)}><option value="count">数量 count</option><option value="exists">是否存在 exists</option><option value="active">是否激活 active</option><option value="remaining_days">最短剩余天数</option></select></label>
          </>}

          {condition.kind === 'event_history' && <>
            <label>目标 Agent（可选）<select value={condition.entity_id} onChange={event => update(index, 'entity_id', event.target.value)}><option value="">全部 Agent</option>{agents.map(agent => <option key={agent.id} value={agent.id}>{agent.name || agent.id}（{agent.id}）</option>)}</select><small>此处 entity_id 表示历史事件 target_agents 中的 Agent ID。</small></label>
            <label>事件类型（可选）<input list={`causal-history-types-${index}`} value={condition.name} onChange={event => update(index, 'name', event.target.value)} placeholder="shortage" /><datalist id={`causal-history-types-${index}`}>{eventTypes.map(name => <option key={name} value={name} />)}</datalist><small>留空统计时间窗口内的全部脱敏历史事件。</small></label>
            <label>读取字段<select value={condition.field || 'count'} onChange={event => update(index, 'field', event.target.value)}><option value="count">数量 count</option><option value="exists">是否出现 exists</option><option value="seen">是否见过 seen</option><option value="last_generated_at">最后生成时间（秒）</option></select></label>
            <label>时间窗口（天）<input type="number" min="0" max="36500" step="any" value={condition.window_days} onChange={event => update(index, 'window_days', event.target.value)} /><small>0 表示只看当前模拟时刻；留空表示全部有界历史。</small></label>
          </>}

          {condition.kind === 'time' && <label>时间口径<select value={condition.name || 'sim_day'} onChange={event => update(index, 'name', event.target.value)}><option value="sim_day">模拟天数 sim_day</option><option value="sim_time">模拟秒数 sim_time</option><option value="seconds">模拟秒数 seconds</option></select><small>建议规则设计使用 sim_day，便于 DLC 作者阅读与复现。</small></label>}

          {condition.kind === 'objective_progress' && <>
            <label>Objective<select value={condition.entity_id || condition.name} onChange={event => updateMany(index, { entity_id: event.target.value, name: '' })}><option value="">-- 请选择 --</option>{objectives.map(item => <option key={item.objective_id} value={item.objective_id}>{item.display_name || item.objective_id}（{item.objective_id}）</option>)}</select></label>
            <label>公开状态字段<input list="causal-objective-fields" value={condition.field || 'progress'} onChange={event => update(index, 'field', event.target.value)} /><datalist id="causal-objective-fields"><option value="progress" /><option value="status" /><option value="completed" /><option value="failed" /></datalist><small>常用 progress（数值）或 status（文本）；实际可读字段取决于 Objective 公共状态。</small></label>
          </>}

          {condition.kind === 'aggregate_metric' && <>
            <label>聚合来源<select value={aggregateSource} onChange={event => { const source = event.target.value; const next = conditions.map(item => ({ ...item })); next[index] = defaultCausalCondition('aggregate_metric', catalogs); next[index].field = source; next[index].aggregate = source === 'entity_count' || source === 'ongoing_event' ? 'count' : 'sum'; if (['anchored_inventory', 'metric', 'parameter'].includes(source)) next[index].name = resourcesForCondition(resources, source)[0]?.name || ''; if (source === 'relation') next[index].name = relationshipTypes[0]?.relationship_type_id || ''; onChange(next) }} >{CAUSAL_AGGREGATE_SOURCES.map(item => <option key={item.key} value={item.key}>{item.label}</option>)}</select><small>来源决定 name、entity_id 与过滤器的解释方式。</small></label>
            <label>聚合方法<select value={condition.aggregate || 'sum'} onChange={event => update(index, 'aggregate', event.target.value)}>{CAUSAL_AGGREGATES.map(item => <option key={item.key} value={item.key}>{item.label}</option>)}</select></label>
            {['entity_count', 'anchored_inventory', 'metric', 'parameter', 'ongoing_event'].includes(aggregateSource) && <EntitySelect label="限定实体（可选）" value={condition.entity_id} entities={entities} onChange={next => update(index, 'entity_id', next)} />}
            {aggregateSource === 'actor_attribute' && <label>限定 Actor（可选）<select value={condition.entity_id} onChange={event => update(index, 'entity_id', event.target.value)}><option value="">全部 Actor</option>{actorOptions.map(actor => <option key={actor.actor_id} value={actor.actor_id}>{actor.display_name || actor.actor_id}（{actor.actor_id}）</option>)}</select></label>}
            {aggregateSource !== 'entity_count' && <label>{aggregateSource === 'actor_attribute' ? 'Attribute 名称' : aggregateSource === 'relation' ? '关系类型（可选）' : aggregateSource === 'ongoing_event' ? '事件类型（可选）' : '资源 / 数值名称'}{nameOptions.length ? <select value={condition.name} onChange={event => update(index, 'name', event.target.value)}><option value="">-- 全部 / 请选择 --</option>{nameOptions.map(item => <option key={item.key} value={item.key}>{item.label}</option>)}</select> : <input value={condition.name} onChange={event => update(index, 'name', event.target.value)} placeholder="名称" />}<small>资源与 Actor Attribute 必填；关系和持续事件可留空统计全部。</small></label>}
          </>}

          {condition.kind === 'player_knowledge' && <>
            <EntitySelect label="关联实体（可选）" value={condition.entity_id} entities={entities} onChange={next => update(index, 'entity_id', next)} />
            <label>知识键<input value={condition.name} onChange={event => update(index, 'name', event.target.value)} placeholder="known_secret_route" /><small>由游戏的知识 Provider 定义，DLC 不得假设该键一定存在。</small></label>
            <label>知识字段（可选）<input value={condition.field} onChange={event => update(index, 'field', event.target.value)} placeholder="confidence" /></label>
            <div className="dlc-condition-warning">隐私边界：这里只读取玩家已经知道的信息；没有知识 Provider 时条件按缺失处理，不会回退读取世界真相。</div>
          </>}

          {condition.kind === 'phase' && <>
            <label>阶段字段<input list="causal-phase-fields" value={condition.field || 'phase_id'} onChange={event => update(index, 'field', event.target.value)} placeholder="phase_id 或 metadata.chapter" /><datalist id="causal-phase-fields"><option value="phase_id" /><option value="metadata.chapter" /><option value="metadata.label" /></datalist><small>使用 phase_id 读取阶段 ID；使用 metadata.xxx 读取 switch_phase 写入的阶段元数据。</small></label>
            <div className="dlc-condition-anchor">阶段状态由 switch_phase 效果原子更新，可用于下一条因果规则判断章节、模式或流程节点。</div>
          </>}
        </div>

        {needsValue ? <div className="dlc-condition-value-row">
          <label>目标值类型<select value={condition._value_type} onChange={event => { const type = event.target.value; updateMany(index, { _value_type: type, _value_text: type === 'number' ? '0' : type === 'boolean' ? 'true' : type === 'list' ? '[]' : '' }) }}>{CAUSAL_VALUE_TYPES.map(item => <option key={item.key} value={item.key}>{item.label}</option>)}</select></label>
          {condition._value_type === 'boolean' ? <label>目标值<select value={condition._value_text} onChange={event => update(index, '_value_text', event.target.value)}><option value="true">true / 是</option><option value="false">false / 否</option></select></label>
            : condition._value_type === 'list' ? <label className="dlc-condition-wide">目标值列表<textarea rows="3" spellCheck="false" value={condition._value_text} onChange={event => update(index, '_value_text', event.target.value)} placeholder='["mobilized", "stable"]' /><small>必须是 JSON 数组，最多 64 项；适合“属于列表 / 不属于列表”。</small></label>
              : <label>目标值<input type={condition._value_type === 'number' ? 'number' : 'text'} step="any" value={condition._value_text} onChange={event => update(index, '_value_text', event.target.value)} /><small>{condition._value_type === 'number' ? '必须是有限数字。' : '最长 512 个字符。'}</small></label>}
        </div> : <div className="dlc-condition-existence-note">“{condition.operator === 'exists' ? '存在' : '不存在'}”操作符不需要目标值；它只判断事实是否能够被安全解析。</div>}

        <details className="dlc-condition-advanced"><summary>高级过滤器 JSON（可选）</summary><label>Filters<textarea rows="4" spellCheck="false" value={condition._filters_text} onChange={event => update(index, '_filters_text', event.target.value)} /><small>最多 16 个字段。entity_count 支持 entity_type、status、tag；actor_attribute 支持 actor_type；relation 支持 active、value_field。其他字段会安全保留，但只有运行时已支持的字段参与求值。</small></label></details>
        {condition.kind === 'anchored_inventory' && <div className="dlc-condition-anchor">当前比较统一锚定价值 = 数量 × anchor_rate{selectedResource ? `；${selectedResource.name} 的 anchor_rate 为 ${selectedResource.anchor_rate ?? 1}` : ''}。</div>}
      </article>
    })}
  </section>
}

const EFFECT_GROUPS = [...new Set(WORLD_EFFECT_TYPES.map(item => item.group))]

function EffectReferenceInput({ label, value, listId, onChange, placeholder = '', help = '' }) {
  return <label>{label}<input list={listId} value={value || ''} onChange={event => onChange(event.target.value)} placeholder={placeholder} />{help && <small>{help}</small>}</label>
}

function ResourceSelect({ value, resources, assetsOnly = false, onChange, label = '资源名称' }) {
  const choices = assetsOnly ? resources.filter(item => item.kind === 'asset') : resources
  return <label>{label}<select value={value || ''} onChange={event => onChange(event.target.value)}><option value="">-- 请选择 --</option>{choices.map(item => <option key={item.name} value={item.name}>{item.name}{item.kind === 'asset' ? `（锚定率 ${item.anchor_rate ?? 1}）` : `（${item.kind}）`}</option>)}</select><small>{assetsOnly ? '仅列出带 anchor_rate 的 asset 资源。' : '资源必须已在 DLC 的资源目录中定义。'}</small></label>
}

function JsonEffectField({ label, value, onChange, rows = 5, help = '必须是合法 JSON 对象。' }) {
  return <label className="dlc-world-effect-wide">{label}<textarea className="dlc-json-field" rows={rows} spellCheck="false" value={value || ''} onChange={event => onChange(event.target.value)} /><small>{help}</small></label>
}

function CausalWorldEffectBuilder({ value, entities, resources, agents, actors, relationshipTypes, issueTypes, objectives, actions, onChange }) {
  const effects = Array.isArray(value) ? value : []
  const catalogs = { entities, resources, agents, actors, relationshipTypes, issueTypes, objectives, actions }
  const createdEntityIds = effects.filter(item => item.effect_type === 'create_entity').map(item => String(item.entity_id || '').trim()).filter(Boolean)
  const entityCatalog = [...new Set([...(entities || []).map(item => item.id), ...createdEntityIds].filter(Boolean))]
  const actorCatalog = (actors || []).map(item => item.actor_id || item.id).filter(Boolean)
  const agentCatalog = (agents || []).map(item => item.id || item.agent_id).filter(Boolean)
  const scopeCatalog = [...new Set([...entityCatalog, ...actorCatalog, ...agentCatalog])]
  const effectDefinition = effect => WORLD_EFFECT_TYPES.find(item => item.key === effect.effect_type) || WORLD_EFFECT_TYPES[0]
  const update = (index, name, nextValue) => {
    const next = effects.map(item => ({ ...item }))
    next[index][name] = nextValue
    onChange(next)
  }
  const changeMany = (index, values) => {
    const next = effects.map(item => ({ ...item }))
    next[index] = { ...next[index], ...values }
    onChange(next)
  }
  const changeType = (index, effectType) => {
    const next = effects.map(item => ({ ...item }))
    next[index] = defaultWorldEffect(effectType, catalogs)
    onChange(next)
  }
  const add = () => {
    if (effects.length >= 32) return
    onChange([...effects, defaultWorldEffect('adjust_resource', catalogs)])
  }
  const remove = index => onChange(effects.filter((_, itemIndex) => itemIndex !== index))

  return <section className="dlc-world-effect-builder">
    <datalist id="dlc-effect-entity-catalog">{entityCatalog.map(id => <option key={id} value={id} />)}</datalist>
    <datalist id="dlc-effect-agent-catalog">{agentCatalog.map(id => <option key={id} value={id} />)}</datalist>
    <datalist id="dlc-effect-actor-catalog">{actorCatalog.map(id => <option key={id} value={id} />)}</datalist>
    <datalist id="dlc-effect-scope-catalog">{scopeCatalog.map(id => <option key={id} value={id} />)}</datalist>
    <datalist id="dlc-effect-relationship-catalog">{(relationshipTypes || []).map(item => <option key={item.relationship_type_id} value={item.relationship_type_id} />)}</datalist>
    <datalist id="dlc-effect-issue-catalog">{(issueTypes || []).map(item => <option key={item.issue_type_id} value={item.issue_type_id} />)}</datalist>
    <datalist id="dlc-effect-objective-catalog">{(objectives || []).map(item => <option key={item.objective_id} value={item.objective_id} />)}</datalist>
    <datalist id="dlc-effect-action-catalog">{(actions || []).map(item => <option key={item.action_id} value={item.action_id} />)}</datalist>
    <div className="dlc-condition-header"><div><strong>事务型通用因果效果</strong><small>效果在调度时冻结，在子事件发送给 Agent 前以原子事务提交；任一效果失败时整批回滚。</small></div><button type="button" disabled={effects.length >= 32} onClick={add}>＋ 添加效果</button></div>
    <div className="dlc-world-effect-overview"><strong>已配置 {effects.length} / 32</strong><span>可创建实体、Issue、知识、关系、职位、目标、行动解锁与世界阶段。新建实体 ID 可被同一规则后续效果直接引用。</span></div>
    {!effects.length && <div className="dlc-condition-empty">未配置效果：该规则只会生成面向 Agent 的后续事件，不修改世界状态。</div>}
    {effects.map((effect, index) => {
      const definition = effectDefinition(effect)
      return <article className="dlc-world-effect-row" key={`world-effect-${index}`}>
        <div className="dlc-world-effect-toolbar">
          <span className="dlc-world-effect-index">效果 {index + 1}</span>
          <label>效果类型<select value={effect.effect_type || 'adjust_resource'} onChange={event => changeType(index, event.target.value)}>{EFFECT_GROUPS.map(group => <optgroup key={group} label={group}>{WORLD_EFFECT_TYPES.filter(item => item.group === group).map(item => <option key={item.key} value={item.key}>{item.label}</option>)}</optgroup>)}</select></label>
          <span className="dlc-world-effect-badge">{definition.group}</span>
          <button type="button" className="danger" onClick={() => remove(index)}>删除</button>
        </div>
        <div className="dlc-world-effect-help"><strong>{definition.label}</strong><span>{definition.description}</span></div>

        {['adjust_resource', 'set_resource'].includes(effect.effect_type) && <div className="dlc-world-effect-grid">
          <EffectReferenceInput label="实体 ID" value={effect.entity_id} listId="dlc-effect-entity-catalog" onChange={next => update(index, 'entity_id', next)} help="可引用当前实体，也可填写本规则前面刚创建的实体 ID。" />
          <ResourceSelect value={effect.resource_name} resources={resources} onChange={next => update(index, 'resource_name', next)} />
          <label>{effect.effect_type === 'adjust_resource' ? '数量增量' : '目标数量'}<input type="number" step="any" value={effect.effect_type === 'adjust_resource' ? effect.quantity_delta ?? 0 : effect.quantity ?? 0} onChange={event => update(index, effect.effect_type === 'adjust_resource' ? 'quantity_delta' : 'quantity', event.target.value)} /><small>{effect.effect_type === 'adjust_resource' ? '允许负数；结果库存仍受世界状态约束。' : '直接覆盖当前库存数量。'}</small></label>
        </div>}

        {effect.effect_type === 'update_status' && <div className="dlc-world-effect-grid">
          <EffectReferenceInput label="实体 ID" value={effect.entity_id} listId="dlc-effect-entity-catalog" onChange={next => update(index, 'entity_id', next)} />
          <label>新状态<input value={effect.new_status || ''} onChange={event => update(index, 'new_status', event.target.value)} placeholder="mobilized" /><small>写入实体 current_status，最长 512 字符。</small></label>
        </div>}

        {effect.effect_type === 'create_ongoing_event' && <div className="dlc-world-effect-grid">
          <EffectReferenceInput label="关联实体 ID" value={effect.entity_id} listId="dlc-effect-entity-catalog" onChange={next => update(index, 'entity_id', next)} />
          <label>持续事件类型<input value={effect.event_type || ''} onChange={event => update(index, 'event_type', event.target.value)} placeholder="relief_operation" /><small>稳定 ID；用于后续条件和成熟结算。</small></label>
          <label>持续天数<input type="number" min="0" max="3650" step="any" value={effect.duration_days ?? 1} onChange={event => update(index, 'duration_days', event.target.value)} /><small>0–3650 天；0 表示立即成熟。</small></label>
          <label className="dlc-world-effect-wide">事件描述<input value={effect.description || ''} onChange={event => update(index, 'description', event.target.value)} /><small>面向编辑者和运行日志的说明，最长 1000 字符。</small></label>
          <JsonEffectField label="成熟结果 JSON" value={effect._expected_result_text} onChange={next => update(index, '_expected_result_text', next)} help="对象可包含 new_status、inventory_deltas 等成熟时执行的结果。" />
        </div>}

        {effect.effect_type === 'transfer_anchored_value' && <>
          <div className="dlc-world-effect-grid">
            <EffectReferenceInput label="来源实体 ID" value={effect.from_entity_id} listId="dlc-effect-entity-catalog" onChange={next => update(index, 'from_entity_id', next)} />
            <ResourceSelect label="来源资产" value={effect.from_resource} resources={resources} assetsOnly onChange={next => update(index, 'from_resource', next)} />
            <EffectReferenceInput label="目标实体 ID" value={effect.to_entity_id} listId="dlc-effect-entity-catalog" onChange={next => update(index, 'to_entity_id', next)} />
            <ResourceSelect label="目标资产" value={effect.to_resource} resources={resources} assetsOnly onChange={next => update(index, 'to_resource', next)} />
            <label>基础价值<input type="number" min="0" step="any" value={effect.base_value ?? 1} onChange={event => update(index, 'base_value', event.target.value)} /><small>必须大于 0；输入的是统一锚定价值，不是资源数量。</small></label>
          </div>
          <div className="dlc-condition-anchor">扣减数量 = 基础价值 ÷ 来源 anchor_rate；增加数量 = 基础价值 ÷ 目标 anchor_rate。整批事务保持锚定价值守恒。</div>
        </>}

        {effect.effect_type === 'create_entity' && <div className="dlc-world-effect-grid">
          <EffectReferenceInput label="新实体 ID" value={effect.entity_id} listId="dlc-effect-entity-catalog" onChange={next => update(index, 'entity_id', next)} placeholder="frontier_outpost" help="必须是新 ID；同一规则后面的效果可立即引用。" />
          <label>实体类型<input value={effect.entity_type || ''} onChange={event => update(index, 'entity_type', event.target.value)} placeholder="location" /><small>稳定类型 ID，例如 person、organization、location。</small></label>
          <label>实体名称<input value={effect.name || ''} onChange={event => update(index, 'name', event.target.value)} placeholder="前线哨站" /><small>玩家可见名称，必填。</small></label>
          <label>当前状态<input value={effect.current_status || ''} onChange={event => update(index, 'current_status', event.target.value)} placeholder="active" /><small>可留空；常用 active、stable、mobilized。</small></label>
          <label className="dlc-world-effect-wide">实体描述<textarea rows="3" value={effect.description || ''} onChange={event => update(index, 'description', event.target.value)} /><small>最长 4000 字符。</small></label>
          <JsonEffectField label="实体 Metadata JSON" value={effect._metadata_text} onChange={next => update(index, '_metadata_text', next)} help="标签、坐标、扩展属性等对象字段。" />
          <JsonEffectField label="初始库存 JSON" value={effect._resources_text} onChange={next => update(index, '_resources_text', next)} help='对象键为已定义资源名称，值为有限数字，例如 {"silver": 20}。' />
        </div>}

        {effect.effect_type === 'move_entity' && <div className="dlc-world-effect-grid">
          <EffectReferenceInput label="实体 ID" value={effect.entity_id} listId="dlc-effect-entity-catalog" onChange={next => update(index, 'entity_id', next)} />
          <label>X 坐标（可选）<input type="number" step="any" value={effect.x ?? ''} onChange={event => update(index, 'x', event.target.value)} /></label>
          <label>Y 坐标（可选）<input type="number" step="any" value={effect.y ?? ''} onChange={event => update(index, 'y', event.target.value)} /></label>
          <label>区域（可选）<input value={effect.region || ''} onChange={event => update(index, 'region', event.target.value)} placeholder="north_frontier" /></label>
          <EffectReferenceInput label="地点 ID（可选）" value={effect.location_id} listId="dlc-effect-entity-catalog" onChange={next => update(index, 'location_id', next)} help="X、Y、区域、地点 ID 至少填写一项。" />
        </div>}

        {effect.effect_type === 'transform_entity' && <div className="dlc-world-effect-grid">
          <EffectReferenceInput label="实体 ID" value={effect.entity_id} listId="dlc-effect-entity-catalog" onChange={next => update(index, 'entity_id', next)} />
          <label>新实体类型（可选）<input value={effect.new_type || ''} onChange={event => update(index, 'new_type', event.target.value)} /></label>
          <label>新名称（可选）<input value={effect.new_name || ''} onChange={event => update(index, 'new_name', event.target.value)} /></label>
          <label>新状态（可选）<input value={effect.new_status || ''} onChange={event => update(index, 'new_status', event.target.value)} /></label>
          <label className="dlc-world-effect-wide">新描述（可选）<textarea rows="3" value={effect.new_description || ''} onChange={event => update(index, 'new_description', event.target.value)} /></label>
          <JsonEffectField label="Metadata 补丁 JSON（可选）" value={effect._metadata_patch_text} onChange={next => update(index, '_metadata_patch_text', next)} help="至少填写一个转化字段；空对象不会产生变更。" />
        </div>}

        {effect.effect_type === 'remove_entity' && <div className="dlc-world-effect-grid"><EffectReferenceInput label="要移除的实体 ID" value={effect.entity_id} listId="dlc-effect-entity-catalog" onChange={next => update(index, 'entity_id', next)} help="移除会同步清理该实体的库存、关系和持续事件。" /></div>}

        {['enable_agent', 'disable_agent'].includes(effect.effect_type) && <div className="dlc-world-effect-grid">
          <EffectReferenceInput label="Agent ID" value={effect.agent_id} listId="dlc-effect-agent-catalog" onChange={next => update(index, 'agent_id', next)} help="必须引用 DLC 中已定义的 Agent。" />
          <EffectReferenceInput label="关联 Actor ID（可选）" value={effect.actor_id} listId="dlc-effect-actor-catalog" onChange={next => update(index, 'actor_id', next)} help="填写时必须引用已定义 Actor。" />
          <JsonEffectField label="Agent 状态 Metadata JSON" value={effect._metadata_text} onChange={next => update(index, '_metadata_text', next)} />
        </div>}

        {effect.effect_type === 'modify_relationship' && <div className="dlc-world-effect-grid">
          <label>操作<select value={effect.operation || 'upsert'} onChange={event => changeMany(index, { operation: event.target.value })}><option value="upsert">新增或更新 upsert</option><option value="remove">删除 remove</option></select><small>删除操作只需要两端实体和关系类型。</small></label>
          <EffectReferenceInput label="父/来源实体 ID" value={effect.parent_id} listId="dlc-effect-entity-catalog" onChange={next => update(index, 'parent_id', next)} />
          <EffectReferenceInput label="子/目标实体 ID" value={effect.child_id} listId="dlc-effect-entity-catalog" onChange={next => update(index, 'child_id', next)} />
          <EffectReferenceInput label="关系类型 ID" value={effect.rel_type} listId="dlc-effect-relationship-catalog" onChange={next => update(index, 'rel_type', next)} help="必须在关系类型目录中定义。" />
          {effect.operation !== 'remove' && <>
            <label>关系强度（可选）<input type="number" step="any" value={effect.strength ?? ''} onChange={event => update(index, 'strength', event.target.value)} /></label>
            <label>可见性（可选）<select value={effect.visibility || ''} onChange={event => update(index, 'visibility', event.target.value)}><option value="">使用类型默认值</option><option value="public">public / 公开</option><option value="private">private / 私密</option><option value="secret">secret / 秘密</option></select></label>
            <label>延迟生效天数<input type="number" min="0" max="3650" step="any" value={effect.starts_in_days ?? 0} onChange={event => update(index, 'starts_in_days', event.target.value)} /></label>
            <label>持续天数（可选）<input type="number" min="0" max="3650" step="any" value={effect.duration_days ?? ''} onChange={event => update(index, 'duration_days', event.target.value)} /><small>留空表示永久；填写时必须大于 0。</small></label>
            <label className="dlc-world-effect-wide">关系备注<input value={effect.note || ''} onChange={event => update(index, 'note', event.target.value)} /></label>
            <JsonEffectField label="关系维度值 JSON" value={effect._values_text} onChange={next => update(index, '_values_text', next)} help='例如 {"trust": 0.8, "fear": 0.1}。' />
            <JsonEffectField label="关系 Metadata JSON" value={effect._metadata_text} onChange={next => update(index, '_metadata_text', next)} />
          </>}
        </div>}

        {effect.effect_type === 'modify_membership' && <div className="dlc-world-effect-grid">
          <label>操作<select value={effect.operation || 'upsert'} onChange={event => update(index, 'operation', event.target.value)}><option value="upsert">新增或更新 upsert</option><option value="remove">删除 remove</option></select></label>
          <EffectReferenceInput label="Actor ID" value={effect.actor_id} listId="dlc-effect-actor-catalog" onChange={next => update(index, 'actor_id', next)} />
          <EffectReferenceInput label="组织实体 ID" value={effect.organization_id} listId="dlc-effect-entity-catalog" onChange={next => update(index, 'organization_id', next)} help="组织必须是已存在或同规则前面创建的实体。" />
          {effect.operation !== 'remove' && <>
            <label>职位 / 角色<input value={effect.role || 'member'} onChange={event => update(index, 'role', event.target.value)} placeholder="commander" /></label>
            <label>成员状态<input value={effect.status || 'active'} onChange={event => update(index, 'status', event.target.value)} placeholder="active" /></label>
            <JsonEffectField label="成员身份 Metadata JSON" value={effect._metadata_text} onChange={next => update(index, '_metadata_text', next)} />
          </>}
        </div>}

        {effect.effect_type === 'create_issue' && <div className="dlc-world-effect-grid">
          <EffectReferenceInput label="Issue 类型 ID" value={effect.issue_type_id} listId="dlc-effect-issue-catalog" onChange={next => update(index, 'issue_type_id', next)} help="必须引用已启用的 Issue 类型。" />
          <label>标题（可选）<input value={effect.title || ''} onChange={event => update(index, 'title', event.target.value)} /></label>
          <label>紧迫度<select value={effect.urgency || 'normal'} onChange={event => update(index, 'urgency', event.target.value)}><option value="low">low / 低</option><option value="normal">normal / 普通</option><option value="high">high / 高</option><option value="critical">critical / 紧急</option></select></label>
          <label>处理期限（天，可选）<input type="number" min="0" max="3650" step="any" value={effect.deadline_days ?? ''} onChange={event => update(index, 'deadline_days', event.target.value)} /></label>
          <label className="dlc-world-effect-wide">摘要（可选）<textarea rows="3" value={effect.summary || ''} onChange={event => update(index, 'summary', event.target.value)} /></label>
          <JsonEffectField label="来源 JSON" value={effect._source_text} onChange={next => update(index, '_source_text', next)} help='例如 {"kind":"event","entity_id":"frontier_fort"}；空对象会由运行时补为 causal_event。' />
          <JsonEffectField label="详情 JSON" value={effect._details_text} onChange={next => update(index, '_details_text', next)} />
          <JsonEffectField label="证据 JSON 数组" value={effect._evidence_text} onChange={next => update(index, '_evidence_text', next)} help="必须是 JSON 数组，可放结构化证据对象。" />
          <JsonEffectField label="Issue Metadata JSON" value={effect._metadata_text} onChange={next => update(index, '_metadata_text', next)} />
        </div>}

        {effect.effect_type === 'reveal_information' && <div className="dlc-world-effect-grid">
          <EffectReferenceInput label="关联实体 ID（可选）" value={effect.entity_id} listId="dlc-effect-entity-catalog" onChange={next => update(index, 'entity_id', next)} />
          <label>知识 ID<input value={effect.knowledge_id || ''} onChange={event => update(index, 'knowledge_id', event.target.value)} placeholder="secret_route" /><small>后续 player_knowledge 条件使用这个稳定 ID。</small></label>
          <label>知识来源<input value={effect.source || 'causal_event'} onChange={event => update(index, 'source', event.target.value)} /></label>
          <label>置信度<input type="number" min="0" max="1" step="0.01" value={effect.confidence ?? 1} onChange={event => update(index, 'confidence', event.target.value)} /><small>0–1。</small></label>
          <JsonEffectField label="知识值 JSON" value={effect._value_text} onChange={next => update(index, '_value_text', next)} help="允许任意 JSON 值：对象、数组、字符串、数字、布尔值或 null。字符串需要包含双引号。" />
          <JsonEffectField label="知识 Metadata JSON" value={effect._metadata_text} onChange={next => update(index, '_metadata_text', next)} />
        </div>}

        {effect.effect_type === 'advance_objective' && <div className="dlc-world-effect-grid">
          <EffectReferenceInput label="Objective ID" value={effect.objective_id} listId="dlc-effect-objective-catalog" onChange={next => update(index, 'objective_id', next)} />
          <label>推进模式<select value={effect.mode || 'adjust'} onChange={event => changeMany(index, event.target.value === 'adjust' ? { mode: 'adjust', amount: effect.amount ?? effect.progress ?? 1 } : { mode: 'set', progress: effect.progress ?? effect.amount ?? 0 })}><option value="adjust">adjust / 增量调整</option><option value="set">set / 直接设定</option></select></label>
          <label>{effect.mode === 'set' ? '目标进度' : '进度增量'}<input type="number" step="any" value={effect.mode === 'set' ? effect.progress ?? 0 : effect.amount ?? 1} onChange={event => update(index, effect.mode === 'set' ? 'progress' : 'amount', event.target.value)} /><small>{effect.mode === 'set' ? '直接覆盖 progress。' : '在当前 progress 上增加，可为负数。'}</small></label>
          <label>目标状态（可选）<input value={effect.status || ''} onChange={event => update(index, 'status', event.target.value)} placeholder="completed" /></label>
        </div>}

        {effect.effect_type === 'unlock_action' && <div className="dlc-world-effect-grid">
          <EffectReferenceInput label="行动 ID" value={effect.action_id} listId="dlc-effect-action-catalog" onChange={next => update(index, 'action_id', next)} help="必须引用 DLC 的行动目录。" />
          <EffectReferenceInput label="作用域 ID（可选）" value={effect.scope_id} listId="dlc-effect-scope-catalog" onChange={next => update(index, 'scope_id', next)} help="留空表示全局解锁；可填写实体、Actor 或 Agent ID。" />
        </div>}

        {effect.effect_type === 'switch_phase' && <div className="dlc-world-effect-grid">
          <label>阶段 ID<input value={effect.phase_id || ''} onChange={event => update(index, 'phase_id', event.target.value)} placeholder="counterattack" /><small>稳定 ID；后续 phase 条件可直接比较。</small></label>
          <JsonEffectField label="阶段 Metadata JSON" value={effect._metadata_text} onChange={next => update(index, '_metadata_text', next)} help="记录章节、解锁来源或 UI 提示等扩展信息。" />
        </div>}

        <label className="dlc-world-effect-reason">原因 / 审计说明（可选）<input value={effect.reason || ''} onChange={event => update(index, 'reason', event.target.value)} placeholder="说明为什么应用该效果" /><small>会写入命令计划和审计记录，最长 512 字符。</small></label>
      </article>
    })}
  </section>
}

function CausalGraphInspector({ analysis }) {
  const [filter, setFilter] = useState('')
  const query = filter.trim().toLowerCase()
  const edges = (analysis?.edges || []).filter(edge => !query || [
    edge.rule_id, edge.source_event_type, edge.child_event_type, edge.template_id,
    ...(edge.condition_group_ids || []),
  ].some(value => String(value || '').toLowerCase().includes(query)))

  return <section className="dlc-causal-graph">
    <div className="dlc-condition-header"><div><strong>Resolved causal graph edges</strong><small>Inspect expanded routing, reusable authoring provenance, timing, conditions, and effects.</small></div><label>Filter<input value={filter} onChange={event => setFilter(event.target.value)} placeholder="event, rule, template, group" /></label></div>
    <div className="dlc-causal-node-strip">{(analysis?.nodes || []).map(node => <article key={node.event_type}><strong>{node.event_type}</strong><small>{(node.origins || []).join(' / ') || 'derived'}</small><code>{node.incoming_rules?.length || 0} in / {node.outgoing_rules?.length || 0} out</code></article>)}</div>
    <div className="dlc-causal-edge-list">
      {!edges.length && <div className="dlc-condition-empty">No graph edge matches the current filter.</div>}
      {edges.map(edge => <article key={edge.edge_id || `${edge.rule_id}:${edge.source_event_type}`} className={!edge.enabled ? 'disabled' : ''}>
        <div className="dlc-causal-edge-route"><code>{edge.source_event_type}</code><span>&rarr;</span><code>{edge.child_event_type}</code></div>
        <div className="dlc-causal-edge-meta"><strong>{edge.rule_id}</strong><span>p{edge.priority} / {edge.delay_days}d / probability {edge.probability}</span><span>{edge.condition_count} conditions / {edge.world_effect_count} effects / {edge.target_mode} targets</span></div>
        <div className="dlc-causal-edge-authoring">{edge.template_id ? <code>template:{edge.template_id}</code> : <code>inline rule</code>}{(edge.condition_group_ids || []).map(groupId => <code key={groupId}>group:{groupId}</code>)}<small>{edge.inline_condition_count || 0} inline conditions</small></div>
      </article>)}
    </div>
  </section>
}

function CausalDryRunPanel({ report, replayComparison, busy, sourceType, severity, horizonDays, eventTypes, onSourceType, onSeverity, onHorizonDays, onRun, onExportReplay, onImportReplay }) {
  const replayInputRef = useRef(null)
  return <section className="dlc-causal-preview">
    <div className="dlc-condition-header"><div><strong>Deterministic chain preview</strong><small>Runs against this unsaved draft and DLC seed facts without changing live world state.</small></div><div className="dlc-causal-replay-actions"><input ref={replayInputRef} type="file" accept="application/json,.json" hidden onChange={onImportReplay} /><button type="button" disabled={!report?.replay_fixture} onClick={onExportReplay}>Export replay fixture</button><button type="button" disabled={busy} onClick={() => replayInputRef.current?.click()}>Import and replay</button></div></div>
    <div className="dlc-causal-preview-controls">
      <label>Source event type<input list="causal-event-type-catalog" value={sourceType} onChange={event => onSourceType(event.target.value)} placeholder="shortage" /><datalist id="causal-event-type-catalog">{eventTypes.map(item => <option key={item} value={item} />)}</datalist></label>
      <label>Severity<input type="number" min="1" max="10" step="1" value={severity} onChange={event => onSeverity(event.target.value)} /></label>
      <label>Horizon days<input type="number" min="0" max="3650" step="1" value={horizonDays} onChange={event => onHorizonDays(event.target.value)} /></label>
      <button type="button" className="primary" disabled={busy || !sourceType.trim()} onClick={onRun}>{busy ? 'Analyzing...' : 'Analyze and preview'}</button>
    </div>
    {replayComparison && <div className={`dlc-causal-replay-result ${replayComparison.matches ? 'match' : 'drift'}`}><strong>{replayComparison.matches ? 'Replay matches baseline' : 'Replay drift detected'}</strong><span>{replayComparison.matches ? 'Rules, seed-dependent results, timeline, and pending work are deterministic.' : (replayComparison.drift_fields || []).join(', ')}</span><code>{String(replayComparison.actual_result_digest || '').slice(0, 16)}</code></div>}
    {report && <>
      <div className="dlc-causal-summary">
        <article><span>Rules</span><strong>{report.analysis.summary.enabled_count}/{report.analysis.summary.rule_count}</strong></article>
        <article><span>Event nodes</span><strong>{report.summary.event_count}</strong></article>
        <article><span>Errors / warnings</span><strong>{report.analysis.summary.error_count || 0} / {report.analysis.summary.warning_count}</strong></article>
        <article><span>Pending</span><strong>{report.summary.pending_count}</strong></article>
      </div>
      {!!report.analysis.issues.length && <div className="dlc-causal-issues">{report.analysis.issues.map((issue, index) => <div key={`${issue.code}-${index}`} className={issue.severity}><strong>{issue.severity}</strong><code>{issue.code}</code><span>{issue.message}</span>{!!issue.rule_ids.length && <small>{issue.rule_ids.join(', ')}</small>}</div>)}</div>}
      <CausalGraphInspector analysis={report.analysis} />
      <div className="dlc-causal-timeline">{report.nodes.map((node, index) => <div className="dlc-causal-node" key={node.event_id}><span>Day {Number(node.day).toFixed(2)}</span><strong>{node.event_type}</strong><code>{node.event_id}</code><small>severity {node.severity} / depth {node.causal_depth}</small>{node.world_command_count > 0 && <small>{node.world_effect_status || 'planned'} / {node.world_command_count} world command{node.world_command_count === 1 ? '' : 's'}</small>}{!!node.world_commands?.length && <div className="dlc-causal-command-list">{node.world_commands.map((command, commandIndex) => <code key={`${node.event_id}-command-${commandIndex}`}>{command.command_type} ? {command.entity_id}{command.resource_name ? ` ? ${command.resource_name}` : ''}{command.quantity_delta != null ? ` ? ${Number(command.quantity_delta).toFixed(4)}` : ''}{command.quantity != null ? ` ? ${Number(command.quantity).toFixed(4)}` : ''}{command.anchored_base_value != null ? ` ? base ${Number(command.anchored_base_value).toFixed(2)}` : ''}{command.end_day != null ? ` ? ends day ${Number(command.end_day).toFixed(2)}` : ''}</code>)}</div>}{node.world_effect_error_code && <small className="dlc-editor-error">{node.world_effect_error_code}</small>}{index < report.nodes.length - 1 && <i>-&gt;</i>}</div>)}</div>
      {report.summary.truncated && <div className="dlc-editor-error">Preview reached the event limit while due events remained.</div>}
    </>}
  </section>
}

export function StructuredContentEditor({ kind, content, selected, onSelect, onSaveContent, onAnalyzeCausal, onReplayCausal, busy = false }) {
  const items = content[kind] || []
  const entities = content.entities || []
  const agents = content.agents || []
  const actors = content.actors || []
  const objectives = content.objectives || []
  const issueTypes = content.issue_types || []
  const actions = content.actions || []
  const ongoingEvents = content.ongoing_events || []
  const resources = content.resources || []
  const relationshipTypes = effectiveRelationshipTypes(content)
  const causalTemplates = content.causal_rule_templates || []
  const causalConditionGroups = content.causal_condition_groups || []
  const assetResources = resources.filter(item => item.kind === 'asset')
  const valueResources = resources.filter(item => item.kind === 'metric' || item.kind === 'parameter')
  const selectedIndex = selected === '' ? -1 : Number(selected)
  const [form, setForm] = useState(() => normalizedListForm(kind, selectedIndex >= 0 ? items[selectedIndex] : null))
  const [error, setError] = useState('')
  const eventTypes = useMemo(() => buildCausalEventCatalog(content), [content])
  const [previewSourceType, setPreviewSourceType] = useState(() => String(form._source_types_text || '').split(',').map(value => value.trim()).find(value => value && value !== '*') || eventTypes[0] || '')
  const [previewSeverity, setPreviewSeverity] = useState(5)
  const [previewHorizon, setPreviewHorizon] = useState(365)
  const [previewBusy, setPreviewBusy] = useState(false)
  const [previewReport, setPreviewReport] = useState(null)
  const [replayComparison, setReplayComparison] = useState(null)

  const resolvedCausalRule = useMemo(() => {
    if (kind !== 'causal_event_rules') return null
    try { return expandCausalRuleDraft(content, form) } catch { return null }
  }, [content, form, kind])

  const set = (name, value) => setForm(current => ({ ...current, [name]: value }))
  const setCapabilityHandler = handler => setForm(current => {
    const next = { ...current, handler, action_id: handler === 'contract_action' ? current.action_id || '' : '' }
    if (handler === 'noop') {
      next.target = { kind: 'none', mode: 'none', allowed_ids: [] }
      next.input = { ...(current.input || {}), content_required: false }
      next.parameters = []
      next._parameters_text = '[]'
    } else if (handler === 'message' || handler === 'broadcast') {
      const mode = current.target?.mode && current.target.mode !== 'none' ? current.target.mode : 'optional'
      next.target = { ...(current.target || {}), kind: 'agent', mode }
      next.input = { ...(current.input || {}), content_required: true }
    } else if (current.target?.kind === 'none') {
      next.target = { kind: 'none', mode: 'none', allowed_ids: [] }
    }
    return next
  })
  const setCapabilityTargetKind = targetKind => setForm(current => ({
    ...current,
    target: targetKind === 'none'
      ? { kind: 'none', mode: 'none', allowed_ids: [] }
      : { ...(current.target || {}), kind: targetKind, mode: current.target?.mode === 'none' ? 'optional' : current.target?.mode || 'optional' },
    _allowed_ids_text: targetKind === 'none' ? '' : current._allowed_ids_text || '',
  }))
  const toggleCapabilityAudience = audience => setForm(current => {
    const selected = new Set(current.audiences || [])
    if (selected.has(audience)) selected.delete(audience)
    else selected.add(audience)
    return { ...current, audiences: [...selected] }
  })
  const formForSerialization = () => {
    if (kind !== 'causal_event_rules' || !form._inherit_template_defaults) return form
    const expanded = expandCausalRuleDraft(content, form)
    const defined = Object.fromEntries(Object.entries(form).filter(([, value]) => value !== undefined))
    return { ...expanded, ...defined, _payload_text: jsonText(expanded.initial_payload), _inherit_keys_text: (expanded.inherit_payload_keys || []).join(', ') }
  }
  const selectCausalTemplate = templateId => {
    if (!templateId) {
      setForm(current => {
        if (!current._inherit_template_defaults) return { ...current, template_id: '', _inherit_template_defaults: false }
        return normalizedListForm('causal_event_rules', {
          ...EMPTY.causal_event_rules,
          rule_id: current.rule_id || '',
          source_event_types: current.source_event_types || ['*'],
          child_event_type: current.child_event_type || '',
          condition_group_ids: current.condition_group_ids || [],
          conditions: current.conditions || [],
        })
      })
      return
    }
    setForm(current => ({
      rule_id: current.rule_id || '', source_event_types: current.source_event_types || ['*'],
      _source_types_text: current._source_types_text || '*', child_event_type: current.child_event_type || '',
      template_id: templateId, condition_group_ids: current.condition_group_ids || [],
      conditions: current.conditions || [], _inherit_template_defaults: true,
    }))
  }
  const overrideCausalTemplate = () => {
    try {
      const expanded = expandCausalRuleDraft(content, form)
      setForm({ ...normalizedListForm('causal_event_rules', expanded), template_id: form.template_id, condition_group_ids: form.condition_group_ids || [], conditions: form.conditions || [], _inherit_template_defaults: false })
    } catch (caught) { setError(caught.message) }
  }
  const runCausalPreview = async () => {
    try {
      const rule = serializeListForm('causal_event_rules', formForSerialization())
      const draft = buildCausalDryRunDraft(content, rule, selectedIndex, {
        event_id: 'studio_preview_source',
        event_type: previewSourceType.trim(),
        severity: Number(previewSeverity),
        target_agents: rule.target_agents || [],
        initial_payload: {},
        generated_at: 0,
      })
      setPreviewBusy(true)
      setError('')
      const report = await onAnalyzeCausal({
        ...draft,
        horizon_days: Number(previewHorizon),
        max_events: 128,
      })
      setPreviewReport(report)
      setReplayComparison(null)
    } catch (caught) {
      setError(caught.message)
    } finally {
      setPreviewBusy(false)
    }
  }
  const exportCausalReplay = () => {
    const fixture = previewReport?.replay_fixture
    if (!fixture) return
    const blob = new Blob([`${JSON.stringify(fixture, null, 2)}\n`], { type: 'application/json;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    const safeRuleId = String(form.rule_id || 'causal-rule').replace(/[^a-zA-Z0-9_-]+/g, '_')
    anchor.href = url
    anchor.download = `${safeRuleId}-replay.json`
    anchor.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 0)
  }
  const importCausalReplay = async event => {
    const file = event.target.files?.[0]
    if (!file) return
    try {
      const fixture = normalizeCausalReplayFixture(JSON.parse(await file.text()))
      const rule = serializeListForm('causal_event_rules', formForSerialization())
      const draft = buildCausalDryRunDraft(content, rule, selectedIndex, fixture.input.source_event)
      setPreviewBusy(true)
      setError('')
      const replay = await onReplayCausal({ content: draft.content, fixture })
      setPreviewSourceType(fixture.input.source_event.event_type)
      setPreviewSeverity(fixture.input.source_event.severity)
      setPreviewHorizon(fixture.input.horizon_days)
      setPreviewReport(replay.report)
      setReplayComparison(replay.comparison)
    } catch (caught) {
      setError(caught.message)
    } finally {
      setPreviewBusy(false)
      event.target.value = ''
    }
  }

  const save = () => {
    try {
      const data = serializeListForm(kind, kind === 'causal_event_rules' ? formForSerialization() : form)
      const nextItems = items.map(deepClone)
      if (selectedIndex >= 0) nextItems[selectedIndex] = data
      else nextItems.push(data)
      setError('')
      onSaveContent({ ...content, [kind]: nextItems }, `${TITLES[kind]} saved`)
    } catch (caught) {
      setError(caught.message)
    }
  }
  const remove = () => {
    if (selectedIndex < 0 || !window.confirm(`${T.delete} ${listLabel(kind, items[selectedIndex])[0]}?`)) return
    onSaveContent({ ...content, [kind]: items.filter((_, index) => index !== selectedIndex) }, `${TITLES[kind]} deleted`)
  }

  const relationshipDefinition = relationshipTypes.find(item => item.relationship_type_id === form.rel_type) || relationshipTypes[0]
  const inventoryResource = assetResources.find(item => item.name === form.item_name)
  const anchoredValue = Number(form.quantity || 0) * Number(inventoryResource?.anchor_rate || 0)

  return <div className="dlc-content-grid dlc-structured-grid">
    <div className="dlc-item-list">
      <button className="dlc-new" onClick={() => onSelect('')}>{T.add}{TITLES[kind]}</button>
      {items.map((item, index) => { const [primary, secondary] = listLabel(kind, item); return <button key={`${kind}-${index}`} className={selectedIndex === index ? 'active' : ''} onClick={() => onSelect(String(index))}><strong>{primary}</strong><small>{secondary}</small></button> })}
    </div>
    <div className="dlc-form dlc-structured-form">
      <div className="dlc-form-title"><h3>{selectedIndex >= 0 ? T.edit : T.add}{TITLES[kind]}</h3>{selectedIndex >= 0 && <code>#{selectedIndex + 1}</code>}</div>

      {kind === 'inventory' && <>
        <div className="dlc-editor-callout">{T.assetOnly}</div>
        <div className="dlc-row"><EntitySelect value={form.entity_id} entities={entities} onChange={value => set('entity_id', value)} /><label>{T.resource}<select value={form.item_name || ''} onChange={event => { const value = event.target.value; setForm(current => ({ ...current, item_name: value, category: current.category || value })) }}><option value="">--</option>{assetResources.map(item => <option key={item.name} value={item.name}>{item.name} ({item.anchor_rate} base/{item.unit || 'unit'})</option>)}</select></label></div>
        <div className="dlc-row"><label>{T.quantity}<input type="number" step="any" value={form.quantity ?? 0} onChange={event => set('quantity', event.target.value)} /></label><label>{T.category}<input value={form.category || ''} onChange={event => set('category', event.target.value)} /></label></div>
        <label>{T.description}<textarea rows="3" value={form.description || ''} onChange={event => set('description', event.target.value)} /></label>
        <div className="dlc-anchor-preview"><span>{T.anchoredValue}</span><strong>{Number.isFinite(anchoredValue) ? anchoredValue.toLocaleString() : '--'}</strong><small>{form.quantity || 0} \u00d7 {inventoryResource?.anchor_rate ?? '--'}</small></div>
      </>}

      {kind === 'entity_values' && <>
        <div className="dlc-row"><EntitySelect value={form.entity_id} entities={entities} onChange={value => set('entity_id', value)} /><label>{T.resource}<select value={form.name || ''} onChange={event => { const resource = valueResources.find(item => item.name === event.target.value); setForm(current => ({ ...current, name: event.target.value, kind: resource?.kind || 'metric' })) }}><option value="">--</option>{valueResources.map(item => <option key={item.name} value={item.name}>{item.name} ({item.kind})</option>)}</select></label></div>
        <div className="dlc-row"><label>{T.kind}<input value={form.kind || ''} disabled /></label><label>{T.value}<input type="number" step="any" value={form.value ?? 0} onChange={event => set('value', event.target.value)} /></label></div>
        <label>{T.description}<textarea rows="3" value={form.description || ''} onChange={event => set('description', event.target.value)} /></label>
      </>}

      {kind === 'relations' && <>
        <div className="dlc-editor-callout">{relationshipDefinition?.description || '关系边会按当前 DLC 关系契约校验强度、可见性、期限和多维值。'}</div>
        <div className="dlc-row"><EntitySelect label={relationshipDefinition?.direction === 'undirected' ? '实体 A' : T.parent} value={form.parent_id} entities={entities} onChange={value => set('parent_id', value)} /><EntitySelect label={relationshipDefinition?.direction === 'undirected' ? '实体 B' : T.child} value={form.child_id} entities={entities} onChange={value => set('child_id', value)} /></div>
        <label>{T.relationType}<select value={form.rel_type || relationshipTypes[0]?.relationship_type_id || 'subordinate'} onChange={event => { const definition = relationshipTypes.find(item => item.relationship_type_id === event.target.value); setForm(current => ({ ...current, rel_type: event.target.value, strength: definition?.default_strength ?? current.strength ?? 0, visibility: definition?.default_visibility || current.visibility || 'public', expires_at: definition?.allow_expiry ? current.expires_at : '', values: Object.fromEntries((definition?.dimensions || []).map(item => [item.dimension_id, item.default ?? 0])), _values_text: jsonText(Object.fromEntries((definition?.dimensions || []).map(item => [item.dimension_id, item.default ?? 0]))) })) }}><option value="">--</option>{relationshipTypes.map(item => <option key={item.relationship_type_id} value={item.relationship_type_id}>{item.display_name || item.relationship_type_id} ({item.relationship_type_id})</option>)}</select></label>
        <div className="dlc-row three"><label>{T.strength}<input type="number" step="any" min={relationshipDefinition?.minimum_strength} max={relationshipDefinition?.maximum_strength} value={form.strength ?? relationshipDefinition?.default_strength ?? 0} onChange={event => set('strength', event.target.value)} /><small>{relationshipDefinition?.minimum_strength ?? '-'} ~ {relationshipDefinition?.maximum_strength ?? '-'}</small></label><label>{T.visibility}<select value={form.visibility || relationshipDefinition?.default_visibility || 'public'} onChange={event => set('visibility', event.target.value)}><option value="public">公开</option><option value="limited">有限公开</option><option value="private">私有</option><option value="secret">秘密</option></select></label><label>方向<input value={relationshipDefinition?.direction === 'undirected' ? '无向' : '有向'} disabled /></label></div>
        <div className="dlc-row"><label>{T.startsAt}<input type="number" step="any" value={form.starts_at ?? 0} onChange={event => set('starts_at', event.target.value)} /></label><label>{T.expiresAt}<input type="number" step="any" disabled={!relationshipDefinition?.allow_expiry} value={form.expires_at ?? ''} onChange={event => set('expires_at', event.target.value)} placeholder={relationshipDefinition?.allow_expiry ? '留空表示不到期' : '该类型不允许到期'} /></label></div>
        {!!relationshipDefinition?.dimensions?.length && <fieldset className="relationship-dimensions compact"><legend>{T.dimensionValues}</legend>{relationshipDefinition.dimensions.map(dimension => { let values = {}; try { values = JSON.parse(form._values_text || '{}') } catch { /* keep raw editor error for save */ } return <label key={dimension.dimension_id}>{dimension.display_name || dimension.dimension_id}<input type="number" step="any" min={dimension.minimum} max={dimension.maximum} value={values[dimension.dimension_id] ?? dimension.default ?? 0} onChange={event => { const next = { ...values, [dimension.dimension_id]: Number(event.target.value) }; setForm(current => ({ ...current, values: next, _values_text: jsonText(next) })) }} /><small>{dimension.dimension_id}: {dimension.minimum} ~ {dimension.maximum}</small></label> })}</fieldset>}
        <label>{T.dimensionValues} JSON<textarea className="dlc-json-field" rows="6" spellCheck="false" value={form._values_text || '{}'} onChange={event => set('_values_text', event.target.value)} /></label>
        <label>{T.note}<textarea rows="3" value={form.note || ''} onChange={event => set('note', event.target.value)} /></label>
        <label>{T.metadata}<textarea className="dlc-json-field" rows="5" spellCheck="false" value={form._metadata_text || '{}'} onChange={event => set('_metadata_text', event.target.value)} /></label>
        {form.parent_id && form.parent_id === form.child_id && <EditorError>A relation cannot point to itself.</EditorError>}
      </>}

      {kind === 'initial_events' && <>
        <div className="dlc-row"><label>{T.eventId}<input value={form.event_id || ''} onChange={event => set('event_id', event.target.value)} placeholder="evt_intro" /></label><label>{T.eventType}<input value={form.event_type || ''} onChange={event => set('event_type', event.target.value)} /></label></div>
        <div className="dlc-row"><label>{T.severity}<input type="number" min="1" max="10" value={form.severity ?? 5} onChange={event => set('severity', event.target.value)} /></label><label>{T.generatedAt}<input type="number" step="any" value={form.generated_at ?? 0} onChange={event => set('generated_at', event.target.value)} /></label></div>
        <AgentTargets value={form.target_agents || []} agents={agents} onChange={value => set('target_agents', value)} />
        <label>{T.initialPayload}<textarea className="dlc-json-field" rows="9" spellCheck="false" value={form._payload_text || ''} onChange={event => set('_payload_text', event.target.value)} /></label>
      </>}

      {kind === 'random_events' && <>
        <div className="dlc-row"><label>{T.templateId}<input value={form.template_id || ''} onChange={event => set('template_id', event.target.value)} placeholder="locust_cycle" /></label><label>{T.eventType}<input value={form.event_type || ''} onChange={event => set('event_type', event.target.value)} /></label></div>
        <label className="dlc-checkbox"><input type="checkbox" checked={form.enabled !== false} onChange={event => set('enabled', event.target.checked)} />{T.enabled}</label>
        <div className="dlc-row"><label>{T.severityMin}<input type="number" min="1" max="10" step="1" value={form.severity_min ?? 3} onChange={event => set('severity_min', event.target.value)} /></label><label>{T.severityMax}<input type="number" min="1" max="10" step="1" value={form.severity_max ?? 7} onChange={event => set('severity_max', event.target.value)} /></label></div>
        <div className="dlc-row"><label>{T.intervalMin}<input type="number" min="0.000001" step="any" value={form.min_interval_days ?? 30} onChange={event => set('min_interval_days', event.target.value)} /></label><label>{T.intervalMax}<input type="number" min="0.000001" step="any" value={form.max_interval_days ?? 90} onChange={event => set('max_interval_days', event.target.value)} /></label></div>
        <div className="dlc-row"><label>{T.probability}<input type="number" min="0" max="1" step="0.01" value={form.probability ?? 1} onChange={event => set('probability', event.target.value)} /></label><label>{T.maxOccurrences}<input type="number" min="0" step="1" value={form.max_occurrences ?? 0} onChange={event => set('max_occurrences', event.target.value)} /></label></div>
        <AgentTargets value={form.target_agents || []} agents={agents} onChange={value => set('target_agents', value)} />
        <label>{T.initialPayload}<textarea className="dlc-json-field" rows="9" spellCheck="false" value={form._payload_text || ''} onChange={event => set('_payload_text', event.target.value)} /></label>
      </>}

      {kind === 'causal_condition_groups' && <>
        <div className="dlc-row"><label>Group ID<input value={form.group_id || ''} onChange={event => set('group_id', event.target.value)} placeholder="treasury_ready" /></label><label>{T.name}<input value={form.name || ''} onChange={event => set('name', event.target.value)} /></label></div>
        <label>{T.description}<textarea rows="3" value={form.description || ''} onChange={event => set('description', event.target.value)} /></label>
        <CausalConditionBuilder value={form.conditions || []} entities={entities} resources={resources} actors={actors} agents={agents} relationshipTypes={relationshipTypes} objectives={objectives} ongoingEvents={ongoingEvents} eventTypes={eventTypes} onChange={value => set('conditions', value)} />
      </>}

      {kind === 'causal_rule_templates' && <>
        <div className="dlc-row"><label>{T.templateId}<input value={form.template_id || ''} onChange={event => set('template_id', event.target.value)} placeholder="delayed_follow_up" /></label><label>{T.name}<input value={form.name || ''} onChange={event => set('name', event.target.value)} /></label></div>
        <label>{T.description}<textarea rows="3" value={form.description || ''} onChange={event => set('description', event.target.value)} /></label>
        <div className="dlc-editor-callout">Reusable defaults are overridden by authored rule fields. Templates cannot recursively reference templates or condition groups.</div>
        <label>Rule defaults JSON<textarea className="dlc-json-field" rows="16" spellCheck="false" value={form._rule_text || ''} onChange={event => set('_rule_text', event.target.value)} /></label>
      </>}

      {kind === 'causal_event_rules' && <>
        <div className="dlc-row"><label>Reusable template<select value={form.template_id || ''} onChange={event => selectCausalTemplate(event.target.value)}><option value="">-- no template --</option>{causalTemplates.map(item => <option key={item.template_id} value={item.template_id}>{item.name || item.template_id} ({item.template_id})</option>)}</select></label><div className="dlc-form-actions">{form.template_id && (form._inherit_template_defaults ? <button type="button" onClick={overrideCausalTemplate}>Override template defaults</button> : <button type="button" onClick={() => selectCausalTemplate(form.template_id)}>Use template defaults</button>)}</div></div>
        {!!causalConditionGroups.length && <fieldset className="dlc-targets"><legend>Reusable condition groups</legend>{causalConditionGroups.map(group => { const checked = (form.condition_group_ids || []).includes(group.group_id); return <label key={group.group_id}><input type="checkbox" checked={checked} onChange={() => set('condition_group_ids', checked ? (form.condition_group_ids || []).filter(id => id !== group.group_id) : [...(form.condition_group_ids || []), group.group_id])} /><span>{group.name || group.group_id}</span><code>{group.group_id} / {group.conditions?.length || 0}</code></label> })}</fieldset>}
        {form.template_id && <div className="dlc-editor-callout">{form._inherit_template_defaults ? 'Using inherited template defaults; condition groups and inline conditions are appended in order.' : 'This rule overrides template defaults while retaining the reusable reference.'}{resolvedCausalRule && <small> Resolved: delay {resolvedCausalRule.delay_days ?? 0}d / probability {resolvedCausalRule.probability ?? 1} / {resolvedCausalRule.conditions?.length || 0} conditions / {resolvedCausalRule.world_effects?.length || 0} effects.</small>}</div>}
        <div className="dlc-row"><label>{T.ruleId}<input value={form.rule_id || ''} onChange={event => set('rule_id', event.target.value)} placeholder="drought_to_famine" /></label>{!form._inherit_template_defaults && <label>Priority<input type="number" step="1" value={form.priority ?? 0} onChange={event => set('priority', event.target.value)} /></label>}</div>
        <div className="dlc-row"><label>Source event types<input value={form._source_types_text || ''} onChange={event => set('_source_types_text', event.target.value)} placeholder="drought, crop_failure" /></label><label>{T.childEventType}<input value={form.child_event_type || ''} onChange={event => set('child_event_type', event.target.value)} placeholder="famine_risk" /></label></div>
        {!form._inherit_template_defaults && <>
          <label className="dlc-checkbox"><input type="checkbox" checked={form.enabled !== false} onChange={event => set('enabled', event.target.checked)} />{T.enabled}</label>
          <div className="dlc-row"><label>Delay days<input type="number" min="0" step="any" value={form.delay_days ?? 0} onChange={event => set('delay_days', event.target.value)} /></label><label>{T.probability}<input type="number" min="0" max="1" step="0.01" value={form.probability ?? 1} onChange={event => set('probability', event.target.value)} /></label></div>
          <div className="dlc-row"><label>Severity delta<input type="number" min="-9" max="9" step="1" value={form.severity_delta ?? 0} onChange={event => set('severity_delta', event.target.value)} /></label><label>Max chain depth<input type="number" min="1" max="16" step="1" value={form.max_depth ?? 4} onChange={event => set('max_depth', event.target.value)} /></label></div>
          <label>Max fires per chain<input type="number" min="1" max="64" step="1" value={form.max_fires_per_chain ?? 1} onChange={event => set('max_fires_per_chain', event.target.value)} /></label>
          <AgentTargets value={form.target_agents || []} agents={agents} onChange={value => set('target_agents', value)} />
          <div className="dlc-editor-callout">No target selected means inherit the source event targets. Anchored inventory conditions compare quantity * anchor_rate in base-value units.</div>
          <label>Inherit payload keys<input value={form._inherit_keys_text || ''} onChange={event => set('_inherit_keys_text', event.target.value)} placeholder="region, crop" /></label>
          <CausalWorldEffectBuilder value={form.world_effects || []} entities={entities} resources={resources} agents={agents} actors={actors} relationshipTypes={relationshipTypes} issueTypes={issueTypes} objectives={objectives} actions={actions} onChange={value => set('world_effects', value)} />
          <label>{T.initialPayload}<textarea className="dlc-json-field" rows="7" spellCheck="false" value={form._payload_text || ''} onChange={event => set('_payload_text', event.target.value)} /></label>
        </>}
        <CausalConditionBuilder value={form.conditions || []} entities={entities} resources={resources} actors={actors} agents={agents} relationshipTypes={relationshipTypes} objectives={objectives} ongoingEvents={ongoingEvents} eventTypes={eventTypes} onChange={value => set('conditions', value)} />
        <CausalDryRunPanel
          report={previewReport}
          replayComparison={replayComparison}
          busy={previewBusy}
          sourceType={previewSourceType}
          severity={previewSeverity}
          horizonDays={previewHorizon}
          eventTypes={eventTypes}
          onSourceType={setPreviewSourceType}
          onSeverity={setPreviewSeverity}
          onHorizonDays={setPreviewHorizon}
          onRun={runCausalPreview}
          onExportReplay={exportCausalReplay}
          onImportReplay={importCausalReplay}
        />
      </>}

      {kind === 'issue_types' && <>
        <div className="dlc-row"><label>Issue Type ID<input value={form.issue_type_id || ''} onChange={event => set('issue_type_id', event.target.value)} placeholder="frontier_security_report" /></label><label>显示名称<input value={form.display_name || ''} onChange={event => set('display_name', event.target.value)} /></label></div>
        <label>描述<textarea rows="3" value={form.description || ''} onChange={event => set('description', event.target.value)} /></label>
        <div className="dlc-row"><label>分类<input value={form.category || ''} onChange={event => set('category', event.target.value)} /></label><label>默认紧迫度<select value={form.default_urgency || 'normal'} onChange={event => set('default_urgency', event.target.value)}><option value="low">low</option><option value="normal">normal</option><option value="high">high</option><option value="critical">critical</option></select></label></div>
        <label>默认期限（天）<input type="number" min="0" step="any" value={form.default_deadline_days ?? ''} onChange={event => set('default_deadline_days', event.target.value)} /></label>
        <label className="dlc-checkbox"><input type="checkbox" checked={form.enabled !== false} onChange={event => set('enabled', event.target.checked)} />{T.enabled}</label>
        {[['source', 'Source 约束（JSON object）'], ['match', '匹配规则（JSON object）'], ['evidence', '证据定义（JSON array）'], ['decision_requests', '决策请求（JSON array）'], ['metadata', 'Metadata（JSON object）']].map(([field, label]) => <label key={field}>{label}<textarea className="dlc-json-field" rows={field === 'decision_requests' ? 16 : 7} spellCheck="false" value={form[`_${field}_text`] || ''} onChange={event => set(`_${field}_text`, event.target.value)} /></label>)}
        <div className="dlc-editor-callout">一个 Case 可聚合多个 Issue；决策选项可引用行动 ID，并继承行动权限、成本、耗时与世界效果。</div>
      </>}

      {kind === 'capabilities' && <>
        <div className="dlc-editor-callout">DLC \u80fd\u529b\u662f\u73a9\u5bb6\u3001Agent \u548c Router \u5171\u7528\u7684\u7a33\u5b9a\u64cd\u4f5c\u5165\u53e3\u3002\u53ea\u80fd\u9009\u62e9\u6846\u67b6\u9884\u7f6e handler\uff0c\u4e0d\u4f1a\u6ce8\u5165\u4efb\u610f Python/React \u51fd\u6570\u3002</div>
        <div className="dlc-row"><label>\u80fd\u529b ID<input value={form.capability_id || ''} onChange={event => set('capability_id', event.target.value)} placeholder="frontier_report" /><small>\u53d1\u5e03\u540e\u4f5c\u4e3a API\u3001Agent \u5de5\u5177\u4e0e Router \u8def\u7531\u7684\u7a33\u5b9a\u6807\u8bc6\uff1b\u5efa\u8bae\u4e0d\u518d\u6539\u540d\u3002</small></label><label>\u663e\u793a\u540d\u79f0<input value={form.display_name || ''} onChange={event => set('display_name', event.target.value)} placeholder="\u4e0a\u62a5\u60c5\u51b5" /><small>\u5728\u73a9\u5bb6\u80fd\u529b\u9762\u677f\u548c\u8c03\u8bd5\u754c\u9762\u663e\u793a\u7684\u4e2d\u6587\u540d\u79f0\u3002</small></label></div>
        <label>\u4e2d\u6587\u8bf4\u660e<textarea rows="3" value={form.description || ''} onChange={event => set('description', event.target.value)} placeholder="\u8bf4\u660e\u8c01\u53ef\u4ee5\u5728\u4ec0\u4e48\u573a\u666f\u4f7f\u7528\uff0c\u4f1a\u4ea7\u751f\u4ec0\u4e48\u6548\u679c\u3002" /><small>\u8fd9\u6bb5\u6587\u5b57\u4f1a\u8fdb\u5165\u73a9\u5bb6 UI \u4e0e Agent/Router \u5de5\u5177\u63cf\u8ff0\uff0c\u8bf7\u5199\u6e05\u8f93\u5165\u3001\u76ee\u6807\u548c\u7ea6\u675f\u3002</small></label>
        <div className="dlc-row"><label>Handler<select value={form.handler || 'message'} onChange={event => setCapabilityHandler(event.target.value)}><option value="noop">\u7a7a\u64cd\u4f5c\uff08noop\uff09</option><option value="message">\u5355\u76ee\u6807\u6d88\u606f\uff08message\uff09</option><option value="broadcast">\u591a\u76ee\u6807\u5e7f\u64ad\uff08broadcast\uff09</option><option value="world_action">\u4e16\u754c\u884c\u52a8\uff08world_action\uff09</option><option value="contract_action">\u5951\u7ea6 Action\uff08contract_action\uff09</option></select><small>\u51b3\u5b9a\u6846\u67b6\u5982\u4f55\u6267\u884c\u8be5\u80fd\u529b\uff1bnoop \u4ec5\u8868\u793a\u7b49\u5f85\uff0ccontract_action \u4f1a\u59d4\u6258\u7ed9 Action Catalog\u3002</small></label><label>\u9ed8\u8ba4\u7d27\u6025\u5ea6<select value={form.default_urgency || 'normal'} onChange={event => set('default_urgency', event.target.value)}><option value="normal">\u666e\u901a</option><option value="urgent">\u7d27\u6025</option><option value="critical">\u5371\u6025</option></select><small>\u8c03\u7528\u65b9\u672a\u6307\u5b9a urgency \u65f6\u4f7f\u7528\u6b64\u503c\u3002</small></label></div>
        <fieldset className="dlc-targets"><legend>\u53ef\u7528\u5bf9\u8c61 Audiences</legend>{[['player', '\u73a9\u5bb6'], ['agent', '\u666e\u901a Agent'], ['router', 'Router LLM']].map(([value, label]) => <label key={value}><input type="checkbox" checked={(form.audiences || []).includes(value)} onChange={() => toggleCapabilityAudience(value)} /><span>{label}</span><code>{value}</code></label>)}<small>player \u51b3\u5b9a\u662f\u5426\u51fa\u73b0\u5728\u73a9\u5bb6\u9762\u677f\uff1bagent/router \u51b3\u5b9a\u662f\u5426\u8fdb\u5165\u5bf9\u5e94 LLM \u7684\u52a8\u6001\u5de5\u5177\u3002</small></fieldset>
        <label>\u522b\u540d Aliases<input value={form._aliases_text || ''} onChange={event => set('_aliases_text', event.target.value)} placeholder="report, send_report" /><small>\u9017\u53f7\u5206\u9694\uff0c\u7528\u4e8e\u517c\u5bb9\u65e7\u6307\u4ee4\u6216\u66f4\u53cb\u597d\u7684\u81ea\u7136\u8bed\u8a00\u6807\u8bc6\uff1b\u5168\u5c40\u4e0d\u5f97\u4e0e\u5176\u4ed6 ID/\u522b\u540d\u51b2\u7a81\u3002</small></label>
        <div className="dlc-row"><label>\u76ee\u6807\u7c7b\u578b<select value={form.target?.kind || 'none'} disabled={form.handler === 'noop' || ['message', 'broadcast'].includes(form.handler)} onChange={event => setCapabilityTargetKind(event.target.value)}><option value="none">\u65e0\u76ee\u6807</option><option value="agent">Agent</option><option value="entity">\u5b9e\u4f53</option></select><small>message/broadcast \u56fa\u5b9a\u4e3a Agent\uff1bnoop \u56fa\u5b9a\u4e3a\u65e0\u76ee\u6807\u3002</small></label><label>\u76ee\u6807\u9009\u62e9\u6a21\u5f0f<select value={form.target?.mode || 'none'} disabled={form.target?.kind === 'none'} onChange={event => setForm(current => ({ ...current, target: { ...(current.target || {}), mode: event.target.value } }))}><option value="none">\u4e0d\u63a5\u53d7\u76ee\u6807</option><option value="optional">\u53ef\u9009\u5355\u76ee\u6807</option><option value="required">\u5fc5\u586b\u5355\u76ee\u6807</option><option value="multiple">\u5fc5\u586b\u591a\u76ee\u6807</option></select><small>multiple \u8981\u6c42\u81f3\u5c11\u4e00\u4e2a\u76ee\u6807\uff1boptional/required \u6700\u591a\u4e00\u4e2a\u3002</small></label></div>
        <label>\u5141\u8bb8\u7684\u76ee\u6807 ID<input value={form._allowed_ids_text || ''} disabled={form.target?.kind === 'none'} onChange={event => set('_allowed_ids_text', event.target.value)} placeholder="guard, station" /><small>\u9017\u53f7\u5206\u9694\u3002\u7559\u7a7a\u8868\u793a\u4e0d\u5728 DLC \u5b9a\u4e49\u5c42\u9650\u5b9a\uff1b\u586b\u5199\u540e Registry \u4f1a\u6821\u9a8c ID \u5b58\u5728\u4e14\u8c03\u7528\u4e0d\u80fd\u8d8a\u754c\u3002</small></label>
        {form.handler === 'contract_action' && <label>\u7ed1\u5b9a Action ID<select value={form.action_id || ''} onChange={event => set('action_id', event.target.value)}><option value="">-- \u8bf7\u9009\u62e9 --</option>{(content.actions || []).map(action => <option key={action.action_id} value={action.action_id}>{action.display_name || action.action_id} ({action.action_id})</option>)}</select><small>\u5fc5\u987b\u5f15\u7528\u5f53\u524d Generation \u4e2d\u5b58\u5728\u7684 DLC Action\uff0c\u6267\u884c\u65f6\u590d\u7528\u6743\u9650\u3001\u6210\u672c\u3001\u8017\u65f6\u548c\u51b7\u5374\u5951\u7ea6\u3002</small></label>}
        <label>\u80fd\u529b\u53c2\u6570\uff08JSON \u6570\u7ec4\uff09<textarea className="dlc-json-field" rows="10" spellCheck="false" value={form._parameters_text || ''} disabled={form.handler === 'noop'} onChange={event => set('_parameters_text', event.target.value)} /><small>\u6bcf\u9879\u652f\u6301 name\u3001type(string/integer/number/boolean)\u3001display_name\u3001description\u3001required\u3001default\u3001minimum\u3001maximum\u3001options\u3002contract_action \u8fd8\u53ef\u900f\u4f20 Action \u53c2\u6570\u3002</small></label>
        <div className="dlc-row"><label className="dlc-checkbox"><input type="checkbox" checked={Boolean(form.input?.content_required)} disabled={form.handler === 'noop'} onChange={event => setForm(current => ({ ...current, input: { ...(current.input || {}), content_required: event.target.checked } }))} />\u5185\u5bb9\u5fc5\u586b<small>\u5f00\u542f\u540e\uff0cAPI \u4e0e Agent \u8c03\u7528\u5fc5\u987b\u63d0\u4f9b\u975e\u7a7a content\u3002</small></label><label className="dlc-checkbox"><input type="checkbox" checked={form.enabled !== false} onChange={event => set('enabled', event.target.checked)} />{T.enabled}<small>\u505c\u7528\u540e\u4fdd\u7559\u5b9a\u4e49\uff0c\u4f46\u4e0d\u4f1a\u66b4\u9732\u7ed9\u73a9\u5bb6\u6216 LLM\u3002</small></label></div>
        <div className="dlc-row"><label>\u5185\u5bb9\u5b57\u6bb5\u6807\u7b7e<input value={form.input?.content_label || ''} onChange={event => setForm(current => ({ ...current, input: { ...(current.input || {}), content_label: event.target.value } }))} placeholder="\u547d\u4ee4\u5185\u5bb9" /><small>\u73a9\u5bb6\u9762\u677f\u7684\u8f93\u5165\u6846\u6807\u9898\u3002</small></label><label>\u5185\u5bb9\u5360\u4f4d\u63d0\u793a<input value={form.input?.content_placeholder || ''} onChange={event => setForm(current => ({ ...current, input: { ...(current.input || {}), content_placeholder: event.target.value } }))} placeholder="\u8bf7\u8f93\u5165\u8be6\u7ec6\u547d\u4ee4..." /><small>\u7528\u6765\u5f15\u5bfc\u73a9\u5bb6\u6216\u7f16\u8f91\u8005\u5199\u51fa\u6709\u6548\u8f93\u5165\u3002</small></label></div>
      </>}

      {kind === 'actions' && <>
        <div className="dlc-row"><label>Action ID<input value={form.action_id || ''} onChange={event => set('action_id', event.target.value)} placeholder="frontier_patrol" /></label><label>Display name<input value={form.display_name || ''} onChange={event => set('display_name', event.target.value)} /></label></div>
        <label>Description<textarea rows="3" value={form.description || ''} onChange={event => set('description', event.target.value)} /></label>
        <label className="dlc-checkbox"><input type="checkbox" checked={form.enabled !== false} onChange={event => set('enabled', event.target.checked)} />{T.enabled}</label>
        {[['parameters', 'Parameters (JSON array)'], ['target', 'Target (JSON object)'], ['permissions', 'Permissions (JSON object)'], ['preconditions', 'Preconditions (JSON array)'], ['costs', 'Anchored costs (JSON array)'], ['duration', 'Duration (JSON object)'], ['cooldown', 'Cooldown (JSON object)'], ['result', 'Result (JSON object)']].map(([field, label]) => <label key={field}>{label}<textarea className="dlc-json-field" rows={field === 'result' ? 12 : 7} spellCheck="false" value={form[`_${field}_text`] || ''} onChange={event => set(`_${field}_text`, event.target.value)} /></label>)}
        <div className="dlc-editor-callout">Costs are declared in anchored base value. Preview and execution share deterministic permission, time, cooldown and world-command rules.</div>
      </>}

      {kind === 'time_rules' && <>
        <div className="dlc-row"><label>{T.ruleId}<input value={form.rule_id || ''} onChange={event => set('rule_id', event.target.value)} placeholder="policy_time" /></label><label>Priority<input type="number" step="1" value={form.priority ?? 0} onChange={event => set('priority', event.target.value)} /></label></div>
        <label>{T.actionTypes}<input value={form._action_types_text || ''} onChange={event => set('_action_types_text', event.target.value)} placeholder="execute_policy, relief" /></label>
        <label className="dlc-checkbox"><input type="checkbox" checked={form.enabled !== false} onChange={event => set('enabled', event.target.checked)} />{T.enabled}</label>
        <div className="dlc-row"><label>Base days<input type="number" min="0" step="any" value={form.base_days ?? 0} onChange={event => set('base_days', event.target.value)} /></label><label>Workload factor<input type="number" min="0" step="any" value={form.workload_factor ?? 1} onChange={event => set('workload_factor', event.target.value)} /></label></div>
        <div className="dlc-row"><label>Minimum days<input type="number" min="0" step="any" value={form.min_days ?? 0.1} onChange={event => set('min_days', event.target.value)} /></label><label>Maximum days<input type="number" min="0" step="any" value={form.max_days ?? 365} onChange={event => set('max_days', event.target.value)} /></label></div>
        <div className="dlc-row"><label>Severity factor<input type="number" min="0" step="any" value={form.severity_factor ?? 0} onChange={event => set('severity_factor', event.target.value)} /></label><label>Distance factor<input type="number" min="0" step="any" value={form.distance_factor ?? 1} onChange={event => set('distance_factor', event.target.value)} /></label></div>
        <div className="dlc-row"><label>Resource factor<input type="number" min="0" step="any" value={form.resource_factor ?? 0} onChange={event => set('resource_factor', event.target.value)} /></label><label>Failure factor<input type="number" min="0" step="any" value={form.success_factor ?? 1} onChange={event => set('success_factor', event.target.value)} /></label></div>
        <div className="dlc-editor-callout">Final days = clamp(base + workload + severity + travel - anchored-resource discount). The LLM duration is only a bounded workload hint.</div>
      </>}

      {kind === 'ongoing_events' && <>
        <div className="dlc-row"><label>{T.eventId}<input value={form.event_id || ''} onChange={event => set('event_id', event.target.value)} placeholder="construction_01" /></label><label>{T.eventType}<input value={form.event_type || ''} onChange={event => set('event_type', event.target.value)} /></label></div>
        <EntitySelect value={form.entity_id} entities={entities} onChange={value => set('entity_id', value)} />
        <label>{T.description}<textarea rows="3" value={form.description || ''} onChange={event => set('description', event.target.value)} /></label>
        <div className="dlc-row"><label>{T.startTime}<input type="number" step="any" value={form.start_time ?? 0} onChange={event => set('start_time', event.target.value)} /></label><label>{T.endTime}<input type="number" step="any" value={form.end_time ?? 0} onChange={event => set('end_time', event.target.value)} /></label></div>
        <label>{T.sourceAction}<input value={form.source_action_id || ''} onChange={event => set('source_action_id', event.target.value)} /></label>
        <label>{T.expectedResult}<textarea className="dlc-json-field" rows="10" spellCheck="false" value={form._result_text || ''} onChange={event => set('_result_text', event.target.value)} /></label>
        {Number(form.end_time) < Number(form.start_time) && <EditorError>end_time cannot be earlier than start_time.</EditorError>}
      </>}

      <EditorError>{error}</EditorError>
      <div className="dlc-form-actions">{selectedIndex >= 0 && <button className="danger" disabled={busy} onClick={remove}>{T.delete}</button>}<button className="primary" disabled={busy || !requiredKey(kind, form) || (kind === 'relations' && form.parent_id === form.child_id)} onClick={save}>{T.save}{TITLES[kind]}</button></div>
    </div>
  </div>
}

export function PersonaEditor({ content, selected, onSelect, onSaveContent, busy = false }) {
  const personas = content.personas || {}
  const names = Object.keys(personas).sort()
  const [name, setName] = useState(selected || '')
  const [text, setText] = useState(selected ? String(personas[selected] || '') : '')

  const save = () => {
    const key = name.trim()
    if (!key || !text.trim()) return
    const next = { ...personas }
    if (selected && selected !== key) delete next[selected]
    next[key] = text
    onSaveContent({ ...content, personas: next }, `${T.persona} ${key} saved`)
  }
  const remove = () => {
    if (!selected || !window.confirm(`${T.delete} ${selected}?`)) return
    const next = { ...personas }
    delete next[selected]
    onSaveContent({ ...content, personas: next }, `${T.persona} deleted`)
  }

  return <div className="dlc-content-grid dlc-structured-grid"><div className="dlc-item-list"><button className="dlc-new" onClick={() => onSelect('')}>{T.add}{T.persona}</button>{names.map(key => <button key={key} className={selected === key ? 'active' : ''} onClick={() => onSelect(key)}><strong>{key}</strong><small>{String(personas[key]).slice(0, 52)}</small></button>)}</div><div className="dlc-form dlc-structured-form"><div className="dlc-form-title"><h3>{selected ? T.edit : T.add}{T.persona}</h3>{selected && <code>{selected}</code>}</div><label>{T.name}<input value={name} disabled={Boolean(selected)} onChange={event => setName(event.target.value)} placeholder="steady" /></label><label>{T.personaText}<textarea rows="15" value={text} onChange={event => setText(event.target.value)} /></label><div className="dlc-form-actions">{selected && <button className="danger" disabled={busy} onClick={remove}>{T.delete}</button>}<button className="primary" disabled={busy || !name.trim() || !text.trim()} onClick={save}>{T.save}{T.persona}</button></div></div></div>
}

function normalizePrompt(source) {
  if (typeof source === 'string') return { version: '1.0', max_chars: '', metadata: {}, _metadata_text: '{}', blocks: [{ name: 'main', template: source, priority: 0, required_variables: [], enabled: true }] }
  const value = source && typeof source === 'object' && !Array.isArray(source) ? deepClone(source) : {}
  let blocks = Array.isArray(value.blocks) ? value.blocks : [{ name: 'main', template: value.template || value.content || '', priority: 0, required_variables: [], enabled: true }]
  blocks = blocks.map((block, index) => typeof block === 'string' ? { name: `block_${index + 1}`, template: block, priority: index * 10, required_variables: [], enabled: true } : { name: block.name || block.id || `block_${index + 1}`, template: block.template ?? block.content ?? '', priority: Number(block.priority ?? index * 10), required_variables: Array.isArray(block.required_variables) ? block.required_variables : Array.isArray(block.required) ? block.required : typeof block.required_variables === 'string' ? [block.required_variables] : [], enabled: block.enabled !== false })
  return { version: String(value.version || '1.0'), max_chars: value.max_chars ?? '', metadata: value.metadata || {}, _metadata_text: jsonText(value.metadata), blocks }
}

function serializePrompt(form) {
  const metadata = parseObject(form._metadata_text, T.metadata)
  const blocks = form.blocks.map((block, index) => ({ name: String(block.name || `block_${index + 1}`).trim(), template: String(block.template || ''), priority: Number(block.priority || 0), required_variables: [...new Set((block.required_variables || []).map(item => String(item).trim()).filter(Boolean))], enabled: block.enabled !== false }))
  if (!blocks.length) throw new Error('Prompt requires at least one block')
  if (blocks.some(block => !block.name || !block.template.trim())) throw new Error('Every Prompt block requires a name and template')
  const result = { version: String(form.version || '1.0'), blocks, metadata }
  if (form.max_chars !== '' && form.max_chars !== null) {
    const maxChars = Number(form.max_chars)
    if (!Number.isInteger(maxChars) || maxChars <= 0) throw new Error('max_chars must be a positive integer')
    result.max_chars = maxChars
  }
  return result
}

export function PromptEditor({ content, selected, onSelect, onSaveContent, busy = false }) {
  const prompts = content.prompts || {}
  const names = Object.keys(prompts).sort()
  const [name, setName] = useState(selected || '')
  const [form, setForm] = useState(() => normalizePrompt(selected ? prompts[selected] : ''))
  const [error, setError] = useState('')

  const set = (field, value) => setForm(current => ({ ...current, [field]: value }))
  const setBlock = (index, field, value) => setForm(current => ({ ...current, blocks: current.blocks.map((block, blockIndex) => blockIndex === index ? { ...block, [field]: value } : block) }))
  const addBlock = () => setForm(current => ({ ...current, blocks: [...current.blocks, { name: `block_${current.blocks.length + 1}`, template: '', priority: current.blocks.length * 10, required_variables: [], enabled: true }] }))
  const removeBlock = index => setForm(current => ({ ...current, blocks: current.blocks.filter((_, blockIndex) => blockIndex !== index) }))
  const preview = useMemo(() => [...form.blocks].filter(block => block.enabled !== false).sort((a, b) => Number(a.priority || 0) - Number(b.priority || 0)).map(block => block.template).join('\n\n'), [form.blocks])

  const save = () => {
    const key = name.trim()
    if (!key) return
    try {
      const next = { ...prompts }
      if (selected && selected !== key) delete next[selected]
      next[key] = serializePrompt(form)
      setError('')
      onSaveContent({ ...content, prompts: next }, `${T.prompt} ${key} saved`)
    } catch (caught) {
      setError(caught.message)
    }
  }
  const remove = () => {
    if (!selected || !window.confirm(`${T.delete} ${selected}?`)) return
    const next = { ...prompts }
    delete next[selected]
    onSaveContent({ ...content, prompts: next }, `${T.prompt} deleted`)
  }

  return <div className="dlc-content-grid dlc-prompt-grid"><div className="dlc-item-list"><button className="dlc-new" onClick={() => onSelect('')}>{T.add}{T.prompt}</button>{names.map(key => { const prompt = normalizePrompt(prompts[key]); return <button key={key} className={selected === key ? 'active' : ''} onClick={() => onSelect(key)}><strong>{key}</strong><small>v{prompt.version} \u00b7 {prompt.blocks.length} blocks</small></button> })}</div><div className="dlc-form dlc-prompt-form"><div className="dlc-form-title"><h3>{selected ? T.edit : T.add}{T.prompt}</h3>{selected && <code>{selected}</code>}</div><div className="dlc-row"><label>{T.name}<input value={name} disabled={Boolean(selected)} onChange={event => setName(event.target.value)} placeholder="official_template" /></label><label>{T.version}<input value={form.version} onChange={event => set('version', event.target.value)} /></label></div><label>{T.maxChars}<input type="number" min="1" value={form.max_chars} onChange={event => set('max_chars', event.target.value)} placeholder="12000" /></label><label>{T.metadata}<textarea className="dlc-json-field" rows="4" spellCheck="false" value={form._metadata_text} onChange={event => set('_metadata_text', event.target.value)} /></label><div className="dlc-prompt-help"><strong>Agent variables</strong><code>{'{id} {name} {rank} {rank_title} {location} {personality} {superior} {role} {agent_type} {actor} {actor_id} {actor_type} {actor_entity_id} {actor_attributes} {actor_traits} {actor_needs} {actor_goals} {actor_capabilities} {actor_identities} {actor_memberships} {actor_states}'}</code><small className="dlc-field-help">actor 变量会以安全 JSON 注入 Prompt；可使用 attributes、needs、goals、states 等字段。Generation 归档会保留这些 Actor 定义；Metadata 默认不会注入提示词。</small></div><section className="dlc-blocks"><div className="dlc-blocks-header"><h4>{T.promptBlocks}</h4><button onClick={addBlock}>{T.add} block</button></div>{form.blocks.map((block, index) => <article className="dlc-prompt-block" key={`${index}-${block.name}`}><div className="dlc-block-toolbar"><strong>#{index + 1}</strong><label className="dlc-check"><input type="checkbox" checked={block.enabled !== false} onChange={event => setBlock(index, 'enabled', event.target.checked)} /> enabled</label><button className="danger" disabled={form.blocks.length <= 1} onClick={() => removeBlock(index)}>{T.delete}</button></div><div className="dlc-row"><label>{T.name}<input value={block.name} onChange={event => setBlock(index, 'name', event.target.value)} /></label><label>Priority<input type="number" value={block.priority} onChange={event => setBlock(index, 'priority', event.target.value)} /></label></div><label>{T.requiredVariables}<input value={(block.required_variables || []).join(', ')} onChange={event => setBlock(index, 'required_variables', event.target.value.split(',').map(item => item.trim()).filter(Boolean))} /></label><label>{T.template}<textarea rows="9" value={block.template} onChange={event => setBlock(index, 'template', event.target.value)} /></label></article>)}</section><section className="dlc-prompt-preview"><div><h4>{T.promptPreview}</h4><small>{preview.length} chars \u00b7 ~{Math.ceil(preview.length / 2)} tokens</small></div><pre>{preview || 'Empty prompt'}</pre></section><EditorError>{error}</EditorError><div className="dlc-form-actions">{selected && <button className="danger" disabled={busy} onClick={remove}>{T.delete}</button>}<button className="primary" disabled={busy || !name.trim()} onClick={save}>{T.save}{T.prompt}</button></div></div></div>
}
