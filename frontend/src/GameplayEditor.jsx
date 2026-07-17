import { useMemo, useState } from 'react'
import { buildGameplayDraft, normalizeGameplayPlayer, suggestPlayerFromContent, validateGameplayPlayer } from './GameplayEditorTools.js'

function objectOptions(content, kind) {
  if (kind === 'agent') return (content.agents || []).map(item => ({ id: String(item.id || ''), label: String(item.name || item.id || '') }))
  return (content.entities || []).map(item => ({ id: String(item.id || ''), label: String(item.name || item.id || '') }))
}

export default function GameplayEditor({ content, onSaveContent, busy = false, onNavigate }) {
  const [player, setPlayer] = useState(() => normalizeGameplayPlayer(content))
  const errors = useMemo(() => validateGameplayPlayer(content, player), [content, player])
  const actors = content.actors || []

  const setField = (key, value) => setPlayer(current => ({ ...current, [key]: value }))
  const addControl = kind => setPlayer(current => ({
    ...current,
    controlled_objects: [...current.controlled_objects, { kind, id: '', mode: kind === 'agent' ? 'command' : 'direct', display_name: '' }],
  }))
  const updateControl = (index, key, value) => setPlayer(current => ({
    ...current,
    controlled_objects: current.controlled_objects.map((item, itemIndex) => itemIndex === index ? { ...item, [key]: value, ...(key === 'kind' ? { id: '' } : {}) } : item),
  }))
  const removeControl = index => setPlayer(current => ({ ...current, controlled_objects: current.controlled_objects.filter((_, itemIndex) => itemIndex !== index) }))
  const save = () => {
    const draft = buildGameplayDraft(content, player)
    if (draft.valid) onSaveContent(draft.nextContent, '玩家身份与控制对象已更新')
  }

  return <section className="dlc-gameplay-editor" aria-label="DLC 玩家身份编辑器">
    <header className="dlc-gameplay-editor-hero">
      <div><span>PLAYER CONTRACT</span><h2>玩家身份与控制对象</h2><p>定义玩家在当前题材中“是谁、代表哪个 Actor、能直接操作哪些实体、能直接接收哪些 Agent 报告、能向哪些 Agent 下达命令”。这是行动权限、Issue 投递和主界面身份显示的共同来源。</p></div>
      <button type="button" onClick={() => setPlayer(suggestPlayerFromContent(content))} disabled={!content.agents?.length || busy}>从已有 Agent 智能填充</button>
    </header>

    <div className="dlc-gameplay-editor-grid">
      <label><span>玩家身份 ID</span><input value={player.identity_id} onChange={event => setField('identity_id', event.target.value)} placeholder="commander" /><small>稳定规则标识，例如 commander、guild_master。发布后不建议修改。</small></label>
      <label><span>玩家显示名称</span><input value={player.display_name} onChange={event => setField('display_name', event.target.value)} placeholder="总指挥" /><small>显示在主界面和报告收件人位置，可使用任意题材中文名称。</small></label>
      <label><span>绑定 Actor（建议配置）</span><select value={player.actor_id} onChange={event => setField('actor_id', event.target.value)}><option value="">不绑定 Actor</option>{actors.map(item => {
        const id = String(item.actor_id || item.id || '')
        return <option key={id} value={id}>{item.display_name || id} · {id}</option>
      })}</select><small>行动权限、Actor 属性、身份与能力通常通过该 Actor ID 判断。</small></label>
    </div>

    <section className="dlc-gameplay-controls">
      <div className="dlc-section-heading"><div><h3>控制对象</h3><p>至少添加一项。相同 kind + ID 只能出现一次。</p></div><div><button type="button" onClick={() => addControl('entity')}>添加实体控制</button><button type="button" onClick={() => addControl('agent')}>添加 Agent 控制</button></div></div>
      <div className="dlc-control-mode-guide">
        <article><b>实体 · direct</b><p>玩家可直接以该实体为控制对象执行行动、支付库存成本和选择目标。</p></article>
        <article><b>Agent · direct</b><p>该 Agent 代表玩家本人或玩家界面收件人；发给它的报告可以进入玩家 Issue 流程。</p></article>
        <article><b>Agent · command</b><p>玩家不能把它当作本人，但可通过行动、能力或路由器向它派发任务并等待回报。</p></article>
      </div>

      {!player.controlled_objects.length ? <div className="dlc-gameplay-empty">尚未配置控制对象。若已有玩家 Agent，可先点击“从已有 Agent 智能填充”。</div> : <div className="dlc-gameplay-control-list">
        {player.controlled_objects.map((control, index) => {
          const options = objectOptions(content, control.kind)
          return <article key={`${control.kind}:${control.id}:${index}`}>
            <span className="dlc-gameplay-control-index">{String(index + 1).padStart(2, '0')}</span>
            <label><span>对象类型</span><select value={control.kind} onChange={event => updateControl(index, 'kind', event.target.value)}><option value="entity">实体</option><option value="agent">Agent</option></select></label>
            <label><span>对象</span><select value={control.id} onChange={event => updateControl(index, 'id', event.target.value)}><option value="">请选择</option>{options.map(item => <option key={item.id} value={item.id}>{item.label} · {item.id}</option>)}</select></label>
            <label><span>控制模式</span><select value={control.mode} onChange={event => updateControl(index, 'mode', event.target.value)}><option value="direct">direct · 直接控制</option><option value="command">command · 下达命令</option></select></label>
            <label><span>界面别名</span><input value={control.display_name} onChange={event => updateControl(index, 'display_name', event.target.value)} placeholder="可留空" /></label>
            <button type="button" className="danger" onClick={() => removeControl(index)}>移除</button>
          </article>
        })}
      </div>}
    </section>

    {errors.length > 0 && <div className="dlc-gameplay-errors"><h3>保存前需要修正</h3><ul>{errors.map((error, index) => <li key={`${error}:${index}`}>{error}</li>)}</ul></div>}

    <footer className="dlc-gameplay-editor-actions">
      <div>{onNavigate && <><button type="button" onClick={() => onNavigate('quick_create')}>批量添加实体 / Agent</button><button type="button" onClick={() => onNavigate('ui_manifest')}>继续配置界面术语</button></>}</div>
      <button type="button" className="primary" onClick={save} disabled={busy || errors.length > 0}>{busy ? '处理中…' : '生成差异预览'}</button>
    </footer>
  </section>
}