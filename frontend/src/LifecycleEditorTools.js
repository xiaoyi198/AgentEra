import { normalizeCausalConditionForEditor, serializeCausalConditions } from './CausalConditionTools.js'

export const LIFECYCLE_KINDS = [
  { key: 'birth', label: '出生 / 新角色加入' },
  { key: 'death', label: '死亡 / 永久退出' },
  { key: 'promotion', label: '晋升 / 任命' },
  { key: 'succession', label: '继任 / 权力交接' },
  { key: 'vacancy', label: '职位空缺' },
  { key: 'organization_split', label: '组织分裂' },
  { key: 'organization_merge', label: '组织合并' },
  { key: 'entity_transform', label: '实体转化' },
  { key: 'custom', label: '自定义生命周期' },
]

export const LIFECYCLE_TRIGGERS = [
  { key: 'condition', label: '持续条件成立' },
  { key: 'manual', label: '仅手动触发' },
  { key: 'day_start', label: '每日开始' },
  { key: 'week_start', label: '每周开始' },
  { key: 'month_start', label: '每月开始' },
  { key: 'year_start', label: '每年开始' },
  { key: 'season_start', label: '季节开始' },
  { key: 'season_end', label: '季节结束' },
  { key: 'cycle_start', label: '周期开始' },
  { key: 'cycle_end', label: '周期结束' },
]

export const LIFECYCLE_OPERATION_TYPES = [
  { key: 'spawn_actor', label: '生成动态 Actor', help: '根据 Actor 模板创建运行时角色；可同时创建实体与动态 Agent。' },
  { key: 'retire_actor', label: '退休 / 移除 Actor', help: '停用动态 Actor、对应动态 Agent，并自动腾出其所有职位。' },
  { key: 'appoint_position', label: '任命职位', help: '任命指定 Actor；留空时按职位候选人和评分规则自动选择。' },
  { key: 'vacate_position', label: '腾空职位', help: '移除指定任职者；Actor 留空时清空该职位的全部任职者。' },
  { key: 'transform_entity', label: '转化实体', help: '原子修改实体的类型、名称、说明、状态或 Metadata。' },
  { key: 'split_organization', label: '分裂组织', help: '创建多个目标组织并把指定 Actor 的成员关系迁移过去。' },
  { key: 'merge_organizations', label: '合并组织', help: '把多个来源组织的成员迁入一个目标组织。' },
]

export const LIFECYCLE_SCORE_SOURCES = [
  { key: 'attribute', label: 'Actor 属性', help: '读取 attributes 中指定字段。' },
  { key: 'state', label: 'Actor 状态', help: '读取 states 中指定字段。' },
  { key: 'membership_rank', label: '组织成员等级', help: '读取候选人在本职位组织中的 rank。' },
  { key: 'actor_id', label: 'Actor ID', help: '按 ID 稳定排序，常用作平分后的确定性兜底。' },
]

const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/
const REFERENCE_ID = /^[^\s/\\]+$/
const KINDS = new Set(LIFECYCLE_KINDS.map(item => item.key))
const TRIGGERS = new Set(LIFECYCLE_TRIGGERS.map(item => item.key))
const OPERATIONS = new Set(LIFECYCLE_OPERATION_TYPES.map(item => item.key))
const SCORE_SOURCES = new Set(LIFECYCLE_SCORE_SOURCES.map(item => item.key))

function clone(value) {
  return JSON.parse(JSON.stringify(value))
}

function object(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {}
}

function list(value) {
  return Array.isArray(value) ? value : []
}

function text(value, fallback = '') {
  return typeof value === 'string' ? value : value == null ? fallback : String(value)
}

function pretty(value, fallback) {
  return JSON.stringify(value === undefined ? fallback : value, null, 2)
}

function withoutEditorFields(value) {
  return Object.fromEntries(Object.entries(object(value)).filter(([key]) => !key.startsWith('_')))
}

function requiredText(value, label, limit = 1000) {
  const result = text(value).trim()
  if (!result) throw new Error(`${label}不能为空。`)
  if (result.length > limit) throw new Error(`${label}不能超过 ${limit} 个字符。`)
  return result
}

