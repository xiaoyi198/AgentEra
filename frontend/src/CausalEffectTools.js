export const WORLD_EFFECT_TYPES = [
  { key: 'adjust_resource', label: '调整资源数量', group: '资源与状态', description: '在现有库存上增加或扣减数量；负数表示消耗。' },
  { key: 'set_resource', label: '设定资源数量', group: '资源与状态', description: '把某项库存直接设为指定数量。' },
  { key: 'update_status', label: '更新实体状态', group: '资源与状态', description: '替换实体的 current_status，用于阶段性状态切换。' },
  { key: 'create_ongoing_event', label: '创建持续事件', group: '资源与状态', description: '创建会在指定天数后结算的持续事件。' },
  { key: 'transfer_anchored_value', label: '转移锚定价值', group: '资源与状态', description: '按 anchor_rate 在两种资产之间原子转移同等基础价值。' },
  { key: 'create_entity', label: '创建实体', group: '实体生命周期', description: '创建新实体，并可同时写入初始库存与 Metadata。' },
  { key: 'move_entity', label: '移动实体', group: '实体生命周期', description: '更新坐标、区域或地点 ID；至少填写一种位置。' },
  { key: 'transform_entity', label: '转化实体', group: '实体生命周期', description: '局部修改类型、名称、描述、状态或 Metadata。' },
  { key: 'remove_entity', label: '移除实体', group: '实体生命周期', description: '从权威世界状态移除实体及其相关运行数据。' },
  { key: 'enable_agent', label: '启用 Agent', group: 'Agent 与 Actor', description: '允许指定 Agent 接收并执行后续任务。' },
  { key: 'disable_agent', label: '停用 Agent', group: 'Agent 与 Actor', description: '阻止指定 Agent 接收和执行后续任务。' },
  { key: 'modify_relationship', label: '修改实体关系', group: '关系与职位', description: '新增、更新或删除一条有类型的实体关系。' },
  { key: 'modify_membership', label: '修改组织成员身份', group: '关系与职位', description: '新增、更新或删除 Actor 的组织成员身份。' },
  { key: 'create_issue', label: '创建 Issue', group: 'Issue 与知识', description: '按已启用的 Case/事务类型创建结构化 Issue。' },
  { key: 'declare_world_truth', label: '声明世界真相', group: 'Issue 与知识', description: '写入权威世界事实；该隐藏层绝不能直接注入 Agent 提示词。' },
  { key: 'set_actor_belief', label: '设置 Actor 认知', group: 'Issue 与知识', description: '写入指定 Actor / Agent holder 的主观认知，可与真相不同、失真或过期。' },
  { key: 'reveal_information', label: '向玩家揭示信息', group: 'Issue 与知识', description: '向指定玩家 holder 写入可见知识，不会自动暴露其他 Actor 认知。' },
  { key: 'propagate_knowledge', label: '传播知识', group: 'Issue 与知识', description: '在三层知识之间传播记录，并保留来源、置信度、失真与保密元数据。' },
  { key: 'start_investigation', label: '启动调查', group: 'Issue 与知识', description: '为玩家或 Actor 启动核验流程，可在完成后按结果策略投影知识。' },
  { key: 'advance_investigation', label: '推进调查', group: 'Issue 与知识', description: '增量推进或直接设定调查进度，并可追加证据和更新状态。' },
  { key: 'advance_objective', label: '推进目标', group: '目标、行动和阶段', description: '增量调整或直接设定 Objective 进度，并可更新状态。' },
  { key: 'unlock_action', label: '解锁行动', group: '目标、行动和阶段', description: '全局或针对实体、Actor、Agent 的作用域解锁行动。' },
  { key: 'switch_phase', label: '切换世界阶段', group: '目标、行动和阶段', description: '切换玩法阶段，并保存阶段 Metadata。' },
]

