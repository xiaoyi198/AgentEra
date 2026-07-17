const FEEDBACK_KINDS = new Set(['immediate', 'delayed_report', 'case_summary', 'causal_reveal'])

function number(value, fallback = 0) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function safeText(value, limit = 4000) {
  return typeof value === 'string' ? value.trim().slice(0, limit) : ''
}

function normalizeChange(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const result = {}
  for (const key of ['kind', 'entity_id', 'entity_name', 'resource_name', 'item_name', 'metric_id', 'display_name', 'field', 'before', 'after', 'delta', 'direction', 'unit', 'old_status', 'new_status', 'quantity', 'quantity_delta', 'base_value_delta', 'summary', 'reason', 'source_kind', 'source_id']) {
    if (value[key] !== undefined && value[key] !== null) result[key] = value[key]
  }
  return Object.keys(result).length ? result : null
}

export function normalizeOutcomeFeedbackRecord(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const feedbackId = safeText(value.feedback_id, 160)
  const kind = safeText(value.kind, 64)
  if (!feedbackId || !FEEDBACK_KINDS.has(kind)) return null
  const source = value.source && typeof value.source === 'object' && !Array.isArray(value.source) ? value.source : {}
  return {
    feedback_id: feedbackId,
    kind,
    generation: Math.max(0, Math.trunc(number(value.generation))),
    rule_id: safeText(value.rule_id, 128),
    source: {
      kind: safeText(source.kind, 64),
      id: safeText(source.id, 128),
      display_name: safeText(source.display_name, 240),
    },
    title: safeText(value.title, 240),
    summary: safeText(value.summary),
    reason: safeText(value.reason, 2000),
    reveal_text: safeText(value.reveal_text),
    changes: Array.isArray(value.changes) ? value.changes.map(normalizeChange).filter(Boolean).slice(0, 64) : [],
    metric_changes: Array.isArray(value.metric_changes) ? value.metric_changes.map(normalizeChange).filter(Boolean).slice(0, 64) : [],
    created_at: number(value.created_at),
    due_at: number(value.due_at),
    delivered_at: value.delivered_at == null ? null : number(value.delivered_at),
  }
}

export function normalizeOutcomeFeedbackResponse(value) {
  const source = value && typeof value === 'object' && !Array.isArray(value) ? value : {}
  const recent = Array.isArray(source.recent) ? source.recent.map(normalizeOutcomeFeedbackRecord).filter(Boolean) : []
  const pending = Array.isArray(source.pending) ? source.pending.filter(item => item && typeof item === 'object').map(item => ({
    feedback_id: safeText(item.feedback_id, 160),
    kind: safeText(item.kind, 64),
    rule_id: safeText(item.rule_id, 128),
    title: safeText(item.title, 240),
    source: item.source && typeof item.source === 'object' ? {
      kind: safeText(item.source.kind, 64), id: safeText(item.source.id, 128), display_name: safeText(item.source.display_name, 240),
    } : { kind: '', id: '', display_name: '' },
    created_at: number(item.created_at), due_at: number(item.due_at),
  })).filter(item => item.feedback_id) : []
  return {
    configured: source.configured === true,
    enabled: source.enabled === true,
    generation: Math.max(0, Math.trunc(number(source.generation))),
    recent,
    pending,
    recent_count: Math.max(recent.length, Math.trunc(number(source.recent_count, recent.length))),
    pending_count: Math.max(pending.length, Math.trunc(number(source.pending_count, pending.length))),
  }
}

export function mergeOutcomeFeedbackState(current, incoming) {
  const base = normalizeOutcomeFeedbackResponse(current)
  const source = incoming && typeof incoming === 'object' ? incoming : {}
  const delivered = Array.isArray(source.delivered) ? source.delivered.map(normalizeOutcomeFeedbackRecord).filter(Boolean) : []
  if (!delivered.length && Array.isArray(source.recent)) return normalizeOutcomeFeedbackResponse(source)
  const byId = new Map(base.recent.map(item => [item.feedback_id, item]))
  for (const item of delivered) byId.set(item.feedback_id, item)
  const recent = [...byId.values()].sort((a, b) => (b.delivered_at ?? b.created_at) - (a.delivered_at ?? a.created_at)).slice(0, 128)
  return {
    ...base,
    configured: source.configured ?? base.configured,
    enabled: source.enabled ?? base.enabled,
    generation: Number.isFinite(Number(source.generation)) ? Number(source.generation) : base.generation,
    recent,
    recent_count: recent.length,
    pending_count: Number.isFinite(Number(source.pending_count)) ? Number(source.pending_count) : base.pending_count,
  }
}

export function feedbackKindLabel(kind) {
  return ({ immediate: '即时变化', delayed_report: '延迟报告', case_summary: '结案总结', causal_reveal: '因果揭示' })[kind] || '结果反馈'
}

export function describeOutcomeChange(change) {
  if (!change || typeof change !== 'object') return ''
  const name = change.display_name || change.entity_name || change.resource_name || change.item_name || change.metric_id || change.entity_id || change.field || '状态'
  if (change.before !== undefined && change.after !== undefined) return `${name}：${change.before}${change.unit || ''} → ${change.after}${change.unit || ''}`
  if (change.quantity_delta !== undefined) return `${name}：${Number(change.quantity_delta) >= 0 ? '+' : ''}${change.quantity_delta}`
  if (change.old_status || change.new_status) return `${name}：${change.old_status || '未知'} → ${change.new_status || '未知'}`
  return change.summary || name
}
