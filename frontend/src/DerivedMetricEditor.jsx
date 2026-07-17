import { useId, useMemo, useState } from 'react'
import {
  AGGREGATE_METHODS,
  AGGREGATE_SOURCES,
  analyzeDerivedMetricDependencies,
  cloneMetricValue,
  createDefaultFormula,
  DERIVED_METRIC_AUDIENCES,
  DIRECT_SOURCES,
  FORMULA_OPERATIONS,
  inferFormulaOperation,
  normalizeFormulaOperation,
  parseJsonObject,
  validateDerivedMetricFormula,
} from './DerivedMetricEditorTools.js'

const EMPTY_METRIC = {
  metric_id: '', display_name: '', description: '', unit: '', precision: 2,
  minimum: null, maximum: null, enabled: true,
  audiences: ['player', 'ui', 'agent', 'rule', 'objective'],
  missing_policy: 'fail', formula: { op: 'constant', value: 0 }, metadata: {},
}
const NARY_OPERATIONS = new Set(['add', 'multiply', 'min', 'max', 'avg', 'sum', 'coalesce'])
const BINARY_OPERATIONS = new Set(['subtract', 'divide'])

function unique(values) {
  return [...new Set(values.filter(value => value !== undefined && value !== null && String(value).trim()).map(value => String(value).trim()))]
}

function CatalogInput({ label, value, options, onChange, placeholder = '', help = '' }) {
  const listId = `metric-catalog-${useId().replaceAll(':', '-')}`
  return <label>{label}<input list={listId} value={value || ''} placeholder={placeholder} onChange={event => onChange(event.target.value)} />
    <datalist id={listId}>{unique(options || []).map(option => <option key={option} value={option} />)}</datalist>
    {help && <small className="dlc-field-help">{help}</small>}
  </label>
}

function numberValue(raw, fallback = 0) {
  if (raw === '') return fallback
  const value = Number(raw)
  return Number.isFinite(value) ? value : fallback
}

function sourceNode(op, source) {
  if (op === 'source') return source === 'sim_day' ? { op, source } : { op, source, entity_id: '', name: '' }
  return { op, source, aggregate: source === 'entity_count' || source === 'ongoing_event' ? 'count' : 'sum' }
}

function FormulaFilters({ node, onChange, catalogs }) {
  const [text, setText] = useState(JSON.stringify(node.filters || {}, null, 2))
  const [error, setError] = useState('')
  const setFilter = (key, value) => {
    const filters = { ...(node.filters || {}) }
    if (value === '' || value === undefined) delete filters[key]
    else filters[key] = value
    setText(JSON.stringify(filters, null, 2))
    setError('')
    onChange({ ...node, ...(Object.keys(filters).length ? { filters } : {}) })
    if (!Object.keys(filters).length) {
      const next = { ...node }
      delete next.filters
      onChange(next)
    }
  }
  const applyAdvanced = () => {
    try {
      const filters = parseJsonObject(text, '高级 Filters')
      setError('')
      const next = { ...node }
      if (Object.keys(filters).length) next.filters = filters
      else delete next.filters
      onChange(next)
    } catch (reason) { setError(reason.message) }
  }
  return <details className="metric-advanced"><summary>筛选条件（可选）</summary>
    <div className="dlc-row three">
      <CatalogInput label="实体类型 entity_type" value={node.filters?.entity_type} options={catalogs.entityTypes} onChange={value => setFilter('entity_type', value)} help="仅聚合匹配 type 的实体。" />
      <CatalogInput label="实体状态 status" value={node.filters?.status} options={catalogs.entityStatuses} onChange={value => setFilter('status', value)} help="匹配实体 current_status。" />
      <CatalogInput label="实体标签 tag" value={node.filters?.tag} options={catalogs.entityTags} onChange={value => setFilter('tag', value)} help="匹配实体 metadata.tags。" />
    </div>
    {node.source === 'actor_attribute' && <label>Actor 类型 actor_type<input value={node.filters?.actor_type || ''} onChange={event => setFilter('actor_type', event.target.value)} /></label>}
    {node.source === 'relation' && <div className="dlc-row">
      <label className="dlc-check"><input type="checkbox" checked={node.filters?.active === true} onChange={event => setFilter('active', event.target.checked ? true : '')} /> 仅统计激活关系</label>
      <label>数值字段 value_field<input value={node.filters?.value_field || ''} placeholder="strength 或 dimensions.personal" onChange={event => setFilter('value_field', event.target.value)} /></label>
    </div>}
    <label>高级 Filters JSON<textarea className="dlc-json-field" rows="4" spellCheck="false" value={text} onChange={event => setText(event.target.value)} /></label>
    <div className="metric-inline-actions"><button type="button" onClick={applyAdvanced}>应用高级筛选</button><small className="dlc-field-help">用于 active、value_field 等少见标量筛选；最多 16 个键。核心来源与公式无需手写 JSON。</small></div>
    {error && <div className="metric-error">{error}</div>}
  </details>
}

