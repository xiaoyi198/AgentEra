import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import ReactFlow, {
  Background,
  Controls,
  MarkerType,
  useNodesState,
  useEdgesState,
} from 'reactflow'
import dagre from '@dagrejs/dagre'
import 'reactflow/dist/style.css'
import './RelationsView.css'

const API_BASE = '/api'

const TYPE_COLORS = {
  location:     { border: '#4a6fa5', label: '地点' },
  institution:  { border: '#9a3b3b', label: '机构' },
  organization: { border: '#3d6b4a', label: '组织' },
  person:       { border: '#c9a84c', label: '人物' },
  object:       { border: '#6b6b6b', label: '物品' },
}

const REL_STYLE = {
  subordinate: {
    label: '属',
    stroke: '#c9a84c',
    dashed: false,
    markerType: MarkerType.ArrowClosed,
  },
  affiliated: {
    label: '附',
    stroke: '#4a6fa5',
    dashed: true,
    markerType: MarkerType.Arrow,
  },
}

const NODE_W = 140
const NODE_H = 48

function layout(nodes, edges) {
  const g = new dagre.graphlib.Graph()
  g.setDefaultEdgeLabel(() => ({}))
  g.setGraph({ rankdir: 'TB', nodesep: 60, ranksep: 100 })

  nodes.forEach(n => g.setNode(n.id, { width: NODE_W, height: NODE_H }))
  edges.forEach(e => g.setEdge(e.source, e.target))
  dagre.layout(g)

  return nodes.map(n => {
    const { x, y } = g.node(n.id)
    return {
      ...n,
      position: { x: x - NODE_W / 2, y: y - NODE_H / 2 },
      targetPosition: 'top',
      sourcePosition: 'bottom',
    }
  })
}

function edgeId(p, c, r) {
  return `${p}->${c}|${r}`
}

function buildEdge(rel) {
  const s = REL_STYLE[rel.rel_type] || REL_STYLE.subordinate
  return {
    id: edgeId(rel.parent_id, rel.child_id, rel.rel_type),
    source: rel.parent_id,
    target: rel.child_id,
    label: s.label,
    labelStyle: { fill: s.stroke, fontSize: 12, fontWeight: 'bold' },
    labelBgStyle: { fill: '#1a1510', fillOpacity: 0.85 },
    labelBgPadding: [2, 4],
    style: {
      stroke: s.stroke,
      strokeWidth: 2,
      strokeDasharray: s.dashed ? '6 4' : undefined,
    },
    markerEnd: { type: s.markerType, color: s.stroke, width: 18, height: 18 },
    data: { rel_type: rel.rel_type, note: rel.note || '' },
  }
}

function buildNode(ent) {
  const color = TYPE_COLORS[ent.type]?.border || '#6b6b6b'
  return {
    id: ent.id,
    type: 'default',
    data: { label: ent.name, _type: ent.type },
    position: { x: 0, y: 0 },
    style: {
      background: '#1f1812',
      color: '#eadfc2',
      border: `1px solid ${color}`,
      borderRadius: 6,
      fontSize: 13,
      width: NODE_W,
      height: NODE_H,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
    },
  }
}

function RelTypePrompt({ title, onPick, onCancel }) {
  return (
    <div className="rv-modal-overlay" onClick={onCancel}>
      <div className="rv-modal" onClick={e => e.stopPropagation()}>
        <h3>{title}</h3>
        <div className="rv-modal-buttons">
          <button className="btn primary" onClick={() => onPick('subordinate')}>
            上下级 (属)
          </button>
          <button className="btn secondary" onClick={() => onPick('affiliated')}>
            附属 (附)
          </button>
          <button className="btn ghost" onClick={onCancel}>取消</button>
        </div>
      </div>
    </div>
  )
}

