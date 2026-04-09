import { useState, useEffect, useRef } from 'react'

const API_BASE = '/api'
const TYPE_COLORS = {
  official: '#c9a84c',
  institution: '#5a9a5a',
  router: '#5a7a9a',
  world_sim: '#9a5a7a',
  tianji: '#4ac9c9',
}
const ACTION_LABELS = {
  report_to_superior: '上报',
  report_to_specific: '越级',
  execute_local_policy: '执政',
  send_private_message: '私信',
  withhold_and_ignore: '压下',
  do_nothing: '不动',
}

const GSD = 86400

function timeLabel(simTime) {
  const d = Math.floor(simTime / GSD)
  const rem = simTime % GSD
  const h = Math.floor(rem / 3600)
  const m = Math.floor((rem % 3600) / 60)
  return `D${d} ${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}`
}

export default function GodView() {
  const [timeline, setTimeline] = useState([])
  const [routerLog, setRouterLog] = useState([])
  const [timers, setTimers] = useState({ pending: [], fired: [] })
  const [prompts, setPrompts] = useState({})
  const [editingPrompt, setEditingPrompt] = useState(null)
  const [promptDraft, setPromptDraft] = useState('')
  const [promptMsg, setPromptMsg] = useState('')
  const timelineRef = useRef(null)
  const pollRef = useRef(null)
  const tlOffsetRef = useRef(0)
  const rlOffsetRef = useRef(0)
  const fetchingRef = useRef(false)

  const fetchIncremental = async () => {
    if (fetchingRef.current) return
    fetchingRef.current = true
    try {
      const [tl, rl, tm] = await Promise.all([
        fetch(`${API_BASE}/debug/timeline?after=${tlOffsetRef.current}&limit=50`).then(r => r.json()),
        fetch(`${API_BASE}/debug/router-log?after=${rlOffsetRef.current}`).then(r => r.json()),
        fetch(`${API_BASE}/debug/timers`).then(r => r.json()),
      ])
      if (tl.entries?.length > 0) {
        setTimeline(prev => [...prev, ...tl.entries])
        tlOffsetRef.current = tl.offset + tl.entries.length
      }
      if (rl.entries?.length > 0) {
        setRouterLog(prev => [...prev, ...rl.entries])
        rlOffsetRef.current += rl.entries.length
      }
      setTimers(tm || { pending: [], fired: [] })
    } catch (e) { console.error('God view fetch failed:', e) }
    fetchingRef.current = false
  }

  const fetchPrompts = async () => {
    try {
      const res = await fetch(`${API_BASE}/prompts`)
      setPrompts(await res.json())
    } catch (e) { console.error('Prompts fetch failed:', e) }
  }

  useEffect(() => {
    fetchIncremental()
    fetchPrompts()
    pollRef.current = setInterval(fetchIncremental, 2000)
    return () => clearInterval(pollRef.current)
  }, [])

  const prevCountRef = useRef(0)
  useEffect(() => {
    if (timelineRef.current && timeline.length > prevCountRef.current) {
      const el = timelineRef.current
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
      if (atBottom) el.scrollTop = el.scrollHeight
    }
    prevCountRef.current = timeline.length
  }, [timeline])

  const savePrompt = async () => {
    if (!editingPrompt) return
    try {
      const res = await fetch(`${API_BASE}/prompts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: editingPrompt, content: promptDraft }),
      })
      const data = await res.json()
      if (data.status === 'ok') {
        setPromptMsg('saved')
        setPrompts(p => ({ ...p, [editingPrompt]: promptDraft }))
        setTimeout(() => setPromptMsg(''), 1500)
      } else {
        setPromptMsg(data.error || 'failed')
      }
    } catch (e) { setPromptMsg('error') }
  }

  const PROMPT_NAMES = {
    official_template: '官员模板',
    router: 'Router (世界运行法则)',
    personas: '人格配置 (JSON)',
  }

  return (
    <div className="god-layout">
      {/* 左：聊天时间线 */}
      <div className="god-panel god-timeline" ref={timelineRef}>
        <h2>Agent 对话时间线</h2>
        {timeline.length === 0 && <div className="empty">暂无记录</div>}
        {timeline.map((e, i) => (
          <div key={e.entry_id || i} className={`chat-bubble ${e.is_distorted ? 'distorted' : ''} ${e.status}`}>
            <div className="chat-header">
              <span className="chat-caller" style={{color: TYPE_COLORS[e.caller_agent_type] || '#e8dcc8'}}>
                {e.caller}
              </span>
              <span className="chat-type-badge">{e.caller_agent_type}</span>
              {e.fc_called && <span className="chat-fc">{ACTION_LABELS[e.fc_called] || e.fc_called}</span>}
              <span className="chat-time">{timeLabel(e.sim_time)}</span>
            </div>
            <div className="chat-content">{e.transmitted_text || '(无内容)'}</div>
            {e.original_text && e.original_text !== e.transmitted_text && (
              <div className="chat-intent">内心: {e.original_text}</div>
            )}
            <div className="chat-meta">
              {e.is_distorted && <span className="tag tag-dist">失真</span>}
              {e.status === 'withheld' && <span className="tag tag-with">压下</span>}
              {e.status === 'loop_detected' && <span className="tag tag-loop">循环</span>}
              {e.routed_to && <span className="tag tag-route">→ {e.routed_to}</span>}
              {e.wakeup_type === 'timer' && <span className="tag tag-timer">定时送达</span>}
              <span className="chat-chain">chain:{e.chain_id?.slice(-6)}</span>
              <span className="chat-hop">hop:{e.hop_count}</span>
            </div>
          </div>
        ))}
      </div>

      {/* 中：Router 决策 + Prompt 编辑 */}
      <div className="god-panel god-router">
        <h2>Router 决策日志</h2>
        <div className="god-router-list">
          {routerLog.length === 0 && <div className="empty">暂无决策</div>}
          {routerLog.map((r, i) => (
            <div key={i} className="router-entry">
              <div className="router-time">{timeLabel(r.sim_time)}</div>
              <div className="router-section">
                <label>输入 ({r.input_actions?.length || 0} 个行动)</label>
                {r.input_actions?.map((a, j) => (
                  <div key={j} className="router-action">
                    <span className="ra-agent">{a.agent}</span>
                    <span className="ra-act">{ACTION_LABELS[a.action] || a.action}</span>
                    <span className="ra-content">{a.content}</span>
                  </div>
                ))}
              </div>
              <div className="router-section">
                <label>输出 ({r.command_count} 条指令)</label>
                {r.output_commands?.map((c, j) => (
                  <div key={j} className="router-cmd">
                    <span className="rc-type">{c.type}</span>
                    <span className="rc-target">→ {c.target}</span>
                    {c.delay > 0 && <span className="rc-delay">{Math.round(c.delay)}s</span>}
                  </div>
                ))}
              </div>
              {r.router_text && (
                <div className="router-text">{r.router_text}</div>
              )}
            </div>
          ))}
        </div>

        <h2 style={{marginTop: 16}}>Prompt 管理</h2>
        <div className="prompt-tabs">
          {Object.entries(PROMPT_NAMES).map(([key, label]) => (
            <button key={key}
              className={`btn ${editingPrompt === key ? 'active' : 'ghost'}`}
              onClick={() => { setEditingPrompt(key); setPromptDraft(prompts[key] || ''); setPromptMsg('') }}
            >{label}</button>
          ))}
        </div>
        {editingPrompt && (
          <div className="prompt-editor">
            <textarea
              value={promptDraft}
              onChange={e => setPromptDraft(e.target.value)}
              rows={10}
            />
            <div className="prompt-actions">
              <button className="btn primary" onClick={savePrompt}>保存</button>
              <button className="btn ghost" onClick={() => setEditingPrompt(null)}>关闭</button>
              {promptMsg && <span className={`prompt-msg ${promptMsg === 'saved' ? 'ok' : 'err'}`}>
                {promptMsg === 'saved' ? '已保存' : promptMsg}
              </span>}
            </div>
          </div>
        )}
      </div>

      {/* 右：定时器 */}
      <div className="god-panel god-timers">
        <h2>待触发定时器 ({timers.pending?.length || 0})</h2>
        {timers.pending?.map(t => (
          <div key={t.timer_id} className="timer-item pending">
            <div className="timer-route">{t.from || '?'} → {t.target}</div>
            <div className="timer-detail">
              剩余 {Math.round(t.remaining_seconds)}s
              ({timeLabel(t.fire_at)} 送达)
            </div>
            {t.message && <div className="timer-msg">{t.message}</div>}
          </div>
        ))}
        {timers.pending?.length === 0 && <div className="empty">无</div>}

        <h2 style={{marginTop: 16}}>已触发 ({timers.fired?.length || 0})</h2>
        {timers.fired?.slice(-20).reverse().map((t, i) => (
          <div key={i} className="timer-item fired">
            <div className="timer-route">{t.from || '?'} → {t.target}</div>
            <div className="timer-detail">
              {timeLabel(t.created_at)} → {timeLabel(t.fired_at)}
            </div>
            {t.message && <div className="timer-msg">{t.message}</div>}
          </div>
        ))}
      </div>
    </div>
  )
}
