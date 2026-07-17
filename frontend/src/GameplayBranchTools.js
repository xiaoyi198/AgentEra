const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._-]*$/u

function cloneJson(value) {
  return JSON.parse(JSON.stringify(value ?? {}))
}

function list(value) {
  return Array.isArray(value) ? value : []
}

function clean(value) {
  return String(value ?? '').trim()
}

function finite(value, fallback = 0) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function unique(values) {
  return [...new Set(values.filter(Boolean))]
}

function sectionKey(section, item) {
  if (section === 'resources') return clean(item?.name)
  if (section === 'inventory') return clean(item?.entity_id) + '\u0000' + clean(item?.item_name)
  if (section === 'entity_values') return clean(item?.entity_id) + '\u0000' + clean(item?.name)
  if (section === 'actions') return clean(item?.action_id)
  if (section === 'issue_types') return clean(item?.issue_type_id)
  if (section === 'initial_events') return clean(item?.event_id)
  if (section === 'causal_event_rules') return clean(item?.rule_id)
  if (section === 'capabilities') return clean(item?.capability_id)
  return ''
}

function mergeItems(current, generated, section, policy, count) {
  const next = list(current).map(cloneJson)
  for (const item of generated) {
    const key = sectionKey(section, item)
    const index = next.findIndex(existing => sectionKey(section, existing) === key)
    if (index < 0) {
      next.push(item)
      count.added += 1
    } else if (policy === 'replace') {
      next[index] = item
      count.replaced += 1
    }
  }
  return next
}

function rhythmRuleKey(item) {
  return clean(item?.rule_id)
}

function feedbackRuleKey(item) {
  return clean(item?.rule_id)
}

function conflictLabels(content, bundle) {
  const labels = {
    resources: '资源/指标',
    inventory: '库存',
    entity_values: '实体指标值',
    actions: 'Action',
    issue_types: 'Issue 类型',
    initial_events: '初始事件',
    causal_event_rules: '因果规则',
    capabilities: 'Capability',
  }
  const conflicts = []
  for (const [section, generated] of Object.entries(bundle.sections)) {
    const existing = new Set(list(content?.[section]).map(item => sectionKey(section, item)))
    for (const item of generated) {
      const key = sectionKey(section, item)
      if (key && existing.has(key)) conflicts.push(labels[section] + '“' + key.replace('\u0000', ' / ') + '”')
    }
  }
  const rhythmIds = new Set(list(content?.rhythm?.rules).map(rhythmRuleKey))
  for (const rule of bundle.rhythmRules) if (rhythmIds.has(rule.rule_id)) conflicts.push('节奏规则“' + rule.rule_id + '”')
  const feedbackIds = new Set(list(content?.outcome_feedback?.rules).map(feedbackRuleKey))
  for (const rule of bundle.feedbackRules) if (feedbackIds.has(rule.rule_id)) conflicts.push('结果反馈规则“' + rule.rule_id + '”')
  return conflicts
}

function baseRhythm(value) {
  const source = value && typeof value === 'object' && !Array.isArray(value) ? cloneJson(value) : {}
  return {
    max_active_issues: source.max_active_issues ?? 8,
    attention_budget: source.attention_budget ?? 12,
    attention_recovery_per_day: source.attention_recovery_per_day ?? 4,
    queue_limit: source.queue_limit ?? 128,
    recent_feedback_limit: source.recent_feedback_limit ?? 32,
    quiet_period_days: source.quiet_period_days ?? 0,
    critical_bypasses_quiet: source.critical_bypasses_quiet !== false,
    urgency_costs: {
      low: source.urgency_costs?.low ?? 1,
      normal: source.urgency_costs?.normal ?? 2,
      high: source.urgency_costs?.high ?? 3,
      critical: source.urgency_costs?.critical ?? 5,
    },
    ...source,
    enabled: true,
    rules: list(source.rules),
  }
}

function baseFeedback(value) {
  const source = value && typeof value === 'object' && !Array.isArray(value) ? cloneJson(value) : {}
  return {
    recent_limit: source.recent_limit ?? 128,
    pending_limit: source.pending_limit ?? 256,
    receipt_limit: source.receipt_limit ?? 2048,
    metric_delta_epsilon: source.metric_delta_epsilon ?? 0.1,
    immediate: {
      enabled: source.immediate?.enabled !== false,
      include_command_changes: source.immediate?.include_command_changes !== false,
      include_metric_changes: source.immediate?.include_metric_changes !== false,
      max_changes: source.immediate?.max_changes ?? 12,
      max_metric_changes: source.immediate?.max_metric_changes ?? 8,
    },
    case_summaries: {
      enabled: source.case_summaries?.enabled !== false,
      include_decisions: source.case_summaries?.include_decisions !== false,
      include_changes: source.case_summaries?.include_changes !== false,
      include_metric_changes: source.case_summaries?.include_metric_changes !== false,
    },
    ...source,
    enabled: true,
    rules: list(source.rules),
  }
}

