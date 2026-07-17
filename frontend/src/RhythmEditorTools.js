import { serializeCausalConditions } from './CausalConditionTools.js'

export const RHYTHM_URGENCIES = [
  { key: 'low', label: '低' },
  { key: 'normal', label: '普通' },
  { key: 'high', label: '高' },
  { key: 'critical', label: '危急' },
]

export const RHYTHM_ISSUE_STATUSES = [
  { key: 'open', label: '待处理' },
  { key: 'monitoring', label: '持续观察' },
]

export const RHYTHM_FINAL_STATUSES = [
  { key: 'resolved', label: '已解决' },
  { key: 'closed', label: '已关闭' },
  { key: 'cancelled', label: '已取消' },
]

const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/
const URGENCY_KEYS = new Set(RHYTHM_URGENCIES.map(item => item.key))

function clone(value) {
  if (value === undefined) return undefined
  if (typeof globalThis.structuredClone === 'function') return globalThis.structuredClone(value)
  return JSON.parse(JSON.stringify(value))
}

function text(value, limit = 2000) {
  return String(value ?? '').trim().slice(0, limit)
}

function uniqueIds(value) {
  const values = Array.isArray(value) ? value : String(value ?? '').split(/[，,\n]/)
  return [...new Set(values.map(item => text(item, 128)).filter(Boolean))]
}

function finiteNumber(value, label, minimum = 0, maximum = 10000) {
  if (typeof value === 'boolean' || value === '' || value === null || value === undefined) throw new Error(label + '不能为空。')
  const result = Number(value)
  if (!Number.isFinite(result) || result < minimum || result > maximum) throw new Error(label + '必须在 ' + minimum + ' 到 ' + maximum + ' 之间。')
  return result
}

function integer(value, label, minimum, maximum) {
  const result = finiteNumber(value, label, minimum, maximum)
  if (!Number.isInteger(result)) throw new Error(label + '必须是整数。')
  return result
}

function validId(value, label, { required = true } = {}) {
  const result = text(value, 128)
  if (!result && !required) return ''
  if (!result || !SAFE_ID.test(result)) throw new Error(label + '只能使用字母、数字、点、下划线或连字符，且必须以字母或数字开头。')
  return result
}

function urgency(value, label) {
  const result = text(value, 16)
  if (!URGENCY_KEYS.has(result)) throw new Error(label + '必须是 low、normal、high 或 critical。')
  return result
}

function normalizeUrgencies(value, maxLevel) {
  const source = Array.isArray(value) && value.length ? value : ['normal', 'high', 'critical', 'critical']
  const result = []
  for (let level = 0; level <= maxLevel; level += 1) result.push(source[level] ?? source[source.length - 1] ?? 'normal')
  return result
}

function normalizeFeedback(value, maxLevel) {
  const source = Array.isArray(value) ? value : []
  return Array.from({ length: maxLevel }, (_, index) => String(source[index] ?? ''))
}

export function createRhythmRule(index = 0) {
  const suffix = Math.max(1, Number(index) + 1)
  return {
    rule_id: 'rhythm_rule_' + suffix,
    display_name: '节奏规则 ' + suffix,
    enabled: true,
    issue_type_ids: [],
    event_types: [],
    priority: 0,
    attention_cost: '',
    max_active: 1,
    cooldown_days: 0,
    mutual_exclusion_group: '',
    allow_during_quiet: false,
    quiet_period_days: 0,
    escalation: {
      enabled: false,
      after_days: 3,
      interval_days: 2,
      max_level: 3,
      urgency_by_level: ['normal', 'high', 'critical', 'critical'],
      feedback_by_level: ['', '', ''],
    },
    deescalation: {
      enabled: false,
      interval_days: 1,
      target_level: 0,
      issue_status: 'monitoring',
      conditions: [],
      feedback: '',
    },
    climax: {
      enabled: false,
      level: 3,
      after_days: 0,
      conditions: [],
      issue_status: 'resolved',
      case_status: 'resolved',
      close_group: true,
      quiet_period_days: 3,
      feedback: '',
    },
  }
}

function normalizeRule(value, index) {
  const source = value && typeof value === 'object' ? clone(value) : {}
  const fallback = createRhythmRule(index)
  const maxLevel = Math.min(16, Math.max(1, Number(source.escalation?.max_level ?? fallback.escalation.max_level) || 3))
  return {
    ...fallback,
    ...source,
    rule_id: text(source.rule_id || fallback.rule_id, 128),
    display_name: String(source.display_name ?? source.rule_id ?? fallback.display_name),
    enabled: source.enabled !== false,
    issue_type_ids: uniqueIds(source.issue_type_ids),
    event_types: uniqueIds(source.event_types),
    attention_cost: source.attention_cost ?? '',
    escalation: {
      ...fallback.escalation,
      ...(source.escalation || {}),
      enabled: Boolean(source.escalation?.enabled),
      max_level: maxLevel,
      urgency_by_level: normalizeUrgencies(source.escalation?.urgency_by_level, maxLevel),
      feedback_by_level: normalizeFeedback(source.escalation?.feedback_by_level, maxLevel),
    },
    deescalation: {
      ...fallback.deescalation,
      ...(source.deescalation || {}),
      enabled: Boolean(source.deescalation?.enabled),
      conditions: clone(source.deescalation?.conditions || []),
    },
    climax: {
      ...fallback.climax,
      ...(source.climax || {}),
      enabled: Boolean(source.climax?.enabled),
      close_group: source.climax?.close_group !== false,
      conditions: clone(source.climax?.conditions || []),
    },
  }
}

