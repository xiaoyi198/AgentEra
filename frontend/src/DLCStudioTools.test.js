import test from 'node:test'
import assert from 'node:assert/strict'

import {
  DLC_CONTENT_EXPORT_FORMAT,
  commitContentHistory,
  createContentExport,
  createContentHistory,
  createPromotionMapping,
  generationRollbackFingerprint,
  normalizeImportedContent,
  parsePromotionArtifact,
  parsePromotionMapping,
  promotionInputFingerprint,
  redoContentHistory,
  summarizeContentDiff,
  summarizeGenerationStatus,
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


const approvedPromotion = {
  schema_version: '1.0',
  gate_id: 'gate-v1',
  environment_id: 'staging',
  decision: 'approved',
  approved: true,
  matrix_id: 'matrix-v1',
  matrix_fingerprint: 'b'.repeat(64),
  policy_fingerprint: 'a'.repeat(64),
  policy_patch: {
    task_policies: {},
    cohort_task_policies: {
      'daming-core': { 'agent.turn': ['deep'] },
      'daming-router': { 'router.ambiguous': ['fast'] },
    },
  },
}

test('promotion artifact parser enforces approval schema fingerprints and cohorts', () => {
  assert.deepEqual(parsePromotionArtifact(JSON.stringify(approvedPromotion)), approvedPromotion)
  assert.throws(() => parsePromotionArtifact('[]'), /root must be an object/)
  assert.throws(() => parsePromotionArtifact('{'), /valid JSON/)
  assert.throws(
    () => parsePromotionArtifact(JSON.stringify({ ...approvedPromotion, approved: false })),
    /not approved/,
  )
  assert.throws(
    () => parsePromotionArtifact(JSON.stringify({ ...approvedPromotion, policy_fingerprint: 'ABC' })),
    /lowercase SHA-256/,
  )
})

test('quick promotion mapping scopes every cohort to current DLC and explicit agents', () => {
  const mapping = createPromotionMapping({
    promotion: approvedPromotion,
    dlcId: 'daming_court',
    agentIds: ['cabinet', 'cabinet', 'emperor'],
    generation: 7,
    promotionId: 'promotion-2026-07-12',
  })

  assert.equal(mapping.expected_gateway_generation, 7)
  assert.equal(mapping.expected_policy_fingerprint, 'a'.repeat(64))
  assert.deepEqual(Object.keys(mapping.cohort_targets), ['daming-core', 'daming-router'])
  assert.deepEqual(mapping.cohort_targets['daming-core'], {
    dlcs: ['daming_court'],
    agents: [
      { dlc_id: 'daming_court', agent_id: 'cabinet' },
      { dlc_id: 'daming_court', agent_id: 'emperor' },
    ],
  })
  assert.deepEqual(parsePromotionMapping(JSON.stringify(mapping)), mapping)
})

test('promotion mapping and input fingerprint reject stale or unsafe operator input', () => {
  assert.throws(
    () => createPromotionMapping({ promotion: approvedPromotion, dlcId: '../bad', generation: 1, promotionId: 'safe' }),
    /DLC ID is invalid/,
  )
  assert.throws(
    () => parsePromotionMapping(JSON.stringify({ schema_version: '1.0', promotion_id: 'safe' })),
    /positive integer/,
  )
  const first = promotionInputFingerprint({ promotionText: '{}', mappingText: '{}', generation: 1, dlcId: 'a' })
  const nextGeneration = promotionInputFingerprint({ promotionText: '{}', mappingText: '{}', generation: 2, dlcId: 'a' })
  const nextDlc = promotionInputFingerprint({ promotionText: '{}', mappingText: '{}', generation: 1, dlcId: 'b' })
  assert.notEqual(first, nextGeneration)
  assert.notEqual(first, nextDlc)
})


test('generation rollback fingerprint invalidates stale source or current generation', () => {
  const first = generationRollbackFingerprint({ sourceGeneration: 1, currentGeneration: 3 })
  assert.notEqual(first, generationRollbackFingerprint({ sourceGeneration: 2, currentGeneration: 3 }))
  assert.notEqual(first, generationRollbackFingerprint({ sourceGeneration: 1, currentGeneration: 4 }))
  assert.equal(first, generationRollbackFingerprint({ sourceGeneration: 1, currentGeneration: 3 }))
})

test('generation status summary keeps operational aggregates and omits unsafe extras', () => {
  const summary = summarizeGenerationStatus({
    schema_version: '1.0',
    current_generation: 3,
    generations: [{
      generation: 3, is_current: true, activated_at: 123,
      config_fingerprint: 'a'.repeat(64), calls: 5, errors: 1,
      fallback_attempts: 2, total_tokens: 42, mean_latency_ms: 10.5,
      max_latency_ms: 20, active_calls: 1, base_url: 'unsafe',
    }],
    archive: { enabled: true, persisted_generation_count: 3, issue_count: 0 },
  })
  assert.deepEqual(summary, {
    currentGeneration: 3,
    generations: [{
      generation: 3, activatedAt: 123, current: true, fingerprint: 'a'.repeat(64),
      calls: 5, errors: 1, fallbacks: 2, tokens: 42, meanLatencyMs: 10.5,
      maxLatencyMs: 20, activeCalls: 1,
    }],
    archiveEnabled: true,
    persistedGenerationCount: 3,
    archiveIssueCount: 0,
  })
  assert.equal(JSON.stringify(summary).includes('unsafe'), false)
  assert.throws(() => summarizeGenerationStatus({ schema_version: '0.9' }), /schema_version/)
})