export function createGameplayBranchForm(content = {}) {
  const entities = list(content.entities)
  const agents = list(content.agents)
  const playerControls = list(content?.gameplay?.player?.controlled_objects)
  const controlledEntity = playerControls.find(item => item?.kind === 'entity')?.id
  const controlledAgent = playerControls.find(item => item?.kind === 'agent')?.id
  const playerAgent = agents.find(item => clean(item?.id) === clean(controlledAgent))
    || agents.find(item => item?.role === 'player' && item?.enabled !== false)
    || agents.find(item => item?.enabled !== false)
    || agents[0]
  const responder = agents.find(item => item !== playerAgent && item?.enabled !== false) || playerAgent || agents[0]
  const target = entities.find(item => clean(item?.id) === clean(controlledEntity)) || entities[0]
  let serial = 1
  const usedActionIds = new Set(list(content.actions).map(item => clean(item?.action_id)))
  while (usedActionIds.has('gameplay_branch_' + serial + '_commit')) serial += 1
  return {
    prefix: 'gameplay_branch_' + serial,
    branchName: '高风险处置分支',
    targetEntityId: clean(target?.id),
    playerAgentId: clean(playerAgent?.id),
    playerActorId: clean(content?.gameplay?.player?.actor_id || playerAgent?.actor_id || playerAgent?.id),
    responderAgentId: clean(responder?.id),
    actorId: clean(responder?.actor_id || responder?.id),
    primaryResourceName: '行动配额',
    primaryResourceUnit: '点',
    primaryInitial: 200,
    secondaryResourceName: '支援储备',
    secondaryResourceUnit: '份',
    secondaryInitial: 160,
    anchorRate: 0.5,
    benefitMetricName: '稳定度',
    benefitInitial: 55,
    tradeoffMetricName: '通行度',
    tradeoffInitial: 85,
    mainActionName: '执行紧急处置',
    recoveryActionName: '恢复受控通道',
    openingIssueName: '高风险处置请求',
    followupIssueName: '处置后的延迟压力',
    mainPrimaryCost: 12,
    mainSecondaryCost: 10,
    recoveryPrimaryCost: 6,
    recoverySecondaryCost: 4,
    mainBenefitGain: 18,
    immediateTradeoffLoss: 20,
    delayedTradeoffLoss: 15,
    recoveryTradeoffGain: 30,
    recoveryBenefitPenalty: 4,
    delayDays: 1,
  }
}

export function branchGeneratedIds(value = {}) {
  const prefix = clean(value.prefix)
  return {
    mainActionId: prefix + '_commit',
    recoveryActionId: prefix + '_recover',
    openingIssueId: prefix + '_opening_issue',
    followupIssueId: prefix + '_followup_issue',
    openingEventId: prefix + '_opening_event',
    committedEventType: prefix + '_committed',
    delayedEventType: prefix + '_delayed_pressure',
    recoveredEventType: prefix + '_recovered',
    causalRuleId: prefix + '_delayed_consequence',
    mainCapabilityId: prefix + '_commit_capability',
    recoveryCapabilityId: prefix + '_recover_capability',
    rhythmRuleId: prefix + '_pressure_rhythm',
    immediateFeedbackId: prefix + '_commit_feedback',
    delayedFeedbackId: prefix + '_delayed_feedback',
    recoveryFeedbackId: prefix + '_recovery_feedback',
  }
}

