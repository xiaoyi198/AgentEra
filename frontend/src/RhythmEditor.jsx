import { useMemo, useState } from 'react'
import { CausalConditionBuilder } from './DLCContentEditors.jsx'
import {
  RHYTHM_FINAL_STATUSES,
  RHYTHM_ISSUE_STATUSES,
  RHYTHM_URGENCIES,
  collectRhythmEventTypes,
  createRhythmRule,
  normalizeRhythmForEditor,
  serializeRhythm,
  validateRhythm,
} from './RhythmEditorTools.js'
import './RhythmEditor.css'

function Toggle({ checked, onChange, label, help }) {
  return <label className="rhythm-toggle">
    <input type="checkbox" checked={Boolean(checked)} onChange={event => onChange(event.target.checked)} />
    <span><strong>{label}</strong>{help && <small>{help}</small>}</span>
  </label>
}

function Field({ label, help, wide = false, children }) {
  return <label className={wide ? 'rhythm-field rhythm-wide' : 'rhythm-field'}>
    <span>{label}</span>
    {children}
    {help && <small>{help}</small>}
  </label>
}

function Section({ title, description, badge, children }) {
  return <section className="rhythm-section">
    <header><div><h3>{title}</h3><p>{description}</p></div>{badge !== undefined && <span className="rhythm-badge">{badge}</span>}</header>
    {children}
  </section>
}

function MultiIdField({ label, help, value, suggestions, emptyText, onChange }) {
  const items = Array.isArray(value) ? value : []
  const [draft, setDraft] = useState('')
  const add = raw => {
    const candidates = String(raw || '').split(/[，,\n]/).map(item => item.trim()).filter(Boolean)
    if (!candidates.length) return
    onChange([...new Set([...items, ...candidates])])
    setDraft('')
  }
  return <div className="rhythm-multi rhythm-wide">
    <div className="rhythm-multi-title"><strong>{label}</strong><small>{help}</small></div>
    <div className="rhythm-chips">
      {items.map(item => <button type="button" className="rhythm-chip selected" key={item} onClick={() => onChange(items.filter(valueItem => valueItem !== item))} title="点击移除">{item}<span>×</span></button>)}
      {!items.length && <span className="rhythm-muted">{emptyText}</span>}
    </div>
    <div className="rhythm-add-id"><input value={draft} onChange={event => setDraft(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); add(draft) } }} placeholder="输入 ID，回车或点击添加；可用逗号分隔" /><button type="button" onClick={() => add(draft)}>添加</button></div>
    {suggestions.length > 0 && <div className="rhythm-suggestions"><span>可选：</span>{suggestions.filter(item => !items.includes(item.id)).map(item => <button type="button" key={item.id} onClick={() => add(item.id)}>{item.label || item.id}<code>{item.id}</code></button>)}</div>}
  </div>
}

function ConditionEditor({ value, content, eventTypes, onChange }) {
  return <CausalConditionBuilder
    value={value}
    entities={content.entities || []}
    resources={content.resources || []}
    actors={content.actors || []}
    agents={content.agents || []}
    relationshipTypes={content.relationship_types || []}
    objectives={content.objectives || []}
    derivedMetrics={content.derived_metrics || []}
    ongoingEvents={content.ongoing_events || []}
    eventTypes={eventTypes}
    onChange={onChange}
  />
}

function RuleToolbar({ rule, index, length, onMove, onRemove }) {
  return <div className="rhythm-rule-toolbar">
    <div><strong>{rule.display_name || '未命名规则'}</strong><code>{rule.rule_id || 'missing-id'}</code></div>
    <div><button type="button" disabled={index === 0} onClick={() => onMove(index, -1)}>↑ 上移</button><button type="button" disabled={index === length - 1} onClick={() => onMove(index, 1)}>↓ 下移</button><button type="button" className="danger" onClick={() => onRemove(index)}>删除</button></div>
  </div>
}

