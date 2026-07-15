export const CAUSAL_CONDITION_KINDS = [
  { key: 'anchored_inventory', label: '锚定库存价值', help: '读取实体某项 asset 库存的统一锚定价值（数量 × anchor_rate），适合跨资源比较。' },
  { key: 'metric', label: '实体指标', help: '读取实体的 metric 数值，例如繁荣度、稳定度或风险指数。' },
  { key: 'parameter', label: '实体参数', help: '读取实体的 parameter 数值，例如税率、阈值或模拟常量。' },
  { key: 'entity_field', label: '实体字段 / Metadata', help: '读取实体直接字段；填写 metadata.xxx 可读取 Metadata 中的键。' },
  { key: 'entity_tag', label: '实体标签', help: '判断实体 Metadata.tags 中是否包含指定标签，实际值为布尔值。' },
  { key: 'entity_state', label: '实体当前状态', help: '读取实体 current_status，用于判断 mobilized、stable 等运行状态。' },
  { key: 'actor_attribute', label: 'Actor 数值属性', help: '读取 Actor Runtime 的 attributes 数值。' },
  { key: 'actor_state', label: 'Actor 运行状态', help: '读取 Actor Runtime 的 states 字段，可比较文本、布尔或数值。' },
  { key: 'actor_trait', label: 'Actor 人格特质', help: '判断 Actor 是否声明指定 trait_id，实际值为布尔值。' },
  { key: 'actor_membership', label: 'Actor 组织成员关系', help: '读取 Actor 对指定 organization_id 的成员关系；可继续选择 role、status 等字段。' },
  { key: 'relationship', label: '实体关系', help: '读取两个实体间指定关系契约，可判断存在、激活状态、强度或 values.xxx 维度。' },
  { key: 'ongoing_event', label: '持续事件', help: '统计当前持续事件，或读取是否存在及最短剩余天数。实体和事件类型均可留空以统计全部。' },
  { key: 'event_history', label: '历史事件', help: '读取有界、脱敏的历史事件；可按目标 Agent、事件类型和时间窗口筛选。' },
  { key: 'time', label: '世界模拟时间', help: '读取模拟天数 sim_day，或读取模拟秒数 sim_time。' },
  { key: 'objective_progress', label: '目标进度', help: '读取 Objective 的 progress、status 等公开状态字段。' },
  { key: 'aggregate_metric', label: '聚合统计', help: '对实体、库存、指标、Actor 属性、关系或持续事件执行 sum/avg/min/max/count。' },
  { key: 'world_truth', label: '世界真相（隐藏层）', help: '读取权威世界事实；仅供模拟规则求值，绝不能直接注入 Agent 提示词。' },
  { key: 'actor_belief', label: 'Actor 主观认知', help: '读取指定 Actor / Agent holder 的认知；可与真相不同，也可能失真或过期。' },
  { key: 'player_knowledge', label: '玩家已知信息', help: '读取指定玩家 holder 的公开投影；不会泄露世界真相或其他 Actor 认知。' },
  { key: 'knowledge_confidence', label: '知识置信度', help: '比较知识记录的 0～1 置信度；Actor 认知层必须指定 holder。' },
  { key: 'knowledge_freshness', label: '知识新鲜度', help: '判断知识记录为 fresh、stale 或 expired；已过期知识不能继续传播。' },
  { key: 'knowledge_secrecy', label: '知识保密级别', help: '比较 public、restricted、confidential、secret 或 top_secret。' },
  { key: 'investigation', label: '调查 / 核验', help: '按调查 ID 读取 status、progress 或 required_progress；可选限定调查者 holder。' },
  { key: 'phase', label: '世界阶段', help: '读取当前世界阶段的 phase_id，或通过 metadata.xxx 读取阶段元数据。' },
]

