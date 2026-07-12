import { useMemo, useState } from 'react'
import { buildCausalDryRunDraft, buildCausalEventCatalog } from './DLCStudioTools.js'

const T = {
  add: '\uff0b \u65b0\u5efa',
  anchoredValue: '\u7edf\u4e00\u951a\u5b9a\u4ef7\u503c',
  assetOnly: '\u5e93\u5b58\u53ea\u80fd\u5f15\u7528 asset \u8d44\u6e90\uff1b\u9528\u5b9a\u4ef7\u503c = \u6570\u91cf \u00d7 anchor_rate\u3002',
  cancel: '\u53d6\u6d88',
  category: '\u5206\u7c7b',
  causalEvents: '\u56e0\u679c\u4e8b\u4ef6\u89c4\u5219',
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
  causal_event_rules: { rule_id: '', source_event_types: ['*'], child_event_type: 'follow_up', target_agents: [], delay_days: 1, probability: 1, severity_delta: 0, initial_payload: {}, inherit_payload_keys: [], conditions: [], world_effects: [], max_depth: 4, max_fires_per_chain: 1, priority: 0, enabled: true },
  time_rules: { rule_id: '', action_types: ['*'], base_days: 0, min_days: 0.1, max_days: 365, severity_factor: 0, workload_factor: 1, resource_factor: 0, distance_factor: 1, success_factor: 1, priority: 0, enabled: true },
}

