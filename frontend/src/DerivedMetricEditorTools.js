export const DERIVED_METRIC_AUDIENCES = [
  ['player', '玩家公开接口'],
  ['ui', '游戏界面'],
  ['agent', 'Agent 提示词'],
  ['rule', '因果规则'],
  ['objective', '目标条件'],
]

export const FORMULA_OPERATIONS = [
  ['constant', '常量'],
  ['source', '直接读取世界数据'],
  ['derived_metric', '引用其他派生指标'],
  ['aggregate', '聚合世界数据'],
  ['add', '相加'],
  ['subtract', '相减'],
  ['multiply', '相乘'],
  ['divide', '相除'],
  ['min', '最小值'],
  ['max', '最大值'],
  ['avg', '平均值'],
  ['sum', '求和'],
  ['weighted_sum', '加权求和'],
  ['clamp', '限制范围'],
  ['round', '四舍五入'],
  ['coalesce', '首个可用值'],
]

export const DIRECT_SOURCES = [
  ['anchored_inventory', '锚定库存总价值'],
  ['metric', '实体指标'],
  ['parameter', '实体参数'],
  ['actor_attribute', 'Actor 属性'],
  ['sim_day', '模拟天数'],
]

export const AGGREGATE_SOURCES = [
  ['entity_count', '实体数量'],
  ['anchored_inventory', '锚定库存价值'],
  ['metric', '实体指标'],
  ['parameter', '实体参数'],
  ['actor_attribute', 'Actor 属性'],
  ['relation', '关系边'],
  ['ongoing_event', '持续事件'],
]

export const AGGREGATE_METHODS = [
  ['sum', '求和'], ['avg', '平均值'], ['min', '最小值'], ['max', '最大值'], ['count', '计数'],
]

export function cloneMetricValue(value) {
  return JSON.parse(JSON.stringify(value))
}

export function createDefaultFormula(op = 'constant') {
  if (op === 'constant') return { op, value: 0 }
  if (op === 'derived_metric') return { op, metric_id: '' }
  if (op === 'source') return { op, source: 'sim_day' }
  if (op === 'aggregate') return { op, source: 'entity_count', aggregate: 'count' }
  if (['add', 'multiply', 'min', 'max', 'avg', 'sum', 'coalesce'].includes(op)) {
    return { op, values: [{ op: 'constant', value: 0 }, { op: 'constant', value: 0 }] }
  }
  if (['subtract', 'divide'].includes(op)) {
    return { op, left: { op: 'constant', value: 0 }, right: { op: 'constant', value: op === 'divide' ? 1 : 0 } }
  }
  if (op === 'weighted_sum') return { op, terms: [{ weight: 1, value: { op: 'constant', value: 0 } }] }
  if (op === 'clamp') return { op, min: 0, max: 100, value: { op: 'constant', value: 0 } }
  if (op === 'round') return { op, precision: 0, value: { op: 'constant', value: 0 } }
  return { op: 'constant', value: 0 }
}

export function normalizeFormulaOperation(node, op) {
  if (node?.op === op) return cloneMetricValue(node)
  return createDefaultFormula(op)
}

export function collectDerivedMetricDependencies(formula, found = new Set()) {
  if (!formula || typeof formula !== 'object' || Array.isArray(formula)) return [...found]
  const op = inferFormulaOperation(formula)
  const dependencyId = formula.metric_id || formula.derived_metric
  if (op === 'derived_metric' && typeof dependencyId === 'string' && dependencyId.trim()) {
    found.add(dependencyId.trim())
  }
  for (const key of ['left', 'right', 'value']) collectDerivedMetricDependencies(formula[key], found)
  for (const child of Array.isArray(formula.values) ? formula.values : []) collectDerivedMetricDependencies(child, found)
  for (const term of Array.isArray(formula.terms) ? formula.terms : []) collectDerivedMetricDependencies(term?.value, found)
  return [...found].sort()
}

function findCycles(graph) {
  const cycles = []
  const visiting = new Set()
  const visited = new Set()
  const stack = []
  const signatures = new Set()
  const visit = id => {
    if (visiting.has(id)) {
      const index = stack.indexOf(id)
      const cycle = [...stack.slice(index), id]
      const signature = [...new Set(cycle.slice(0, -1))].sort().join('|')
      if (!signatures.has(signature)) {
        signatures.add(signature)
        cycles.push(cycle)
      }
      return
    }
    if (visited.has(id)) return
    visiting.add(id)
    stack.push(id)
    for (const next of graph.get(id) || []) if (graph.has(next)) visit(next)
    stack.pop()
    visiting.delete(id)
    visited.add(id)
  }
  for (const id of graph.keys()) visit(id)
  return cycles
}