function identifier(value, label, { required = true } = {}) {
  const result = text(value).trim()
  if (!result && !required) return ''
  if (!SAFE_ID.test(result)) throw new Error(`${label}必须以字母或数字开头，并且只能包含字母、数字、点、下划线和连字符。`)
  return result
}

function reference(value, label, { required = true, allowPattern = false } = {}) {
  const result = text(value).trim()
  if (!result && !required) return ''
  if (allowPattern && result.includes('{') && result.includes('}')) return result
  if (result.length > 128 || !REFERENCE_ID.test(result) || [...result].some(char => char.charCodeAt(0) < 32)) throw new Error(`${label}不能为空，且不能包含空白、斜杠或控制字符。`)
  return result
}

function finite(value, label, minimum, maximum, { optional = false } = {}) {
  if (optional && (value === '' || value === null || value === undefined)) return null
  const result = Number(value)
  if (!Number.isFinite(result) || result < minimum || result > maximum) throw new Error(`${label}必须是 ${minimum}～${maximum} 之间的有限数字。`)
  return result
}

function integer(value, label, minimum, maximum) {
  const result = Number(value)
  if (!Number.isInteger(result) || result < minimum || result > maximum) throw new Error(`${label}必须是 ${minimum}～${maximum} 之间的整数。`)
  return result
}

function parseJson(value, label, fallback, expected = 'object') {
  let parsed
  try {
    parsed = JSON.parse(text(value, JSON.stringify(fallback)) || JSON.stringify(fallback))
  } catch {
    throw new Error(`${label}必须是合法 JSON。`)
  }
  if (expected === 'object' && (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))) throw new Error(`${label}必须是 JSON 对象。`)
  if (expected === 'array' && !Array.isArray(parsed)) throw new Error(`${label}必须是 JSON 数组。`)
  return parsed
}

function parseIds(value) {
  const source = Array.isArray(value) ? value : text(value).split(/[,，\n]/)
  return [...new Set(source.map(item => text(item).trim()).filter(Boolean))]
}

function unique(values, label) {
  if (new Set(values).size !== values.length) throw new Error(`${label}不能重复。`)
}

function normalizeActorTemplate(source, index) {
  const raw = object(source)
  const actor = object(raw.actor)
  const entity = object(raw.entity)
  const agent = object(raw.agent)
  return {
    ...clone(raw),
    template_id: text(raw.template_id, `actor_template_${index + 1}`),
    display_name: text(raw.display_name, `动态角色模板 ${index + 1}`),
    actor: clone(actor),
    entity: clone(entity),
    agent: clone(agent),
    metadata: clone(object(raw.metadata)),
    _actor_type: text(raw._actor_type ?? actor.actor_type, 'person'),
    _actor_display_name: text(raw._actor_display_name ?? actor.display_name, raw.display_name),
    _actor_attributes_text: text(raw._actor_attributes_text, pretty(object(actor.attributes), {})),
    _actor_states_text: text(raw._actor_states_text, pretty(object(actor.states), {})),
    _actor_memberships_text: text(raw._actor_memberships_text, pretty(list(actor.memberships), [])),
    _actor_traits_text: text(raw._actor_traits_text, pretty(list(actor.traits), [])),
    _actor_metadata_text: text(raw._actor_metadata_text, pretty(object(actor.metadata), {})),
    _entity_enabled: raw._entity_enabled ?? Object.keys(entity).length > 0,
    _entity_type: text(raw._entity_type ?? entity.entity_type ?? entity.type, 'person'),
    _entity_name: text(raw._entity_name ?? entity.name, ''),
    _entity_description: text(raw._entity_description ?? entity.description, ''),
    _entity_status: text(raw._entity_status ?? entity.current_status ?? entity.status, 'active'),
    _entity_location_id: text(raw._entity_location_id ?? entity.location_id, ''),
    _entity_metadata_text: text(raw._entity_metadata_text, pretty(object(entity.metadata), {})),
    _agent_enabled: raw._agent_enabled ?? Object.keys(agent).length > 0,
    _agent_source_id: text(raw._agent_source_id ?? agent.source_agent_id, ''),
    _agent_display_name: text(raw._agent_display_name ?? agent.display_name, ''),
    _agent_system_prompt: text(raw._agent_system_prompt ?? agent.system_prompt, ''),
    _agent_active: raw._agent_active ?? agent.enabled !== false,
    _agent_extra_text: text(raw._agent_extra_text, pretty(Object.fromEntries(Object.entries(agent).filter(([key]) => !['source_agent_id', 'display_name', 'system_prompt', 'enabled'].includes(key))), {})),
    _metadata_text: text(raw._metadata_text, pretty(object(raw.metadata), {})),
  }
}

