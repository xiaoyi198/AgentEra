export const MODEL_SECTIONS = [
  'providers',
  'models',
  'task_policies',
  'dlc_task_policies',
  'agent_task_policies',
]

export function deepClone(value) {
  return JSON.parse(JSON.stringify(value ?? null))
}

export function stripModelMetadata(modelGateway = {}) {
  const stripped = Object.fromEntries(MODEL_SECTIONS.map(section => [
    section,
    deepClone(modelGateway[section] ?? {}),
  ]))
  stripped.providers = Object.fromEntries(Object.entries(stripped.providers).map(([name, provider]) => {
    const { api_key_configured: _apiKeyConfigured, ...editable } = provider
    return [name, editable]
  }))
  return stripped
}

export function createSettingsDraft(document = {}) {
  return {
    model_gateway: stripModelMetadata(document.model_gateway),
    runtime: deepClone(document.runtime || {}),
    expected_generation: document.generation ?? document.model_gateway?.generation ?? null,
  }
}

export function settingsFingerprint(draft) {
  if (!draft) return ''
  return JSON.stringify({
    model_gateway: draft.model_gateway,
    runtime: draft.runtime,
  })
}

export function parseProfileList(value) {
  const seen = new Set()
  return String(value || '')
    .split(',')
    .map(item => item.trim())
    .filter(item => item && !seen.has(item) && seen.add(item))
}

export function parseAdvancedModelJson(text) {
  let parsed
  try {
    parsed = JSON.parse(text)
  } catch (error) {
    throw new Error(`JSON 语法错误：${error.message}`)
  }
  if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
    throw new Error('高级模型配置必须是 JSON 对象。')
  }
  for (const section of MODEL_SECTIONS) {
    const value = parsed[section]
    if (!value || Array.isArray(value) || typeof value !== 'object') {
      throw new Error(`缺少对象字段：${section}`)
    }
  }
  return stripModelMetadata(parsed)
}

export function nextUniqueName(prefix, mapping = {}) {
  if (!mapping[prefix]) return prefix
  let index = 2
  while (mapping[`${prefix}_${index}`]) index += 1
  return `${prefix}_${index}`
}

export function renameMappingKey(mapping, oldName, nextName) {
  const name = String(nextName || '').trim()
  if (!name) throw new Error('名称不能为空。')
  if (name !== oldName && mapping[name]) throw new Error(`名称“${name}”已存在。`)
  return Object.fromEntries(Object.entries(mapping).map(([key, value]) => [
    key === oldName ? name : key,
    value,
  ]))
}

export function supportsProviderQueueMetrics(metrics) {
  return Boolean(
    metrics
    && Object.prototype.hasOwnProperty.call(metrics, 'queues')
    && Array.isArray(metrics.queues)
  )
}

export function summarizeSettingsPreview(preview = {}) {
  const profile = preview.profiles || {}
  const parts = []
  if (profile.added?.length) parts.push(`新增 ${profile.added.length} 个 Profile`)
  if (profile.removed?.length) parts.push(`删除 ${profile.removed.length} 个 Profile`)
  if (profile.changed?.length) parts.push(`修改 ${profile.changed.length} 个 Profile`)
  if (preview.task_policies_changed?.length) parts.push(`修改 ${preview.task_policies_changed.length} 条全局路由`)
  if (preview.dlc_task_policies_changed?.length) parts.push(`修改 ${preview.dlc_task_policies_changed.length} 个 DLC 路由域`)
  if (preview.agent_task_policies_changed?.length) parts.push(`修改 ${preview.agent_task_policies_changed.length} 个 Agent 路由域`)
  return parts.length ? parts.join('；') : '模型网关配置没有结构性变化'
}