export const CAUSAL_CONDITION_OPERATORS = [
  { key: 'lt', label: '小于（<）' },
  { key: 'lte', label: '小于等于（≤）' },
  { key: 'eq', label: '等于（=）' },
  { key: 'ne', label: '不等于（≠）' },
  { key: 'gte', label: '大于等于（≥）' },
  { key: 'gt', label: '大于（>）' },
  { key: 'contains', label: '包含' },
  { key: 'not_contains', label: '不包含' },
  { key: 'in', label: '属于列表' },
  { key: 'not_in', label: '不属于列表' },
  { key: 'exists', label: '存在' },
  { key: 'not_exists', label: '不存在' },
]

export const CAUSAL_AGGREGATES = [
  { key: 'sum', label: '求和 sum' },
  { key: 'avg', label: '平均值 avg' },
  { key: 'min', label: '最小值 min' },
  { key: 'max', label: '最大值 max' },
  { key: 'count', label: '计数 count' },
]

export const CAUSAL_AGGREGATE_SOURCES = [
  { key: 'entity_count', label: '实体数量' },
  { key: 'anchored_inventory', label: '锚定库存价值' },
  { key: 'metric', label: '实体指标' },
  { key: 'parameter', label: '实体参数' },
  { key: 'actor_attribute', label: 'Actor 数值属性' },
  { key: 'relation', label: '关系数值' },
  { key: 'ongoing_event', label: '持续事件数量' },
]

export const CAUSAL_VALUE_TYPES = [
  { key: 'number', label: '数值' },
  { key: 'text', label: '文本' },
  { key: 'boolean', label: '布尔值' },
  { key: 'list', label: '列表（JSON）' },
]

const VALID_KINDS = new Set(CAUSAL_CONDITION_KINDS.map(item => item.key))
const VALID_OPERATORS = new Set(CAUSAL_CONDITION_OPERATORS.map(item => item.key))
const VALID_AGGREGATES = new Set(CAUSAL_AGGREGATES.map(item => item.key))
const VALID_AGGREGATE_SOURCES = new Set(CAUSAL_AGGREGATE_SOURCES.map(item => item.key))
const MAX_REFERENCE_LENGTH = 128
const ACTOR_COMPONENT_KINDS = new Set(['actor_attribute', 'actor_state', 'actor_trait', 'actor_membership'])
const KNOWLEDGE_LAYERS = new Set(['world_truth', 'actor_belief', 'player_knowledge'])
const KNOWLEDGE_META_KINDS = new Set(['knowledge_confidence', 'knowledge_freshness', 'knowledge_secrecy'])

function text(value, limit = MAX_REFERENCE_LENGTH) {
  const result = String(value ?? '').trim()
  if (result.length > limit) throw new Error(`条件引用不能超过 ${limit} 个字符。`)
  return result
}

function scalar(value, depth = 0) {
  if (depth > 8) throw new Error('条件值列表嵌套过深。')
  if (value === null || typeof value === 'boolean') return value
  if (typeof value === 'string') {
    if (value.length > 512) throw new Error('条件文本值不能超过 512 个字符。')
    return value
  }
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new Error('条件数值必须是有限数字。')
    return value
  }
  if (Array.isArray(value)) {
    if (value.length > 64) throw new Error('条件列表最多包含 64 项。')
    return value.map(item => scalar(item, depth + 1))
  }
  throw new Error('条件值只支持文本、布尔值、有限数字或这些值组成的列表。')
}

export function inferCausalValueType(value) {
  if (Array.isArray(value)) return 'list'
  if (typeof value === 'boolean') return 'boolean'
  if (typeof value === 'number') return 'number'
  return 'text'
}

function valueText(value, valueType) {
  if (valueType === 'list') return JSON.stringify(Array.isArray(value) ? value : [], null, 2)
  if (valueType === 'boolean') return value === false ? 'false' : 'true'
  if (value === null || value === undefined) return valueType === 'number' ? '0' : ''
  return String(value)
}