function SourceFields({ node, onChange, catalogs }) {
  const aggregate = node.op === 'aggregate'
  const choices = aggregate ? AGGREGATE_SOURCES : DIRECT_SOURCES
  const set = (key, value) => {
    const next = { ...node }
    if (value === '') delete next[key]
    else next[key] = value
    onChange(next)
  }
  const source = node.source
  const entityOptions = catalogs.entities
  const actorOptions = catalogs.actors
  const valueNames = source === 'parameter' ? catalogs.parameters : catalogs.metrics
  return <div className="metric-source-fields">
    <div className="dlc-row three">
      <label>数据来源<select value={source || choices[0][0]} onChange={event => onChange(sourceNode(node.op, event.target.value))}>{choices.map(([value, label]) => <option key={value} value={value}>{label} · {value}</option>)}</select><small className="dlc-field-help">读取权威世界状态；不会创建第二份可写状态。</small></label>
      {aggregate && <label>聚合方法<select value={node.aggregate || 'sum'} onChange={event => set('aggregate', event.target.value)}>{AGGREGATE_METHODS.map(([value, label]) => <option key={value} value={value}>{label} · {value}</option>)}</select><small className="dlc-field-help">count 统计记录数，其余方法处理可用数值。</small></label>}
      {!aggregate && source === 'sim_day' && <div className="metric-note">按模拟秒数确定性换算为游戏天数，不读取实体。</div>}
    </div>
    {['anchored_inventory', 'metric', 'parameter'].includes(source) && <div className="dlc-row">
      <CatalogInput label={aggregate ? '限定实体（可选）' : '实体 ID'} value={node.entity_id} options={entityOptions} onChange={value => set('entity_id', value)} help={aggregate ? '留空表示遍历所有符合筛选器的实体。' : '直接读取时必填。'} />
      <CatalogInput label={source === 'anchored_inventory' ? '资源 / 库存项名称' : `${source === 'metric' ? '指标' : '参数'}名称`} value={node.name} options={source === 'anchored_inventory' ? catalogs.resources : valueNames} onChange={value => set('name', value)} help={source === 'anchored_inventory' ? '结果使用统一锚定后的 base_value，而不是原始数量。' : '必须与 entity_values 中的 name 一致。'} />
    </div>}
    {source === 'actor_attribute' && <div className="dlc-row">
      <CatalogInput label={aggregate ? '限定 Actor（可选）' : 'Actor ID'} value={node.entity_id} options={actorOptions} onChange={value => set('entity_id', value)} help={aggregate ? '留空聚合全部 Actor。' : '直接读取时必填。'} />
      <CatalogInput label="属性名称" value={node.name} options={catalogs.actorAttributes} onChange={value => set('name', value)} help="读取 Actor runtime.attributes 中的有限数值。" />
    </div>}
    {source === 'relation' && <div className="dlc-row three">
      <CatalogInput label="关系类型（可选）" value={node.name} options={catalogs.relationshipTypes} onChange={value => set('name', value)} help="留空匹配所有关系类型。" />
      <CatalogInput label="起点实体（可选）" value={node.entity_id} options={entityOptions} onChange={value => set('entity_id', value)} />
      <CatalogInput label="终点实体（可选）" value={node.target_entity_id} options={entityOptions} onChange={value => set('target_entity_id', value)} />
      <label>关系数值字段<input value={node.field || ''} placeholder="strength" onChange={event => set('field', event.target.value)} /><small className="dlc-field-help">可填写 strength 或 dimensions.personal 等字段路径。</small></label>
    </div>}
    {source === 'ongoing_event' && <div className="dlc-row">
      <CatalogInput label="实体 ID（可选）" value={node.entity_id} options={entityOptions} onChange={value => set('entity_id', value)} help="留空读取全部持续事件。" />
      <CatalogInput label="事件类型（可选）" value={node.name} options={catalogs.eventTypes} onChange={value => set('name', value)} help="留空统计所有事件。" />
    </div>}
    {aggregate && <FormulaFilters node={node} onChange={onChange} catalogs={catalogs} />}
  </div>
}