export function normalizeRhythmForEditor(value = {}) {
  const source = value && typeof value === 'object' ? clone(value) : {}
  return {
    enabled: Boolean(source.enabled),
    max_active_issues: source.max_active_issues ?? 8,
    attention_budget: source.attention_budget ?? 12,
    attention_recovery_per_day: source.attention_recovery_per_day ?? 4,
    queue_limit: source.queue_limit ?? 128,
    recent_feedback_limit: source.recent_feedback_limit ?? 32,
    quiet_period_days: source.quiet_period_days ?? 0,
    critical_bypasses_quiet: source.critical_bypasses_quiet !== false,
    urgency_costs: {
      low: source.urgency_costs?.low ?? 1,
      normal: source.urgency_costs?.normal ?? 2,
      high: source.urgency_costs?.high ?? 3,
      critical: source.urgency_costs?.critical ?? 5,
    },
    rules: (Array.isArray(source.rules) ? source.rules : []).map(normalizeRule),
  }
}

function addEventType(result, value) {
  for (const item of uniqueIds(value)) if (SAFE_ID.test(item)) result.add(item)
}

export function collectRhythmEventTypes(content = {}) {
  const result = new Set()
  for (const event of content.initial_events || []) addEventType(result, event.event_type)
  for (const event of content.ongoing_events || []) addEventType(result, event.event_type || event.type)
  for (const event of content.random_events || []) addEventType(result, event.event_type || event.type)
  for (const rule of content.causal_event_rules || []) {
    addEventType(result, rule.source_event_types)
    addEventType(result, rule.child_event_type)
  }
  for (const event of content.time_system?.boundary_events || []) addEventType(result, event.event_type)
  for (const event of content.lifecycle?.transitions || []) addEventType(result, event.event_type)
  for (const issueType of content.issue_types || []) addEventType(result, issueType.match?.payload_types)
  return [...result].sort((a, b) => a.localeCompare(b, 'zh-CN'))
}