export function normalizeCausalConditionForEditor(source = {}) {
  const operator = VALID_OPERATORS.has(source.operator) ? source.operator : 'eq'
  const valueType = source._value_type || inferCausalValueType(source.value)
  const filters = source.filters && typeof source.filters === 'object' && !Array.isArray(source.filters) ? source.filters : {}
  const kind = VALID_KINDS.has(source.kind) ? source.kind : 'anchored_inventory'
  const result = {
    ...source,
    kind,
    entity_id: String(source.entity_id || ''),
    name: String(source.name || ''),
    operator,
    target_entity_id: String(source.target_entity_id || ''),
    field: String(source.field || ''),
    aggregate: String(source.aggregate || ''),
    window_days: source.window_days ?? '',
    _value_type: valueType,
    _value_text: source._value_text ?? valueText(source.value, valueType),
    _filters_text: source._filters_text ?? JSON.stringify(filters, null, 2),
  }
  if (KNOWLEDGE_META_KINDS.has(kind)) result._knowledge_layer = String(source._knowledge_layer || filters.layer || 'player_knowledge')
  return result
}

function firstByKind(resources, kind) {
  const expected = kind === 'anchored_inventory' ? 'asset' : kind
  return (resources || []).find(item => item.kind === expected)?.name || ''
}

function firstActorId(catalogs) {
  return catalogs.actors?.[0]?.actor_id || catalogs.actors?.[0]?.id || ''
}

export function defaultCausalCondition(kind = 'anchored_inventory', catalogs = {}) {
  const entityId = catalogs.entities?.[0]?.id || ''
  const actorId = firstActorId(catalogs)
  const relationshipType = catalogs.relationshipTypes?.[0]?.relationship_type_id || ''
  const objectiveId = catalogs.objectives?.[0]?.objective_id || ''
  const eventType = catalogs.eventTypes?.[0] || ''
  const ongoingType = catalogs.ongoingEvents?.[0]?.event_type || eventType
  let condition = { kind, operator: 'eq', value: '', entity_id: '', name: '', target_entity_id: '', field: '', aggregate: '', window_days: '', filters: {} }
  if (['anchored_inventory', 'metric', 'parameter'].includes(kind)) condition = { ...condition, entity_id: entityId, name: firstByKind(catalogs.resources, kind), operator: kind === 'anchored_inventory' ? 'lt' : 'gte', value: 0 }
  else if (kind === 'entity_field') condition = { ...condition, entity_id: entityId, field: 'name', value: '' }
  else if (kind === 'entity_tag') condition = { ...condition, entity_id: entityId, name: '', value: true }
  else if (kind === 'entity_state') condition = { ...condition, entity_id: entityId, value: '' }
  else if (kind === 'actor_attribute') condition = { ...condition, entity_id: actorId, name: '', operator: 'gte', value: 0 }
  else if (kind === 'actor_state') condition = { ...condition, entity_id: actorId, name: '', value: '' }
  else if (kind === 'actor_trait') condition = { ...condition, entity_id: actorId, name: '', value: true }
  else if (kind === 'actor_membership') condition = { ...condition, entity_id: actorId, name: '', field: '', value: true }
  else if (kind === 'relationship') condition = { ...condition, entity_id: entityId, target_entity_id: catalogs.entities?.[1]?.id || entityId, name: relationshipType, field: 'strength', operator: 'gte', value: 0 }
  else if (kind === 'ongoing_event') condition = { ...condition, name: ongoingType, field: 'exists', value: true }
  else if (kind === 'event_history') condition = { ...condition, name: eventType, field: 'count', operator: 'gte', value: 1, window_days: 30 }
  else if (kind === 'time') condition = { ...condition, name: 'sim_day', operator: 'gte', value: 0 }
  else if (kind === 'objective_progress') condition = { ...condition, entity_id: objectiveId, field: 'progress', operator: 'gte', value: 1 }
  else if (kind === 'aggregate_metric') condition = { ...condition, field: 'entity_count', aggregate: 'count', operator: 'gte', value: 1 }
  else if (kind === 'world_truth') condition = { ...condition, name: '', field: '', value: '' }
  else if (kind === 'actor_belief') condition = { ...condition, name: '', target_entity_id: actorId, field: '', value: '' }
  else if (kind === 'player_knowledge') condition = { ...condition, name: '', target_entity_id: '', field: '', value: '' }
  else if (kind === 'knowledge_confidence') condition = { ...condition, name: '', target_entity_id: '', operator: 'gte', value: 0.5, filters: { layer: 'player_knowledge' } }
  else if (kind === 'knowledge_freshness') condition = { ...condition, name: '', target_entity_id: '', operator: 'eq', value: 'fresh', filters: { layer: 'player_knowledge' } }
  else if (kind === 'knowledge_secrecy') condition = { ...condition, name: '', target_entity_id: '', operator: 'eq', value: 'public', filters: { layer: 'player_knowledge' } }
  else if (kind === 'investigation') condition = { ...condition, entity_id: '', target_entity_id: '', field: 'status', operator: 'eq', value: 'active' }
  else if (kind === 'phase') condition = { ...condition, field: 'phase_id', value: '' }
  return normalizeCausalConditionForEditor(condition)
}

