import test from 'node:test'
import assert from 'node:assert/strict'

import {
  createSettingsDraft,
  nextUniqueName,
  parseAdvancedModelJson,
  parseProfileList,
  renameMappingKey,
  settingsFingerprint,
  stripModelMetadata,
  summarizeSettingsPreview,
  supportsProviderQueueMetrics,
} from './SettingsSchemaTools.js'

const document = {
  generation: 4,
  model_gateway: {
    generation: 4,
    source_path: 'config/models.json',
    legacy_mode: false,
    providers: { primary: { base_url: 'https://example.invalid/v1', api_key: 'local-key', timeout_s: 30, api_key_configured: true } },
    models: { fast: { provider: 'primary', model: 'fast' } },
    task_policies: { default: ['fast'] },
    dlc_task_policies: {},
    agent_task_policies: {},
  },
  runtime: { simulation: { seconds_per_day: 120 } },
}

test('draft strips server metadata and preserves editable local key', () => {
  const draft = createSettingsDraft(document)
  assert.equal(draft.expected_generation, 4)
  assert.equal(draft.model_gateway.generation, undefined)
  assert.equal(draft.model_gateway.source_path, undefined)
  assert.equal(draft.model_gateway.providers.primary.api_key_configured, undefined)
  assert.equal(draft.model_gateway.providers.primary.api_key, 'local-key')
  assert.deepEqual(stripModelMetadata(document.model_gateway).task_policies, { default: ['fast'] })
})

test('profile list parsing trims and removes duplicates in fallback order', () => {
  assert.deepEqual(parseProfileList(' deep, fast, deep, , backup '), ['deep', 'fast', 'backup'])
})

test('advanced JSON requires every model section and rejects syntax errors', () => {
  const parsed = parseAdvancedModelJson(JSON.stringify(document.model_gateway))
  assert.deepEqual(Object.keys(parsed), [
    'providers', 'models', 'task_policies', 'dlc_task_policies', 'agent_task_policies',
  ])
  assert.throws(() => parseAdvancedModelJson('{oops'), /JSON 语法错误/)
  assert.throws(() => parseAdvancedModelJson('{}'), /providers/)
})

test('mapping helpers produce unique and collision-safe identifiers', () => {
  assert.equal(nextUniqueName('provider', { provider: {}, provider_2: {} }), 'provider_3')
  assert.deepEqual(renameMappingKey({ old: { value: 1 }, other: {} }, 'old', 'new'), {
    new: { value: 1 }, other: {},
  })
  assert.throws(() => renameMappingKey({ old: {}, other: {} }, 'old', 'other'), /已存在/)
})

test('fingerprint tracks plaintext key and runtime changes', () => {
  const first = createSettingsDraft(document)
  const second = createSettingsDraft(document)
  second.model_gateway.providers.primary.api_key = 'changed-local-key'
  assert.notEqual(settingsFingerprint(first), settingsFingerprint(second))
  second.model_gateway.providers.primary.api_key = 'local-key'
  assert.equal(settingsFingerprint(first), settingsFingerprint(second))
  second.runtime.simulation.seconds_per_day = 90
  assert.notEqual(settingsFingerprint(first), settingsFingerprint(second))
})

test('preview summary uses chinese change counts', () => {
  assert.equal(summarizeSettingsPreview({
    profiles: { added: ['x'], removed: [], changed: ['fast'] },
    task_policies_changed: ['default'],
  }), '新增 1 个 Profile；修改 1 个 Profile；修改 1 条全局路由')
})


test('queue metrics capability distinguishes an old backend payload from an empty queue', () => {
  assert.equal(supportsProviderQueueMetrics({ totals: {}, entries: [] }), false)
  assert.equal(supportsProviderQueueMetrics({ totals: {}, queues: [] }), true)
  assert.equal(supportsProviderQueueMetrics(null), false)
})
