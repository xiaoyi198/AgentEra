import assert from 'node:assert/strict'
import test from 'node:test'

import { derivedMetricStatusLabel, formatDerivedMetricValue, normalizeDerivedMetricResponse } from './DerivedMetricTools.js'

test('normalizer keeps only safe display projection fields', () => {
  const state = normalizeDerivedMetricResponse({ configured: true, generation: 4, metrics: [{
    metric_id: 'readiness', display_name: 'Readiness', description: 'Safe', unit: 'pts',
    precision: 1, status: 'ok', value: 72.55, dependencies: ['supply'],
    formula: { op: 'constant', value: 1 }, metadata: { secret: 'do-not-copy' },
  }] })
  assert.equal(state.metrics[0].metric_id, 'readiness')
  assert.equal(state.metrics[0].value, 72.55)
  assert.equal('formula' in state.metrics[0], false)
  assert.equal('metadata' in state.metrics[0], false)
  assert.equal(JSON.stringify(state).includes('do-not-copy'), false)
})

test('formatting is bounded and unavailable values do not leak error details', () => {
  assert.equal(formatDerivedMetricValue({ status: 'ok', value: 12.345, precision: 2 }), '12.35')
  assert.equal(formatDerivedMetricValue({ status: 'error', value: 99, precision: 0 }), '—')
  assert.equal(derivedMetricStatusLabel('missing'), '数据缺失')
})
