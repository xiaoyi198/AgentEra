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
  const [actionLog, setActionLog] = useState([])
  const [timers, setTimers] = useState({ pending: [], fired: [] })
  const [expandedActions, setExpandedActions] = useState({})
  const timelineRef = useRef(null)
  const pollRef = useRef(null)
  const tlOffsetRef = useRef(0)
  const rlOffsetRef = useRef(0)
  const alOffsetRef = useRef(0)
  const fetchingRef = useRef(false)

  const fetchIncremental = async () => {
    if (fetchingRef.current) return
    fetchingRef.current = true
    try {
      const [tl, rl, al, tm] = await Promise.all([
        fetch(`${API_BASE}/debug/timeline?after=${tlOffsetRef.current}&limit=50`).then(r => r.json()),
        fetch(`${API_BASE}/debug/router-log?after=${rlOffsetRef.current}`).then(r => r.json()),
        fetch(`${API_BASE}/debug/action-log?after=${alOffsetRef.current}`).then(r => r.json()),
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
      if (al.entries?.length > 0) {
        setActionLog(prev => [...prev, ...al.entries])
        alOffsetRef.current += al.entries.length
      }
      setTimers(tm || { pending: [], fired: [] })
    } catch (e) { console.error('God view fetch failed:', e) }
    fetchingRef.current = false
  }

  useEffect(() => {
    fetchIncremental()
    pollRef.current = setInterval(fetchIncremental, 2000)
    return () => clearInterval(pollRef.current)
  }, [])

  const toggleAction = (i) => setExpandedActions(p => ({ ...p, [i]: !p[i] }))

  const fmtArgs = (args) => {
    if (!args || typeof args !== 'object') return ''
    const parts = Object.entries(args).map(([k, v]) => {
      const s = typeof v === 'string' ? v : JSON.stringify(v)
      return `${k}=${s.length > 40 ? s.slice(0, 40) + '…' : s}`
    })
    return parts.join(', ')
  }

  const fmtResult = (result) => {
    if (!result || typeof result !== 'object') return ''
    if (result.found === false) return '未找到'
    if (result.ok === false) return `失败: ${result.error || ''}`
    if (result.ok === true) {
      if ('new_quantity' in result) return `ok (qty=${result.new_quantity})`
      return 'ok'
    }
    if (result.found === true) {
      return `${result.name || result.id}${result.current_status ? ' · ' + result.current_status : ''}`
    }
    if (Array.isArray(result.items)) {
      return `${result.items.length} 条库存`
    }
    return JSON.stringify(result).slice(0, 80)
  }

  const prevCountRef = useRef(0)
  useEffect(() => {
    if (timelineRef.current && timeline.length > prevCountRef.current) {
      const el = timelineRef.current
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
      if (atBottom) el.scrollTop = el.scrollHeight
    }
    prevCountRef.current = timeline.length
  }, [timeline])

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

      {/* 中：系统 Agent 日志（Router + ActionAgent）*/}
      <div className="god-panel god-router">
        <h2>系统 Agent 日志</h2>

        <div className="sys-log-section">
          <h3>Router 决策 ({routerLog.length})</h3>
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
        </div>

        <div className="sys-log-section">
          <h3>ActionAgent 执行 ({actionLog.length})</h3>
          <div className="god-action-list">
            {actionLog.length === 0 && <div className="empty">暂无行动</div>}
            {actionLog.map((a, i) => {
              const expanded = !!expandedActions[i]
              return (
                <div key={i} className={`action-entry ${a.success ? 'ok' : 'fail'}`}>
                  <div className="action-header" onClick={() => toggleAction(i)}>
                    <span className="router-time">{timeLabel(a.sim_time)}</span>
                    <span className="action-type">{a.action_type || '(未命名)'}</span>
                    <span className="action-route">
                      {a.executor_id || '?'} → {a.target_location || '?'}
                    </span>
                    <span className={`action-badge ${a.success ? 'ok' : 'fail'}`}>
                      {a.success ? '成功' : '失败'}
                    </span>
                    <span className="action-meta">{a.fc_rounds}轮 · {a.duration_days}天</span>
                    <span className="action-toggle">{expanded ? '▾' : '▸'}</span>
                  </div>
                  {a.result_description && (
                    <div className="action-result">{a.result_description}</div>
                  )}
                  {expanded && (
                    <div className="action-trace">
                      {a.fc_trace?.length === 0 && <div className="empty">无 FC 轨迹</div>}
                      {a.fc_trace?.map((rd, ri) => (
                        <div key={ri} className="action-round">
                          <div className="action-round-hdr">Round {rd.round}</div>
                          {rd.assistant_content && (
                            <div className="action-assistant">{rd.assistant_content}</div>
                          )}
                          {rd.calls?.map((c, ci) => (
                            <div key={ci} className="action-fc-call">
                              <span className="fc-tool">{c.tool}</span>
                              <span className="fc-args">({fmtArgs(c.args)})</span>
                              <span className="fc-arrow">→</span>
                              <span className="fc-result">{fmtResult(c.result)}</span>
                            </div>
                          ))}
                        </div>
                      ))}
                      {a.changes_made?.length > 0 && (
                        <div className="action-changes">
                          <div className="action-round-hdr">世界变更 ({a.changes_made.length})</div>
                          {a.changes_made.map((ch, ci) => {
                            let text
                            if (ch.type === 'status_update') {
                              text = `[状态] ${ch.entity_id}: ${ch.new_status}`
                            } else if (ch.type === 'inventory_update') {
                              text = `[库存] ${ch.entity_id} ${ch.item_name || ch.resource_name} ${ch.delta > 0 ? '+' : ''}${ch.delta} → ${ch.new_quantity}`
                            } else if (ch.type === 'ongoing_event_add') {
                              text = `[事件+] ${ch.entity_id} · ${ch.description} (${ch.duration_days}天后)`
                            } else if (ch.type === 'ongoing_event_modify') {
                              text = `[事件~] ${ch.event_id} 改 ${(ch.fields || []).join('/')}`
                            } else if (ch.type === 'ongoing_event_delete') {
                              text = `[事件-] ${ch.event_id}`
                            } else {
                              text = `[${ch.type}] ${ch.entity_id || ch.event_id || ''}`
                            }
                            return <div key={ci} className="action-change-item">{text}</div>
                          })}
                        </div>
                      )}
                      {a.error && <div className="action-error">错误: {a.error}</div>}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
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
