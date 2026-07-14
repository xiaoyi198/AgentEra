import { useMemo, useState } from 'react'

const CATEGORY_META = {
  primary: { label: '主目标', tone: 'primary' },
  optional: { label: '可选目标', tone: 'optional' },
  failure: { label: '失败条件', tone: 'failure' },
}

const STATUS_LABELS = {
  sandbox: '沙盒运行',
  in_progress: '进行中',
  ended: '已结局',
  active: '进行中',
  completed: '已完成',
  pending: '待达成',
  locked: '未解锁',
  disabled: '已停用',
}

const OUTCOME_META = {
  victory: { label: '胜利', icon: '◆' },
  defeat: { label: '失败', icon: '×' },
  neutral: { label: '中性结局', icon: '◇' },
}

const KIND_LABELS = {
  anchored_inventory: '锚定库存价值',
  metric: '实体指标',
  parameter: '实体参数',
  entity_status: '实体状态',
  sim_day: '模拟天数',
  issue_count: 'Issue 数量',
  case_count: 'Case 数量',
  decision_count: '决策数量',
  action_count: '行动成功次数',
}

const OPERATOR_LABELS = {
  lt: '<', lte: '≤', eq: '=', ne: '≠', gte: '≥', gt: '>',
  all: '全部满足', any: '任一满足', not: '条件不成立',
}

function percent(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return 0
  return Math.max(0, Math.min(100, Math.round(number * 100)))
}

function displayValue(value) {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(2).replace(/\.00$/, '')
  return String(value)
}

function ConditionEvaluation({ value, depth = 0 }) {
  if (!value) return <span className="objective-condition-empty">仅由里程碑决定</span>
  if (value.op) {
    return (
      <div className={`objective-condition-group depth-${Math.min(depth, 3)}`}>
        <div className="objective-condition-line">
          <span className={`objective-condition-result ${value.ok ? 'ok' : 'waiting'}`}>{value.ok ? '✓' : '○'}</span>
          <strong>{value.label || OPERATOR_LABELS[value.op] || value.op}</strong>
          <small>{percent(value.progress)}%</small>
        </div>
        <div className="objective-condition-children">
          {(value.conditions || []).map((condition, index) => <ConditionEvaluation key={`${depth}-${index}`} value={condition} depth={depth + 1} />)}
        </div>
      </div>
    )
  }
  return (
    <div className={`objective-condition-leaf ${value.ok ? 'ok' : 'waiting'}`}>
      <span className="objective-condition-result">{value.ok ? '✓' : '○'}</span>
      <div>
        <strong>{value.label || KIND_LABELS[value.kind] || value.kind || '条件'}</strong>
        <small>
          {value.entity_id ? `${value.entity_id} · ` : ''}{value.name ? `${value.name} · ` : ''}
          当前 {displayValue(value.actual)} {OPERATOR_LABELS[value.operator] || value.operator} 目标 {displayValue(value.expected)}
        </small>
        {value.error && value.error !== 'condition_failed' && <em>读取失败：{value.error}</em>}
      </div>
      <span>{percent(value.progress)}%</span>
    </div>
  )
}

function ObjectiveCard({ objective }) {
  const [expanded, setExpanded] = useState(objective.category === 'primary' || objective.category === 'failure')
  const category = CATEGORY_META[objective.category] || CATEGORY_META.primary
  return (
    <article className={`objective-card ${category.tone} ${objective.status}`}>
      <button type="button" className="objective-card-heading" onClick={() => setExpanded(value => !value)} aria-expanded={expanded}>
        <span className={`objective-category ${category.tone}`}>{category.label}</span>
        <span className="objective-title-copy">
          <strong>{objective.display_name || objective.objective_id}</strong>
          <small>{objective.required ? '必需' : '非必需'} · {objective.milestone_mode === 'independent' ? '里程碑可独立完成' : '里程碑按顺序解锁'}</small>
        </span>
        <span className="objective-progress-number">{percent(objective.progress)}%</span>
        <span className="objective-expand">{expanded ? '−' : '+'}</span>
      </button>
      <div className="objective-progress-track" aria-label={`目标进度 ${percent(objective.progress)}%`}><i style={{ width: `${percent(objective.progress)}%` }} /></div>
      {expanded && <div className="objective-card-body">
        {objective.description && <p>{objective.description}</p>}
        <div className="objective-status-row">
          <span className={`objective-status ${objective.status}`}>{STATUS_LABELS[objective.status] || objective.status}</span>
          <code>{objective.objective_id}</code>
        </div>
        {(objective.milestones || []).length > 0 && <section className="objective-milestones">
          <h4>里程碑</h4>
          {(objective.milestones || []).map(milestone => <div key={milestone.milestone_id} className={`objective-milestone ${milestone.status}`}>
            <span className="objective-milestone-dot">{milestone.status === 'completed' ? '✓' : milestone.status === 'locked' ? '◇' : '○'}</span>
            <div>
              <strong>{milestone.display_name || milestone.milestone_id}</strong>
              {milestone.description && <small>{milestone.description}</small>}
              {milestone.status !== 'locked' && <ConditionEvaluation value={milestone.evaluation} />}
            </div>
            <span>{STATUS_LABELS[milestone.status] || milestone.status}</span>
          </div>)}
        </section>}
        <section className="objective-completion">
          <h4>{objective.category === 'failure' ? '触发判定' : '完成判定'}</h4>
          <ConditionEvaluation value={objective.completion_evaluation} />
        </section>
      </div>}
    </article>
  )
}

