import test from 'node:test'
import assert from 'node:assert/strict'
import {
  branchGeneratedIds,
  buildGameplayBranchDraft,
  createGameplayBranchForm,
  summarizeGameplayBranch,
  validateGameplayBranchForm,
} from './GameplayBranchTools.js'

const base = {
  schema_version: '1.0',
  resources: [],
  inventory: [],
  entity_values: [],
  entities: [{ id: 'station', name: '中继站', type: 'location' }],
  agents: [
    { id: 'commander', actor_id: 'commander_actor', role: 'player', enabled: true },
    { id: 'engineer', actor_id: 'engineer_actor', role: 'specialist', enabled: true },
  ],
  actors: [
    { actor_id: 'commander_actor', display_name: '指挥官', capability_ids: [] },
    { actor_id: 'engineer_actor', display_name: '工程师', capability_ids: ['existing_capability'], metadata: { keep: true } },
  ],
  gameplay: { player: { controlled_objects: [{ kind: 'entity', id: 'station', mode: 'direct' }, { kind: 'agent', id: 'commander', mode: 'direct' }] } },
  actions: [],
  issue_types: [],
  initial_events: [],
  causal_event_rules: [],
  capabilities: [],
  rhythm: { enabled: false, attention_budget: 20, rules: [{ rule_id: 'existing_rhythm', display_name: '旧节奏' }] },
  outcome_feedback: { enabled: false, recent_limit: 64, rules: [{ rule_id: 'existing_feedback', display_name: '旧反馈' }] },
}

function form(overrides = {}) {
  return {
    ...createGameplayBranchForm(base),
    prefix: 'station_crisis',
    branchName: '中继站危机分支',
    primaryResourceName: '维护配额',
    secondaryResourceName: '应急储备',
    benefitMetricName: '结构稳定度',
    tradeoffMetricName: '补给通行度',
    ...overrides,
  }
}

test('branch form suggests controlled target, player receiver and a responder Agent', () => {
  const value = createGameplayBranchForm(base)
  assert.equal(value.targetEntityId, 'station')
  assert.equal(value.playerAgentId, 'commander')
  assert.equal(value.playerActorId, 'commander_actor')
  assert.equal(value.responderAgentId, 'engineer')
  assert.equal(value.actorId, 'engineer_actor')
})

test('gameplay branch generator creates a complete reversible delayed-consequence bundle', () => {
  const result = buildGameplayBranchDraft(base, form())
  const ids = branchGeneratedIds(form())
  assert.equal(result.valid, true)
  assert.equal(result.changed, true)
  assert.equal(result.nextContent.resources.length, 4)
  assert.equal(result.nextContent.inventory.length, 2)
  assert.equal(result.nextContent.entity_values.length, 2)
  assert.equal(result.nextContent.actions.length, 2)
  assert.equal(result.nextContent.issue_types.length, 2)
  assert.equal(result.nextContent.initial_events.length, 1)
  assert.equal(result.nextContent.causal_event_rules.length, 1)
  assert.equal(result.nextContent.capabilities.length, 2)

  const assets = result.nextContent.resources.filter(item => item.kind === 'asset')
  assert.deepEqual(assets.map(item => item.anchor_rate), [0.5, 0.5])
  assert.equal(assets[1].metadata.parity_with, '维护配额')
  assert.deepEqual(result.nextContent.inventory.map(item => item.quantity), [200, 160])

  const mainAction = result.nextContent.actions.find(item => item.action_id === ids.mainActionId)
  assert.deepEqual(mainAction.costs.map(item => item.base_value), [12, 10])
  assert.deepEqual(mainAction.permissions.actor_ids, ['commander_actor'])
  assert.equal(mainAction.result.results.some(item => item.mode === 'wake_agent' && item.agent_id === 'engineer'), true)
  assert.equal(mainAction.result.results.find(item => item.mode === 'emit_event').event.event_type, ids.committedEventType)

  const delayed = result.nextContent.causal_event_rules[0]
  assert.equal(delayed.delay_days, 1)
  assert.equal(delayed.source_event_types[0], ids.committedEventType)
  assert.equal(delayed.world_effects.find(item => item.effect_type === 'create_issue').issue_type_id, ids.followupIssueId)
  assert.equal(result.nextContent.issue_types[0].decision_requests[0].options[0].action_id, ids.mainActionId)
  assert.equal(result.nextContent.issue_types[1].decision_requests[0].options[0].action_id, ids.recoveryActionId)

  const actor = result.nextContent.actors.find(item => item.actor_id === 'engineer_actor')
  assert.deepEqual(actor.capability_ids, ['existing_capability', ids.mainCapabilityId, ids.recoveryCapabilityId])
  assert.equal(actor.metadata.keep, true)
  assert.equal(result.nextContent.rhythm.enabled, true)
  assert.equal(result.nextContent.rhythm.attention_budget, 20)
  assert.equal(result.nextContent.rhythm.rules.some(item => item.rule_id === ids.rhythmRuleId), true)
  assert.equal(result.nextContent.outcome_feedback.enabled, true)
  assert.equal(result.nextContent.outcome_feedback.recent_limit, 64)
  assert.equal(result.nextContent.outcome_feedback.rules.filter(item => item.rule_id.startsWith('station_crisis_')).length, 3)
  assert.match(summarizeGameplayBranch(result), /Action 2/)
  assert.equal(base.actions.length, 0)
})

test('reject conflict policy keeps the complete branch atomic', () => {
  const first = buildGameplayBranchDraft(base, form())
  const second = buildGameplayBranchDraft(first.nextContent, form())
  assert.equal(second.valid, false)
  assert.equal(second.changed, false)
  assert.match(second.errors[0], /同 ID/)
  assert.deepEqual(second.nextContent, first.nextContent)
})

test('replace policy replaces generated declarations without duplicating them', () => {
  const first = buildGameplayBranchDraft(base, form())
  const changed = form({ mainActionName: '立即封锁', mainBenefitGain: 22 })
  const second = buildGameplayBranchDraft(first.nextContent, changed, { conflictPolicy: 'replace' })
  assert.equal(second.valid, true)
  assert.equal(second.nextContent.actions.length, 2)
  assert.equal(second.counts.actions.replaced, 2)
  assert.equal(second.nextContent.actions[0].display_name, '立即封锁')
  assert.equal(second.nextContent.actions[0].parameters[0].default, 22)
  assert.match(second.warnings.at(-1), /整套覆盖/)
})

test('branch validation explains broken references and controlled-only risks in Chinese', () => {
  const invalid = validateGameplayBranchForm(base, form({ actorId: 'missing_actor', targetEntityId: 'missing_place' }))
  assert.match(invalid.errors.join('\n'), /missing_actor/)
  assert.match(invalid.errors.join('\n'), /missing_place/)

  const uncontrolledBase = { ...base, gameplay: { player: { controlled_objects: [{ kind: 'agent', id: 'commander' }] } } }
  const warning = validateGameplayBranchForm(uncontrolledBase, form())
  assert.match(warning.warnings.join('\n'), /controlled_only/)
})
