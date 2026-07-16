function cloneJson(value) {
  return JSON.parse(JSON.stringify(value ?? {}))
}

export function normalizeGameplayPlayer(content) {
  const player = content?.gameplay?.player
  if (!player || typeof player !== 'object' || Array.isArray(player)) {
    return { identity_id: '', display_name: '', actor_id: '', controlled_objects: [] }
  }
  return {
    identity_id: String(player.identity_id || ''),
    display_name: String(player.display_name || ''),
    actor_id: String(player.actor_id || ''),
    controlled_objects: Array.isArray(player.controlled_objects)
      ? player.controlled_objects.filter(item => item && typeof item === 'object' && !Array.isArray(item)).map(item => ({
        kind: item.kind === 'agent' ? 'agent' : 'entity',
        id: String(item.id || ''),
        mode: item.mode === 'command' ? 'command' : 'direct',
        display_name: String(item.display_name || ''),
      }))
      : [],
  }
}

export function validateGameplayPlayer(content, player) {
  const errors = []
  const entityIds = new Set((content?.entities || []).map(item => String(item?.id || '')))
  const agentIds = new Set((content?.agents || []).map(item => String(item?.id || '')))
  const actorIds = new Set((content?.actors || []).map(item => String(item?.actor_id || item?.id || '')))
  const identityId = String(player?.identity_id || '').trim()
  const displayName = String(player?.display_name || '').trim()
  const actorId = String(player?.actor_id || '').trim()
  const controls = Array.isArray(player?.controlled_objects) ? player.controlled_objects : []

  if (!identityId) errors.push('玩家身份 ID 不能为空。')
  if (!displayName) errors.push('玩家显示名称不能为空。')
  if (!controls.length) errors.push('至少添加一个玩家控制对象。')
  if (actorId && !actorIds.has(actorId)) errors.push(`玩家 Actor“${actorId}”不存在；请先创建对应 Actor，或暂时留空。`)

  const seen = new Set()
  controls.forEach((control, index) => {
    const label = `控制对象 ${index + 1}`
    const kind = control?.kind === 'agent' ? 'agent' : 'entity'
    const id = String(control?.id || '').trim()
    if (!id) errors.push(`${label}尚未选择实体或 Agent。`)
    else if (kind === 'entity' && !entityIds.has(id)) errors.push(`${label}引用的实体“${id}”不存在。`)
    else if (kind === 'agent' && !agentIds.has(id)) errors.push(`${label}引用的 Agent“${id}”不存在。`)
    const key = `${kind}:${id}`
    if (id && seen.has(key)) errors.push(`${label}与前面的 ${key} 重复。`)
    seen.add(key)
  })
  return errors
}

export function buildGameplayDraft(content, player) {
  const errors = validateGameplayPlayer(content, player)
  if (errors.length) return { valid: false, errors, nextContent: cloneJson(content) }
  const nextContent = cloneJson(content)
  nextContent.gameplay = {
    ...(nextContent.gameplay && typeof nextContent.gameplay === 'object' && !Array.isArray(nextContent.gameplay) ? nextContent.gameplay : {}),
    player: {
      identity_id: String(player.identity_id).trim(),
      display_name: String(player.display_name).trim(),
      actor_id: String(player.actor_id || '').trim(),
      controlled_objects: player.controlled_objects.map(control => ({
        kind: control.kind === 'agent' ? 'agent' : 'entity',
        id: String(control.id || '').trim(),
        mode: control.mode === 'command' ? 'command' : 'direct',
        display_name: String(control.display_name || '').trim(),
      })),
    },
  }
  return { valid: true, errors: [], nextContent }
}

export function suggestPlayerFromContent(content) {
  const agents = Array.isArray(content?.agents) ? content.agents : []
  const actors = Array.isArray(content?.actors) ? content.actors : []
  const entities = Array.isArray(content?.entities) ? content.entities : []
  const agent = agents.find(item => item?.role === 'player' && item?.enabled !== false) || agents.find(item => item?.enabled !== false) || agents[0]
  if (!agent) return normalizeGameplayPlayer(content)
  const agentId = String(agent.id || '')
  const actorId = String(agent.actor_id || agentId)
  const actor = actors.find(item => String(item?.actor_id || item?.id || '') === actorId)
  const locationId = String(agent.location_id || actor?.entity_id || '')
  const controls = []
  if (locationId && entities.some(item => String(item?.id || '') === locationId)) controls.push({ kind: 'entity', id: locationId, mode: 'direct', display_name: '' })
  controls.push({ kind: 'agent', id: agentId, mode: 'direct', display_name: String(agent.name || actor?.display_name || agentId) })
  return {
    identity_id: String(actor?.identities?.[0]?.identity_id || agent.role || agentId),
    display_name: String(agent.name || actor?.display_name || agentId),
    actor_id: actorId,
    controlled_objects: controls,
  }
}