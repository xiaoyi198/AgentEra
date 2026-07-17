import { useCallback, useEffect, useRef, useState } from 'react'
import { useWebSocket } from './hooks/useWebSocket'
import GodView from './GodView'
import WorldStateView from './WorldStateView'
import RelationsView from './RelationsView'
import SettingsCenter from './SettingsCenter'
import ObjectivePanel from './ObjectivePanel'
import DerivedMetricPanel from './DerivedMetricPanel'
import OutcomeFeedbackPanel from './OutcomeFeedbackPanel'
import CapabilityPanel from './CapabilityPanel'
import { normalizeCapabilityResponse } from './CapabilityTools.js'
import { normalizeDerivedMetricResponse } from './DerivedMetricTools.js'
import {
  mergeOutcomeFeedbackState, normalizeOutcomeFeedbackResponse,
} from './OutcomeFeedbackTools.js'
import {
  activeTemporalCycles, formatAutoPauseNotice, formatCycleProgress,
  formatSeason, formatTemporalDate, normalizeTimeSystemState,
} from './TemporalStateTools.js'
import { normalizeUIManifestResponse, visibleNavigation, visibleWidgets } from './UIManifestTools.js'
import './App.css'

const WS_URL = `ws://${window.location.host}/ws`
const API_BASE = '/api'

const GSD = 86400 // 1 game day = 86400 game seconds
const SHICHEN = ['子', '丑', '寅', '卯', '辰', '巳', '午', '未', '申', '酉', '戌', '亥']

function formatSimTime(simTime) {
  const day = Math.floor(simTime / GSD)
  const rem = simTime % GSD
  const hour = Math.floor(rem / 3600)
  const minute = Math.floor((rem % 3600) / 60)
  const shichenIdx = Math.floor(hour / 2) % 12
  const shichen = SHICHEN[shichenIdx]
  const ke = Math.floor((hour % 2) * 4 + minute / 15)
  return { day, hour, minute, shichen, ke }
}

function timeStr(simTime) {
  const t = formatSimTime(simTime)
  return `第${t.day}天 ${t.shichen}时${t.ke > 0 ? t.ke + '刻' : ''}`
}

function timeStrFull(simTime) {
  const t = formatSimTime(simTime)
  const hh = String(t.hour).padStart(2, '0')
  const mm = String(t.minute).padStart(2, '0')
  return `第${t.day}天 ${t.shichen}时${t.ke > 0 ? t.ke + '刻' : ''} (${hh}:${mm})`
}

function remainingStr(gameSeconds) {
  if (gameSeconds < 60) return '即将送达'
  const days = Math.floor(gameSeconds / GSD)
  const hours = Math.floor((gameSeconds % GSD) / 3600)
  const mins = Math.floor((gameSeconds % 3600) / 60)
  const parts = []
  if (days > 0) parts.push(`${days}天`)
  if (hours > 0) parts.push(`${hours}时辰`)
  if (mins > 0 && days === 0) parts.push(`${mins}刻钟`)
  return parts.join('') + '后送达'
}

