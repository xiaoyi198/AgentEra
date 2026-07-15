import { normalizeCausalConditionForEditor, serializeCausalConditions } from './CausalConditionTools.js'

export const TEMPORAL_BOUNDARY_TRIGGERS = [
  { key: 'day_start', label: '每日开始' },
  { key: 'week_start', label: '每周开始' },
  { key: 'month_start', label: '每月开始' },
  { key: 'year_start', label: '每年开始' },
  { key: 'season_start', label: '季节开始' },
  { key: 'season_end', label: '季节结束' },
  { key: 'cycle_start', label: '周期开始' },
  { key: 'cycle_end', label: '周期结束' },
]

export const TEMPORAL_PAUSE_TRIGGERS = [
  { key: 'condition', label: '持续条件变为成立' },
  ...TEMPORAL_BOUNDARY_TRIGGERS,
]

export const TEMPORAL_CYCLE_KINDS = [
  { key: 'turn', label: '回合' },
  { key: 'meeting', label: '会议' },
  { key: 'task', label: '任务' },
  { key: 'custom', label: '自定义周期' },
]

const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/
const TRIGGERS = new Set(TEMPORAL_BOUNDARY_TRIGGERS.map(item => item.key))
const PAUSE_TRIGGERS = new Set(TEMPORAL_PAUSE_TRIGGERS.map(item => item.key))
const CYCLE_KINDS = new Set(TEMPORAL_CYCLE_KINDS.map(item => item.key))

function clone(value) {
  return JSON.parse(JSON.stringify(value))
}

function object(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {}
}

function list(value) {
  return Array.isArray(value) ? value : []
}

function text(value, fallback = '') {
  return typeof value === 'string' ? value : value == null ? fallback : String(value)
}

function integer(value, label, minimum, maximum) {
  const number = Number(value)
  if (!Number.isInteger(number) || number < minimum || number > maximum) {
    throw new Error(`${label}必须是 ${minimum}～${maximum} 之间的整数。`)
  }
  return number
}

function finite(value, label, minimum, maximum) {
  const number = Number(value)
  if (!Number.isFinite(number) || number < minimum || number > maximum) {
    throw new Error(`${label}必须是 ${minimum}～${maximum} 之间的有限数字。`)
  }
  return number
}

function requiredText(value, label, limit = 512) {
  const result = text(value).trim()
  if (!result) throw new Error(`${label}不能为空。`)
  if (result.length > limit) throw new Error(`${label}不能超过 ${limit} 个字符。`)
  return result
}

function identifier(value, label, { required = true } = {}) {
  const result = text(value).trim()
  if (!result && !required) return ''
  if (!SAFE_ID.test(result)) throw new Error(`${label}必须以字母或数字开头，并且只能包含字母、数字、点、下划线和连字符。`)
  return result
}

function unique(values, label) {
  if (new Set(values).size !== values.length) throw new Error(`${label}不能重复。`)
}

