const ENTITY_TYPES = new Set(['location', 'organization', 'institution', 'region', 'character', 'faction', 'object', 'person', 'group', 'creature', 'system', 'item', 'other'])
const ACTOR_TYPES = new Set(['person', 'organization', 'institution', 'group', 'creature', 'system', 'other'])

function cloneJson(value) {
  return JSON.parse(JSON.stringify(value ?? {}))
}

function splitLine(line) {
  const delimiter = line.includes('\t') ? '\t' : '|'
  return line.split(delimiter).map(item => item.trim())
}

function hasControlCharacters(value) {
  return Array.from(String(value || '')).some(character => character.charCodeAt(0) < 32)
}

function sourceLines(text) {
  return String(text || '').split(/\r?\n/u)
    .map((raw, index) => ({ raw: raw.trim(), line: index + 1 }))
    .filter(item => item.raw && !item.raw.startsWith('#'))
}

function pushDuplicateErrors(rows, key, label, errors) {
  const seen = new Map()
  for (const row of rows) {
    const value = row[key]
    if (seen.has(value)) errors.push({ line: row.line, message: `${label}“${value}”与第 ${seen.get(value)} 行重复。` })
    else seen.set(value, row.line)
  }
}

export function parseQuickEntities(text) {
  const errors = []
  const rows = sourceLines(text).map(item => {
    const [id = '', name = '', type = 'location', status = '', description = ''] = splitLine(item.raw)
    if (!id) errors.push({ line: item.line, message: '实体 ID 不能为空。' })
    if (/[/\\\s]/u.test(id) || hasControlCharacters(id)) errors.push({ line: item.line, message: '实体 ID 不能包含空格、斜杠或控制字符。' })
    const normalizedType = type || 'location'
    if (!ENTITY_TYPES.has(normalizedType)) errors.push({ line: item.line, message: `实体类型“${normalizedType}”不在推荐类型列表中。` })
    return {
      line: item.line,
      id,
      name: name || id,
      type: normalizedType,
      current_status: status,
      description,
    }
  })
  pushDuplicateErrors(rows.filter(row => row.id), 'id', '实体 ID', errors)
  return { rows, errors }
}

export function parseQuickAgents(text) {
  const errors = []
  const rows = sourceLines(text).map(item => {
    const [id = '', name = '', actorId = '', entityId = '', role = 'agent', description = '', actorType = 'person'] = splitLine(item.raw)
    if (!id) errors.push({ line: item.line, message: 'Agent ID 不能为空。' })
    if (/[/\\\s]/u.test(id) || hasControlCharacters(id)) errors.push({ line: item.line, message: 'Agent ID 不能包含空格、斜杠或控制字符。' })
    const resolvedActorId = actorId || (id ? `${id}_actor` : '')
    if (/[/\\\s]/u.test(resolvedActorId) || hasControlCharacters(resolvedActorId)) errors.push({ line: item.line, message: 'Actor ID 不能包含空格、斜杠或控制字符。' })
    if (/[/\\\s]/u.test(role || '') || hasControlCharacters(role)) errors.push({ line: item.line, message: '角色标识不能包含空格、斜杠或控制字符。' })
    const resolvedActorType = actorType || 'person'
    if (!ACTOR_TYPES.has(resolvedActorType)) errors.push({ line: item.line, message: `Actor 类型“${resolvedActorType}”无效。` })
    return {
      line: item.line,
      id,
      name: name || id,
      actor_id: resolvedActorId,
      entity_id: entityId,
      role: role || 'agent',
      description,
      actor_type: resolvedActorType,
    }
  })
  pushDuplicateErrors(rows.filter(row => row.id), 'id', 'Agent ID', errors)
  pushDuplicateErrors(rows.filter(row => row.actor_id), 'actor_id', 'Actor ID', errors)
  return { rows, errors }
}

function replaceOrAppend(items, key, value, policy) {
  const index = items.findIndex(item => String(item?.[key] || '') === String(value?.[key] || ''))
  if (index < 0) {
    items.push(value)
    return 'added'
  }
  if (policy === 'replace') {
    items[index] = value
    return 'replaced'
  }
  return 'skipped'
}