function SaveManagerModal({ onClose, onLoaded }) {
  const [saves, setSaves] = useState([])
  const [loading, setLoading] = useState(true)
  const [renamingKey, setRenamingKey] = useState(null)
  const [renameVal, setRenameVal] = useState('')
  const [busy, setBusy] = useState(false)

  const reload = async () => {
    setLoading(true)
    try {
      const res = await fetch(`${API_BASE}/saves`)
      setSaves(await res.json())
    } catch { setSaves([]) }
    setLoading(false)
  }

  useEffect(() => { reload() }, [])

  const handleLoad = async (saveName) => {
    const meta = saves.find(s => s.save_name === saveName)
    const day = meta?.sim_day ?? '?'
    if (!window.confirm(`读档「${saveName}」（第${day}天）？当前进度将丢失。`)) return
    setBusy(true)
    try {
      const res = await fetch(`${API_BASE}/load`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ save_name: saveName }),
      })
      const data = await res.json()
      if (data.error) { alert('读档失败：' + data.error); return }
      onLoaded(data)
      onClose()
    } finally { setBusy(false) }
  }

  const handleDelete = async (saveName) => {
    if (!window.confirm(`确定删除存档「${saveName}」？此操作不可恢复。`)) return
    setBusy(true)
    try {
      await fetch(`${API_BASE}/saves/${encodeURIComponent(saveName)}`, { method: 'DELETE' })
      await reload()
    } finally { setBusy(false) }
  }

  const handleRename = async (oldName) => {
    const newName = renameVal.trim()
    if (!newName || newName === oldName) { setRenamingKey(null); return }
    setBusy(true)
    try {
      const res = await fetch(`${API_BASE}/saves/rename`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ old_name: oldName, new_name: newName }),
      })
      const data = await res.json()
      if (data.error) { alert('重命名失败：' + data.error); return }
      setRenamingKey(null)
      await reload()
    } finally { setBusy(false) }
  }

  const fmtDate = (ts) => ts ? new Date(ts * 1000).toLocaleString('zh-CN') : '—'

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal save-manager" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h2>存档管理</h2>
          <button className="modal-close" onClick={onClose}>&times;</button>
        </div>
        <div className="modal-body">
          {loading ? (
            <div className="empty">加载中...</div>
          ) : saves.length === 0 ? (
            <div className="empty">暂无存档</div>
          ) : saves.map(s => (
            <div key={s.save_name} className="save-item">
              {renamingKey === s.save_name ? (
                <div className="save-rename-row">
                  <input
                    autoFocus
                    value={renameVal}
                    onChange={e => setRenameVal(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter') handleRename(s.save_name); if (e.key === 'Escape') setRenamingKey(null) }}
                  />
                  <button className="btn primary" disabled={busy} onClick={() => handleRename(s.save_name)}>确认</button>
                  <button className="btn ghost" onClick={() => setRenamingKey(null)}>取消</button>
                </div>
              ) : (
                <>
                  <div className="save-info">
                    <span className="save-name">{s.save_name}</span>
                    <span className="save-meta">第{s.sim_day}天 · {fmtDate(s.saved_at)}</span>
                    <span className="save-meta">{s.agent_count ?? '?'}个Agent · {s.timer_count ?? '?'}个定时器</span>
                  </div>
                  <div className="save-actions">
                    <button className="btn primary" disabled={busy} onClick={() => handleLoad(s.save_name)}>读档</button>
                    <button className="btn ghost" disabled={busy} onClick={() => { setRenamingKey(s.save_name); setRenameVal(s.save_name) }}>重命名</button>
                    <button className="btn danger" disabled={busy} onClick={() => handleDelete(s.save_name)}>删除</button>
                  </div>
                </>
              )}
            </div>
          ))}
        </div>
        <div className="modal-footer">
          <button className="btn ghost" onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  )
}