const VALID_TYPES = new Set(WORLD_EFFECT_TYPES.map(item => item.key))
const ASCII_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/
const UNICODE_ID = /^[^/\\]{1,128}$/u
const KNOWLEDGE_LAYERS = new Set(['world_truth', 'actor_belief', 'player_knowledge'])
const HOLDER_LAYERS = new Set(['actor_belief', 'player_knowledge'])
const SECRECY_LEVELS = new Set(['public', 'restricted', 'confidential', 'secret', 'top_secret'])
const KNOWLEDGE_STATUSES = new Set(['active', 'disputed', 'retracted'])
const INVESTIGATION_STATUSES = new Set(['active', 'paused', 'completed', 'cancelled'])

function firstId(items, keys) {
  for (const item of items || []) {
    for (const key of keys) {
      const value = String(item?.[key] || '').trim()
      if (value) return value
    }
  }
  return ''
}

function objectText(value) {
  return JSON.stringify(value && typeof value === 'object' && !Array.isArray(value) ? value : {}, null, 2)
}

function valueText(value, fallback = true) {
  return JSON.stringify(value === undefined ? fallback : value, null, 2)
}

function parseJson(text, label, fallback) {
  try {
    return JSON.parse(String(text ?? '').trim() || fallback)
  } catch (error) {
    throw new Error(`${label}不是有效 JSON：${error.message}`)
  }
}