export default function RelationsView() {
  const [entities, setEntities] = useState([])
  const [relations, setRelations] = useState([])
  const [nodes, setNodes, onNodesChange] = useNodesState([])
  const [edges, setEdges, onEdgesChange] = useEdgesState([])
  const [filter, setFilter] = useState('all') // 'all' | 'subordinate' | 'affiliated'
  const [selectedNode, setSelectedNode] = useState(null)
  const [nodeDetail, setNodeDetail] = useState(null)
  const [pendingConnect, setPendingConnect] = useState(null) // { source, target } from onConnect
  const [pendingEdgeChange, setPendingEdgeChange] = useState(null) // existing edge to reassign
  const [msg, setMsg] = useState('')
  const didLayout = useRef(false)
  const nameMap = useMemo(
    () => Object.fromEntries(entities.map(e => [e.id, e.name])),
    [entities]
  )
  const typeMap = useMemo(
    () => Object.fromEntries(entities.map(e => [e.id, e.type])),
    [entities]
  )

  const flash = (s) => { setMsg(s); setTimeout(() => setMsg(''), 2000) }

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/relations`)
      const data = await res.json()
      if (data.error) { flash('✗ ' + data.error); return }
      setEntities(data.entities || [])
      setRelations(data.relations || [])
    } catch {
      flash('✗ 网络错误')
    }
  }, [])

  useEffect(() => { load() }, [load])

  // 首次布局：entities + relations 首批到达时跑一次 dagre，之后不再重排
  useEffect(() => {
    if (entities.length === 0 || didLayout.current) return
    const rawNodes = entities.map(buildNode)
    const rawEdges = relations.map(buildEdge) // 用全量边确定层级
    const laid = layout(rawNodes, rawEdges)
    setNodes(laid)
    didLayout.current = true
  }, [entities, relations, setNodes])

  // 边随 relations / filter 变化，但不触发重排
  useEffect(() => {
    const filteredRels = relations.filter(r =>
      filter === 'all' ? true : r.rel_type === filter
    )
    setEdges(filteredRels.map(buildEdge))
  }, [relations, filter, setEdges])

  // 手动重布局
  const relayout = useCallback(() => {
    if (entities.length === 0) return
    const rawNodes = entities.map(buildNode)
    const rawEdges = relations.map(buildEdge)
    const laid = layout(rawNodes, rawEdges)
    setNodes(laid)
    flash('✓ 已重新布局')
  }, [entities, relations, setNodes])

  // ── 写操作 ──
  const postRelation = async (parent_id, child_id, rel_type, note = '') => {
    const res = await fetch(`${API_BASE}/relations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ parent_id, child_id, rel_type, note }),
    })
    return res.json()
  }

  const deleteRelation = async (parent_id, child_id, rel_type) => {
    const qs = new URLSearchParams({ parent_id, child_id, rel_type }).toString()
    const res = await fetch(`${API_BASE}/relations?${qs}`, { method: 'DELETE' })
    return res.json()
  }

  const onConnect = useCallback((conn) => {
    if (!conn.source || !conn.target || conn.source === conn.target) return
    setPendingConnect({ source: conn.source, target: conn.target })
  }, [])

  const confirmCreate = async (rel_type) => {
    const { source, target } = pendingConnect
    setPendingConnect(null)
    const data = await postRelation(source, target, rel_type)
    if (data.error) { flash('✗ ' + data.error); return }
    setRelations(prev => {
      const filtered = prev.filter(r =>
        !(r.parent_id === source && r.child_id === target && r.rel_type === rel_type)
      )
      return [...filtered, { parent_id: source, child_id: target, rel_type, note: '' }]
    })
    flash('✓ 已创建边')
  }

  const onEdgesDelete = useCallback(async (toDelete) => {
    for (const e of toDelete) {
      const [src, restAfterSrc] = e.id.split('->')
      const [child, rel_type] = restAfterSrc.split('|')
      await deleteRelation(src, child, rel_type)
      setRelations(prev => prev.filter(r =>
        !(r.parent_id === src && r.child_id === child && r.rel_type === rel_type)
      ))
    }
    flash('✓ 已删除边')
  }, [])

  const onEdgeDoubleClick = useCallback((_evt, edge) => {
    setPendingEdgeChange({
      source: edge.source,
      target: edge.target,
      old_rel_type: edge.data?.rel_type,
    })
  }, [])

  const confirmChangeRelType = async (new_rel_type) => {
    const { source, target, old_rel_type } = pendingEdgeChange
    setPendingEdgeChange(null)
    if (new_rel_type === old_rel_type) return
    const del = await deleteRelation(source, target, old_rel_type)
    if (del.error) { flash('✗ 删除旧边失败'); return }
    const add = await postRelation(source, target, new_rel_type)
    if (add.error) { flash('✗ 新建失败: ' + add.error); return }
    setRelations(prev => {
      const filtered = prev.filter(r =>
        !(r.parent_id === source && r.child_id === target)
      )
      return [...filtered, { parent_id: source, child_id: target, rel_type: new_rel_type, note: '' }]
    })
    flash('✓ 已切换关系类型')
  }

  const onNodeClick = useCallback(async (_evt, node) => {
    setSelectedNode(node.id)
    try {
      const res = await fetch(`${API_BASE}/world-state/${encodeURIComponent(node.id)}`)
      const data = await res.json()
      if (data.error) { setNodeDetail(null); return }
      setNodeDetail(data)
    } catch {
      setNodeDetail(null)
    }
  }, [])

  const closeDetail = () => { setSelectedNode(null); setNodeDetail(null) }

  return (
    <div className="rv-container">
      {/* 工具栏 */}
      <div className="rv-toolbar">
        <div className="rv-filter">
          <button
            className={`btn ${filter === 'all' ? 'active' : 'ghost'}`}
            onClick={() => setFilter('all')}
          >全部</button>
          <button
            className={`btn ${filter === 'subordinate' ? 'active' : 'ghost'}`}
            onClick={() => setFilter('subordinate')}
          >仅上下级</button>
          <button
            className={`btn ${filter === 'affiliated' ? 'active' : 'ghost'}`}
            onClick={() => setFilter('affiliated')}
          >仅附属</button>
          <button className="btn ghost" onClick={relayout}>重新布局</button>
        </div>
        <div className="rv-legend">
          {Object.entries(TYPE_COLORS).map(([t, v]) => (
            <span key={t} className="rv-legend-item">
              <i style={{ background: v.border }} />
              {v.label}
            </span>
          ))}
          <span className="rv-legend-sep" />
          <span className="rv-legend-item">
            <i className="rv-edge-solid" style={{ background: REL_STYLE.subordinate.stroke }} />
            上下级
          </span>
          <span className="rv-legend-item">
            <i className="rv-edge-dashed" />
            附属
          </span>
        </div>
        <span className="rv-hint">拖动连线建边 · 选中边按 Delete 删除 · 双击边改类型</span>
        {msg && <span className="rv-msg">{msg}</span>}
      </div>

      {/* 图 + 详情 */}
      <div className="rv-main">
        <div className="rv-graph">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onEdgesDelete={onEdgesDelete}
            onEdgeDoubleClick={onEdgeDoubleClick}
            onNodeClick={onNodeClick}
            fitView
            fitViewOptions={{ padding: 0.2 }}
            deleteKeyCode={['Delete', 'Backspace']}
            nodesDeletable={false}
          >
            <Background color="#3a2e20" gap={24} />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>

        {selectedNode && nodeDetail && (
          <aside className="rv-detail">
            <div className="rv-detail-header">
              <h3>{nodeDetail.entity.name}</h3>
              <button className="modal-close" onClick={closeDetail}>&times;</button>
            </div>
            <div className="rv-detail-meta">
              <span className="rv-type-badge" style={{ borderColor: TYPE_COLORS[nodeDetail.entity.type]?.border }}>
                {TYPE_COLORS[nodeDetail.entity.type]?.label || nodeDetail.entity.type}
              </span>
              <span className="rv-id">{nodeDetail.entity.id}</span>
            </div>

            <section className="rv-rel-group">
              <h4>父级 ({nodeDetail.relations.parents.length})</h4>
              {nodeDetail.relations.parents.length === 0 ? (
                <div className="rv-empty">无</div>
              ) : (
                nodeDetail.relations.parents.map((p, i) => (
                  <div key={i} className="rv-rel-row">
                    <span className="rv-rel-name">{nameMap[p.parent_id] || p.parent_id}</span>
                    <span className="rv-rel-tag" data-rel={p.rel_type}>
                      {REL_STYLE[p.rel_type]?.label || p.rel_type}
                    </span>
                  </div>
                ))
              )}
            </section>

            <section className="rv-rel-group">
              <h4>子级 ({nodeDetail.relations.children.length})</h4>
              {nodeDetail.relations.children.length === 0 ? (
                <div className="rv-empty">无</div>
              ) : (
                nodeDetail.relations.children.map((c, i) => (
                  <div key={i} className="rv-rel-row">
                    <span className="rv-rel-name">{nameMap[c.child_id] || c.child_id}</span>
                    <span className="rv-rel-tag" data-rel={c.rel_type}>
                      {REL_STYLE[c.rel_type]?.label || c.rel_type}
                    </span>
                  </div>
                ))
              )}
            </section>

            <section className="rv-rel-group">
              <h4>同级 ({nodeDetail.relations.siblings.length})</h4>
              {nodeDetail.relations.siblings.length === 0 ? (
                <div className="rv-empty">无</div>
              ) : (
                nodeDetail.relations.siblings.map((s, i) => (
                  <div key={i} className="rv-rel-row rv-sibling">
                    <span className="rv-rel-name">{nameMap[s.sibling_id] || s.sibling_id}</span>
                    <span className="rv-rel-via">
                      via {nameMap[s.via_parent_id] || s.via_parent_id}
                      （{REL_STYLE[s.rel_type]?.label || s.rel_type}）
                    </span>
                  </div>
                ))
              )}
            </section>
          </aside>
        )}
      </div>

      {pendingConnect && (
        <RelTypePrompt
          title={`${nameMap[pendingConnect.source] || pendingConnect.source} → ${nameMap[pendingConnect.target] || pendingConnect.target}  选择关系类型`}
          onPick={confirmCreate}
          onCancel={() => setPendingConnect(null)}
        />
      )}

      {pendingEdgeChange && (
        <RelTypePrompt
          title={`切换关系类型：${nameMap[pendingEdgeChange.source] || pendingEdgeChange.source} → ${nameMap[pendingEdgeChange.target] || pendingEdgeChange.target}`}
          onPick={confirmChangeRelType}
          onCancel={() => setPendingEdgeChange(null)}
        />
      )}
    </div>
  )
}
