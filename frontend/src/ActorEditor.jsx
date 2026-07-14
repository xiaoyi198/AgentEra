import { useEffect, useMemo, useState } from 'react'

const EMPTY_ACTOR = {
  actor_id: '', display_name: '', description: '', actor_type: 'person', entity_id: '', enabled: true,
  attributes: {}, traits: [], needs: [], goals: [], capability_ids: [], identities: [], memberships: [], states: {}, metadata: {},
}

const COMPONENTS = {
  traits: {
    title: 'Trait / 特质', help: '稳定的人格或行为倾向。intensity 必须在 0~1；tags 用逗号分隔。',
    empty: { trait_id: '', display_name: '', description: '', intensity: 1, tags: [] },
    fields: [['trait_id', 'Trait ID'], ['display_name', '显示名称'], ['description', '说明'], ['intensity', '强度 0~1', 'number'], ['tags', '标签（逗号分隔）', 'csv']],
  },
  needs: {
    title: 'Need / 需求', help: '会随世界模拟变化的需求值。current 与 target 必须位于 minimum~maximum。',
    empty: { need_id: '', display_name: '', description: '', current: 50, target: 80, minimum: 0, maximum: 100, priority: 50 },
    fields: [['need_id', 'Need ID'], ['display_name', '显示名称'], ['description', '说明'], ['current', '初始值', 'number'], ['target', '目标值', 'number'], ['minimum', '最小值', 'number'], ['maximum', '最大值', 'number'], ['priority', '优先级 0~100', 'number']],
  },
  goals: {
    title: 'Goal / 目标', help: 'Actor 自身目标。运行时只保存 status 与 progress，热重载时按新定义安全合并。',
    empty: { goal_id: '', display_name: '', description: '', status: 'active', priority: 50, progress: 0, target: 100 },
    fields: [['goal_id', 'Goal ID'], ['display_name', '显示名称'], ['description', '说明'], ['status', '状态'], ['priority', '优先级 0~100', 'number'], ['progress', '初始进度', 'number'], ['target', '目标进度', 'number']],
  },
  identities: {
    title: 'Identity / 身份', help: '一个 Actor 可以同时具有公开或非公开身份；kind 可填写 role、title、cover 等。',
    empty: { identity_id: '', display_name: '', kind: 'role', public: true, description: '' },
    fields: [['identity_id', 'Identity ID'], ['display_name', '显示名称'], ['kind', '身份类型'], ['public', '公开身份', 'bool'], ['description', '说明']],
  },
  memberships: {
    title: 'Membership / 成员关系', help: '引用 Entities 页中的组织实体。每个 organization_id 只能出现一次，primary 表示主要归属。',
    empty: { organization_id: '', role: 'member', status: 'active', rank: null, primary: false, description: '' },
    fields: [['organization_id', '组织实体 ID'], ['role', '成员角色'], ['status', '状态'], ['rank', '组织内等级', 'nullable-number'], ['primary', '主要归属', 'bool'], ['description', '说明']],
  },
}

function clone(value) { return JSON.parse(JSON.stringify(value)) }
function jsonText(value) { return JSON.stringify(value || {}, null, 2) }

function JsonField({ label, help, value, onChange, rows = 6 }) {
  return <label>{label}<textarea className="dlc-json-field" rows={rows} spellCheck="false" value={value} onChange={event => onChange(event.target.value)} /><small className="dlc-field-help">{help}</small></label>
}