export function validateGameplayBranchForm(content, value = {}) {
  const errors = []
  const warnings = []
  const requiredText = [
    ['分支前缀', value.prefix], ['分支名称', value.branchName], ['目标实体', value.targetEntityId],
    ['玩家接收 Agent', value.playerAgentId], ['行动授权 Actor', value.playerActorId], ['响应 Agent', value.responderAgentId], ['能力挂载 Actor', value.actorId],
    ['主资源名称', value.primaryResourceName], ['副资源名称', value.secondaryResourceName],
    ['收益指标名称', value.benefitMetricName], ['代价指标名称', value.tradeoffMetricName],
    ['主行动名称', value.mainActionName], ['恢复行动名称', value.recoveryActionName],
    ['初始 Issue 名称', value.openingIssueName], ['后续 Issue 名称', value.followupIssueName],
  ]
  for (const [label, raw] of requiredText) if (!clean(raw)) errors.push(label + '不能为空。')
  if (clean(value.prefix) && !SAFE_ID.test(clean(value.prefix))) errors.push('分支前缀只能使用字母、数字、点、下划线或连字符，且必须以字母或数字开头。')

  const entityIds = new Set(list(content?.entities).map(item => clean(item?.id)))
  const agentIds = new Set(list(content?.agents).map(item => clean(item?.id)))
  const actorIds = new Set(list(content?.actors).map(item => clean(item?.actor_id || item?.id)))
  if (clean(value.targetEntityId) && !entityIds.has(clean(value.targetEntityId))) errors.push('目标实体“' + clean(value.targetEntityId) + '”不存在。')
  if (clean(value.playerAgentId) && !agentIds.has(clean(value.playerAgentId))) errors.push('玩家接收 Agent“' + clean(value.playerAgentId) + '”不存在。')
  if (clean(value.responderAgentId) && !agentIds.has(clean(value.responderAgentId))) errors.push('响应 Agent“' + clean(value.responderAgentId) + '”不存在。')
  if (clean(value.playerActorId) && !actorIds.has(clean(value.playerActorId))) errors.push('行动授权 Actor“' + clean(value.playerActorId) + '”不存在。')
  if (clean(value.actorId) && !actorIds.has(clean(value.actorId))) errors.push('Actor“' + clean(value.actorId) + '”不存在。')

  const names = [value.primaryResourceName, value.secondaryResourceName, value.benefitMetricName, value.tradeoffMetricName].map(clean)
  if (names.filter(Boolean).length !== new Set(names.filter(Boolean)).size) errors.push('两种库存资产和两项指标必须使用不同名称。')

  const numericRules = [
    ['统一价值锚', value.anchorRate, 0.000001, 1000000],
    ['主资源初始数量', value.primaryInitial, 0, 1000000000], ['副资源初始数量', value.secondaryInitial, 0, 1000000000],
    ['收益指标初始值', value.benefitInitial, 0, 100], ['代价指标初始值', value.tradeoffInitial, 0, 100],
    ['主行动主资源成本', value.mainPrimaryCost, 0, 1000000000], ['主行动副资源成本', value.mainSecondaryCost, 0, 1000000000],
    ['恢复行动主资源成本', value.recoveryPrimaryCost, 0, 1000000000], ['恢复行动副资源成本', value.recoverySecondaryCost, 0, 1000000000],
    ['主行动收益', value.mainBenefitGain, 0, 100], ['即时取舍损失', value.immediateTradeoffLoss, 0, 100],
    ['延迟取舍损失', value.delayedTradeoffLoss, 0, 100], ['恢复收益', value.recoveryTradeoffGain, 0, 100],
    ['恢复副作用', value.recoveryBenefitPenalty, 0, 100], ['延迟天数', value.delayDays, 0.01, 3650],
  ]
  for (const [label, raw, minimum, maximum] of numericRules) {
    const number = Number(raw)
    if (!Number.isFinite(number) || number < minimum || number > maximum) errors.push(label + '必须在 ' + minimum + ' 到 ' + maximum + ' 之间。')
  }

  const anchor = finite(value.anchorRate)
  if (anchor > 0) {
    const primaryValue = finite(value.primaryInitial) * anchor
    const secondaryValue = finite(value.secondaryInitial) * anchor
    const primaryNeed = finite(value.mainPrimaryCost) + finite(value.recoveryPrimaryCost)
    const secondaryNeed = finite(value.mainSecondaryCost) + finite(value.recoverySecondaryCost)
    if (primaryValue < primaryNeed) warnings.push('主资源初始锚定价值不足以连续支付主行动与恢复行动。')
    if (secondaryValue < secondaryNeed) warnings.push('副资源初始锚定价值不足以连续支付主行动与恢复行动。')
  }
  const controlled = list(content?.gameplay?.player?.controlled_objects).some(item => item?.kind === 'entity' && clean(item?.id) === clean(value.targetEntityId))
  if (clean(value.targetEntityId) && !controlled) warnings.push('目标实体尚未出现在 gameplay.player.controlled_objects 中；生成后 Action 可能因 controlled_only 无法执行。')
  const responder = list(content?.agents).find(item => clean(item?.id) === clean(value.responderAgentId))
  if (responder && clean(responder.actor_id || responder.id) !== clean(value.actorId)) warnings.push('所选 Actor 不是响应 Agent 当前绑定的 Actor；能力会挂到所选 Actor，请确认这是预期设计。')
  return { errors, warnings }
}

