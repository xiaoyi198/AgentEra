import { useState, useEffect, useRef, useCallback } from 'react'

// ── API helpers ──

const api = (url, opts = {}) =>
  fetch(url, { headers: { 'Content-Type': 'application/json' }, ...opts }).then(r => r.json())

// ── 工具调用折叠块 ──

function ToolCallBlock({ name, args, result }) {
  const [open, setOpen] = useState(false)
  const isError = result && (result.ok === false || result.error)

  return (
    <div style={{ marginBottom: 4 }}>
      <div className="tj-tool-call-block">
        <div className="tj-tool-call-header" onClick={() => setOpen(o => !o)}>
          <span className="tj-tool-call-name">⚙ {name}()</span>
          <span className="tj-tool-call-status">{open ? '▲' : '▼'}</span>
        </div>
        {open && (
          <div className="tj-tool-call-body">
            {JSON.stringify(args, null, 2)}
          </div>
        )}
      </div>
      {result !== null && (
        <div className={`tj-tool-result-block${isError ? ' error' : ''}`}>
          <div className="tj-tool-result-header" onClick={() => setOpen(o => !o)}>
            <span className="tj-tool-result-name">
              {isError ? '✗' : '✓'} {name} 结果
            </span>
            <span className="tj-tool-call-status">{open ? '▲' : '▼'}</span>
          </div>
          {open && (
            <div className="tj-tool-result-body">
              {JSON.stringify(result, null, 2)}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── 单条消息 ──

function Message({ msg }) {
  if (msg.role === 'user') {
    return (
      <div className="tj-message user">
        <div className="tj-message-label">您</div>
        <div className="tj-bubble">{msg.content}</div>
      </div>
    )
  }

  if (msg.role === 'assistant') {
    if (!msg.content || !msg.content.trim()) return null
    return (
      <div className="tj-message assistant">
        <div className="tj-message-label">天玑</div>
        <div className="tj-bubble">{msg.content}</div>
      </div>
    )
  }

  if (msg.role === 'tool-group') {
    return (
      <div className="tj-message tool-group">
        {msg.calls.map((call, i) => (
          <ToolCallBlock
            key={i}
            name={call.name}
            args={call.args}
            result={call.result}
          />
        ))}
      </div>
    )
  }

  return null
}

// ── 工具面板 ──

function ToolsPanel({ categories }) {
  const [collapsed, setCollapsed] = useState({})

  const toggleCat = (cat) => setCollapsed(c => ({ ...c, [cat]: !c[cat] }))

  return (
    <div className="tj-tools-panel">
      <h2>可用工具</h2>
      {Object.entries(categories).map(([cat, tools]) => (
        tools.length === 0 ? null : (
          <div key={cat} className="tj-tool-category">
            <div className="tj-tool-category-header" onClick={() => toggleCat(cat)}>
              <span>{cat}</span>
              <span className={`tj-tool-category-chevron ${collapsed[cat] ? '' : 'open'}`}>▶</span>
            </div>
            {!collapsed[cat] && tools.map(tool => (
              <div key={tool.name} className="tj-tool-item">
                <div className="tj-tool-name">{tool.name}</div>
                <div className="tj-tool-desc">{tool.description}</div>
              </div>
            ))}
          </div>
        )
      ))}
    </div>
  )
}

// ── 世界状态面板 ──

function WorldStatePanel({ entities, onRefresh }) {
  return (
    <div className="tj-state-panel">
      <h2>世界状态</h2>
      <div className="tj-state-list">
        {entities.length === 0 ? (
          <div style={{ padding: '16px', color: 'var(--text-dim)', fontSize: 12 }}>
            点击刷新查看实体
          </div>
        ) : entities.map(ent => (
          <div key={ent.id} className="tj-state-item">
            <div className="tj-state-entity-id">{ent.id}</div>
            <div className="tj-state-entity-name">{ent.name}</div>
            {ent.current_status && (
              <div className="tj-state-entity-status">{ent.current_status}</div>
            )}
          </div>
        ))}
      </div>
      <div className="tj-state-refresh">
        <button className="tj-btn" style={{ width: '100%' }} onClick={onRefresh}>
          刷新状态
        </button>
      </div>
    </div>
  )
}

// ── 主组件 ──

export default function TianJiView() {
  const [displayMsgs, setDisplayMsgs] = useState([])   // 用于渲染的消息列表
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [toolCategories, setToolCategories] = useState({})
  const [entities, setEntities] = useState([])
  const messagesEndRef = useRef(null)
  const inputRef = useRef(null)

  // 滚动到底部
  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  useEffect(() => { scrollToBottom() }, [displayMsgs, scrollToBottom])

  // 加载工具 schema
  useEffect(() => {
    api('/api/tianji/tools')
      .then(data => {
        if (data.categories) setToolCategories(data.categories)
      })
      .catch(() => {})
  }, [])

  // 刷新世界状态
  const refreshWorldState = useCallback(() => {
    api('/api/world-state')
      .then(data => {
        if (Array.isArray(data)) setEntities(data)
        else if (data.entities) setEntities(data.entities)
      })
      .catch(() => {})
  }, [])

  // 重置会话
  const handleReset = useCallback(async () => {
    await api('/api/tianji/reset', { method: 'POST' })
    setDisplayMsgs([])
    setInput('')
  }, [])

  // 发送消息
  const handleSend = useCallback(async () => {
    const text = input.trim()
    if (!text || loading) return

    setInput('')
    setLoading(true)

    // 添加用户消息
    setDisplayMsgs(prev => [...prev, { role: 'user', content: text }])

    // 发送到后端，读取 SSE 流
    try {
      const resp = await fetch('/api/tianji/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
      })

      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      // 当前 assistant 文本（增量拼接）
      let assistantText = ''
      // 当前工具调用组（多个 tool_call + tool_result 配对）
      let currentToolGroup = []    // [{name, args, result}]
      let pendingToolCall = null   // {name, args}
      // 每组工具调用分配唯一 gid，用于前端去重：
      // tool_result 实时更新用 gid 匹配，commitToolGroup 也用 gid 匹配。
      let toolGroupGid = 0

      const flushAssistant = () => {
        if (assistantText.trim()) {
          const snapshot = assistantText  // 快照：防止 React 批量更新时闭包读到已重置的值
          setDisplayMsgs(prev => {
            const last = prev[prev.length - 1]
            if (last && last.role === 'assistant') {
              return [...prev.slice(0, -1), { role: 'assistant', content: snapshot }]
            }
            return [...prev, { role: 'assistant', content: snapshot }]
          })
          assistantText = ''
        }
      }

      // 将 currentToolGroup 写入 / 更新到 displayMsgs（幂等：按 gid 去重）
      const commitToolGroup = () => {
        if (currentToolGroup.length === 0) return
        const group = [...currentToolGroup]
        const gid = toolGroupGid
        setDisplayMsgs(prev => {
          const idx = prev.findIndex(m => m.role === 'tool-group' && m._gid === gid)
          if (idx >= 0) {
            // 已存在 → 原地替换（实时更新）
            const copy = [...prev]
            copy[idx] = { role: 'tool-group', calls: group, _gid: gid }
            return copy
          }
          return [...prev, { role: 'tool-group', calls: group, _gid: gid }]
        })
      }

      // 终结当前工具组，为下一轮准备新 gid
      const finalizeToolGroup = () => {
        if (currentToolGroup.length > 0) {
          commitToolGroup()
          currentToolGroup = []
          pendingToolCall = null
          toolGroupGid++
        }
      }

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() // 最后一行可能不完整，留待下次

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          let event
          try { event = JSON.parse(line.slice(6)) } catch { continue }

          if (event.type === 'assistant_delta') {
            if (!event.text) continue  // 跳过空文本
            finalizeToolGroup()
            assistantText += event.text
            const snapshot = assistantText  // 快照：防止 React 批量更新时闭包读到被重置的值
            setDisplayMsgs(prev => {
              const last = prev[prev.length - 1]
              if (last && last.role === 'assistant') {
                return [...prev.slice(0, -1), { role: 'assistant', content: snapshot }]
              }
              return [...prev, { role: 'assistant', content: snapshot }]
            })
          } else if (event.type === 'tool_round_start') {
            flushAssistant()
            finalizeToolGroup()
          } else if (event.type === 'tool_call') {
            flushAssistant()
            pendingToolCall = { name: event.name, args: event.arguments }
          } else if (event.type === 'tool_result') {
            if (pendingToolCall) {
              currentToolGroup.push({ ...pendingToolCall, result: event.result })
              pendingToolCall = null
            }
            commitToolGroup()
          } else if (event.type === 'done') {
            flushAssistant()
            if (pendingToolCall) {
              currentToolGroup.push({ ...pendingToolCall, result: null })
              pendingToolCall = null
            }
            finalizeToolGroup()
          } else if (event.type === 'error') {
            flushAssistant()
            finalizeToolGroup()
            setDisplayMsgs(prev => [...prev, {
              role: 'assistant',
              content: `[错误] ${event.text}`,
            }])
          }
        }
      }
    } catch (err) {
      setDisplayMsgs(prev => [...prev, {
        role: 'assistant',
        content: `[网络错误] ${err.message}`,
      }])
    }

    setLoading(false)
    inputRef.current?.focus()
  }, [input, loading])

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      {/* Header */}
      <div className="tj-header">
        <div>
          <h1>天 玑</h1>
          <div className="tj-header-subtitle">全流程测试调试助手</div>
        </div>
        <div className="tj-header-actions">
          <button className="tj-btn" onClick={refreshWorldState}>刷新世界</button>
          <button className="tj-btn danger" onClick={handleReset}>重置会话</button>
        </div>
      </div>

      {/* 三栏 */}
      <div className="tj-layout">
        {/* 左：工具列表 */}
        <ToolsPanel categories={toolCategories} />

        {/* 中：对话 */}
        <div className="tj-chat-panel">
          <div className="tj-messages">
            {displayMsgs.length === 0 ? (
              <div className="tj-empty">
                <div className="tj-empty-title">天 玑</div>
                <div className="tj-empty-hint">
                  我是天玑，大明世界模拟器的调试助手。<br />
                  你可以让我查询世界状态、触发行动模拟、<br />
                  测试路由指令，或模拟官员行为。
                </div>
              </div>
            ) : (
              displayMsgs.map((msg, i) => <Message key={i} msg={msg} />)
            )}
            <div ref={messagesEndRef} />
          </div>

          <div className="tj-input-area">
            <textarea
              ref={inputRef}
              className="tj-input"
              placeholder="输入指令，如：查询吴县当前状况；给吴县增加500石稻米；模拟吴县知县上报灾情…"
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={loading}
              rows={1}
              style={{ height: Math.min(120, Math.max(40, (input.split('\n').length) * 22)) + 'px' }}
            />
            <button
              className="tj-btn primary"
              onClick={handleSend}
              disabled={loading || !input.trim()}
            >
              {loading ? <><span className="tj-loading" />处理中</> : '发送'}
            </button>
          </div>
        </div>

        {/* 右：世界状态 */}
        <WorldStatePanel entities={entities} onRefresh={refreshWorldState} />
      </div>
    </div>
  )
}