function actorFromRow(row) {
  return {
    actor_id: row.actor_id,
    display_name: row.name,
    description: row.description,
    actor_type: row.actor_type,
    entity_id: row.entity_id,
    enabled: true,
    attributes: { rank: 1 },
    traits: [],
    needs: [],
    goals: [],
    capability_ids: [],
    identities: [{ identity_id: row.role, display_name: row.role, kind: 'role', public: true, description: '' }],
    memberships: [],
    states: {},
    metadata: { created_by: 'dlc_studio_quick_create' },
  }
}

function agentFromRow(row) {
  return {
    id: row.id,
    actor_id: row.actor_id,
    name: row.name,
    rank: 1,
    location_id: row.entity_id,
    role: row.role,
    agent_type: row.actor_type === 'institution' ? 'institution' : 'official',
    superior_id: '',
    personality_seed: '',
    enabled: true,
    extra_prompt: row.description,
  }
}

export function buildQuickCreateDraft(content, options = {}) {
  const entityResult = parseQuickEntities(options.entitiesText)
  const agentResult = parseQuickAgents(options.agentsText)
  const errors = [...entityResult.errors, ...agentResult.errors]
  const warnings = []
  const policy = options.conflictPolicy === 'replace' ? 'replace' : 'skip'
  const nextContent = cloneJson(content)
  nextContent.entities = Array.isArray(nextContent.entities) ? nextContent.entities : []
  nextContent.actors = Array.isArray(nextContent.actors) ? nextContent.actors : []
  nextContent.agents = Array.isArray(nextContent.agents) ? nextContent.agents : []

  const entityIds = new Set(nextContent.entities.map(item => String(item?.id || '')))
  for (const row of entityResult.rows) entityIds.add(row.id)
  for (const row of agentResult.rows) {
    if (row.entity_id && !entityIds.has(row.entity_id)) {
      errors.push({ line: row.line, message: `Agent“${row.id}”引用的所在实体“${row.entity_id}”不存在；请先在实体区加入它，或留空。` })
    }
  }

  const counts = {
    entities: { added: 0, replaced: 0, skipped: 0 },
    actors: { added: 0, replaced: 0, skipped: 0 },
    agents: { added: 0, replaced: 0, skipped: 0 },
  }
  if (errors.length) return { valid: false, errors, warnings, counts, nextContent: cloneJson(content), entityRows: entityResult.rows, agentRows: agentResult.rows }

  for (const row of entityResult.rows) {
    const outcome = replaceOrAppend(nextContent.entities, 'id', {
      id: row.id,
      type: row.type,
      name: row.name,
      description: row.description,
      current_status: row.current_status,
      metadata: { created_by: 'dlc_studio_quick_create' },
    }, policy)
    counts.entities[outcome] += 1
    if (outcome === 'skipped') warnings.push(`实体“${row.id}”已存在，按“跳过冲突”策略保留原值。`)
  }

  const existingActorIds = new Set(nextContent.actors.map(item => String(item?.actor_id || item?.id || '')))
  const existingAgentIds = new Set(nextContent.agents.map(item => String(item?.id || '')))
  for (const row of agentResult.rows) {
    const actorExists = existingActorIds.has(row.actor_id)
    const agentExists = existingAgentIds.has(row.id)
    if (policy === 'skip' && (actorExists || agentExists)) {
      counts.actors.skipped += 1
      counts.agents.skipped += 1
      warnings.push(`Agent 套件“${row.id} / ${row.actor_id}”与已有 Agent 或 Actor 冲突，整套跳过，避免产生半套引用。`)
      continue
    }
    const actorOutcome = replaceOrAppend(nextContent.actors, 'actor_id', actorFromRow(row), policy)
    const agentOutcome = replaceOrAppend(nextContent.agents, 'id', agentFromRow(row), policy)
    counts.actors[actorOutcome] += 1
    counts.agents[agentOutcome] += 1
    existingActorIds.add(row.actor_id)
    existingAgentIds.add(row.id)
  }

  const changed = Object.values(counts).some(section => section.added > 0 || section.replaced > 0)
  return { valid: true, changed, errors, warnings, counts, nextContent, entityRows: entityResult.rows, agentRows: agentResult.rows }
}

export function summarizeQuickCreate(result) {
  const counts = result?.counts || {}
  const part = (label, value = {}) => `${label} +${value.added || 0} / 覆盖 ${value.replaced || 0} / 跳过 ${value.skipped || 0}`
  return [part('实体', counts.entities), part('Actor', counts.actors), part('Agent', counts.agents)].join('；')
}