function normalizePosition(source, index) {
  const raw = object(source)
  return {
    ...clone(raw),
    position_id: text(raw.position_id, `position_${index + 1}`),
    display_name: text(raw.display_name, `职位 ${index + 1}`),
    organization_id: text(raw.organization_id, ''),
    role: text(raw.role, 'member'),
    rank: raw.rank ?? '',
    capacity: raw.capacity ?? 1,
    _incumbents_text: text(raw._incumbents_text, parseIds(raw.incumbent_actor_ids).join(', ')),
    _candidates_text: text(raw._candidates_text, parseIds(raw.candidate_actor_ids).join(', ')),
    scores: list(raw.scores).map(item => ({ source: SCORE_SOURCES.has(item?.source) ? item.source : 'attribute', name: text(item?.name), weight: item?.weight ?? 1, direction: item?.direction === 'asc' ? 'asc' : 'desc' })),
    _metadata_text: text(raw._metadata_text, pretty(object(raw.metadata), {})),
  }
}

function normalizeOperation(source, index) {
  const raw = object(source)
  const operation = OPERATIONS.has(raw.operation) ? raw.operation : 'spawn_actor'
  return {
    ...clone(raw),
    operation,
    _key: text(raw._key, `${operation}_${index + 1}`),
    _actor_patch_text: text(raw._actor_patch_text, pretty(object(raw.actor_patch), {})),
    _entity_patch_text: text(raw._entity_patch_text, pretty(object(raw.entity_patch), {})),
    _agent_patch_text: text(raw._agent_patch_text, pretty(object(raw.agent_patch), {})),
    _metadata_patch_text: text(raw._metadata_patch_text, pretty(object(raw.metadata_patch), {})),
    _destinations_text: text(raw._destinations_text, pretty(list(raw.destinations), [])),
    _source_organizations_text: text(raw._source_organizations_text, parseIds(raw.source_organization_ids).join(', ')),
    _actor_ids_text: text(raw._actor_ids_text, parseIds(raw.actor_ids).join(', ')),
    _all_actors: raw._all_actors ?? raw.actor_ids === undefined,
    _target_text: text(raw._target_text, pretty(object(raw.target), {})),
  }
}

function normalizeTransition(source, index) {
  const raw = object(source)
  return {
    ...clone(raw),
    transition_id: text(raw.transition_id, `transition_${index + 1}`),
    display_name: text(raw.display_name, `生命周期规则 ${index + 1}`),
    kind: KINDS.has(raw.kind) ? raw.kind : 'custom',
    trigger: TRIGGERS.has(raw.trigger) ? raw.trigger : 'condition',
    cycle_id: text(raw.cycle_id),
    season_id: text(raw.season_id),
    month_id: text(raw.month_id),
    day: raw.day ?? '',
    mode: raw.mode === 'any' ? 'any' : 'all',
    conditions: list(raw.conditions).map(normalizeCausalConditionForEditor),
    once: Boolean(raw.once),
    cooldown_days: raw.cooldown_days ?? 0,
    edge_triggered: raw.edge_triggered !== false,
    enabled: raw.enabled !== false,
    operations: list(raw.operations).map(normalizeOperation),
    _feedback_enabled: raw._feedback_enabled ?? Object.keys(object(raw.feedback)).length > 0,
    _feedback_message: text(raw._feedback_message ?? raw.feedback?.message ?? raw.feedback?.success),
    _feedback_level: text(raw._feedback_level ?? raw.feedback?.level, 'info'),
    _feedback_metadata_text: text(raw._feedback_metadata_text, pretty(Object.fromEntries(Object.entries(object(raw.feedback)).filter(([key]) => !['message', 'success', 'level'].includes(key))), {})),
  }
}