function ComponentEditor({ kind, items, onChange }) {
  const spec = COMPONENTS[kind]
  const setField = (index, name, value) => onChange(items.map((item, position) => position === index ? { ...item, [name]: value } : item))
  return <section className="dlc-blocks"><div className="dlc-blocks-header"><div><h4>{spec.title}</h4><small className="dlc-field-help">{spec.help}</small></div><button type="button" onClick={() => onChange([...items, clone(spec.empty)])}>＋ 添加</button></div>{items.map((item, index) => <article className="dlc-prompt-block" key={kind + '-' + index}><div className="dlc-block-toolbar"><strong>#{index + 1}</strong><button type="button" className="danger" onClick={() => onChange(items.filter((_, position) => position !== index))}>删除</button></div><div className="dlc-row">{spec.fields.map(([name, label, type]) => <label key={name}>{label}{type === 'bool' ? <span className="dlc-check"><input type="checkbox" checked={item[name] === true} onChange={event => setField(index, name, event.target.checked)} /> 启用</span> : <input type={type === 'number' || type === 'nullable-number' ? 'number' : 'text'} step={type === 'number' || type === 'nullable-number' ? 'any' : undefined} value={type === 'csv' ? (item[name] || []).join(', ') : (item[name] ?? '')} onChange={event => { const raw = event.target.value; const value = type === 'csv' ? raw.split(',').map(entry => entry.trim()).filter(Boolean) : type === 'number' ? Number(raw) : type === 'nullable-number' ? (raw === '' ? null : Number(raw)) : raw; setField(index, name, value) }} />}</label>)}</div></article>)}</section>
}

export default function ActorEditor({ content, selected, onSelect, onSaveContent, busy }) {
  const actors = content.actors || []
  const found = actors.find(item => (item.actor_id || item.id) === selected)
  const [form, setForm] = useState(() => clone(found || EMPTY_ACTOR))
  const [json, setJson] = useState(() => ({ attributes: jsonText(found?.attributes), states: jsonText(found?.states), metadata: jsonText(found?.metadata) }))
  const [error, setError] = useState('')
  useEffect(() => {
    const next = clone(found || EMPTY_ACTOR)
    setForm(next)
    setJson({ attributes: jsonText(next.attributes), states: jsonText(next.states), metadata: jsonText(next.metadata) })
    setError('')
  }, [selected]) // eslint-disable-line react-hooks/exhaustive-deps
  const capabilityIds = useMemo(() => (content.capabilities || []).map(item => item.capability_id).filter(Boolean), [content.capabilities])
  const set = (name, value) => setForm(current => ({ ...current, [name]: value }))
  const save = () => {
    try {
      const actor = clone(form)
      actor.actor_id = String(actor.actor_id || '').trim()
      if (!actor.actor_id) throw new Error('Actor ID 不能为空')
      for (const name of ['attributes', 'states', 'metadata']) {
        const parsed = JSON.parse(json[name] || '{}')
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error(name + ' 必须是 JSON 对象')
        actor[name] = parsed
      }
      const next = [...actors]
      const index = next.findIndex(item => (item.actor_id || item.id) === selected)
      if (index >= 0) next[index] = actor
      else next.push(actor)
      onSaveContent({ ...content, actors: next }, actor.actor_id + ' Actor 已保存')
    } catch (reason) { setError(reason.message) }
  }
  const remove = () => {
    if (!selected || !window.confirm('删除 Actor ' + selected + '？显式引用它的 Agent 将无法通过校验。')) return
    onSaveContent({ ...content, actors: actors.filter(item => (item.actor_id || item.id) !== selected) }, selected + ' Actor 已删除')
  }
  return <div className="dlc-content-grid"><div className="dlc-item-list"><button className="dlc-new" onClick={() => onSelect('')}>＋ 新建 Actor</button>{actors.map(actor => <button key={actor.actor_id || actor.id} className={selected === (actor.actor_id || actor.id) ? 'active' : ''} onClick={() => onSelect(actor.actor_id || actor.id)}><strong>{actor.display_name || actor.name || actor.actor_id}</strong><small>{actor.actor_type || 'person'} · {actor.actor_id || actor.id}</small></button>)}</div><div className="dlc-form"><div className="dlc-form-title"><h3>{selected ? '编辑 Actor' : '新建 Actor'}</h3>{selected && <code>{selected}</code>}</div><div className="dlc-row"><label>Actor ID<input value={form.actor_id || ''} disabled={Boolean(selected)} onChange={event => set('actor_id', event.target.value)} placeholder="frontier_guard" /><small className="dlc-field-help">跨 DLC 唯一的稳定标识。Agent 通过 actor_id 引用；允许中文，但不能含空格、斜杠或控制字符。</small></label><label>显示名称<input value={form.display_name || ''} onChange={event => set('display_name', event.target.value)} /><small className="dlc-field-help">界面和 Prompt 中展示的名称，与技术 ID 分离。</small></label></div><div className="dlc-row"><label>Actor 类型<select value={form.actor_type || 'person'} onChange={event => set('actor_type', event.target.value)}>{['person','organization','institution','group','creature','system','other'].map(value => <option key={value}>{value}</option>)}</select><small className="dlc-field-help">决定兼容 Official 投影与默认 Agent 类型，不限制可添加的组件。</small></label><label>绑定实体 ID<input list="actor-entity-options" value={form.entity_id || ''} onChange={event => set('entity_id', event.target.value)} /><datalist id="actor-entity-options">{(content.entities || []).map(entity => <option key={entity.id} value={entity.id}>{entity.name}</option>)}</datalist><small className="dlc-field-help">可选。引用 Entities 页中的地点、组织、角色或其他世界实体。</small></label></div><label>说明<textarea rows="4" value={form.description || ''} onChange={event => set('description', event.target.value)} /><small className="dlc-field-help">描述 Actor 在世界中的职责、背景与模拟边界。</small></label><label className="dlc-check"><input type="checkbox" checked={form.enabled !== false} onChange={event => set('enabled', event.target.checked)} /> 启用 Actor<small className="dlc-field-help">禁用后，关联 Agent 不创建自主 LLM Runtime，但定义仍可编辑和投影。</small></label><JsonField label="Attributes / 数值属性 JSON" help="只允许有限数字，例如 rank、health、influence。运行时只保留新定义中仍存在的键。" value={json.attributes} onChange={value => setJson(current => ({ ...current, attributes: value }))} /><ComponentEditor kind="traits" items={form.traits || []} onChange={value => set('traits', value)} /><ComponentEditor kind="needs" items={form.needs || []} onChange={value => set('needs', value)} /><ComponentEditor kind="goals" items={form.goals || []} onChange={value => set('goals', value)} /><section className="dlc-blocks"><div className="dlc-blocks-header"><div><h4>Capability / 能力引用</h4><small className="dlc-field-help">勾选 Capabilities 页已定义的能力。Registry 会阻止未知 ID；Prompt 可使用 actor_capabilities。</small></div></div>{capabilityIds.length ? <div className="dlc-row">{capabilityIds.map(id => <label className="dlc-check" key={id}><input type="checkbox" checked={(form.capability_ids || []).includes(id)} onChange={event => set('capability_ids', event.target.checked ? [...new Set([...(form.capability_ids || []), id])] : (form.capability_ids || []).filter(value => value !== id))} /> {id}</label>)}</div> : <small className="dlc-field-help">当前 DLC 尚未定义能力，可先到“能力”页创建。</small>}</section><ComponentEditor kind="identities" items={form.identities || []} onChange={value => set('identities', value)} /><ComponentEditor kind="memberships" items={form.memberships || []} onChange={value => set('memberships', value)} /><JsonField label="States / 初始状态 JSON" help="只允许字符串、数字、布尔值或 null。动态状态保存在 Runtime overlay，不反写 DLC Generation。" value={json.states} onChange={value => setJson(current => ({ ...current, states: value }))} /><JsonField label="Metadata / 扩展元数据 JSON" help="供 DLC 扩展使用，限制深度、条目数和字符串长度；不会完整无限注入 Prompt。" value={json.metadata} onChange={value => setJson(current => ({ ...current, metadata: value }))} />{error && <div className="dlc-field-warning">{error}</div>}<div className="dlc-form-actions">{selected && <button className="danger" disabled={busy} onClick={remove}>删除 Actor</button>}<button className="primary" disabled={busy || !String(form.actor_id || '').trim()} onClick={save}>保存 Actor</button></div></div></div>
}
