import { useEffect, useMemo, useState } from 'react'

const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/
const NUMERIC_OPERATORS = ['lt', 'lte', 'eq', 'ne', 'gte', 'gt']
const TEXT_OPERATORS = ['eq', 'ne']
const FACT_KINDS = [
  ['anchored_inventory', '锚定库存价值'],
  ['metric', '实体指标'],
  ['parameter', '实体参数'],
  ['entity_status', '实体状态'],
  ['sim_day', '模拟天数'],
  ['issue_count', 'Issue 数量'],
  ['case_count', 'Case 数量'],
  ['decision_count', '决策请求数量'],
  ['action_count', '行动成功次数'],
]
const OPERATOR_LABELS = { lt: '< 小于', lte: '≤ 小于等于', eq: '= 等于', ne: '≠ 不等于', gte: '≥ 大于等于', gt: '> 大于' }

function clone(value) {
  return JSON.parse(JSON.stringify(value))
}

function FieldHelp({ children }) {
  return <small className="dlc-field-help">{children}</small>
}

function firstEntity(content) {
  return content.entities?.[0]?.id || ''
}

function firstResource(content, kind) {
  const wanted = kind === 'anchored_inventory' ? 'asset' : kind
  return content.resources?.find(item => (item.kind || 'asset') === wanted)?.name || ''
}

function defaultFact(kind, content) {
  if (kind === 'sim_day') return { kind, operator: 'gte', value: 1 }
  if (kind === 'entity_status') return { kind, entity_id: firstEntity(content), operator: 'eq', value: '' }
  if (['issue_count', 'case_count', 'decision_count'].includes(kind)) return {
    kind, issue_type_id: content.issue_types?.[0]?.issue_type_id || '', status: '', operator: 'gte', value: 1,
  }
  if (kind === 'action_count') return {
    kind, action_id: content.actions?.[0]?.action_id || '', actor_id: '', control_object_id: '', target_id: '', operator: 'gte', value: 1,
  }
  return { kind, entity_id: firstEntity(content), name: firstResource(content, kind), operator: 'gte', value: 0 }
}

function defaultGroup(content) {
  return { op: 'all', label: '', conditions: [defaultFact('sim_day', content)] }
}

function stripEmpty(value) {
  if (Array.isArray(value)) return value.map(stripEmpty)
  if (!value || typeof value !== 'object') return value
  return Object.fromEntries(Object.entries(value)
    .filter(([key, item]) => key === 'value' || key === 'enabled' || (item !== '' && item !== undefined && item !== null))
    .map(([key, item]) => [key, stripEmpty(item)]))
}