function FormulaNode({ node, onChange, onRemove, catalogs, metricId, depth = 0, label = '公式节点' }) {
  const actual = node && typeof node === 'object' ? node : createDefaultFormula()
  const op = inferFormulaOperation(actual) || 'constant'
  const canAdd = depth < 11
  const updateValueChild = (key, value) => onChange({ ...actual, [key]: value })
  return <article className={`metric-formula-node depth-${Math.min(depth, 4)}`}>
    <div className="metric-node-toolbar"><strong>{label}</strong><label>运算<select value={op} onChange={event => onChange(normalizeFormulaOperation(actual, event.target.value))}>{FORMULA_OPERATIONS.map(([value, title]) => <option key={value} value={value}>{title} · {value}</option>)}</select></label>{onRemove && <button type="button" className="danger" onClick={onRemove}>删除节点</button>}</div>
    {op === 'constant' && <label>常量值<input type="number" step="any" value={actual.value ?? actual.constant ?? 0} onChange={event => onChange({ op, value: numberValue(event.target.value) })} /><small className="dlc-field-help">只允许有限数值。</small></label>}
    {op === 'derived_metric' && <CatalogInput label="引用指标 ID" value={actual.metric_id || actual.derived_metric} options={catalogs.derivedMetrics.filter(id => id !== metricId)} onChange={value => onChange({ op, metric_id: value })} help="只可引用同一 Generation 内的其他指标；编辑器会检测缺失和循环依赖。" />}
    {['source', 'aggregate'].includes(op) && <SourceFields node={{ ...actual, op }} onChange={onChange} catalogs={catalogs} />}
    {NARY_OPERATIONS.has(op) && <div className="metric-node-children">{(actual.values || []).map((child, index) => <FormulaNode key={index} node={child} label={`子项 ${index + 1}`} depth={depth + 1} catalogs={catalogs} metricId={metricId} onChange={value => onChange({ ...actual, op, values: actual.values.map((item, position) => position === index ? value : item) })} onRemove={() => onChange({ ...actual, op, values: actual.values.filter((_, position) => position !== index) })} />)}<button type="button" disabled={!canAdd} onClick={() => onChange({ ...actual, op, values: [...(actual.values || []), createDefaultFormula()] })}>＋ 添加子项</button></div>}
    {BINARY_OPERATIONS.has(op) && <div className="metric-node-children binary"><FormulaNode node={actual.left} label={op === 'divide' ? '被除数' : '被减数'} depth={depth + 1} catalogs={catalogs} metricId={metricId} onChange={value => updateValueChild('left', value)} /><FormulaNode node={actual.right} label={op === 'divide' ? '除数' : '减数'} depth={depth + 1} catalogs={catalogs} metricId={metricId} onChange={value => updateValueChild('right', value)} /></div>}
    {op === 'weighted_sum' && <div className="metric-node-children">{(actual.terms || []).map((term, index) => <div className="metric-weighted-term" key={index}><label>权重<input type="number" step="any" value={term.weight ?? 1} onChange={event => onChange({ ...actual, terms: actual.terms.map((item, position) => position === index ? { ...item, weight: numberValue(event.target.value, 1) } : item) })} /></label><FormulaNode node={term.value} label={`加权项 ${index + 1}`} depth={depth + 1} catalogs={catalogs} metricId={metricId} onChange={value => onChange({ ...actual, terms: actual.terms.map((item, position) => position === index ? { ...item, value } : item) })} onRemove={() => onChange({ ...actual, terms: actual.terms.filter((_, position) => position !== index) })} /></div>)}<button type="button" disabled={!canAdd} onClick={() => onChange({ ...actual, terms: [...(actual.terms || []), { weight: 1, value: createDefaultFormula() }] })}>＋ 添加加权项</button></div>}
    {op === 'clamp' && <><div className="dlc-row"><label>下限 min<input type="number" step="any" value={actual.min ?? 0} onChange={event => onChange({ ...actual, min: numberValue(event.target.value) })} /></label><label>上限 max<input type="number" step="any" value={actual.max ?? 100} onChange={event => onChange({ ...actual, max: numberValue(event.target.value, 100) })} /></label></div><FormulaNode node={actual.value} label="待限制值" depth={depth + 1} catalogs={catalogs} metricId={metricId} onChange={value => updateValueChild('value', value)} /></>}
    {op === 'round' && <><label>节点精度 0~8<input type="number" min="0" max="8" value={actual.precision ?? 0} onChange={event => onChange({ ...actual, precision: Math.max(0, Math.min(8, Math.trunc(numberValue(event.target.value)))) })} /></label><FormulaNode node={actual.value} label="待舍入值" depth={depth + 1} catalogs={catalogs} metricId={metricId} onChange={value => updateValueChild('value', value)} /></>}
  </article>
}

