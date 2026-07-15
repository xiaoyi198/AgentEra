import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ReactFlow, {
  Background,
  Controls,
  MarkerType,
  useEdgesState,
  useNodesState,
} from 'reactflow'
import dagre from '@dagrejs/dagre'
import 'reactflow/dist/style.css'
import './RelationsView.css'

const API_BASE = '/api'
const NODE_W = 140
const NODE_H = 48

const T = {
  all: '\u5168\u90e8',
  activeOnly: '\u4ec5\u6d3b\u8dc3',
  includeInactive: '\u5305\u542b\u672a\u5f00\u59cb / \u5df2\u5230\u671f',
  relayout: '\u91cd\u65b0\u5e03\u5c40',
  create: '\u521b\u5efa\u5173\u7cfb',
  edit: '\u7f16\u8f91\u5173\u7cfb',
  cancel: '\u53d6\u6d88',
  save: '\u4fdd\u5b58',
  relationType: '\u5173\u7cfb\u7c7b\u578b',
  strength: '\u5f3a\u5ea6',
  visibility: '\u53ef\u89c1\u6027',
  startsAt: '\u5f00\u59cb\u65f6\u95f4',
  expiresAt: '\u5230\u671f\u65f6\u95f4',
  noExpiry: '\u7559\u7a7a\u8868\u793a\u4e0d\u5230\u671f',
  note: '\u5907\u6ce8',
  dimensions: '\u591a\u7ef4\u5173\u7cfb\u503c',
  metadata: 'Metadata JSON',
  directed: '\u6709\u5411',
  undirected: '\u65e0\u5411',
  public: '\u516c\u5f00',
  limited: '\u6709\u9650\u516c\u5f00',
  private: '\u79c1\u6709',
  secret: '\u79d8\u5bc6',
  active: '\u6d3b\u8dc3',
  inactive: '\u672a\u6fc0\u6d3b / \u5df2\u5230\u671f',
  saved: '\u5173\u7cfb\u5df2\u4fdd\u5b58',
  deleted: '\u5173\u7cfb\u5df2\u5220\u9664',
  hint: '\u62d6\u52a8\u8fde\u7ebf\u521b\u5efa\uff1b\u53cc\u51fb\u8fb9\u7f16\u8f91\uff1b\u9009\u4e2d\u8fb9\u540e\u6309 Delete \u5220\u9664\u3002',
  details: '\u5b9e\u4f53\u8be6\u60c5',
  close: '\u5173\u95ed',
  noRelations: '\u6682\u65e0\u5173\u7cfb',
}

const TYPE_COLORS = {
  location: { border: '#4a6fa5', label: '\u5730\u70b9' },
  institution: { border: '#9a3b3b', label: '\u673a\u6784' },
  organization: { border: '#3d6b4a', label: '\u7ec4\u7ec7' },
  person: { border: '#c9a84c', label: '\u4eba\u7269' },
  object: { border: '#6b6b6b', label: '\u7269\u54c1' },
}

function layout(nodes, edges) {
  const graph = new dagre.graphlib.Graph()
  graph.setDefaultEdgeLabel(() => ({}))
  graph.setGraph({ rankdir: 'TB', nodesep: 60, ranksep: 100 })
  nodes.forEach(node => graph.setNode(node.id, { width: NODE_W, height: NODE_H }))
  edges.forEach(edge => graph.setEdge(edge.source, edge.target))
  dagre.layout(graph)
  return nodes.map(node => {
    const point = graph.node(node.id) || { x: 0, y: 0 }
    return {
      ...node,
      position: { x: point.x - NODE_W / 2, y: point.y - NODE_H / 2 },
      targetPosition: 'top',
      sourcePosition: 'bottom',
    }
  })
}

function buildNode(entity) {
  const color = TYPE_COLORS[entity.type]?.border || '#6b6b6b'
  return {
    id: entity.id,
    data: { label: entity.name, entityType: entity.type },
    position: { x: 0, y: 0 },
    style: {
      background: '#1f1812', color: '#eadfc2', border: `1px solid ${color}`,
      borderRadius: 6, fontSize: 13, width: NODE_W, height: NODE_H,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    },
  }
}