export function createGameplayBranchBundle(value = {}) {
  const ids = branchGeneratedIds(value)
  const prefix = clean(value.prefix)
  const branchName = clean(value.branchName)
  const target = clean(value.targetEntityId)
  const playerAgent = clean(value.playerAgentId)
  const responder = clean(value.responderAgentId)
  const playerActorId = clean(value.playerActorId)
  const actorId = clean(value.actorId)
  const primary = clean(value.primaryResourceName)
  const secondary = clean(value.secondaryResourceName)
  const benefit = clean(value.benefitMetricName)
  const tradeoff = clean(value.tradeoffMetricName)
  const anchor = finite(value.anchorRate)
  const mainBenefit = finite(value.mainBenefitGain)
  const immediateLoss = finite(value.immediateTradeoffLoss)
  const delayedLoss = finite(value.delayedTradeoffLoss)
  const recoveryGain = finite(value.recoveryTradeoffGain)
  const recoveryPenalty = finite(value.recoveryBenefitPenalty)
  const delayDays = finite(value.delayDays, 1)
  const mainCostTotal = finite(value.mainPrimaryCost) + finite(value.mainSecondaryCost)
  const recoveryCostTotal = finite(value.recoveryPrimaryCost) + finite(value.recoverySecondaryCost)
  const metadata = { created_by: 'dlc_studio_gameplay_branch', branch_prefix: prefix }

  const resources = [
    { name: primary, unit: clean(value.primaryResourceUnit) || '点', kind: 'asset', default_quantity: 0, min_quantity: 0, max_quantity: null, anchor_rate: anchor, metadata: { ...metadata, value_anchor: prefix + '_base_value' } },
    { name: secondary, unit: clean(value.secondaryResourceUnit) || '份', kind: 'asset', default_quantity: 0, min_quantity: 0, max_quantity: null, anchor_rate: anchor, metadata: { ...metadata, value_anchor: prefix + '_base_value', parity_with: primary } },
    { name: benefit, unit: '%', kind: 'metric', default_quantity: finite(value.benefitInitial), min_quantity: 0, max_quantity: 100, anchor_rate: null, metadata: { ...metadata, role: 'branch_benefit' } },
    { name: tradeoff, unit: '%', kind: 'metric', default_quantity: finite(value.tradeoffInitial), min_quantity: 0, max_quantity: 100, anchor_rate: null, metadata: { ...metadata, role: 'branch_tradeoff' } },
  ]
  const inventory = [
    { entity_id: target, category: primary, item_name: primary, quantity: finite(value.primaryInitial), description: branchName + '使用的锚定库存资产' },
    { entity_id: target, category: secondary, item_name: secondary, quantity: finite(value.secondaryInitial), description: '与“' + primary + '”按同一价值锚换算的库存资产' },
  ]
  const entityValues = [
    { entity_id: target, name: benefit, kind: 'metric', value: finite(value.benefitInitial), description: branchName + '的即时收益指标' },
    { entity_id: target, name: tradeoff, kind: 'metric', value: finite(value.tradeoffInitial), description: branchName + '的取舍与恢复指标' },
  ]
  const targetContract = { kind: 'entity', required: true, allowed_ids: [target], entity_types: [], controlled_only: true }
  const permissions = { control_modes: ['direct'], control_kinds: ['entity'], control_ids: [target], actor_ids: [playerActorId] }
  const mainAction = {
    action_id: ids.mainActionId,
    display_name: clean(value.mainActionName),
    description: '以两种同价值锚资产换取“' + benefit + '”的快速提升，同时降低“' + tradeoff + '”并触发延迟代价。',
    enabled: true,
    parameters: [{ name: 'benefit_gain', type: 'number', display_name: benefit + '提升', required: false, default: mainBenefit, minimum: Math.max(0, mainBenefit - 10), maximum: Math.min(100, mainBenefit + 20) }],
    target: cloneJson(targetContract),
    permissions: cloneJson(permissions),
    preconditions: [
      { kind: 'anchored_inventory', entity_id: '$control', name: primary, operator: 'gte', base_value: finite(value.mainPrimaryCost) },
      { kind: 'anchored_inventory', entity_id: '$control', name: secondary, operator: 'gte', base_value: finite(value.mainSecondaryCost) },
      { kind: 'metric', entity_id: '$target', name: benefit, operator: 'lte', value: Math.max(0, 100 - mainBenefit) },
      { kind: 'metric', entity_id: '$target', name: tradeoff, operator: 'gte', value: immediateLoss },
    ],
    costs: [
      { entity_id: '$control', resource_name: primary, base_value: finite(value.mainPrimaryCost), reason: clean(value.mainActionName) },
      { entity_id: '$control', resource_name: secondary, base_value: finite(value.mainSecondaryCost), reason: clean(value.mainActionName) },
    ],
    duration: { action_type: ids.mainActionId, proposed_days: 0.25, severity: 8, resource_value: mainCostTotal, distance_days: 0 },
    cooldown: { days: Math.max(1, delayDays), scope: 'control_target' },
    result: { mode: 'composite', results: [
      { mode: 'world_commands', effects: [
        { effect_type: 'adjust_resource', entity_id: '$target', resource_name: benefit, quantity_delta: '$param.benefit_gain', reason: clean(value.mainActionName) + '产生即时收益' },
        { effect_type: 'adjust_resource', entity_id: '$target', resource_name: tradeoff, quantity_delta: -immediateLoss, reason: clean(value.mainActionName) + '产生即时取舍代价' },
        { effect_type: 'update_status', entity_id: '$target', new_status: clean(value.mainActionName) + '已执行', reason: branchName + '进入延迟观察阶段' },
      ] },
      { mode: 'wake_agent', agent_id: responder, payload: { description: '跟进“' + branchName + '”并准备延迟结果报告', target_id: '$target', benefit_gain: '$param.benefit_gain' } },
      { mode: 'emit_event', event: { event_type: ids.committedEventType, severity: 8, target_agents: [responder], initial_payload: { target_id: '$target', action_id: ids.mainActionId, branch_prefix: prefix } } },
    ] },
  }
  const recoveryAction = {
    action_id: ids.recoveryActionId,
    display_name: clean(value.recoveryActionName),
    description: '处理延迟代价，恢复“' + tradeoff + '”，但继续消耗锚定资产并使“' + benefit + '”承受可控回撤。',
    enabled: true,
    parameters: [{ name: 'tradeoff_gain', type: 'number', display_name: tradeoff + '恢复', required: false, default: recoveryGain, minimum: Math.max(0, recoveryGain - 10), maximum: Math.min(100, recoveryGain + 20) }],
    target: cloneJson(targetContract),
    permissions: cloneJson(permissions),
    preconditions: [
      { kind: 'anchored_inventory', entity_id: '$control', name: primary, operator: 'gte', base_value: finite(value.recoveryPrimaryCost) },
      { kind: 'anchored_inventory', entity_id: '$control', name: secondary, operator: 'gte', base_value: finite(value.recoverySecondaryCost) },
      { kind: 'metric', entity_id: '$target', name: tradeoff, operator: 'lte', value: Math.min(100, finite(value.tradeoffInitial)) },
      { kind: 'metric', entity_id: '$target', name: benefit, operator: 'gte', value: recoveryPenalty },
    ],
    costs: [
      { entity_id: '$control', resource_name: primary, base_value: finite(value.recoveryPrimaryCost), reason: clean(value.recoveryActionName) },
      { entity_id: '$control', resource_name: secondary, base_value: finite(value.recoverySecondaryCost), reason: clean(value.recoveryActionName) },
    ],
    duration: { action_type: ids.recoveryActionId, proposed_days: 0.5, severity: 5, resource_value: recoveryCostTotal, distance_days: 0 },
    cooldown: { days: 1, scope: 'control_target' },
    result: { mode: 'composite', results: [
      { mode: 'world_commands', effects: [
        { effect_type: 'adjust_resource', entity_id: '$target', resource_name: tradeoff, quantity_delta: '$param.tradeoff_gain', reason: clean(value.recoveryActionName) + '缓解延迟代价' },
        { effect_type: 'adjust_resource', entity_id: '$target', resource_name: benefit, quantity_delta: -recoveryPenalty, reason: clean(value.recoveryActionName) + '产生可控副作用' },
        { effect_type: 'update_status', entity_id: '$target', new_status: clean(value.recoveryActionName) + '已完成', reason: branchName + '进入收束阶段' },
      ] },
      { mode: 'wake_agent', agent_id: responder, payload: { description: '复核“' + branchName + '”恢复结果', target_id: '$target', tradeoff_gain: '$param.tradeoff_gain' } },
      { mode: 'emit_event', event: { event_type: ids.recoveredEventType, severity: 4, target_agents: [responder], initial_payload: { target_id: '$target', action_id: ids.recoveryActionId, branch_prefix: prefix } } },
    ] },
  }
  const openingIssue = {
    issue_type_id: ids.openingIssueId,
    display_name: clean(value.openingIssueName),
    description: '让玩家在高收益、高代价行动与持续观察之间作出选择。',
    category: 'crisis', default_urgency: 'high', default_deadline_days: Math.max(1, delayDays + 1),
    source: { allowed_kinds: ['agent'], allowed_ids: [responder] },
    match: { payload_types: [ids.openingIssueId], source_ids: [responder], default_for_player_reports: false },
    evidence: [{ kind: prefix + '_opening_report', display_name: branchName + '初始报告', required: true }],
    decision_requests: [{ request_id: prefix + '_opening_decision', title: '决定“' + branchName + '”处置方式', prompt: '立即行动会获得快速收益，但会产生即时与延迟代价。', deadline_days: Math.max(1, delayDays), options: [
      { option_id: 'commit', display_name: clean(value.mainActionName), action_id: ids.mainActionId, control_object_id: target, target_id: target, parameters: { benefit_gain: mainBenefit }, resolution: { issue_status: 'resolved', case_status: 'resolved', summary: '已执行“' + clean(value.mainActionName) + '”' } },
      { option_id: 'monitor', display_name: '持续观察', resolution: { issue_status: 'monitoring', case_status: 'monitoring', summary: '暂不行动，继续收集信息' } },
    ] }], enabled: true,
  }
  const followupIssue = {
    issue_type_id: ids.followupIssueId,
    display_name: clean(value.followupIssueName),
    description: '把主行动的延迟代价转化为可恢复但不能免费撤销的后续事项。',
    category: 'consequence', default_urgency: 'high', default_deadline_days: Math.max(1, delayDays + 1),
    source: { allowed_kinds: ['agent'], allowed_ids: [responder] },
    match: { payload_types: [ids.delayedEventType], source_ids: [responder], default_for_player_reports: false },
    evidence: [{ kind: prefix + '_delayed_report', display_name: branchName + '延迟代价报告', required: true }],
    decision_requests: [{ request_id: prefix + '_followup_decision', title: '处理“' + clean(value.followupIssueName) + '”', prompt: '恢复行动会消耗两种锚定资产，并对即时收益指标造成小幅回撤。', deadline_days: 1, options: [
      { option_id: 'recover', display_name: clean(value.recoveryActionName), action_id: ids.recoveryActionId, control_object_id: target, target_id: target, parameters: { tradeoff_gain: recoveryGain }, resolution: { issue_status: 'resolved', case_status: 'resolved', summary: '已执行“' + clean(value.recoveryActionName) + '”' } },
      { option_id: 'accept', display_name: '接受代价并继续观察', resolution: { issue_status: 'monitoring', case_status: 'monitoring', summary: '暂不恢复，保留当前取舍状态' } },
    ] }], enabled: true,
  }
  const initialEvent = {
    event_id: ids.openingEventId, event_type: ids.openingIssueId, severity: 7,
    target_agents: unique([playerAgent]),
    initial_payload: { issue_type_id: ids.openingIssueId, type: ids.openingIssueId, source_kind: 'agent', source_id: responder, from: responder, title: clean(value.openingIssueName), summary: branchName + '需要玩家决定是否立即执行高收益、高代价方案。', evidence: [{ kind: prefix + '_opening_report', summary: '当前状态允许立即行动，但后果会在 ' + delayDays + ' 个游戏日后进一步显现。' }], urgency: 'high', deadline_days: Math.max(1, delayDays + 1) },
  }
  const causalRule = {
    rule_id: ids.causalRuleId, source_event_types: [ids.committedEventType], child_event_type: ids.delayedEventType,
    target_agents: [], initial_payload: { phase: 'delayed_consequence', summary: branchName + '的延迟代价开始显现' },
    inherit_payload_keys: ['target_id', 'action_id', 'branch_prefix'], conditions: [],
    world_effects: [
      { effect_type: 'adjust_resource', entity_id: target, resource_name: tradeoff, quantity_delta: -delayedLoss, reason: branchName + '的延迟代价持续累积' },
      { effect_type: 'create_issue', issue_type_id: ids.followupIssueId, source: { kind: 'agent', id: responder, entity_id: target }, title: clean(value.followupIssueName), summary: branchName + '的即时收益已经兑现，但“' + tradeoff + '”继续承压，需要决定是否恢复。', details: { phase: 'delayed_consequence', branch_prefix: prefix }, evidence: [{ kind: prefix + '_delayed_report', source: responder, summary: '延迟代价已转化为可处理的后续事项。' }], urgency: 'high', deadline_days: Math.max(1, delayDays + 1), metadata, reason: '把主行动的延迟代价交付为玩家可见 Issue' },
    ],
    delay_days: delayDays, probability: 1, max_depth: 4, max_fires_per_chain: 1, severity_delta: 1, enabled: true, priority: 120,
  }
  const capability = (id, actionId, displayName, urgency, parameter) => ({
    capability_id: id, display_name: displayName, description: '调用“' + actionId + '”契约 Action，统一执行权限、锚定成本、结果与事件。',
    handler: 'contract_action', audiences: ['player', 'agent', 'router'], aliases: [],
    target: { kind: 'entity', mode: 'required', allowed_ids: [target] }, parameters: [parameter], action_id: actionId,
    default_urgency: urgency, input: { content_required: false, content_label: displayName + '备注', content_placeholder: '可选：补充本次行动的重点与限制。' }, enabled: true,
  })
  const capabilities = [
    capability(ids.mainCapabilityId, ids.mainActionId, clean(value.mainActionName), 'critical', { name: 'benefit_gain', type: 'number', display_name: benefit + '提升', description: '主行动计划提升的收益指标。', required: false, default: mainBenefit, minimum: Math.max(0, mainBenefit - 10), maximum: Math.min(100, mainBenefit + 20) }),
    capability(ids.recoveryCapabilityId, ids.recoveryActionId, clean(value.recoveryActionName), 'urgent', { name: 'tradeoff_gain', type: 'number', display_name: tradeoff + '恢复', description: '恢复行动计划修复的取舍指标。', required: false, default: recoveryGain, minimum: Math.max(0, recoveryGain - 10), maximum: Math.min(100, recoveryGain + 20) }),
  ]
  const rhythmRules = [{
    rule_id: ids.rhythmRuleId, display_name: branchName + '延迟压力', enabled: true,
    issue_type_ids: [ids.followupIssueId], event_types: [ids.delayedEventType], priority: 90, attention_cost: 2, max_active: 1,
    cooldown_days: delayDays, mutual_exclusion_group: prefix + '_consequence', allow_during_quiet: true, quiet_period_days: 0,
    escalation: { enabled: true, after_days: 1, interval_days: 1, max_level: 3, urgency_by_level: ['high', 'high', 'critical', 'critical'], feedback_by_level: [clean(value.followupIssueName) + '仍未处理。', '延迟代价正在持续累积。', '该分支已进入危急等级。'] },
    deescalation: { enabled: true, interval_days: 0.5, target_level: 1, issue_status: 'monitoring', conditions: [{ kind: 'metric', operator: 'gte', entity_id: target, name: tradeoff, value: Math.min(100, finite(value.tradeoffInitial) - 5) }], feedback: tradeoff + '已经恢复，后续压力降为观察。' },
    climax: { enabled: true, level: 3, after_days: 0.5, conditions: [{ kind: 'metric', operator: 'gte', entity_id: target, name: tradeoff, value: Math.min(100, finite(value.tradeoffInitial)) }], issue_status: 'resolved', case_status: 'resolved', close_group: true, quiet_period_days: 1, feedback: branchName + '已经完成收束。' },
  }]
  const feedbackRules = [
    { rule_id: ids.immediateFeedbackId, display_name: branchName + '即时反馈', enabled: true, priority: 110, source_kinds: ['action'], delivery: 'immediate', action_ids: [ids.mainActionId], issue_type_ids: [], event_types: [], metric_ids: [], delay_days: 0, conditions: [], title: clean(value.mainActionName) + '已执行', summary: benefit + '迅速提升，但' + tradeoff + '下降；延迟代价将在后续显现。', reason: '主行动以双锚定资产换取快速收益。', reveal_text: '', create_issue_type_id: '', attach_evidence: false },
    { rule_id: ids.delayedFeedbackId, display_name: branchName + '延迟代价反馈', enabled: true, priority: 105, source_kinds: ['causal_event'], delivery: 'immediate', action_ids: [], issue_type_ids: [], event_types: [ids.delayedEventType], metric_ids: [], delay_days: 0, conditions: [], title: clean(value.followupIssueName) + '已显现', summary: tradeoff + '继续下降，系统已创建可恢复但需要额外成本的后续事项。', reason: '这是主行动在 ' + delayDays + ' 个游戏日后产生的延迟代价。', reveal_text: '', create_issue_type_id: '', attach_evidence: true },
    { rule_id: ids.recoveryFeedbackId, display_name: branchName + '恢复反馈', enabled: true, priority: 100, source_kinds: ['action'], delivery: 'immediate', action_ids: [ids.recoveryActionId], issue_type_ids: [], event_types: [], metric_ids: [], delay_days: 0, conditions: [], title: clean(value.recoveryActionName) + '已完成', summary: tradeoff + '得到恢复，同时' + benefit + '承受小幅回撤。', reason: '玩家再次支付同价值锚资产完成了分支收束。', reveal_text: '', create_issue_type_id: '', attach_evidence: false },
  ]
  return {
    ids,
    sections: { resources, inventory, entity_values: entityValues, actions: [mainAction, recoveryAction], issue_types: [openingIssue, followupIssue], initial_events: [initialEvent], causal_event_rules: [causalRule], capabilities },
    rhythmRules,
    feedbackRules,
    actorId,
    capabilityIds: capabilities.map(item => item.capability_id),
  }
}