function parseObject(text, label) {
  const value = parseJson(text, label, '{}')
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${label}必须是 JSON 对象。`)
  return value
}

function parseArray(text, label) {
  const value = parseJson(text, label, '[]')
  if (!Array.isArray(value)) throw new Error(`${label}必须是 JSON 数组。`)
  return value
}

function parseValue(text, label) {
  return parseJson(text, label, 'true')
}

function requiredText(value, label, limit = 128) {
  const text = String(value ?? '').trim()
  if (!text) throw new Error(`${label}不能为空。`)
  if (text.length > limit) throw new Error(`${label}不能超过 ${limit} 个字符。`)
  return text
}

function optionalText(value, label, limit) {
  const text = String(value ?? '').trim()
  if (!text) return ''
  if (text.length > limit) throw new Error(`${label}不能超过 ${limit} 个字符。`)
  return text
}

function identifier(value, label, required = true) {
  const text = String(value ?? '').trim()
  if (!text && !required) return ''
  if (!text) throw new Error(`${label}不能为空。`)
  if (!ASCII_ID.test(text) && !UNICODE_ID.test(text)) throw new Error(`${label}不是安全 ID；不得包含控制字符、斜杠或反斜杠，最长 128 字符。`)
  return text
}

function finiteNumber(value, label, { min = -Infinity, max = Infinity, strictMin = false } = {}) {
  if (value === '' || value === null || value === undefined || typeof value === 'boolean') throw new Error(`${label}必须是有限数字。`)
  const number = Number(value)
  if (!Number.isFinite(number)) throw new Error(`${label}必须是有限数字。`)
  if ((strictMin ? number <= min : number < min) || number > max) throw new Error(`${label}必须在${strictMin ? `大于 ${min}` : `${min} 到 ${max}`}的范围内。`)
  return number
}

function optionalNumber(value, label, options) {
  if (value === '' || value === null || value === undefined) return undefined
  return finiteNumber(value, label, options)
}

function addReason(result, effect) {
  const reason = optionalText(effect.reason, '原因', 512)
  if (reason) result.reason = reason
  return result
}

function knowledgeRecordDefaults(holderId) {
  return {
    entity_id: '', knowledge_id: '', holder_id: holderId, value: true,
    source: 'causal_event', confidence: 1, stale_after_days: '', expires_in_days: '',
    secrecy_level: 'public', visibility_scopes: [], distortion: {}, status: 'active', metadata: {},
    _value_text: 'true', _visibility_scopes_text: '[]', _distortion_text: '{}', _metadata_text: '{}', reason: '',
  }
}

export function defaultWorldEffect(effectType = 'adjust_resource', catalogs = {}) {
  const type = VALID_TYPES.has(effectType) ? effectType : 'adjust_resource'
  const entityId = firstId(catalogs.entities, ['id', 'entity_id'])
  const resourceName = firstId(catalogs.resources, ['name', 'resource_name'])
  const assetName = firstId((catalogs.resources || []).filter(item => item.kind === 'asset'), ['name', 'resource_name'])
  const agentId = firstId(catalogs.agents, ['id', 'agent_id'])
  const actorId = firstId(catalogs.actors, ['actor_id', 'id'])
  const relationshipType = firstId(catalogs.relationshipTypes, ['relationship_type_id', 'id'])
  const issueType = firstId(catalogs.issueTypes, ['issue_type_id', 'id'])
  const objectiveId = firstId(catalogs.objectives, ['objective_id', 'id'])
  const actionId = firstId(catalogs.actions, ['action_id', 'id'])

  const defaults = {
    adjust_resource: { entity_id: entityId, resource_name: resourceName, quantity_delta: 0, reason: '' },
    set_resource: { entity_id: entityId, resource_name: resourceName, quantity: 0, reason: '' },
    update_status: { entity_id: entityId, new_status: '', reason: '' },
    create_ongoing_event: { entity_id: entityId, event_type: 'state_change', description: '', duration_days: 1, expected_result: {}, _expected_result_text: '{}', reason: '' },
    transfer_anchored_value: { from_entity_id: entityId, from_resource: assetName, to_entity_id: entityId, to_resource: assetName, base_value: 1, reason: '' },
    create_entity: { entity_id: '', entity_type: 'location', name: '', description: '', current_status: 'active', metadata: {}, resources: {}, _metadata_text: '{}', _resources_text: '{}', reason: '' },
    move_entity: { entity_id: entityId, x: '', y: '', region: '', location_id: '', reason: '' },
    transform_entity: { entity_id: entityId, new_type: '', new_name: '', new_description: '', new_status: '', metadata_patch: {}, _metadata_patch_text: '{}', reason: '' },
    remove_entity: { entity_id: entityId, reason: '' },
    enable_agent: { agent_id: agentId, actor_id: '', metadata: {}, _metadata_text: '{}', reason: '' },
    disable_agent: { agent_id: agentId, actor_id: '', metadata: {}, _metadata_text: '{}', reason: '' },
    modify_relationship: { operation: 'upsert', parent_id: entityId, child_id: entityId, rel_type: relationshipType, strength: 100, visibility: 'public', starts_in_days: 0, duration_days: '', values: {}, note: '', metadata: {}, _values_text: '{}', _metadata_text: '{}', reason: '' },
    modify_membership: { operation: 'upsert', actor_id: actorId, organization_id: entityId, role: 'member', status: 'active', metadata: {}, _metadata_text: '{}', reason: '' },
    create_issue: { issue_type_id: issueType, source: { kind: 'system', id: 'causal_event' }, title: '', summary: '', details: {}, evidence: [], urgency: 'normal', deadline_days: '', metadata: {}, _source_text: '{\n  "kind": "system",\n  "id": "causal_event"\n}', _details_text: '{}', _evidence_text: '[]', _metadata_text: '{}', reason: '' },
    declare_world_truth: knowledgeRecordDefaults(''),
    set_actor_belief: knowledgeRecordDefaults(actorId),
    reveal_information: knowledgeRecordDefaults('player'),
    propagate_knowledge: { source_layer: 'world_truth', target_layer: 'actor_belief', entity_id: '', knowledge_id: '', source_holder_id: '', target_holder_id: actorId, confidence_multiplier: 1, source: 'knowledge_propagation', value_override: true, secrecy_level: '', distortion: {}, metadata: {}, _use_value_override: false, _value_override_text: 'true', _distortion_text: '{}', _metadata_text: '{}', reason: '' },
    start_investigation: { investigation_id: '', entity_id: '', knowledge_id: '', investigator_layer: 'player_knowledge', investigator_id: 'player', required_progress: 1, evidence: [], result_policy: {}, metadata: {}, _evidence_text: '[]', _use_result_policy: false, _result_source_layer: 'world_truth', _result_target_layer: 'player_knowledge', _result_source_holder_id: '', _result_target_holder_id: 'player', _result_confidence_multiplier: 1, _result_source: '', _result_secrecy_level: '', _result_distortion_text: '{}', _result_policy_text: '{}', _metadata_text: '{}', reason: '' },
    advance_investigation: { investigation_id: '', mode: 'adjust', amount: 1, evidence: [], status: '', _evidence_text: '[]', reason: '' },
    advance_objective: { objective_id: objectiveId, mode: 'adjust', amount: 1, status: '', reason: '' },
    unlock_action: { action_id: actionId, scope_id: '', reason: '' },
    switch_phase: { phase_id: '', metadata: {}, _metadata_text: '{}', reason: '' },
  }
  return { effect_type: type, ...defaults[type] }
}

export function normalizeWorldEffectForEditor(value = {}) {
  const type = VALID_TYPES.has(value.effect_type) ? value.effect_type : 'adjust_resource'
  const result = { ...value, effect_type: type }
  if (type === 'create_ongoing_event') result._expected_result_text = objectText(value.expected_result)
  if (type === 'create_entity') {
    result._metadata_text = objectText(value.metadata)
    result._resources_text = objectText(value.resources)
  }
  if (type === 'transform_entity') result._metadata_patch_text = objectText(value.metadata_patch)
  if (['enable_agent', 'disable_agent', 'modify_membership', 'switch_phase'].includes(type)) result._metadata_text = objectText(value.metadata)
  if (type === 'modify_relationship') {
    result._values_text = objectText(value.values)
    result._metadata_text = objectText(value.metadata)
  }
  if (type === 'create_issue') {
    result._source_text = objectText(value.source)
    result._details_text = objectText(value.details)
    result._evidence_text = JSON.stringify(Array.isArray(value.evidence) ? value.evidence : [], null, 2)
    result._metadata_text = objectText(value.metadata)
  }
  if (['declare_world_truth', 'set_actor_belief', 'reveal_information'].includes(type)) {
    result._value_text = valueText(value.value)
    result._visibility_scopes_text = JSON.stringify(Array.isArray(value.visibility_scopes) ? value.visibility_scopes : [], null, 2)
    result._distortion_text = objectText(value.distortion)
    result._metadata_text = objectText(value.metadata)
  }
  if (type === 'propagate_knowledge') {
    result._use_value_override = Object.prototype.hasOwnProperty.call(value, 'value_override')
    result._value_override_text = valueText(value.value_override)
    result._distortion_text = objectText(value.distortion)
    result._metadata_text = objectText(value.metadata)
  }
  if (type === 'start_investigation') {
    const policy = value.result_policy && typeof value.result_policy === 'object' && !Array.isArray(value.result_policy) ? value.result_policy : {}
    result._evidence_text = JSON.stringify(Array.isArray(value.evidence) ? value.evidence : [], null, 2)
    result._use_result_policy = Object.keys(policy).length > 0
    result._result_source_layer = String(policy.source_layer || 'world_truth')
    result._result_target_layer = String(policy.target_layer || value.investigator_layer || 'player_knowledge')
    result._result_source_holder_id = String(policy.source_holder_id || '')
    result._result_target_holder_id = String(policy.target_holder_id || value.investigator_id || 'player')
    result._result_confidence_multiplier = policy.confidence_multiplier ?? 1
    result._result_source = String(policy.source || '')
    result._result_secrecy_level = String(policy.secrecy_level || '')
    result._result_distortion_text = objectText(policy.distortion)
    const policyExtras = { ...policy }
    for (const key of ['source_layer', 'target_layer', 'source_holder_id', 'target_holder_id', 'confidence_multiplier', 'source', 'secrecy_level', 'distortion']) delete policyExtras[key]
    result._result_policy_text = objectText(policyExtras)
    result._metadata_text = objectText(value.metadata)
  }
  if (type === 'advance_investigation') result._evidence_text = JSON.stringify(Array.isArray(value.evidence) ? value.evidence : [], null, 2)
  return result
}

export function normalizeWorldEffectsForEditor(values) {
  return (Array.isArray(values) ? values : []).map(normalizeWorldEffectForEditor)
}

function knowledgeLayer(value, label, { holderOnly = false } = {}) {
  const layer = String(value || '').trim()
  if (!KNOWLEDGE_LAYERS.has(layer) || (holderOnly && !HOLDER_LAYERS.has(layer))) throw new Error(`${label}无效。`)
  return layer
}

function secrecyLevel(value, { optional = false } = {}) {
  const secrecy = String(value ?? '').trim()
  if (!secrecy && optional) return ''
  if (!SECRECY_LEVELS.has(secrecy)) throw new Error('知识保密级别无效。')
  return secrecy
}

function knowledgeStatus(value) {
  const status = String(value || 'active').trim()
  if (!KNOWLEDGE_STATUSES.has(status)) throw new Error('知识状态无效。')
  return status
}

function serializeKnowledgeRecord(effect, type) {
  const visibilityScopes = parseArray(effect._visibility_scopes_text ?? JSON.stringify(effect.visibility_scopes || []), '可见作用域')
  for (const scope of visibilityScopes) identifier(scope, '可见作用域 ID')
  const result = {
    effect_type: type,
    entity_id: identifier(effect.entity_id, '关联实体 ID', false),
    knowledge_id: identifier(effect.knowledge_id, '知识 ID'),
    value: parseValue(effect._value_text ?? valueText(effect.value), '知识值'),
    source: requiredText(effect.source || 'causal_event', '知识来源', 256),
    confidence: finiteNumber(effect.confidence ?? 1, '置信度', { min: 0, max: 1 }),
    secrecy_level: secrecyLevel(effect.secrecy_level || 'public'),
    visibility_scopes: visibilityScopes,
    distortion: parseObject(effect._distortion_text ?? objectText(effect.distortion), '失真信息'),
    status: knowledgeStatus(effect.status),
    metadata: parseObject(effect._metadata_text ?? objectText(effect.metadata), '知识 Metadata'),
  }
  if (type === 'set_actor_belief') result.holder_id = identifier(effect.holder_id, 'Actor / Agent holder ID')
  if (type === 'reveal_information') result.holder_id = identifier(effect.holder_id || 'player', '玩家 holder ID')
  for (const [key, label] of [['stale_after_days', '变陈旧天数'], ['expires_in_days', '过期天数']]) {
    const duration = optionalNumber(effect[key], label, { min: 0, max: 36500 })
    if (duration !== undefined) result[key] = duration
  }
  return result
}

export function serializeWorldEffect(effect = {}) {
  const type = String(effect.effect_type || '').trim()
  if (!VALID_TYPES.has(type)) throw new Error('效果类型无效。')
  let result

  if (type === 'adjust_resource' || type === 'set_resource') {
    const key = type === 'adjust_resource' ? 'quantity_delta' : 'quantity'
    result = { effect_type: type, entity_id: identifier(effect.entity_id, '实体 ID'), resource_name: identifier(effect.resource_name, '资源名称'), [key]: finiteNumber(effect[key], type === 'adjust_resource' ? '数量增量' : '目标数量') }
  } else if (type === 'update_status') {
    result = { effect_type: type, entity_id: identifier(effect.entity_id, '实体 ID'), new_status: requiredText(effect.new_status, '新状态', 512) }
  } else if (type === 'create_ongoing_event') {
    result = { effect_type: type, entity_id: identifier(effect.entity_id, '实体 ID'), event_type: identifier(effect.event_type, '持续事件类型'), description: optionalText(effect.description, '事件描述', 1000), duration_days: finiteNumber(effect.duration_days, '持续天数', { min: 0, max: 3650 }), expected_result: parseObject(effect._expected_result_text ?? objectText(effect.expected_result), '成熟结果') }
  } else if (type === 'transfer_anchored_value') {
    result = { effect_type: type, from_entity_id: identifier(effect.from_entity_id, '来源实体 ID'), from_resource: identifier(effect.from_resource, '来源资产'), to_entity_id: identifier(effect.to_entity_id, '目标实体 ID'), to_resource: identifier(effect.to_resource, '目标资产'), base_value: finiteNumber(effect.base_value, '基础价值', { min: 0, strictMin: true }) }
  } else if (type === 'create_entity') {
    const resources = parseObject(effect._resources_text ?? objectText(effect.resources), '初始库存')
    for (const [name, amount] of Object.entries(resources)) {
      identifier(name, '初始库存资源名')
      finiteNumber(amount, `资源 ${name} 的数量`)
    }
    result = { effect_type: type, entity_id: identifier(effect.entity_id, '新实体 ID'), entity_type: identifier(effect.entity_type, '实体类型'), name: requiredText(effect.name, '实体名称', 512), description: optionalText(effect.description, '实体描述', 4000), current_status: optionalText(effect.current_status, '当前状态', 512), metadata: parseObject(effect._metadata_text ?? objectText(effect.metadata), '实体 Metadata'), resources }
  } else if (type === 'move_entity') {
    result = { effect_type: type, entity_id: identifier(effect.entity_id, '实体 ID') }
    const x = optionalNumber(effect.x, 'X 坐标')
    const y = optionalNumber(effect.y, 'Y 坐标')
    const region = optionalText(effect.region, '区域', 256)
    const locationId = identifier(effect.location_id, '地点 ID', false)
    if (x === undefined && y === undefined && !region && !locationId) throw new Error('移动实体至少要填写 X、Y、区域或地点 ID 中的一项。')
    if (x !== undefined) result.x = x
    if (y !== undefined) result.y = y
    if (region) result.region = region
    if (locationId) result.location_id = locationId
  } else if (type === 'transform_entity') {
    result = { effect_type: type, entity_id: identifier(effect.entity_id, '实体 ID') }
    const fields = [
      ['new_type', '新实体类型', 128], ['new_name', '新名称', 512], ['new_description', '新描述', 4000], ['new_status', '新状态', 512],
    ]
    for (const [key, label, limit] of fields) {
      const text = optionalText(effect[key], label, limit)
      if (text) result[key] = key === 'new_type' ? identifier(text, label) : text
    }
    const metadataText = String(effect._metadata_patch_text ?? '').trim()
    if (metadataText && metadataText !== '{}') result.metadata_patch = parseObject(metadataText, 'Metadata 补丁')
    if (Object.keys(result).length === 2) throw new Error('转化实体至少要填写一个变更字段。')
  } else if (type === 'remove_entity') {
    result = { effect_type: type, entity_id: identifier(effect.entity_id, '实体 ID') }
  } else if (type === 'enable_agent' || type === 'disable_agent') {
    result = { effect_type: type, agent_id: identifier(effect.agent_id, 'Agent ID'), actor_id: identifier(effect.actor_id, 'Actor ID', false), metadata: parseObject(effect._metadata_text ?? objectText(effect.metadata), 'Agent Metadata') }
  } else if (type === 'modify_relationship') {
    const operation = String(effect.operation || 'upsert').trim().toLowerCase()
    if (!['upsert', 'remove'].includes(operation)) throw new Error('关系操作只能是 upsert 或 remove。')
    result = { effect_type: type, operation, parent_id: identifier(effect.parent_id, '父实体 ID'), child_id: identifier(effect.child_id, '子实体 ID'), rel_type: identifier(effect.rel_type, '关系类型') }
    if (operation === 'upsert') {
      const strength = optionalNumber(effect.strength, '关系强度')
      const visibility = optionalText(effect.visibility, '可见性', 32)
      const starts = finiteNumber(effect.starts_in_days ?? 0, '延迟生效天数', { min: 0, max: 3650 })
      const duration = optionalNumber(effect.duration_days, '持续天数', { min: 0, max: 3650, strictMin: true })
      if (strength !== undefined) result.strength = strength
      if (visibility) result.visibility = visibility
      result.starts_in_days = starts
      if (duration !== undefined) result.duration_days = duration
      result.values = parseObject(effect._values_text ?? objectText(effect.values), '关系维度值')
      result.note = optionalText(effect.note, '关系备注', 2000)
      result.metadata = parseObject(effect._metadata_text ?? objectText(effect.metadata), '关系 Metadata')
    }
  } else if (type === 'modify_membership') {
    const operation = String(effect.operation || 'upsert').trim().toLowerCase()
    if (!['upsert', 'remove'].includes(operation)) throw new Error('成员身份操作只能是 upsert 或 remove。')
    result = { effect_type: type, operation, actor_id: identifier(effect.actor_id, 'Actor ID'), organization_id: identifier(effect.organization_id, '组织实体 ID') }
    if (operation === 'upsert') {
      result.role = requiredText(effect.role || 'member', '职位/角色', 256)
      result.status = requiredText(effect.status || 'active', '成员状态', 128)
      result.metadata = parseObject(effect._metadata_text ?? objectText(effect.metadata), '成员身份 Metadata')
    }
  } else if (type === 'create_issue') {
    result = { effect_type: type, issue_type_id: identifier(effect.issue_type_id, 'Issue 类型 ID'), source: parseObject(effect._source_text ?? objectText(effect.source), 'Issue 来源'), title: optionalText(effect.title, 'Issue 标题', 512), summary: optionalText(effect.summary, 'Issue 摘要', 12000), details: parseObject(effect._details_text ?? objectText(effect.details), 'Issue 详情'), evidence: parseArray(effect._evidence_text ?? JSON.stringify(effect.evidence || []), 'Issue 证据'), urgency: requiredText(effect.urgency || 'normal', '紧迫度', 32), metadata: parseObject(effect._metadata_text ?? objectText(effect.metadata), 'Issue Metadata') }
    const deadline = optionalNumber(effect.deadline_days, '处理期限', { min: 0, max: 3650 })
    if (deadline !== undefined) result.deadline_days = deadline
  } else if (['declare_world_truth', 'set_actor_belief', 'reveal_information'].includes(type)) {
    result = serializeKnowledgeRecord(effect, type)
  } else if (type === 'propagate_knowledge') {
    const sourceLayer = knowledgeLayer(effect.source_layer, '来源知识层')
    const targetLayer = knowledgeLayer(effect.target_layer, '目标知识层')
    result = {
      effect_type: type, source_layer: sourceLayer, target_layer: targetLayer,
      entity_id: identifier(effect.entity_id, '关联实体 ID', false), knowledge_id: identifier(effect.knowledge_id, '知识 ID'),
      source_holder_id: identifier(effect.source_holder_id, '来源 holder ID', sourceLayer !== 'world_truth'),
      target_holder_id: identifier(effect.target_holder_id, '目标 holder ID', targetLayer !== 'world_truth'),
      confidence_multiplier: finiteNumber(effect.confidence_multiplier ?? 1, '置信度倍率', { min: 0, max: 1 }),
      source: requiredText(effect.source || 'knowledge_propagation', '传播来源', 256),
      distortion: parseObject(effect._distortion_text ?? objectText(effect.distortion), '传播失真'),
      metadata: parseObject(effect._metadata_text ?? objectText(effect.metadata), '传播 Metadata'),
    }
    if (effect._use_value_override ?? Object.prototype.hasOwnProperty.call(effect, 'value_override')) result.value_override = parseValue(effect._value_override_text ?? valueText(effect.value_override), '覆盖知识值')
    const secrecy = secrecyLevel(effect.secrecy_level, { optional: true })
    if (secrecy) result.secrecy_level = secrecy
  } else if (type === 'start_investigation') {
    const investigatorLayer = knowledgeLayer(effect.investigator_layer || 'player_knowledge', '调查者知识层', { holderOnly: true })
    let resultPolicy = parseObject(effect._result_policy_text ?? objectText(effect.result_policy), '调查完成结果策略')
    const useResultPolicy = effect._use_result_policy ?? Boolean(effect.result_policy && typeof effect.result_policy === 'object' && Object.keys(effect.result_policy).length)
    if (useResultPolicy) {
      const sourceLayer = knowledgeLayer(effect._result_source_layer || 'world_truth', '结果来源层')
      const targetLayer = knowledgeLayer(effect._result_target_layer || investigatorLayer, '结果目标层', { holderOnly: true })
      resultPolicy = {
        ...resultPolicy,
        source_layer: sourceLayer,
        target_layer: targetLayer,
        source_holder_id: identifier(effect._result_source_holder_id, '来源 holder ID', sourceLayer !== 'world_truth'),
        target_holder_id: identifier(effect._result_target_holder_id || effect.investigator_id, '结果目标 holder ID'),
        confidence_multiplier: finiteNumber(effect._result_confidence_multiplier ?? 1, '结果置信度倍率', { min: 0, max: 1 }),
        distortion: parseObject(effect._result_distortion_text ?? '{}', '调查结果失真'),
      }
      const policySource = optionalText(effect._result_source, '结果来源', 256)
      if (policySource) resultPolicy.source = policySource
      const policySecrecy = secrecyLevel(effect._result_secrecy_level, { optional: true })
      if (policySecrecy) resultPolicy.secrecy_level = policySecrecy
    }
    result = {
      effect_type: type, investigation_id: identifier(effect.investigation_id, '调查 ID'),
      entity_id: identifier(effect.entity_id, '关联实体 ID', false), knowledge_id: identifier(effect.knowledge_id, '知识 ID'),
      investigator_layer: investigatorLayer, investigator_id: identifier(effect.investigator_id || (investigatorLayer === 'player_knowledge' ? 'player' : ''), '调查者 ID'),
      required_progress: finiteNumber(effect.required_progress ?? 1, '所需调查进度', { min: 0, max: 1000000, strictMin: true }),
      evidence: parseArray(effect._evidence_text ?? JSON.stringify(effect.evidence || []), '调查证据'),
      result_policy: resultPolicy,
      metadata: parseObject(effect._metadata_text ?? objectText(effect.metadata), '调查 Metadata'),
    }
  } else if (type === 'advance_investigation') {
    const mode = String(effect.mode || 'adjust').trim().toLowerCase()
    if (!['adjust', 'set'].includes(mode)) throw new Error('调查推进模式只能是 adjust 或 set。')
    const key = mode === 'adjust' ? 'amount' : 'progress'
    result = {
      effect_type: type, investigation_id: identifier(effect.investigation_id, '调查 ID'), mode,
      [key]: finiteNumber(effect[key], mode === 'adjust' ? '调查进度增量' : '调查目标进度', { min: 0, max: 1000000 }),
      evidence: parseArray(effect._evidence_text ?? JSON.stringify(effect.evidence || []), '调查证据'),
    }
    const status = String(effect.status || '').trim()
    if (status && !INVESTIGATION_STATUSES.has(status)) throw new Error('调查状态无效。')
    if (status) result.status = status
  } else if (type === 'advance_objective') {
    const mode = String(effect.mode || 'adjust').trim().toLowerCase()
    if (!['adjust', 'set'].includes(mode)) throw new Error('目标推进模式只能是 adjust 或 set。')
    const key = mode === 'adjust' ? 'amount' : 'progress'
    result = { effect_type: type, objective_id: identifier(effect.objective_id, 'Objective ID'), mode, [key]: finiteNumber(effect[key], mode === 'adjust' ? '进度增量' : '目标进度') }
    const status = optionalText(effect.status, 'Objective 状态', 64)
    if (status) result.status = status
  } else if (type === 'unlock_action') {
    result = { effect_type: type, action_id: identifier(effect.action_id, '行动 ID'), scope_id: identifier(effect.scope_id, '作用域 ID', false) }
  } else {
    result = { effect_type: type, phase_id: identifier(effect.phase_id, '阶段 ID'), metadata: parseObject(effect._metadata_text ?? objectText(effect.metadata), '阶段 Metadata') }
  }

  return addReason(result, effect)
}

export function serializeWorldEffects(values) {
  if (!Array.isArray(values) || values.length > 32) throw new Error('通用效果必须是数组，且最多只能配置 32 项。')
  return values.map((effect, index) => {
    try {
      return serializeWorldEffect(effect)
    } catch (error) {
      throw new Error(`第 ${index + 1} 个效果：${error.message}`)
    }
  })
}