export function createDefaultLifecycle() {
  return { enabled: false, recent_feedback_limit: 50, actor_templates: [], positions: [], transitions: [] }
}

export function createLifecycleItem(kind, index = 0, catalogs = {}) {
  const number = index + 1
  if (kind === 'actor_template') return normalizeActorTemplate({ template_id: `actor_template_${number}`, display_name: `动态角色模板 ${number}`, actor: { display_name: `动态角色 ${number}`, actor_type: 'person', attributes: {}, states: {}, memberships: [], traits: [], metadata: {} }, entity: {}, agent: {}, metadata: {} }, index)
  if (kind === 'position') return normalizePosition({ position_id: `position_${number}`, display_name: `职位 ${number}`, organization_id: catalogs.organizations?.[0] || '', role: 'member', capacity: 1, incumbent_actor_ids: [], candidate_actor_ids: [], scores: [], metadata: {} }, index)
  if (kind === 'score') return { source: 'attribute', name: 'leadership', weight: 1, direction: 'desc' }
  if (kind === 'operation') return normalizeOperation({ operation: 'spawn_actor', template_id: catalogs.templates?.[0] || '', actor_id_pattern: '{template_id}_{sequence}' }, index)
  if (kind === 'transition') return normalizeTransition({ transition_id: `transition_${number}`, display_name: `生命周期规则 ${number}`, kind: 'custom', trigger: 'condition', mode: 'all', conditions: [], once: false, cooldown_days: 0, edge_triggered: true, enabled: true, operations: [createLifecycleItem('operation', 0, catalogs)], feedback: {} }, index)
  throw new Error(`未知的生命周期编辑项：${kind}`)
}

export function normalizeLifecycleForEditor(value) {
  const source = object(value)
  return {
    enabled: source.enabled === undefined ? Boolean(list(source.actor_templates).length || list(source.positions).length || list(source.transitions).length) : Boolean(source.enabled),
    recent_feedback_limit: source.recent_feedback_limit ?? 50,
    actor_templates: list(source.actor_templates).map(normalizeActorTemplate),
    positions: list(source.positions).map(normalizePosition),
    transitions: list(source.transitions).map(normalizeTransition),
  }
}

function serializeActorTemplate(source, index, content) {
  const label = `第 ${index + 1} 个 Actor 模板`
  const templateId = identifier(source.template_id, `${label} ID`)
  const actor = {
    ...withoutEditorFields(source.actor),
    display_name: requiredText(source._actor_display_name || source.display_name, `${label} Actor 名称`, 128),
    actor_type: identifier(source._actor_type || 'person', `${label} Actor 类型`),
    attributes: parseJson(source._actor_attributes_text, `${label}属性`, {}, 'object'),
    states: parseJson(source._actor_states_text, `${label}状态`, {}, 'object'),
    memberships: parseJson(source._actor_memberships_text, `${label}成员关系`, [], 'array'),
    traits: parseJson(source._actor_traits_text, `${label}人格特质`, [], 'array'),
    metadata: parseJson(source._actor_metadata_text, `${label} Actor Metadata`, {}, 'object'),
  }
  delete actor.actor_id
  let entity = {}
  if (source._entity_enabled) {
    entity = {
      ...withoutEditorFields(source.entity),
      entity_type: identifier(source._entity_type || 'person', `${label}实体类型`),
      name: text(source._entity_name).trim(),
      description: text(source._entity_description),
      current_status: requiredText(source._entity_status || 'active', `${label}实体状态`, 128),
      metadata: parseJson(source._entity_metadata_text, `${label}实体 Metadata`, {}, 'object'),
    }
    const locationId = reference(source._entity_location_id, `${label}地点`, { required: false })
    if (locationId) entity.location_id = locationId
    else delete entity.location_id
  }
  let agent = {}
  if (source._agent_enabled) {
    const sourceAgentId = reference(source._agent_source_id, `${label}来源 Agent`)
    const knownAgents = new Set(list(content?.agents).map(item => text(item?.id ?? item?.agent_id).trim()).filter(Boolean))
    if (knownAgents.size && !knownAgents.has(sourceAgentId)) throw new Error(`${label}引用了不存在的来源 Agent：${sourceAgentId}。`)
    agent = {
      ...parseJson(source._agent_extra_text, `${label}动态 Agent 扩展配置`, {}, 'object'),
      source_agent_id: sourceAgentId,
      display_name: text(source._agent_display_name).trim(),
      system_prompt: text(source._agent_system_prompt),
      enabled: Boolean(source._agent_active),
    }
    if (!agent.display_name) delete agent.display_name
    if (!agent.system_prompt) delete agent.system_prompt
  }
  return { template_id: templateId, display_name: requiredText(source.display_name || templateId, `${label}中文名称`, 128), actor, entity, agent, metadata: parseJson(source._metadata_text, `${label}模板 Metadata`, {}, 'object') }
}