function parseEditorValue(condition) {
  const valueType = condition._value_type || inferCausalValueType(condition.value)
  const raw = condition._value_text ?? valueText(condition.value, valueType)
  if (valueType === 'number') {
    const number = Number(raw)
    if (String(raw).trim() === '' || !Number.isFinite(number)) throw new Error('目标值必须是有限数字。')
    return number
  }
  if (valueType === 'boolean') {
    if (raw === true || String(raw).toLowerCase() === 'true') return true
    if (raw === false || String(raw).toLowerCase() === 'false') return false
    throw new Error('布尔目标值只能是 true 或 false。')
  }
  if (valueType === 'list') {
    let parsed
    try { parsed = JSON.parse(String(raw || '[]')) } catch (error) { throw new Error(`目标值列表 JSON 无效：${error.message}`) }
    if (!Array.isArray(parsed)) throw new Error('列表目标值必须是 JSON 数组。')
    return scalar(parsed)
  }
  return scalar(String(raw ?? ''))
}

function parseFilters(raw) {
  let parsed
  try { parsed = JSON.parse(String(raw || '{}')) } catch (error) { throw new Error(`过滤器 JSON 无效：${error.message}`) }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('过滤器必须是 JSON 对象。')
  const entries = Object.entries(parsed)
  if (entries.length > 16) throw new Error('过滤器最多包含 16 个字段。')
  return Object.fromEntries(entries.map(([key, value]) => [text(key, 64), scalar(value)]))
}

function requireReference(condition, field, label) {
  if (!condition[field]) throw new Error(`${label}不能为空。`)
}

