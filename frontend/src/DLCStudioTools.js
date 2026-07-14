export const DLC_CONTENT_EXPORT_FORMAT = 'world-simulator-dlc-content-v1'
export const CAUSAL_REPLAY_FIXTURE_FORMAT = 'agentera-causal-replay-v1'

const ARRAY_SECTIONS = [
  'resources',
  'entities',
  'inventory',
  'entity_values',
  'relations',
  'actors',
  'agents',
  'initial_events',
  'ongoing_events',
  'random_events',
  'causal_condition_groups',
  'causal_rule_templates',
  'causal_event_rules',
  'time_rules',
  'actions',
  'capabilities',
  'issue_types',
  'objectives',
  'endings',
]
const OBJECT_SECTIONS = ['personas', 'prompts', 'ui_manifest']
const KNOWN_SECTIONS = new Set(['schema_version', ...ARRAY_SECTIONS, ...OBJECT_SECTIONS])

const KEYERS = {
  resources: item => item?.name,
  entities: item => item?.id,
  inventory: item => [item?.entity_id, item?.item_name || item?.resource_name, item?.category].join(' | '),
  entity_values: item => [item?.entity_id, item?.name].join(' | '),
  relations: item => [item?.parent_id, item?.child_id, item?.rel_type].join(' | '),
  actors: item => item?.actor_id || item?.id,
  agents: item => item?.id,
  initial_events: item => item?.event_id,
  ongoing_events: item => item?.event_id,
  random_events: item => item?.template_id,
  causal_condition_groups: item => item?.group_id,
  causal_rule_templates: item => item?.template_id,
  causal_event_rules: item => item?.rule_id,
  time_rules: item => item?.rule_id,
  actions: item => item?.action_id,
  capabilities: item => item?.capability_id,
  issue_types: item => item?.issue_type_id,
  objectives: item => item?.objective_id,
  endings: item => item?.ending_id,
}