function serializeRule(rule, index, issueTypeIds) {
  const prefix = '第 ' + (index + 1) + ' 条规则'
  const ruleId = validId(rule.rule_id, prefix + ' ID')
  const displayName = text(rule.display_name, 512)
  if (!displayName) throw new Error(prefix + '的中文显示名不能为空。')
  const issueIds = uniqueIds(rule.issue_type_ids).map((id, itemIndex) => validId(id, prefix + '的第 ' + (itemIndex + 1) + ' 个 Issue 类型 ID'))
  const eventTypes = uniqueIds(rule.event_types).map((id, itemIndex) => validId(id, prefix + '的第 ' + (itemIndex + 1) + ' 个事件类型'))
  if (!issueIds.length && !eventTypes.length) throw new Error(prefix + '至少要匹配一个 Issue 类型或事件类型。')
  for (const issueId of issueIds) if (!issueTypeIds.has(issueId)) throw new Error(prefix + '引用了不存在的 Issue 类型：' + issueId)

  const maxLevel = integer(rule.escalation?.max_level ?? 3, prefix + '的升级最大等级', 1, 16)
  const urgencies = Array.from({ length: maxLevel + 1 }, (_, level) => urgency(rule.escalation?.urgency_by_level?.[level] ?? 'normal', prefix + '第 ' + level + ' 级紧迫度'))
  const feedback = Array.from({ length: maxLevel }, (_, level) => text(rule.escalation?.feedback_by_level?.[level], 2000))
  const deescalationLevel = integer(rule.deescalation?.target_level ?? 0, prefix + '的降级目标等级', 0, 16)
  const climaxLevel = integer(rule.climax?.level ?? Math.min(3, maxLevel), prefix + '的高潮等级', 1, 16)
  if (deescalationLevel > maxLevel) throw new Error(prefix + '的降级目标等级不能高于升级最大等级。')
  if (climaxLevel > maxLevel) throw new Error(prefix + '的高潮等级不能高于升级最大等级。')

  const deescalationStatus = text(rule.deescalation?.issue_status || 'monitoring', 32)
  if (!['open', 'monitoring'].includes(deescalationStatus)) throw new Error(prefix + '的降级 Issue 状态无效。')
  const climaxIssueStatus = text(rule.climax?.issue_status || 'resolved', 32)
  const climaxCaseStatus = text(rule.climax?.case_status || 'resolved', 32)
  if (!RHYTHM_FINAL_STATUSES.some(item => item.key === climaxIssueStatus)) throw new Error(prefix + '的高潮 Issue 状态无效。')
  if (!RHYTHM_FINAL_STATUSES.some(item => item.key === climaxCaseStatus)) throw new Error(prefix + '的高潮 Case 状态无效。')

  return {
    rule_id: ruleId,
    display_name: displayName,
    enabled: rule.enabled !== false,
    issue_type_ids: issueIds,
    event_types: eventTypes,
    priority: integer(rule.priority ?? 0, prefix + '的优先级', -1000, 1000),
    attention_cost: rule.attention_cost === '' || rule.attention_cost === null || rule.attention_cost === undefined ? null : finiteNumber(rule.attention_cost, prefix + '的注意力覆盖成本', 0, 10000),
    max_active: integer(rule.max_active ?? 1, prefix + '的活跃上限', 1, 64),
    cooldown_days: finiteNumber(rule.cooldown_days ?? 0, prefix + '的冷却天数', 0, 36500),
    mutual_exclusion_group: validId(rule.mutual_exclusion_group, prefix + '的互斥组', { required: false }),
    allow_during_quiet: Boolean(rule.allow_during_quiet),
    quiet_period_days: finiteNumber(rule.quiet_period_days ?? 0, prefix + '的规则平静期', 0, 36500),
    escalation: {
      enabled: Boolean(rule.escalation?.enabled),
      after_days: finiteNumber(rule.escalation?.after_days ?? 3, prefix + '的升级起始天数', 0, 36500),
      interval_days: finiteNumber(rule.escalation?.interval_days ?? 2, prefix + '的升级间隔天数', 0.000001, 36500),
      max_level: maxLevel,
      urgency_by_level: urgencies,
      feedback_by_level: feedback,
    },
    deescalation: {
      enabled: Boolean(rule.deescalation?.enabled),
      interval_days: finiteNumber(rule.deescalation?.interval_days ?? 1, prefix + '的降级间隔天数', 0.000001, 36500),
      target_level: deescalationLevel,
      issue_status: deescalationStatus,
      conditions: serializeCausalConditions(rule.deescalation?.conditions || []),
      feedback: text(rule.deescalation?.feedback, 2000),
    },
    climax: {
      enabled: Boolean(rule.climax?.enabled),
      level: climaxLevel,
      after_days: finiteNumber(rule.climax?.after_days ?? 0, prefix + '的高潮延迟天数', 0, 36500),
      conditions: serializeCausalConditions(rule.climax?.conditions || []),
      issue_status: climaxIssueStatus,
      case_status: climaxCaseStatus,
      close_group: rule.climax?.close_group !== false,
      quiet_period_days: finiteNumber(rule.climax?.quiet_period_days ?? 3, prefix + '的高潮后平静期', 0, 36500),
      feedback: text(rule.climax?.feedback, 2000),
    },
  }
}

function buildRhythm(form, content = {}) {
  const source = normalizeRhythmForEditor(form)
  if (source.rules.length > 256) throw new Error('节奏规则最多 256 条。')
  const issueTypeIds = new Set((content.issue_types || []).map(item => text(item.issue_type_id || item.id, 128)).filter(Boolean))
  const rules = source.rules.map((rule, index) => serializeRule(rule, index, issueTypeIds))
  const seen = new Set()
  for (const rule of rules) {
    if (seen.has(rule.rule_id)) throw new Error('节奏规则 ID 重复：' + rule.rule_id)
    seen.add(rule.rule_id)
  }
  return {
    enabled: Boolean(source.enabled),
    max_active_issues: integer(source.max_active_issues, '活跃 Issue 全局上限', 1, 64),
    attention_budget: finiteNumber(source.attention_budget, '注意力预算', 0, 10000),
    attention_recovery_per_day: finiteNumber(source.attention_recovery_per_day, '每日注意力恢复量', 0, 10000),
    queue_limit: integer(source.queue_limit, '排队上限', 1, 2048),
    recent_feedback_limit: integer(source.recent_feedback_limit, '最近反馈数量', 1, 256),
    quiet_period_days: finiteNumber(source.quiet_period_days, '全局平静期', 0, 36500),
    critical_bypasses_quiet: Boolean(source.critical_bypasses_quiet),
    urgency_costs: Object.fromEntries(RHYTHM_URGENCIES.map(item => [item.key, finiteNumber(source.urgency_costs[item.key], item.label + '紧迫度成本', 0, 10000)])),
    rules,
  }
}

export function serializeRhythm(form, content = {}) {
  return buildRhythm(form, content)
}

export function validateRhythm(form, content = {}) {
  try {
    buildRhythm(form, content)
    return { valid: true, errors: [] }
  } catch (error) {
    return { valid: false, errors: [error.message] }
  }
}
