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
    'ongoing_event', 'event_history', 'time', 'objective_progress', 'aggregate_metric', 'world_truth', 'actor_belief',
    'player_knowledge', 'knowledge_confidence', 'knowledge_freshness', 'knowledge_secrecy', 'investigation', 'phase',
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
  assert.equal(defaultCausalCondition('actor_belief', catalogs).target_entity_id, 'guard')
  assert.equal(defaultCausalCondition('knowledge_confidence', catalogs)._knowledge_layer, 'player_knowledge')
  assert.equal(defaultCausalCondition('investigation', catalogs).field, 'status')
  assert.equal(defaultCausalCondition('phase', catalogs).field, 'phase_id')
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

  assert.deepEqual(serializeCausalCondition({
    kind: 'phase', field: 'metadata.chapter', operator: 'eq',
    _value_type: 'number', _value_text: '2', _filters_text: '{}',
  }), { kind: 'phase', field: 'metadata.chapter', operator: 'eq', value: 2 })
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
  assert.throws(() => serializeCausalCondition({ kind: 'phase', field: 'chapter', operator: 'eq', _value_type: 'number', _value_text: '2', _filters_text: '{}' }), /phase_id 或 metadata\.xxx/)
  assert.throws(() => serializeCausalConditions([], { requireOne: true }), /至少添加一个完整条件/)
})


test('three-layer knowledge conditions serialize holders metadata selectors and investigations', () => {
  assert.deepEqual(serializeCausalCondition({
    kind: 'world_truth', entity_id: 'fort', name: 'route_status', field: 'metadata.version', operator: 'gte',
    _value_type: 'number', _value_text: '2', _filters_text: '{}',
  }), { kind: 'world_truth', entity_id: 'fort', name: 'route_status', operator: 'gte', value: 2, field: 'metadata.version' })

  assert.deepEqual(serializeCausalCondition({
    kind: 'actor_belief', entity_id: 'fort', name: 'route_status', target_entity_id: 'guard', operator: 'eq',
    _value_type: 'boolean', _value_text: 'false', _filters_text: '{}',
  }), { kind: 'actor_belief', entity_id: 'fort', name: 'route_status', target_entity_id: 'guard', operator: 'eq', value: false })

  assert.deepEqual(serializeCausalCondition({
    kind: 'knowledge_confidence', entity_id: 'fort', name: 'route_status', target_entity_id: 'guard', operator: 'gte',
    _value_type: 'number', _value_text: '0.6', _knowledge_layer: 'actor_belief', _filters_text: '{"source":"briefing"}',
  }), {
    kind: 'knowledge_confidence', entity_id: 'fort', name: 'route_status', target_entity_id: 'guard', operator: 'gte', value: 0.6,
    filters: { source: 'briefing', layer: 'actor_belief' },
  })

  assert.deepEqual(serializeCausalCondition({
    kind: 'knowledge_freshness', name: 'route_status', operator: 'eq', _value_type: 'text', _value_text: 'fresh',
    _knowledge_layer: 'world_truth', _filters_text: '{}',
  }), { kind: 'knowledge_freshness', name: 'route_status', operator: 'eq', value: 'fresh', filters: { layer: 'world_truth' } })

  assert.deepEqual(serializeCausalCondition({
    kind: 'knowledge_secrecy', name: 'route_status', operator: 'in', _value_type: 'list', _value_text: '["secret","top_secret"]',
    _knowledge_layer: 'player_knowledge', target_entity_id: 'player', _filters_text: '{}',
  }), { kind: 'knowledge_secrecy', name: 'route_status', target_entity_id: 'player', operator: 'in', value: ['secret', 'top_secret'], filters: { layer: 'player_knowledge' } })

  assert.deepEqual(serializeCausalCondition({
    kind: 'investigation', entity_id: 'verify_route', target_entity_id: 'player', field: 'progress', operator: 'gte',
    _value_type: 'number', _value_text: '2', _filters_text: '{}',
  }), { kind: 'investigation', entity_id: 'verify_route', target_entity_id: 'player', field: 'progress', operator: 'gte', value: 2 })
})

test('actor belief is not misclassified as an Actor component and invalid knowledge references fail clearly', () => {
  assert.doesNotThrow(() => serializeCausalCondition({
    kind: 'actor_belief', name: 'route_status', target_entity_id: 'guard', operator: 'exists', _filters_text: '{}',
  }))
  assert.throws(() => serializeCausalCondition({ kind: 'actor_belief', name: 'route_status', operator: 'exists', _filters_text: '{}' }), /holder/)
  assert.throws(() => serializeCausalCondition({ kind: 'knowledge_confidence', name: 'route_status', operator: 'gte', _value_type: 'number', _value_text: '0.5', _knowledge_layer: 'actor_belief', _filters_text: '{}' }), /holder/)
  assert.throws(() => serializeCausalCondition({ kind: 'knowledge_freshness', name: '', operator: 'eq', _value_type: 'text', _value_text: 'fresh', _knowledge_layer: 'world_truth', _filters_text: '{}' }), /ID/)
  assert.throws(() => serializeCausalCondition({ kind: 'knowledge_secrecy', name: 'route', operator: 'eq', _value_type: 'text', _value_text: 'secret', _knowledge_layer: 'invalid', _filters_text: '{}' }), /知识层/)
  assert.throws(() => serializeCausalCondition({ kind: 'investigation', entity_id: 'verify', field: 'unknown', operator: 'eq', _value_type: 'text', _value_text: 'active', _filters_text: '{}' }), /调查字段/)
})