export function serializeCausalCondition(source, index = null) {
  const condition = normalizeCausalConditionForEditor(source)
  const prefix = index === null ? '' : `第 ${index + 1} 个条件：`
  try {
    if (!VALID_KINDS.has(condition.kind)) throw new Error('条件来源无效。')
    if (!VALID_OPERATORS.has(condition.operator)) throw new Error('比较操作符无效。')
    const result = { kind: condition.kind, operator: condition.operator }
    for (const field of ['entity_id', 'name', 'target_entity_id', 'field']) {
      const value = text(condition[field])
      if (value) result[field] = value
    }
    if (!['exists', 'not_exists'].includes(condition.operator)) result.value = parseEditorValue(condition)
    if (condition.aggregate) {
      const aggregate = text(condition.aggregate, 16)
      if (!VALID_AGGREGATES.has(aggregate)) throw new Error('聚合方法无效。')
      result.aggregate = aggregate
    }
    if (condition.window_days !== '' && condition.window_days !== null && condition.window_days !== undefined) {
      const windowDays = Number(condition.window_days)
      if (!Number.isFinite(windowDays) || windowDays < 0 || windowDays > 36500) throw new Error('历史窗口必须在 0 到 36500 天之间。')
      result.window_days = windowDays
    }
    const filters = parseFilters(condition._filters_text)
    if (KNOWLEDGE_META_KINDS.has(result.kind)) {
      const layer = text(condition._knowledge_layer || filters.layer || 'player_knowledge', 32)
      if (!KNOWLEDGE_LAYERS.has(layer)) throw new Error('知识层无效。')
      filters.layer = layer
    }
    if (Object.keys(filters).length) result.filters = filters

    if (['anchored_inventory', 'metric', 'parameter'].includes(result.kind)) {
      requireReference(result, 'entity_id', '实体')
      requireReference(result, 'name', '资源 / 数值名称')
    }
    if (['entity_field', 'entity_tag', 'entity_state'].includes(result.kind)) requireReference(result, 'entity_id', '实体')
    if (result.kind === 'entity_field' && !result.field && !result.name) throw new Error('实体字段名不能为空。')
    if (result.kind === 'entity_tag') requireReference(result, 'name', '标签')
    if (ACTOR_COMPONENT_KINDS.has(result.kind)) {
      requireReference(result, 'entity_id', 'Actor')
      requireReference(result, 'name', 'Actor 组件名称')
    }
    if (['world_truth', 'actor_belief', 'player_knowledge'].includes(result.kind)) {
      requireReference(result, 'name', '知识 ID')
      if (result.kind === 'actor_belief') requireReference(result, 'target_entity_id', 'Actor / Agent holder')
    }
    if (KNOWLEDGE_META_KINDS.has(result.kind)) {
      requireReference(result, 'name', '知识 ID')
      const layer = result.filters?.layer || 'player_knowledge'
      if (layer === 'actor_belief') requireReference(result, 'target_entity_id', 'Actor / Agent holder')
    }
    if (result.kind === 'investigation') {
      if (!result.entity_id && !result.name) throw new Error('调查 ID 不能为空。')
      if (!result.field) result.field = 'status'
      if (!['status', 'progress', 'required_progress'].includes(result.field)) throw new Error('调查字段只能是 status、progress 或 required_progress。')
    }
    if (result.kind === 'relationship') {
      requireReference(result, 'entity_id', '来源实体')
      requireReference(result, 'target_entity_id', '目标实体')
      requireReference(result, 'name', '关系类型')
    }
    if (result.kind === 'objective_progress' && !result.entity_id && !result.name) throw new Error('目标 ID 不能为空。')
    if (result.kind === 'phase') {
      requireReference(result, 'field', '阶段字段')
      if (result.field !== 'phase_id' && !/^metadata\.[^.]+(?:\.[^.]+)*$/.test(result.field)) {
        throw new Error('阶段字段只能是 phase_id 或 metadata.xxx。')
      }
    }
    if (result.kind === 'aggregate_metric') {
      requireReference(result, 'field', '聚合来源')
      if (!VALID_AGGREGATE_SOURCES.has(result.field)) throw new Error('聚合来源无效。')
      if (!result.aggregate) result.aggregate = 'sum'
      if (['anchored_inventory', 'metric', 'parameter', 'actor_attribute'].includes(result.field)) requireReference(result, 'name', '聚合数值名称')
    }
    return result
  } catch (error) {
    throw new Error(prefix + error.message)
  }
}

export function serializeCausalConditions(conditions, { requireOne = false } = {}) {
  const list = Array.isArray(conditions) ? conditions : []
  if (requireOne && !list.length) throw new Error('至少添加一个完整条件。')
  if (list.length > 32) throw new Error('每条规则或条件组最多包含 32 个条件。')
  return list.map((condition, index) => serializeCausalCondition(condition, index))
}
