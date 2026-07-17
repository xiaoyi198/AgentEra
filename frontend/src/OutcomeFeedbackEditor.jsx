import { useMemo, useState } from 'react'
import { CausalConditionBuilder } from './DLCContentEditors.jsx'
import {
  buildOutcomeFeedbackCatalogs,
  defaultOutcomeFeedbackRule,
  normalizeOutcomeFeedbackForEditor,
  OUTCOME_DELIVERIES,
  OUTCOME_SOURCE_KINDS,
  serializeOutcomeFeedbackEditor,
} from './OutcomeFeedbackEditorTools.js'
import './OutcomeFeedbackEditor.css'

function Section({ title, description, badge, children }) {
  return <section className="outcome-editor-section">
    <header><div><h3>{title}</h3><p>{description}</p></div>{badge != null && <span>{badge}</span>}</header>
    {children}
  </section>
}

function Field({ label, help, wide = false, children }) {
  return <label className={wide ? 'outcome-editor-field wide' : 'outcome-editor-field'}>
    <strong>{label}</strong>{children}{help && <small>{help}</small>}
  </label>
}

function Toggle({ checked, onChange, label, help }) {
  return <label className="outcome-editor-toggle">
    <input type="checkbox" checked={checked} onChange={event => onChange(event.target.checked)} />
    <span><strong>{label}</strong>{help && <small>{help}</small>}</span>
  </label>
}

function ReferencePicker({ label, help, values, options, onChange, placeholder }) {
  const [draft, setDraft] = useState('')
  const selected = Array.isArray(values) ? values : []
  const remaining = options.filter(item => !selected.includes(item.id))
  const add = () => {
    const value = draft.trim()
    if (!value || selected.includes(value)) return
    onChange([...selected, value]); setDraft('')
  }
  return <div className="outcome-reference-picker">
    <strong>{label}</strong>
    <div className="outcome-reference-add">
      <input list={`${label}-catalog`} value={draft} onChange={event => setDraft(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); add() } }} placeholder={placeholder || '选择或输入稳定 ID'} />
      <datalist id={`${label}-catalog`}>{remaining.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</datalist>
      <button type="button" onClick={add}>添加</button>
    </div>
    <div className="outcome-reference-chips">
      {selected.length === 0 && <span className="empty-chip">未限制（匹配全部）</span>}
      {selected.map(value => {
        const option = options.find(item => item.id === value)
        return <button key={value} type="button" title="点击移除" onClick={() => onChange(selected.filter(item => item !== value))}>{option?.label || value}<code>{value}</code> ×</button>
      })}
    </div>
    {help && <small>{help}</small>}
  </div>
}

function RuleToolbar({ index, length, onMove, onDuplicate, onRemove }) {
  return <div className="outcome-rule-toolbar">
    <button type="button" disabled={index === 0} onClick={() => onMove(index, -1)}>↑ 上移</button>
    <button type="button" disabled={index === length - 1} onClick={() => onMove(index, 1)}>↓ 下移</button>
    <button type="button" onClick={() => onDuplicate(index)}>复制规则</button>
    <button type="button" className="danger" onClick={() => onRemove(index)}>删除规则</button>
  </div>
}

let ruleCardKeySequence = 0

function newRuleCardKey() {
  ruleCardKeySequence += 1
  return `outcome-rule-${ruleCardKeySequence}`
}

function RuleCard({ initiallyOpen = false, children }) {
  const [open, setOpen] = useState(initiallyOpen)
  return <details
    className="outcome-rule-card"
    open={open}
    onToggle={event => setOpen(event.currentTarget.open)}
  >
    {children}
  </details>
}

