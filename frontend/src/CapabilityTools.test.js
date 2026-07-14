import test from 'node:test'
import assert from 'node:assert/strict'

import {
  EMPTY_CAPABILITY_STATE,
  buildCapabilityInvocation,
  capabilityFormDefaults,
  normalizeCapabilityResponse,
} from './CapabilityTools.js'

const capability = {
  capability_id: 'frontier_operation',
  handler: 'world_action',
  default_urgency: 'urgent',
  parameters: [
    { name: 'days', type: 'integer', default: 2 },
    { name: 'ratio', type: 'number' },
    { name: 'verified', type: 'boolean' },
    { name: 'mode', type: 'string', default: 'safe' },
  ],
}

test('capability response normalization keeps only enabled definitions', () => {
  assert.deepEqual(normalizeCapabilityResponse(null), EMPTY_CAPABILITY_STATE)
  assert.deepEqual(normalizeCapabilityResponse({
    configured: true,
    generation: 5,
    fingerprint: 'abc',
    audience: 'player',
    capabilities: [capability, { capability_id: 'disabled', enabled: false }, null],
  }), {
    configured: true,
    generation: 5,
    fingerprint: 'abc',
    audience: 'player',
    capabilities: [capability],
  })
})

test('capability form defaults preserve parameter defaults and boolean shape', () => {
  assert.deepEqual(capabilityFormDefaults(capability), {
    content: '',
    targets: '',
    urgency: 'urgent',
    parameters: { days: 2, ratio: '', verified: false, mode: 'safe' },
  })
})

test('player invocation normalizes targets parameter types and gameplay identity', () => {
  const originalNow = Date.now
  const originalRandom = Math.random
  Date.now = () => 1234
  Math.random = () => 0.5
  try {
    const payload = buildCapabilityInvocation(capability, {
      content: '  patrol the ridge  ',
      targets: 'guard\uFF0C station,guard',
      urgency: 'critical',
      parameters: { days: '3', ratio: '0.75', verified: true, mode: 'fast' },
    }, {
      player: {
        identity_id: 'commander',
        actor_id: 'player_actor',
        controlled_objects: [{ id: 'frontier_station', kind: 'entity' }],
      },
    }, 8)

    assert.equal(payload.invocation_id, 'ui_1234_8')
    assert.equal(payload.expected_generation, 8)
    assert.equal(payload.source_id, 'player_actor')
    assert.equal(payload.actor_id, 'player_actor')
    assert.equal(payload.control_object_id, 'frontier_station')
    assert.equal(payload.content, 'patrol the ridge')
    assert.equal(payload.target_id, 'guard')
    assert.deepEqual(payload.target_ids, ['guard', 'station', 'guard'])
    assert.deepEqual(payload.parameters, { days: 3, ratio: 0.75, verified: true, mode: 'fast' })
    assert.equal(payload.urgency, 'critical')
  } finally {
    Date.now = originalNow
    Math.random = originalRandom
  }
})