function parseObjectJson(value, label) {
  let parsed
  try {
    parsed = JSON.parse(text(value, '{}') || '{}')
  } catch {
    throw new Error(`${label}必须是合法 JSON。`)
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error(`${label}必须是 JSON 对象。`)
  if (Object.keys(parsed).length > 128) throw new Error(`${label}最多包含 128 个字段。`)
  return parsed
}

function parseIds(value) {
  const source = Array.isArray(value) ? value : text(value).split(',')
  return [...new Set(source.map(item => text(item).trim()).filter(Boolean))]
}

function defaultMonths() {
  const days = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
  return days.map((count, index) => ({ month_id: `month_${String(index + 1).padStart(2, '0')}`, display_name: `${index + 1}月`, days: count }))
}

export function createDefaultTimeSystem() {
  const months = defaultMonths()
  const weekdays = ['一', '二', '三', '四', '五', '六', '日']
  return {
    enabled: false,
    calendar: {
      calendar_id: 'world_calendar',
      display_name: '世界历',
      epoch_label: '纪元',
      start_year: 1,
      start_month_id: months[0].month_id,
      start_day: 1,
      weekdays,
      start_weekday: weekdays[0],
      months,
    },
    seasons: [],
    cycles: [],
    boundary_events: [],
    auto_pause_conditions: [],
  }
}

export function createTemporalItem(kind, index = 0) {
  const number = index + 1
  if (kind === 'month') return { month_id: `month_${String(number).padStart(2, '0')}`, display_name: `${number}月`, days: 30 }
  if (kind === 'season') return { season_id: `season_${number}`, display_name: `季节 ${number}`, start_day: 1, end_day: 30, description: '', color: '' }
  if (kind === 'cycle') return { cycle_id: `cycle_${number}`, display_name: `周期 ${number}`, kind: 'custom', period_days: 7, duration_days: 7, offset_days: 0, description: '', color: '', enabled: true }
  if (kind === 'boundary_event') return { boundary_event_id: `boundary_${number}`, trigger: 'cycle_start', event_type: 'cycle_notice', severity: 5, target_agents: [], cycle_id: '', season_id: '', month_id: '', day: '', issue_type_id: '', payload: {}, _target_agents_text: '', _payload_text: '{}', enabled: true }
  if (kind === 'auto_pause') return { pause_id: `pause_${number}`, display_name: `自动暂停 ${number}`, trigger: 'condition', reason: '需要玩家处理新的情况', mode: 'all', conditions: [], cycle_id: '', season_id: '', month_id: '', day: '', cooldown_days: 0, once: false, edge_triggered: true, enabled: true }
  throw new Error(`Unknown temporal item kind: ${kind}`)
}

export function normalizeTimeSystemForEditor(value) {
  const defaults = createDefaultTimeSystem()
  const source = object(value)
  const calendarSource = object(source.calendar)
  const rawMonths = list(calendarSource.months)
  const months = (rawMonths.length ? rawMonths : defaults.calendar.months).map(item => ({
    month_id: text(object(item).month_id),
    display_name: text(object(item).display_name),
    days: object(item).days ?? 30,
  }))
  const rawWeekdays = list(calendarSource.weekdays).map(item => text(item)).filter(Boolean)
  const weekdays = rawWeekdays.length ? rawWeekdays : defaults.calendar.weekdays
  const calendar = {
    ...defaults.calendar,
    ...clone(calendarSource),
    months,
    weekdays,
    start_month_id: text(calendarSource.start_month_id, months[0]?.month_id || ''),
    start_weekday: text(calendarSource.start_weekday, weekdays[0] || ''),
  }
  return {
    enabled: typeof source.enabled === 'boolean' ? source.enabled : defaults.enabled,
    calendar,
    seasons: list(source.seasons).map(item => ({ description: '', color: '', ...clone(object(item)) })),
    cycles: list(source.cycles).map(item => ({ kind: 'custom', offset_days: 0, description: '', color: '', enabled: true, ...clone(object(item)) })),
    boundary_events: list(source.boundary_events).map(item => {
      const event = { severity: 5, target_agents: [], payload: {}, enabled: true, ...clone(object(item)) }
      return { ...event, day: event.day ?? '', _target_agents_text: typeof event._target_agents_text === 'string' ? event._target_agents_text : parseIds(event.target_agents).join(', '), _payload_text: typeof event._payload_text === 'string' ? event._payload_text : JSON.stringify(object(event.payload), null, 2) }
    }),
    auto_pause_conditions: list(source.auto_pause_conditions).map(item => {
      const pause = { mode: 'all', conditions: [], cooldown_days: 0, once: false, edge_triggered: true, enabled: true, ...clone(object(item)) }
      return { ...pause, day: pause.day ?? '', conditions: list(pause.conditions).map(normalizeCausalConditionForEditor) }
    }),
  }
}

function serializeCalendar(source) {
  const calendar = object(source)
  const months = list(calendar.months).map((item, index) => ({
    month_id: identifier(object(item).month_id, `第 ${index + 1} 个月份 ID`),
    display_name: requiredText(object(item).display_name, `第 ${index + 1} 个月份名称`, 512),
    days: integer(object(item).days, `第 ${index + 1} 个月份天数`, 1, 3660),
  }))
  if (!months.length || months.length > 64) throw new Error('历法必须包含 1～64 个月份。')
  unique(months.map(item => item.month_id), '月份 ID')
  const weekdays = list(calendar.weekdays).map((item, index) => requiredText(item, `第 ${index + 1} 个星期名称`, 64))
  if (!weekdays.length || weekdays.length > 32) throw new Error('历法必须包含 1～32 个星期名称。')
  unique(weekdays, '星期名称')
  const startMonthId = identifier(calendar.start_month_id, '起始月份')
  const startMonth = months.find(item => item.month_id === startMonthId)
  if (!startMonth) throw new Error('起始月份必须引用月份列表中的 ID。')
  const startWeekday = requiredText(calendar.start_weekday, '起始星期', 64)
  if (!weekdays.includes(startWeekday)) throw new Error('起始星期必须来自星期名称列表。')
  return {
    calendar_id: identifier(calendar.calendar_id, '历法 ID'),
    display_name: requiredText(calendar.display_name, '历法名称'),
    epoch_label: text(calendar.epoch_label).trim().slice(0, 128),
    start_year: integer(calendar.start_year, '起始年份', -1000000, 1000000),
    start_month_id: startMonthId,
    start_day: integer(calendar.start_day, '起始日期', 1, startMonth.days),
    weekdays,
    start_weekday: startWeekday,
    months,
  }
}

function seasonContains(season, day) {
  return season.start_day <= season.end_day
    ? season.start_day <= day && day <= season.end_day
    : day >= season.start_day || day <= season.end_day
}

function serializeSeasons(values, yearDays) {
  const seasons = list(values).map((item, index) => ({
    season_id: identifier(object(item).season_id, `第 ${index + 1} 个季节 ID`),
    display_name: requiredText(object(item).display_name, `第 ${index + 1} 个季节名称`),
    start_day: integer(object(item).start_day, `第 ${index + 1} 个季节开始日`, 1, yearDays),
    end_day: integer(object(item).end_day, `第 ${index + 1} 个季节结束日`, 1, yearDays),
    description: text(object(item).description).trim().slice(0, 4000),
    color: text(object(item).color).trim().slice(0, 64),
  }))
  if (seasons.length > 128) throw new Error('季节最多 128 个。')
  unique(seasons.map(item => item.season_id), '季节 ID')
  for (let day = 1; day <= yearDays; day += 1) {
    if (seasons.filter(season => seasonContains(season, day)).length > 1) throw new Error(`季节范围在年度第 ${day} 天发生重叠。`)
  }
  return seasons
}

function serializeCycles(values) {
  const cycles = list(values).map((item, index) => {
    const source = object(item)
    const period = integer(source.period_days, `第 ${index + 1} 个周期的间隔天数`, 1, 1000000)
    const kind = text(source.kind, 'custom').trim()
    if (!CYCLE_KINDS.has(kind)) throw new Error(`第 ${index + 1} 个周期类型无效。`)
    return {
      cycle_id: identifier(source.cycle_id, `第 ${index + 1} 个周期 ID`),
      display_name: requiredText(source.display_name, `第 ${index + 1} 个周期名称`),
      kind,
      period_days: period,
      duration_days: integer(source.duration_days, `第 ${index + 1} 个周期的持续天数`, 1, period),
      offset_days: integer(source.offset_days ?? 0, `第 ${index + 1} 个周期的起始偏移`, 0, 1000000),
      description: text(source.description).trim().slice(0, 4000),
      color: text(source.color).trim().slice(0, 64),
      enabled: source.enabled !== false,
    }
  })
  if (cycles.length > 256) throw new Error('周期最多 256 个。')
  unique(cycles.map(item => item.cycle_id), '周期 ID')
  return cycles
}

function serializeBoundaryFilter(source, label, catalogs) {
  const cycleId = identifier(source.cycle_id, `${label}周期引用`, { required: false })
  const seasonId = identifier(source.season_id, `${label}季节引用`, { required: false })
  const monthId = identifier(source.month_id, `${label}月份引用`, { required: false })
  if (cycleId && !catalogs.cycleIds.has(cycleId)) throw new Error(`${label}引用了不存在的周期 ${cycleId}。`)
  if (seasonId && !catalogs.seasonIds.has(seasonId)) throw new Error(`${label}引用了不存在的季节 ${seasonId}。`)
  if (monthId && !catalogs.months.has(monthId)) throw new Error(`${label}引用了不存在的月份 ${monthId}。`)
  const day = source.day === '' || source.day == null ? null : integer(source.day, `${label}日期过滤`, 1, monthId ? catalogs.months.get(monthId) : catalogs.yearDays)
  return { cycle_id: cycleId, season_id: seasonId, month_id: monthId, day }
}

function serializeBoundaryEvents(values, catalogs, content) {
  const agentIds = new Set(list(content?.agents).map(item => text(object(item).id).trim()).filter(Boolean))
  const issueIds = new Set(list(content?.issue_types).map(item => text(object(item).issue_type_id).trim()).filter(Boolean))
  const events = list(values).map((item, index) => {
    const source = object(item)
    const label = `第 ${index + 1} 个边界事件`
    const trigger = text(source.trigger, 'day_start').trim()
    if (!TRIGGERS.has(trigger)) throw new Error(`${label}的触发边界无效。`)
    const targetAgents = parseIds(source._target_agents_text ?? source.target_agents).map(agentId => identifier(agentId, `${label}目标 Agent`))
    const missingAgent = targetAgents.find(agentId => agentIds.size && !agentIds.has(agentId))
    if (missingAgent) throw new Error(`${label}引用了不存在的 Agent ${missingAgent}。`)
    const issueTypeId = identifier(source.issue_type_id, `${label} Issue 类型`, { required: false })
    if (issueTypeId && issueIds.size && !issueIds.has(issueTypeId)) throw new Error(`${label}引用了不存在的 Issue 类型 ${issueTypeId}。`)
    return {
      boundary_event_id: identifier(source.boundary_event_id, `${label} ID`),
      trigger,
      ...serializeBoundaryFilter(source, label, catalogs),
      event_type: identifier(source.event_type, `${label}事件类型`),
      severity: integer(source.severity ?? 5, `${label}严重度`, 1, 10),
      target_agents: targetAgents,
      issue_type_id: issueTypeId,
      payload: parseObjectJson(source._payload_text ?? JSON.stringify(object(source.payload)), `${label} Payload`),
      enabled: source.enabled !== false,
    }
  })
  if (events.length > 512) throw new Error('边界事件最多 512 个。')
  unique(events.map(item => item.boundary_event_id), '边界事件 ID')
  return events
}

function serializeAutoPauses(values, catalogs) {
  const pauses = list(values).map((item, index) => {
    const source = object(item)
    const label = `第 ${index + 1} 个自动暂停条件`
    const trigger = text(source.trigger, 'condition').trim()
    if (!PAUSE_TRIGGERS.has(trigger)) throw new Error(`${label}的触发方式无效。`)
    const mode = text(source.mode, 'all').trim()
    if (!['all', 'any'].includes(mode)) throw new Error(`${label}的条件组合方式无效。`)
    const conditions = serializeCausalConditions(source.conditions, { requireOne: trigger === 'condition' })
    return {
      pause_id: identifier(source.pause_id, `${label} ID`),
      display_name: requiredText(source.display_name, `${label}名称`),
      trigger,
      reason: requiredText(source.reason, `${label}中文原因`, 2000),
      mode,
      conditions,
      ...serializeBoundaryFilter(source, label, catalogs),
      cooldown_days: finite(source.cooldown_days ?? 0, `${label}冷却天数`, 0, 1000000),
      once: Boolean(source.once),
      edge_triggered: source.edge_triggered !== false,
      enabled: source.enabled !== false,
    }
  })
  if (pauses.length > 256) throw new Error('自动暂停条件最多 256 个。')
  unique(pauses.map(item => item.pause_id), '自动暂停 ID')
  return pauses
}

export function serializeTimeSystem(value, content = {}) {
  const source = normalizeTimeSystemForEditor(value)
  const calendar = serializeCalendar(source.calendar)
  const yearDays = calendar.months.reduce((sum, month) => sum + month.days, 0)
  const seasons = serializeSeasons(source.seasons, yearDays)
  const cycles = serializeCycles(source.cycles)
  const catalogs = {
    yearDays,
    months: new Map(calendar.months.map(item => [item.month_id, item.days])),
    seasonIds: new Set(seasons.map(item => item.season_id)),
    cycleIds: new Set(cycles.map(item => item.cycle_id)),
  }
  return {
    enabled: Boolean(source.enabled),
    calendar,
    seasons,
    cycles,
    boundary_events: serializeBoundaryEvents(source.boundary_events, catalogs, content),
    auto_pause_conditions: serializeAutoPauses(source.auto_pause_conditions, catalogs),
  }
}

export function summarizeTimeSystem(value) {
  const source = normalizeTimeSystemForEditor(value)
  return {
    enabled: source.enabled,
    months: source.calendar.months.length,
    year_days: source.calendar.months.reduce((sum, item) => sum + (Number(item.days) || 0), 0),
    seasons: source.seasons.length,
    cycles: source.cycles.length,
    boundary_events: source.boundary_events.length,
    auto_pauses: source.auto_pause_conditions.length,
  }
}