function buildCatalogs(content) {
  const entities = content.entities || []
  const actors = content.actors || []
  const values = content.entity_values || []
  return {
    entities: unique(entities.map(item => item.id)),
    entityTypes: unique(entities.map(item => item.type)),
    entityStatuses: unique(entities.map(item => item.current_status || item.status)),
    entityTags: unique(entities.flatMap(item => Array.isArray(item.metadata?.tags) ? item.metadata.tags : [])),
    actors: unique(actors.map(item => item.actor_id || item.id)),
    actorAttributes: unique(actors.flatMap(item => Object.keys(item.attributes || {}))),
    resources: unique((content.resources || []).map(item => item.name)),
    metrics: unique(values.filter(item => (item.kind || 'metric') === 'metric').map(item => item.name)),
    parameters: unique(values.filter(item => item.kind === 'parameter').map(item => item.name)),
    relationshipTypes: unique((content.relationship_types || []).map(item => item.relationship_type_id || item.id)),
    eventTypes: unique([...(content.ongoing_events || []), ...(content.random_events || [])].map(item => item.event_type || item.template_id)),
    derivedMetrics: unique((content.derived_metrics || []).map(item => item.metric_id)),
  }
}

export default function DerivedMetricEditor({ content, selected, onSelect, onSaveContent, busy }) {
  const metrics = useMemo(() => content.derived_metrics || [], [content.derived_metrics])
  const found = metrics.find(item => item.metric_id === selected)
  const [draft, setDraft] = useState(cloneMetricValue(found || EMPTY_METRIC))
  const [originalId, setOriginalId] = useState(found?.metric_id || '')
  const [metadataText, setMetadataText] = useState(JSON.stringify(found?.metadata || {}, null, 2))
  const [message, setMessage] = useState('')
  const catalogs = useMemo(() => buildCatalogs(content), [content])
  const dependency = useMemo(() => analyzeDerivedMetricDependencies(metrics, draft), [metrics, draft])
  const selectMetric = id => { onSelect(id); setMessage('') }
  const startNew = () => { onSelect(''); setDraft(cloneMetricValue(EMPTY_METRIC)); setOriginalId(''); setMetadataText('{}'); setMessage('') }
  const setOptionalNumber = (key, raw) => setDraft(current => ({ ...current, [key]: raw === '' ? null : numberValue(raw) }))
  const toggleAudience = audience => setDraft(current => ({ ...current, audiences: current.audiences.includes(audience) ? current.audiences.filter(item => item !== audience) : [...current.audiences, audience] }))
  const save = () => {
    try {
      const metricId = String(draft.metric_id || '').trim()
      if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(metricId)) throw new Error('指标 ID 必须为 1~64 位，只能包含英文、数字、点、下划线或连字符。')
      if (!String(draft.display_name || '').trim()) throw new Error('请填写中文显示名称。')
      if (!draft.audiences?.length) throw new Error('至少选择一个可见受众。')
      if (draft.minimum !== null && draft.maximum !== null && draft.minimum > draft.maximum) throw new Error('结果下限不能大于结果上限。')
      const formulaErrors = validateDerivedMetricFormula(draft.formula)
      if (formulaErrors.length) throw new Error(formulaErrors[0])
      if (dependency.selfReference) throw new Error('公式不能引用自身。')
      if (dependency.missing.length) throw new Error(`存在当前草稿中找不到的指标依赖：${dependency.missing.join('、')}`)
      if (dependency.cycles.length) throw new Error(`指标依赖存在循环：${dependency.cycles[0].join(' → ')}`)
      if (metrics.some(item => item.metric_id === metricId && item.metric_id !== originalId)) throw new Error(`指标 ID ${metricId} 已存在。`)
      const payload = { ...cloneMetricValue(draft), metric_id: metricId, display_name: draft.display_name.trim(), metadata: parseJsonObject(metadataText, 'Metadata') }
      const index = metrics.findIndex(item => item.metric_id === originalId)
      const next = [...metrics]
      if (index >= 0) next[index] = payload
      else next.push(payload)
      onSaveContent({ ...content, derived_metrics: next }, `${metricId} 派生指标已暂存`)
      setOriginalId(metricId)
      onSelect(metricId)
      setMessage('已加入待应用差异；后端 Registry 保存时会执行权威契约校验。')
    } catch (reason) { setMessage(reason.message) }
  }
  const remove = id => {
    if (!window.confirm(`确定删除派生指标 ${id}？引用它的目标、规则和其他指标将无法通过校验。`)) return
    onSaveContent({ ...content, derived_metrics: metrics.filter(item => item.metric_id !== id) }, `${id} 派生指标已删除`)
    startNew()
  }
  return <div className="dlc-structured-grid metric-editor-grid">
    <aside className="dlc-item-list"><button type="button" className={!originalId ? 'active' : ''} onClick={startNew}><strong>＋ 新建派生指标</strong><small>可视化公式树</small></button>{metrics.map(item => <button type="button" key={item.metric_id} className={originalId === item.metric_id ? 'active' : ''} onClick={() => selectMetric(item.metric_id)}><strong>{item.display_name || item.metric_id}</strong><small>{item.metric_id} · {item.enabled === false ? '已停用' : '启用'}</small></button>)}</aside>
    <section className="dlc-form metric-editor-form">
      <div className="dlc-form-title"><div><h3>{originalId ? '编辑派生指标' : '新建派生指标'}</h3><code>{draft.metric_id || 'new_metric'}</code></div>{originalId && <button type="button" className="danger" onClick={() => remove(originalId)}>删除指标</button>}</div>
      <div className="dlc-editor-callout"><strong>确定性派生值，不是可写状态</strong><p>公式只读取当前 Generation 的权威世界状态。目标、因果规则、Agent 与 UI 会共享同一目录，但分别受 audiences 隔离。锚定库存来源读取统一 base_value，避免不同代币单位造成价值漂移。</p></div>
      <div className="dlc-row three"><label>指标 ID<input value={draft.metric_id} onChange={event => setDraft(current => ({ ...current, metric_id: event.target.value }))} placeholder="frontier_readiness" /><small className="dlc-field-help">发布后建议保持稳定；目标和规则通过此 ID 引用。</small></label><label>中文显示名称<input value={draft.display_name} onChange={event => setDraft(current => ({ ...current, display_name: event.target.value }))} placeholder="边境战备度" /></label><label>单位<input value={draft.unit} onChange={event => setDraft(current => ({ ...current, unit: event.target.value }))} placeholder="% / 银券 / 天" /></label></div>
      <label>中文说明<textarea rows="3" value={draft.description} onChange={event => setDraft(current => ({ ...current, description: event.target.value }))} placeholder="说明这个值如何理解、由哪些权威数据推导，以及玩家应如何使用。" /></label>
      <div className="dlc-row three"><label>显示精度 0~8<input type="number" min="0" max="8" value={draft.precision} onChange={event => setDraft(current => ({ ...current, precision: Math.max(0, Math.min(8, Math.trunc(numberValue(event.target.value, 2)))) }))} /><small className="dlc-field-help">最终结果的小数位数；公式内部仍按有限浮点数计算。</small></label><label>结果下限（可选）<input type="number" step="any" value={draft.minimum ?? ''} onChange={event => setOptionalNumber('minimum', event.target.value)} /></label><label>结果上限（可选）<input type="number" step="any" value={draft.maximum ?? ''} onChange={event => setOptionalNumber('maximum', event.target.value)} /></label></div>
      <div className="dlc-row"><label>缺失数据策略<select value={draft.missing_policy} onChange={event => setDraft(current => ({ ...current, missing_policy: event.target.value }))}><option value="fail">fail · 标记求值失败</option><option value="zero">zero · 缺失按 0 处理</option></select><small className="dlc-field-help">推荐 fail，避免把配置错误悄悄伪装成 0；只有业务语义明确时才选 zero。</small></label><label className="dlc-check"><input type="checkbox" checked={draft.enabled !== false} onChange={event => setDraft(current => ({ ...current, enabled: event.target.checked }))} /> 启用该指标</label></div>
      <fieldset className="metric-audiences"><legend>可见受众 audiences</legend><p className="dlc-field-help">未选择的通道无法读取该指标。player 控制公开 API；ui 控制界面；agent 进入只读 Prompt；rule/objective 分别允许规则和目标引用。</p><div>{DERIVED_METRIC_AUDIENCES.map(([audience, label]) => <label className="dlc-check" key={audience}><input type="checkbox" checked={draft.audiences.includes(audience)} onChange={() => toggleAudience(audience)} /> {label} <code>{audience}</code></label>)}</div></fieldset>
      <section className="metric-formula-section"><div><h4>公式树</h4><p className="dlc-field-help">通过节点构建器组合来源、聚合和算术。最大深度 12、最多 256 节点；运行时拒绝除零、NaN、Infinity 与循环依赖。</p></div><FormulaNode node={draft.formula} onChange={formula => setDraft(current => ({ ...current, formula }))} catalogs={catalogs} metricId={draft.metric_id} /></section>
      <section className="metric-dependency-panel"><h4>依赖检查</h4><p><strong>直接依赖：</strong>{dependency.dependencies.length ? dependency.dependencies.join('、') : '无'}</p>{dependency.selfReference && <div className="metric-error">检测到自引用。</div>}{dependency.missing.length > 0 && <div className="metric-error">当前草稿缺失：{dependency.missing.join('、')}</div>}{dependency.cycles.map((cycle, index) => <div className="metric-error" key={`${cycle.join('-')}-${index}`}>循环 #{index + 1}：{cycle.join(' → ')}</div>)}{!dependency.selfReference && !dependency.missing.length && !dependency.cycles.length && <div className="metric-ok">客户端依赖图检查通过；保存时仍以 Registry 校验为准。</div>}</section>
      <details className="metric-advanced"><summary>高级 Metadata（可选）</summary><label>Metadata JSON<textarea className="dlc-json-field" rows="5" spellCheck="false" value={metadataText} onChange={event => setMetadataText(event.target.value)} /><small className="dlc-field-help">仅保存作者备注或扩展标量，不会进入玩家 API 或 Agent 派生指标上下文。</small></label></details>
      {message && <div className={message.startsWith('已加入') ? 'metric-ok' : 'metric-error'}>{message}</div>}
      <div className="dlc-form-actions"><button type="button" className="primary" disabled={busy} onClick={save}>保存到内容草稿</button></div>
    </section>
  </div>
}
