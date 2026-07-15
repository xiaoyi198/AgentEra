const STATUS = new Set(['ok', 'missing', 'error', 'disabled'])

function text(value, fallback = '', limit = 240) {
  if (typeof value !== 'string') return fallback
  const clean = Array.from(value).filter(character => { const code = character.charCodeAt(0); return code > 31 && code !== 127 }).join('').trim()
  return clean.slice(0, limit) || fallback
}

function finiteOrNull(value) {
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

export function normalizeDerivedMetricResponse(payload) {
  const source = payload && typeof payload === 'object' ? payload : {}
  const metrics = Array.isArray(source.metrics) ? source.metrics.flatMap((item, index) => {
    if (!item || typeof item !== 'object') return []
    const metricId = text(item.metric_id, '', 64)
    if (!metricId) return []
    const status = STATUS.has(item.status) ? item.status : 'error'
    return [{
      metric_id: metricId,
      display_name: text(item.display_name, metricId, 160),
      description: text(item.description, '', 1000),
      unit: text(item.unit, '', 32),
      precision: Math.max(0, Math.min(8, Number.isInteger(Number(item.precision)) ? Number(item.precision) : 2)),
      minimum: finiteOrNull(item.minimum),
      maximum: finiteOrNull(item.maximum),
      dependencies: Array.isArray(item.dependencies) ? item.dependencies.map(value => text(value, '', 64)).filter(Boolean).slice(0, 128) : [],
      status,
      value: status === 'ok' ? finiteOrNull(item.value) : null,
      error_code: status === 'ok' ? '' : text(item.error_code, '', 96),
      order: index,
    }]
  }) : []
  return {
    configured: Boolean(source.configured),
    generation: Number.isFinite(Number(source.generation)) ? Number(source.generation) : 0,
    evaluated_at: Number.isFinite(Number(source.evaluated_at)) ? Number(source.evaluated_at) : 0,
    metric_count: metrics.length,
    metrics,
  }
}

export function formatDerivedMetricValue(metric) {
  if (metric?.status !== 'ok' || !Number.isFinite(metric?.value)) return '—'
  return Number(metric.value).toLocaleString(undefined, {
    minimumFractionDigits: metric.precision,
    maximumFractionDigits: metric.precision,
  })
}

export function derivedMetricStatusLabel(status) {
  return ({ ok: '正常', missing: '数据缺失', error: '计算失败', disabled: '已停用' })[status] || '不可用'
}