function serializePosition(source, index) {
  const label = `第 ${index + 1} 个职位`
  const incumbents = parseIds(source._incumbents_text).map((item, itemIndex) => reference(item, `${label}第 ${itemIndex + 1} 个初始任职者`))
  const candidates = parseIds(source._candidates_text).map((item, itemIndex) => reference(item, `${label}第 ${itemIndex + 1} 个候选 Actor`))
  unique(incumbents, `${label}初始任职者`)
  unique(candidates, `${label}候选 Actor`)
  const capacity = integer(source.capacity ?? 1, `${label}容量`, 1, 128)
  if (incumbents.length > capacity) throw new Error(`${label}的初始任职者数量不能超过职位容量。`)
  const scores = list(source.scores).map((score, scoreIndex) => {
    const scoreLabel = `${label}第 ${scoreIndex + 1} 个评分字段`
    const scoreSource = identifier(score?.source || 'attribute', `${scoreLabel}来源`)
    if (!SCORE_SOURCES.has(scoreSource)) throw new Error(`${scoreLabel}来源无效。`)
    return { source: scoreSource, name: identifier(score?.name, `${scoreLabel}字段名`, { required: scoreSource === 'attribute' }), weight: finite(score?.weight ?? 1, `${scoreLabel}权重`, -1000, 1000), direction: score?.direction === 'asc' ? 'asc' : 'desc' }
  })
  return {
    position_id: identifier(source.position_id, `${label} ID`),
    display_name: requiredText(source.display_name, `${label}中文名称`, 128),
    organization_id: reference(source.organization_id, `${label}所属组织`),
    role: identifier(source.role || 'member', `${label}角色`),
    rank: finite(source.rank, `${label}等级`, -1e9, 1e9, { optional: true }),
    capacity,
    incumbent_actor_ids: incumbents,
    candidate_actor_ids: candidates,
    scores,
    metadata: parseJson(source._metadata_text, `${label} Metadata`, {}, 'object'),
  }
}

