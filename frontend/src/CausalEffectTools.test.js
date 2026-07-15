import test from 'node:test'
import assert from 'node:assert/strict'

import {
  WORLD_EFFECT_TYPES,
  defaultWorldEffect,
  normalizeWorldEffectForEditor,
  serializeWorldEffect,
  serializeWorldEffects,
} from './CausalEffectTools.js'

const catalogs = {
  entities: [{ id: 'city' }, { id: 'fort' }],
  resources: [{ name: 'silver', kind: 'asset', anchor_rate: 1 }, { name: 'morale', kind: 'metric' }],
  agents: [{ id: 'governor' }],
  actors: [{ actor_id: 'guard' }],
  relationshipTypes: [{ relationship_type_id: 'allied' }],
  issueTypes: [{ issue_type_id: 'security_report' }],
  objectives: [{ objective_id: 'hold_frontier' }],
  actions: [{ action_id: 'mobilize' }],
}

test('effect catalog exposes all backend effect types with Chinese authoring metadata', () => {
  assert.deepEqual(WORLD_EFFECT_TYPES.map(item => item.key), [
    'adjust_resource', 'set_resource', 'update_status', 'create_ongoing_event', 'transfer_anchored_value',
    'create_entity', 'move_entity', 'transform_entity', 'remove_entity', 'enable_agent', 'disable_agent',
    'modify_relationship', 'modify_membership', 'create_issue', 'declare_world_truth', 'set_actor_belief',
    'reveal_information', 'propagate_knowledge', 'start_investigation', 'advance_investigation',
    'advance_objective', 'unlock_action', 'switch_phase',
  ])
  assert.equal(WORLD_EFFECT_TYPES.every(item => item.label && item.group && item.description), true)
})

test('defaults use DLC catalogs and keep create-entity IDs authorable', () => {
  assert.deepEqual(defaultWorldEffect('adjust_resource', catalogs), {
    effect_type: 'adjust_resource', entity_id: 'city', resource_name: 'silver', quantity_delta: 0, reason: '',
  })
  assert.equal(defaultWorldEffect('create_entity', catalogs).entity_id, '')
  assert.equal(defaultWorldEffect('enable_agent', catalogs).agent_id, 'governor')
  assert.equal(defaultWorldEffect('modify_membership', catalogs).actor_id, 'guard')
  assert.equal(defaultWorldEffect('create_issue', catalogs).issue_type_id, 'security_report')
  assert.equal(defaultWorldEffect('set_actor_belief', catalogs).holder_id, 'guard')
  assert.equal(defaultWorldEffect('reveal_information', catalogs).holder_id, 'player')
  assert.equal(defaultWorldEffect('start_investigation', catalogs).investigator_id, 'player')
  assert.equal(defaultWorldEffect('advance_objective', catalogs).objective_id, 'hold_frontier')
  assert.equal(defaultWorldEffect('unlock_action', catalogs).action_id, 'mobilize')
})

test('normalization creates editable JSON text for every structured effect field', () => {
  assert.deepEqual(normalizeWorldEffectForEditor({ effect_type: 'create_entity', metadata: { tags: ['new'] }, resources: { silver: 4 } }), {
    effect_type: 'create_entity', metadata: { tags: ['new'] }, resources: { silver: 4 },
    _metadata_text: '{\n  "tags": [\n    "new"\n  ]\n}', _resources_text: '{\n  "silver": 4\n}',
  })
  const reveal = normalizeWorldEffectForEditor({ effect_type: 'reveal_information', value: ['route', true], metadata: { secret: false } })
  assert.equal(reveal._value_text, '[\n  "route",\n  true\n]')
  assert.equal(reveal._metadata_text, '{\n  "secret": false\n}')
  assert.equal(reveal._visibility_scopes_text, '[]')
  assert.equal(reveal._distortion_text, '{}')
  const investigation = normalizeWorldEffectForEditor({ effect_type: 'start_investigation', result_policy: { source_layer: 'world_truth', target_layer: 'player_knowledge', target_holder_id: 'player', metadata: { reviewed: true } } })
  assert.equal(investigation._use_result_policy, true)
  assert.equal(investigation._result_source_layer, 'world_truth')
  assert.equal(investigation._result_target_holder_id, 'player')
  assert.equal(investigation._result_policy_text, '{\n  "metadata": {\n    "reviewed": true\n  }\n}')
})

