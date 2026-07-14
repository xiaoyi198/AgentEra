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

function formatNumber(value, maximumFractionDigits = 2) {
  const number = Number(value)
  if (!Number.isFinite(number)) return String(value ?? '')
  return number.toLocaleString('zh-CN', { maximumFractionDigits })
}

function createIdempotencyKey() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  return `world-state-ui:${Date.now()}:${Math.random().toString(16).slice(2)}`
}

function typeIcon(type) {
  return TYPE_ICONS[type] || '❓'
}

export default function WorldStateView({ manifest = {}, terms = {} }) {
  const visibleFields = new Set(Array.isArray(manifest.visible_fields) ? manifest.visible_fields : ['id', 'type', 'name', 'description', 'current_status'])
  const sections = new Set(Array.isArray(manifest.sections) ? manifest.sections : ['summary', 'ongoing_events', 'location', 'inventory', 'metrics', 'parameters'])
  const entityTerm = terms.entity || '实体'
  const entitiesTerm = terms.entities || entityTerm

  const [entities, setEntities] = useState([])         // 全量实体列表（轻量，只含 id/type/name）
  const [selected, setSelected] = useState(null)       // 当前选中的完整实体 {entity, inventory}
  const [entityForm, setEntityForm] = useState(null)   // 编辑表单（镜像 selected.entity）
  const [filterType, setFilterType] = useState('all')
  const [searchKw, setSearchKw] = useState('')
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')                   // 操作反馈消息
  const [economy, setEconomy] = useState(null)
  const [economyLoading, setEconomyLoading] = useState(false)

  // 库存行内编辑
  const [editingInv, setEditingInv] = useState(null)   // { idx, field } 正在编辑的格子
  const [invEdits, setInvEdits] = useState({})         // idx → {quantity, description} 临时值


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

  const loadEconomy = useCallback(async () => {
    setEconomyLoading(true)
    try {
      const res = await fetch(`${API_BASE}/world-state/economy`)
      const data = await res.json()
      if (res.ok && !data.error) setEconomy(data)
    } catch (e) {
      console.error('Failed to load economic invariants:', e)
    } finally {
      setEconomyLoading(false)
    }
  }, [])

  useEffect(() => {
    loadEntities()
    loadEconomy()
  }, [loadEntities, loadEconomy])

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
    } catch {
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
      await loadEconomy()
      setMsg('✓ 已删除')
      setTimeout(() => setMsg(''), 2000)
    } catch {
      setMsg('✗ 删除失败')
    }
  }

  // ── 资产 / 指标 / 参数行内编辑保存 ──
  const handleSaveResourceRow = async (item, editKey) => {
    if (!selected) return
    const edited = invEdits[editKey] || {}
    const originalValue = item.quantity ?? item.value
    const qty = edited.quantity !== undefined ? Number(edited.quantity) : Number(originalValue)
    if (!Number.isFinite(qty)) { setMsg('✗ 数值必须是有限数字'); return }

    const resourceName = item.item_name || item.name
    setEditingInv(null)
    try {
      const res = await fetch(`${API_BASE}/world-state/${encodeURIComponent(selected.entity.id)}/inventory`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': createIdempotencyKey(),
        },
        body: JSON.stringify({
          resource_name: resourceName,
          quantity: qty,
          expected_version: item.version,
          reason: 'world_state_editor',
        }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok || data.error || data.status !== 'ok') {
        const detail = data.detail?.message || data.detail?.error || data.error || `HTTP ${res.status}`
        throw new Error(detail)
      }
      setInvEdits(prev => { const next = { ...prev }; delete next[editKey]; return next })
      await loadEntity(selected.entity.id)
      await loadEconomy()
      setMsg(`✓ ${resourceName} 已更新`)
      setTimeout(() => setMsg(''), 2000)
    } catch (e) {
      setMsg(`✗ 保存 ${resourceName} 失败：${e.message || '未知错误'}，已刷新最新版本`)
      await loadEntity(selected.entity.id)
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
        await loadEconomy()
      } else {
        setMsg('✗ 创建失败: ' + JSON.stringify(data))
      }
    } catch {
      setMsg('✗ 网络错误')
    }
  }

  const economyResources = [...(economy?.inventory?.by_resource || [])]
    .sort((left, right) => Number(right.base_value || 0) - Number(left.base_value || 0))
    .slice(0, 8)
  const transferHealthy = economy?.transfers?.conserved !== false
  const causalIssues = Number(economy?.causal_effects?.rejections || 0)
    + Number(economy?.causal_effects?.failures || 0)

  // ── 渲染 ──
  return (
    <div className="world-state-view">
      {/* 顶部工具栏 */}
      <div className="ws-toolbar">
        <input
          className="ws-search"
          type="text"
          placeholder={`${terms.search || '搜索'}${entityTerm}名称或 ID…`}
          value={searchKw}
          onChange={e => setSearchKw(e.target.value)}
        />
        <div className="ws-type-tabs">
          <button
            className={`ws-type-tab ${filterType === 'all' ? 'active' : ''}`}
            onClick={() => setFilterType('all')}
          >{`全部${entitiesTerm}`} ({entities.length})</button>
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
          + 新建{entityTerm}
        </button>
        {msg && <span className="ws-msg">{msg}</span>}
      </div>

      {sections.has('inventory') && economy && (
        <section className={`ws-economy-dashboard ${transferHealthy ? 'healthy' : 'warning'}`}>
          <div className="ws-economy-header">
            <div>
              <strong>统一价值锚经济监测</strong>
              <span>模拟时间 {formatNumber(economy.sim_time || 0)} 秒 · 账本 {formatNumber(economy.ledger?.entries_total || 0, 0)} 条</span>
            </div>
            <button className="btn ghost ws-economy-refresh" onClick={loadEconomy} disabled={economyLoading}>
              {economyLoading ? '刷新中…' : '刷新经济快照'}
            </button>
          </div>
          <div className="ws-economy-metrics">
            <div><span>当前基础价值</span><strong>{formatNumber(economy.inventory?.total_base_value || 0)}</strong></div>
            <div><span>累计发行</span><strong>{formatNumber(economy.ledger?.issued_base_value || 0)}</strong></div>
            <div><span>累计销毁</span><strong>{formatNumber(economy.ledger?.destroyed_base_value || 0)}</strong></div>
            <div className={transferHealthy ? 'ok' : 'danger'}>
              <span>转移守恒差额</span>
              <strong>{formatNumber(economy.transfers?.net_base_value || 0, 8)}</strong>
              <small>{economy.transfers?.batch_count || 0} 批 / {economy.transfers?.mismatch_batch_count || 0} 异常</small>
            </div>
            <div className={causalIssues ? 'danger' : 'ok'}>
              <span>因果效果拒绝/故障</span>
              <strong>{formatNumber(causalIssues, 0)}</strong>
              <small>{formatNumber((economy.causal_effects?.rejection_rate || 0) * 100)}%</small>
            </div>
          </div>
          {economyResources.length > 0 && (
            <div className="ws-economy-resources">
              {economyResources.map(resource => (
                <div key={resource.resource_name}>
                  <span>{resource.resource_name}</span>
                  <strong>{formatNumber(resource.base_value)}</strong>
                  <small>{formatNumber(resource.quantity)} × {formatNumber(resource.anchor_rate, 6)}</small>
                </div>
              ))}
            </div>
          )}
        </section>
      )}

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
            <div className="ws-placeholder">← 选择左侧{entityTerm}查看详情</div>
          ) : (
            <>
              {/* entity summary */}
              {sections.has('summary') && (
                <section className="ws-section">
                  <div className="ws-section-header">
                    <h3>{typeIcon(entityForm.type)} {visibleFields.has('name') ? entityForm.name : entityTerm}</h3>
                    {visibleFields.has('id') && <span className="ws-entity-id-badge">{entityForm.id}</span>}
                  </div>
                  <div className="ws-form-grid">
                    {visibleFields.has('type') && (
                      <label className="ws-label">
                        类型
                        <select value={entityForm.type} onChange={event => setEntityForm(previous => ({ ...previous, type: event.target.value }))}>
                          {ALL_TYPES.map(type => <option key={type} value={type}>{TYPE_LABELS[type]}</option>)}
                        </select>
                      </label>
                    )}
                    {visibleFields.has('name') && (
                      <label className="ws-label">
                        名称
                        <input value={entityForm.name} onChange={event => setEntityForm(previous => ({ ...previous, name: event.target.value }))} />
                      </label>
                    )}
                    {visibleFields.has('description') && (
                      <label className="ws-label ws-full">
                        基本描述
                        <textarea rows={3} value={entityForm.description} onChange={event => setEntityForm(previous => ({ ...previous, description: event.target.value }))} />
                      </label>
                    )}
                    {visibleFields.has('current_status') && (
                      <label className="ws-label ws-full">
                        当前状况
                        <textarea rows={3} value={entityForm.current_status} onChange={event => setEntityForm(previous => ({ ...previous, current_status: event.target.value }))} />
                      </label>
                    )}
                    {visibleFields.has('updated_at') && <div className="ws-label">更新时间<span>{formatNumber(entityForm.updated_at || 0)}</span></div>}
                    {visibleFields.has('version') && <div className="ws-label">版本<span>v{entityForm.version ?? 0}</span></div>}
                  </div>
                  <div className="ws-form-actions">
                    <button className="btn primary" onClick={handleSaveEntity} disabled={saving}>
                      {saving ? '保存中…' : `保存${entityTerm}`}
                    </button>
                    <button className="btn warning" onClick={handleDeleteEntity}>删除{entityTerm}</button>
                    {msg && <span className="ws-inline-msg">{msg}</span>}
                  </div>
                </section>
              )}

              {/* ongoing events */}
              {sections.has('ongoing_events') && (
                <OngoingEventsCard
                  events={selected.ongoing_events || []}
                  onChanged={() => loadEntity(entityForm.id)}
                  onMsg={setMsg}
                />
              )}

              {/* 地点坐标（仅 location 类型显示） */}
              {sections.has('location') && locCoords && entityForm.type === 'location' && (
                <section className="ws-section">
                  <div className="ws-section-header">
                    <h3>{terms.inventory || '库存'}</h3>
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

              {/* 锚定资产 */}
              {sections.has('inventory') && (
                <section className="ws-section">
                <div className="ws-section-header ws-section-header-split">
                  <h3>锚定资产</h3>
                  <span className="ws-valuation-total">
                    总基础价值 {formatNumber(selected.inventory_valuation?.base_value ?? 0)}
                  </span>
                </div>

                {(() => {
                  const visibleItems = (selected.inventory || []).filter(item => item.quantity !== -1)
                  return visibleItems.length === 0 ? (
                    <div className="ws-empty">暂无可支出资产</div>
                  ) : (
                    <table className="ws-inventory-table">
                      <thead>
                        <tr>
                          <th>资产</th>
                          <th>数量</th>
                          <th>单位/说明</th>
                          <th>价值锚</th>
                          <th>基础价值</th>
                          <th>版本</th>
                        </tr>
                      </thead>
                      <tbody>
                        {visibleItems.map(item => {
                          const editKey = `asset:${item.item_name}`
                          const isEditingQty = editingInv?.idx === editKey && editingInv?.field === 'quantity'
                          const currentQty = invEdits[editKey]?.quantity !== undefined
                            ? invEdits[editKey].quantity
                            : item.quantity
                          return (
                            <tr key={item.item_name} className="ws-inv-row">
                              <td className="ws-inv-name">{item.item_name}</td>
                              <td className="ws-inv-qty">
                                {isEditingQty ? (
                                  <input
                                    className="ws-inline-edit"
                                    type="number"
                                    step="any"
                                    value={currentQty}
                                    autoFocus
                                    onChange={e => setInvEdits(prev => ({
                                      ...prev,
                                      [editKey]: { ...prev[editKey], quantity: e.target.value },
                                    }))}
                                    onBlur={() => handleSaveResourceRow(item, editKey)}
                                    onKeyDown={e => {
                                      if (e.key === 'Enter') { e.preventDefault(); e.currentTarget.blur() }
                                      if (e.key === 'Escape') {
                                        e.preventDefault()
                                        setEditingInv(null)
                                        setInvEdits(prev => { const next = { ...prev }; delete next[editKey]; return next })
                                      }
                                    }}
                                  />
                                ) : (
                                  <span
                                    className="ws-editable-cell"
                                    onClick={() => setEditingInv({ idx: editKey, field: 'quantity' })}
                                    title="点击编辑；保存时校验版本并使用幂等命令"
                                  >
                                    {formatNumber(currentQty)}
                                  </span>
                                )}
                              </td>
                              <td className="ws-inv-desc">{item.description || '—'}</td>
                              <td className="ws-anchor-rate">× {formatNumber(item.anchor_rate, 6)}</td>
                              <td className="ws-base-value">{formatNumber(item.base_value)}</td>
                              <td><span className="ws-version-badge">v{item.version ?? 0}</span></td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  )
                })()}
                </section>
              )}

              {/* 世界指标：不可支出，仅由模拟与命令更新 */}
              {sections.has('metrics') && (
                <section className="ws-section">
                <div className="ws-section-header">
                  <h3>{terms.metrics || '指标'}</h3>
                  <span className="ws-section-note">非库存，不参与资产余额与价值合计</span>
                </div>
                {(selected.metrics || []).length === 0 ? (
                  <div className="ws-empty">暂无指标记录</div>
                ) : (
                  <table className="ws-inventory-table">
                    <thead>
                      <tr>
                        <th>指标</th>
                        <th>当前值</th>
                        <th>说明</th>
                        <th>版本</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selected.metrics.map(item => (
                        <tr key={item.name} className="ws-inv-row">
                          <td className="ws-inv-name">{item.name}</td>
                          <td className="ws-metric-value">{formatNumber(item.value)}</td>
                          <td className="ws-inv-desc">{item.description || '—'}</td>
                          <td><span className="ws-version-badge">v{item.version ?? 0}</span></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                </section>
              )}

              {/* 核心参数 */}
              {sections.has('parameters') && (
                <section className="ws-section">
                <div className="ws-section-header">
                  <h3>{terms.parameters || '参数'}</h3>
                  <span className="ws-section-note">独立参数存储，不计入库存</span>
                </div>

                {(selected.parameters || []).length === 0 ? (
                  <div className="ws-empty">暂无参数记录（当前实体可能不适用）</div>
                ) : (
                  <table className="ws-inventory-table">
                    <thead>
                      <tr>
                        <th>参数</th>
                        <th>数值</th>
                        <th>说明</th>
                        <th>版本</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selected.parameters.map(item => {
                        const editKey = `parameter:${item.name}`
                        const isEditingQty = editingInv?.idx === editKey && editingInv?.field === 'quantity'
                        const currentQty = invEdits[editKey]?.quantity !== undefined
                          ? invEdits[editKey].quantity
                          : item.value
                        return (
                          <tr key={item.name} className="ws-inv-row">
                            <td className="ws-inv-name">{item.name}</td>
                            <td className="ws-inv-qty">
                              {isEditingQty ? (
                                <input
                                  className="ws-inline-edit"
                                  type="number"
                                  step="any"
                                  value={currentQty}
                                  autoFocus
                                  onChange={e => setInvEdits(prev => ({
                                    ...prev,
                                    [editKey]: { ...prev[editKey], quantity: e.target.value },
                                  }))}
                                  onBlur={() => handleSaveResourceRow(item, editKey)}
                                  onKeyDown={e => {
                                    if (e.key === 'Enter') { e.preventDefault(); e.currentTarget.blur() }
                                    if (e.key === 'Escape') {
                                      e.preventDefault()
                                      setEditingInv(null)
                                      setInvEdits(prev => { const next = { ...prev }; delete next[editKey]; return next })
                                    }
                                  }}
                                />
                              ) : (
                                <span
                                  className="ws-editable-cell"
                                  onClick={() => setEditingInv({ idx: editKey, field: 'quantity' })}
                                  title="点击编辑；保存时校验参数版本"
                                >
                                  {formatNumber(currentQty)}
                                </span>
                              )}
                            </td>
                            <td className="ws-inv-desc">{item.description || '—'}</td>
                            <td><span className="ws-version-badge">v{item.version ?? 0}</span></td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                )}
                </section>
              )}
            </>
          )}
        </main>
      </div>
    </div>
  )
}


function OngoingEventsCard({ events, onChanged, onMsg }) {
  const [editingId, setEditingId] = useState(null)
  const [draftDays, setDraftDays] = useState('')

  const handleDelete = async (eventId) => {
    if (!confirm(`确定取消事件 ${eventId} 吗？该事件的预期结果将不会被应用。`)) return
    try {
      const res = await fetch(`${API_BASE}/world-state/events/${encodeURIComponent(eventId)}`, {
        method: 'DELETE',
      })
      const data = await res.json()
      if (data.status === 'ok') {
        onMsg && onMsg(`事件 ${eventId} 已取消`)
        onChanged && onChanged()
      } else {
        onMsg && onMsg(`取消失败：${data.status || '未知'}`)
      }
    } catch (e) {
      onMsg && onMsg(`取消失败：${e.message}`)
    }
  }

  const handleSaveDays = async (eventId) => {
    const days = parseFloat(draftDays)
    if (Number.isNaN(days) || days < 0) {
      onMsg && onMsg('剩余天数必须是非负数')
      return
    }
    try {
      const res = await fetch(`${API_BASE}/world-state/events/${encodeURIComponent(eventId)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ duration_days: days }),
      })
      const data = await res.json()
      if (data.status === 'ok') {
        onMsg && onMsg(`事件 ${eventId} 剩余天数已更新为 ${days}`)
        setEditingId(null)
        setDraftDays('')
        onChanged && onChanged()
      } else {
        onMsg && onMsg(`修改失败：${data.status || '未知'}`)
      }
    } catch (e) {
      onMsg && onMsg(`修改失败：${e.message}`)
    }
  }

  return (
    <section className="ws-section">
      <div className="ws-section-header">
        <h3>正在发生的事情</h3>
        <span className="ws-entity-id-badge">{events.length} 条</span>
      </div>
      {events.length === 0 ? (
        <div className="ws-empty">无进行中事件</div>
      ) : (
        <table className="ws-inventory-table">
          <thead>
            <tr>
              <th>事件描述</th>
              <th>类型</th>
              <th>剩余天数</th>
              <th>预期结果</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {events.map(ev => {
              const isEditing = editingId === ev.event_id
              const expected = ev.expected_result || {}
              const newStatus = expected.new_status || ''
              const deltas = expected.inventory_deltas || []
              return (
                <tr key={ev.event_id} className="ws-inv-row">
                  <td className="ws-inv-name" title={ev.event_id}>{ev.description}</td>
                  <td className="ws-inv-desc">{ev.event_type}</td>
                  <td className="ws-inv-qty">
                    {isEditing ? (
                      <input
                        className="ws-inline-edit"
                        type="number"
                        step="0.1"
                        value={draftDays}
                        autoFocus
                        onChange={e => setDraftDays(e.target.value)}
                        onBlur={() => handleSaveDays(ev.event_id)}
                        onKeyDown={e => {
                          if (e.key === 'Enter') handleSaveDays(ev.event_id)
                          if (e.key === 'Escape') { setEditingId(null); setDraftDays('') }
                        }}
                      />
                    ) : (
                      <span
                        className="ws-editable-cell"
                        onClick={() => { setEditingId(ev.event_id); setDraftDays(String(ev.days_remaining?.toFixed(1) || 0)) }}
                        title="点击编辑剩余天数"
                      >
                        {ev.days_remaining?.toFixed(1) ?? '?'} 天
                      </span>
                    )}
                  </td>
                  <td className="ws-inv-desc" style={{ fontSize: '12px' }}>
                    {newStatus && <div>状态 → {newStatus}</div>}
                    {deltas.map((d, i) => (
                      <div key={i}>{d.resource} {d.delta > 0 ? '+' : ''}{d.delta}{d.reason ? `（${d.reason}）` : ''}</div>
                    ))}
                    {!newStatus && deltas.length === 0 && <span style={{ color: '#999' }}>（空）</span>}
                  </td>
                  <td>
                    <button className="btn warning" onClick={() => handleDelete(ev.event_id)}>取消</button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </section>
  )
}