function serializeOperation(source, index, catalogs) {
  const label = `第 ${index + 1} 个操作`
  const operation = identifier(source.operation, `${label}类型`)
  if (!OPERATIONS.has(operation)) throw new Error(`${label}类型无效。`)
  if (operation === 'spawn_actor') {
    const templateId = identifier(source.template_id, `${label}模板 ID`)
    if (catalogs.templateIds.size && !catalogs.templateIds.has(templateId)) throw new Error(`${label}引用了不存在的 Actor 模板：${templateId}。`)
    const result = { operation, template_id: templateId, actor_id_pattern: requiredText(source.actor_id_pattern || '{template_id}_{sequence}', `${label} Actor ID 模式`, 256), actor_patch: parseJson(source._actor_patch_text, `${label} Actor Patch`, {}, 'object'), entity_patch: parseJson(source._entity_patch_text, `${label}实体 Patch`, {}, 'object'), agent_patch: parseJson(source._agent_patch_text, `${label} Agent Patch`, {}, 'object') }
    for (const key of ['display_name', 'entity_id_pattern', 'agent_id_pattern']) if (text(source[key]).trim()) result[key] = text(source[key]).trim()
    return result
  }
  if (operation === 'retire_actor') {
    const actorId = reference(source.actor_id, `${label} Actor`, { required: false, allowPattern: true })
    const positionId = identifier(source.position_id, `${label}职位`, { required: false })
    if (!actorId && !positionId) throw new Error(`${label}必须填写 Actor ID 或职位 ID。`)
    if (positionId && catalogs.positionIds.size && !catalogs.positionIds.has(positionId)) throw new Error(`${label}引用了不存在的职位：${positionId}。`)
    const result = { operation, reason: text(source.reason).trim() || 'lifecycle transition', remove_entity: Boolean(source.remove_entity) }
    if (actorId) result.actor_id = actorId
    if (positionId) result.position_id = positionId
    if (text(source.entity_id).trim()) result.entity_id = reference(source.entity_id, `${label}实体`, { allowPattern: true })
    return result
  }
  if (operation === 'appoint_position' || operation === 'vacate_position') {
    const positionId = identifier(source.position_id, `${label}职位 ID`)
    if (catalogs.positionIds.size && !catalogs.positionIds.has(positionId)) throw new Error(`${label}引用了不存在的职位：${positionId}。`)
    const result = { operation, position_id: positionId }
    const actorId = reference(source.actor_id, `${label} Actor`, { required: false, allowPattern: true })
    if (actorId) result.actor_id = actorId
    if (operation === 'appoint_position') {
      result.replace = Boolean(source.replace)
      if (text(source.role).trim()) result.role = identifier(source.role, `${label}任命角色`)
      const rank = finite(source.rank, `${label}任命等级`, -1e9, 1e9, { optional: true })
      if (rank !== null) result.rank = rank
      result.primary = Boolean(source.primary)
      if (text(source.description).trim()) result.description = text(source.description).trim()
    }
    return result
  }
  if (operation === 'transform_entity') {
    const result = { operation, entity_id: reference(source.entity_id, `${label}实体 ID`, { allowPattern: true }), metadata_patch: parseJson(source._metadata_patch_text, `${label} Metadata Patch`, {}, 'object') }
    for (const key of ['new_type', 'new_name', 'new_description', 'new_status']) if (text(source[key]).trim()) result[key] = text(source[key]).trim()
    return result
  }
  if (operation === 'split_organization') {
    const destinations = parseJson(source._destinations_text, `${label}目标组织`, [], 'array')
    if (!destinations.length) throw new Error(`${label}至少需要一个目标组织。`)
    return { operation, source_organization_id: reference(source.source_organization_id, `${label}来源组织`), destinations, remove_source: Boolean(source.remove_source) }
  }
  const sources = parseIds(source._source_organizations_text).map((item, itemIndex) => reference(item, `${label}第 ${itemIndex + 1} 个来源组织`))
  if (!sources.length) throw new Error(`${label}至少需要一个来源组织。`)
  const result = { operation, source_organization_ids: sources, target_organization_id: reference(source.target_organization_id, `${label}目标组织`), target: parseJson(source._target_text, `${label}目标组织定义`, {}, 'object'), role: identifier(source.role || 'member', `${label}成员角色`), remove_sources: Boolean(source.remove_sources) }
  const rank = finite(source.rank, `${label}成员等级`, -1e9, 1e9, { optional: true })
  if (rank !== null) result.rank = rank
  if (!source._all_actors) result.actor_ids = parseIds(source._actor_ids_text).map((item, itemIndex) => reference(item, `${label}第 ${itemIndex + 1} 个 Actor`))
  return result
}