test('serialization covers entity agent relationship issue knowledge objective action and phase effects', () => {
  assert.deepEqual(serializeWorldEffect({
    effect_type: 'create_entity', entity_id: '前线营地', entity_type: 'location', name: '前线营地',
    description: '', current_status: 'active', _metadata_text: '{"tags":["frontier"]}', _resources_text: '{"silver":2}', reason: '事件生成',
  }), {
    effect_type: 'create_entity', entity_id: '前线营地', entity_type: 'location', name: '前线营地', description: '',
    current_status: 'active', metadata: { tags: ['frontier'] }, resources: { silver: 2 }, reason: '事件生成',
  })
  assert.deepEqual(serializeWorldEffect({ effect_type: 'move_entity', entity_id: '前线营地', x: '2.5', y: '', region: '北境', location_id: '', reason: '' }), {
    effect_type: 'move_entity', entity_id: '前线营地', x: 2.5, region: '北境',
  })
  assert.deepEqual(serializeWorldEffect({ effect_type: 'disable_agent', agent_id: 'governor', actor_id: 'guard', _metadata_text: '{"temporary":true}' }), {
    effect_type: 'disable_agent', agent_id: 'governor', actor_id: 'guard', metadata: { temporary: true },
  })
  assert.deepEqual(serializeWorldEffect({
    effect_type: 'modify_relationship', operation: 'upsert', parent_id: 'city', child_id: 'fort', rel_type: 'allied',
    strength: '75', visibility: 'public', starts_in_days: '1', duration_days: '30', _values_text: '{"trust":0.8}', note: '共同防守', _metadata_text: '{}',
  }), {
    effect_type: 'modify_relationship', operation: 'upsert', parent_id: 'city', child_id: 'fort', rel_type: 'allied',
    strength: 75, visibility: 'public', starts_in_days: 1, duration_days: 30, values: { trust: 0.8 }, note: '共同防守', metadata: {},
  })
  assert.deepEqual(serializeWorldEffect({
    effect_type: 'create_issue', issue_type_id: 'security_report', _source_text: '{"kind":"event","entity_id":"fort"}',
    title: '边防告警', summary: '需要决策', _details_text: '{"level":3}', _evidence_text: '[{"kind":"report"}]', urgency: 'high', deadline_days: '2', _metadata_text: '{}',
  }), {
    effect_type: 'create_issue', issue_type_id: 'security_report', source: { kind: 'event', entity_id: 'fort' }, title: '边防告警',
    summary: '需要决策', details: { level: 3 }, evidence: [{ kind: 'report' }], urgency: 'high', metadata: {}, deadline_days: 2,
  })
  assert.deepEqual(serializeWorldEffect({ effect_type: 'reveal_information', entity_id: 'fort', knowledge_id: 'secret_route', _value_text: '42', source: 'scout', confidence: '0.8', _metadata_text: '{}' }), {
    effect_type: 'reveal_information', entity_id: 'fort', knowledge_id: 'secret_route', value: 42, source: 'scout', confidence: 0.8,
    secrecy_level: 'public', visibility_scopes: [], distortion: {}, status: 'active', metadata: {}, holder_id: 'player',
  })
  assert.deepEqual(serializeWorldEffect({ effect_type: 'advance_objective', objective_id: 'hold_frontier', mode: 'set', progress: '0.75', status: 'active' }), {
    effect_type: 'advance_objective', objective_id: 'hold_frontier', mode: 'set', progress: 0.75, status: 'active',
  })
  assert.deepEqual(serializeWorldEffect({ effect_type: 'unlock_action', action_id: 'mobilize', scope_id: 'fort' }), {
    effect_type: 'unlock_action', action_id: 'mobilize', scope_id: 'fort',
  })
  assert.deepEqual(serializeWorldEffect({ effect_type: 'switch_phase', phase_id: 'counterattack', _metadata_text: '{"chapter":2}' }), {
    effect_type: 'switch_phase', phase_id: 'counterattack', metadata: { chapter: 2 },
  })
})

test('serialization strips remove-only fields and rejects incomplete or malformed effects with indexed Chinese errors', () => {
  assert.deepEqual(serializeWorldEffect({
    effect_type: 'modify_membership', operation: 'remove', actor_id: 'guard', organization_id: 'city',
    role: 'commander', status: 'active', _metadata_text: '{"discard":true}', reason: '撤职',
  }), { effect_type: 'modify_membership', operation: 'remove', actor_id: 'guard', organization_id: 'city', reason: '撤职' })
  assert.throws(() => serializeWorldEffect({ effect_type: 'move_entity', entity_id: 'city', x: '', y: '', region: '', location_id: '' }), /至少要填写/)
  assert.throws(() => serializeWorldEffect({ effect_type: 'transform_entity', entity_id: 'city', _metadata_patch_text: '{}' }), /至少要填写/)
  assert.throws(() => serializeWorldEffect({ effect_type: 'reveal_information', knowledge_id: 'secret', _value_text: '{', confidence: 1, _metadata_text: '{}' }), /不是有效 JSON/)
  assert.throws(() => serializeWorldEffects([{ effect_type: 'remove_entity', entity_id: '' }]), /第 1 个效果：实体 ID不能为空/)
  assert.throws(() => serializeWorldEffects(new Array(33).fill({ effect_type: 'remove_entity', entity_id: 'city' })), /最多只能配置 32 项/)
})


