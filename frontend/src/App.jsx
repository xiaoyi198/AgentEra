import { useState, useEffect } from 'react'
import { useWebSocket } from './hooks/useWebSocket'
import GodView from './GodView'
import WorldStateView from './WorldStateView'
import RelationsView from './RelationsView'
import SettingsCenter from './SettingsCenter'
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
  const [currentView, setCurrentView] = useState('emperor') // 'emperor' | 'god' | 'world' | 'relations'
  const [tokenUsage, setTokenUsage] = useState({})
  const [decreeText, setDecreeText] = useState('')
  const [noteText, setNoteText] = useState('')
  const [secondsPerDay, setSecondsPerDay] = useState(600)
  const [showSaveManager, setShowSaveManager] = useState(false)
  const [showSettings, setShowSettings] = useState(false)

  // WebSocket messages are external events; mirroring a completed tick into view state is intentional.
  useEffect(() => {
    if (!lastMessage || lastMessage.type !== 'tick') return
    const data = lastMessage.data
    // eslint-disable-next-line react-hooks/set-state-in-effect -- mirror an external WebSocket event into view state.
    setSimTime(data.sim_time)
    setInTransit(data.in_transit || [])
    setTokenUsage(data.token_usage || {})
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
  }, [lastMessage])

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
  const handleResume = async () => { await apiPost('/resume'); setPaused(false) }

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
    window.setTimeout(() => {
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
  }

  const handleDecree = async () => {
    if (!decreeText.trim()) return
    await apiPost('/player-action', { action_type: 'issue_decree', content: decreeText })
    setDecreeText('')
  }

  const handleTimeScale = async (value) => {
    setSecondsPerDay(value)
    await apiPost('/time-scale', { seconds_per_day: value })
  }

  const openSettings = () => setShowSettings(true)

  const controlledObjects = gameplay.player?.controlled_objects || []
  const playerDisplayName = gameplay.player?.display_name || '\u73a9\u5bb6'
  const worldTitle = gameplay.package_name || '\u4e16\u754c\u6a21\u62df\u5668'
  const worldSubtitle = gameplay.configured
    ? `${playerDisplayName} / ${controlledObjects.length} controlled objects`
    : 'DLC-driven world simulation'

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
          <span className="day-counter">{timeStrFull(simTime)}</span>
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
          <button
            className={`btn ${currentView === 'god' ? 'active' : 'ghost'}`}
            onClick={() => setCurrentView(v => v === 'god' ? 'emperor' : 'god')}
          >
            {currentView === 'god' ? `${playerDisplayName}\u89c6\u89d2` : '\u4e0a\u5e1d\u89c6\u89d2'}
          </button>
          <button
            className={`btn ${currentView === 'world' ? 'active' : 'ghost'}`}
            onClick={() => setCurrentView(v => v === 'world' ? 'emperor' : 'world')}
          >
            世界状态
          </button>
          <button
            className={`btn ${currentView === 'relations' ? 'active' : 'ghost'}`}
            onClick={() => setCurrentView(v => v === 'relations' ? 'emperor' : 'relations')}
          >
            关系图
          </button>
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

      {currentView === 'god' ? (
        <GodView />
      ) : currentView === 'world' ? (
        <WorldStateView />
      ) : currentView === 'relations' ? (
        <RelationsView />
      ) : (
        <div className="layout">
          <aside className="panel memorial-list">
            <h2>待处理事项 ({issues.filter(item => !['resolved', 'closed', 'dismissed'].includes(item.status)).length})</h2>
            {issues.length === 0 && <div className="empty">等待世界产生需要处理的事项...</div>}
            {issues.map(item => (
              <div
                key={item.issue_id}
                className={`item ${selectedIssue?.issue_id === item.issue_id ? 'selected' : ''} ${['resolved', 'closed', 'dismissed'].includes(item.status) ? 'handled' : ''}`}
                onClick={() => setSelectedIssue(item)}
              >
                <div className="item-title">{item.title || item.summary?.slice(0, 30) || '(无标题事项)'}</div>
                <div className="item-meta">
                  {item.source?.display_name || item.source?.id || item.source?.kind || '未知来源'} · {item.urgency || 'normal'} · {timeStr(item.created_at || 0)}
                </div>
                <span className={`badge ${item.status}`}>{item.status}</span>
              </div>
            ))}
          </aside>

          <main className="panel detail">
            {selectedIssue ? (
              <>
                <h2>{selectedIssue.title || '事项详情'}</h2>
                <div className="field"><label>来源</label><span>{selectedIssue.source?.display_name || selectedIssue.source?.id || selectedIssue.source?.kind || '未知'}</span></div>
                <div className="field"><label>内容</label><p>{selectedIssue.summary}</p></div>
                <div className="field"><label>状态</label><span>{selectedIssue.status} · {selectedIssue.urgency}</span></div>
                <div className="field"><label>Case</label><span>{selectedIssue.case_id}</span></div>
                {selectedIssue.deadline_at != null && <div className="field"><label>期限</label><span>{timeStrFull(selectedIssue.deadline_at)}</span></div>}
                <div className="field"><label>证据</label><div>{(selectedIssue.evidence || []).length === 0 ? '暂无证据' : (selectedIssue.evidence || []).map(evidence => <p key={evidence.evidence_id}><strong>{evidence.kind}</strong>：{evidence.summary || evidence.content || evidence.description || evidence.evidence_id}</p>)}</div></div>
                {decisionRequests.filter(request => request.issue_id === selectedIssue.issue_id && request.status === 'pending').map(request => (
                  <section key={request.decision_request_id} className="decision-request">
                    <h3>{request.title}</h3>
                    <p>{request.prompt}</p>
                    {request.deadline_at != null && <small>决策期限：{timeStrFull(request.deadline_at)}</small>}
                    <div className="actions">
                      <input placeholder="决策备注..." value={noteText} onChange={event => setNoteText(event.target.value)} />
                      {(request.options || []).map(option => <button key={option.option_id} className="btn approve" onClick={() => handleDecision(request, option)} title={option.description || ''}>{option.display_name || option.option_id}</button>)}
                    </div>
                  </section>
                ))}
                {decisionRequests.filter(request => request.issue_id === selectedIssue.issue_id && request.status === 'pending').length === 0 && <div className="empty">此事项当前没有待处理决策。</div>}
              </>
            ) : (
              <div className="empty">选择一项 Issue 查看来源、证据、期限和动态决策选项</div>
            )}
            <div className="decree-section">
              <h3>发布消息</h3>
              <textarea placeholder="输入玩家消息..." value={decreeText} onChange={e => setDecreeText(e.target.value)} />
              <button className="btn decree" onClick={handleDecree} disabled={!decreeText.trim()}>发送</button>
            </div>
          </main>

          <aside className="panel transit-panel">
            <h2>在途公文 ({inTransit.length})</h2>
            {inTransit.length === 0 && <div className="empty">暂无在途公文</div>}
            {inTransit.map(t => (
              <div key={t.timer_id} className="transit-item">
                <div className="transit-route">{t.from || '?'} → {t.target}</div>
                <div className="transit-eta">
                  {remainingStr(t.remaining_seconds || 0)}
                </div>
                {t.message_preview && <div className="transit-preview">{t.message_preview}</div>}
              </div>
            ))}
          </aside>
        </div>
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