function typeMap(types) {
  return Object.fromEntries(types.map(type => [type.relationship_type_id, type]))
}

function styleFor(type) {
  const style = type?.style || {}
  return {
    label: style.label || type?.display_name || type?.relationship_type_id || '?',
    color: style.color || '#7d8da6',
    dashed: Boolean(style.dashed),
    marker: style.marker || (type?.direction === 'undirected' ? 'arrow' : 'closed'),
  }
}

function edgeId(relation) {
  return JSON.stringify([relation.parent_id, relation.child_id, relation.rel_type])
}

function buildEdge(relation, definitions) {
  const type = definitions[relation.rel_type]
  const style = styleFor(type)
  const markerType = style.marker === 'closed' ? MarkerType.ArrowClosed : MarkerType.Arrow
  const marker = { type: markerType, color: style.color, width: 18, height: 18 }
  const inactive = relation.active === false
  return {
    id: edgeId(relation),
    source: relation.parent_id,
    target: relation.child_id,
    label: `${style.label} ${Number(relation.strength ?? type?.default_strength ?? 0).toLocaleString()}${inactive ? ` (${T.inactive})` : ''}`,
    labelStyle: { fill: style.color, fontSize: 11, fontWeight: 'bold' },
    labelBgStyle: { fill: '#1a1510', fillOpacity: 0.88 },
    labelBgPadding: [2, 4],
    style: {
      stroke: style.color,
      strokeWidth: 2,
      strokeDasharray: style.dashed ? '6 4' : undefined,
      opacity: inactive ? 0.38 : 1,
    },
    markerStart: type?.direction === 'undirected' ? marker : undefined,
    markerEnd: marker,
    data: { relation, type },
  }
}

function apiError(data, fallback) {
  const detail = data?.detail
  if (typeof detail === 'string') return detail
  if (detail?.message) return `${detail.code || 'relationship.invalid'}: ${detail.message}`
  return data?.error || fallback
}

