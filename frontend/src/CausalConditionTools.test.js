import test from 'node:test'
import assert from 'node:assert/strict'

import {
  CAUSAL_AGGREGATE_SOURCES,
  CAUSAL_CONDITION_KINDS,
  CAUSAL_CONDITION_OPERATORS,
  defaultCausalCondition,
  normalizeCausalConditionForEditor,
  serializeCausalCondition,
  serializeCausalConditions,
} from './CausalConditionTools.js'

test('catalog exposes every runtime condition kind, operator, and aggregate source', () => {
  assert.deepEqual(CAUSAL_CONDITION_KINDS.map(item => item.key), [
    'anchored_inventory', 'metric', 'parameter', 'entity_field', 'entity_tag', 'entity_state',
    'actor_attribute', 'actor_state', 'actor_trait', 'actor_membership', 'relationship',
    'ongoing_event', 'event_history', 'time', 'objective_progress', 'aggregate_metric', 'player_knowledge',
  ])
  assert.deepEqual(CAUSAL_CONDITION_OPERATORS.map(item => item.key), [
    'lt', 'lte', 'eq', 'ne', 'gte', 'gt', 'contains', 'not_contains', 'in', 'not_in', 'exists', 'not_exists',
  ])
  assert.deepEqual(CAUSAL_AGGREGATE_SOURCES.map(item => item.key), [
    'entity_count', 'anchored_inventory', 'metric', 'parameter', 'actor_attribute', 'relation', 'ongoing_event',
  ])
})

test('normalization preserves typed values and creates editable text fields', () => {
  assert.deepEqual(normalizeCausalConditionForEditor({
    kind: 'event_history', operator: 'in', value: ['warning', 2, true], filters: { tag: 'frontier' }, window_days: 7,
  }), {
    kind: 'event_history', entity_id: '', name: '', operator: 'in', value: ['warning', 2, true], filters: { tag: 'frontier' },
    target_entity_id: '', field: '', aggregate: '', window_days: 7,
    _value_type: 'list', _value_text: '[\n  "warning",\n  2,\n  true\n]', _filters_text: '{\n  "tag": "frontier"\n}',
  })
})

test('default conditions use available entity actor resource relationship and objective catalogs', () => {
  const catalogs = {
    entities: [{ id: 'city' }, { id: 'fort' }],
    actors: [{ actor_id: 'guard' }],
    resources: [{ name: 'grain', kind: 'asset' }, { name: 'morale', kind: 'metric' }],
    relationshipTypes: [{ relationship_type_id: 'allied' }],
    objectives: [{ objective_id: 'hold_frontier' }],
    eventTypes: ['shortage'], ongoingEvents: [{ event_type: 'relief_operation' }],
  }
  assert.equal(defaultCausalCondition('anchored_inventory', catalogs).name, 'grain')
  assert.equal(defaultCausalCondition('actor_attribute', catalogs).entity_id, 'guard')
  assert.deepEqual(serializeCausalCondition(defaultCausalCondition('relationship', catalogs)), {
    kind: 'relationship', entity_id: 'city', name: 'allied', operator: 'gte', value: 0,
    target_entity_id: 'fort', field: 'strength',
  })
  assert.equal(defaultCausalCondition('objective_progress', catalogs).entity_id, 'hold_frontier')
  assert.equal(defaultCausalCondition('ongoing_event', catalogs).name, 'relief_operation')
})

test('serialization supports string boolean list filters window and existence operators', () => {
  assert.deepEqual(serializeCausalCondition(normalizeCausalConditionForEditor({
    kind: 'entity_state', entity_id: 'city', operator: 'eq', value: 'mobilized',
  })), { kind: 'entity_state', entity_id: 'city', operator: 'eq', value: 'mobilized' })

  assert.deepEqual(serializeCausalCondition({
    kind: 'event_history', entity_id: 'guard', name: 'shortage', field: 'count', operator: 'in',
    _value_type: 'list', _value_text: '[1, 2, true, "many"]', window_days: '30',
    _filters_text: '{"tag":"frontier"}',
  }), {
    kind: 'event_history', entity_id: 'guard', name: 'shortage', operator: 'in', value: [1, 2, true, 'many'],
    field: 'count', window_days: 30, filters: { tag: 'frontier' },
  })

  assert.deepEqual(serializeCausalCondition({
    kind: 'player_knowledge', entity_id: 'city', name: 'secret_route', operator: 'not_exists',
    _value_type: 'text', _value_text: 'discard me', _filters_text: '{}',
  }), { kind: 'player_knowledge', entity_id: 'city', name: 'secret_route', operator: 'not_exists' })
})

test('aggregate serialization validates sources and supplies default sum', () => {
  assert.deepEqual(serializeCausalCondition({
    kind: 'aggregate_metric', field: 'metric', name: 'morale', operator: 'gte',
    _value_type: 'number', _value_text: '12.5', _filters_text: '{"entity_type":"city"}',
  }), {
    kind: 'aggregate_metric', name: 'morale', operator: 'gte', value: 12.5,
    field: 'metric', aggregate: 'sum', filters: { entity_type: 'city' },
  })
  assert.throws(() => serializeCausalCondition({
    kind: 'aggregate_metric', field: 'unknown', operator: 'gte', _value_type: 'number', _value_text: '1', _filters_text: '{}',
  }), /聚合来源无效/)
})

test('serialization rejects incomplete references malformed JSON and unsafe values with indexed Chinese errors', () => {
  assert.throws(() => serializeCausalConditions([{ kind: 'relationship', operator: 'eq', _value_type: 'boolean', _value_text: 'true', _filters_text: '{}' }]), /第 1 个条件：来源实体不能为空/)
  assert.throws(() => serializeCausalCondition({ kind: 'time', operator: 'in', _value_type: 'list', _value_text: '{}', _filters_text: '{}' }), /JSON 数组/)
  assert.throws(() => serializeCausalCondition({ kind: 'time', operator: 'eq', _value_type: 'number', _value_text: 'Infinity', _filters_text: '{}' }), /有限数字/)
  assert.throws(() => serializeCausalCondition({ kind: 'time', operator: 'eq', _value_type: 'text', _value_text: 'x', _filters_text: '[]' }), /JSON 对象/)
  assert.throws(() => serializeCausalConditions([], { requireOne: true }), /至少添加一个完整条件/)
})
