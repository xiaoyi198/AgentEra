export const EMPTY_CAPABILITY_STATE = Object.freeze({
  configured: false,
  generation: 0,
  fingerprint: '',
  audience: 'player',
  capabilities: [],
})

export function normalizeCapabilityResponse(value) {
  const document = value && typeof value === 'object' ? value : {}
  return {
    configured: Boolean(document.configured),
    generation: Number.isInteger(document.generation) ? document.generation : 0,
    fingerprint: typeof document.fingerprint === 'string' ? document.fingerprint : '',
    audience: typeof document.audience === 'string' ? document.audience : 'player',
    capabilities: Array.isArray(document.capabilities)
      ? document.capabilities.filter(item => item && typeof item === 'object' && item.enabled !== false)
      : [],
  }
}

export function capabilityFormDefaults(capability) {
  const parameters = {}
  for (const parameter of capability?.parameters || []) {
    if (parameter.default !== undefined && parameter.default !== null) parameters[parameter.name] = parameter.default
    else if (parameter.type === 'boolean') parameters[parameter.name] = false
    else parameters[parameter.name] = ''
  }
  return {
    content: '',
    targets: '',
    urgency: capability?.default_urgency || 'normal',
    parameters,
  }
}

function normalizedParameter(parameter, value) {
  if (parameter.type === 'boolean') return Boolean(value)
  if (value === '' || value === undefined || value === null) return undefined
  if (parameter.type === 'integer') return Number.parseInt(value, 10)
  if (parameter.type === 'number') return Number(value)
  return String(value)
}

export function buildCapabilityInvocation(capability, form, gameplay, generation) {
  const targets = String(form.targets || '')
    .replaceAll('\uFF0C', ',')
    .split(',')
    .map(item => item.trim())
    .filter(Boolean)
  const parameters = {}
  for (const parameter of capability?.parameters || []) {
    const value = normalizedParameter(parameter, form.parameters?.[parameter.name])
    if (value !== undefined) parameters[parameter.name] = value
  }
  const player = gameplay?.player || {}
  const controlled = Array.isArray(player.controlled_objects) ? player.controlled_objects : []
  return {
    invocation_id: `ui_${Date.now()}_${Math.random().toString(16).slice(2)}`,
    expected_generation: Number.isInteger(generation) ? generation : undefined,
    source_id: player.actor_id || player.identity_id || 'player',
    actor_id: player.actor_id || player.identity_id || 'player',
    control_object_id: controlled[0]?.id || '',
    content: String(form.content || '').trim(),
    target_id: targets[0] || '',
    target_ids: targets,
    parameters,
    urgency: form.urgency || capability?.default_urgency || 'normal',
  }
}