export default function RhythmEditor({ content, onSaveContent, busy }) {
  const [form, setForm] = useState(() => normalizeRhythmForEditor(content.rhythm))
  const [message, setMessage] = useState('')
  const eventTypes = useMemo(() => collectRhythmEventTypes(content), [content])
  const issueSuggestions = useMemo(() => (content.issue_types || []).map(item => ({ id: item.issue_type_id || item.id, label: item.display_name || item.issue_type_id || item.id })).filter(item => item.id), [content])
  const eventSuggestions = useMemo(() => eventTypes.map(id => ({ id, label: id })), [eventTypes])
  const validation = useMemo(() => validateRhythm(form, content), [form, content])

  const patch = values => setForm(current => ({ ...current, ...values }))
  const patchRule = (index, values) => setForm(current => ({ ...current, rules: current.rules.map((rule, ruleIndex) => ruleIndex === index ? { ...rule, ...values } : rule) }))
  const patchNested = (index, section, values) => setForm(current => ({ ...current, rules: current.rules.map((rule, ruleIndex) => ruleIndex === index ? { ...rule, [section]: { ...rule[section], ...values } } : rule) }))
  const addRule = () => setForm(current => ({ ...current, rules: [...current.rules, createRhythmRule(current.rules.length)] }))
  const removeRule = index => {
    if (!window.confirm('确定删除这条节奏规则吗？')) return
    setForm(current => ({ ...current, rules: current.rules.filter((_, ruleIndex) => ruleIndex !== index) }))
  }
  const moveRule = (index, delta) => setForm(current => {
    const target = index + delta
    if (target < 0 || target >= current.rules.length) return current
    const rules = [...current.rules]
    const [item] = rules.splice(index, 1)
    rules.splice(target, 0, item)
    return { ...current, rules }
  })
  const changeMaxLevel = (index, rawValue) => {
    const maxLevel = Math.min(16, Math.max(1, Number(rawValue) || 1))
    const rule = form.rules[index]
    const urgencies = Array.from({ length: maxLevel + 1 }, (_, level) => rule.escalation.urgency_by_level[level] || rule.escalation.urgency_by_level.at(-1) || 'normal')
    const feedback = Array.from({ length: maxLevel }, (_, level) => rule.escalation.feedback_by_level[level] || '')
    patchNested(index, 'escalation', { max_level: maxLevel, urgency_by_level: urgencies, feedback_by_level: feedback })
  }
  const save = () => {
    try {
      const rhythm = serializeRhythm(form, content)
      onSaveContent({ ...content, rhythm }, '节奏导演配置已加入待保存变更')
      setMessage('校验通过。请在变更确认窗口中保存到 DLC。')
    } catch (error) {
      setMessage(error.message)
    }
  }

  return <div className="rhythm-editor">
    <div className="rhythm-hero">
      <div><span className="rhythm-kicker">DLC PACING DIRECTOR</span><h2>节奏导演</h2><p>用注意力预算、平静期、升级、降级与高潮规则控制世界事件密度。所有时间均为模拟世界天数，热重载后立即生效并尽量保留运行状态。</p></div>
      <div className="rhythm-health"><strong>{form.rules.length}</strong><span>条规则</span><em className={validation.valid ? 'ok' : 'bad'}>{validation.valid ? '配置可保存' : '需要修正'}</em></div>
    </div>

    <Section title="一、总开关与世界级配额" description="决定节奏系统是否接管 Issue / Event 准入，并限制同时占用玩家注意力的内容数量。">
      <div className="rhythm-toggle-row"><Toggle checked={form.enabled} onChange={value => patch({ enabled: value })} label="启用节奏导演" help="关闭后保留配置，但运行时不执行节奏限制。" /><Toggle checked={form.critical_bypasses_quiet} onChange={value => patch({ critical_bypasses_quiet: value })} label="危急事件绕过平静期" help="critical 仍会受活跃上限与注意力预算影响。" /></div>
      <div className="rhythm-grid rhythm-grid-4">
        <Field label="活跃 Issue 全局上限" help="允许同时活跃的 Issue 数量，范围 1～64。"><input type="number" min="1" max="64" value={form.max_active_issues} onChange={event => patch({ max_active_issues: event.target.value })} /></Field>
        <Field label="注意力预算" help="当前节奏池容量，范围 0～10000。"><input type="number" min="0" max="10000" step="0.1" value={form.attention_budget} onChange={event => patch({ attention_budget: event.target.value })} /></Field>
        <Field label="每日注意力恢复量" help="每经过一个模拟日补回的预算。"><input type="number" min="0" max="10000" step="0.1" value={form.attention_recovery_per_day} onChange={event => patch({ attention_recovery_per_day: event.target.value })} /></Field>
        <Field label="排队上限" help="暂不能进入世界的事件最多排队 1～2048 条。"><input type="number" min="1" max="2048" value={form.queue_limit} onChange={event => patch({ queue_limit: event.target.value })} /></Field>
        <Field label="最近反馈数量" help="保留给 UI/API 的最近节奏反馈，范围 1～256。"><input type="number" min="1" max="256" value={form.recent_feedback_limit} onChange={event => patch({ recent_feedback_limit: event.target.value })} /></Field>
        <Field label="全局平静期（天）" help="高潮或规则触发后，低优先级内容暂缓进入世界。"><input type="number" min="0" max="36500" step="0.1" value={form.quiet_period_days} onChange={event => patch({ quiet_period_days: event.target.value })} /></Field>
      </div>
    </Section>

    <Section title="二、紧迫度注意力成本" description="没有设置规则级覆盖成本时，运行时按 Issue 紧迫度扣除下列预算。成本越高，越不容易在拥挤时被准入。">
      <div className="rhythm-grid rhythm-grid-4">{RHYTHM_URGENCIES.map(item => <Field key={item.key} label={item.label + '（' + item.key + '）'} help="必须是非负有限数。"><input type="number" min="0" max="10000" step="0.1" value={form.urgency_costs[item.key]} onChange={event => patch({ urgency_costs: { ...form.urgency_costs, [item.key]: event.target.value } })} /></Field>)}</div>
    </Section>

    <Section title="三、节奏规则" description="按优先级匹配 Issue 类型或事件类型。拖动式排序暂未启用，可用上移/下移明确作者顺序；运行时仍会优先执行高优先级规则。" badge={form.rules.length}>
      <div className="rhythm-callout">建议：普通报告使用较低注意力成本；资源或安全危机启用升级；条件改善时降级；达到 critical 后用高潮完成收束并设置平静期。</div>
      <div className="rhythm-list-actions"><button type="button" onClick={addRule}>＋ 添加节奏规则</button></div>
      {!form.rules.length && <div className="rhythm-empty">尚未配置规则。启用总开关后，世界级配额仍会生效，但事件不会获得规则级升级、降级或高潮行为。</div>}
      <div className="rhythm-rule-list">{form.rules.map((rule, index) => <details className="rhythm-rule" key={'rhythm-rule-' + index} defaultOpen={index === 0}>
        <summary><span>{rule.display_name || '未命名规则'}<code>{rule.rule_id || 'missing-id'}</code></span><span>{rule.enabled ? '启用' : '停用'} · 优先级 {rule.priority}</span></summary>
        <div className="rhythm-rule-body">
          <RuleToolbar rule={rule} index={index} length={form.rules.length} onMove={moveRule} onRemove={removeRule} />
          <div className="rhythm-toggle-row"><Toggle checked={rule.enabled} onChange={value => patchRule(index, { enabled: value })} label="启用该规则" /><Toggle checked={rule.allow_during_quiet} onChange={value => patchRule(index, { allow_during_quiet: value })} label="绕过平静期" help="适合必须及时出现的系统性报告。" /></div>
          <div className="rhythm-grid rhythm-grid-4">
            <Field label="规则 ID" help="发布后建议保持稳定，只能使用安全 ID 字符。"><input value={rule.rule_id} onChange={event => patchRule(index, { rule_id: event.target.value })} placeholder="frontier_supply_crisis" /></Field>
            <Field label="中文显示名" help="用于编辑器、诊断和节奏反馈。"><input value={rule.display_name} onChange={event => patchRule(index, { display_name: event.target.value })} placeholder="粮草危机升级" /></Field>
            <Field label="优先级" help="-1000～1000；数值越高越先匹配。"><input type="number" min="-1000" max="1000" value={rule.priority} onChange={event => patchRule(index, { priority: event.target.value })} /></Field>
            <Field label="注意力覆盖成本" help="留空时使用紧迫度成本；填写后固定使用该值。"><input type="number" min="0" max="10000" step="0.1" value={rule.attention_cost} onChange={event => patchRule(index, { attention_cost: event.target.value })} placeholder="留空继承" /></Field>
            <Field label="规则级活跃上限" help="该规则同时允许 1～64 个活跃实例。"><input type="number" min="1" max="64" value={rule.max_active} onChange={event => patchRule(index, { max_active: event.target.value })} /></Field>
            <Field label="冷却天数" help="同一规则两次准入之间的最短模拟时间。"><input type="number" min="0" max="36500" step="0.1" value={rule.cooldown_days} onChange={event => patchRule(index, { cooldown_days: event.target.value })} /></Field>
            <Field label="互斥组" help="同组规则避免同时活跃；留空表示不互斥。"><input value={rule.mutual_exclusion_group} onChange={event => patchRule(index, { mutual_exclusion_group: event.target.value })} placeholder="frontier_crisis" /></Field>
            <Field label="规则平静期（天）" help="该规则被准入后追加的平静期。"><input type="number" min="0" max="36500" step="0.1" value={rule.quiet_period_days} onChange={event => patchRule(index, { quiet_period_days: event.target.value })} /></Field>
          </div>
          <div className="rhythm-grid rhythm-grid-2">
            <MultiIdField label="匹配 Issue 类型" help="从当前 DLC 的 Issue 类型中选择；引用不存在时禁止保存。" value={rule.issue_type_ids} suggestions={issueSuggestions} emptyText="未匹配 Issue 类型" onChange={value => patchRule(index, { issue_type_ids: value })} />
            <MultiIdField label="匹配事件类型" help="可选择已发现的事件类型，也可输入合法的自定义事件类型。" value={rule.event_types} suggestions={eventSuggestions} emptyText="未匹配事件类型" onChange={value => patchRule(index, { event_types: value })} />
          </div>

          <div className="rhythm-subsection">
            <div className="rhythm-subhead"><div><h4>升级链</h4><p>Issue 持续未解决时按时间逐级提高紧迫度，并向玩家输出阶段反馈。</p></div><Toggle checked={rule.escalation.enabled} onChange={value => patchNested(index, 'escalation', { enabled: value })} label="启用升级" /></div>
            <div className="rhythm-grid rhythm-grid-3">
              <Field label="起始天数" help="Issue 创建后等待多久开始第一次升级。"><input type="number" min="0" max="36500" step="0.1" value={rule.escalation.after_days} onChange={event => patchNested(index, 'escalation', { after_days: event.target.value })} /></Field>
              <Field label="间隔天数" help="每次升级之间的最短时间，必须大于 0。"><input type="number" min="0.000001" max="36500" step="0.1" value={rule.escalation.interval_days} onChange={event => patchNested(index, 'escalation', { interval_days: event.target.value })} /></Field>
              <Field label="最大等级" help="范围 1～16；0 级为初始状态。"><input type="number" min="1" max="16" value={rule.escalation.max_level} onChange={event => changeMaxLevel(index, event.target.value)} /></Field>
            </div>
            <div className="rhythm-levels"><div className="rhythm-level-head"><strong>等级紧迫度</strong><small>为 0 级到最大等级逐级指定 low / normal / high / critical。</small></div>{Array.from({ length: Number(rule.escalation.max_level) + 1 }, (_, level) => <div className="rhythm-level-row" key={'urgency-' + level}><span>等级 {level}</span><select value={rule.escalation.urgency_by_level[level] || 'normal'} onChange={event => { const values = [...rule.escalation.urgency_by_level]; values[level] = event.target.value; patchNested(index, 'escalation', { urgency_by_level: values }) }}>{RHYTHM_URGENCIES.map(item => <option key={item.key} value={item.key}>{item.label} · {item.key}</option>)}</select></div>)}</div>
            <div className="rhythm-feedback-list"><div><strong>等级反馈</strong><small>每次进入 1～最大等级时展示；可留空。</small></div>{Array.from({ length: Number(rule.escalation.max_level) }, (_, level) => <Field key={'feedback-' + level} label={'进入等级 ' + (level + 1)}><textarea rows="2" value={rule.escalation.feedback_by_level[level] || ''} onChange={event => { const values = [...rule.escalation.feedback_by_level]; values[level] = event.target.value; patchNested(index, 'escalation', { feedback_by_level: values }) }} placeholder="局势正在升级，请及时处理。" /></Field>)}</div>
          </div>

          <div className="rhythm-subsection">
            <div className="rhythm-subhead"><div><h4>条件降级</h4><p>世界条件改善后，将 Issue 降到目标等级并恢复为待处理或持续观察状态。</p></div><Toggle checked={rule.deescalation.enabled} onChange={value => patchNested(index, 'deescalation', { enabled: value })} label="启用降级" /></div>
            <div className="rhythm-grid rhythm-grid-3">
              <Field label="检查间隔天数" help="满足条件后至少隔多久再次降级，必须大于 0。"><input type="number" min="0.000001" max="36500" step="0.1" value={rule.deescalation.interval_days} onChange={event => patchNested(index, 'deescalation', { interval_days: event.target.value })} /></Field>
              <Field label="目标等级" help="必须在 0 与升级最大等级之间。"><input type="number" min="0" max={rule.escalation.max_level} value={rule.deescalation.target_level} onChange={event => patchNested(index, 'deescalation', { target_level: event.target.value })} /></Field>
              <Field label="降级后 Issue 状态" help="open 保持待处理；monitoring 转入持续观察。"><select value={rule.deescalation.issue_status} onChange={event => patchNested(index, 'deescalation', { issue_status: event.target.value })}>{RHYTHM_ISSUE_STATUSES.map(item => <option key={item.key} value={item.key}>{item.label} · {item.key}</option>)}</select></Field>
              <Field label="玩家反馈" help="条件改善并完成降级时展示。" wide><textarea rows="2" value={rule.deescalation.feedback} onChange={event => patchNested(index, 'deescalation', { feedback: event.target.value })} placeholder="补给恢复，危机等级已降低。" /></Field>
            </div>
            <ConditionEditor value={rule.deescalation.conditions} content={content} eventTypes={eventTypes} onChange={value => patchNested(index, 'deescalation', { conditions: value })} />
          </div>

          <div className="rhythm-subsection climax">
            <div className="rhythm-subhead"><div><h4>高潮与收束</h4><p>达到指定等级并满足条件后，关闭 Issue / Case、处理同组决策请求，并进入收束后的平静期。</p></div><Toggle checked={rule.climax.enabled} onChange={value => patchNested(index, 'climax', { enabled: value })} label="启用高潮" /></div>
            <div className="rhythm-grid rhythm-grid-4">
              <Field label="高潮等级" help="达到该等级后才开始等待高潮条件。"><input type="number" min="1" max={rule.escalation.max_level} value={rule.climax.level} onChange={event => patchNested(index, 'climax', { level: event.target.value })} /></Field>
              <Field label="延迟天数" help="达到高潮等级后继续等待的模拟天数。"><input type="number" min="0" max="36500" step="0.1" value={rule.climax.after_days} onChange={event => patchNested(index, 'climax', { after_days: event.target.value })} /></Field>
              <Field label="Issue 收束状态" help="高潮完成后写入 Issue 的终态。"><select value={rule.climax.issue_status} onChange={event => patchNested(index, 'climax', { issue_status: event.target.value })}>{RHYTHM_FINAL_STATUSES.map(item => <option key={item.key} value={item.key}>{item.label} · {item.key}</option>)}</select></Field>
              <Field label="Case 收束状态" help="高潮完成后写入关联 Case 的终态。"><select value={rule.climax.case_status} onChange={event => patchNested(index, 'climax', { case_status: event.target.value })}>{RHYTHM_FINAL_STATUSES.map(item => <option key={item.key} value={item.key}>{item.label} · {item.key}</option>)}</select></Field>
              <Field label="收束后平静期（天）" help="高潮完成后抑制新噪声的时间。"><input type="number" min="0" max="36500" step="0.1" value={rule.climax.quiet_period_days} onChange={event => patchNested(index, 'climax', { quiet_period_days: event.target.value })} /></Field>
            </div>
            <div className="rhythm-toggle-row"><Toggle checked={rule.climax.close_group} onChange={value => patchNested(index, 'climax', { close_group: value })} label="处理同组 Case 与决策请求" help="高潮收束时关闭关联 Case，并一并结束该组仍待处理的决策请求。" /></div>
            <Field label="高潮反馈文本" help="高潮完成时展示给玩家，建议明确说明结果与后续平静期。" wide><textarea rows="3" value={rule.climax.feedback} onChange={event => patchNested(index, 'climax', { feedback: event.target.value })} placeholder="危机已经收束，边境进入短暂恢复期。" /></Field>
            <ConditionEditor value={rule.climax.conditions} content={content} eventTypes={eventTypes} onChange={value => patchNested(index, 'climax', { conditions: value })} />
          </div>
        </div>
      </details>)}</div>
    </Section>

    <div className="rhythm-savebar">
      <div className={validation.valid ? 'rhythm-validation ok' : 'rhythm-validation bad'}><strong>{validation.valid ? '配置校验通过' : '配置尚不能保存'}</strong><span>{message || (validation.valid ? '保存后会进入 DLC Studio 的统一变更确认流程。' : validation.errors[0])}</span></div>
      <button type="button" className="primary" disabled={busy || !validation.valid} onClick={save}>{busy ? '处理中…' : '保存节奏导演配置'}</button>
    </div>
  </div>
}