function normalizeConditionForSave(value, path = '条件') {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${path}必须是 JSON 对象。`)
  if (value.op) {
    if (!['all', 'any', 'not'].includes(value.op)) throw new Error(`${path}的组合方式无效。`)
    const children = Array.isArray(value.conditions) ? value.conditions : []
    if ((value.op === 'not' && children.length !== 1) || (value.op !== 'not' && children.length === 0)) throw new Error(`${path}需要正确数量的子条件。`)
    return stripEmpty({ op: value.op, label: String(value.label || '').trim(), conditions: children.map((child, index) => normalizeConditionForSave(child, `${path}.${index + 1}`)) })
  }
  const kind = String(value.kind || '')
  if (!FACT_KINDS.some(([key]) => key === kind)) throw new Error(`${path}的事实类型无效。`)
  const result = { ...value, kind, label: String(value.label || '').trim(), operator: String(value.operator || 'gte') }
  if (kind === 'entity_status') {
    if (!result.entity_id || !TEXT_OPERATORS.includes(result.operator) || !String(result.value ?? '').trim()) throw new Error(`${path}需要实体、状态值和文本比较符。`)
    result.value = String(result.value)
  } else {
    result.value = Number(result.value)
    if (!NUMERIC_OPERATORS.includes(result.operator) || !Number.isFinite(result.value)) throw new Error(`${path}需要有效数值和比较符。`)
    if (['anchored_inventory', 'metric', 'parameter'].includes(kind) && (!result.entity_id || !result.name)) throw new Error(`${path}需要实体与资源/指标名称。`)
    if (kind === 'action_count' && !SAFE_ID.test(String(result.action_id || ''))) throw new Error(`${path}需要有效 Action ID。`)
  }
  return stripEmpty(result)
}

function AdvancedJsonEditor({ value, onApply, label = '高级 JSON 编辑' }) {
  const [text, setText] = useState(() => JSON.stringify(value, null, 2))
  const [error, setError] = useState('')
  useEffect(() => { setText(JSON.stringify(value, null, 2)); setError('') }, [value])
  const apply = () => {
    try {
      const parsed = JSON.parse(text)
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('必须是 JSON 对象')
      onApply(parsed)
      setError('')
    } catch (caught) { setError(caught.message) }
  }
  return <details className="dlc-advanced-json">
    <summary>{label}</summary>
    <p>适合复制、批量修改或粘贴已有契约；应用后仍会经过保存校验。</p>
    <textarea className="dlc-json-field" rows="12" spellCheck="false" value={text} onChange={event => setText(event.target.value)} />
    {error && <div className="dlc-editor-error">{error}</div>}
    <div><button type="button" onClick={() => setText(JSON.stringify(value, null, 2))}>从表单刷新 JSON</button><button type="button" className="primary" onClick={apply}>应用 JSON 到表单</button></div>
  </details>
}

function ConditionNode({ value, onChange, onRemove, content, depth = 0 }) {
  const grouped = Boolean(value?.op)
  const set = (key, next) => onChange({ ...value, [key]: next })
  const switchType = type => onChange(type === 'group' ? defaultGroup(content) : defaultFact('sim_day', content))
  const switchKind = kind => onChange({ ...defaultFact(kind, content), label: value?.label || '' })
  const entities = content.entities || []
  const resources = content.resources || []
  const matchingResources = resources.filter(item => (item.kind || 'asset') === (value.kind === 'anchored_inventory' ? 'asset' : value.kind))

  return <div className={`objective-condition-editor depth-${Math.min(depth, 4)}`}>
    <div className="objective-condition-editor-head">
      <label>节点类型<select value={grouped ? 'group' : 'fact'} onChange={event => switchType(event.target.value)}><option value="fact">事实条件</option><option value="group">组合条件</option></select></label>
      <label className="wide">条件说明（可选）<input value={value?.label || ''} onChange={event => set('label', event.target.value)} placeholder="例如：警戒度达到安全线" /></label>
      {onRemove && <button type="button" className="danger" onClick={onRemove}>删除节点</button>}
    </div>
    {grouped ? <>
      <div className="dlc-row">
        <label>组合方式<select value={value.op || 'all'} onChange={event => {
          const op = event.target.value
          const conditions = op === 'not' ? [(value.conditions || [defaultFact('sim_day', content)])[0]] : (value.conditions?.length ? value.conditions : [defaultFact('sim_day', content)])
          onChange({ ...value, op, conditions })
        }}><option value="all">全部满足（AND）</option><option value="any">任一满足（OR）</option><option value="not">取反（NOT，只允许一个子条件）</option></select><FieldHelp>组合节点可嵌套，最多由后端限制为 8 层。</FieldHelp></label>
      </div>
      <div className="objective-condition-children-editor">
        {(value.conditions || []).map((condition, index) => <ConditionNode key={index} value={condition} content={content} depth={depth + 1} onChange={next => set('conditions', value.conditions.map((item, itemIndex) => itemIndex === index ? next : item))} onRemove={() => set('conditions', value.conditions.filter((_, itemIndex) => itemIndex !== index))} />)}
      </div>
      {value.op !== 'not' && <button type="button" onClick={() => set('conditions', [...(value.conditions || []), defaultFact('sim_day', content)])}>＋ 添加子条件</button>}
    </> : <>
      <div className="dlc-row objective-fact-row">
        <label>事实类型<select value={value.kind || 'sim_day'} onChange={event => switchKind(event.target.value)}>{FACT_KINDS.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select><FieldHelp>只读取世界状态、Issue/Case、成功行动回执和模拟时间。</FieldHelp></label>
        {['anchored_inventory', 'metric', 'parameter', 'entity_status'].includes(value.kind) && <label>实体<select value={value.entity_id || ''} onChange={event => set('entity_id', event.target.value)}><option value="">请选择实体</option>{entities.map(entity => <option key={entity.id} value={entity.id}>{entity.name || entity.id} ({entity.id})</option>)}</select><FieldHelp>条件读取该实体的权威状态。</FieldHelp></label>}
        {['anchored_inventory', 'metric', 'parameter'].includes(value.kind) && <label>资源 / 指标<select value={value.name || ''} onChange={event => set('name', event.target.value)}><option value="">请选择</option>{matchingResources.map(resource => <option key={resource.name} value={resource.name}>{resource.name}{resource.unit ? ` (${resource.unit})` : ''}</option>)}</select><FieldHelp>{value.kind === 'anchored_inventory' ? '按数量 × anchor_rate 计算统一锚定价值。' : '读取实体对应 kind 的数值。'}</FieldHelp></label>}
        {value.kind === 'action_count' && <label>Action ID<select value={value.action_id || ''} onChange={event => set('action_id', event.target.value)}><option value="">请选择行动</option>{(content.actions || []).map(action => <option key={action.action_id} value={action.action_id}>{action.display_name || action.action_id} ({action.action_id})</option>)}</select><FieldHelp>只统计成功且已有幂等回执的行动执行。</FieldHelp></label>}
        {['issue_count', 'case_count', 'decision_count'].includes(value.kind) && <label>Issue 类型（可选）<select value={value.issue_type_id || ''} onChange={event => set('issue_type_id', event.target.value)}><option value="">全部类型</option>{(content.issue_types || []).map(item => <option key={item.issue_type_id} value={item.issue_type_id}>{item.display_name || item.issue_type_id}</option>)}</select><FieldHelp>留空时统计所有 Issue 类型。</FieldHelp></label>}
        {['issue_count', 'case_count', 'decision_count'].includes(value.kind) && <label>状态过滤（可选）<input value={value.status || ''} onChange={event => set('status', event.target.value)} placeholder="例如 open / pending / closed" /><FieldHelp>必须与运行时状态字符串完全一致；留空表示全部状态。</FieldHelp></label>}
        <label>比较符<select value={value.operator || 'gte'} onChange={event => set('operator', event.target.value)}>{(value.kind === 'entity_status' ? TEXT_OPERATORS : NUMERIC_OPERATORS).map(operator => <option key={operator} value={operator}>{OPERATOR_LABELS[operator]}</option>)}</select><FieldHelp>比较当前事实与目标值。</FieldHelp></label>
        <label>目标值<input type={value.kind === 'entity_status' ? 'text' : 'number'} step="any" value={value.value ?? ''} onChange={event => set('value', event.target.value)} placeholder={value.kind === 'entity_status' ? '例如 active' : '0'} /><FieldHelp>{value.kind === 'sim_day' ? '1 表示模拟第 1 天。' : '达到比较结果后条件成立。'}</FieldHelp></label>
      </div>
      {value.kind === 'action_count' && <div className="dlc-row objective-action-filters"><label>Actor ID（可选）<input value={value.actor_id || ''} onChange={event => set('actor_id', event.target.value)} /></label><label>控制对象 ID（可选）<input value={value.control_object_id || ''} onChange={event => set('control_object_id', event.target.value)} /></label><label>目标 ID（可选）<input value={value.target_id || ''} onChange={event => set('target_id', event.target.value)} /></label></div>}
    </>}
  </div>
}

function ConditionBuilder({ label, value, onChange, content, allowEmpty = false }) {
  return <section className="objective-condition-builder">
    <div className="objective-builder-title"><div><strong>{label}</strong><FieldHelp>LLM 和客户端不能直接修改完成状态；框架每次从权威事实重新判定。</FieldHelp></div>{allowEmpty && value && <button type="button" className="danger" onClick={() => onChange(null)}>移除条件</button>}</div>
    {!value ? <button type="button" onClick={() => onChange(defaultFact('sim_day', content))}>＋ 添加判定条件</button> : <>
      <ConditionNode value={value} onChange={onChange} content={content} />
      <AdvancedJsonEditor value={value} onApply={onChange} label="高级条件 JSON" />
    </>}
  </section>
}

function EditorShell({ kind, items, selected, onSelect, formTitle, onDelete, children }) {
  const selectedIndex = selected === '' ? -1 : Number(selected)
  return <div className="dlc-structured-grid">
    <aside className="dlc-structured-list">
      <div className="dlc-structured-list-head"><div><h3>{kind === 'objectives' ? '目标与失败条件' : '结局'}</h3><span>{items.length}</span></div><button type="button" onClick={() => onSelect('')}>＋ 新建</button></div>
      {items.length === 0 && <div className="dlc-structured-empty">尚未定义。无目标且无结局时，DLC 将明确作为沙盒运行。</div>}
      {items.map((item, index) => <button type="button" key={item.objective_id || item.ending_id || index} className={`dlc-structured-item ${selectedIndex === index ? 'active' : ''}`} onClick={() => onSelect(String(index))}>
        <strong>{item.display_name || item.objective_id || item.ending_id}</strong>
        <small>{kind === 'objectives' ? `${item.objective_id} · ${item.category || 'primary'}` : `${item.ending_id} · ${item.outcome || 'neutral'} · P${item.priority || 0}`}</small>
      </button>)}
    </aside>
    <div className="dlc-structured-form">
      <div className="dlc-objective-form-title"><div><h3>{formTitle}</h3><p>{kind === 'objectives' ? '定义可验证的目标、失败条件和里程碑。' : '结局只引用已声明目标；同 Tick 多结局满足时按优先级再按 ID 决定。'}</p></div>{selectedIndex >= 0 && <button type="button" className="danger" onClick={onDelete}>删除</button>}</div>
      {children}
    </div>
  </div>
}

function normalizeObjectiveForm(source) {
  const result = {
    objective_id: '', display_name: '', description: '', category: 'primary', required: true,
    milestone_mode: 'ordered', milestones: [], completion: { kind: 'sim_day', operator: 'gte', value: 1 }, enabled: true, order: 0,
    ...clone(source || {}),
  }
  result.milestones = clone(source?.milestones || [])
  result.completion = source && Object.hasOwn(source, 'completion') ? clone(source.completion) : { kind: 'sim_day', operator: 'gte', value: 1 }
  return result
}

export function ObjectiveEditor({ content, selected, onSelect, onSaveContent, busy = false }) {
  const items = content.objectives || []
  const selectedIndex = selected === '' ? -1 : Number(selected)
  const [form, setForm] = useState(() => normalizeObjectiveForm(selectedIndex >= 0 ? items[selectedIndex] : null))
  const [error, setError] = useState('')
  const set = (key, value) => setForm(current => ({ ...current, [key]: value }))
  const save = () => {
    try {
      const objective = {
        ...form,
        objective_id: String(form.objective_id || '').trim(), display_name: String(form.display_name || '').trim(), description: String(form.description || '').trim(),
        required: Boolean(form.required), enabled: Boolean(form.enabled), order: Number(form.order),
        milestones: (form.milestones || []).map((milestone, index) => ({
          milestone_id: String(milestone.milestone_id || '').trim(), display_name: String(milestone.display_name || '').trim(), description: String(milestone.description || '').trim(),
          condition: normalizeConditionForSave(milestone.condition, `里程碑 ${index + 1}`),
        })),
        completion: form.completion ? normalizeConditionForSave(form.completion, '目标完成条件') : null,
      }
      if (!SAFE_ID.test(objective.objective_id)) throw new Error('目标 ID 必须为 1-64 位安全标识符（字母、数字、点、下划线或连字符）。')
      if (!objective.display_name) throw new Error('目标显示名称不能为空。')
      if (!['primary', 'optional', 'failure'].includes(objective.category)) throw new Error('目标分类无效。')
      if (!['ordered', 'independent'].includes(objective.milestone_mode)) throw new Error('里程碑模式无效。')
      if (!Number.isInteger(objective.order)) throw new Error('显示顺序必须是整数。')
      if (!objective.milestones.length && !objective.completion) throw new Error('目标至少需要一个里程碑或完成条件。')
      const ids = objective.milestones.map(item => item.milestone_id)
      if (ids.some(id => !SAFE_ID.test(id)) || new Set(ids).size !== ids.length) throw new Error('里程碑 ID 必须有效且在同一目标内唯一。')
      if (objective.milestones.some(item => !item.display_name)) throw new Error('每个里程碑都需要显示名称。')
      const next = items.map(clone)
      if (selectedIndex >= 0) next[selectedIndex] = stripEmpty(objective)
      else next.push(stripEmpty(objective))
      onSaveContent({ ...content, objectives: next }, '目标契约已保存')
      setError('')
    } catch (caught) { setError(caught.message) }
  }
  const remove = () => {
    if (!window.confirm(`删除目标“${form.display_name || form.objective_id}”？引用它的结局也需要同步修改。`)) return
    onSaveContent({ ...content, objectives: items.filter((_, index) => index !== selectedIndex) }, '目标已删除')
  }
  const updateMilestone = (index, next) => set('milestones', form.milestones.map((item, itemIndex) => itemIndex === index ? next : item))
  const rawForm = useMemo(() => stripEmpty(form), [form])

  return <EditorShell kind="objectives" items={items} selected={selected} onSelect={onSelect} formTitle={selectedIndex >= 0 ? '编辑目标' : '新建目标'} onDelete={remove}>
    <div className="dlc-row"><label>目标 ID<input value={form.objective_id || ''} onChange={event => set('objective_id', event.target.value)} placeholder="secure_frontier" /><FieldHelp>稳定且唯一；保存后不建议修改，结局会通过它引用目标。</FieldHelp></label><label>显示名称<input value={form.display_name || ''} onChange={event => set('display_name', event.target.value)} placeholder="守住边境" /><FieldHelp>玩家在主界面看到的目标名称。</FieldHelp></label></div>
    <label>目标描述<textarea rows="3" value={form.description || ''} onChange={event => set('description', event.target.value)} placeholder="说明为什么要完成该目标以及玩家应关注什么。" /><FieldHelp>只用于展示和叙事，不参与权威判定。</FieldHelp></label>
    <div className="dlc-row"><label>目标分类<select value={form.category || 'primary'} onChange={event => {
      const category = event.target.value
      setForm(current => ({ ...current, category, required: category === 'primary' ? current.required : false }))
    }}><option value="primary">主目标</option><option value="optional">可选目标</option><option value="failure">失败条件</option></select><FieldHelp>failure 达成通常由失败结局引用；框架不会仅凭分类自动结束游戏。</FieldHelp></label><label>里程碑模式<select value={form.milestone_mode || 'ordered'} onChange={event => set('milestone_mode', event.target.value)}><option value="ordered">按顺序解锁</option><option value="independent">可独立完成</option></select><FieldHelp>有序模式下，后续里程碑在前一项完成前显示为未解锁。</FieldHelp></label><label>显示顺序<input type="number" step="1" value={form.order ?? 0} onChange={event => set('order', event.target.value)} /><FieldHelp>数字越小越靠前；相同值按目标 ID 排序。</FieldHelp></label></div>
    <div className="dlc-row"><label className="dlc-checkbox"><input type="checkbox" checked={form.required !== false} onChange={event => set('required', event.target.checked)} />必需目标<FieldHelp>供 UI 和内容语义使用；结局仍以 trigger.objective_ids 为准。</FieldHelp></label><label className="dlc-checkbox"><input type="checkbox" checked={form.enabled !== false} onChange={event => set('enabled', event.target.checked)} />启用目标<FieldHelp>停用后不推进，但已有完成回执不会丢失。</FieldHelp></label></div>

    <section className="objective-milestone-editor">
      <div className="objective-builder-title"><div><strong>里程碑</strong><FieldHelp>里程碑完成后生成只写一次的权威回执；热重载和读档都会保留。</FieldHelp></div><button type="button" onClick={() => set('milestones', [...(form.milestones || []), { milestone_id: `step_${form.milestones.length + 1}`, display_name: `里程碑 ${form.milestones.length + 1}`, description: '', condition: defaultFact('sim_day', content) }])}>＋ 添加里程碑</button></div>
      {(form.milestones || []).length === 0 && <div className="dlc-structured-empty">可不配置里程碑，直接使用下方完成条件。</div>}
      {(form.milestones || []).map((milestone, index) => <article className="objective-milestone-form" key={index}>
        <div className="dlc-row"><label>里程碑 ID<input value={milestone.milestone_id || ''} onChange={event => updateMilestone(index, { ...milestone, milestone_id: event.target.value })} /></label><label>显示名称<input value={milestone.display_name || ''} onChange={event => updateMilestone(index, { ...milestone, display_name: event.target.value })} /></label><button type="button" className="danger" onClick={() => set('milestones', form.milestones.filter((_, itemIndex) => itemIndex !== index))}>删除里程碑</button></div>
        <label>说明<input value={milestone.description || ''} onChange={event => updateMilestone(index, { ...milestone, description: event.target.value })} placeholder="向玩家解释这一步代表什么。" /></label>
        <ConditionBuilder label={`里程碑 ${index + 1} 判定`} value={milestone.condition} onChange={condition => updateMilestone(index, { ...milestone, condition })} content={content} />
      </article>)}
    </section>
    <ConditionBuilder label={form.category === 'failure' ? '失败触发条件' : '目标完成条件'} value={form.completion} onChange={value => set('completion', value)} content={content} allowEmpty={(form.milestones || []).length > 0} />
    <AdvancedJsonEditor value={rawForm} onApply={value => setForm(normalizeObjectiveForm(value))} label="高级目标 JSON" />
    {error && <div className="dlc-editor-error">{error}</div>}
    <div className="dlc-form-actions"><button type="button" className="primary" disabled={busy || !form.objective_id || !form.display_name} onClick={save}>保存目标</button></div>
  </EditorShell>
}

function normalizeEndingForm(source) {
  const result = {
    ending_id: '', display_name: '', description: '', outcome: 'victory', trigger: { objective_ids: [], operator: 'all' }, priority: 0, enabled: true,
    ...clone(source || {}),
  }
  result.trigger = { objective_ids: [], operator: 'all', ...clone(source?.trigger || {}) }
  return result
}

export function EndingEditor({ content, selected, onSelect, onSaveContent, busy = false }) {
  const items = content.endings || []
  const selectedIndex = selected === '' ? -1 : Number(selected)
  const [form, setForm] = useState(() => normalizeEndingForm(selectedIndex >= 0 ? items[selectedIndex] : null))
  const [error, setError] = useState('')
  const set = (key, value) => setForm(current => ({ ...current, [key]: value }))
  const objectiveIds = new Set(form.trigger?.objective_ids || [])
  const toggleObjective = id => {
    const next = new Set(objectiveIds)
    if (next.has(id)) next.delete(id); else next.add(id)
    set('trigger', { ...form.trigger, objective_ids: [...next] })
  }
  const save = () => {
    try {
      const ending = {
        ...form,
        ending_id: String(form.ending_id || '').trim(), display_name: String(form.display_name || '').trim(), description: String(form.description || '').trim(),
        trigger: { objective_ids: [...new Set(form.trigger?.objective_ids || [])], operator: form.trigger?.operator || 'all' },
        priority: Number(form.priority), enabled: Boolean(form.enabled),
      }
      if (!SAFE_ID.test(ending.ending_id)) throw new Error('结局 ID 必须为 1-64 位安全标识符。')
      if (!ending.display_name) throw new Error('结局显示名称不能为空。')
      if (!['victory', 'defeat', 'neutral'].includes(ending.outcome)) throw new Error('结局结果无效。')
      if (!['all', 'any'].includes(ending.trigger.operator) || !ending.trigger.objective_ids.length) throw new Error('结局至少需要引用一个目标。')
      if (!Number.isInteger(ending.priority)) throw new Error('结局优先级必须是整数。')
      const known = new Set((content.objectives || []).map(item => item.objective_id))
      const unknown = ending.trigger.objective_ids.filter(id => !known.has(id))
      if (unknown.length) throw new Error(`结局引用了不存在的目标：${unknown.join(', ')}`)
      const next = items.map(clone)
      if (selectedIndex >= 0) next[selectedIndex] = stripEmpty(ending); else next.push(stripEmpty(ending))
      onSaveContent({ ...content, endings: next }, '结局契约已保存')
      setError('')
    } catch (caught) { setError(caught.message) }
  }
  const remove = () => {
    if (!window.confirm(`删除结局“${form.display_name || form.ending_id}”？`)) return
    onSaveContent({ ...content, endings: items.filter((_, index) => index !== selectedIndex) }, '结局已删除')
  }
  const rawForm = useMemo(() => stripEmpty(form), [form])

  return <EditorShell kind="endings" items={items} selected={selected} onSelect={onSelect} formTitle={selectedIndex >= 0 ? '编辑结局' : '新建结局'} onDelete={remove}>
    <div className="dlc-row"><label>结局 ID<input value={form.ending_id || ''} onChange={event => set('ending_id', event.target.value)} placeholder="frontier_safe" /><FieldHelp>稳定且唯一；用于存档回执和 API。</FieldHelp></label><label>显示名称<input value={form.display_name || ''} onChange={event => set('display_name', event.target.value)} placeholder="边境安定" /><FieldHelp>结局触发后显示给玩家。</FieldHelp></label></div>
    <label>结局描述<textarea rows="4" value={form.description || ''} onChange={event => set('description', event.target.value)} placeholder="总结玩家达成该结局后的世界状态。" /><FieldHelp>描述只负责展示；是否触发完全由目标回执决定。</FieldHelp></label>
    <div className="dlc-row"><label>结局结果<select value={form.outcome || 'neutral'} onChange={event => set('outcome', event.target.value)}><option value="victory">胜利</option><option value="defeat">失败</option><option value="neutral">中性</option></select><FieldHelp>决定主界面视觉语义，不改变优先级。</FieldHelp></label><label>目标组合<select value={form.trigger?.operator || 'all'} onChange={event => set('trigger', { ...form.trigger, operator: event.target.value })}><option value="all">全部引用目标都达成</option><option value="any">任一引用目标达成</option></select><FieldHelp>仅检查下方明确勾选的目标。</FieldHelp></label><label>优先级<input type="number" step="1" value={form.priority ?? 0} onChange={event => set('priority', event.target.value)} /><FieldHelp>同一 Tick 多个结局满足时，数字更高者优先；相同则按 ending_id。</FieldHelp></label></div>
    <fieldset className="objective-ending-targets"><legend>引用目标</legend><FieldHelp>结局必须至少引用一个目标。失败结局通常勾选 failure 分类目标。</FieldHelp>{(content.objectives || []).length ? (content.objectives || []).map(objective => <label key={objective.objective_id}><input type="checkbox" checked={objectiveIds.has(objective.objective_id)} onChange={() => toggleObjective(objective.objective_id)} /><span><strong>{objective.display_name || objective.objective_id}</strong><small>{objective.objective_id} · {objective.category || 'primary'}</small></span></label>) : <div className="dlc-structured-empty">请先在“目标”页签创建目标。</div>}</fieldset>
    <label className="dlc-checkbox"><input type="checkbox" checked={form.enabled !== false} onChange={event => set('enabled', event.target.checked)} />启用结局<FieldHelp>停用结局不会进入 eligible；已经锁定的历史结局不会反转。</FieldHelp></label>
    <AdvancedJsonEditor value={rawForm} onApply={value => setForm(normalizeEndingForm(value))} label="高级结局 JSON" />
    {error && <div className="dlc-editor-error">{error}</div>}
    <div className="dlc-form-actions"><button type="button" className="primary" disabled={busy || !form.ending_id || !form.display_name} onClick={save}>保存结局</button></div>
  </EditorShell>
}