function isObject(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

export function cloneJson(value) {
  return JSON.parse(JSON.stringify(value))
}

function stableValue(value) {
  if (Array.isArray(value)) return value.map(stableValue)
  if (!isObject(value)) return value
  return Object.fromEntries(Object.keys(value).sort().map(key => [key, stableValue(value[key])]))
}

export function sameJson(left, right) {
  return JSON.stringify(stableValue(left)) === JSON.stringify(stableValue(right))
}

export function normalizeImportedContent(payload) {
  if (!isObject(payload)) throw new Error('Imported JSON root must be an object')
  if (payload.format !== undefined && payload.format !== DLC_CONTENT_EXPORT_FORMAT) {
    throw new Error(`Unsupported DLC content format: ${payload.format}`)
  }
  const source = payload.format === DLC_CONTENT_EXPORT_FORMAT ? payload.content : payload
  if (!isObject(source)) throw new Error('Imported DLC content must be an object')

  const content = cloneJson(source)
  content.schema_version = String(content.schema_version || '1.0')
  for (const section of ARRAY_SECTIONS) {
    if (content[section] === undefined) content[section] = []
    if (!Array.isArray(content[section])) throw new Error(`${section} must be an array`)
  }
  for (const section of OBJECT_SECTIONS) {
    if (content[section] === undefined) content[section] = {}
    if (!isObject(content[section])) throw new Error(`${section} must be an object`)
  }
  return content
}

export function createContentExport(manifest, content, exportedAt = new Date().toISOString()) {
  const source = isObject(manifest) ? manifest : {}
  return {
    format: DLC_CONTENT_EXPORT_FORMAT,
    exported_at: exportedAt,
    source: {
      id: String(source.id || ''),
      name: String(source.name || ''),
      version: String(source.version || ''),
    },
    content: normalizeImportedContent(content),
  }
}

function indexedItems(items, keyer) {
  const seen = new Map()
  const result = new Map()
  items.forEach((item, index) => {
    const raw = String(keyer(item) || `index ${index + 1}`)
    const count = (seen.get(raw) || 0) + 1
    seen.set(raw, count)
    const key = count === 1 ? raw : `${raw} #${count}`
    result.set(key, item)
  })
  return result
}

function compareMaps(section, beforeMap, afterMap) {
  const keys = [...new Set([...beforeMap.keys(), ...afterMap.keys()])].sort()
  const entries = []
  let added = 0
  let removed = 0
  let changed = 0
  let unchanged = 0
  for (const key of keys) {
    const hasBefore = beforeMap.has(key)
    const hasAfter = afterMap.has(key)
    let type = 'unchanged'
    if (!hasBefore) {
      type = 'added'
      added += 1
    } else if (!hasAfter) {
      type = 'removed'
      removed += 1
    } else if (!sameJson(beforeMap.get(key), afterMap.get(key))) {
      type = 'changed'
      changed += 1
    } else {
      unchanged += 1
    }
    if (type !== 'unchanged') entries.push({ section, key, type })
  }
  return { section, added, removed, changed, unchanged, entries }
}

export function summarizeContentDiff(beforeValue, afterValue) {
  const before = normalizeImportedContent(beforeValue)
  const after = normalizeImportedContent(afterValue)
  const sections = []

  if (before.schema_version !== after.schema_version) {
    sections.push({
      section: 'schema_version',
      added: 0,
      removed: 0,
      changed: 1,
      unchanged: 0,
      entries: [{ section: 'schema_version', key: `${before.schema_version} -> ${after.schema_version}`, type: 'changed' }],
    })
  }

  for (const section of ARRAY_SECTIONS) {
    const beforeMap = indexedItems(before[section], KEYERS[section])
    const afterMap = indexedItems(after[section], KEYERS[section])
    const sectionDiff = compareMaps(section, beforeMap, afterMap)
    if (!sectionDiff.added && !sectionDiff.removed && !sectionDiff.changed
      && !sameJson([...beforeMap.keys()], [...afterMap.keys()])) {
      sectionDiff.changed = 1
      sectionDiff.entries.push({ section, key: 'item order', type: 'changed' })
    }
    sections.push(sectionDiff)
  }
  for (const section of OBJECT_SECTIONS) {
    sections.push(compareMaps(
      section,
      new Map(Object.entries(before[section])),
      new Map(Object.entries(after[section])),
    ))
  }

  const beforeExtensions = new Map(Object.entries(before).filter(([key]) => !KNOWN_SECTIONS.has(key)))
  const afterExtensions = new Map(Object.entries(after).filter(([key]) => !KNOWN_SECTIONS.has(key)))
  sections.push(compareMaps('extensions', beforeExtensions, afterExtensions))

  const totals = sections.reduce((result, section) => ({
    added: result.added + section.added,
    removed: result.removed + section.removed,
    changed: result.changed + section.changed,
    unchanged: result.unchanged + section.unchanged,
  }), { added: 0, removed: 0, changed: 0, unchanged: 0 })
  return {
    sections: sections.filter(section => section.added || section.removed || section.changed),
    entries: sections.flatMap(section => section.entries),
    totals,
    hasChanges: totals.added + totals.removed + totals.changed > 0,
  }
}

export function createContentHistory(content) {
  return { past: [], present: normalizeImportedContent(content), future: [] }
}

export function commitContentHistory(history, nextContent, limit = 50) {
  const next = normalizeImportedContent(nextContent)
  if (sameJson(history.present, next)) return history
  return {
    past: [...history.past, cloneJson(history.present)].slice(-Math.max(1, limit)),
    present: next,
    future: [],
  }
}

export function undoContentHistory(history) {
  if (!history.past.length) return null
  const present = cloneJson(history.past[history.past.length - 1])
  return {
    past: history.past.slice(0, -1),
    present,
    future: [cloneJson(history.present), ...history.future],
  }
}

export function redoContentHistory(history) {
  if (!history.future.length) return null
  const present = cloneJson(history.future[0])
  return {
    past: [...history.past, cloneJson(history.present)],
    present,
    future: history.future.slice(1),
  }
}

const SAFE_PROMOTION_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/
const SHA256_HEX = /^[a-f0-9]{64}$/

function promotionObject(value, label) {
  if (!isObject(value)) throw new Error(`${label} root must be an object`)
  return value
}

function promotionId(value, label) {
  const result = String(value || '').trim()
  if (!SAFE_PROMOTION_ID.test(result)) throw new Error(`${label} is invalid`)
  return result
}

export function parsePromotionArtifact(text) {
  let value
  try {
    value = JSON.parse(String(text || ''))
  } catch {
    throw new Error('Promotion artifact must be valid JSON')
  }
  const artifact = promotionObject(value, 'Promotion artifact')
  if (artifact.schema_version !== '1.0') throw new Error('Promotion schema_version must be 1.0')
  if (artifact.approved !== true || artifact.decision !== 'approved') {
    throw new Error('Promotion artifact is not approved')
  }
  if (!SHA256_HEX.test(String(artifact.policy_fingerprint || ''))) {
    throw new Error('Promotion policy_fingerprint must be a lowercase SHA-256 hex value')
  }
  const patch = promotionObject(artifact.policy_patch, 'Promotion policy_patch')
  const cohorts = promotionObject(patch.cohort_task_policies, 'Promotion cohort_task_policies')
  const cohortIds = Object.keys(cohorts)
  if (!cohortIds.length) throw new Error('Promotion artifact has no cohort policies')
  cohortIds.forEach(id => promotionId(id, 'Cohort ID'))
  return cloneJson(artifact)
}

export function parsePromotionMapping(text) {
  let value
  try {
    value = JSON.parse(String(text || ''))
  } catch {
    throw new Error('Promotion mapping must be valid JSON')
  }
  const mapping = promotionObject(value, 'Promotion mapping')
  if (mapping.schema_version !== '1.0') throw new Error('Promotion mapping schema_version must be 1.0')
  promotionId(mapping.promotion_id, 'Promotion ID')
  if (!Number.isInteger(mapping.expected_gateway_generation) || mapping.expected_gateway_generation < 1) {
    throw new Error('Expected Gateway generation must be a positive integer')
  }
  if (!SHA256_HEX.test(String(mapping.expected_policy_fingerprint || ''))) {
    throw new Error('Expected policy fingerprint must be a lowercase SHA-256 hex value')
  }
  promotionObject(mapping.cohort_targets, 'Promotion cohort_targets')
  return cloneJson(mapping)
}

export function createPromotionMapping({ promotion, dlcId, agentIds = [], generation, promotionId: requestedId }) {
  const artifact = typeof promotion === 'string' ? parsePromotionArtifact(promotion) : parsePromotionArtifact(JSON.stringify(promotion))
  const safeDlcId = promotionId(dlcId, 'DLC ID')
  const safePromotionId = promotionId(requestedId, 'Promotion ID')
  if (!Number.isInteger(generation) || generation < 1) throw new Error('Gateway generation must be a positive integer')
  const agents = [...new Set(agentIds.map(id => promotionId(id, 'Agent ID')))].sort()
  const cohortIds = Object.keys(artifact.policy_patch.cohort_task_policies).sort()
  return {
    schema_version: '1.0',
    promotion_id: safePromotionId,
    expected_gateway_generation: generation,
    expected_policy_fingerprint: artifact.policy_fingerprint,
    cohort_targets: Object.fromEntries(cohortIds.map(cohort => [cohort, {
      dlcs: [safeDlcId],
      agents: agents.map(agentId => ({ dlc_id: safeDlcId, agent_id: agentId })),
    }])),
  }
}

export function promotionInputFingerprint({ promotionText, mappingText, generation, dlcId }) {
  return JSON.stringify({
    promotionText: String(promotionText || ''),
    mappingText: String(mappingText || ''),
    generation: Number.isInteger(generation) ? generation : null,
    dlcId: String(dlcId || ''),
  })
}


export function generationRollbackFingerprint({ sourceGeneration, currentGeneration }) {
  return JSON.stringify({
    sourceGeneration: Number.isInteger(sourceGeneration) ? sourceGeneration : null,
    currentGeneration: Number.isInteger(currentGeneration) ? currentGeneration : null,
  })
}

export function summarizeGenerationStatus(payload) {
  if (!isObject(payload) || payload.schema_version !== '1.0') {
    throw new Error('Generation status schema_version must be 1.0')
  }
  if (!Number.isInteger(payload.current_generation) || payload.current_generation < 1) {
    throw new Error('Current generation must be a positive integer')
  }
  if (!Array.isArray(payload.generations)) throw new Error('Generation status must include generations')
  const generations = payload.generations.map(item => {
    if (!isObject(item) || !Number.isInteger(item.generation) || item.generation < 1) {
      throw new Error('Generation entry is invalid')
    }
    return {
      generation: item.generation,
      activatedAt: Number(item.activated_at) || 0,
      current: item.is_current === true,
      fingerprint: String(item.config_fingerprint || ''),
      calls: Math.max(0, Number(item.calls) || 0),
      errors: Math.max(0, Number(item.errors) || 0),
      fallbacks: Math.max(0, Number(item.fallback_attempts) || 0),
      tokens: Math.max(0, Number(item.total_tokens) || 0),
      meanLatencyMs: Math.max(0, Number(item.mean_latency_ms) || 0),
      maxLatencyMs: Math.max(0, Number(item.max_latency_ms) || 0),
      activeCalls: Math.max(0, Number(item.active_calls) || 0),
    }
  })
  return {
    currentGeneration: payload.current_generation,
    generations,
    archiveEnabled: payload.archive?.enabled === true,
    persistedGenerationCount: Math.max(0, Number(payload.archive?.persisted_generation_count) || 0),
    archiveIssueCount: Math.max(0, Number(payload.archive?.issue_count) || 0),
  }
}


export function expandCausalRuleDraft(content, authoredRule) {
  if (!isObject(content) || !isObject(authoredRule)) throw new Error('Causal rule draft is invalid')
  const templates = new Map((Array.isArray(content.causal_rule_templates) ? content.causal_rule_templates : [])
    .filter(isObject).map(item => [String(item.template_id || ''), item]))
  const groups = new Map((Array.isArray(content.causal_condition_groups) ? content.causal_condition_groups : [])
    .filter(isObject).map(item => [String(item.group_id || ''), item]))
  const authored = cloneJson(authoredRule)
  const templateId = String(authored.template_id || '').trim()
  const groupIds = Array.isArray(authored.condition_group_ids) ? authored.condition_group_ids.map(value => String(value || '').trim()) : []
  delete authored.template_id
  delete authored.condition_group_ids
  const template = templateId ? templates.get(templateId) : null
  if (templateId && !template) throw new Error(`Unknown causal template: ${templateId}`)
  const base = isObject(template?.rule) ? cloneJson(template.rule) : {}
  const templateConditions = Array.isArray(base.conditions) ? cloneJson(base.conditions) : []
  delete base.conditions
  const inlineConditions = Array.isArray(authored.conditions) ? cloneJson(authored.conditions) : []
  delete authored.conditions
  const groupedConditions = []
  for (const groupId of groupIds) {
    const group = groups.get(groupId)
    if (!group) throw new Error(`Unknown causal condition group: ${groupId}`)
    if (!Array.isArray(group.conditions)) throw new Error(`Invalid causal condition group: ${groupId}`)
    groupedConditions.push(...cloneJson(group.conditions))
  }
  return { ...base, ...authored, conditions: [...templateConditions, ...groupedConditions, ...inlineConditions] }
}


export function normalizeCausalReplayFixture(payload) {
  if (!isObject(payload)) throw new Error('Causal replay fixture root must be an object')
  if (payload.format !== CAUSAL_REPLAY_FIXTURE_FORMAT) throw new Error(`Unsupported causal replay format: ${payload.format || '<missing>'}`)
  if (payload.schema_version !== '1.0') throw new Error(`Unsupported causal replay schema: ${payload.schema_version || '<missing>'}`)
  if (!isObject(payload.input) || !isObject(payload.expected) || !isObject(payload.input.source_event)) throw new Error('Causal replay fixture is incomplete')
  const source = payload.input.source_event
  const horizonDays = Number(payload.input.horizon_days)
  const maxEvents = Number(payload.input.max_events)
  const severity = Number(source.severity)
  const generatedAt = Number(source.generated_at ?? 0)
  const digestPattern = /^[0-9a-f]{64}$/
  if (!digestPattern.test(String(payload.expected.analysis_fingerprint || '')) || !digestPattern.test(String(payload.expected.result_digest || ''))) throw new Error('Causal replay fixture digest is invalid')
  if (!isObject(payload.expected.summary) || !Array.isArray(source.target_agents) || !isObject(source.initial_payload)) throw new Error('Causal replay fixture payload is invalid')
  if (!String(source.event_id || '').trim() || !String(source.event_type || '').trim() || !Number.isInteger(severity) || severity < 1 || severity > 10) throw new Error('Causal replay source event is invalid')
  if (source.target_agents.length > 256 || source.target_agents.some(value => typeof value !== 'string' || !value.trim())) throw new Error('Causal replay target agents are invalid')
  if (!Number.isFinite(generatedAt) || generatedAt < 0) throw new Error('Causal replay generated_at is invalid')
  if (!Number.isFinite(horizonDays) || horizonDays < 0 || horizonDays > 3650 || !Number.isInteger(maxEvents) || maxEvents < 1 || maxEvents > 256) throw new Error('Causal replay limits are invalid')
  return {
    format: CAUSAL_REPLAY_FIXTURE_FORMAT,
    schema_version: '1.0',
    input: {
      source_event: {
        event_id: String(source.event_id),
        event_type: String(source.event_type),
        severity,
        target_agents: source.target_agents.map(value => value.trim()),
        initial_payload: cloneJson(source.initial_payload),
        generated_at: generatedAt,
      },
      horizon_days: horizonDays,
      max_events: maxEvents,
    },
    expected: {
      analysis_fingerprint: String(payload.expected.analysis_fingerprint),
      result_digest: String(payload.expected.result_digest),
      summary: cloneJson(payload.expected.summary),
    },
  }
}


export function buildCausalEventCatalog(content) {
  if (!isObject(content)) return []
  const values = new Set()
  for (const section of ['initial_events', 'random_events']) {
    for (const item of Array.isArray(content[section]) ? content[section] : []) {
      const eventType = typeof item?.event_type === 'string' ? item.event_type.trim() : ''
      if (eventType) values.add(eventType)
    }
  }
  for (const authoredRule of Array.isArray(content.causal_event_rules) ? content.causal_event_rules : []) {
    let rule = authoredRule
    try { rule = expandCausalRuleDraft(content, authoredRule) } catch { /* keep partial authoring catalog usable */ }
    const childType = typeof rule?.child_event_type === 'string' ? rule.child_event_type.trim() : ''
    if (childType) values.add(childType)
    for (const source of Array.isArray(rule?.source_event_types) ? rule.source_event_types : []) {
      const eventType = typeof source === 'string' ? source.trim() : ''
      if (eventType && eventType !== '*') values.add(eventType)
    }
  }
  return [...values].sort()
}

export function buildCausalDryRunDraft(content, rule, selectedIndex, sourceEvent) {
  if (!isObject(content) || !isObject(rule) || !isObject(sourceEvent)) {
    throw new Error('Causal dry-run draft is invalid')
  }
  const rules = (Array.isArray(content.causal_event_rules) ? content.causal_event_rules : []).map(cloneJson)
  if (Number.isInteger(selectedIndex) && selectedIndex >= 0 && selectedIndex < rules.length) rules[selectedIndex] = cloneJson(rule)
  else rules.push(cloneJson(rule))
  return {
    content: { ...cloneJson(content), causal_event_rules: rules },
    source_event: cloneJson(sourceEvent),
  }
}
