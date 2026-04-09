import { useState, useEffect, useCallback, useRef } from 'react'

const RANK_TITLES = {
  1: '知县', 5: '知府', 8: '布政使', 9: '通政司',
  10: '内阁大学士', 12: '六部尚书', 13: '太监', 15: '皇帝',
}
const ROLES = ['official', 'eunuch', 'player']
const AGENT_TYPES = ['official', 'institution']

// ── API helpers ──

async function api(url, opts = {}) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  })
  return res.json()
}

const fetchAll = () => api('/api/editor/officials')
const fetchPersonas = () => api('/api/editor/personas')
const saveOfficial = (id, data) => api(`/api/editor/officials/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(data) })
const deleteOfficial = (id) => api(`/api/editor/officials/${encodeURIComponent(id)}`, { method: 'DELETE' })
const batchSave = (officials) => api('/api/editor/officials/batch', { method: 'POST', body: JSON.stringify(officials) })
const previewPrompt = (data) => api('/api/editor/prompt-preview', { method: 'POST', body: JSON.stringify(data) })
const upsertPersona = (name, data) => api(`/api/editor/personas/${encodeURIComponent(name)}`, { method: 'PUT', body: JSON.stringify(data) })
const deletePersonaApi = (name) => api(`/api/editor/personas/${encodeURIComponent(name)}`, { method: 'DELETE' })

// ── Hierarchy tree builder ──

function buildTree(officials) {
  const map = {}
  officials.forEach(o => { map[o.id] = { ...o, children: [] } })
  const roots = []
  officials.forEach(o => {
    if (o.superior_id && map[o.superior_id]) {
      map[o.superior_id].children.push(map[o.id])
    } else if (!o.superior_id) {
      roots.push(map[o.id])
    } else {
      roots.push(map[o.id]) // orphan
    }
  })
  return roots
}

// ── Collapsible Tree Node ──

function TreeNode({ node, depth, selected, collapsed, onSelect, onToggleCollapse, onToggleActive, onDragStart, onDragOver, onDrop }) {
  const isSelected = selected === node.id
  const rankTitle = RANK_TITLES[node.rank] || `品级${node.rank}`
  const hasChildren = node.children.length > 0
  const isCollapsed = collapsed.has(rankKey(node))

  // Group same-rank children
  const rankGroups = {}
  node.children.forEach(child => {
    const rk = child.rank
    if (!rankGroups[rk]) rankGroups[rk] = []
    rankGroups[rk].push(child)
  })

  return (
    <div className="tree-branch">
      <div
        className={`tree-node ${isSelected ? 'selected' : ''} ${node.role === 'player' ? 'player' : ''} ${node.enabled === false ? 'disabled' : ''}`}
        style={{ paddingLeft: `${depth * 24 + 12}px` }}
        draggable={node.role !== 'player'}
        onClick={() => onSelect(node.id)}
        onDragStart={e => { e.dataTransfer.setData('text/plain', node.id); onDragStart(node.id) }}
        onDragOver={e => { e.preventDefault(); onDragOver(node.id) }}
        onDrop={e => { e.preventDefault(); onDrop(node.id, e.dataTransfer.getData('text/plain')) }}
      >
        {/* Collapse toggle for nodes with children */}
        {hasChildren ? (
          <span className="tree-toggle" onClick={e => { e.stopPropagation(); onToggleCollapse(rankKey(node)) }}>
            {isCollapsed ? '▸' : '▾'}
          </span>
        ) : (
          <span className="tree-toggle-placeholder" />
        )}

        <span className="tree-icon">
          {node.agent_type === 'institution' ? '\u{1F3DB}' : node.role === 'player' ? '\u{1F451}' : node.role === 'eunuch' ? '\u{1F531}' : '\u{1F393}'}
        </span>
        <span className="tree-name">{node.name}</span>
        <span className="tree-rank">{rankTitle}</span>

        {/* Enabled toggle */}
        {node.role !== 'player' && (
          <span
            className={`tree-enabled-dot ${node.enabled === false ? 'off' : 'on'}`}
            title={node.enabled === false ? '已禁用（点击启用）' : '已启用（点击禁用）'}
            onClick={e => { e.stopPropagation(); onToggleActive(node.id) }}
          />
        )}

        {/* Active badge — has extra_prompt */}
        {node.role !== 'player' && node.extra_prompt && (
          <span className="tree-active-badge" title="活跃：有附加提示词">活跃</span>
        )}

        <span className="tree-location">{node.location_id}</span>
      </div>

      {/* Children — collapsible */}
      {!isCollapsed && Object.entries(rankGroups).map(([rank, children]) => {
        const groupKey = `${node.id}__rank_${rank}`
        const groupCollapsed = collapsed.has(groupKey)

        // If more than 1 child at same rank, wrap in a collapsible group
        if (children.length > 1) {
          const groupTitle = RANK_TITLES[Number(rank)] || `品级${rank}`
          return (
            <div key={groupKey} className="rank-group">
              <div
                className="rank-group-header"
                style={{ paddingLeft: `${(depth + 1) * 24 + 12}px` }}
                onClick={() => onToggleCollapse(groupKey)}
              >
                <span className="tree-toggle">{groupCollapsed ? '▸' : '▾'}</span>
                <span className="rank-group-label">{groupTitle} ({children.length})</span>
              </div>
              {!groupCollapsed && children.map(child => (
                <TreeNode
                  key={child.id} node={child} depth={depth + 2}
                  selected={selected} collapsed={collapsed}
                  onSelect={onSelect} onToggleCollapse={onToggleCollapse}
                  onToggleActive={onToggleActive}
                  onDragStart={onDragStart} onDragOver={onDragOver} onDrop={onDrop}
                />
              ))}
            </div>
          )
        }

        // Single child, render directly
        return children.map(child => (
          <TreeNode
            key={child.id} node={child} depth={depth + 1}
            selected={selected} collapsed={collapsed}
            onSelect={onSelect} onToggleCollapse={onToggleCollapse}
            onToggleActive={onToggleActive}
            onDragStart={onDragStart} onDragOver={onDragOver} onDrop={onDrop}
          />
        ))
      })}
    </div>
  )
}

function rankKey(node) { return `collapse_${node.id}` }

// ── Detail Editor Panel ──

function DetailPanel({ official, personas, allOfficials, onChange, onSave, onDelete, promptText, onPreview }) {
  if (!official) {
    return <div className="detail-empty">在左侧选择或新建一名官员</div>
  }

  const personaKeys = Object.keys(personas)
  const otherOfficials = allOfficials.filter(o => o.id !== official.id)

  return (
    <div className="detail-panel">
      <h3>{official._isNew ? '新建官员' : `编辑: ${official.name}`}</h3>

      <div className="form-grid">
        <label>ID（唯一标识）</label>
        <input value={official.id} onChange={e => onChange({ ...official, id: e.target.value })} placeholder="如：松江知府_陈五" />

        <label>姓名</label>
        <input value={official.name} onChange={e => onChange({ ...official, name: e.target.value })} placeholder="陈五" />

        <label>品级</label>
        <select value={official.rank} onChange={e => onChange({ ...official, rank: Number(e.target.value) })}>
          {Object.entries(RANK_TITLES).map(([r, t]) => (
            <option key={r} value={r}>{r} — {t}</option>
          ))}
        </select>

        <label>驻地</label>
        <input value={official.location_id} onChange={e => onChange({ ...official, location_id: e.target.value })} placeholder="如：吴县" />

        <label>角色</label>
        <select value={official.role} onChange={e => onChange({ ...official, role: e.target.value })}>
          {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
        </select>

        <label>Agent 类型</label>
        <select value={official.agent_type} onChange={e => onChange({ ...official, agent_type: e.target.value })}>
          {AGENT_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
        </select>

        <label>直属上级</label>
        <select value={official.superior_id || ''} onChange={e => onChange({ ...official, superior_id: e.target.value || null })}>
          <option value="">无（顶级）</option>
          {otherOfficials.map(o => (
            <option key={o.id} value={o.id}>{o.name}（{o.id}）</option>
          ))}
        </select>

        <label>人格种子</label>
        <select value={official.personality_seed} onChange={e => onChange({ ...official, personality_seed: e.target.value })}>
          {personaKeys.map(p => <option key={p} value={p}>{p}</option>)}
        </select>


        {/* Enabled toggle */}
        <label>启用状态</label>
        <div className="active-toggle-row">
          <button
            className={`toggle-btn ${official.enabled !== false ? 'on' : 'off'}`}
            onClick={() => onChange({ ...official, enabled: official.enabled === false ? true : false })}
          >
            {official.enabled !== false ? '启用' : '禁用'}
          </button>
          <span className="toggle-hint">
            {official.enabled !== false ? '该官员参与模拟，Agent 将被创建' : '该官员不参与模拟'}
          </span>
        </div>
      </div>

      {/* Extra prompt — always visible; having content marks agent as "活跃" */}
      <div className="extra-prompt-section">
        <label>
          附加提示词
          {official.extra_prompt ? <span className="active-flag">活跃</span> : null}
          <span className="label-hint">（填写后该官员标记为「活跃」，体现个体特殊性）</span>
        </label>
        <textarea
          value={official.extra_prompt || ''}
          onChange={e => onChange({ ...official, extra_prompt: e.target.value })}
          placeholder="例如：你暗中与江南盐商有利益往来，每年收受白银三千两。你特别忌惮巡按御史的巡查..."
          rows={4}
        />
      </div>

      <div className="detail-actions">
        <button className="btn-save" onClick={onSave}>保存</button>
        <button className="btn-preview" onClick={onPreview}>预览 Prompt</button>
        {official._isNew ? null : <button className="btn-delete" onClick={onDelete}>删除</button>}
      </div>

      {promptText !== null && (
        <div className="prompt-preview">
          <h4>System Prompt 预览</h4>
          <pre>{promptText}</pre>
        </div>
      )}
    </div>
  )
}

// ── Persona Editor ──

function PersonaEditor({ personas, onUpdate, showToast }) {
  const [editing, setEditing] = useState(null) // { originalName, name, description }
  const [adding, setAdding] = useState(false)

  const startEdit = (name) => {
    setEditing({ originalName: name, name, description: personas[name] })
    setAdding(false)
  }

  const startAdd = () => {
    setEditing({ originalName: null, name: '', description: '' })
    setAdding(true)
  }

  const handleSave = async () => {
    if (!editing || !editing.name.trim()) {
      showToast('人格名称不能为空', 'err')
      return
    }
    const apiName = editing.originalName || editing.name
    await upsertPersona(apiName, { name: editing.name, description: editing.description })
    showToast('人格已保存（已热重载）')
    setEditing(null)
    setAdding(false)
    onUpdate()
  }

  const handleDelete = async (name) => {
    if (!confirm(`确定删除人格「${name}」？使用此人格的官员不受影响，但新官员将无法选择此人格。`)) return
    await deletePersonaApi(name)
    showToast('已删除')
    if (editing && editing.originalName === name) setEditing(null)
    onUpdate()
  }

  const handleCancel = () => {
    setEditing(null)
    setAdding(false)
  }

  return (
    <div className="persona-editor">
      <div className="persona-header">
        <h3>人格种子管理</h3>
        <span className="persona-hint">保存后立即生效，可在官员编辑中选用</span>
      </div>

      <div className="persona-list">
        {Object.entries(personas).map(([name, desc]) => {
          const isEditing = editing && editing.originalName === name
          return (
            <div key={name} className={`persona-card ${isEditing ? 'editing' : ''}`}>
              {isEditing ? (
                <>
                  <input
                    className="persona-name-input"
                    value={editing.name}
                    onChange={e => setEditing({ ...editing, name: e.target.value })}
                    placeholder="人格名称"
                  />
                  <textarea
                    className="persona-desc-input"
                    value={editing.description}
                    onChange={e => setEditing({ ...editing, description: e.target.value })}
                    rows={4}
                  />
                  <div className="persona-card-actions">
                    <button className="btn-save btn-sm" onClick={handleSave}>保存</button>
                    <button className="btn-cancel btn-sm" onClick={handleCancel}>取消</button>
                  </div>
                </>
              ) : (
                <>
                  <div className="persona-card-header">
                    <span className="persona-name">{name}</span>
                    <div className="persona-card-btns">
                      <button className="btn-edit-sm" onClick={() => startEdit(name)}>编辑</button>
                      <button className="btn-remove" onClick={() => handleDelete(name)}>x</button>
                    </div>
                  </div>
                  <p className="persona-desc">{desc}</p>
                </>
              )}
            </div>
          )
        })}

        {/* Add new persona */}
        {adding && editing && editing.originalName === null ? (
          <div className="persona-card editing">
            <input
              className="persona-name-input"
              value={editing.name}
              onChange={e => setEditing({ ...editing, name: e.target.value })}
              placeholder="新人格名称（如：阴狠、清高、好大喜功）"
              autoFocus
            />
            <textarea
              className="persona-desc-input"
              value={editing.description}
              onChange={e => setEditing({ ...editing, description: e.target.value })}
              placeholder="描述这个人格在朝堂上的行为模式..."
              rows={4}
            />
            <div className="persona-card-actions">
              <button className="btn-save btn-sm" onClick={handleSave}>保存</button>
              <button className="btn-cancel btn-sm" onClick={handleCancel}>取消</button>
            </div>
          </div>
        ) : null}
      </div>

      {!adding && (
        <button className="btn-add" onClick={startAdd} style={{ marginTop: 12 }}>+ 新增人格种子</button>
      )}
    </div>
  )
}

// ── Location Editor ──

function SystemAgentEditor() {
  return (
    <div className="system-agent-editor">
      <div className="placeholder-content">
        <h2>系统 Agent 编辑</h2>
        <p className="placeholder-text">功能开发中...</p>
        <p className="placeholder-hint">此处将用于编辑 Router、ActionAgent 等系统级 Agent 的配置</p>
      </div>
    </div>
  )
}

// ── Import/Export ──

function ImportExport({ officials, locations, onImport }) {
  const fileRef = useRef()

  const handleExport = () => {
    const data = JSON.stringify({ officials, locations }, null, 2)
    const blob = new Blob([data], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'officials.json'
    a.click()
    URL.revokeObjectURL(url)
  }

  const handleImport = (e) => {
    const file = e.target.files[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => {
      try {
        const data = JSON.parse(reader.result)
        onImport(data)
      } catch (err) {
        alert('JSON 解析失败: ' + err.message)
      }
    }
    reader.readAsText(file)
    fileRef.current.value = ''
  }

  return (
    <div className="import-export">
      <button className="btn-export" onClick={handleExport}>导出 JSON</button>
      <button className="btn-import" onClick={() => fileRef.current.click()}>导入 JSON</button>
      <input ref={fileRef} type="file" accept=".json" style={{ display: 'none' }} onChange={handleImport} />
    </div>
  )
}

// ── Main App ──

export default function AgentEditor() {
  const [officials, setOfficials] = useState([])
  const [personas, setPersonas] = useState({})
  const [selectedId, setSelectedId] = useState(null)
  const [editForm, setEditForm] = useState(null)
  const [promptText, setPromptText] = useState(null)
  const [collapsed, setCollapsed] = useState(new Set())
  const [tab, setTab] = useState('tree') // tree | system_agents | personas
  const [toast, setToast] = useState(null)

  const showToast = useCallback((msg, type = 'ok') => {
    setToast({ msg, type })
    setTimeout(() => setToast(null), 2000)
  }, [])

  const reloadPersonas = useCallback(() => {
    fetchPersonas().then(setPersonas)
  }, [])

  // Load data
  useEffect(() => {
    fetchAll().then(data => {
      setOfficials(data.officials || [])
    })
    reloadPersonas()
  }, [reloadPersonas])

  // Select official
  const handleSelect = useCallback((id) => {
    setSelectedId(id)
    const o = officials.find(o => o.id === id)
    if (o) {
      setEditForm({ ...o })
      setPromptText(null)
    }
  }, [officials])

  // New official
  const handleNew = () => {
    const blank = {
      id: '', name: '', rank: 1, location_id: '',
      role: 'official', agent_type: 'official',
      superior_id: null, personality_seed: Object.keys(personas)[0] || '忠直',
      enabled: true, extra_prompt: '',
      _isNew: true,
    }
    setSelectedId(null)
    setEditForm(blank)
    setPromptText(null)
  }

  // Save
  const handleSave = async () => {
    if (!editForm || !editForm.id || !editForm.name) {
      showToast('ID 和姓名不能为空', 'err')
      return
    }
    const { _isNew, ...data } = editForm
    const res = await saveOfficial(data.id, data)
    if (res.status === 'ok') {
      showToast(res.created ? '新建成功' : '已保存')
      const fresh = await fetchAll()
      setOfficials(fresh.officials || [])
      setSelectedId(data.id)
      setEditForm({ ...data })
    }
  }

  // Delete
  const handleDelete = async () => {
    if (!editForm || editForm._isNew) return
    if (!confirm(`确定删除 ${editForm.name}（${editForm.id}）？`)) return
    await deleteOfficial(editForm.id)
    showToast('已删除')
    const fresh = await fetchAll()
    setOfficials(fresh.officials || [])
    setSelectedId(null)
    setEditForm(null)
    setPromptText(null)
  }

  // Preview prompt
  const handlePreview = async () => {
    if (!editForm) return
    const res = await previewPrompt({
      id: editForm.id,
      name: editForm.name,
      rank: editForm.rank,
      location_id: editForm.location_id,
      superior_id: editForm.superior_id,
      personality_seed: editForm.personality_seed,
      extra_prompt: editForm.extra_prompt || '',
    })
    setPromptText(res.prompt || '(empty)')
  }

  // Toggle collapse
  const handleToggleCollapse = useCallback((key) => {
    setCollapsed(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }, [])

  // Toggle enabled from tree
  const handleToggleActive = useCallback(async (id) => {
    const o = officials.find(o => o.id === id)
    if (!o) return
    const updated = { ...o, enabled: o.enabled === false ? true : false }
    const res = await saveOfficial(id, updated)
    if (res.status === 'ok') {
      const fresh = await fetchAll()
      setOfficials(fresh.officials || [])
      if (selectedId === id) {
        setEditForm(prev => prev ? { ...prev, enabled: updated.enabled } : prev)
      }
      showToast(updated.enabled ? `${o.name} 已启用` : `${o.name} 已禁用`)
    }
  }, [officials, selectedId, showToast])

  // Drag and drop to re-parent
  const handleDrop = async (targetId, draggedId) => {
    if (targetId === draggedId) return
    const dragged = officials.find(o => o.id === draggedId)
    const target = officials.find(o => o.id === targetId)
    if (!dragged || !target) return

    // Prevent circular
    let cur = target
    while (cur) {
      if (cur.superior_id === draggedId) {
        showToast('不能把上级拖到下级下面', 'err')
        return
      }
      cur = officials.find(o => o.id === cur.superior_id)
    }

    const updated = officials.map(o =>
      o.id === draggedId ? { ...o, superior_id: targetId } : o
    )
    setOfficials(updated)
    await batchSave(updated)
    showToast(`${dragged.name} → ${target.name} 下属`)

    if (selectedId === draggedId) {
      setEditForm(prev => prev ? { ...prev, superior_id: targetId } : prev)
    }
  }

  // Import
  const handleImport = async (data) => {
    if (data.officials) await batchSave(data.officials)
    const fresh = await fetchAll()
    setOfficials(fresh.officials || [])
    setSelectedId(null)
    setEditForm(null)
    showToast(`已导入 ${(fresh.officials || []).length} 名官员`)
  }

  const tree = buildTree(officials)

  // Stats
  const enabledCount = officials.filter(o => o.enabled !== false && o.role !== 'player').length
  const activeCount = officials.filter(o => o.extra_prompt && o.role !== 'player').length
  const totalCount = officials.filter(o => o.role !== 'player').length

  return (
    <div className="editor-root">
      {/* Header */}
      <header className="editor-header">
        <h1>Agent 编辑器</h1>
        <div className="header-stats">
          <span className="stat">启用: <strong>{enabledCount}</strong> / {totalCount}</span>
          <span className="stat">活跃: <strong>{activeCount}</strong></span>
        </div>
        <div className="header-actions">
          <ImportExport officials={officials} locations={[]} onImport={handleImport} />
          <a href="/" className="btn-back">返回主界面</a>
        </div>
      </header>

      {/* Toast */}
      {toast && <div className={`toast toast-${toast.type}`}>{toast.msg}</div>}

      {/* Body */}
      <div className="editor-body">
        {/* Left: Tree + Locations + Personas */}
        <div className="editor-left">
          <div className="tab-bar">
            <button className={tab === 'tree' ? 'active' : ''} onClick={() => setTab('tree')}>层级树</button>
            <button className={tab === 'personas' ? 'active' : ''} onClick={() => setTab('personas')}>人格种子</button>
            <button className={tab === 'system_agents' ? 'active' : ''} onClick={() => setTab('system_agents')}>系统 Agent</button>
          </div>

          {tab === 'tree' && (
            <>
              <div className="tree-toolbar">
                <button className="btn-add" onClick={handleNew}>+ 新建官员</button>
                <span className="tree-hint">拖拽调整上下级 | 圆点切换启用</span>
              </div>
              <div className="tree-container">
                {tree.map(root => (
                  <TreeNode
                    key={root.id}
                    node={root}
                    depth={0}
                    selected={selectedId}
                    collapsed={collapsed}
                    onSelect={handleSelect}
                    onToggleCollapse={handleToggleCollapse}
                    onToggleActive={handleToggleActive}
                    onDragStart={() => {}}
                    onDragOver={() => {}}
                    onDrop={handleDrop}
                  />
                ))}
              </div>
            </>
          )}

          {tab === 'personas' && (
            <PersonaEditor
              personas={personas}
              onUpdate={reloadPersonas}
              showToast={showToast}
            />
          )}

          {tab === 'system_agents' && (
            <SystemAgentEditor />
          )}
        </div>

        {/* Right: Detail */}
        <div className="editor-right">
          <DetailPanel
            official={editForm}
            personas={personas}
            allOfficials={officials}
            onChange={setEditForm}
            onSave={handleSave}
            onDelete={handleDelete}
            promptText={promptText}
            onPreview={handlePreview}
          />
        </div>
      </div>
    </div>
  )
}
