import test from 'node:test'
import assert from 'node:assert/strict'

import {
  DLC_CONTENT_EXPORT_FORMAT,
  commitContentHistory,
  createContentExport,
  createContentHistory,
  normalizeImportedContent,
  redoContentHistory,
  summarizeContentDiff,
  undoContentHistory,
} from './DLCStudioTools.js'

const base = {
  schema_version: '1.0',
  resources: [{ name: 'silver', kind: 'asset', anchor_rate: 1 }],
  entities: [{ id: 'city', name: 'City' }],
  inventory: [],
  entity_values: [],
  relations: [],
  agents: [],
  initial_events: [],
  ongoing_events: [],
  random_events: [],
  time_rules: [],
  personas: {},
  prompts: {},
}

test('content export is secret-free and can be imported from its envelope', () => {
  const exported = createContentExport(
    { id: 'demo', name: 'Demo', version: '1.2.3', api_key: 'never-export' },
    base,
    '2026-07-11T00:00:00.000Z',
  )

  assert.equal(exported.format, DLC_CONTENT_EXPORT_FORMAT)
  assert.deepEqual(exported.source, { id: 'demo', name: 'Demo', version: '1.2.3' })
  assert.equal(JSON.stringify(exported).includes('never-export'), false)
  assert.deepEqual(normalizeImportedContent(exported), base)
})

test('import normalization fills missing collections and rejects wrong shapes', () => {
  const normalized = normalizeImportedContent({ entities: [] })
  assert.equal(normalized.schema_version, '1.0')
  assert.deepEqual(normalized.prompts, {})
  assert.deepEqual(normalized.inventory, [])
  assert.throws(() => normalizeImportedContent({ entities: {} }), /entities must be an array/)
  assert.throws(() => normalizeImportedContent({ prompts: [] }), /prompts must be an object/)
  assert.throws(
    () => normalizeImportedContent({ format: 'world-simulator-dlc-content-v2', content: base }),
    /Unsupported DLC content format/,
  )
})

test('diff summary reports keyed additions removals and changes', () => {
  const next = {
    ...base,
    resources: [{ name: 'silver', kind: 'asset', anchor_rate: 2 }, { name: 'grain', kind: 'asset', anchor_rate: 0.5 }],
    entities: [],
    personas: { calm: 'Verify first.' },
  }
  const diff = summarizeContentDiff(base, next)

  assert.equal(diff.hasChanges, true)
  assert.deepEqual(diff.totals, { added: 2, removed: 1, changed: 1, unchanged: 0 })
  assert.deepEqual(diff.entries.map(item => `${item.section}:${item.key}:${item.type}`), [
    'resources:grain:added',
    'resources:silver:changed',
    'entities:city:removed',
    'personas:calm:added',
  ])
})

test('history truncates redo on commit and round-trips undo redo', () => {
  const initial = createContentHistory(base)
  const withAgent = commitContentHistory(initial, { ...base, agents: [{ id: 'agent' }] })
  const withPrompt = commitContentHistory(withAgent, { ...withAgent.present, prompts: { official: 'Act.' } })

  const undone = undoContentHistory(withPrompt)
  assert.deepEqual(undone.present.agents, [{ id: 'agent' }])
  assert.deepEqual(undone.present.prompts, {})

  const redone = redoContentHistory(undone)
  assert.deepEqual(redone.present.prompts, { official: 'Act.' })

  const branched = commitContentHistory(undone, { ...undone.present, personas: { calm: 'Calm.' } })
  assert.deepEqual(branched.future, [])
  assert.equal(redoContentHistory(branched), null)
})


test('diff detects array reordering and extension field changes', () => {
  const reordered = {
    ...base,
    resources: [
      { name: 'grain', kind: 'asset', anchor_rate: 0.5 },
      ...base.resources,
    ],
  }
  const sameItemsDifferentOrder = {
    ...reordered,
    resources: [...reordered.resources].reverse(),
  }
  const orderDiff = summarizeContentDiff(reordered, sameItemsDifferentOrder)
  assert.equal(orderDiff.hasChanges, true)
  assert.deepEqual(orderDiff.entries, [{ section: 'resources', key: 'item order', type: 'changed' }])

  const extensionDiff = summarizeContentDiff(base, { ...base, custom_rules: { enabled: true } })
  assert.equal(extensionDiff.hasChanges, true)
  assert.deepEqual(extensionDiff.entries, [{ section: 'extensions', key: 'custom_rules', type: 'added' }])
})


test('migration preview content is represented as a reviewable schema and keyed diff', () => {
  const legacy = {
    ...base,
    schema_version: '0.9',
    resources: [{ name: 'silver', type: 'asset', anchor_rate: 1 }],
    inventory: [{ entity_id: 'city', resource_name: 'silver', quantity: 2 }],
  }
  const migrated = {
    ...base,
    schema_version: '1.0',
    resources: [{ name: 'silver', kind: 'asset', anchor_rate: 1 }],
    inventory: [{ entity_id: 'city', item_name: 'silver', quantity: 2 }],
  }

  const diff = summarizeContentDiff(legacy, migrated)
  const history = commitContentHistory(createContentHistory(legacy), migrated)

  assert.equal(diff.hasChanges, true)
  assert.equal(diff.entries.some(item => item.section === 'schema_version'), true)
  assert.equal(diff.entries.some(item => item.section === 'resources' && item.type === 'changed'), true)
  assert.equal(diff.entries.some(item => item.section === 'inventory' && item.type === 'changed'), true)
  assert.equal(history.past.length, 1)
  assert.equal(history.present.schema_version, '1.0')
})
