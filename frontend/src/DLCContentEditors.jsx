import { useMemo, useRef, useState } from 'react'
import { buildCausalDryRunDraft, buildCausalEventCatalog, expandCausalRuleDraft, normalizeCausalReplayFixture } from './DLCStudioTools.js'

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
  relations: { parent_id: '', child_id: '', rel_type: 'subordinate', note: '' },
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
  if (kind === 'causal_condition_groups') source.conditions = Array.isArray(source.conditions) ? source.conditions : []
  if (kind === 'causal_rule_templates') source._rule_text = JSON.stringify(source.rule && typeof source.rule === 'object' && !Array.isArray(source.rule) ? source.rule : {}, null, 2)
  if (kind === 'causal_event_rules') {
    source._source_types_text = (source.source_event_types || ['*']).join(', ')
    source.condition_group_ids = Array.isArray(source.condition_group_ids) ? source.condition_group_ids : []
    source._inherit_template_defaults = Boolean(source.template_id) && !['delay_days', 'probability', 'severity_delta', 'initial_payload', 'inherit_payload_keys', 'world_effects', 'max_depth', 'max_fires_per_chain', 'priority', 'enabled', 'target_agents'].some(key => Object.hasOwn(source, key))
    source._inherit_keys_text = (source.inherit_payload_keys || []).join(', ')
    source.conditions = Array.isArray(source.conditions) ? source.conditions : []
    source.world_effects = (Array.isArray(source.world_effects) ? source.world_effects : []).map(effect => ({
      ...effect,
      _expected_result_text: effect.effect_type === 'create_ongoing_event' ? jsonText(effect.expected_result) : undefined,
    }))
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
    data.conditions = (Array.isArray(data.conditions) ? data.conditions : []).map(condition => ({
      kind: String(condition.kind || ''), entity_id: String(condition.entity_id || ''),
      name: String(condition.name || ''), operator: String(condition.operator || ''), value: Number(condition.value),
    }))
    const kinds = new Set(['anchored_inventory', 'metric', 'parameter'])
    const operators = new Set(['lt', 'lte', 'eq', 'gte', 'gt'])
    if (!data.conditions.length || data.conditions.some(condition => !kinds.has(condition.kind) || !condition.entity_id || !condition.name || !operators.has(condition.operator) || !Number.isFinite(condition.value))) throw new Error('Condition groups require at least one complete condition.')
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
    data.conditions = (Array.isArray(data.conditions) ? data.conditions : []).map(condition => ({
      kind: String(condition.kind || ''),
      entity_id: String(condition.entity_id || ''),
      name: String(condition.name || ''),
      operator: String(condition.operator || ''),
      value: Number(condition.value),
    }))
    data.world_effects = (Array.isArray(data.world_effects) ? data.world_effects : []).map(source => {
      const effect = { ...source, effect_type: String(source.effect_type || '') }
      delete effect._expected_result_text
      if (effect.effect_type === 'adjust_resource') effect.quantity_delta = Number(effect.quantity_delta)
      if (effect.effect_type === 'set_resource') effect.quantity = Number(effect.quantity)
      if (effect.effect_type === 'transfer_anchored_value') effect.base_value = Number(effect.base_value)
      if (effect.effect_type === 'create_ongoing_event') {
        effect.duration_days = Number(effect.duration_days)
        effect.expected_result = parseObject(source._expected_result_text || '{}', 'Ongoing expected result')
      }
      return effect
    })
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
    const conditionKinds = new Set(['anchored_inventory', 'metric', 'parameter'])
    const conditionOperators = new Set(['lt', 'lte', 'eq', 'gte', 'gt'])
    if (data.conditions.some(condition => !conditionKinds.has(condition.kind) || !condition.entity_id || !condition.name || !conditionOperators.has(condition.operator) || !Number.isFinite(condition.value))) throw new Error('Every condition needs a kind, entity, matching resource, operator, and finite value.')
    const effectTypes = new Set(['adjust_resource', 'set_resource', 'update_status', 'create_ongoing_event', 'transfer_anchored_value'])
    if (data.world_effects.length > 32 || data.world_effects.some(effect => !effectTypes.has(effect.effect_type))) throw new Error('World effects must use a supported type and cannot exceed 32 entries.')
    if (data.world_effects.some(effect => effect.effect_type !== 'transfer_anchored_value' && !effect.entity_id)) throw new Error('Every world effect requires an entity.')
    if (data.world_effects.some(effect => ['adjust_resource', 'set_resource'].includes(effect.effect_type) && (!effect.resource_name || !Number.isFinite(effect.quantity_delta ?? effect.quantity)))) throw new Error('Resource effects require a resource and finite quantity.')
    if (data.world_effects.some(effect => effect.effect_type === 'update_status' && !String(effect.new_status || '').trim())) throw new Error('Status effects require a new status.')
    if (data.world_effects.some(effect => effect.effect_type === 'create_ongoing_event' && (!safeId(effect.event_type) || !Number.isFinite(effect.duration_days) || effect.duration_days < 0 || effect.duration_days > 3650))) throw new Error('Ongoing effects require a safe event type and duration from 0 to 3650 days.')
    if (data.world_effects.some(effect => effect.effect_type === 'transfer_anchored_value' && (!effect.from_entity_id || !effect.to_entity_id || !effect.from_resource || !effect.to_resource || !Number.isFinite(effect.base_value) || effect.base_value <= 0))) throw new Error('Anchored transfers require both entities, both asset resources, and a positive base value.')
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

const CONDITION_KINDS = [
  { key: 'anchored_inventory', label: 'Anchored inventory value' },
  { key: 'metric', label: 'World metric' },
  { key: 'parameter', label: 'World parameter' },
]
const CONDITION_OPERATORS = [
  { key: 'lt', label: '<' }, { key: 'lte', label: '<=' }, { key: 'eq', label: '=' },
  { key: 'gte', label: '>=' }, { key: 'gt', label: '>' },
]

function resourcesForCondition(resources, kind) {
  const resourceKind = kind === 'anchored_inventory' ? 'asset' : kind
  return resources.filter(item => item.kind === resourceKind)
}

function CausalConditionBuilder({ value, entities, resources, onChange }) {
  const conditions = Array.isArray(value) ? value : []
  const update = (index, name, nextValue) => {
    const next = conditions.map(item => ({ ...item }))
    next[index][name] = nextValue
    if (name === 'kind') {
      const candidates = resourcesForCondition(resources, nextValue)
      if (!candidates.some(item => item.name === next[index].name)) next[index].name = candidates[0]?.name || ''
    }
    onChange(next)
  }
  const add = () => {
    const kind = 'anchored_inventory'
    onChange([...conditions, {
      kind,
      entity_id: entities[0]?.id || '',
      name: resourcesForCondition(resources, kind)[0]?.name || '',
      operator: 'lt',
      value: 0,
    }])
  }
  const remove = index => onChange(conditions.filter((_, itemIndex) => itemIndex !== index))

  return <section className="dlc-condition-builder">
    <div className="dlc-condition-header"><div><strong>Condition builder</strong><small>All conditions must pass. No raw JSON is required.</small></div><button type="button" onClick={add}>+ Add condition</button></div>
    {!conditions.length && <div className="dlc-condition-empty">No conditions: matching event types continue to probability evaluation.</div>}
    {conditions.map((condition, index) => {
      const candidates = resourcesForCondition(resources, condition.kind)
      const selectedResource = candidates.find(item => item.name === condition.name)
      return <div className="dlc-condition-row" key={`condition-${index}`}>
        <label>Fact kind<select value={condition.kind || 'anchored_inventory'} onChange={event => update(index, 'kind', event.target.value)}>{CONDITION_KINDS.map(item => <option key={item.key} value={item.key}>{item.label}</option>)}</select></label>
        <EntitySelect value={condition.entity_id} entities={entities} onChange={next => update(index, 'entity_id', next)} />
        <label>Resource / value<select value={condition.name || ''} onChange={event => update(index, 'name', event.target.value)}><option value="">--</option>{candidates.map(item => <option key={item.name} value={item.name}>{item.name}{item.unit ? ` (${item.unit})` : ''}</option>)}</select></label>
        <label>Compare<select value={condition.operator || 'lt'} onChange={event => update(index, 'operator', event.target.value)}>{CONDITION_OPERATORS.map(item => <option key={item.key} value={item.key}>{item.label}</option>)}</select></label>
        <label>Threshold<input type="number" step="any" value={condition.value ?? 0} onChange={event => update(index, 'value', event.target.value)} /></label>
        <button type="button" className="danger" onClick={() => remove(index)}>Delete</button>
        {condition.kind === 'anchored_inventory' && <small className="dlc-condition-anchor">Compares quantity * anchor_rate{selectedResource ? `; ${selectedResource.name} anchor rate ${selectedResource.anchor_rate ?? 1}` : ''}</small>}
      </div>
    })}
  </section>
}

const WORLD_EFFECT_TYPES = [
  { key: 'adjust_resource', label: 'Adjust resource' },
  { key: 'set_resource', label: 'Set resource' },
  { key: 'update_status', label: 'Update entity status' },
  { key: 'create_ongoing_event', label: 'Create ongoing event' },
  { key: 'transfer_anchored_value', label: 'Transfer anchored value' },
]

function defaultWorldEffect(effectType, entities, resources) {
  const entityId = entities[0]?.id || ''
  const resource = resources[0]?.name || ''
  const asset = resources.find(item => item.kind === 'asset')?.name || ''
  if (effectType === 'set_resource') return { effect_type: effectType, entity_id: entityId, resource_name: resource, quantity: 0, reason: '' }
  if (effectType === 'update_status') return { effect_type: effectType, entity_id: entityId, new_status: '' }
  if (effectType === 'create_ongoing_event') return { effect_type: effectType, entity_id: entityId, event_type: 'state_change', description: '', duration_days: 1, expected_result: {}, _expected_result_text: '{}' }
  if (effectType === 'transfer_anchored_value') return { effect_type: effectType, from_entity_id: entityId, from_resource: asset, to_entity_id: entityId, to_resource: asset, base_value: 1, reason: '' }
  return { effect_type: 'adjust_resource', entity_id: entityId, resource_name: resource, quantity_delta: 0, reason: '' }
}

function ResourceSelect({ value, resources, assetsOnly = false, onChange }) {
  const choices = assetsOnly ? resources.filter(item => item.kind === 'asset') : resources
  return <label>Resource<select value={value || ''} onChange={event => onChange(event.target.value)}><option value="">--</option>{choices.map(item => <option key={item.name} value={item.name}>{item.name}{item.kind === 'asset' ? ` (anchor ${item.anchor_rate ?? 1})` : ` (${item.kind})`}</option>)}</select></label>
}

function CausalWorldEffectBuilder({ value, entities, resources, onChange }) {
  const effects = Array.isArray(value) ? value : []
  const update = (index, name, nextValue) => {
    const next = effects.map(item => ({ ...item }))
    next[index][name] = nextValue
    onChange(next)
  }
  const changeType = (index, effectType) => {
    const next = effects.map(item => ({ ...item }))
    next[index] = defaultWorldEffect(effectType, entities, resources)
    onChange(next)
  }
  const add = () => onChange([...effects, defaultWorldEffect('adjust_resource', entities, resources)])
  const remove = index => onChange(effects.filter((_, itemIndex) => itemIndex !== index))

  return <section className="dlc-world-effect-builder">
    <div className="dlc-condition-header"><div><strong>Transactional world effects</strong><small>Effects are frozen when scheduled and committed atomically before the child event reaches Agents.</small></div><button type="button" onClick={add}>+ Add world effect</button></div>
    {!effects.length && <div className="dlc-condition-empty">No effects: this rule only emits an Agent-facing event.</div>}
    {effects.map((effect, index) => <div className="dlc-world-effect-row" key={`world-effect-${index}`}>
      <div className="dlc-world-effect-toolbar"><label>Effect type<select value={effect.effect_type || 'adjust_resource'} onChange={event => changeType(index, event.target.value)}>{WORLD_EFFECT_TYPES.map(item => <option key={item.key} value={item.key}>{item.label}</option>)}</select></label><button type="button" className="danger" onClick={() => remove(index)}>Delete</button></div>
      {['adjust_resource', 'set_resource'].includes(effect.effect_type) && <div className="dlc-world-effect-grid">
        <EntitySelect value={effect.entity_id} entities={entities} onChange={next => update(index, 'entity_id', next)} />
        <ResourceSelect value={effect.resource_name} resources={resources} onChange={next => update(index, 'resource_name', next)} />
        <label>{effect.effect_type === 'adjust_resource' ? 'Quantity delta' : 'Quantity'}<input type="number" step="any" value={effect.effect_type === 'adjust_resource' ? effect.quantity_delta ?? 0 : effect.quantity ?? 0} onChange={event => update(index, effect.effect_type === 'adjust_resource' ? 'quantity_delta' : 'quantity', event.target.value)} /></label>
        <label>Reason<input value={effect.reason || ''} onChange={event => update(index, 'reason', event.target.value)} /></label>
      </div>}
      {effect.effect_type === 'update_status' && <div className="dlc-world-effect-grid"><EntitySelect value={effect.entity_id} entities={entities} onChange={next => update(index, 'entity_id', next)} /><label>New status<input value={effect.new_status || ''} onChange={event => update(index, 'new_status', event.target.value)} /></label></div>}
      {effect.effect_type === 'create_ongoing_event' && <div className="dlc-world-effect-grid">
        <EntitySelect value={effect.entity_id} entities={entities} onChange={next => update(index, 'entity_id', next)} />
        <label>Ongoing event type<input value={effect.event_type || ''} onChange={event => update(index, 'event_type', event.target.value)} /></label>
        <label>Duration days<input type="number" min="0" max="3650" step="any" value={effect.duration_days ?? 1} onChange={event => update(index, 'duration_days', event.target.value)} /></label>
        <label className="dlc-world-effect-wide">Description<input value={effect.description || ''} onChange={event => update(index, 'description', event.target.value)} /></label>
        <label className="dlc-world-effect-wide">Matured result JSON<textarea className="dlc-json-field" rows="5" spellCheck="false" value={effect._expected_result_text ?? jsonText(effect.expected_result)} onChange={event => update(index, '_expected_result_text', event.target.value)} /></label>
      </div>}
      {effect.effect_type === 'transfer_anchored_value' && <>
        <div className="dlc-world-effect-grid">
          <EntitySelect value={effect.from_entity_id} entities={entities} onChange={next => update(index, 'from_entity_id', next)} />
          <ResourceSelect value={effect.from_resource} resources={resources} assetsOnly onChange={next => update(index, 'from_resource', next)} />
          <EntitySelect value={effect.to_entity_id} entities={entities} onChange={next => update(index, 'to_entity_id', next)} />
          <ResourceSelect value={effect.to_resource} resources={resources} assetsOnly onChange={next => update(index, 'to_resource', next)} />
          <label>Base value<input type="number" min="0" step="any" value={effect.base_value ?? 1} onChange={event => update(index, 'base_value', event.target.value)} /></label>
          <label>Reason<input value={effect.reason || ''} onChange={event => update(index, 'reason', event.target.value)} /></label>
        </div>
        <small className="dlc-condition-anchor">Debit quantity = base value / source anchor; credit quantity = base value / destination anchor. The atomic batch conserves anchored value.</small>
      </>}
    </div>)}
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
  const resources = content.resources || []
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
        <div className="dlc-row"><EntitySelect label={T.parent} value={form.parent_id} entities={entities} onChange={value => set('parent_id', value)} /><EntitySelect label={T.child} value={form.child_id} entities={entities} onChange={value => set('child_id', value)} /></div>
        <label>{T.relationType}<select value={form.rel_type || 'subordinate'} onChange={event => set('rel_type', event.target.value)}><option value="subordinate">subordinate</option><option value="affiliated">affiliated</option></select></label>
        <label>{T.note}<textarea rows="3" value={form.note || ''} onChange={event => set('note', event.target.value)} /></label>
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
        <CausalConditionBuilder value={form.conditions || []} entities={entities} resources={resources} onChange={value => set('conditions', value)} />
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
          <CausalWorldEffectBuilder value={form.world_effects || []} entities={entities} resources={resources} onChange={value => set('world_effects', value)} />
          <label>{T.initialPayload}<textarea className="dlc-json-field" rows="7" spellCheck="false" value={form._payload_text || ''} onChange={event => set('_payload_text', event.target.value)} /></label>
        </>}
        <CausalConditionBuilder value={form.conditions || []} entities={entities} resources={resources} onChange={value => set('conditions', value)} />
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

  return <div className="dlc-content-grid dlc-prompt-grid"><div className="dlc-item-list"><button className="dlc-new" onClick={() => onSelect('')}>{T.add}{T.prompt}</button>{names.map(key => { const prompt = normalizePrompt(prompts[key]); return <button key={key} className={selected === key ? 'active' : ''} onClick={() => onSelect(key)}><strong>{key}</strong><small>v{prompt.version} \u00b7 {prompt.blocks.length} blocks</small></button> })}</div><div className="dlc-form dlc-prompt-form"><div className="dlc-form-title"><h3>{selected ? T.edit : T.add}{T.prompt}</h3>{selected && <code>{selected}</code>}</div><div className="dlc-row"><label>{T.name}<input value={name} disabled={Boolean(selected)} onChange={event => setName(event.target.value)} placeholder="official_template" /></label><label>{T.version}<input value={form.version} onChange={event => set('version', event.target.value)} /></label></div><label>{T.maxChars}<input type="number" min="1" value={form.max_chars} onChange={event => set('max_chars', event.target.value)} placeholder="12000" /></label><label>{T.metadata}<textarea className="dlc-json-field" rows="4" spellCheck="false" value={form._metadata_text} onChange={event => set('_metadata_text', event.target.value)} /></label><div className="dlc-prompt-help"><strong>Agent variables</strong><code>{'{id} {name} {rank} {rank_title} {location} {personality} {superior} {role} {agent_type}'}</code></div><section className="dlc-blocks"><div className="dlc-blocks-header"><h4>{T.promptBlocks}</h4><button onClick={addBlock}>{T.add} block</button></div>{form.blocks.map((block, index) => <article className="dlc-prompt-block" key={`${index}-${block.name}`}><div className="dlc-block-toolbar"><strong>#{index + 1}</strong><label className="dlc-check"><input type="checkbox" checked={block.enabled !== false} onChange={event => setBlock(index, 'enabled', event.target.checked)} /> enabled</label><button className="danger" disabled={form.blocks.length <= 1} onClick={() => removeBlock(index)}>{T.delete}</button></div><div className="dlc-row"><label>{T.name}<input value={block.name} onChange={event => setBlock(index, 'name', event.target.value)} /></label><label>Priority<input type="number" value={block.priority} onChange={event => setBlock(index, 'priority', event.target.value)} /></label></div><label>{T.requiredVariables}<input value={(block.required_variables || []).join(', ')} onChange={event => setBlock(index, 'required_variables', event.target.value.split(',').map(item => item.trim()).filter(Boolean))} /></label><label>{T.template}<textarea rows="9" value={block.template} onChange={event => setBlock(index, 'template', event.target.value)} /></label></article>)}</section><section className="dlc-prompt-preview"><div><h4>{T.promptPreview}</h4><small>{preview.length} chars \u00b7 ~{Math.ceil(preview.length / 2)} tokens</small></div><pre>{preview || 'Empty prompt'}</pre></section><EditorError>{error}</EditorError><div className="dlc-form-actions">{selected && <button className="danger" disabled={busy} onClick={remove}>{T.delete}</button>}<button className="primary" disabled={busy || !name.trim()} onClick={save}>{T.save}{T.prompt}</button></div></div></div>
}
