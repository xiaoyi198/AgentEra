import assert from 'node:assert/strict'
import test from 'node:test'

import {
  activeTemporalCycles,
  boundaryTriggerLabel,
  formatAutoPauseNotice,
  formatCycleProgress,
  formatSeason,
  formatTemporalDate,
  normalizeTimeSystemState,
} from './TemporalStateTools.js'

test('normalizeTimeSystemState keeps only the public time projection', () => {
  const source = {
    configured: true,
    enabled: true,
    generation: 7,
    fingerprint: 'abc',
    calendar: { calendar_id: 'frontier', display_name: '边境驻防历', epoch_label: '驻防纪元', year_days: 360, months: [{ secret: true }] },
    current_date: { calendar_id: 'frontier', epoch_label: '驻防纪元', year: 12, month_id: 'rain', month_name: '雨月', day: 3, weekday: '巡日', day_of_year: 33, year_days: 360, hidden: 'x' },
    current_season: { season_id: 'spring', display_name: '融雪季', start_day: 1, end_day: 90, private_rule: 'x' },
    cycles: [{ cycle_id: 'command', display_name: '指挥回合', kind: 'turn', period_days: 7, duration_days: 7, occurrence: 2, active: true, progress: 0.5, private_rule: 'x' }],
    auto_pause_conditions: [{ pause_id: 'risk', display_name: '战备告警', reason: '战备不足', trigger: 'condition', conditions: [{ value: 30 }], fired_count: 1 }],
    auto_pause: { pause_id: 'risk', display_name: '战备告警', reason: '战备不足', trigger: 'condition', fired_at: 86400, conditions: [{ value: 30 }], requests: [{ secret: 1 }] },
  }

  const normalized = normalizeTimeSystemState(source)
  assert.equal(normalized.calendar.display_name, '边境驻防历')
  assert.equal(normalized.current_date.month_name, '雨月')
  assert.equal(normalized.cycles[0].progress, 0.5)
  assert.equal(normalized.auto_pause.request_count, 1)
  assert.equal('months' in normalized.calendar, false)
  assert.equal('hidden' in normalized.current_date, false)
  assert.equal('private_rule' in normalized.cycles[0], false)
  assert.equal('conditions' in normalized.auto_pause_conditions[0], false)
  assert.equal('conditions' in normalized.auto_pause, false)
  assert.notEqual(normalized, source)
})

test('date and season formatting gracefully fall back across DLC calendars', () => {
  const state = normalizeTimeSystemState({
    current_date: { epoch_label: '星历', year: 4, month_name: '长夜月', day: 9, weekday: '火曜日' },
    current_season: { season_id: 'winter', display_name: '封冻季' },
  })
  assert.equal(formatTemporalDate(state), '星历 4年 长夜月9日 · 火曜日')
  assert.equal(formatSeason(state), '封冻季')
  assert.equal(formatTemporalDate(null), '')
})

test('active cycles are sorted by gameplay kind and show occurrence progress', () => {
  const cycles = activeTemporalCycles([
    { cycle_id: 'task', display_name: '巡防', kind: 'task', active: true, occurrence: 1, progress: 0.25 },
    { cycle_id: 'meeting', display_name: '联席会议', kind: 'meeting', active: false, occurrence: 0, progress: 1 },
    { cycle_id: 'turn', display_name: '指挥回合', kind: 'turn', active: true, occurrence: 2, progress: 0.51 },
  ])
  assert.deepEqual(cycles.map(item => item.cycle_id), ['turn', 'task'])
  assert.equal(formatCycleProgress(cycles[0]), '回合 · 第3期 · 51%')
})

test('auto pause notice exposes a Chinese safe boundary summary without nested requests', () => {
  const notice = formatAutoPauseNotice({
    pause_id: 'meeting',
    display_name: '联席会议暂停',
    reason: '等待玩家处理议程',
    trigger: 'cycle_start',
    fired_at: 864000,
    requests: [{ conditions: [{ value: 'hidden' }] }, {}],
    marker: {
      trigger: 'cycle_start',
      cycle_id: 'joint_meeting',
      date: { epoch_label: '驻防纪元', year: 1, month_name: '初雪月', day: 11, weekday: '议日' },
      private_payload: { secret: true },
    },
  })
  assert.equal(notice.boundary, '周期开始')
  assert.equal(notice.request_count, 2)
  assert.match(notice.detail, /驻防纪元 1年 初雪月11日/)
  assert.equal('requests' in notice, false)
  assert.equal('private_payload' in notice.marker, false)
  assert.equal(boundaryTriggerLabel('season_end'), '季节结束')
})