function App() {
  const { connected, lastMessage } = useWebSocket(WS_URL)

  const [issues, setIssues] = useState([])
  const [decisionRequests, setDecisionRequests] = useState([])
  const [gameplay, setGameplay] = useState({
    configured: false, package_id: '', package_name: '', player: null,
  })
  const [selectedIssue, setSelectedIssue] = useState(null)
  const [inTransit, setInTransit] = useState([])
  const [simTime, setSimTime] = useState(0)
  const [running, setRunning] = useState(false)
  const [paused, setPaused] = useState(false)
  const [currentView, setCurrentView] = useState('dashboard')
  const [tokenUsage, setTokenUsage] = useState({})
  const [capabilityState, setCapabilityState] = useState(() => normalizeCapabilityResponse(null))
  const [noteText, setNoteText] = useState('')
  const [secondsPerDay, setSecondsPerDay] = useState(600)
  const [showSaveManager, setShowSaveManager] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [objectiveState, setObjectiveState] = useState({
    configured: false, sandbox: true, generation: 0, status: 'sandbox',
    objectives: [], endings: [], current_ending: null, recent_transitions: [],
  })
  const [derivedMetricState, setDerivedMetricState] = useState(() => normalizeDerivedMetricResponse(null))
  const [outcomeFeedbackState, setOutcomeFeedbackState] = useState(() => normalizeOutcomeFeedbackResponse(null))
  const [uiManifest, setUIManifest] = useState(() => normalizeUIManifestResponse(null))
  const [timeSystemState, setTimeSystemState] = useState(() => normalizeTimeSystemState(null))
  const [autoPauseNotice, setAutoPauseNotice] = useState(null)
  const objectiveRefreshAt = useRef(0)
  const derivedMetricRefreshAt = useRef(0)

  const refreshObjectives = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/objectives`)
      const data = await response.json()
      if (data && typeof data === 'object') {
        setObjectiveState(data)
        objectiveRefreshAt.current = Date.now()
      }
    } catch { /* \u4fdd\u7559 tick \u63a8\u9001\u7684\u4e0a\u4e00\u4efd\u76ee\u6807\u5feb\u7167 */ }
  }, [])

  const refreshUIManifest = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/ui-manifest`)
      if (!response.ok) return
      setUIManifest(normalizeUIManifestResponse(await response.json()))
    } catch { /* \u4fdd\u7559\u4e0a\u4e00\u4efd\u754c\u9762\u914d\u7f6e */ }
  }, [])
  const refreshCapabilities = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/capabilities?audience=player`)
      if (!response.ok) return
      setCapabilityState(normalizeCapabilityResponse(await response.json()))
    } catch { /* capability registry remains empty while backend is unavailable */ }
  }, [])

  const refreshTimeSystem = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/time-system`)
      if (!response.ok) return
      const normalized = normalizeTimeSystemState(await response.json())
      setTimeSystemState(normalized)
      if (normalized.auto_pause) setAutoPauseNotice(formatAutoPauseNotice(normalized.auto_pause))
    } catch { /* keep the last capability-safe calendar snapshot */ }
  }, [])

  const refreshDerivedMetrics = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/derived-metrics`)
      if (!response.ok) return
      setDerivedMetricState(normalizeDerivedMetricResponse(await response.json()))
      derivedMetricRefreshAt.current = Date.now()
    } catch { /* keep the last generation-bound safe projection */ }
  }, [])

  const refreshOutcomeFeedback = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/outcome-feedback`)
      if (!response.ok) return
      setOutcomeFeedbackState(normalizeOutcomeFeedbackResponse(await response.json()))
    } catch { /* keep the last generation-bound player-safe feedback projection */ }
  }, [])


  // WebSocket messages are external events; mirroring a completed tick into view state is intentional.
  useEffect(() => {
    if (!lastMessage || lastMessage.type !== 'tick') return
    const data = lastMessage.data
    // eslint-disable-next-line react-hooks/set-state-in-effect -- mirror an external WebSocket event into view state.
    setSimTime(data.sim_time)
    setInTransit(data.in_transit || [])
    setTokenUsage(data.token_usage || {})
    if (data.time_system) setTimeSystemState(normalizeTimeSystemState(data.time_system))
    if (data.auto_pause) {
      setAutoPauseNotice(formatAutoPauseNotice(data.auto_pause))
      setPaused(true)
    }
    if (data.objective_transitions?.length > 0 || Date.now() - objectiveRefreshAt.current > 5000) refreshObjectives()
    if (Date.now() - derivedMetricRefreshAt.current > 5000) refreshDerivedMetrics()
    if (data.outcome_feedback) {
      setOutcomeFeedbackState(previous => mergeOutcomeFeedbackState(previous, data.outcome_feedback))
    }
    if (data.new_issues?.length > 0) {
      setIssues(prev => {
        const existingIds = new Set(prev.map(item => item.issue_id))
        const fresh = data.new_issues.filter(item => !existingIds.has(item.issue_id))
        return fresh.length > 0 ? [...fresh, ...prev] : prev
      })
    }
    if (data.new_decision_requests?.length > 0) {
      setDecisionRequests(prev => {
        const existingIds = new Set(prev.map(item => item.decision_request_id))
        const fresh = data.new_decision_requests.filter(item => !existingIds.has(item.decision_request_id))
        return fresh.length > 0 ? [...fresh, ...prev] : prev
      })
    }
  }, [lastMessage, refreshDerivedMetrics, refreshObjectives])

  // Issue / Case / DecisionRequest 是题材无关的玩家事项权威状态。
  useEffect(() => {
    Promise.all([
      fetch(`${API_BASE}/issues`).then(r => r.json()),
      fetch(`${API_BASE}/decision-requests`).then(r => r.json()),
    ]).then(([issueData, requestData]) => {
      if (Array.isArray(issueData)) setIssues(issueData)
      if (Array.isArray(requestData)) setDecisionRequests(requestData)
    }).catch(() => {})
  }, [])


  useEffect(() => {
    fetch(`${API_BASE}/gameplay`)
      .then(r => r.json())
      .then(data => {
        if (data && typeof data === 'object') setGameplay(data)
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetches an external generation-bound registry snapshot.
    refreshCapabilities()
  }, [objectiveState.generation, refreshCapabilities])

  useEffect(() => {
    const initial = window.setTimeout(refreshTimeSystem, 0)
    return () => window.clearTimeout(initial)
  }, [objectiveState.generation, refreshTimeSystem])

  useEffect(() => {
    const initial = window.setTimeout(refreshDerivedMetrics, 0)
    const timer = window.setInterval(refreshDerivedMetrics, 5000)
    return () => { window.clearTimeout(initial); window.clearInterval(timer) }
  }, [objectiveState.generation, refreshDerivedMetrics])

  useEffect(() => {
    const initial = window.setTimeout(refreshOutcomeFeedback, 0)
    return () => window.clearTimeout(initial)
  }, [objectiveState.generation, refreshOutcomeFeedback])

  useEffect(() => {
    let active = true
    fetch(`${API_BASE}/ui-manifest`)
      .then(response => response.ok ? response.json() : Promise.reject(new Error(`HTTP ${response.status}`)))
      .then(data => { if (active) setUIManifest(normalizeUIManifestResponse(data)) })
      .catch(() => {})
    return () => { active = false }
  }, [objectiveState.generation])

  const apiPost = async (path, body = {}) => {
    try {
      const res = await fetch(`${API_BASE}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      return await res.json()
    } catch (e) {
      console.error(`API ${path} failed:`, e)
      return { error: e.message }
    }
  }

  const handleStart = async () => {
    const res = await apiPost('/start')
    if (!res.error) { setRunning(true); setPaused(false) }
  }
  const handlePause = async () => { await apiPost('/pause'); setPaused(true) }
  const handleResume = async () => { await apiPost('/resume'); setPaused(false); setAutoPauseNotice(null) }

  const handleSave = async () => {
    const simDay = Math.floor(simTime / 86400)
    const defaultName = `第${simDay}天-${Math.floor(Math.random() * 9000) + 1000}`
    const saveName = window.prompt('存档名称：', defaultName)
    if (saveName === null) return  // 取消

    // 检查是否已存在同名存档
    try {
      const existing = await fetch(`${API_BASE}/saves`).then(r => r.json())
      const dup = existing.find(s => s.save_name === saveName.trim())
      if (dup) {
        if (!window.confirm(`存档「${saveName.trim()}」已存在，确定覆盖？`)) return
      }
    } catch { /* 检查失败则继续 */ }

    const res = await apiPost('/save', { save_name: saveName.trim() })
    if (res.error) {
      alert('存档失败：' + res.error)
    } else {
      alert(`存档成功：${res.save_name}（第${res.sim_day}天）`)
    }
  }

  const handleLoaded = (data) => {
    setRunning(false)
    setPaused(false)
    setSimTime(data.sim_time)
    setSecondsPerDay(data.seconds_per_day)
    setIssues([])
    setDecisionRequests([])
    setSelectedIssue(null)
    setAutoPauseNotice(null)
    window.setTimeout(() => {
      refreshObjectives()
      refreshUIManifest()
      refreshCapabilities()
      refreshDerivedMetrics()
      refreshOutcomeFeedback()
      refreshTimeSystem()
      Promise.all([fetch(`${API_BASE}/issues`).then(r => r.json()), fetch(`${API_BASE}/decision-requests`).then(r => r.json())])
        .then(([issueData, requestData]) => { if (Array.isArray(issueData)) setIssues(issueData); if (Array.isArray(requestData)) setDecisionRequests(requestData) })
        .catch(() => {})
    }, 0)
    setInTransit([])
    alert(`读档成功：${data.save_name}（第${Math.floor(data.sim_time / 86400)}天）`)
  }

  const handleDecision = async (request, option) => {
    const decisionId = request.decision_request_id
    const response = await apiPost(`/decision-requests/${encodeURIComponent(request.decision_request_id)}/resolve`, {
      decision_id: decisionId,
      option_id: option.option_id,
      expected_generation: request.generation,
      note: noteText,
      invocation: {},
    })
    if (response.error || response.detail) {
      alert('决策失败：' + (response.error || response.detail?.message || response.detail?.code || 'unknown error'))
      return
    }
    setNoteText('')
    setIssues(prev => prev.map(item => item.issue_id === response.issue?.issue_id ? response.issue : item))
    setDecisionRequests(prev => prev.map(item => item.decision_request_id === response.decision_request?.decision_request_id ? response.decision_request : item))
    setSelectedIssue(response.issue || selectedIssue)
    if (response.outcome_feedback) {
      setOutcomeFeedbackState(previous => mergeOutcomeFeedbackState(previous, response.outcome_feedback))
    }
    refreshOutcomeFeedback()
    refreshObjectives()
  }

  const handleCapabilityInvoke = async (capabilityId, payload) => {
    const response = await apiPost(`/capabilities/${encodeURIComponent(capabilityId)}/invoke`, payload)
    if (response?.ok) refreshObjectives()
    return response
  }

  const handleTimeScale = async (value) => {
    setSecondsPerDay(value)
    await apiPost('/time-scale', { seconds_per_day: value })
  }

  const openSettings = () => setShowSettings(true)

  const controlledObjects = gameplay.player?.controlled_objects || []
  const terms = uiManifest.terminology
  const playerDisplayName = gameplay.player?.display_name || terms.player
  const worldTitle = uiManifest.title || gameplay.package_name || '\u4e16\u754c\u6a21\u62df\u5668'
  const worldSubtitle = uiManifest.subtitle || (gameplay.configured
    ? `${playerDisplayName} / ${controlledObjects.length} \u4e2a\u53ef\u63a7\u5bf9\u8c61`
    : 'DLC \u6e38\u620f\u5951\u7ea6\u672a\u914d\u7f6e')
  const mainNavigation = visibleNavigation(uiManifest, 'main')
  const activeView = mainNavigation.some(item => item.view === currentView)
    ? currentView
    : (mainNavigation[0]?.view || 'dashboard')
  const listFields = new Set(uiManifest.issue_page.list_fields)
  const detailFields = new Set(uiManifest.issue_page.detail_fields)
  const visibleIssues = uiManifest.issue_page.show_resolved
    ? issues
    : issues.filter(item => !['resolved', 'closed', 'dismissed'].includes(item.status))
  const pendingIssueCount = issues.filter(item => !['resolved', 'closed', 'dismissed'].includes(item.status)).length

  const issueSource = (item) => item.source?.display_name || item.source?.id || item.source?.kind || `\u672a\u77e5${terms.source}`
  const listMeta = (item) => [
    listFields.has('source') ? issueSource(item) : '',
    listFields.has('urgency') ? (item.urgency || 'normal') : '',
    listFields.has('created_at') ? timeStr(item.created_at || 0) : '',
    listFields.has('issue_type') ? item.issue_type_id : '',
    listFields.has('case_id') ? item.case_id : '',
  ].filter(Boolean).join(' \u00b7 ')

  const renderDashboardWidget = (widget) => {
    if (widget.kind === 'objectives') {
      return <ObjectivePanel key={widget.widget_id} state={objectiveState} onRefresh={refreshObjectives} />
    }
    if (widget.kind === 'derived_metrics') {
      return <DerivedMetricPanel key={widget.widget_id} state={derivedMetricState} onRefresh={refreshDerivedMetrics} title={terms.metrics || '派生指标'} />
    }
    if (widget.kind === 'outcome_feedback') {
      return (
        <OutcomeFeedbackPanel
          key={widget.widget_id}
          state={outcomeFeedbackState}
          onRefresh={refreshOutcomeFeedback}
        />
      )
    }
    if (widget.kind === 'issue_list') {
      return (
        <aside key={widget.widget_id} className="panel memorial-list">
          <h2>{terms.issue_plural} ({pendingIssueCount})</h2>
          {visibleIssues.length === 0 && <div className="empty">当前没有待处理的{terms.issue}...</div>}
          {visibleIssues.map(item => (
            <div
              key={item.issue_id}
              className={`item ${selectedIssue?.issue_id === item.issue_id ? 'selected' : ''} ${['resolved', 'closed', 'dismissed'].includes(item.status) ? 'handled' : ''}`}
              onClick={() => setSelectedIssue(item)}
            >
              {listFields.has('title') && <div className="item-title">{item.title || item.summary?.slice(0, 30) || `(\u672a\u547d\u540d${terms.issue})`}</div>}
              {listMeta(item) && <div className="item-meta">{listMeta(item)}</div>}
              {listFields.has('status') && <span className={`badge ${item.status}`}>{item.status}</span>}
            </div>
          ))}
        </aside>
      )
    }
    if (widget.kind === 'issue_detail') {
      const pendingRequests = selectedIssue
        ? decisionRequests.filter(request => request.issue_id === selectedIssue.issue_id && request.status === 'pending')
        : []
      return (
        <section key={widget.widget_id} className="panel detail">
          {selectedIssue ? (
            <>
              <h2>{detailFields.has('title') ? (selectedIssue.title || `${terms.issue}\u8be6\u60c5`) : `${terms.issue}\u8be6\u60c5`}</h2>
              {detailFields.has('source') && <div className="field"><label>{terms.source}</label><span>{issueSource(selectedIssue)}</span></div>}
              {detailFields.has('summary') && <div className="field"><label>{terms.content}</label><p>{selectedIssue.summary}</p></div>}
              {detailFields.has('status') && <div className="field"><label>{terms.status}</label><span>{selectedIssue.status}{detailFields.has('urgency') ? ` \u00b7 ${selectedIssue.urgency}` : ''}</span></div>}
              {detailFields.has('case_id') && <div className="field"><label>{terms.case}</label><span>{selectedIssue.case_id}</span></div>}
              {detailFields.has('issue_type') && <div className="field"><label>{terms.issue_type}</label><span>{selectedIssue.issue_type_id}</span></div>}
              {detailFields.has('created_at') && <div className="field"><label>{terms.created_at}</label><span>{timeStrFull(selectedIssue.created_at || 0)}</span></div>}
              {detailFields.has('external_id') && <div className="field"><label>External ID</label><span>{selectedIssue.external_id || '-'}</span></div>}
              {detailFields.has('deadline') && selectedIssue.deadline_at != null && <div className="field"><label>{terms.deadline}</label><span>{timeStrFull(selectedIssue.deadline_at)}</span></div>}
              {detailFields.has('evidence') && <div className="field"><label>{terms.evidence}</label><div>{(selectedIssue.evidence || []).length === 0 ? `\u6682\u65e0${terms.evidence}` : (selectedIssue.evidence || []).map(evidence => <p key={evidence.evidence_id}><strong>{evidence.kind}</strong>：{evidence.summary || evidence.content || evidence.description || evidence.evidence_id}</p>)}</div></div>}
              {detailFields.has('decisions') && pendingRequests.map(request => (
                <section key={request.decision_request_id} className="decision-request">
                  <h3>{request.title}</h3>
                  <p>{request.prompt}</p>
                  {request.deadline_at != null && <small>{terms.decision}{terms.deadline}：{timeStrFull(request.deadline_at)}</small>}
                  <div className="actions">
                    <input placeholder={`${terms.decision}\u5907\u6ce8...`} value={noteText} onChange={event => setNoteText(event.target.value)} />
                    {(request.options || []).map(option => <button key={option.option_id} className="btn approve" onClick={() => handleDecision(request, option)} title={option.description || ''}>{option.display_name || option.option_id}</button>)}
                  </div>
                </section>
              ))}
              {detailFields.has('decisions') && pendingRequests.length === 0 && <div className="empty">该{terms.issue}当前没有待处理{terms.decision}。</div>}
            </>
          ) : <div className="empty">请先选择一个{terms.issue}查看详情</div>}
        </section>
      )
    }
    if (widget.kind === 'player_message') {
      return (
        <section key={widget.widget_id} className="panel decree-widget">
          <CapabilityPanel
            state={capabilityState}
            gameplay={gameplay}
            title={terms.publish_message || '\u53d1\u5e03\u6d88\u606f'}
            onInvoke={handleCapabilityInvoke}
          />
        </section>
      )
    }
    if (widget.kind === 'in_transit') {
      return (
        <aside key={widget.widget_id} className="panel transit-panel">
          <h2>{terms.in_transit} ({inTransit.length})</h2>
          {inTransit.length === 0 && <div className="empty">暂无{terms.in_transit}</div>}
          {inTransit.map(item => (
            <div key={item.timer_id} className="transit-item">
              <div className="transit-route">{item.from || '?'} → {item.target}</div>
              <div className="transit-eta">{remainingStr(item.remaining_seconds || 0)}</div>
              {item.message_preview && <div className="transit-preview">{item.message_preview}</div>}
            </div>
          ))}
        </aside>
      )
    }
    return null
  }

  const renderRegion = (region) => visibleWidgets(uiManifest, region).map(renderDashboardWidget)
  const temporalDate = formatTemporalDate(timeSystemState)
  const temporalSeason = formatSeason(timeSystemState)
  const activeCycles = activeTemporalCycles(timeSystemState)

  return (
    <div className="app">
      <header className="header">
        <div className="header-brand">
          <span className="brand-seal" aria-hidden="true">{worldTitle.slice(0, 1)}</span>
          <div className="brand-copy">
            <h1>{worldTitle}</h1>
            <p>{worldSubtitle}</p>
          </div>
        </div>
        <div className="header-info">
          <div className={`temporal-clock ${timeSystemState.configured ? 'configured' : 'fallback'}`}>
            <div className="temporal-date-row">
              <span className="day-counter">{temporalDate || timeStrFull(simTime)}</span>
              {temporalSeason && <span className="temporal-season">{temporalSeason}</span>}
            </div>
            {timeSystemState.configured && activeCycles.length > 0 && (
              <div className="temporal-cycle-list" aria-label="当前活跃周期">
                {activeCycles.map(cycle => (
                  <span
                    key={cycle.cycle_id}
                    className={`temporal-cycle-chip ${cycle.kind || 'custom'}`}
                    title={cycle.description || formatCycleProgress(cycle)}
                  >
                    <strong>{cycle.display_name || cycle.cycle_id}</strong>
                    <small>{formatCycleProgress(cycle)}</small>
                  </span>
                ))}
              </div>
            )}
            {timeSystemState.configured && (
              <small className="legacy-sim-time">模拟时间：{timeStrFull(simTime)}</small>
            )}
          </div>
          <div className="header-meta-row">
            {gameplay.configured && (
              <span
                className="player-identity"
                title={controlledObjects.map(item => `${item.kind}:${item.id} (${item.mode})`).join(' / ')}
              >
                <strong>{playerDisplayName}</strong>
                <small>{controlledObjects.length} controls</small>
              </span>
            )}
            <span className={`ws-status ${connected ? 'on' : 'off'}`}>
              <i aria-hidden="true" />{connected ? '世界已连接' : '世界离线'}
            </span>
          </div>
        </div>
        <div className="header-controls">
          {!running ? (
            <>
              <button className="btn primary" onClick={handleStart}>开始模拟</button>
              <button className="btn secondary" onClick={() => setShowSaveManager(true)}>读档</button>
            </>
          ) : paused ? (
            <>
              <button className="btn primary" onClick={handleResume}>继续</button>
              <button className="btn secondary" onClick={handleSave}>存档</button>
              <button className="btn ghost" onClick={() => setShowSaveManager(true)}>读档</button>
            </>
          ) : (
            <button className="btn warning" onClick={handlePause}>暂停</button>
          )}
          {mainNavigation.map(item => (
            <button
              key={item.nav_id}
              className={`btn ${activeView === item.view ? 'active' : 'ghost'}`}
              onClick={() => setCurrentView(item.view)}
              title={item.label}
            >
              <span aria-hidden="true">{item.icon}</span> {item.label}
            </button>
          ))}
          <button className="btn danger" onClick={async () => {
            if (!confirm('确定要开始新游戏？当前运行时状态（含所有 Agent 记忆、世界状态、进度）将被清除。')) return
            await fetch(`${API_BASE}/new-game`, { method: 'POST' })
            location.reload()
          }}>新游戏</button>
          <button className="btn ghost" onClick={openSettings} title="settings">
            &#9881;
          </button>
          <select
            className="time-scale"
            value={secondsPerDay}
            onChange={e => handleTimeScale(Number(e.target.value))}
            disabled={running && !paused}
            title={running && !paused ? '暂停后才能调整' : ''}
          >
            <option value={600}>慢速 (10分钟/天)</option>
            <option value={300}>中速 (5分钟/天)</option>
            <option value={120}>快速 (2分钟/天)</option>
            <option value={60}>极速 (1分钟/天)</option>
          </select>
        </div>
        {tokenUsage.total_tokens > 0 && (
          <div className="token-info">
            {tokenUsage.total_tokens?.toLocaleString()} tokens / {tokenUsage.call_count} 次调用
          </div>
        )}
      </header>

      {autoPauseNotice && (
        <aside className="auto-pause-notice" role="alert" aria-live="assertive">
          <span className="auto-pause-icon" aria-hidden="true">Ⅱ</span>
          <div className="auto-pause-copy">
            <strong>{autoPauseNotice.display_name || '时间系统已自动暂停'}</strong>
            <p>{autoPauseNotice.reason || 'DLC 定义的时间条件已经满足。'}</p>
            {autoPauseNotice.detail && <small>{autoPauseNotice.detail}</small>}
            {autoPauseNotice.request_count > 1 && <small>本次同时触发 {autoPauseNotice.request_count} 项暂停规则</small>}
          </div>
          <span className="auto-pause-guidance">模拟保持暂停，请确认后手动继续</span>
          <button
            type="button"
            className="auto-pause-dismiss"
            onClick={() => setAutoPauseNotice(null)}
            aria-label="关闭自动暂停提示"
            title="仅关闭提示，不会恢复模拟"
          >×</button>
        </aside>
      )}

      {activeView === 'debug' ? (
        <GodView />
      ) : activeView === 'world' ? (
        <WorldStateView manifest={uiManifest.entity_detail} terms={terms} />
      ) : activeView === 'relations' ? (
        <RelationsView />
      ) : (
        <main className={`manifest-dashboard manifest-dashboard-${uiManifest.dashboard.layout}`}>
          {renderRegion('top')}
          <div className={`layout manifest-layout manifest-layout-${uiManifest.dashboard.layout}`}>
            <div className="manifest-region manifest-region-left">{renderRegion('left')}</div>
            <div className="manifest-region manifest-region-main">{renderRegion('main')}</div>
            <div className="manifest-region manifest-region-right">{renderRegion('right')}</div>
          </div>
          {renderRegion('bottom')}
        </main>
      )}

      {showSaveManager && (
        <SaveManagerModal
          onClose={() => setShowSaveManager(false)}
          onLoaded={handleLoaded}
        />
      )}

      <SettingsCenter
        open={showSettings}
        onClose={() => setShowSettings(false)}
        onSaved={nextDocument => {
          const value = nextDocument?.runtime?.simulation?.seconds_per_day
          if (Number.isFinite(value)) setSecondsPerDay(value)
        }}
      />
    </div>
  )
}

export default App
