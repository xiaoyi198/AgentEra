export const DLC_CONTENT_EXPORT_FORMAT = 'world-simulator-dlc-content-v1'

const ARRAY_SECTIONS = [
  'resources',
  'entities',
  'inventory',
  'entity_values',
  'relations',
  'agents',
  'initial_events',
  'ongoing_events',
  'random_events',
  'time_rules',
]
const OBJECT_SECTIONS = ['personas', 'prompts']
const KNOWN_SECTIONS = new Set(['schema_version', ...ARRAY_SECTIONS, ...OBJECT_SECTIONS])

const KEYERS = {
  resources: item => item?.name,
  entities: item => item?.id,
  inventory: item => [item?.entity_id, item?.item_name || item?.resource_name, item?.category].join(' | '),
  entity_values: item => [item?.entity_id, item?.name].join(' | '),
  relations: item => [item?.parent_id, item?.child_id, item?.rel_type].join(' | '),
  agents: item => item?.id,
  initial_events: item => item?.event_id,
  ongoing_events: item => item?.event_id,
  random_events: item => item?.template_id,
  time_rules: item => item?.rule_id,
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
