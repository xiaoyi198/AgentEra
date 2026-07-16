import { normalizeCausalConditionForEditor, serializeCausalConditions } from './CausalConditionTools.js'

export const OUTCOME_SOURCE_KINDS = [
  { key: 'action', label: '玩家行动', help: '玩家执行 DLC Action 后产生反馈。' },
  { key: 'decision', label: '玩家决策', help: '玩家处理 Decision Request 后产生反馈。' },
  { key: 'case_closed', label: '事项结案', help: 'Case 关闭时生成总结或后续报告。' },
  { key: 'causal_event', label: '因果事件', help: '因果事件链触发时记录反馈。' },
  { key: 'lifecycle', label: '生命周期', help: '实体生成、转化、退场等生命周期事件。' },
  { key: 'rhythm', label: '节奏导演', help: '升级、降级、高潮或平静期等节奏事件。' },
]

export const OUTCOME_DELIVERIES = [
  { key: 'immediate', label: '即时反馈', help: '来源发生后立即展示。' },
  { key: 'delayed', label: '延迟报告', help: '按模拟天数延迟后交付。' },
  { key: 'causal_reveal', label: '因果揭示', help: '先记录结果，条件满足后才揭示隐藏因果。' },
]

const SOURCE_KEYS = new Set(OUTCOME_SOURCE_KINDS.map(item => item.key))
const DELIVERY_KEYS = new Set(OUTCOME_DELIVERIES.map(item => item.key))
const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/

function list(value) { return Array.isArray(value) ? value : [] }
function text(value) { return String(value ?? '').trim() }
function bool(value, fallback = false) { return value == null ? fallback : value === true }
function numericText(value, fallback) { return Number.isFinite(Number(value)) ? String(value) : String(fallback) }
function uniqueTextList(value) { return [...new Set(list(value).map(text).filter(Boolean))] }

export function defaultOutcomeFeedbackRule(index = 0) {
  return {
    rule_id: `feedback_rule_${index + 1}`,
    display_name: '新的结果反馈规则',
    enabled: true,
    priority: '0',
    source_kinds: ['action'],
    delivery: 'immediate',
    action_ids: [],
    issue_type_ids: [],
    event_types: [],
    metric_ids: [],
    delay_days: '0',
    conditions: [],
    title: '',
    summary: '',
    reason: '',
    reveal_text: '',
    create_issue_type_id: '',
    attach_evidence: false,
  }
}

export function normalizeOutcomeFeedbackForEditor(value) {
  const source = value && typeof value === 'object' && !Array.isArray(value) ? value : {}
  const immediate = source.immediate && typeof source.immediate === 'object' ? source.immediate : {}
  const caseSummaries = source.case_summaries && typeof source.case_summaries === 'object' ? source.case_summaries : {}
  return {
    enabled: bool(source.enabled, true),
    recent_limit: numericText(source.recent_limit, 128),
    pending_limit: numericText(source.pending_limit, 256),
    receipt_limit: numericText(source.receipt_limit, 2048),
    metric_delta_epsilon: numericText(source.metric_delta_epsilon, 0.01),
    immediate: {
      enabled: bool(immediate.enabled, true),
      include_command_changes: bool(immediate.include_command_changes, true),
      include_metric_changes: bool(immediate.include_metric_changes, true),
      max_changes: numericText(immediate.max_changes, 12),
      max_metric_changes: numericText(immediate.max_metric_changes, 8),
    },
    case_summaries: {
      enabled: bool(caseSummaries.enabled, true),
      include_decisions: bool(caseSummaries.include_decisions, true),
      include_changes: bool(caseSummaries.include_changes, true),
      include_metric_changes: bool(caseSummaries.include_metric_changes, true),
    },
    rules: list(source.rules).map((rule, index) => {
      const base = defaultOutcomeFeedbackRule(index)
      const item = rule && typeof rule === 'object' ? rule : {}
      return {
        ...base,
        ...item,
        rule_id: text(item.rule_id),
        display_name: text(item.display_name),
        enabled: bool(item.enabled, true),
        priority: numericText(item.priority, 0),
        source_kinds: uniqueTextList(item.source_kinds),
        delivery: text(item.delivery) || 'delayed',
        action_ids: uniqueTextList(item.action_ids),
        issue_type_ids: uniqueTextList(item.issue_type_ids),
        event_types: uniqueTextList(item.event_types),
        metric_ids: uniqueTextList(item.metric_ids),
        delay_days: numericText(item.delay_days, 0),
        conditions: list(item.conditions).map(normalizeCausalConditionForEditor),
        title: text(item.title),
        summary: text(item.summary),
        reason: text(item.reason),
        reveal_text: text(item.reveal_text),
        create_issue_type_id: text(item.create_issue_type_id),
        attach_evidence: bool(item.attach_evidence, false),
      }
    }),
  }
}

function integer(value, label, minimum, maximum) {
  const parsed = Number(value)
  if (!Number.isInteger(parsed) || parsed < minimum || parsed > maximum) throw new Error(`${label} 必须是 ${minimum}～${maximum} 的整数。`)
  return parsed
}

function number(value, label, minimum, maximum) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed) || parsed < minimum || parsed > maximum) throw new Error(`${label} 必须是 ${minimum}～${maximum} 的有限数值。`)
  return parsed
}

