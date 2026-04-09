import { useState, useEffect, useCallback } from 'react'

const API_BASE = '/api'

const TYPE_ICONS = {
  location: '🏙',
  person: '👤',
  institution: '🏛',
  organization: '⚙',
  object: '📦',
}

const TYPE_LABELS = {
  location: '地点',
  person: '人物',
  institution: '机构',
  organization: '组织',
  object: '物品',
}

const ALL_TYPES = ['location', 'person', 'institution', 'organization', 'object']

const CATEGORY_SUGGESTIONS = ['粮食', '货币', '兵力', '物资', '人口', '税收']

function typeIcon(type) {
  return TYPE_ICONS[type] || '❓'
}

export default function WorldStateView() {
  const [entities, setEntities] = useState([])         // 全量实体列表（轻量，只含 id/type/name）
  const [selected, setSelected] = useState(null)       // 当前选中的完整实体 {entity, inventory}
  const [entityForm, setEntityForm] = useState(null)   // 编辑表单（镜像 selected.entity）
  const [filterType, setFilterType] = useState('all')
  const [searchKw, setSearchKw] = useState('')
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')                   // 操作反馈消息

  // 库存行内编辑
  const [editingInv, setEditingInv] = useState(null)   // { idx, field } 正在编辑的格子
  const [invEdits, setInvEdits] = useState({})         // idx → {quantity, description} 临时值

  // 新增库存表单
  const [showNewItem, setShowNewItem] = useState(false)
  const [newItem, setNewItem] = useState({ category: '', item_name: '', quantity: '', description: '' })

  // 地点坐标（仅 type=location 时使用）
  const [locCoords, setLocCoords] = useState(null)  // { x, y, region, name } | null
  const [coordsSaving, setCoordsSaving] = useState(false)

  // 新建实体面板
  const [showNewEntity, setShowNewEntity] = useState(false)
  const [newEntity, setNewEntity] = useState({ id: '', type: 'location', name: '', description: '', current_status: '' })

  // ── 加载实体列表 ──
  const loadEntities = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/world-state`)
      const data = await res.json()
      if (data.entities) {
        setEntities(data.entities)
      }
    } catch (e) {
      console.error('Failed to load world state:', e)
    }
  }, [])

  useEffect(() => { loadEntities() }, [loadEntities])

  // ── 加载单个实体详情 ──
  const loadEntity = useCallback(async (entityId) => {
    try {
      const res = await fetch(`${API_BASE}/world-state/${encodeURIComponent(entityId)}`)
      const data = await res.json()
      if (data.entity) {
        setSelected(data)
        setEntityForm({ ...data.entity })
        setEditingInv(null)
        setInvEdits({})
        setShowNewItem(false)
        // 如果是地点类型，加载坐标
        if (data.entity.type === 'location') {
          try {
            const locRes = await fetch(`${API_BASE}/editor/locations/${encodeURIComponent(entityId)}`)
            const locData = await locRes.json()
            if (!locData.error) {
              setLocCoords({ x: locData.x, y: locData.y, region: locData.region || '', name: locData.name || '' })
            } else {
              setLocCoords({ x: 0, y: 0, region: '', name: data.entity.name })
            }
          } catch {
            setLocCoords({ x: 0, y: 0, region: '', name: data.entity.name })
          }
        } else {
          setLocCoords(null)
        }
      }
    } catch (e) {
      console.error('Failed to load entity:', e)
    }
  }, [])

  // ── 筛选后的实体列表 ──
  const filteredEntities = entities.filter(e => {
    if (filterType !== 'all' && e.type !== filterType) return false
    if (searchKw) {
      const kw = searchKw.toLowerCase()
      return e.id.toLowerCase().includes(kw) || e.name.toLowerCase().includes(kw)
    }
    return true
  })

  // ── 保存实体 ──
  const handleSaveEntity = async () => {
    if (!entityForm) return
    setSaving(true)
    setMsg('')
    try {
      const res = await fetch(`${API_BASE}/world-state/${encodeURIComponent(entityForm.id)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          type: entityForm.type,
          name: entityForm.name,
          description: entityForm.description,
          current_status: entityForm.current_status,
          metadata: entityForm.metadata || {},
        }),
      })
      const data = await res.json()
      if (data.status === 'ok') {
        setMsg('✓ 已保存')
        await loadEntities()
        await loadEntity(entityForm.id)
      } else {
        setMsg('✗ 保存失败: ' + JSON.stringify(data))
      }
    } catch (e) {
      setMsg('✗ 网络错误')
    } finally {
      setSaving(false)
      setTimeout(() => setMsg(''), 2000)
    }
  }

  // ── 删除实体 ──
  const handleDeleteEntity = async () => {
    if (!selected) return
    if (!confirm(`确定删除实体「${selected.entity.name}」及其全部库存？`)) return
    try {
      await fetch(`${API_BASE}/world-state/${encodeURIComponent(selected.entity.id)}`, {
        method: 'DELETE',
      })
      setSelected(null)
      setEntityForm(null)
      await loadEntities()
      setMsg('✓ 已删除')
      setTimeout(() => setMsg(''), 2000)
    } catch (e) {
      setMsg('✗ 删除失败')
    }
  }

  // ── 库存行内编辑保存 ──
  const handleSaveInvRow = async (item, idx) => {
    if (!selected) return
    const edited = invEdits[idx] || {}
    const qty = edited.quantity !== undefined ? parseFloat(edited.quantity) : item.quantity
    const desc = edited.description !== undefined ? edited.description : item.description
    if (isNaN(qty)) { setMsg('✗ 数量必须是数字'); return }
    try {
      await fetch(`${API_BASE}/world-state/${encodeURIComponent(selected.entity.id)}/inventory`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          category: item.category,
          item_name: item.item_name,
          quantity: qty,
          description: desc,
        }),
      })
      setEditingInv(null)
      setInvEdits(prev => { const n = { ...prev }; delete n[idx]; return n })
      await loadEntity(selected.entity.id)
    } catch (e) {
      setMsg('✗ 保存库存失败')
    }
  }

  // ── 删除库存项 ──
  const handleDeleteInvItem = async (item) => {
    if (!selected) return
    const catEnc = encodeURIComponent(item.category)
    const itemEnc = encodeURIComponent(item.item_name)
    try {
      await fetch(`${API_BASE}/world-state/${encodeURIComponent(selected.entity.id)}/inventory/${catEnc}/${itemEnc}`, {
        method: 'DELETE',
      })
      await loadEntity(selected.entity.id)
    } catch (e) {
      setMsg('✗ 删除失败')
    }
  }

  // ── 新增库存项 ──
  const handleAddNewItem = async () => {
    if (!selected) return
    if (!newItem.category.trim() || !newItem.item_name.trim()) {
      setMsg('✗ 类目和名称不能为空')
      return
    }
    const qty = parseFloat(newItem.quantity)
    if (isNaN(qty)) { setMsg('✗ 数量必须是数字'); return }
    try {
      await fetch(`${API_BASE}/world-state/${encodeURIComponent(selected.entity.id)}/inventory`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          category: newItem.category,
          item_name: newItem.item_name,
          quantity: qty,
          description: newItem.description,
        }),
      })
      setNewItem({ category: '', item_name: '', quantity: '', description: '' })
      setShowNewItem(false)
      await loadEntity(selected.entity.id)
    } catch (e) {
      setMsg('✗ 添加失败')
    }
  }

  // ── 保存地点坐标 ──
  const handleSaveCoords = async () => {
    if (!selected || !locCoords) return
    setCoordsSaving(true)
    try {
      const res = await fetch(`${API_BASE}/editor/locations/${encodeURIComponent(selected.entity.id)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: selected.entity.id,
          name: locCoords.name || selected.entity.name,
          region: locCoords.region || '',
          x: parseFloat(locCoords.x) || 0,
          y: parseFloat(locCoords.y) || 0,
        }),
      })
      const data = await res.json()
      if (data.status === 'ok') {
        // 回读确认持久化成功，避免"写了但未刷新"的假象
        try {
          const verifyRes = await fetch(`${API_BASE}/editor/locations/${encodeURIComponent(selected.entity.id)}`)
          const verifyData = await verifyRes.json()
          if (!verifyData.error) {
            setLocCoords({
              x: verifyData.x,
              y: verifyData.y,
              region: verifyData.region || '',
              name: verifyData.name || selected.entity.name,
            })
          }
        } catch { /* 忽略回读失败，写入本身已成功 */ }
        setMsg('✓ 坐标已保存')
      } else {
        setMsg('✗ 坐标保存失败')
      }
    } catch {
      setMsg('✗ 网络错误')
    } finally {
      setCoordsSaving(false)
      setTimeout(() => setMsg(''), 2000)
    }
  }

  // ── 新建实体 ──
  const handleCreateEntity = async () => {
    if (!newEntity.id.trim() || !newEntity.name.trim()) {
      setMsg('✗ ID 和名称不能为空')
      return
    }
    try {
      const res = await fetch(`${API_BASE}/world-state/${encodeURIComponent(newEntity.id.trim())}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          type: newEntity.type,
          name: newEntity.name,
          description: newEntity.description,
          current_status: newEntity.current_status,
          metadata: {},
        }),
      })
      const data = await res.json()
      if (data.status === 'ok') {
        setShowNewEntity(false)
        setNewEntity({ id: '', type: 'location', name: '', description: '', current_status: '' })
        await loadEntities()
        await loadEntity(newEntity.id.trim())
      } else {
        setMsg('✗ 创建失败: ' + JSON.stringify(data))
      }
    } catch (e) {
      setMsg('✗ 网络错误')
    }
  }

  // ── 渲染 ──
  return (
    <div className="world-state-view">
      {/* 顶部工具栏 */}
      <div className="ws-toolbar">
        <input
          className="ws-search"
          type="text"
          placeholder="搜索实体名称或 ID…"
          value={searchKw}
          onChange={e => setSearchKw(e.target.value)}
        />
        <div className="ws-type-tabs">
          <button
            className={`ws-type-tab ${filterType === 'all' ? 'active' : ''}`}
            onClick={() => setFilterType('all')}
          >全部 ({entities.length})</button>
          {ALL_TYPES.map(t => {
            const cnt = entities.filter(e => e.type === t).length
            if (!cnt) return null
            return (
              <button
                key={t}
                className={`ws-type-tab ${filterType === t ? 'active' : ''}`}
                onClick={() => setFilterType(t)}
              >
                {typeIcon(t)} {TYPE_LABELS[t]} ({cnt})
              </button>
            )
          })}
        </div>
        <button className="btn primary ws-new-btn" onClick={() => setShowNewEntity(!showNewEntity)}>
          + 新建实体
        </button>
        {msg && <span className="ws-msg">{msg}</span>}
      </div>

      {/* 新建实体面板 */}
      {showNewEntity && (
        <div className="ws-new-entity-panel">
          <h3>新建实体</h3>
          <div className="ws-new-entity-fields">
            <label>ID<input value={newEntity.id} onChange={e => setNewEntity(p => ({ ...p, id: e.target.value }))} placeholder="如：松江府" /></label>
            <label>类型
              <select value={newEntity.type} onChange={e => setNewEntity(p => ({ ...p, type: e.target.value }))}>
                {ALL_TYPES.map(t => <option key={t} value={t}>{typeIcon(t)} {TYPE_LABELS[t]}</option>)}
              </select>
            </label>
            <label>名称<input value={newEntity.name} onChange={e => setNewEntity(p => ({ ...p, name: e.target.value }))} placeholder="如：松江府" /></label>
            <label>描述<input value={newEntity.description} onChange={e => setNewEntity(p => ({ ...p, description: e.target.value }))} placeholder="可选" /></label>
            <label>初始状况<input value={newEntity.current_status} onChange={e => setNewEntity(p => ({ ...p, current_status: e.target.value }))} placeholder="可选" /></label>
          </div>
          <div className="ws-new-entity-actions">
            <button className="btn primary" onClick={handleCreateEntity}>确认创建</button>
            <button className="btn ghost" onClick={() => setShowNewEntity(false)}>取消</button>
          </div>
        </div>
      )}

      {/* 主体左右分栏 */}
      <div className="ws-layout">
        {/* 左：实体列表 */}
        <aside className="ws-entity-list">
          {filteredEntities.length === 0 ? (
            <div className="ws-empty">暂无实体</div>
          ) : (
            filteredEntities.map(e => (
              <div
                key={e.id}
                className={`ws-entity-item ${selected?.entity?.id === e.id ? 'selected' : ''}`}
                onClick={() => loadEntity(e.id)}
              >
                <span className="ws-entity-icon">{typeIcon(e.type)}</span>
                <span className="ws-entity-name">{e.name}</span>
                <span className="ws-entity-id">{e.id !== e.name ? e.id : ''}</span>
              </div>
            ))
          )}
        </aside>

        {/* 右：详情面板 */}
        <main className="ws-detail-panel">
          {!selected ? (
            <div className="ws-placeholder">← 选择左侧实体查看详情</div>
          ) : (
            <>
              {/* 实体信息编辑 */}
              <section className="ws-section">
                <div className="ws-section-header">
                  <h3>{typeIcon(entityForm.type)} {entityForm.name}</h3>
                  <span className="ws-entity-id-badge">{entityForm.id}</span>
                </div>
                <div className="ws-form-grid">
                  <label className="ws-label">
                    类型
                    <select
                      value={entityForm.type}
                      onChange={e => setEntityForm(p => ({ ...p, type: e.target.value }))}
                    >
                      {ALL_TYPES.map(t => <option key={t} value={t}>{TYPE_LABELS[t]}</option>)}
                    </select>
                  </label>
                  <label className="ws-label">
                    名称
                    <input
                      value={entityForm.name}
                      onChange={e => setEntityForm(p => ({ ...p, name: e.target.value }))}
                    />
                  </label>
                  <label className="ws-label ws-full">
                    基本描述
                    <textarea
                      rows={3}
                      value={entityForm.description}
                      onChange={e => setEntityForm(p => ({ ...p, description: e.target.value }))}
                      placeholder="实体的基本情况（相对静态）"
                    />
                  </label>
                  <label className="ws-label ws-full">
                    当前状况
                    <textarea
                      rows={3}
                      value={entityForm.current_status}
                      onChange={e => setEntityForm(p => ({ ...p, current_status: e.target.value }))}
                      placeholder="实体目前的动态状况（ActionAgent 可更新）"
                    />
                  </label>
                </div>
                <div className="ws-form-actions">
                  <button className="btn primary" onClick={handleSaveEntity} disabled={saving}>
                    {saving ? '保存中…' : '保存实体'}
                  </button>
                  <button className="btn warning" onClick={handleDeleteEntity}>删除实体</button>
                  {msg && <span className="ws-inline-msg">{msg}</span>}
                </div>
              </section>

              {/* 地点坐标（仅 location 类型显示） */}
              {locCoords && entityForm.type === 'location' && (
                <section className="ws-section">
                  <div className="ws-section-header">
                    <h3>坐标 (虚拟平面直角坐标系)</h3>
                  </div>
                  <div className="ws-form-grid">
                    <label className="ws-label">
                      X (里)
                      <input
                        type="number"
                        value={locCoords.x}
                        onChange={e => setLocCoords(p => ({ ...p, x: e.target.value }))}
                      />
                    </label>
                    <label className="ws-label">
                      Y (里)
                      <input
                        type="number"
                        value={locCoords.y}
                        onChange={e => setLocCoords(p => ({ ...p, y: e.target.value }))}
                      />
                    </label>
                    <label className="ws-label">
                      区域
                      <input
                        value={locCoords.region}
                        onChange={e => setLocCoords(p => ({ ...p, region: e.target.value }))}
                        placeholder="如：京畿、江南"
                      />
                    </label>
                  </div>
                  <div className="ws-form-actions">
                    <button className="btn primary" onClick={handleSaveCoords} disabled={coordsSaving}>
                      {coordsSaving ? '保存中…' : '保存坐标'}
                    </button>
                    <span className="ws-coord-hint">
                      京城为原点 (0,0)，1里=500米，传信速度 300里/天
                    </span>
                  </div>
                </section>
              )}

              {/* 库存管理 */}
              <section className="ws-section">
                <div className="ws-section-header">
                  <h3>库存</h3>
                  <button className="btn ghost ws-add-inv-btn" onClick={() => setShowNewItem(!showNewItem)}>
                    + 添加
                  </button>
                </div>

                {selected.inventory.length === 0 && !showNewItem ? (
                  <div className="ws-empty">暂无库存记录</div>
                ) : (
                  <table className="ws-inventory-table">
                    <thead>
                      <tr>
                        <th>类目</th>
                        <th>物品名称</th>
                        <th>数量</th>
                        <th>单位/备注</th>
                        <th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {selected.inventory.map((item, idx) => {
                        const isEditingQty = editingInv?.idx === idx && editingInv?.field === 'quantity'
                        const isEditingDesc = editingInv?.idx === idx && editingInv?.field === 'description'
                        const currentQty = invEdits[idx]?.quantity !== undefined ? invEdits[idx].quantity : item.quantity
                        const currentDesc = invEdits[idx]?.description !== undefined ? invEdits[idx].description : item.description
                        return (
                          <tr key={`${item.category}-${item.item_name}`} className="ws-inv-row">
                            <td className="ws-inv-category">{item.category}</td>
                            <td className="ws-inv-name">{item.item_name}</td>
                            <td className="ws-inv-qty">
                              {isEditingQty ? (
                                <input
                                  className="ws-inline-edit"
                                  type="number"
                                  value={currentQty}
                                  autoFocus
                                  onChange={e => setInvEdits(p => ({ ...p, [idx]: { ...p[idx], quantity: e.target.value } }))}
                                  onBlur={() => handleSaveInvRow(item, idx)}
                                  onKeyDown={e => {
                                    if (e.key === 'Enter') handleSaveInvRow(item, idx)
                                    if (e.key === 'Escape') { setEditingInv(null); setInvEdits(p => { const n = {...p}; delete n[idx]; return n }) }
                                  }}
                                />
                              ) : (
                                <span
                                  className="ws-editable-cell"
                                  onClick={() => setEditingInv({ idx, field: 'quantity' })}
                                  title="点击编辑"
                                >
                                  {typeof currentQty === 'number' ? currentQty.toLocaleString() : currentQty}
                                </span>
                              )}
                            </td>
                            <td className="ws-inv-desc">
                              {isEditingDesc ? (
                                <input
                                  className="ws-inline-edit"
                                  value={currentDesc}
                                  autoFocus
                                  onChange={e => setInvEdits(p => ({ ...p, [idx]: { ...p[idx], description: e.target.value } }))}
                                  onBlur={() => handleSaveInvRow(item, idx)}
                                  onKeyDown={e => {
                                    if (e.key === 'Enter') handleSaveInvRow(item, idx)
                                    if (e.key === 'Escape') { setEditingInv(null); setInvEdits(p => { const n = {...p}; delete n[idx]; return n }) }
                                  }}
                                />
                              ) : (
                                <span
                                  className="ws-editable-cell ws-desc-cell"
                                  onClick={() => setEditingInv({ idx, field: 'description' })}
                                  title="点击编辑单位"
                                >
                                  {currentDesc || <span className="ws-placeholder-text">+ 单位</span>}
                                </span>
                              )}
                            </td>
                            <td>
                              <button
                                className="ws-del-btn"
                                onClick={() => handleDeleteInvItem(item)}
                                title="删除此项"
                              >✕</button>
                            </td>
                          </tr>
                        )
                      })}

                      {/* 新增行 */}
                      {showNewItem && (
                        <tr className="ws-new-item-row">
                          <td>
                            <input
                              className="ws-inline-edit"
                              list="ws-cat-list"
                              placeholder="类目"
                              value={newItem.category}
                              onChange={e => setNewItem(p => ({ ...p, category: e.target.value }))}
                            />
                            <datalist id="ws-cat-list">
                              {CATEGORY_SUGGESTIONS.map(c => <option key={c} value={c} />)}
                            </datalist>
                          </td>
                          <td>
                            <input
                              className="ws-inline-edit"
                              placeholder="名称"
                              value={newItem.item_name}
                              onChange={e => setNewItem(p => ({ ...p, item_name: e.target.value }))}
                            />
                          </td>
                          <td>
                            <input
                              className="ws-inline-edit"
                              type="number"
                              placeholder="数量"
                              value={newItem.quantity}
                              onChange={e => setNewItem(p => ({ ...p, quantity: e.target.value }))}
                            />
                          </td>
                          <td>
                            <input
                              className="ws-inline-edit"
                              placeholder="单位"
                              value={newItem.description}
                              onChange={e => setNewItem(p => ({ ...p, description: e.target.value }))}
                            />
                          </td>
                          <td className="ws-new-item-actions">
                            <button className="btn primary ws-confirm-btn" onClick={handleAddNewItem}>✓</button>
                            <button className="btn ghost ws-cancel-btn" onClick={() => { setShowNewItem(false); setNewItem({ category: '', item_name: '', quantity: '', description: '' }) }}>✕</button>
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                )}
              </section>
            </>
          )}
        </main>
      </div>
    </div>
  )
}
