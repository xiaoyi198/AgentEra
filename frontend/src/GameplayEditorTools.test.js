import test from 'node:test'
import assert from 'node:assert/strict'
import { buildGameplayDraft, normalizeGameplayPlayer, suggestPlayerFromContent, validateGameplayPlayer } from './GameplayEditorTools.js'

const content = {
  entities: [{ id: 'station', name: '站点' }],
  actors: [{ actor_id: 'captain_actor', display_name: '站长', identities: [{ identity_id: 'captain' }] }],
  agents: [{ id: 'captain_agent', actor_id: 'captain_actor', name: '站长', role: 'player', location_id: 'station', enabled: true }],
}

test('normalizes absent gameplay into an editable player contract', () => {
  assert.deepEqual(normalizeGameplayPlayer(content), {
    identity_id: '', display_name: '', actor_id: '', controlled_objects: [],
  })
})

test('suggests a direct player Agent and its existing location entity', () => {
  const player = suggestPlayerFromContent(content)
  assert.equal(player.identity_id, 'captain')
  assert.equal(player.actor_id, 'captain_actor')
  assert.deepEqual(player.controlled_objects.map(item => `${item.kind}:${item.id}:${item.mode}`), [
    'entity:station:direct', 'agent:captain_agent:direct',
  ])
})

test('validates references, duplicates and required identity fields', () => {
  const errors = validateGameplayPlayer(content, {
    identity_id: '', display_name: '', actor_id: 'missing_actor',
    controlled_objects: [
      { kind: 'entity', id: 'missing_entity', mode: 'direct' },
      { kind: 'entity', id: 'missing_entity', mode: 'direct' },
    ],
  })
  assert.ok(errors.some(item => item.includes('身份 ID')))
  assert.ok(errors.some(item => item.includes('missing_actor')))
  assert.ok(errors.some(item => item.includes('missing_entity')))
  assert.ok(errors.some(item => item.includes('重复')))
})

test('builds a detached gameplay draft while preserving extension fields', () => {
  const source = { ...content, gameplay: { chapter: 'opening' } }
  const result = buildGameplayDraft(source, {
    identity_id: 'captain', display_name: '站长', actor_id: 'captain_actor',
    controlled_objects: [{ kind: 'agent', id: 'captain_agent', mode: 'direct', display_name: '本人' }],
  })
  assert.equal(result.valid, true)
  assert.equal(result.nextContent.gameplay.chapter, 'opening')
  assert.equal(result.nextContent.gameplay.player.controlled_objects[0].display_name, '本人')
  assert.equal(source.gameplay.player, undefined)
})