function validateId(value, label, required = true) {
  const result = text(value)
  if (required && !result) throw new Error(`${label}不能为空。`)
  if (result && !SAFE_ID.test(result)) throw new Error(`${label}只能使用字母、数字、点、下划线和连字符，并且必须以字母或数字开头。`)
  return result
}

function validatedIds(value, label) { return uniqueTextList(value).map(item => validateId(item, label)) }

export function serializeOutcomeFeedbackEditor(value) {
  const form = normalizeOutcomeFeedbackForEditor(value)
  const seen = new Set()
  const rules = form.rules.map((rule, index) => {
    const prefix = `规则 #${index + 1}`
    const ruleId = validateId(rule.rule_id, `${prefix} 的规则 ID`)
    if (seen.has(ruleId)) throw new Error(`规则 ID「${ruleId}」重复。`)
    seen.add(ruleId)
    const displayName = text(rule.display_name)
    if (!displayName) throw new Error(`${prefix} 的中文显示名不能为空。`)
    if (displayName.length > 160) throw new Error(`${prefix} 的中文显示名不能超过 160 个字符。`)
    const sourceKinds = uniqueTextList(rule.source_kinds)
    if (!sourceKinds.length || sourceKinds.some(kind => !SOURCE_KEYS.has(kind))) throw new Error(`${prefix} 至少选择一种合法来源。`)
    const delivery = text(rule.delivery)
    if (!DELIVERY_KEYS.has(delivery)) throw new Error(`${prefix} 的交付方式无效。`)
    const revealText = text(rule.reveal_text)
    if (delivery === 'causal_reveal' && !revealText) throw new Error(`${prefix} 使用因果揭示时必须填写揭示文本。`)
    return {
      rule_id: ruleId,
      display_name: displayName,
      enabled: rule.enabled === true,
      priority: integer(rule.priority, `${prefix} 优先级`, -10000, 10000),
      source_kinds: sourceKinds,
      delivery,
      action_ids: validatedIds(rule.action_ids, `${prefix} 行动引用`),
      issue_type_ids: validatedIds(rule.issue_type_ids, `${prefix} Issue 类型引用`),
      event_types: validatedIds(rule.event_types, `${prefix} 事件类型引用`),
      metric_ids: validatedIds(rule.metric_ids, `${prefix} 派生指标引用`),
      delay_days: number(rule.delay_days, `${prefix} 延迟天数`, 0, 36500),
      conditions: serializeCausalConditions(rule.conditions),
      title: text(rule.title),
      summary: text(rule.summary),
      reason: text(rule.reason),
      reveal_text: revealText,
      create_issue_type_id: validateId(rule.create_issue_type_id, `${prefix} 后续 Issue 类型`, false),
      attach_evidence: rule.attach_evidence === true,
    }
  })
  return {
    enabled: form.enabled === true,
    recent_limit: integer(form.recent_limit, '最近反馈保留上限', 1, 1024),
    pending_limit: integer(form.pending_limit, '待交付反馈上限', 1, 4096),
    receipt_limit: integer(form.receipt_limit, '幂等回执上限', 1, 16384),
    metric_delta_epsilon: number(form.metric_delta_epsilon, '指标变化最小阈值', 0, 1e12),
    immediate: {
      enabled: form.immediate.enabled === true,
      include_command_changes: form.immediate.include_command_changes === true,
      include_metric_changes: form.immediate.include_metric_changes === true,
      max_changes: integer(form.immediate.max_changes, '即时命令变化上限', 0, 64),
      max_metric_changes: integer(form.immediate.max_metric_changes, '即时指标变化上限', 0, 64),
    },
    case_summaries: {
      enabled: form.case_summaries.enabled === true,
      include_decisions: form.case_summaries.include_decisions === true,
      include_changes: form.case_summaries.include_changes === true,
      include_metric_changes: form.case_summaries.include_metric_changes === true,
    },
    rules,
  }
}

function addCatalog(map, id, label) {
  const key = text(id)
  if (!key || map.has(key)) return
  map.set(key, { id: key, label: text(label) || key })
}

export function buildOutcomeFeedbackCatalogs(content = {}) {
  const actions = new Map(); const issues = new Map(); const metrics = new Map(); const events = new Map()
  for (const item of list(content.actions)) addCatalog(actions, item.action_id, item.display_name || item.name)
  for (const item of list(content.issue_types)) addCatalog(issues, item.issue_type_id, item.display_name || item.name)
  for (const item of list(content.derived_metrics)) addCatalog(metrics, item.metric_id, item.display_name || item.name)
  for (const section of ['initial_events', 'ongoing_events', 'random_events']) {
    for (const item of list(content[section])) addCatalog(events, item.event_type || item.type || item.event_id, item.display_name || item.name || item.title)
  }
  for (const item of list(content.causal_event_rules)) {
    for (const eventType of uniqueTextList(item.source_event_types)) if (eventType !== '*') addCatalog(events, eventType, eventType)
    addCatalog(events, item.child_event_type, item.child_event_type)
  }
  for (const item of list(content.lifecycle?.rules)) for (const eventType of uniqueTextList(item.event_types)) addCatalog(events, eventType, eventType)
  for (const item of list(content.rhythm?.rules)) for (const eventType of uniqueTextList(item.event_types)) addCatalog(events, eventType, eventType)
  return { actions: [...actions.values()], issueTypes: [...issues.values()], metrics: [...metrics.values()], eventTypes: [...events.values()] }
}
