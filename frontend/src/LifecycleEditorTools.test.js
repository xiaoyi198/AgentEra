import assert from 'node:assert/strict'
import test from 'node:test'

import {
  createDefaultLifecycle,
  createLifecycleItem,
  normalizeLifecycleForEditor,
  serializeLifecycle,
  summarizeLifecycle,
} from './LifecycleEditorTools.js'

test('normalizer creates editable JSON fields without mutating lifecycle source', () => {
  const source = {
    enabled: true,
    actor_templates: [{
      template_id: 'patrol_officer',
      display_name: '巡防员',
      actor: { actor_type: 'person', attributes: { command: 7 }, states: { ready: true } },
      entity: { entity_type: 'person', name: '巡防员实体' },
      agent: { source_agent_id: 'warden', system_prompt: '守卫边境' },
    }],
  }
  const normalized = normalizeLifecycleForEditor(source)
  normalized.actor_templates[0]._actor_type = 'officer'
  assert.equal(source.actor_templates[0].actor.actor_type, 'person')
  assert.match(normalized.actor_templates[0]._actor_attributes_text, /command/)
  assert.equal(normalized.actor_templates[0]._agent_enabled, true)
})

test('serializer preserves visual actor, agent, position, transition and operation semantics', () => {
  const form = createDefaultLifecycle()
  form.enabled = true
  const template = createLifecycleItem('actor_template', 0)
  template.template_id = 'patrol_officer'
  template.display_name = '动态巡防员'
  template._actor_display_name = '巡防员'
  template._actor_attributes_text = '{"command":7}'
  template._entity_enabled = true
  template._entity_name = '巡防员实体'
  template._agent_enabled = true
  template._agent_source_id = 'warden'
  template._agent_display_name = '巡防 Agent'
  template._agent_system_prompt = '维护边境秩序'
  form.actor_templates.push(template)

  const position = createLifecycleItem('position', 0, { organizations: ['frontier_watch'] })
  position.position_id = 'watch_captain'
  position.display_name = '巡防队长'
  position._candidates_text = 'existing_actor'
  position.scores = [{ source: 'attribute', name: 'command', weight: 2, direction: 'desc' }]
  form.positions.push(position)

  const transition = createLifecycleItem('transition', 0, { templates: ['patrol_officer'] })
  transition.transition_id = 'spawn_patrol'
  transition.display_name = '补充巡防员'
  transition.trigger = 'cycle_start'
  transition.cycle_id = 'command_turn'
  transition.operations = [
    { ...createLifecycleItem('operation', 0, { templates: ['patrol_officer'] }), template_id: 'patrol_officer', actor_id_pattern: 'patrol_{sequence}' },
    { ...createLifecycleItem('operation', 1), operation: 'appoint_position', position_id: 'watch_captain', actor_id: '{actor_id}', replace: true, primary: true },
  ]
  transition._feedback_enabled = true
  transition._feedback_message = '{actor_id} 已加入巡防队。'
  form.transitions.push(transition)

  const result = serializeLifecycle(form, {
    agents: [{ id: 'warden' }],
    time_system: { cycles: [{ cycle_id: 'command_turn' }], calendar: { months: [] }, seasons: [] },
  })
  assert.equal(result.actor_templates[0].agent.source_agent_id, 'warden')
  assert.deepEqual(result.actor_templates[0].actor.attributes, { command: 7 })
  assert.equal(result.positions[0].scores[0].name, 'command')
  assert.equal(result.transitions[0].operations[0].actor_id_pattern, 'patrol_{sequence}')
  assert.equal(result.transitions[0].operations[1].actor_id, '{actor_id}')
  assert.match(result.transitions[0].feedback.message, /actor_id/)
})

test('serializer rejects missing source agent, template references and condition-only transitions without conditions', () => {
  const form = createDefaultLifecycle()
  const template = createLifecycleItem('actor_template', 0)
  template._agent_enabled = true
  template._agent_source_id = 'missing'
  form.actor_templates.push(template)
  assert.throws(() => serializeLifecycle(form, { agents: [{ id: 'warden' }] }), /不存在的来源 Agent/)

  template._agent_enabled = false
  const transition = createLifecycleItem('transition', 0, { templates: ['missing_template'] })
  transition.operations[0].template_id = 'missing_template'
  form.transitions.push(transition)
  assert.throws(() => serializeLifecycle(form), /不存在的 Actor 模板/)

  transition.operations[0].template_id = template.template_id
  assert.throws(() => serializeLifecycle(form), /至少添加一个.*条件/)
})

test('serializer supports split and merge organization payloads', () => {
  const form = createDefaultLifecycle()
  const transition = createLifecycleItem('transition', 0)
  transition.trigger = 'manual'
  transition.operations = [
    { operation: 'split_organization', source_organization_id: 'old_watch', _destinations_text: '[{"organization_id":"east_watch","actor_ids":["a1"]}]', remove_source: false },
    { operation: 'merge_organizations', _source_organizations_text: 'east_watch, west_watch', target_organization_id: 'unified_watch', _target_text: '{"name":"统一巡防署"}', _all_actors: true, role: 'member', remove_sources: true },
  ]
  form.transitions.push(transition)
  const result = serializeLifecycle(form)
  assert.equal(result.transitions[0].operations[0].destinations[0].organization_id, 'east_watch')
  assert.equal('actor_ids' in result.transitions[0].operations[1], false)
  assert.deepEqual(result.transitions[0].operations[1].source_organization_ids, ['east_watch', 'west_watch'])
})

test('summary reports authoring density', () => {
  const form = createDefaultLifecycle()
  const template = createLifecycleItem('actor_template', 0)
  template._agent_enabled = true
  form.actor_templates.push(template)
  form.positions.push(createLifecycleItem('position', 0))
  form.transitions.push(createLifecycleItem('transition', 0, { templates: [template.template_id] }))
  assert.deepEqual(summarizeLifecycle(form), { enabled: false, actor_templates: 1, dynamic_agents: 1, positions: 1, transitions: 1, operations: 1 })
})