export default function OutcomeFeedbackEditor({ content, onSaveContent, busy = false }) {
  const [form, setForm] = useState(() => {
    const normalized = normalizeOutcomeFeedbackForEditor(content.outcome_feedback)
    return { ...normalized, rules: normalized.rules.map(rule => ({ ...rule, __editor_key: newRuleCardKey() })) }
  })
  const [error, setError] = useState('')
  const catalogs = useMemo(() => buildOutcomeFeedbackCatalogs(content), [content])
  const eventTypes = catalogs.eventTypes.map(item => item.id)
  const relationshipTypes = content.relationship_types || []

  const patch = values => setForm(current => ({ ...current, ...values }))
  const patchImmediate = values => setForm(current => ({ ...current, immediate: { ...current.immediate, ...values } }))
  const patchCase = values => setForm(current => ({ ...current, case_summaries: { ...current.case_summaries, ...values } }))
  const patchRule = (index, values) => setForm(current => ({ ...current, rules: current.rules.map((rule, itemIndex) => itemIndex === index ? { ...rule, ...values } : rule) }))
  const toggleSource = (index, kind) => setForm(current => ({ ...current, rules: current.rules.map((rule, itemIndex) => {
    if (itemIndex !== index) return rule
    const selected = new Set(rule.source_kinds || [])
    if (selected.has(kind)) selected.delete(kind); else selected.add(kind)
    return { ...rule, source_kinds: [...selected] }
  }) }))
  const addRule = () => setForm(current => {
    const used = new Set(current.rules.map(item => item.rule_id))
    let suffix = current.rules.length + 1
    while (used.has(`feedback_rule_${suffix}`)) suffix += 1
    return { ...current, rules: [...current.rules, { ...defaultOutcomeFeedbackRule(suffix - 1), rule_id: `feedback_rule_${suffix}`, __editor_key: newRuleCardKey() }] }
  })
  const moveRule = (index, direction) => setForm(current => {
    const target = index + direction
    if (target < 0 || target >= current.rules.length) return current
    const rules = [...current.rules]; [rules[index], rules[target]] = [rules[target], rules[index]]
    return { ...current, rules }
  })
  const removeRule = index => setForm(current => ({ ...current, rules: current.rules.filter((_, itemIndex) => itemIndex !== index) }))
  const duplicateRule = index => setForm(current => {
    const source = current.rules[index]
    const used = new Set(current.rules.map(item => item.rule_id))
    let suffix = 2; let ruleId = `${source.rule_id || 'feedback_rule'}_copy`
    while (used.has(ruleId)) { ruleId = `${source.rule_id || 'feedback_rule'}_copy_${suffix}`; suffix += 1 }
    const copy = { ...source, rule_id: ruleId, display_name: `${source.display_name || '结果反馈'}（副本）`, source_kinds: [...source.source_kinds], action_ids: [...source.action_ids], issue_type_ids: [...source.issue_type_ids], event_types: [...source.event_types], metric_ids: [...source.metric_ids], conditions: source.conditions.map(item => ({ ...item })), __editor_key: newRuleCardKey() }
    const rules = [...current.rules]; rules.splice(index + 1, 0, copy)
    return { ...current, rules }
  })

  const save = () => {
    try {
      const outcomeFeedback = serializeOutcomeFeedbackEditor(form)
      setError('')
      onSaveContent({ ...content, outcome_feedback: outcomeFeedback }, `已更新结果反馈配置（${outcomeFeedback.rules.length} 条规则）`)
    } catch (caught) { setError(caught.message || String(caught)) }
  }

  return <div className="outcome-feedback-editor">
    <header className="outcome-editor-hero">
      <div><span>OUTCOME FEEDBACK</span><h2>玩家结果反馈编辑器</h2><p>把行动、决策、结案与世界事件转换为玩家可理解的即时变化、延迟报告和因果揭示。</p></div>
      <div className="outcome-editor-save"><span>{form.enabled ? '运行时已启用' : '运行时已停用'}</span><button type="button" className="primary" disabled={busy} onClick={save}>{busy ? '处理中…' : '保存结果反馈'}</button></div>
    </header>

    {error && <div className="outcome-editor-error" role="alert">{error}</div>}
    <div className="outcome-editor-warning"><strong>信息安全说明</strong><span>条件可以读取世界真相、Actor 认知等隐藏层，但玩家只会看到你填写的公开文案和白名单变化字段。条件阈值、Prompt、命令 Payload 与隐藏事实不会直接公开。</span></div>

    <Section title="一、运行时与容量" description="控制反馈总开关、历史窗口、延迟队列和幂等回执。容量是存档体积与可追溯性的权衡。">
      <div className="outcome-toggle-grid"><Toggle checked={form.enabled} onChange={value => patch({ enabled: value })} label="启用结果反馈系统" help="关闭后不再创建新反馈；已有配置仍保留在 DLC。" /></div>
      <div className="outcome-editor-grid four">
        <Field label="最近反馈保留上限" help="1～1024；玩家界面可回看的已交付反馈数量。"><input type="number" min="1" max="1024" value={form.recent_limit} onChange={event => patch({ recent_limit: event.target.value })} /></Field>
        <Field label="待交付反馈上限" help="1～4096；延迟报告与待揭示因果的最大排队数。"><input type="number" min="1" max="4096" value={form.pending_limit} onChange={event => patch({ pending_limit: event.target.value })} /></Field>
        <Field label="幂等回执上限" help="1～16384；用于避免读档、重试或重复 Tick 造成重复反馈。"><input type="number" min="1" max="16384" value={form.receipt_limit} onChange={event => patch({ receipt_limit: event.target.value })} /></Field>
        <Field label="指标变化最小阈值" help="小于该绝对差值的派生指标变化不会生成噪声反馈。"><input type="number" min="0" step="any" value={form.metric_delta_epsilon} onChange={event => patch({ metric_delta_epsilon: event.target.value })} /></Field>
      </div>
    </Section>

    <Section title="二、即时反馈" description="适合玩家刚执行行动后立刻确认资源、状态与指标发生了什么变化。">
      <div className="outcome-toggle-grid">
        <Toggle checked={form.immediate.enabled} onChange={value => patchImmediate({ enabled: value })} label="启用自动即时反馈" help="从安全命令差异和玩家可见指标中自动生成即时结果。" />
        <Toggle checked={form.immediate.include_command_changes} onChange={value => patchImmediate({ include_command_changes: value })} label="包含命令变化" help="展示库存、实体字段、状态等白名单变化，不展示原始命令。" />
        <Toggle checked={form.immediate.include_metric_changes} onChange={value => patchImmediate({ include_metric_changes: value })} label="包含指标变化" help="展示玩家可见派生指标的前后值、差值和确定性原因。" />
      </div>
      <div className="outcome-editor-grid two">
        <Field label="单次命令变化上限" help="0～64；过多变化会被截断以保持界面可读。"><input type="number" min="0" max="64" value={form.immediate.max_changes} onChange={event => patchImmediate({ max_changes: event.target.value })} /></Field>
        <Field label="单次指标变化上限" help="0～64；按运行时稳定顺序选取。"><input type="number" min="0" max="64" value={form.immediate.max_metric_changes} onChange={event => patchImmediate({ max_metric_changes: event.target.value })} /></Field>
      </div>
    </Section>

    <Section title="三、结案总结" description="Case 关闭时生成结构化回顾，让玩家理解哪些决策与变化共同导致结案。">
      <div className="outcome-toggle-grid">
        <Toggle checked={form.case_summaries.enabled} onChange={value => patchCase({ enabled: value })} label="启用结案总结" help="关闭后仍可通过自定义规则监听 case_closed。" />
        <Toggle checked={form.case_summaries.include_decisions} onChange={value => patchCase({ include_decisions: value })} label="包含决策记录" help="只展示玩家已做出的公开决策摘要。" />
        <Toggle checked={form.case_summaries.include_changes} onChange={value => patchCase({ include_changes: value })} label="包含世界变化" help="使用安全白名单变化，不暴露命令 Payload。" />
        <Toggle checked={form.case_summaries.include_metric_changes} onChange={value => patchCase({ include_metric_changes: value })} label="包含指标变化" help="把结案前后玩家可见指标差异加入总结。" />
      </div>
    </Section>

    <Section title="四、自定义反馈规则" description="规则按优先级匹配来源；引用列表留空表示不限制。条件组内所有条件必须同时满足。" badge={form.rules.length}>
      <div className="outcome-rule-actions"><button type="button" onClick={addRule}>＋ 添加反馈规则</button></div>
      {!form.rules.length && <div className="outcome-editor-empty">尚未配置自定义规则。自动即时反馈与结案总结仍可独立运行。</div>}
      <div className="outcome-rule-list">{form.rules.map((rule, index) => <RuleCard key={rule.__editor_key} initiallyOpen={index === 0}>
        <summary><span><strong>{rule.display_name || '未命名规则'}</strong><code>{rule.rule_id || 'missing-id'}</code></span><span>{rule.enabled ? '启用' : '停用'} · {OUTCOME_DELIVERIES.find(item => item.key === rule.delivery)?.label || rule.delivery} · 优先级 {rule.priority}</span></summary>
        <div className="outcome-rule-body">
          <RuleToolbar index={index} length={form.rules.length} onMove={moveRule} onDuplicate={duplicateRule} onRemove={removeRule} />
          <div className="outcome-toggle-grid"><Toggle checked={rule.enabled} onChange={value => patchRule(index, { enabled: value })} label="启用该规则" help="停用规则会保留作者配置，但运行时不会匹配。" /></div>
          <div className="outcome-editor-grid four">
            <Field label="规则 ID" help="发布后应保持稳定；用于存档幂等和热重载兼容判断。"><input value={rule.rule_id} onChange={event => patchRule(index, { rule_id: event.target.value })} placeholder="frontier_supply_result" /></Field>
            <Field label="中文显示名" help="用于编辑器、诊断与公开规则概览。"><input value={rule.display_name} onChange={event => patchRule(index, { display_name: event.target.value })} placeholder="补给行动结果" /></Field>
            <Field label="优先级" help="-10000～10000；数值越高越先匹配。"><input type="number" min="-10000" max="10000" value={rule.priority} onChange={event => patchRule(index, { priority: event.target.value })} /></Field>
            <Field label="交付方式" help={OUTCOME_DELIVERIES.find(item => item.key === rule.delivery)?.help}><select value={rule.delivery} onChange={event => patchRule(index, { delivery: event.target.value })}>{OUTCOME_DELIVERIES.map(item => <option key={item.key} value={item.key}>{item.label}（{item.key}）</option>)}</select></Field>
            <Field label="延迟天数" help="0～36500；即时反馈通常为 0，延迟报告按模拟时间排队。"><input type="number" min="0" max="36500" step="0.1" value={rule.delay_days} onChange={event => patchRule(index, { delay_days: event.target.value })} /></Field>
          </div>

          <div className="outcome-source-picker"><strong>触发来源（至少选择一项）</strong><div>{OUTCOME_SOURCE_KINDS.map(item => <Toggle key={item.key} checked={rule.source_kinds.includes(item.key)} onChange={() => toggleSource(index, item.key)} label={`${item.label}（${item.key}）`} help={item.help} />)}</div></div>

          <div className="outcome-reference-grid">
            <ReferencePicker label="行动 ID" values={rule.action_ids} options={catalogs.actions} onChange={value => patchRule(index, { action_ids: value })} help="仅对 action 来源生效；留空匹配所有行动。" />
            <ReferencePicker label="Issue 类型 ID" values={rule.issue_type_ids} options={catalogs.issueTypes} onChange={value => patchRule(index, { issue_type_ids: value })} help="用于决策、结案或来源事项筛选。" />
            <ReferencePicker label="事件类型" values={rule.event_types} options={catalogs.eventTypes} onChange={value => patchRule(index, { event_types: value })} help="用于 causal_event、lifecycle 与 rhythm 来源筛选。" />
            <ReferencePicker label="派生指标 ID" values={rule.metric_ids} options={catalogs.metrics} onChange={value => patchRule(index, { metric_ids: value })} help="限制规则关注的指标；只允许引用 DLC 已定义指标。" />
          </div>

          <CausalConditionBuilder value={rule.conditions} entities={content.entities || []} resources={content.resources || []} actors={content.actors || []} agents={content.agents || []} relationshipTypes={relationshipTypes} objectives={content.objectives || []} derivedMetrics={content.derived_metrics || []} ongoingEvents={content.ongoing_events || []} eventTypes={eventTypes} onChange={value => patchRule(index, { conditions: value })} />
          <div className="outcome-hidden-note">这些条件只用于运行时判断是否交付。即使读取世界真相或 Actor 认知，玩家也不会直接看到条件类型、阈值或原始值。</div>

          <div className="outcome-editor-grid two copy-grid">
            <Field label="反馈标题" help="最多 240 字；留空时运行时使用来源显示名。" wide><input value={rule.title} onChange={event => patchRule(index, { title: event.target.value })} placeholder="补给线开始恢复" /></Field>
            <Field label="结果摘要" help="最多 4000 字；回答“发生了什么”。" wide><textarea rows="3" value={rule.summary} onChange={event => patchRule(index, { summary: event.target.value })} placeholder="运输队已抵达前线，短期补给压力有所下降。" /></Field>
            <Field label="确定性原因" help="最多 2000 字；回答“为什么发生”，不要写入隐藏条件值。" wide><textarea rows="3" value={rule.reason} onChange={event => patchRule(index, { reason: event.target.value })} placeholder="原因来自已完成的护送行动与公开资源变化。" /></Field>
            <Field label="因果揭示文本" help="因果揭示必填，最多 4000 字；用于条件满足后的事后说明。" wide><textarea rows="3" value={rule.reveal_text} onChange={event => patchRule(index, { reveal_text: event.target.value })} placeholder="数日后查明，早先的道路整修降低了运输损耗。" /></Field>
          </div>

          <div className="outcome-editor-grid two">
            <Field label="创建后续 Issue 类型" help="预留联动字段；留空不创建后续事项。"><select value={rule.create_issue_type_id} onChange={event => patchRule(index, { create_issue_type_id: event.target.value })}><option value="">不创建后续 Issue</option>{catalogs.issueTypes.map(item => <option key={item.id} value={item.id}>{item.label}（{item.id}）</option>)}</select></Field>
            <div className="outcome-toggle-grid"><Toggle checked={rule.attach_evidence} onChange={value => patchRule(index, { attach_evidence: value })} label="附加为事项证据" help="预留联动字段；运行时支持后可把公开反馈作为安全证据附加。" /></div>
          </div>
        </div>
      </RuleCard>)}</div>
    </Section>
  </div>
}