export function buildGameplayBranchDraft(content, value = {}, options = {}) {
  const validation = validateGameplayBranchForm(content, value)
  const policy = options.conflictPolicy === 'replace' ? 'replace' : 'reject'
  const emptyCounts = {
    resources: { added: 0, replaced: 0 }, inventory: { added: 0, replaced: 0 }, entity_values: { added: 0, replaced: 0 },
    actions: { added: 0, replaced: 0 }, issue_types: { added: 0, replaced: 0 }, initial_events: { added: 0, replaced: 0 },
    causal_event_rules: { added: 0, replaced: 0 }, capabilities: { added: 0, replaced: 0 }, rhythm_rules: { added: 0, replaced: 0 },
    feedback_rules: { added: 0, replaced: 0 }, actors: { updated: 0 },
  }
  if (validation.errors.length) return { valid: false, changed: false, errors: validation.errors, warnings: validation.warnings, counts: emptyCounts, nextContent: cloneJson(content), ids: branchGeneratedIds(value) }
  const bundle = createGameplayBranchBundle(value)
  const conflicts = conflictLabels(content, bundle)
  if (policy === 'reject' && conflicts.length) {
    return { valid: false, changed: false, errors: ['发现同 ID 内容：' + conflicts.join('；') + '。请修改分支前缀或切换为“覆盖整套分支”。'], warnings: validation.warnings, counts: emptyCounts, nextContent: cloneJson(content), ids: bundle.ids }
  }
  const nextContent = cloneJson(content)
  const counts = cloneJson(emptyCounts)
  for (const [section, generated] of Object.entries(bundle.sections)) {
    nextContent[section] = mergeItems(nextContent[section], generated, section, policy, counts[section])
  }
  const rhythm = baseRhythm(nextContent.rhythm)
  const rhythmExisting = rhythm.rules.map(cloneJson)
  for (const rule of bundle.rhythmRules) {
    const index = rhythmExisting.findIndex(item => rhythmRuleKey(item) === rule.rule_id)
    if (index < 0) { rhythmExisting.push(rule); counts.rhythm_rules.added += 1 }
    else { rhythmExisting[index] = rule; counts.rhythm_rules.replaced += 1 }
  }
  rhythm.rules = rhythmExisting
  nextContent.rhythm = rhythm

  const feedback = baseFeedback(nextContent.outcome_feedback)
  const feedbackExisting = feedback.rules.map(cloneJson)
  for (const rule of bundle.feedbackRules) {
    const index = feedbackExisting.findIndex(item => feedbackRuleKey(item) === rule.rule_id)
    if (index < 0) { feedbackExisting.push(rule); counts.feedback_rules.added += 1 }
    else { feedbackExisting[index] = rule; counts.feedback_rules.replaced += 1 }
  }
  feedback.rules = feedbackExisting
  nextContent.outcome_feedback = feedback

  const actorIndex = list(nextContent.actors).findIndex(item => clean(item?.actor_id || item?.id) === bundle.actorId)
  const actor = cloneJson(nextContent.actors[actorIndex])
  actor.capability_ids = unique([...list(actor.capability_ids).map(clean), ...bundle.capabilityIds])
  actor.metadata = { ...(actor.metadata || {}), gameplay_branch_prefixes: unique([...list(actor.metadata?.gameplay_branch_prefixes).map(clean), clean(value.prefix)]) }
  nextContent.actors[actorIndex] = actor
  counts.actors.updated = 1

  const warnings = [...validation.warnings]
  if (policy === 'replace' && conflicts.length) warnings.push('已按整套覆盖策略替换 ' + conflicts.length + ' 个冲突条目；应用差异前请重点检查原有高级字段。')
  return { valid: true, changed: true, errors: [], warnings, counts, nextContent, ids: bundle.ids }
}

export function summarizeGameplayBranch(result) {
  const counts = result?.counts || {}
  const total = Object.entries(counts).reduce((sum, [section, value]) => section === 'actors' ? sum + (value.updated || 0) : sum + (value.added || 0) + (value.replaced || 0), 0)
  return '玩法分支共处理 ' + total + ' 个声明：Action ' + ((counts.actions?.added || 0) + (counts.actions?.replaced || 0))
    + '，Issue ' + ((counts.issue_types?.added || 0) + (counts.issue_types?.replaced || 0))
    + '，因果规则 ' + ((counts.causal_event_rules?.added || 0) + (counts.causal_event_rules?.replaced || 0))
    + '，Capability ' + ((counts.capabilities?.added || 0) + (counts.capabilities?.replaced || 0)) + '。'
}
