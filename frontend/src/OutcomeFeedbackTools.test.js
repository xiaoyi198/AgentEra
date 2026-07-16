import assert from 'node:assert/strict'
import test from 'node:test'

import {
  describeOutcomeChange, feedbackKindLabel, mergeOutcomeFeedbackState,
  normalizeOutcomeFeedbackResponse,
} from './OutcomeFeedbackTools.js'

test('normalizes safe feedback and removes unknown fields', () => {
  const state = normalizeOutcomeFeedbackResponse({ configured: true, enabled: true, generation: 4, recent: [{
    feedback_id: 'feedback_1', kind: 'immediate', title: '\u5b8c\u6210', prompt: 'secret',
    source: { kind: 'action', id: 'a1', display_name: '\u884c\u52a8', payload: { secret: true } },
    metric_changes: [{ metric_id: 'stability', before: 1, after: 2, delta: 1, formula: 'secret' }],
  }] })
  assert.deepEqual(state.recent[0].metric_changes[0], { metric_id: 'stability', before: 1, after: 2, delta: 1 })
  assert.equal('prompt' in state.recent[0], false)
  assert.equal('payload' in state.recent[0].source, false)
})

test('merges tick deliveries idempotently', () => {
  const item = { feedback_id: 'feedback_1', kind: 'delayed_report', title: '\u62a5\u544a', delivered_at: 10 }
  const first = mergeOutcomeFeedbackState({}, { enabled: true, delivered: [item], pending_count: 1 })
  const second = mergeOutcomeFeedbackState(first, { enabled: true, delivered: [item], pending_count: 0 })
  assert.equal(second.recent.length, 1)
  assert.equal(second.pending_count, 0)
})

test('formats feedback labels and deterministic changes', () => {
  assert.equal(feedbackKindLabel('causal_reveal'), '\u56e0\u679c\u63ed\u793a')
  assert.equal(
    describeOutcomeChange({ display_name: '\u7a33\u5b9a\u5ea6', before: 10, after: 15, unit: '%' }),
    '\u7a33\u5b9a\u5ea6\uff1a10% \u2192 15%',
  )
})
