const EMPTY_TIME_SYSTEM_STATE = Object.freeze({
  configured: false,
  enabled: false,
  generation: 0,
  fingerprint: '',
  calendar: null,
  current_date: null,
  current_season: null,
  cycles: [],
  auto_pause_conditions: [],
  recent_auto_pauses: [],
  auto_pause: null,
})

const CYCLE_KIND_LABELS = Object.freeze({
  turn: '回合',
  meeting: '会议',
  task: '任务',
  custom: '周期',
})

const BOUNDARY_LABELS = Object.freeze({
  day_start: '新的一天开始',
  week_start: '新的一周开始',
  month_start: '新月份开始',
  year_start: '新年份开始',
  season_start: '季节开始',
  season_end: '季节结束',
  cycle_start: '周期开始',
  cycle_end: '周期结束',
  condition: '条件满足',
})

function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function safeString(value, fallback = '', limit = 512) {
  return typeof value === 'string' ? value.slice(0, limit) : fallback
}

function safeNumber(value, fallback = 0) {
  return Number.isFinite(value) ? value : fallback
}

function safeInteger(value, fallback = 0) {
  return Number.isInteger(value) ? value : fallback
}

function normalizeDate(value) {
  if (!isObject(value)) return null
  return {
    calendar_id: safeString(value.calendar_id, '', 128),
    display_name: safeString(value.display_name, '', 128),
    epoch_label: safeString(value.epoch_label, '', 128),
    year: safeInteger(value.year, 0),
    month_id: safeString(value.month_id, '', 128),
    month_name: safeString(value.month_name, '', 128),
    month_index: safeInteger(value.month_index, 0),
    day: Math.max(1, safeInteger(value.day, 1)),
    day_of_year: Math.max(1, safeInteger(value.day_of_year, 1)),
    year_days: Math.max(1, safeInteger(value.year_days, 1)),
    weekday: safeString(value.weekday, '', 64),
    weekday_index: Math.max(0, safeInteger(value.weekday_index, 0)),
    day_index: Math.max(0, safeInteger(value.day_index, 0)),
    day_fraction: Math.min(1, Math.max(0, safeNumber(value.day_fraction, 0))),
  }
}

function normalizeCalendar(value) {
  if (!isObject(value)) return null
  return {
    calendar_id: safeString(value.calendar_id, '', 128),
    display_name: safeString(value.display_name, '', 128),
    epoch_label: safeString(value.epoch_label, '', 128),
    year_days: Math.max(1, safeInteger(value.year_days, 1)),
  }
}

function normalizeSeason(value) {
  if (!isObject(value)) return null
  return {
    season_id: safeString(value.season_id, '', 128),
    display_name: safeString(value.display_name, '', 128),
    description: safeString(value.description, '', 2000),
    color: safeString(value.color, '', 64),
    start_day: Math.max(1, safeInteger(value.start_day, 1)),
    end_day: Math.max(1, safeInteger(value.end_day, 1)),
  }
}

function normalizeCycle(value) {
  if (!isObject(value)) return null
  const kind = Object.hasOwn(CYCLE_KIND_LABELS, value.kind) ? value.kind : 'custom'
  return {
    cycle_id: safeString(value.cycle_id, '', 128),
    display_name: safeString(value.display_name, '', 128),
    kind,
    description: safeString(value.description, '', 2000),
    color: safeString(value.color, '', 64),
    period_days: Math.max(1, safeInteger(value.period_days, 1)),
    duration_days: Math.max(1, safeInteger(value.duration_days, 1)),
    offset_days: Math.max(0, safeInteger(value.offset_days, 0)),
    occurrence: safeInteger(value.occurrence, -1),
    active: value.active === true,
    progress: Math.min(1, Math.max(0, safeNumber(value.progress, 0))),
    next_boundary_day_index: Math.max(0, safeNumber(value.next_boundary_day_index, 0)),
    next_boundary_sim_time: Math.max(0, safeNumber(value.next_boundary_sim_time, 0)),
    next_boundary_trigger: safeString(value.next_boundary_trigger, '', 64),
  }
}

function normalizeMarker(value) {
  if (!isObject(value)) return null
  return {
    trigger: safeString(value.trigger, '', 64),
    sim_time: Math.max(0, safeNumber(value.sim_time, 0)),
    day_index: Math.max(0, safeInteger(value.day_index, 0)),
    cycle_id: safeString(value.cycle_id, '', 128),
    cycle_kind: safeString(value.cycle_kind, '', 64),
    season_id: safeString(value.season_id, '', 128),
    occurrence: safeInteger(value.occurrence, -1),
    date: normalizeDate(value.date),
  }
}