function EndingSummary({ ending, current }) {
  const outcome = OUTCOME_META[ending.outcome] || OUTCOME_META.neutral
  const reached = current?.ending_id === ending.ending_id || ending.reached
  return <div className={`objective-ending ${ending.outcome || 'neutral'} ${reached ? 'reached' : ''} ${ending.eligible ? 'eligible' : ''}`}>
    <span className="objective-ending-icon">{outcome.icon}</span>
    <div>
      <strong>{ending.display_name || ending.ending_id}</strong>
      <small>{outcome.label} · 优先级 {ending.priority ?? 0} · {(ending.trigger?.operator || 'all') === 'all' ? '全部目标达成' : '任一目标达成'}</small>
      {ending.description && <p>{ending.description}</p>}
    </div>
    <span>{reached ? '已达成' : ending.eligible ? '可触发' : ending.enabled === false ? '已停用' : '未达成'}</span>
  </div>
}

export default function ObjectivePanel({ state, onRefresh }) {
  const [collapsed, setCollapsed] = useState(false)
  const objectives = state?.objectives || []
  const endings = state?.endings || []
  const campaignObjectives = objectives.filter(item => item.enabled !== false && item.category !== 'failure')
  const overall = useMemo(() => campaignObjectives.length
    ? campaignObjectives.reduce((sum, item) => sum + Number(item.progress || 0), 0) / campaignObjectives.length
    : 0, [campaignObjectives])
  const current = state?.current_ending
  const outcome = current ? (OUTCOME_META[current.outcome] || OUTCOME_META.neutral) : null

  return <section className={`objective-panel ${collapsed ? 'collapsed' : ''} ${current?.outcome || ''}`}>
    <header className="objective-panel-header">
      <div className="objective-panel-title">
        <span className="objective-panel-mark">◎</span>
        <div>
          <h2>{state?.sandbox ? '沙盒进程' : 'Campaign 目标'}</h2>
          <p>{state?.sandbox ? '此 DLC 未声明强制目标或结局，世界将持续运行。' : `${objectives.length} 个目标 · ${endings.length} 个结局 · Generation ${state?.generation ?? 0}`}</p>
        </div>
      </div>
      {!state?.sandbox && <div className="objective-overall">
        <span>总体进度</span><strong>{percent(overall)}%</strong>
        <div><i style={{ width: `${percent(overall)}%` }} /></div>
      </div>}
      <div className="objective-panel-actions">
        {onRefresh && <button type="button" onClick={onRefresh} title="刷新权威目标状态">↻</button>}
        <button type="button" onClick={() => setCollapsed(value => !value)}>{collapsed ? '展开' : '收起'}</button>
      </div>
    </header>

    {!collapsed && <div className="objective-panel-content">
      {current && <div className={`objective-current-ending ${current.outcome || 'neutral'}`}>
        <span>{outcome.icon}</span>
        <div><small>{outcome.label}</small><strong>{current.display_name || current.ending_id}</strong>{current.description && <p>{current.description}</p>}</div>
        <em>结局已锁定</em>
      </div>}
      {state?.sandbox ? <div className="objective-sandbox-note">
        <strong>自由沙盒模式</strong>
        <span>没有自动胜利或失败判定。你仍可通过 Issue、行动、Agent 与世界规则持续塑造进程。</span>
      </div> : <>
        <div className="objective-grid">
          {objectives.map(objective => <ObjectiveCard key={objective.objective_id} objective={objective} />)}
        </div>
        {endings.length > 0 && <section className="objective-ending-list">
          <h3>可能结局</h3>
          {endings.map(ending => <EndingSummary key={ending.ending_id} ending={ending} current={current} />)}
        </section>}
      </>}
    </div>}
  </section>
}