const TITLES = {
  inventory: T.inventory,
  entity_values: T.values,
  relations: T.relations,
  initial_events: T.initialEvents,
  ongoing_events: T.ongoingEvents,
  random_events: T.randomEvents,
  causal_event_rules: T.causalEvents,
  time_rules: T.timeRules,
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


function normalizedListForm(kind, value) {
  const source = value ? deepClone(value) : deepClone(EMPTY[kind])
  if (kind === 'initial_events' || kind === 'random_events' || kind === 'causal_event_rules') source._payload_text = jsonText(source.initial_payload)
  if (kind === 'causal_event_rules') {
    source._source_types_text = (source.source_event_types || ['*']).join(', ')
    source._inherit_keys_text = (source.inherit_payload_keys || []).join(', ')
    source.conditions = Array.isArray(source.conditions) ? source.conditions : []
    source.world_effects = (Array.isArray(source.world_effects) ? source.world_effects : []).map(effect => ({
      ...effect,
      _expected_result_text: effect.effect_type === 'create_ongoing_event' ? jsonText(effect.expected_result) : undefined,
    }))
  }
  if (kind === 'ongoing_events') source._result_text = jsonText(source.expected_result)
  if (kind === 'time_rules') source._action_types_text = (source.action_types || ['*']).join(', ')
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
  if (kind === 'causal_event_rules') {
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
  if (kind === 'causal_event_rules') return [item.rule_id || T.ruleId, `${(item.source_event_types || []).join(', ')} \u2192 ${item.child_event_type || ''}`]
  if (kind === 'time_rules') return [item.rule_id || T.ruleId, `${(item.action_types || []).join(', ')} \u00b7 p${item.priority || 0}`]
  return [item.event_id || T.eventId, `${item.entity_id || ''} \u00b7 ${item.event_type || ''}`]
}

function requiredKey(kind, form) {
  if (kind === 'inventory') return form.entity_id && form.item_name
  if (kind === 'entity_values') return form.entity_id && form.name
  if (kind === 'relations') return form.parent_id && form.child_id && form.rel_type
  if (kind === 'initial_events') return form.event_id
  if (kind === 'ongoing_events') return form.event_id && form.entity_id
  if (kind === 'random_events') return form.template_id && form.event_type && (form.target_agents || []).length
  if (kind === 'causal_event_rules') return form.rule_id && form.child_event_type && String(form._source_types_text || '').trim()
  if (kind === 'time_rules') return form.rule_id && String(form._action_types_text || '').trim()
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

function CausalDryRunPanel({ report, busy, sourceType, severity, horizonDays, eventTypes, onSourceType, onSeverity, onHorizonDays, onRun }) {
  return <section className="dlc-causal-preview">
    <div className="dlc-condition-header"><div><strong>Deterministic chain preview</strong><small>Runs against this unsaved draft and DLC seed facts without changing live world state.</small></div></div>
    <div className="dlc-causal-preview-controls">
      <label>Source event type<input list="causal-event-type-catalog" value={sourceType} onChange={event => onSourceType(event.target.value)} placeholder="shortage" /><datalist id="causal-event-type-catalog">{eventTypes.map(item => <option key={item} value={item} />)}</datalist></label>
      <label>Severity<input type="number" min="1" max="10" step="1" value={severity} onChange={event => onSeverity(event.target.value)} /></label>
      <label>Horizon days<input type="number" min="0" max="3650" step="1" value={horizonDays} onChange={event => onHorizonDays(event.target.value)} /></label>
      <button type="button" className="primary" disabled={busy || !sourceType.trim()} onClick={onRun}>{busy ? 'Analyzing...' : 'Analyze and preview'}</button>
    </div>
    {report && <>
      <div className="dlc-causal-summary">
        <article><span>Rules</span><strong>{report.analysis.summary.enabled_count}/{report.analysis.summary.rule_count}</strong></article>
        <article><span>Event nodes</span><strong>{report.summary.event_count}</strong></article>
        <article><span>Errors / warnings</span><strong>{report.analysis.summary.error_count || 0} / {report.analysis.summary.warning_count}</strong></article>
        <article><span>Pending</span><strong>{report.summary.pending_count}</strong></article>
      </div>
      {!!report.analysis.issues.length && <div className="dlc-causal-issues">{report.analysis.issues.map((issue, index) => <div key={`${issue.code}-${index}`} className={issue.severity}><strong>{issue.severity}</strong><code>{issue.code}</code><span>{issue.message}</span>{!!issue.rule_ids.length && <small>{issue.rule_ids.join(', ')}</small>}</div>)}</div>}
      <div className="dlc-causal-timeline">{report.nodes.map((node, index) => <div className="dlc-causal-node" key={node.event_id}><span>Day {Number(node.day).toFixed(2)}</span><strong>{node.event_type}</strong><code>{node.event_id}</code><small>severity {node.severity} / depth {node.causal_depth}</small>{node.world_command_count > 0 && <small>{node.world_effect_status || 'planned'} / {node.world_command_count} world command{node.world_command_count === 1 ? '' : 's'}</small>}{!!node.world_commands?.length && <div className="dlc-causal-command-list">{node.world_commands.map((command, commandIndex) => <code key={`${node.event_id}-command-${commandIndex}`}>{command.command_type} ? {command.entity_id}{command.resource_name ? ` ? ${command.resource_name}` : ''}{command.quantity_delta != null ? ` ? ${Number(command.quantity_delta).toFixed(4)}` : ''}{command.quantity != null ? ` ? ${Number(command.quantity).toFixed(4)}` : ''}{command.anchored_base_value != null ? ` ? base ${Number(command.anchored_base_value).toFixed(2)}` : ''}{command.end_day != null ? ` ? ends day ${Number(command.end_day).toFixed(2)}` : ''}</code>)}</div>}{node.world_effect_error_code && <small className="dlc-editor-error">{node.world_effect_error_code}</small>}{index < report.nodes.length - 1 && <i>-&gt;</i>}</div>)}</div>
      {report.summary.truncated && <div className="dlc-editor-error">Preview reached the event limit while due events remained.</div>}
    </>}
  </section>
}

export function StructuredContentEditor({ kind, content, selected, onSelect, onSaveContent, onAnalyzeCausal, busy = false }) {
  const items = content[kind] || []
  const entities = content.entities || []
  const agents = content.agents || []
  const resources = content.resources || []
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

  const set = (name, value) => setForm(current => ({ ...current, [name]: value }))
  const runCausalPreview = async () => {
    try {
      const rule = serializeListForm('causal_event_rules', form)
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
    } catch (caught) {
      setError(caught.message)
    } finally {
      setPreviewBusy(false)
    }
  }
  const save = () => {
    try {
      const data = serializeListForm(kind, form)
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

      {kind === 'causal_event_rules' && <>
        <div className="dlc-row"><label>{T.ruleId}<input value={form.rule_id || ''} onChange={event => set('rule_id', event.target.value)} placeholder="drought_to_famine" /></label><label>Priority<input type="number" step="1" value={form.priority ?? 0} onChange={event => set('priority', event.target.value)} /></label></div>
        <div className="dlc-row"><label>Source event types<input value={form._source_types_text || ''} onChange={event => set('_source_types_text', event.target.value)} placeholder="drought, crop_failure" /></label><label>{T.childEventType}<input value={form.child_event_type || ''} onChange={event => set('child_event_type', event.target.value)} placeholder="famine_risk" /></label></div>
        <label className="dlc-checkbox"><input type="checkbox" checked={form.enabled !== false} onChange={event => set('enabled', event.target.checked)} />{T.enabled}</label>
        <div className="dlc-row"><label>Delay days<input type="number" min="0" step="any" value={form.delay_days ?? 0} onChange={event => set('delay_days', event.target.value)} /></label><label>{T.probability}<input type="number" min="0" max="1" step="0.01" value={form.probability ?? 1} onChange={event => set('probability', event.target.value)} /></label></div>
        <div className="dlc-row"><label>Severity delta<input type="number" min="-9" max="9" step="1" value={form.severity_delta ?? 0} onChange={event => set('severity_delta', event.target.value)} /></label><label>Max chain depth<input type="number" min="1" max="16" step="1" value={form.max_depth ?? 4} onChange={event => set('max_depth', event.target.value)} /></label></div>
        <label>Max fires per chain<input type="number" min="1" max="64" step="1" value={form.max_fires_per_chain ?? 1} onChange={event => set('max_fires_per_chain', event.target.value)} /></label>
        <AgentTargets value={form.target_agents || []} agents={agents} onChange={value => set('target_agents', value)} />
        <div className="dlc-editor-callout">No target selected means inherit the source event targets. Anchored inventory conditions compare quantity * anchor_rate in base-value units.</div>
        <label>Inherit payload keys<input value={form._inherit_keys_text || ''} onChange={event => set('_inherit_keys_text', event.target.value)} placeholder="region, crop" /></label>
        <CausalConditionBuilder value={form.conditions || []} entities={entities} resources={resources} onChange={value => set('conditions', value)} />
        <CausalWorldEffectBuilder value={form.world_effects || []} entities={entities} resources={resources} onChange={value => set('world_effects', value)} />
        <label>{T.initialPayload}<textarea className="dlc-json-field" rows="7" spellCheck="false" value={form._payload_text || ''} onChange={event => set('_payload_text', event.target.value)} /></label>
        <CausalDryRunPanel
          report={previewReport}
          busy={previewBusy}
          sourceType={previewSourceType}
          severity={previewSeverity}
          horizonDays={previewHorizon}
          eventTypes={eventTypes}
          onSourceType={setPreviewSourceType}
          onSeverity={setPreviewSeverity}
          onHorizonDays={setPreviewHorizon}
          onRun={runCausalPreview}
        />
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
