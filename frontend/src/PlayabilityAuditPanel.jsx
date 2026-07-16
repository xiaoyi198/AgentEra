import { normalizePlayabilityAudit } from './PlayabilityAuditTools.js'

const EVIDENCE_LABELS = {
  action: '行动',
  agent: 'Agent',
  causal_rule: '因果规则',
  ending: '结局',
  gameplay: '玩家身份',
  initial_event: '初始事件',
  issue_type: 'Issue 类型',
  objective: '目标',
  outcome_feedback: '结果反馈',
  random_event: '随机事件',
  sandbox_cycle: '沙盒周期',
  time_boundary: '时间边界',
  ui_manifest: '主界面',
}

const CHECK_ROUTES = {
  player_surface: [{ tab: 'gameplay', label: '编辑玩家身份' }, { tab: 'ui_manifest', label: '编辑主界面' }],
  action_roles: [{ tab: 'agents', label: '编辑 Agent' }, { tab: 'actions', label: '编辑行动' }],
  issue_intake: [{ tab: 'issue_types', label: '编辑 Issue' }, { tab: 'initial_events', label: '编辑初始事件' }],
  costly_decision: [{ tab: 'issue_types', label: '编辑决策选项' }, { tab: 'actions', label: '编辑成本行动' }],
  world_simulation: [{ tab: 'causal_event_rules', label: '编辑因果链' }, { tab: 'time_system', label: '编辑时间系统' }],
  outcome_feedback: [{ tab: 'outcome_feedback', label: '编辑结果反馈' }],
  progression: [{ tab: 'objectives', label: '编辑目标' }, { tab: 'endings', label: '编辑结局' }],
}

function Evidence({ item }) {
  return <li>
    <span>{EVIDENCE_LABELS[item.kind] || item.kind}</span>
    <strong>{item.label}</strong>
    {item.itemId && <code>{item.itemId}</code>}
  </li>
}

export default function PlayabilityAuditPanel({ report, busy = false, onRefresh, onNavigate }) {
  const audit = normalizePlayabilityAudit(report)

  if (!audit.available) {
    return <section className="dlc-playability dlc-playability-empty" aria-label="DLC 可玩性验收">
      <h2>七步可玩性验收</h2>
      <p>当前服务尚未返回可玩性审计。请重新校验 DLC，或确认后端已启用 playability 报告。</p>
      {onRefresh && <button disabled={busy} onClick={onRefresh}>重新读取审计</button>}
    </section>
  }

  return <section className="dlc-playability" aria-label="DLC 可玩性验收">
    <header className={`dlc-playability-hero ${audit.playable ? 'is-playable' : 'has-gaps'}`}>
      <div>
        <span className="dlc-playability-kicker">PLAYABILITY AUDIT {audit.auditVersion && `V${audit.auditVersion}`}</span>
        <h2>七步可玩性验收</h2>
        <p>检查 DLC 是否仅依靠内容配置就能形成完整玩家循环。Registry 校验负责配置合法性，本页负责发现“合法但还不好玩”的缺口。</p>
      </div>
      <div className="dlc-playability-score" aria-label={`通过 ${audit.passedCount} 项，共 ${audit.totalCount} 项`}>
        <strong>{audit.passedCount}<small>/{audit.totalCount}</small></strong>
        <span>{audit.playable ? '已具备完整循环' : '仍有可玩性缺口'}</span>
      </div>
    </header>

    <div className="dlc-playability-progress" aria-hidden="true"><span style={{ width: `${audit.percent}%` }} /></div>
    <div className="dlc-playability-toolbar">
      <p>{audit.playable ? '七项静态证据齐全，可继续运行真实 playthrough 验收。' : `还需补齐 ${audit.totalCount - audit.passedCount} 项。优先处理红色卡片中的中文建议。`}</p>
      {onRefresh && <button disabled={busy} onClick={onRefresh}>{busy ? '读取中…' : '重新读取审计'}</button>}
    </div>

    <div className="dlc-playability-checks">
      {audit.checks.map((check, index) => <article key={check.checkId} className={check.passed ? 'passed' : 'gap'}>
        <div className="dlc-playability-check-title">
          <span>{String(index + 1).padStart(2, '0')}</span>
          <div><h3>{check.title}</h3><p>{check.summary}</p></div>
          <b>{check.passed ? '通过' : '待补'}</b>
        </div>
        {check.gaps.length > 0 && <div className="dlc-playability-gaps">
          <h4>待补配置</h4>
          <ul>{check.gaps.map((gap, gapIndex) => <li key={`${check.checkId}:gap:${gapIndex}`}>{gap}</li>)}</ul>
        </div>}
        {check.evidence.length > 0 && <div className="dlc-playability-evidence">
          <h4>已识别证据</h4>
          <ul>{check.evidence.map((item, evidenceIndex) => <Evidence key={`${check.checkId}:evidence:${item.kind}:${item.itemId}:${evidenceIndex}`} item={item} />)}</ul>
        </div>}
        {onNavigate && <div className="dlc-playability-routes">
          {(CHECK_ROUTES[check.checkId] || []).map(route => <button key={`${check.checkId}:${route.tab}`} type="button" onClick={() => onNavigate(route.tab)}>{check.passed ? '查看' : '去补齐'}：{route.label}</button>)}
        </div>}
      </article>)}
    </div>

    <footer>提示：静态审计证明“配置声明了闭环”，不能替代真实运行中的 Agent、多轮调用、时间推进与后果反馈测试。</footer>
  </section>
}