test('knowledge records propagation and investigations serialize the complete three-layer contract', () => {
  assert.deepEqual(serializeWorldEffect({
    effect_type: 'declare_world_truth', entity_id: 'fort', knowledge_id: 'route_status', _value_text: '{"open":true}',
    source: 'sensor', confidence: '0.95', stale_after_days: '3', expires_in_days: '10', secrecy_level: 'secret',
    status: 'active', _visibility_scopes_text: '["rules"]', _distortion_text: '{}', _metadata_text: '{"version":2}',
  }), {
    effect_type: 'declare_world_truth', entity_id: 'fort', knowledge_id: 'route_status', value: { open: true }, source: 'sensor',
    confidence: 0.95, secrecy_level: 'secret', visibility_scopes: ['rules'], distortion: {}, status: 'active',
    metadata: { version: 2 }, stale_after_days: 3, expires_in_days: 10,
  })
  assert.equal(serializeWorldEffect({
    effect_type: 'set_actor_belief', holder_id: 'guard', knowledge_id: 'route_status', _value_text: 'false',
    _visibility_scopes_text: '[]', _distortion_text: '{"bias":"rumor"}', _metadata_text: '{}',
  }).holder_id, 'guard')

  assert.deepEqual(serializeWorldEffect({
    effect_type: 'propagate_knowledge', source_layer: 'world_truth', target_layer: 'actor_belief', entity_id: 'fort',
    knowledge_id: 'route_status', source_holder_id: '', target_holder_id: 'guard', confidence_multiplier: '0.7',
    source: 'briefing', secrecy_level: 'restricted', _use_value_override: true, _value_override_text: '{"open":"unknown"}',
    _distortion_text: '{"omitted":["origin"]}', _metadata_text: '{}',
  }), {
    effect_type: 'propagate_knowledge', source_layer: 'world_truth', target_layer: 'actor_belief', entity_id: 'fort',
    knowledge_id: 'route_status', source_holder_id: '', target_holder_id: 'guard', confidence_multiplier: 0.7,
    source: 'briefing', distortion: { omitted: ['origin'] }, metadata: {}, value_override: { open: 'unknown' }, secrecy_level: 'restricted',
  })

  const start = normalizeWorldEffectForEditor({
    effect_type: 'start_investigation', investigation_id: 'verify_route', entity_id: 'fort', knowledge_id: 'route_status',
    investigator_layer: 'player_knowledge', investigator_id: 'player', required_progress: 2, evidence: [{ kind: 'lead' }],
    result_policy: { source_layer: 'world_truth', target_layer: 'player_knowledge', target_holder_id: 'player', confidence_multiplier: 0.9 },
    metadata: { category: 'verification' },
  })
  assert.deepEqual(serializeWorldEffect(start), {
    effect_type: 'start_investigation', investigation_id: 'verify_route', entity_id: 'fort', knowledge_id: 'route_status',
    investigator_layer: 'player_knowledge', investigator_id: 'player', required_progress: 2, evidence: [{ kind: 'lead' }],
    result_policy: { source_layer: 'world_truth', target_layer: 'player_knowledge', target_holder_id: 'player', confidence_multiplier: 0.9, source_holder_id: '', distortion: {} },
    metadata: { category: 'verification' },
  })
  assert.deepEqual(serializeWorldEffect({ effect_type: 'advance_investigation', investigation_id: 'verify_route', mode: 'set', progress: '2', status: 'completed', _evidence_text: '[{"kind":"confirmation"}]' }), {
    effect_type: 'advance_investigation', investigation_id: 'verify_route', mode: 'set', progress: 2, evidence: [{ kind: 'confirmation' }], status: 'completed',
  })
})

test('knowledge authoring rejects unsafe layers holders freshness privacy and JSON with Chinese errors', () => {
  assert.throws(() => serializeWorldEffect({ effect_type: 'set_actor_belief', knowledge_id: 'route', holder_id: '', _value_text: 'true', _visibility_scopes_text: '[]', _distortion_text: '{}', _metadata_text: '{}' }), /holder ID/)
  assert.throws(() => serializeWorldEffect({ effect_type: 'reveal_information', knowledge_id: 'route', confidence: 2, _value_text: 'true', _visibility_scopes_text: '[]', _distortion_text: '{}', _metadata_text: '{}' }), /置信度/)
  assert.throws(() => serializeWorldEffect({ effect_type: 'declare_world_truth', knowledge_id: 'route', secrecy_level: 'unknown', _value_text: 'true', _visibility_scopes_text: '[]', _distortion_text: '{}', _metadata_text: '{}' }), /保密级别/)
  assert.throws(() => serializeWorldEffect({ effect_type: 'propagate_knowledge', source_layer: 'invalid', target_layer: 'player_knowledge', knowledge_id: 'route', target_holder_id: 'player', _distortion_text: '{}', _metadata_text: '{}' }), /来源知识层/)
  assert.throws(() => serializeWorldEffect({ effect_type: 'advance_investigation', investigation_id: 'verify', mode: 'adjust', amount: -1, _evidence_text: '[]' }), /调查进度增量/)
  assert.throws(() => serializeWorldEffect({ effect_type: 'advance_investigation', investigation_id: 'verify', mode: 'adjust', amount: 1, status: 'unknown', _evidence_text: '[]' }), /调查状态/)
})
