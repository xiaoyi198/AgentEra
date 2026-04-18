import { useState, useEffect } from 'react'
import { useWebSocket } from './hooks/useWebSocket'
import GodView from './GodView'
import WorldStateView from './WorldStateView'
import RelationsView from './RelationsView'
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

  const [memorials, setMemorials] = useState([])
  const [selectedMemorial, setSelectedMemorial] = useState(null)
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
  const [settings, setSettings] = useState({
    base_url: '', api_key_masked: '', model: '',
    timeout_s: 30, max_concurrent: 4, seconds_per_day: 600,
  })
  const [settingsForm, setSettingsForm] = useState({})
  const [settingsSaving, setSettingsSaving] = useState(false)
  const [settingsMsg, setSettingsMsg] = useState('')

  // 处理 WebSocket tick
  useEffect(() => {
    if (!lastMessage || lastMessage.type !== 'tick') return
    const data = lastMessage.data
    setSimTime(data.sim_time)
    setInTransit(data.in_transit || [])
    setTokenUsage(data.token_usage || {})
    if (data.new_memorials?.length > 0) {
      setMemorials(prev => {
        const existingIds = new Set(prev.map(m => m.memorial_id))
        const fresh = data.new_memorials.filter(m => !existingIds.has(m.memorial_id))
        return fresh.length > 0 ? [...fresh, ...prev] : prev
      })
    }
  }, [lastMessage])

  // 挂载时从后端拉取已有 memorials（刷新不丢）
  useEffect(() => {
    fetch(`${API_BASE}/memorials`)
      .then(r => r.json())
      .then(data => {
        if (Array.isArray(data) && data.length > 0) {
          setMemorials(data)
        }
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
    setMemorials(data.pending_memorials || [])
    setSelectedMemorial(null)
    setInTransit([])
    alert(`读档成功：${data.save_name}（第${Math.floor(data.sim_time / 86400)}天）`)
  }

  const handlePlayerAction = async (actionType) => {
    if (!selectedMemorial) return
    await apiPost('/player-action', {
      action_type: actionType,
      memorial_id: selectedMemorial.memorial_id,
      content: actionType === 'issue_decree' ? decreeText : '',
      note: noteText,
    })
    setNoteText('')
    setMemorials(prev =>
      prev.map(m =>
        m.memorial_id === selectedMemorial.memorial_id
          ? { ...m, handled: true, action: actionType }
          : m
      )
    )
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

  const openSettings = async () => {
    try {
      const res = await fetch(`${API_BASE}/settings`)
      const data = await res.json()
      setSettings(data)
      setSettingsForm({
        base_url: data.base_url || '',
        api_key: '',
        model: data.model || '',
        timeout_s: data.timeout_s || 30,
        max_concurrent: data.max_concurrent || 4,
        seconds_per_day: data.seconds_per_day || 600,
      })
      setSettingsMsg('')
    } catch (e) {
      setSettingsForm({
        base_url: '', api_key: '', model: '',
        timeout_s: 30, max_concurrent: 4, seconds_per_day: 600,
      })
    }
    setShowSettings(true)
  }

  const saveSettings = async () => {
    setSettingsSaving(true)
    setSettingsMsg('')
    const res = await apiPost('/settings', settingsForm)
    setSettingsSaving(false)
    if (res.error) {
      setSettingsMsg(res.error)
    } else {
      setSettingsMsg('saved')
      setSecondsPerDay(settingsForm.seconds_per_day)
      setTimeout(() => setShowSettings(false), 800)
    }
  }

  return (
    <div className="app">
      <header className="header">
        <h1>大明朝廷</h1>
        <div className="header-info">
          <span className="day-counter">{timeStrFull(simTime)}</span>
          <span className={`ws-status ${connected ? 'on' : 'off'}`}>
            {connected ? '●' : '○'}
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
            {currentView === 'god' ? '皇帝视角' : '上帝视角'}
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
            <h2>待批折子 ({memorials.filter(m => !m.handled).length})</h2>
            {memorials.length === 0 && <div className="empty">等待模拟开始...</div>}
            {memorials.map(m => (
              <div
                key={m.memorial_id}
                className={`item ${selectedMemorial?.memorial_id === m.memorial_id ? 'selected' : ''} ${m.handled ? 'handled' : ''}`}
                onClick={() => setSelectedMemorial(m)}
              >
                <div className="item-title">{m.content?.slice(0, 30) || '(无内容)'}...</div>
                <div className="item-meta">
                  {m.from} · {timeStr(m.received_at || 0)}
                </div>
                {m.handled && (
                  <span className={`badge ${m.action}`}>
                    {{ approve: '准', reject: '驳', hold: '留' }[m.action] || m.action}
                  </span>
                )}
              </div>
            ))}
          </aside>

          <main className="panel detail">
            {selectedMemorial ? (
              <>
                <h2>折子详情</h2>
                <div className="field"><label>来源</label><span>{selectedMemorial.from}</span></div>
                <div className="field"><label>内容</label><p>{selectedMemorial.content}</p></div>
                {selectedMemorial.event_id && (
                  <div className="field"><label>事件</label><span>{selectedMemorial.event_id}</span></div>
                )}
                {!selectedMemorial.handled && (
                  <div className="actions">
                    <input placeholder="批注..." value={noteText} onChange={e => setNoteText(e.target.value)} />
                    <button className="btn approve" onClick={() => handlePlayerAction('approve')}>批准</button>
                    <button className="btn reject" onClick={() => handlePlayerAction('reject')}>驳回</button>
                    <button className="btn hold" onClick={() => handlePlayerAction('hold')}>留中</button>
                  </div>
                )}
              </>
            ) : (
              <div className="empty">选择一份折子查看详情</div>
            )}
            <div className="decree-section">
              <h3>下旨</h3>
              <textarea placeholder="拟旨内容..." value={decreeText} onChange={e => setDecreeText(e.target.value)} />
              <button className="btn decree" onClick={handleDecree} disabled={!decreeText.trim()}>颁布圣旨</button>
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

      {showSettings && (
        <div className="modal-overlay" onClick={() => setShowSettings(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h2>设置</h2>
              <button className="modal-close" onClick={() => setShowSettings(false)}>&times;</button>
            </div>
            <div className="modal-body">
              <div className="setting-group">
                <h3>模型接口</h3>
                <label>接口地址</label>
                <input
                  value={settingsForm.base_url}
                  onChange={e => setSettingsForm(s => ({...s, base_url: e.target.value}))}
                  placeholder="https://api.openai.com/v1"
                />
                <label>密钥 {settings.api_key_masked && <span className="masked">({settings.api_key_masked})</span>}</label>
                <input
                  type="password"
                  value={settingsForm.api_key}
                  onChange={e => setSettingsForm(s => ({...s, api_key: e.target.value}))}
                  placeholder="sk-..."
                />
                <label>模型名称</label>
                <input
                  value={settingsForm.model}
                  onChange={e => setSettingsForm(s => ({...s, model: e.target.value}))}
                  placeholder="gpt-4o-mini"
                />
                <div className="setting-row">
                  <div>
                    <label>超时时间 (秒)</label>
                    <input
                      type="number" min="5" max="120"
                      value={settingsForm.timeout_s}
                      onChange={e => setSettingsForm(s => ({...s, timeout_s: Number(e.target.value)}))}
                    />
                  </div>
                  <div>
                    <label>最大并发数</label>
                    <input
                      type="number" min="1" max="16"
                      value={settingsForm.max_concurrent}
                      onChange={e => setSettingsForm(s => ({...s, max_concurrent: Number(e.target.value)}))}
                    />
                  </div>
                </div>
              </div>

              <div className="setting-group">
                <h3>模拟速度</h3>
                <label>时间倍率</label>
                <select
                  value={settingsForm.seconds_per_day}
                  onChange={e => setSettingsForm(s => ({...s, seconds_per_day: Number(e.target.value)}))}
                >
                  <option value={600}>慢速 (10分钟/天)</option>
                  <option value={300}>中速 (5分钟/天)</option>
                  <option value={120}>快速 (2分钟/天)</option>
                  <option value={60}>极速 (1分钟/天)</option>
                </select>
              </div>

              {settingsMsg && (
                <div className={`settings-msg ${settingsMsg === 'saved' ? 'ok' : 'err'}`}>
                  {settingsMsg === 'saved' ? '已保存' : settingsMsg}
                </div>
              )}
            </div>
            <div className="modal-footer">
              <button className="btn ghost" onClick={() => setShowSettings(false)}>取消</button>
              <button className="btn primary" onClick={saveSettings} disabled={settingsSaving}>
                {settingsSaving ? '保存中...' : '保存'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default App