function normalizeAutoPause(value) {
  if (!isObject(value)) return null
  return {
    pause_id: safeString(value.pause_id, '', 128),
    display_name: safeString(value.display_name, '自动暂停', 128),
    reason: safeString(value.reason, '', 2000),
    trigger: safeString(value.trigger, 'condition', 64),
    fired_at: Math.max(0, safeNumber(value.fired_at, 0)),
    fired_count: Math.max(0, safeInteger(value.fired_count, 0)),
    marker: normalizeMarker(value.marker),
    request_count: Array.isArray(value.requests) ? value.requests.length : 1,
  }
}

function normalizePauseDefinition(value) {
  if (!isObject(value)) return null
  return {
    pause_id: safeString(value.pause_id, '', 128),
    display_name: safeString(value.display_name, '', 128),
    trigger: safeString(value.trigger, 'condition', 64),
    reason: safeString(value.reason, '', 2000),
    cooldown_days: Math.max(0, safeNumber(value.cooldown_days, 0)),
    once: value.once === true,
    edge_triggered: value.edge_triggered !== false,
    enabled: value.enabled !== false,
    fired_count: Math.max(0, safeInteger(value.fired_count, 0)),
    last_fired_at: value.last_fired_at == null ? null : Math.max(0, safeNumber(value.last_fired_at, 0)),
  }
}

export function normalizeTimeSystemState(value) {
  if (!isObject(value)) return { ...EMPTY_TIME_SYSTEM_STATE }
  return {
    configured: value.configured === true,
    enabled: value.enabled === true,
    generation: Math.max(0, safeInteger(value.generation, 0)),
    fingerprint: safeString(value.fingerprint, '', 128),
    calendar: normalizeCalendar(value.calendar),
    current_date: normalizeDate(value.current_date),
    current_season: normalizeSeason(value.current_season),
    cycles: Array.isArray(value.cycles) ? value.cycles.map(normalizeCycle).filter(Boolean) : [],
    auto_pause_conditions: Array.isArray(value.auto_pause_conditions)
      ? value.auto_pause_conditions.map(normalizePauseDefinition).filter(Boolean)
      : [],
    recent_auto_pauses: Array.isArray(value.recent_auto_pauses)
      ? value.recent_auto_pauses.map(normalizeAutoPause).filter(Boolean).slice(-20)
      : [],
    auto_pause: normalizeAutoPause(value.auto_pause),
  }
}

export function formatTemporalDate(value) {
  const state = isObject(value) && Object.hasOwn(value, 'current_date')
    ? value
    : normalizeTimeSystemState(value)
  const date = state.current_date
  if (!date) return ''
  const epoch = date.epoch_label ? `${date.epoch_label} ` : ''
  const month = date.month_name || date.month_id || `第${date.month_index + 1}月`
  const weekday = date.weekday ? ` · ${date.weekday}` : ''
  return `${epoch}${date.year}年 ${month}${date.day}日${weekday}`
}

export function formatSeason(value) {
  const season = isObject(value) && Object.hasOwn(value, 'season_id')
    ? value
    : normalizeTimeSystemState(value).current_season
  return season?.display_name || season?.season_id || ''
}

export function activeTemporalCycles(value) {
  const cycles = Array.isArray(value) ? value : normalizeTimeSystemState(value).cycles
  return cycles
    .filter(item => item?.active === true)
    .sort((left, right) => {
      const order = { turn: 0, meeting: 1, task: 2, custom: 3 }
      return (order[left.kind] ?? 9) - (order[right.kind] ?? 9)
        || left.display_name.localeCompare(right.display_name, 'zh-CN')
    })
}

export function formatCycleProgress(value) {
  if (!isObject(value)) return ''
  const kind = CYCLE_KIND_LABELS[value.kind] || CYCLE_KIND_LABELS.custom
  const occurrence = Number.isInteger(value.occurrence) && value.occurrence >= 0
    ? `第${value.occurrence + 1}期`
    : '等待开始'
  const progress = `${Math.round(Math.min(1, Math.max(0, safeNumber(value.progress, 0))) * 100)}%`
  return `${kind} · ${occurrence} · ${progress}`
}

export function boundaryTriggerLabel(trigger) {
  return BOUNDARY_LABELS[trigger] || '时间条件满足'
}

export function formatAutoPauseNotice(value) {
  const pause = normalizeAutoPause(value)
  if (!pause) return null
  const marker = pause.marker
  const date = marker?.date ? formatTemporalDate({ current_date: marker.date }) : ''
  const boundary = boundaryTriggerLabel(marker?.trigger || pause.trigger)
  const target = marker?.cycle_id || marker?.season_id || ''
  return {
    ...pause,
    boundary,
    date,
    detail: [date, boundary, target].filter(Boolean).join(' · '),
  }
}

export { CYCLE_KIND_LABELS, EMPTY_TIME_SYSTEM_STATE }