function RelationDialog({ initial, relationshipTypes, onCancel, onSave, busy }) {
  const initialType = relationshipTypes.find(type => type.relationship_type_id === initial.rel_type) || relationshipTypes[0]
  const [form, setForm] = useState(() => ({
    parent_id: initial.parent_id || '',
    child_id: initial.child_id || '',
    rel_type: initial.rel_type || initialType?.relationship_type_id || '',
    strength: initial.strength ?? initialType?.default_strength ?? 0,
    visibility: initial.visibility || initialType?.default_visibility || 'public',
    starts_at: initial.starts_at ?? 0,
    expires_at: initial.expires_at ?? '',
    values: { ...Object.fromEntries((initialType?.dimensions || []).map(item => [item.dimension_id, item.default ?? 0])), ...(initial.values || {}) },
    note: initial.note || '',
    metadata_text: JSON.stringify(initial.metadata || {}, null, 2),
  }))
  const [error, setError] = useState('')
  const definition = relationshipTypes.find(type => type.relationship_type_id === form.rel_type) || initialType
  const set = (key, value) => setForm(current => ({ ...current, [key]: value }))
  const changeType = typeId => {
    const next = relationshipTypes.find(type => type.relationship_type_id === typeId)
    setForm(current => ({
      ...current,
      rel_type: typeId,
      strength: next?.default_strength ?? 0,
      visibility: next?.default_visibility || 'public',
      expires_at: next?.allow_expiry ? current.expires_at : '',
      values: Object.fromEntries((next?.dimensions || []).map(item => [item.dimension_id, item.default ?? 0])),
    }))
  }
  const submit = async () => {
    try {
      const metadata = JSON.parse(form.metadata_text || '{}')
      if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) throw new Error('Metadata must be a JSON object.')
      const payload = {
        parent_id: form.parent_id,
        child_id: form.child_id,
        rel_type: form.rel_type,
        strength: Number(form.strength),
        visibility: form.visibility,
        starts_at: Number(form.starts_at || 0),
        expires_at: form.expires_at === '' ? null : Number(form.expires_at),
        values: form.values,
        note: form.note,
        metadata,
      }
      if (!Number.isFinite(payload.strength) || !Number.isFinite(payload.starts_at)) throw new Error('Strength and starts_at must be finite numbers.')
      if (payload.expires_at !== null && (!Number.isFinite(payload.expires_at) || payload.expires_at <= payload.starts_at)) throw new Error('expires_at must be later than starts_at.')
      setError('')
      await onSave(payload)
    } catch (caught) { setError(caught.message) }
  }
  return <div className="rv-modal-overlay" onClick={onCancel}>
    <div className="rv-modal rv-relation-dialog" onClick={event => event.stopPropagation()}>
      <h3>{initial._editing ? T.edit : T.create}</h3>
      <div className="rv-dialog-endpoints"><code>{form.parent_id}</code><span>{definition?.direction === 'undirected' ? '\u2194' : '\u2192'}</span><code>{form.child_id}</code></div>
      <label>{T.relationType}<select value={form.rel_type} onChange={event => changeType(event.target.value)}>{relationshipTypes.map(type => <option key={type.relationship_type_id} value={type.relationship_type_id}>{type.display_name} ({type.relationship_type_id})</option>)}</select><small>{definition?.description}</small></label>
      <div className="rv-dialog-grid three"><label>{T.strength}<input type="number" step="any" min={definition?.minimum_strength} max={definition?.maximum_strength} value={form.strength} onChange={event => set('strength', event.target.value)} /><small>{definition?.minimum_strength} ~ {definition?.maximum_strength}</small></label><label>{T.visibility}<select value={form.visibility} onChange={event => set('visibility', event.target.value)}>{['public', 'limited', 'private', 'secret'].map(value => <option key={value} value={value}>{T[value]}</option>)}</select></label><label>{definition?.direction === 'undirected' ? T.undirected : T.directed}<input disabled value={definition?.direction || 'directed'} /></label></div>
      <div className="rv-dialog-grid"><label>{T.startsAt}<input type="number" step="any" value={form.starts_at} onChange={event => set('starts_at', event.target.value)} /></label><label>{T.expiresAt}<input type="number" step="any" disabled={!definition?.allow_expiry} value={form.expires_at} onChange={event => set('expires_at', event.target.value)} placeholder={definition?.allow_expiry ? T.noExpiry : '-'} /></label></div>
      {!!definition?.dimensions?.length && <fieldset className="rv-dimensions"><legend>{T.dimensions}</legend>{definition.dimensions.map(item => <label key={item.dimension_id}>{item.display_name || item.dimension_id}<input type="number" step="any" min={item.minimum} max={item.maximum} value={form.values[item.dimension_id] ?? item.default ?? 0} onChange={event => set('values', { ...form.values, [item.dimension_id]: Number(event.target.value) })} /><small>{item.dimension_id}: {item.minimum} ~ {item.maximum}</small></label>)}</fieldset>}
      <label>{T.note}<textarea rows="3" value={form.note} onChange={event => set('note', event.target.value)} /></label>
      <details><summary>{T.metadata}</summary><textarea className="rv-json" rows="5" spellCheck="false" value={form.metadata_text} onChange={event => set('metadata_text', event.target.value)} /></details>
      {error && <div className="rv-dialog-error">{error}</div>}
      <div className="rv-modal-buttons"><button className="btn ghost" onClick={onCancel}>{T.cancel}</button><button className="btn primary" disabled={busy || !form.rel_type} onClick={submit}>{T.save}</button></div>
    </div>
  </div>
}

