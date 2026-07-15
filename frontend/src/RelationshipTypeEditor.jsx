import { useMemo, useState } from 'react'

import { effectiveRelationshipTypes } from './RelationshipTypeTools.js'

const EMPTY_TYPE = {
  relationship_type_id: '',
  display_name: '',
  description: '',
  direction: 'directed',
  minimum_strength: -100,
  maximum_strength: 100,
  default_strength: 0,
  default_visibility: 'public',
  allow_expiry: false,
  dimensions: [],
  style: { label: '', color: '#7d8da6', dashed: false, marker: 'arrow' },
  metadata: {},
}

function clone(value) {
  return JSON.parse(JSON.stringify(value))
}

function jsonText(value) {
  return JSON.stringify(value && typeof value === 'object' && !Array.isArray(value) ? value : {}, null, 2)
}

function parseObject(text, label) {
  let value
  try { value = JSON.parse(text || '{}') } catch (error) { throw new Error(`${label} JSON 无效：${error.message}`) }
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${label}必须是 JSON 对象。`)
  return value
}

function normalizeForm(value) {
  const source = clone(value || EMPTY_TYPE)
  source.relationship_type_id = String(source.relationship_type_id || source.id || '')
  source.display_name = String(source.display_name || '')
  source.description = String(source.description || '')
  source.direction = source.direction === 'undirected' ? 'undirected' : 'directed'
  source.minimum_strength = source.minimum_strength ?? -100
  source.maximum_strength = source.maximum_strength ?? 100
  source.default_strength = source.default_strength ?? 0
  source.default_visibility = source.default_visibility || 'public'
  source.allow_expiry = Boolean(source.allow_expiry)
  source.dimensions = Array.isArray(source.dimensions) ? source.dimensions.map(item => ({
    dimension_id: String(item.dimension_id || item.id || ''),
    display_name: String(item.display_name || ''),
    description: String(item.description || ''),
    minimum: item.minimum ?? -100,
    maximum: item.maximum ?? 100,
    default: item.default ?? 0,
  })) : []
  source._style_text = jsonText(source.style)
  source._metadata_text = jsonText(source.metadata)
  return source
}

function serialize(form, existingIds) {
  const id = String(form.relationship_type_id || '').trim()
  if (!id || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(id)) throw new Error('类型 ID 必须为 1-128 位，仅允许字母、数字、点、下划线、冒号和连字符。')
  if (existingIds.has(id)) throw new Error(`类型 ID “${id}” 已存在。`)
  const displayName = String(form.display_name || '').trim()
  if (!displayName) throw new Error('显示名称不能为空。')
  const minimum = Number(form.minimum_strength)
  const maximum = Number(form.maximum_strength)
  const defaultStrength = Number(form.default_strength)
  if (![minimum, maximum, defaultStrength].every(Number.isFinite)) throw new Error('强度范围必须是有限数字。')
  if (maximum < minimum) throw new Error('最大强度不能小于最小强度。')
  if (defaultStrength < minimum || defaultStrength > maximum) throw new Error('默认强度必须位于最小值和最大值之间。')
  const dimensionIds = new Set()
  const dimensions = form.dimensions.map((item, index) => {
    const dimensionId = String(item.dimension_id || '').trim()
    if (!dimensionId) throw new Error(`第 ${index + 1} 个维度缺少维度 ID。`)
    if (dimensionIds.has(dimensionId)) throw new Error(`维度 ID “${dimensionId}” 重复。`)
    dimensionIds.add(dimensionId)
    const dmin = Number(item.minimum)
    const dmax = Number(item.maximum)
    const ddef = Number(item.default)
    if (![dmin, dmax, ddef].every(Number.isFinite) || dmax < dmin || ddef < dmin || ddef > dmax) {
      throw new Error(`维度 “${dimensionId}” 的最小值、最大值或默认值无效。`)
    }
    return {
      dimension_id: dimensionId,
      display_name: String(item.display_name || dimensionId).trim(),
      description: String(item.description || '').trim(),
      minimum: dmin,
      maximum: dmax,
      default: ddef,
    }
  })
  return {
    relationship_type_id: id,
    display_name: displayName,
    description: String(form.description || '').trim(),
    direction: form.direction === 'undirected' ? 'undirected' : 'directed',
    minimum_strength: minimum,
    maximum_strength: maximum,
    default_strength: defaultStrength,
    default_visibility: form.default_visibility,
    allow_expiry: Boolean(form.allow_expiry),
    dimensions,
    style: parseObject(form._style_text, '样式'),
    metadata: parseObject(form._metadata_text, 'Metadata'),
  }
}

export default function RelationshipTypeEditor({ content, selected, onSelect, onSaveContent, busy = false }) {
  const items = Array.isArray(content.relationship_types) ? content.relationship_types : []
  const selectedIndex = selected === '' ? -1 : Number(selected)
  const [form, setForm] = useState(() => normalizeForm(selectedIndex >= 0 ? items[selectedIndex] : null))
  const [error, setError] = useState('')
  const effectiveTypes = useMemo(() => effectiveRelationshipTypes(content), [content])
  const set = (key, value) => setForm(current => ({ ...current, [key]: value }))
  const setDimension = (index, key, value) => setForm(current => ({
    ...current,
    dimensions: current.dimensions.map((item, itemIndex) => itemIndex === index ? { ...item, [key]: value } : item),
  }))
  const addDimension = () => setForm(current => ({
    ...current,
    dimensions: [...current.dimensions, { dimension_id: '', display_name: '', description: '', minimum: -100, maximum: 100, default: 0 }],
  }))
  const removeDimension = index => setForm(current => ({ ...current, dimensions: current.dimensions.filter((_, itemIndex) => itemIndex !== index) }))

  const save = () => {
    try {
      const existingIds = new Set(items.filter((_, index) => index !== selectedIndex).map(item => String(item.relationship_type_id || item.id || '')))
      const value = serialize(form, existingIds)
      const next = items.map(clone)
      if (selectedIndex >= 0) next[selectedIndex] = value
      else next.push(value)
      setError('')
      onSaveContent({ ...content, relationship_types: next }, `关系类型 ${value.relationship_type_id} 已保存`)
    } catch (caught) { setError(caught.message) }
  }
  const remove = () => {
    if (selectedIndex < 0) return
    const current = items[selectedIndex]
    const id = current.relationship_type_id || current.id
    const used = (content.relations || []).filter(item => item.rel_type === id).length
    if (used) { setError(`该类型仍被 ${used} 条关系边引用，请先修改或删除这些关系边。`); return }
    if (!window.confirm(`删除关系类型 ${id}？`)) return
    onSaveContent({ ...content, relationship_types: items.filter((_, index) => index !== selectedIndex) }, `关系类型 ${id} 已删除`)
  }

  return <div className="dlc-content-grid dlc-structured-grid">
    <div className="dlc-item-list">
      <button className="dlc-new" onClick={() => onSelect('')}>＋ 新建关系类型</button>
      <div className="dlc-editor-callout compact">内建类型始终可用：上下级、附属。创建同 ID 类型可覆盖其显示和约束。</div>
      {items.map((item, index) => <button key={`${item.relationship_type_id}-${index}`} className={selectedIndex === index ? 'active' : ''} onClick={() => onSelect(String(index))}>
        <strong>{item.display_name || item.relationship_type_id}</strong>
        <small>{item.relationship_type_id} · {item.direction === 'undirected' ? '无向' : '有向'}</small>
      </button>)}
    </div>
    <div className="dlc-form dlc-structured-form relationship-type-form">
      <div className="dlc-form-title"><h3>{selectedIndex >= 0 ? '编辑' : '新建'}关系类型</h3><code>{effectiveTypes.length} 个运行时类型</code></div>
      <div className="dlc-editor-callout">关系类型定义属于 DLC 契约。类型 ID 保存后不可修改；热重载会原子校验已有关系边，失败时不会发布新契约。</div>
      <div className="dlc-row">
        <label>稳定类型 ID<input value={form.relationship_type_id} disabled={selectedIndex >= 0} onChange={event => set('relationship_type_id', event.target.value)} placeholder="trust" /><small>1-128 位；建议使用小写英文、数字、点、下划线或连字符。</small></label>
        <label>显示名称<input value={form.display_name} onChange={event => set('display_name', event.target.value)} placeholder="信任" /></label>
      </div>
      <label>中文说明<textarea rows="3" value={form.description} onChange={event => set('description', event.target.value)} placeholder="说明这类关系在世界模拟中的含义和使用边界。" /></label>
      <div className="dlc-row">
        <label>方向<select value={form.direction} onChange={event => set('direction', event.target.value)}><option value="directed">有向（父实体 → 子实体）</option><option value="undirected">无向（双方对等）</option></select></label>
        <label>默认公开性<select value={form.default_visibility} onChange={event => set('default_visibility', event.target.value)}><option value="public">公开</option><option value="limited">有限公开</option><option value="private">私有</option><option value="secret">秘密</option></select></label>
      </div>
      <div className="dlc-row three">
        <label>最小强度<input type="number" step="any" value={form.minimum_strength} onChange={event => set('minimum_strength', event.target.value)} /></label>
        <label>最大强度<input type="number" step="any" value={form.maximum_strength} onChange={event => set('maximum_strength', event.target.value)} /></label>
        <label>默认强度<input type="number" step="any" value={form.default_strength} onChange={event => set('default_strength', event.target.value)} /></label>
      </div>
      <label className="dlc-checkbox"><input type="checkbox" checked={form.allow_expiry} onChange={event => set('allow_expiry', event.target.checked)} />允许关系边设置到期时间</label>

      <fieldset className="relationship-dimensions"><legend>多维关系值</legend>
        <p>可选。用于把一条关系拆分为“个人信任、制度信任、依赖度”等独立维度。</p>
        {form.dimensions.map((dimension, index) => <div className="relationship-dimension-card" key={`dimension-${index}`}>
          <div className="dlc-row"><label>维度 ID<input value={dimension.dimension_id} onChange={event => setDimension(index, 'dimension_id', event.target.value)} placeholder="personal" /></label><label>显示名称<input value={dimension.display_name} onChange={event => setDimension(index, 'display_name', event.target.value)} placeholder="个人信任" /></label></div>
          <div className="dlc-row three"><label>最小值<input type="number" step="any" value={dimension.minimum} onChange={event => setDimension(index, 'minimum', event.target.value)} /></label><label>最大值<input type="number" step="any" value={dimension.maximum} onChange={event => setDimension(index, 'maximum', event.target.value)} /></label><label>默认值<input type="number" step="any" value={dimension.default} onChange={event => setDimension(index, 'default', event.target.value)} /></label></div>
          <label>维度说明<input value={dimension.description} onChange={event => setDimension(index, 'description', event.target.value)} /></label>
          <button type="button" className="danger compact-button" onClick={() => removeDimension(index)}>删除维度</button>
        </div>)}
        <button type="button" onClick={addDimension}>＋ 添加关系维度</button>
      </fieldset>
      <div className="dlc-row">
        <label>样式 JSON<textarea className="dlc-json-field" rows="8" spellCheck="false" value={form._style_text} onChange={event => set('_style_text', event.target.value)} /><small>建议字段：label、color、dashed、marker。</small></label>
        <label>Metadata 高级 JSON<textarea className="dlc-json-field" rows="8" spellCheck="false" value={form._metadata_text} onChange={event => set('_metadata_text', event.target.value)} /><small>只保存安全 JSON 数据，不应放置凭据。</small></label>
      </div>
      {error && <div className="dlc-editor-error">{error}</div>}
      <div className="dlc-form-actions">{selectedIndex >= 0 && <button className="danger" disabled={busy} onClick={remove}>删除</button>}<button className="primary" disabled={busy || !form.relationship_type_id.trim() || !form.display_name.trim()} onClick={save}>保存关系类型</button></div>
    </div>
  </div>
}
