import test from 'node:test'
import assert from 'node:assert/strict'
import { buildQuickCreateDraft, parseQuickAgents, parseQuickEntities, summarizeQuickCreate } from './QuickCreateTools.js'

const base = {
  entities: [{ id: 'hq', name: '旧总部', type: 'location' }],
  actors: [{ actor_id: 'existing_actor', display_name: '旧角色' }],
  agents: [{ id: 'existing', actor_id: 'existing_actor', name: '旧 Agent' }],
}

test('batch parser accepts Chinese pipe-delimited entity and Agent rows', () => {
  const entities = parseQuickEntities('# 说明\noutpost | 前哨站 | location | 警戒 | 边境地点')
  const agents = parseQuickAgents('scout | 侦察员 | scout_actor | outpost | specialist | 负责侦察 | person')
  assert.equal(entities.errors.length, 0)
  assert.deepEqual(entities.rows[0], {
    line: 2,
    id: 'outpost',
    name: '前哨站',
    type: 'location',
    current_status: '警戒',
    description: '边境地点',
  })
  assert.equal(agents.errors.length, 0)
  assert.equal(agents.rows[0].actor_id, 'scout_actor')
  assert.equal(agents.rows[0].entity_id, 'outpost')
})

test('quick creation atomically adds entities, Actors and Agents', () => {
  const result = buildQuickCreateDraft(base, {
    entitiesText: 'outpost | 前哨站 | location | 警戒 | 边境地点',
    agentsText: 'scout | 侦察员 | scout_actor | outpost | specialist | 负责侦察 | person',
  })
  assert.equal(result.valid, true)
  assert.equal(result.changed, true)
  assert.equal(result.counts.entities.added, 1)
  assert.equal(result.counts.actors.added, 1)
  assert.equal(result.counts.agents.added, 1)
  assert.equal(result.nextContent.actors.at(-1).metadata.created_by, 'dlc_studio_quick_create')
  assert.equal(result.nextContent.agents.at(-1).actor_id, 'scout_actor')
  assert.match(summarizeQuickCreate(result), /实体 \+1/)
  assert.equal(base.entities.length, 1)
})

test('quick creation rejects missing location references before changing content', () => {
  const result = buildQuickCreateDraft(base, {
    agentsText: 'scout | 侦察员 | scout_actor | missing_place | specialist',
  })
  assert.equal(result.valid, false)
  assert.match(result.errors[0].message, /missing_place/)
  assert.deepEqual(result.nextContent, base)
})

test('skip policy keeps an Agent and Actor bundle atomic on either collision', () => {
  const result = buildQuickCreateDraft(base, {
    agentsText: 'new_agent | 新名字 | existing_actor | hq | specialist',
    conflictPolicy: 'skip',
  })
  assert.equal(result.valid, true)
  assert.equal(result.changed, false)
  assert.equal(result.counts.actors.skipped, 1)
  assert.equal(result.counts.agents.skipped, 1)
  assert.equal(result.nextContent.agents.some(item => item.id === 'new_agent'), false)
})

test('replace policy replaces conflicts and preserves stable references', () => {
  const result = buildQuickCreateDraft(base, {
    entitiesText: 'hq | 新总部 | location | 运转 | 新说明',
    agentsText: 'existing | 新 Agent | existing_actor | hq | player | 玩家代理',
    conflictPolicy: 'replace',
  })
  assert.equal(result.valid, true)
  assert.equal(result.counts.entities.replaced, 1)
  assert.equal(result.counts.actors.replaced, 1)
  assert.equal(result.counts.agents.replaced, 1)
  assert.equal(result.nextContent.entities[0].name, '新总部')
  assert.equal(result.nextContent.agents[0].actor_id, 'existing_actor')
})