export default function RelationsView() {
  const [entities, setEntities] = useState([])
  const [relations, setRelations] = useState([])
  const [relationshipTypes, setRelationshipTypes] = useState([])
  const [nodes, setNodes, onNodesChange] = useNodesState([])
  const [edges, setEdges, onEdgesChange] = useEdgesState([])
  const [filter, setFilter] = useState('all')
  const [includeInactive, setIncludeInactive] = useState(false)
  const [selectedNode, setSelectedNode] = useState(null)
  const [nodeDetail, setNodeDetail] = useState(null)
  const [dialog, setDialog] = useState(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const didLayout = useRef(false)
  const definitions = useMemo(() => typeMap(relationshipTypes), [relationshipTypes])
  const nameMap = useMemo(() => Object.fromEntries(entities.map(entity => [entity.id, entity.name])), [entities])
  const visibleRelations = useMemo(() => relations.filter(relation => (filter === 'all' || relation.rel_type === filter) && (includeInactive || relation.active !== false)), [relations, filter, includeInactive])
  const selectedRelations = useMemo(() => relations.filter(relation => relation.parent_id === selectedNode || relation.child_id === selectedNode), [relations, selectedNode])
  const flash = useCallback(text => { setMessage(text); window.setTimeout(() => setMessage(''), 2400) }, [])

  useEffect(() => {
    let active = true
    fetch(`${API_BASE}/relations`).then(response => response.json()).then(data => {
      if (!active) return
      if (data.error || data.detail) throw new Error(apiError(data, 'Failed to load relationships.'))
      setEntities(data.entities || [])
      setRelations(data.relations || [])
      setRelationshipTypes(data.relationship_types || [])
    }).catch(error => active && flash(error.message))
    return () => { active = false }
  }, [flash])

  useEffect(() => {
    if (didLayout.current || !entities.length) return
    const rawNodes = entities.map(buildNode)
    const rawEdges = relations.map(relation => buildEdge(relation, definitions))
    setNodes(layout(rawNodes, rawEdges))
    didLayout.current = true
  }, [definitions, entities, relations, setNodes])

  useEffect(() => {
    setEdges(visibleRelations.map(relation => buildEdge(relation, definitions)))
  }, [definitions, setEdges, visibleRelations])

  const relayout = useCallback(() => {
    const rawNodes = entities.map(buildNode)
    const rawEdges = relations.map(relation => buildEdge(relation, definitions))
    setNodes(layout(rawNodes, rawEdges))
  }, [definitions, entities, relations, setNodes])

  const postRelation = async payload => {
    const response = await fetch(`${API_BASE}/relations`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
    const data = await response.json()
    if (!response.ok || data.error) throw new Error(apiError(data, 'Failed to save relationship.'))
    return data.relation
  }
  const deleteRelation = async relation => {
    const query = new URLSearchParams({ parent_id: relation.parent_id, child_id: relation.child_id, rel_type: relation.rel_type })
    const response = await fetch(`${API_BASE}/relations?${query}`, { method: 'DELETE' })
    const data = await response.json()
    if (!response.ok || data.error) throw new Error(apiError(data, 'Failed to delete relationship.'))
  }

  const saveDialog = async payload => {
    setBusy(true)
    try {
      const saved = await postRelation(payload)
      if (dialog?._editing && dialog.rel_type !== saved.rel_type) await deleteRelation(dialog)
      setRelations(current => [...current.filter(item => edgeId(item) !== edgeId(dialog || payload) && edgeId(item) !== edgeId(saved)), saved])
      setDialog(null)
      flash(T.saved)
    } catch (error) { flash(error.message) } finally { setBusy(false) }
  }
  const onConnect = useCallback(connection => {
    if (!connection.source || !connection.target || connection.source === connection.target) return
    setDialog({ parent_id: connection.source, child_id: connection.target })
  }, [])
  const onEdgesDelete = useCallback(async deletedEdges => {
    for (const edge of deletedEdges) {
      const relation = edge.data?.relation
      if (!relation) continue
      try {
        await deleteRelation(relation)
        setRelations(current => current.filter(item => edgeId(item) !== edgeId(relation)))
        flash(T.deleted)
      } catch (error) { flash(error.message) }
    }
  }, [flash])
  const onEdgeDoubleClick = useCallback((_event, edge) => setDialog({ ...edge.data.relation, _editing: true }), [])
  const onNodeClick = useCallback(async (_event, node) => {
    setSelectedNode(node.id)
    try {
      const response = await fetch(`${API_BASE}/world-state/${encodeURIComponent(node.id)}`)
      const data = await response.json()
      setNodeDetail(data.error ? null : data)
    } catch { setNodeDetail(null) }
  }, [])

  return <div className="rv-container">
    <div className="rv-toolbar">
      <div className="rv-filter rv-dynamic-filter">
        <button className={`btn ${filter === 'all' ? 'active' : 'ghost'}`} onClick={() => setFilter('all')}>{T.all}</button>
        {relationshipTypes.map(type => <button key={type.relationship_type_id} className={`btn ${filter === type.relationship_type_id ? 'active' : 'ghost'}`} style={{ '--rel-color': styleFor(type).color }} onClick={() => setFilter(type.relationship_type_id)}>{type.display_name}</button>)}
        <label className="rv-inactive-toggle"><input type="checkbox" checked={includeInactive} onChange={event => setIncludeInactive(event.target.checked)} />{includeInactive ? T.includeInactive : T.activeOnly}</label>
        <button className="btn ghost" onClick={relayout}>{T.relayout}</button>
      </div>
      <div className="rv-legend">
        {relationshipTypes.map(type => { const style = styleFor(type); return <span key={type.relationship_type_id} className="rv-legend-item"><i className={style.dashed ? 'rv-edge-dashed' : 'rv-edge-solid'} style={{ background: style.color }} />{type.display_name}<small>{type.direction === 'undirected' ? T.undirected : T.directed}</small></span> })}
      </div>
      <span className="rv-hint">{T.hint}</span>{message && <span className="rv-msg">{message}</span>}
    </div>
    <div className="rv-main">
      <div className="rv-graph"><ReactFlow nodes={nodes} edges={edges} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onConnect={onConnect} onEdgesDelete={onEdgesDelete} onEdgeDoubleClick={onEdgeDoubleClick} onNodeClick={onNodeClick} fitView fitViewOptions={{ padding: 0.2 }} deleteKeyCode={['Delete', 'Backspace']} nodesDeletable={false}><Background color="#3a2e20" gap={24} /><Controls showInteractive={false} /></ReactFlow></div>
      {selectedNode && <aside className="rv-detail"><div className="rv-detail-head"><h3>{nodeDetail?.entity?.name || nameMap[selectedNode] || selectedNode}</h3><button onClick={() => { setSelectedNode(null); setNodeDetail(null) }}>{T.close}</button></div><code>{selectedNode}</code>{nodeDetail?.entity && <><p>{nodeDetail.entity.description || nodeDetail.entity.current_status}</p><div className="rv-detail-stats"><span>{TYPE_COLORS[nodeDetail.entity.type]?.label || nodeDetail.entity.type}</span><span>v{nodeDetail.entity.version}</span></div></>}<h4>{T.details}</h4>{selectedRelations.length ? <div className="rv-relation-list">{selectedRelations.map(relation => { const type = definitions[relation.rel_type]; const other = relation.parent_id === selectedNode ? relation.child_id : relation.parent_id; return <button key={edgeId(relation)} onClick={() => setDialog({ ...relation, _editing: true })}><strong>{type?.display_name || relation.rel_type}</strong><span>{nameMap[other] || other}</span><small>{relation.active === false ? T.inactive : T.active} / {T[relation.visibility] || relation.visibility} / {relation.strength}</small></button> })}</div> : <p className="rv-empty">{T.noRelations}</p>}</aside>}
    </div>
    {dialog && <RelationDialog key={`${dialog.parent_id}:${dialog.child_id}:${dialog.rel_type || 'new'}`} initial={dialog} relationshipTypes={relationshipTypes} onCancel={() => setDialog(null)} onSave={saveDialog} busy={busy} />}
  </div>
}