function serializeTransition(source, index, catalogs) {
  const label = `第 ${index + 1} 条生命周期规则`
  const kind = identifier(source.kind || 'custom', `${label}种类`)
  const trigger = identifier(source.trigger || 'condition', `${label}触发方式`)
  if (!KINDS.has(kind)) throw new Error(`${label}种类无效。`)
  if (!TRIGGERS.has(trigger)) throw new Error(`${label}触发方式无效。`)
  const cycleId = identifier(source.cycle_id, `${label}周期`, { required: false })
  const seasonId = identifier(source.season_id, `${label}季节`, { required: false })
  const monthId = identifier(source.month_id, `${label}月份`, { required: false })
  if (cycleId && catalogs.cycleIds.size && !catalogs.cycleIds.has(cycleId)) throw new Error(`${label}引用了不存在的周期：${cycleId}。`)
  if (seasonId && catalogs.seasonIds.size && !catalogs.seasonIds.has(seasonId)) throw new Error(`${label}引用了不存在的季节：${seasonId}。`)
  if (monthId && catalogs.monthIds.size && !catalogs.monthIds.has(monthId)) throw new Error(`${label}引用了不存在的月份：${monthId}。`)
  const operations = list(source.operations).map((item, operationIndex) => serializeOperation(item, operationIndex, catalogs))
  if (!operations.length) throw new Error(`${label}至少需要一个操作。`)
  let feedback = {}
  if (source._feedback_enabled) {
    feedback = { ...parseJson(source._feedback_metadata_text, `${label}反馈扩展数据`, {}, 'object'), message: requiredText(source._feedback_message, `${label}玩家反馈`, 4000), level: identifier(source._feedback_level || 'info', `${label}反馈级别`) }
  }
  return {
    transition_id: identifier(source.transition_id, `${label} ID`),
    display_name: requiredText(source.display_name, `${label}中文名称`, 128),
    kind,
    trigger,
    cycle_id: cycleId,
    season_id: seasonId,
    month_id: monthId,
    day: source.day === '' || source.day === null || source.day === undefined ? null : integer(source.day, `${label}日期`, 1, 100000),
    mode: source.mode === 'any' ? 'any' : 'all',
    conditions: serializeCausalConditions(source.conditions, { requireOne: trigger === 'condition' }),
    once: Boolean(source.once),
    cooldown_days: finite(source.cooldown_days ?? 0, `${label}冷却天数`, 0, 365000),
    edge_triggered: Boolean(source.edge_triggered),
    enabled: Boolean(source.enabled),
    operations,
    feedback,
  }
}

export function serializeLifecycle(value, content = {}) {
  const source = normalizeLifecycleForEditor(value)
  const actorTemplates = source.actor_templates.map((item, index) => serializeActorTemplate(item, index, content))
  const positions = source.positions.map(serializePosition)
  unique(actorTemplates.map(item => item.template_id), 'Actor 模板 ID')
  unique(positions.map(item => item.position_id), '职位 ID')
  const timeSystem = object(content.time_system)
  const catalogs = {
    templateIds: new Set(actorTemplates.map(item => item.template_id)),
    positionIds: new Set(positions.map(item => item.position_id)),
    cycleIds: new Set(list(timeSystem.cycles).map(item => text(item?.cycle_id).trim()).filter(Boolean)),
    seasonIds: new Set(list(timeSystem.seasons).map(item => text(item?.season_id).trim()).filter(Boolean)),
    monthIds: new Set(list(timeSystem.calendar?.months).map(item => text(item?.month_id).trim()).filter(Boolean)),
  }
  const transitions = source.transitions.map((item, index) => serializeTransition(item, index, catalogs))
  unique(transitions.map(item => item.transition_id), '生命周期规则 ID')
  if (actorTemplates.length > 256) throw new Error('Actor 模板最多 256 个。')
  if (positions.length > 512) throw new Error('职位最多 512 个。')
  if (transitions.length > 1024) throw new Error('生命周期规则最多 1024 条。')
  return { enabled: Boolean(source.enabled), actor_templates: actorTemplates, positions, transitions, recent_feedback_limit: integer(source.recent_feedback_limit ?? 50, '最近反馈保留数量', 1, 500) }
}

export function summarizeLifecycle(value) {
  const source = normalizeLifecycleForEditor(value)
  const operations = source.transitions.reduce((sum, item) => sum + item.operations.length, 0)
  const dynamicAgents = source.actor_templates.filter(item => item._agent_enabled).length
  return { enabled: source.enabled, actor_templates: source.actor_templates.length, dynamic_agents: dynamicAgents, positions: source.positions.length, transitions: source.transitions.length, operations }
}