export function analyzeDerivedMetricDependencies(definitions, draft = null) {
  const source = Array.isArray(definitions) ? definitions : []
  const merged = draft?.metric_id
    ? [...source.filter(item => item?.metric_id !== draft.metric_id), draft]
    : [...source, ...(draft ? [draft] : [])]
  const ids = new Set(merged.map(item => String(item?.metric_id || '').trim()).filter(Boolean))
  const graph = new Map()
  const duplicateIds = []
  const seen = new Set()
  for (const item of merged) {
    const id = String(item?.metric_id || '').trim()
    if (!id) continue
    if (seen.has(id) && !duplicateIds.includes(id)) duplicateIds.push(id)
    seen.add(id)
    graph.set(id, collectDerivedMetricDependencies(item.formula))
  }
  const draftId = String(draft?.metric_id || '').trim()
  const dependencies = collectDerivedMetricDependencies(draft?.formula)
  return {
    dependencies,
    selfReference: Boolean(draftId && dependencies.includes(draftId)),
    missing: dependencies.filter(id => !ids.has(id)),
    cycles: findCycles(graph),
    duplicateIds: duplicateIds.sort(),
  }
}

export function parseJsonObject(text, label = 'JSON') {
  const value = JSON.parse(String(text || '{}'))
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${label} 必须是 JSON 对象`)
  return value
}

export function inferFormulaOperation(formula) {
  if (!formula || typeof formula !== 'object' || Array.isArray(formula)) return ''
  if (typeof formula.op === 'string' && formula.op.trim()) return formula.op.trim().toLowerCase()
  if (Object.hasOwn(formula, 'derived_metric')) return 'derived_metric'
  if (Object.hasOwn(formula, 'source')) return Object.hasOwn(formula, 'aggregate') ? 'aggregate' : 'source'
  if (Object.hasOwn(formula, 'constant')) return 'constant'
  return ''
}

export function validateDerivedMetricFormula(formula) {
  const errors = []
  let nodes = 0
  const finite = value => typeof value === 'number' && Number.isFinite(value)
  const visit = (node, path, depth) => {
    nodes += 1
    if (nodes > 256) { errors.push('公式节点超过 256 个。'); return }
    if (depth > 12) { errors.push(`${path} 超过最大深度 12。`); return }
    if (!node || typeof node !== 'object' || Array.isArray(node)) { errors.push(`${path} 必须是公式节点。`); return }
    const op = inferFormulaOperation(node)
    if (!FORMULA_OPERATIONS.some(([value]) => value === op)) { errors.push(`${path} 缺少有效运算类型。`); return }
    if (op === 'constant') {
      const value = node.value ?? node.constant
      if (!finite(value)) errors.push(`${path} 的常量必须是有限数值。`)
      return
    }
    if (op === 'derived_metric') {
      if (!String(node.metric_id || node.derived_metric || '').trim()) errors.push(`${path} 必须选择引用指标。`)
      return
    }
    if (op === 'source' || op === 'aggregate') {
      const source = String(node.source || '')
      const allowed = (op === 'source' ? DIRECT_SOURCES : AGGREGATE_SOURCES).some(([value]) => value === source)
      if (!allowed) errors.push(`${path} 的数据来源无效。`)
      if (op === 'aggregate' && !AGGREGATE_METHODS.some(([value]) => value === node.aggregate)) errors.push(`${path} 的聚合方法无效。`)
      if (['anchored_inventory', 'metric', 'parameter', 'actor_attribute'].includes(source)) {
        if (op === 'source' && !String(node.entity_id || '').trim()) errors.push(`${path} 直接读取时必须选择实体或 Actor。`)
        if (!String(node.name || '').trim()) errors.push(`${path} 必须填写来源名称。`)
      }
      return
    }
    if (['add', 'multiply', 'min', 'max', 'avg', 'sum', 'coalesce'].includes(op)) {
      if (!Array.isArray(node.values) || !node.values.length) { errors.push(`${path} 至少需要一个子项。`); return }
      node.values.forEach((child, index) => visit(child, `${path}.子项${index + 1}`, depth + 1))
      return
    }
    if (['subtract', 'divide'].includes(op)) {
      visit(node.left, `${path}.左值`, depth + 1)
      visit(node.right, `${path}.右值`, depth + 1)
      return
    }
    if (op === 'weighted_sum') {
      if (!Array.isArray(node.terms) || !node.terms.length) { errors.push(`${path} 至少需要一个加权项。`); return }
      node.terms.forEach((term, index) => {
        if (!finite(term?.weight)) errors.push(`${path}.加权项${index + 1} 的权重必须是有限数值。`)
        visit(term?.value, `${path}.加权项${index + 1}`, depth + 1)
      })
      return
    }
    if (op === 'clamp') {
      if (!finite(node.min) || !finite(node.max)) errors.push(`${path} 的上下限必须是有限数值。`)
      else if (node.min > node.max) errors.push(`${path} 的下限不能大于上限。`)
      visit(node.value, `${path}.待限制值`, depth + 1)
      return
    }
    if (op === 'round') {
      if (!Number.isInteger(node.precision) || node.precision < 0 || node.precision > 8) errors.push(`${path} 的精度必须是 0~8 的整数。`)
      visit(node.value, `${path}.待舍入值`, depth + 1)
    }
  }
  visit(formula, '根公式', 0)
  return [...new Set(errors)]
}
