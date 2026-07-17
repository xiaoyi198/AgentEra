import assert from 'node:assert/strict'
import test from 'node:test'

import {
  collectRhythmEventTypes,
  createRhythmRule,
  normalizeRhythmForEditor,
  serializeRhythm,
  validateRhythm,
} from './RhythmEditorTools.js'

const content = {
  issue_types: [{ issue_type_id: 'frontier_report', display_name: '边境报告' }],
  initial_events: [{ event_type: 'patrol_report' }],
  causal_event_rules: [{ source_event_types: ['patrol_report'], child_event_type: 'frontier_response' }],
  time_system: { boundary_events: [{ event_type: 'command_turn' }] },
}

function validForm() {
  const form = normalizeRhythmForEditor({ enabled: true })
  const rule = createRhythmRule(0)
  rule.rule_id = 'frontier_pacing'
  rule.display_name = '边境危机节奏'
  rule.issue_type_ids = ['frontier_report']
  rule.event_types = ['patrol_report']
  rule.escalation.enabled = true
  rule.escalation.max_level = 3
  rule.deescalation.enabled = true
  rule.deescalation.conditions = [{ kind: 'time', operator: 'gte', field: 'sim_day', value: 2 }]
  rule.climax.enabled = true
  form.rules = [rule]
  return form
}

test('normalizer creates complete defaults without mutating the source', () => {
  const source = { enabled: true, rules: [{ rule_id: 'a', display_name: 'A', event_types: ['patrol_report'], escalation: { max_level: 2, urgency_by_level: ['low'] } }] }
  const normalized = normalizeRhythmForEditor(source)
  normalized.rules[0].event_types.push('other')
  assert.deepEqual(source.rules[0].event_types, ['patrol_report'])
  assert.equal(normalized.max_active_issues, 8)
  assert.equal(normalized.rules[0].escalation.urgency_by_level.length, 3)
})

test('serializer emits clean runtime contract and serializes causal conditions', () => {
  const result = serializeRhythm(validForm(), content)
  assert.equal(result.enabled, true)
  assert.equal(result.rules[0].attention_cost, null)
  assert.equal(result.rules[0].deescalation.conditions[0].kind, 'time')
  assert.equal(result.rules[0].escalation.urgency_by_level.length, 4)
})

test('serializer rejects invalid and duplicate rule ids', () => {
  const invalid = validForm()
  invalid.rules[0].rule_id = '含空格 id'
  assert.throws(() => serializeRhythm(invalid, content), /只能使用字母/)
  const duplicate = validForm()
  duplicate.rules.push({ ...createRhythmRule(1), rule_id: 'frontier_pacing', event_types: ['command_turn'] })
  assert.throws(() => serializeRhythm(duplicate, content), /ID 重复/)
})

test('serializer enforces ranges, references, matching and urgency values', () => {
  const tooManyActive = validForm()
  tooManyActive.max_active_issues = 65
  assert.throws(() => serializeRhythm(tooManyActive, content), /活跃 Issue 全局上限/)
  const missingMatch = validForm()
  missingMatch.rules[0].issue_type_ids = []
  missingMatch.rules[0].event_types = []
  assert.throws(() => serializeRhythm(missingMatch, content), /至少要匹配/)
  const missingIssue = validForm()
  missingIssue.rules[0].issue_type_ids = ['missing']
  assert.throws(() => serializeRhythm(missingIssue, content), /不存在的 Issue 类型/)
  const badUrgency = validForm()
  badUrgency.rules[0].escalation.urgency_by_level[1] = 'urgent'
  assert.throws(() => serializeRhythm(badUrgency, content), /low、normal、high 或 critical/)
  const badNumber = validForm()
  badNumber.attention_budget = Number.NaN
  assert.throws(() => serializeRhythm(badNumber, content), /注意力预算/)
})

test('serializer enforces queue, rule count, priority and level bounds', () => {
  const badQueue = validForm()
  badQueue.queue_limit = 2049
  assert.throws(() => serializeRhythm(badQueue, content), /排队上限/)
  const badPriority = validForm()
  badPriority.rules[0].priority = 1001
  assert.throws(() => serializeRhythm(badPriority, content), /优先级/)
  const badLevel = validForm()
  badLevel.rules[0].escalation.max_level = 2
  badLevel.rules[0].climax.level = 3
  assert.throws(() => serializeRhythm(badLevel, content), /高潮等级不能高于/)
  const tooMany = validForm()
  tooMany.rules = Array.from({ length: 257 }, (_, index) => ({ ...createRhythmRule(index), event_types: ['patrol_report'] }))
  assert.throws(() => serializeRhythm(tooMany, content), /最多 256 条/)
})

test('event catalog collects authored event sources and validation reports errors', () => {
  assert.deepEqual(collectRhythmEventTypes(content), ['command_turn', 'frontier_response', 'patrol_report'])
  const form = validForm()
  form.rules[0].cooldown_days = -1
  const validation = validateRhythm(form, content)
  assert.equal(validation.valid, false)
  assert.match(validation.errors[0], /冷却天数/)
})
