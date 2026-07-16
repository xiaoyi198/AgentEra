import test from 'node:test'
import assert from 'node:assert/strict'

import { normalizePlayabilityAudit, playabilityBadge } from './PlayabilityAuditTools.js'

const complete = {
  audit_version: '1.0',
  playable: true,
  passed_count: 7,
  total_count: 7,
  checks: [{
    check_id: 'player_surface',
    title: '1. 玩家身份与主界面',
    passed: true,
    status: 'pass',
    summary: '玩家身份与主界面已经声明。',
    gaps: [],
    evidence: [{ kind: 'gameplay', item_id: 'captain', label: '站长' }],
  }],
}

test('normalizes a complete seven-stage audit for the Chinese Studio panel', () => {
  const audit = normalizePlayabilityAudit(complete)
  assert.equal(audit.available, true)
  assert.equal(audit.playable, true)
  assert.equal(audit.percent, 100)
  assert.deepEqual(audit.checks[0].evidence[0], { kind: 'gameplay', itemId: 'captain', label: '站长' })
  assert.deepEqual(playabilityBadge(complete), { tone: 'ok', label: '7/7 可玩性' })
})

test('keeps actionable gaps and derives safe defaults from partial reports', () => {
  const audit = normalizePlayabilityAudit({
    playable: false,
    checks: [{ check_id: 'world_simulation', status: 'gap', gaps: ['增加因果规则。'] }],
  })
  assert.equal(audit.totalCount, 1)
  assert.equal(audit.passedCount, 0)
  assert.equal(audit.checks[0].title, '验收项 1')
  assert.deepEqual(audit.checks[0].gaps, ['增加因果规则。'])
  assert.deepEqual(playabilityBadge(null), { tone: 'bad', label: '未审计' })
})

test('does not display a contradictory playable state when counts are incomplete', () => {
  const audit = normalizePlayabilityAudit({ playable: true, passed_count: 6, total_count: 7, checks: [] })
  assert.equal(audit.playable, false)
  assert.equal(audit.percent, 86)
})
