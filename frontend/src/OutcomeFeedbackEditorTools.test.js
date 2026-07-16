import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildOutcomeFeedbackCatalogs,
  defaultOutcomeFeedbackRule,
  normalizeOutcomeFeedbackForEditor,
  serializeOutcomeFeedbackEditor,
} from './OutcomeFeedbackEditorTools.js'

test('normalizes every outcome feedback setting for controlled editor fields', () => {
  const form = normalizeOutcomeFeedbackForEditor({
    enabled: false,
    recent_limit: 32,
    immediate: { max_changes: 5 },
    rules: [{ rule_id: 'rule_a', display_name: '规则 A', source_kinds: ['action'], delivery: 'delayed', delay_days: 2 }],
  })
  assert.equal(form.enabled, false)
  assert.equal(form.recent_limit, '32')
  assert.equal(form.immediate.max_changes, '5')
  assert.equal(form.immediate.include_metric_changes, true)
  assert.equal(form.rules[0].delay_days, '2')
  assert.deepEqual(form.rules[0].action_ids, [])
})

test('serializes complete rule contract and deterministic numeric values', () => {
  const form = normalizeOutcomeFeedbackForEditor({ rules: [{
    rule_id: 'supply_result', display_name: '补给结果', source_kinds: ['action', 'case_closed'],
    delivery: 'delayed', delay_days: 3.5, action_ids: ['escort_supply'], issue_type_ids: ['supply_case'],
    event_types: ['supply_arrived'], metric_ids: ['readiness'], title: '补给报告', attach_evidence: true,
  }] })
  const document = serializeOutcomeFeedbackEditor(form)
  assert.equal(document.recent_limit, 128)
  assert.equal(document.rules[0].delay_days, 3.5)
  assert.deepEqual(document.rules[0].source_kinds, ['action', 'case_closed'])
  assert.deepEqual(document.rules[0].metric_ids, ['readiness'])
  assert.equal(document.rules[0].attach_evidence, true)
})

test('requires unique safe IDs and reveal text for causal reveal', () => {
  const first = defaultOutcomeFeedbackRule(0)
  const second = { ...defaultOutcomeFeedbackRule(1), rule_id: first.rule_id }
  assert.throws(() => serializeOutcomeFeedbackEditor({ ...normalizeOutcomeFeedbackForEditor({}), rules: [first, second] }), /重复/)
  assert.throws(() => serializeOutcomeFeedbackEditor({ ...normalizeOutcomeFeedbackForEditor({}), rules: [{ ...first, delivery: 'causal_reveal', reveal_text: '' }] }), /揭示文本/)
  assert.throws(() => serializeOutcomeFeedbackEditor({ ...normalizeOutcomeFeedbackForEditor({}), rules: [{ ...first, rule_id: 'bad id' }] }), /只能使用/)
})

test('builds reference catalogs from authored content and removes duplicates', () => {
  const catalogs = buildOutcomeFeedbackCatalogs({
    actions: [{ action_id: 'escort', display_name: '护送' }],
    issue_types: [{ issue_type_id: 'supply', display_name: '补给事项' }],
    derived_metrics: [{ metric_id: 'readiness', display_name: '战备度' }],
    initial_events: [{ event_type: 'supply_arrived', title: '补给抵达' }],
    causal_event_rules: [{ source_event_types: ['supply_arrived', 'road_open'], child_event_type: 'route_safe' }],
  })
  assert.deepEqual(catalogs.actions, [{ id: 'escort', label: '护送' }])
  assert.deepEqual(catalogs.eventTypes.map(item => item.id), ['supply_arrived', 'road_open', 'route_safe'])
})
