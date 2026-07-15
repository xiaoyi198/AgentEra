import assert from 'node:assert/strict'
import test from 'node:test'

import {
  createDefaultTimeSystem,
  normalizeTimeSystemForEditor,
  serializeTimeSystem,
  summarizeTimeSystem,
} from './TemporalSystemTools.js'

test('normalizer creates a complete editable calendar without mutating source', () => {
  const source = { enabled: true, calendar: { calendar_id: 'frontier', display_name: '边境历', months: [{ month_id: 'm1', display_name: '一月', days: 10 }], weekdays: ['甲', '乙'], start_month_id: 'm1', start_weekday: '甲' }, boundary_events: [{ boundary_event_id: 'turn_notice', trigger: 'day_start', event_type: 'notice', payload: { safe: true }, target_agents: ['warden'] }] }
  const normalized = normalizeTimeSystemForEditor(source)
  normalized.calendar.months[0].days = 12
  assert.equal(source.calendar.months[0].days, 10)
  assert.equal(normalized.boundary_events[0]._payload_text.includes('safe'), true)
  assert.equal(normalized.boundary_events[0]._target_agents_text, 'warden')
})

test('serializer strips editor fields and preserves boundary and causal pause semantics', () => {
  const form = createDefaultTimeSystem()
  form.enabled = true
  form.calendar.months = [{ month_id: 'm1', display_name: '一月', days: 10 }]
  form.calendar.start_month_id = 'm1'
  form.seasons = [{ season_id: 'dry', display_name: '旱季', start_day: 6, end_day: 2, description: '跨年季节', color: '#c98b47' }]
  form.cycles = [{ cycle_id: 'turn', display_name: '指挥回合', kind: 'turn', period_days: 7, duration_days: 7, offset_days: 0, enabled: true }]
  form.boundary_events = [{ boundary_event_id: 'turn_notice', trigger: 'cycle_start', cycle_id: 'turn', event_type: 'cycle_notice', severity: 5, _target_agents_text: 'warden, clerk', _payload_text: '{"type":"turn"}', enabled: true }]
  form.auto_pause_conditions = [{ pause_id: 'low_supply', display_name: '补给不足', trigger: 'condition', reason: '补给低于安全线', mode: 'all', conditions: [{ kind: 'time', operator: 'gte', field: 'sim_day', value: 3 }], cooldown_days: 1, once: false, edge_triggered: true, enabled: true }]
  const result = serializeTimeSystem(form, { agents: [{ id: 'warden' }, { id: 'clerk' }] })
  assert.deepEqual(result.boundary_events[0].target_agents, ['warden', 'clerk'])
  assert.deepEqual(result.boundary_events[0].payload, { type: 'turn' })
  assert.equal('_payload_text' in result.boundary_events[0], false)
  assert.equal(result.auto_pause_conditions[0].conditions[0].kind, 'time')
  assert.equal(result.seasons[0].start_day, 6)
})

test('serializer rejects overlapping seasons and missing cycle references', () => {
  const form = createDefaultTimeSystem()
  form.calendar.months = [{ month_id: 'm1', display_name: '一月', days: 10 }]
  form.calendar.start_month_id = 'm1'
  form.seasons = [
    { season_id: 'a', display_name: 'A', start_day: 1, end_day: 6 },
    { season_id: 'b', display_name: 'B', start_day: 6, end_day: 9 },
  ]
  assert.throws(() => serializeTimeSystem(form), /重叠/)
  form.seasons = []
  form.boundary_events = [{ boundary_event_id: 'bad', trigger: 'cycle_start', cycle_id: 'missing', event_type: 'notice', severity: 5, payload: {}, enabled: true }]
  assert.throws(() => serializeTimeSystem(form), /不存在的周期/)
})

test('summary reports authoring density', () => {
  const form = createDefaultTimeSystem()
  form.cycles.push({ cycle_id: 'turn' })
  const summary = summarizeTimeSystem(form)
  assert.equal(summary.months, 12)
  assert.equal(summary.year_days, 365)
  assert.equal(summary.cycles, 1)
})
