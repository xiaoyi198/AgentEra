import test from 'node:test'
import assert from 'node:assert/strict'

import {
  analyzeDerivedMetricDependencies,
  collectDerivedMetricDependencies,
  createDefaultFormula,
  normalizeFormulaOperation,
  validateDerivedMetricFormula,
  parseJsonObject,
} from './DerivedMetricEditorTools.js'

test('formula defaults cover every structural family without shared references', () => {
  assert.deepEqual(createDefaultFormula('constant'), { op: 'constant', value: 0 })
  assert.deepEqual(createDefaultFormula('divide'), {
    op: 'divide', left: { op: 'constant', value: 0 }, right: { op: 'constant', value: 1 },
  })
  const first = createDefaultFormula('sum')
  const second = createDefaultFormula('sum')
  first.values[0].value = 9
  assert.equal(second.values[0].value, 0)
  assert.deepEqual(normalizeFormulaOperation({ op: 'constant', value: 4 }, 'round'), {
    op: 'round', precision: 0, value: { op: 'constant', value: 0 },
  })
})

test('dependency collection traverses n-ary binary unary and weighted nodes', () => {
  const formula = {
    op: 'sum',
    values: [
      { op: 'derived_metric', metric_id: 'alpha' },
      { op: 'divide', left: { op: 'derived_metric', metric_id: 'beta' }, right: { constant: 2 } },
      { op: 'weighted_sum', terms: [{ weight: 1, value: { op: 'derived_metric', metric_id: 'alpha' } }] },
      { op: 'clamp', min: 0, max: 1, value: { op: 'derived_metric', metric_id: 'gamma' } },
    ],
  }
  assert.deepEqual(collectDerivedMetricDependencies(formula), ['alpha', 'beta', 'gamma'])
})

test('dependency analysis reports self reference missing IDs duplicate IDs and graph cycles', () => {
  const definitions = [
    { metric_id: 'alpha', formula: { op: 'derived_metric', metric_id: 'beta' } },
    { metric_id: 'beta', formula: { op: 'derived_metric', metric_id: 'alpha' } },
    { metric_id: 'duplicate', formula: { constant: 1 } },
    { metric_id: 'duplicate', formula: { constant: 2 } },
  ]
  const draft = {
    metric_id: 'current',
    formula: { op: 'sum', values: [
      { op: 'derived_metric', metric_id: 'current' },
      { op: 'derived_metric', metric_id: 'missing' },
    ] },
  }
  const result = analyzeDerivedMetricDependencies(definitions, draft)
  assert.equal(result.selfReference, true)
  assert.deepEqual(result.missing, ['missing'])
  assert.deepEqual(result.duplicateIds, ['duplicate'])
  assert.ok(result.cycles.some(cycle => cycle.join('>') === 'alpha>beta>alpha'))
  assert.ok(result.cycles.some(cycle => cycle.join('>') === 'current>current'))
})

test('draft replaces same ID for cycle analysis and JSON parser only accepts objects', () => {
  const definitions = [
    { metric_id: 'alpha', formula: { op: 'constant', value: 1 } },
    { metric_id: 'beta', formula: { op: 'derived_metric', metric_id: 'alpha' } },
  ]
  const result = analyzeDerivedMetricDependencies(definitions, {
    metric_id: 'alpha', formula: { op: 'derived_metric', metric_id: 'beta' },
  })
  assert.ok(result.cycles.some(cycle => new Set(cycle).has('alpha') && new Set(cycle).has('beta')))
  assert.deepEqual(parseJsonObject('{"tag":"frontier"}', '筛选器'), { tag: 'frontier' })
  assert.throws(() => parseJsonObject('[]', '筛选器'), /筛选器 必须是 JSON 对象/)
})


test('formula validation reports authoring errors before Registry validation', () => {
  assert.deepEqual(validateDerivedMetricFormula({ source: 'sim_day' }), [])
  assert.deepEqual(validateDerivedMetricFormula({ derived_metric: 'readiness' }), [])
  const errors = validateDerivedMetricFormula({
    op: 'sum',
    values: [
      { op: 'source', source: 'metric', entity_id: '', name: '' },
      { op: 'clamp', min: 10, max: 1, value: { op: 'constant', value: Number.NaN } },
      { op: 'weighted_sum', terms: [{ weight: Number.POSITIVE_INFINITY, value: null }] },
    ],
  })
  assert.ok(errors.some(error => error.includes('必须选择实体')))
  assert.ok(errors.some(error => error.includes('必须填写来源名称')))
  assert.ok(errors.some(error => error.includes('下限不能大于上限')))
  assert.ok(errors.some(error => error.includes('常量必须是有限数值')))
  assert.ok(errors.some(error => error.includes('权重必须是有限数值')))
})
