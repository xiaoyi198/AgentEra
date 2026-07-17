import { useMemo, useState } from 'react'
import {
  buildGameplayBranchDraft,
  createGameplayBranchForm,
  summarizeGameplayBranch,
} from './GameplayBranchTools.js'

function NumberField({ label, value, onChange, min = 0, max, step = 1, help }) {
  return <label><span>{label}</span><input type="number" value={value} min={min} max={max} step={step} onChange={event => onChange(event.target.value)} />{help && <small>{help}</small>}</label>
}

function TextField({ label, value, onChange, placeholder = '', help }) {
  return <label><span>{label}</span><input value={value} onChange={event => onChange(event.target.value)} placeholder={placeholder} />{help && <small>{help}</small>}</label>
}

function PreviewCount({ label, value }) {
  return <article><strong>{label}</strong><span>新增 {value?.added || 0}</span><span>覆盖 {value?.replaced || 0}</span></article>
}

export default function GameplayBranchPanel({ content, onSaveContent, busy = false }) {
  const [form, setForm] = useState(() => createGameplayBranchForm(content))
  const [conflictPolicy, setConflictPolicy] = useState('reject')
  const entities = Array.isArray(content?.entities) ? content.entities : []
  const agents = Array.isArray(content?.agents) ? content.agents : []
  const actors = Array.isArray(content?.actors) ? content.actors : []
  const preview = useMemo(() => buildGameplayBranchDraft(content, form, { conflictPolicy }), [conflictPolicy, content, form])
  const set = (key, value) => setForm(current => ({ ...current, [key]: value }))
  const selectPlayerAgent = agentId => {
    const agent = agents.find(item => String(item?.id || '') === agentId)
    setForm(current => ({ ...current, playerAgentId: agentId, playerActorId: String(agent?.actor_id || agent?.id || current.playerActorId || '') }))
  }
  const selectResponder = agentId => {
    const agent = agents.find(item => String(item?.id || '') === agentId)
    setForm(current => ({ ...current, responderAgentId: agentId, actorId: String(agent?.actor_id || agent?.id || current.actorId || '') }))
  }
  const reset = () => setForm(createGameplayBranchForm(content))
  const submit = () => {
    if (!preview.valid || !preview.changed || busy) return
    onSaveContent(preview.nextContent, '玩法分支快速创建完成：' + summarizeGameplayBranch(preview))
  }

  return <section className="dlc-branch-builder" aria-label="DLC 玩法分支快速创建">
    <header className="dlc-branch-builder-hero">
      <div>
        <span>MECHANICAL BRANCH WIZARD</span>
        <h2>玩法分支模板向导</h2>
        <p>一次生成“高收益主行动 → 即时代价 → 延迟因果事件 → 后续 Issue → 有成本恢复行动”的完整机械链。生成内容只进入 Studio 差异预览，不会直接发布或绕过后端校验。</p>
      </div>
      <div className="dlc-branch-builder-flow" aria-label="生成流程">
        <b>生成链路</b>
        <ol><li>双锚定库存资产 + 两项取舍指标</li><li>主 Action + 初始 Issue 决策</li><li>延迟因果事件 + 后续 Issue</li><li>恢复 Action + 即时/延迟反馈</li><li>Rhythm + Capability + Actor 引用</li></ol>
      </div>
    </header>

    <div className="dlc-branch-form-grid">
      <fieldset>
        <legend>1. 分支身份与参与者</legend>
        <TextField label="分支前缀" value={form.prefix} onChange={value => set('prefix', value)} placeholder="station_crisis" help="生成的 Action、Issue、事件和规则 ID 都以此前缀开头；只允许字母、数字、点、下划线和连字符。" />
        <TextField label="分支中文名称" value={form.branchName} onChange={value => set('branchName', value)} placeholder="边境封锁处置分支" help="用于描述、反馈和审计元数据，不要求与题材绑定。" />
        <label><span>目标实体</span><select value={form.targetEntityId} onChange={event => set('targetEntityId', event.target.value)}><option value="">请选择实体</option>{entities.map(item => <option key={item.id} value={item.id}>{item.name || item.id} · {item.id}</option>)}</select><small>两个 Action 都要求玩家直接控制该实体；未配置控制权时预览会给出中文提醒。</small></label>
        <label><span>接收初始决策的玩家 Agent</span><select value={form.playerAgentId} onChange={event => selectPlayerAgent(event.target.value)}><option value="">请选择 Agent</option>{agents.map(item => <option key={item.id} value={item.id}>{item.name || item.id} · {item.id}</option>)}</select><small>初始事件会投递给该 Agent，以便在新游戏开局形成可处理 Issue。</small></label>
        <label><span>行动授权 Actor</span><select value={form.playerActorId} onChange={event => set('playerActorId', event.target.value)}><option value="">请选择 Actor</option>{actors.map(item => { const id = String(item.actor_id || item.id || ''); return <option key={id} value={id}>{item.display_name || id} · {id}</option> })}</select><small>写入 Action.permissions.actor_ids，通常应选择 gameplay.player.actor_id；它决定玩家身份能否执行两个契约 Action。</small></label>
        <label><span>报告与唤醒 Agent</span><select value={form.responderAgentId} onChange={event => selectResponder(event.target.value)}><option value="">请选择 Agent</option>{agents.map(item => <option key={item.id} value={item.id}>{item.name || item.id} · {item.id}</option>)}</select><small>主行动、恢复行动都会唤醒该 Agent；延迟 Issue 也以它作为报告来源。</small></label>
        <label><span>挂载能力的 Actor</span><select value={form.actorId} onChange={event => set('actorId', event.target.value)}><option value="">请选择 Actor</option>{actors.map(item => { const id = String(item.actor_id || item.id || ''); return <option key={id} value={id}>{item.display_name || id} · {id}</option> })}</select><small>向导会把主行动和恢复行动的 Capability ID 合并进 Actor.capability_ids，不会删除已有能力。</small></label>
      </fieldset>

      <fieldset>
        <legend>2. 同价值锚库存</legend>
        <TextField label="主资源名称" value={form.primaryResourceName} onChange={value => set('primaryResourceName', value)} placeholder="行动配额" help="资源名称同时作为库存 item_name 和 anchored_inventory 前置条件引用。" />
        <div className="dlc-branch-inline"><TextField label="主资源单位" value={form.primaryResourceUnit} onChange={value => set('primaryResourceUnit', value)} placeholder="点" /><NumberField label="初始数量" value={form.primaryInitial} onChange={value => set('primaryInitial', value)} /></div>
        <TextField label="副资源名称" value={form.secondaryResourceName} onChange={value => set('secondaryResourceName', value)} placeholder="支援储备" help="副资源会与主资源使用完全相同的 anchor_rate，并记录 parity_with。" />
        <div className="dlc-branch-inline"><TextField label="副资源单位" value={form.secondaryResourceUnit} onChange={value => set('secondaryResourceUnit', value)} placeholder="份" /><NumberField label="初始数量" value={form.secondaryInitial} onChange={value => set('secondaryInitial', value)} /></div>
        <NumberField label="统一价值锚 anchor_rate" value={form.anchorRate} onChange={value => set('anchorRate', value)} min={0.000001} step={0.01} help="实际扣除数量 = Action 的 base_value ÷ anchor_rate。两种资源使用同一锚后，同一 base_value 代表同一基础价值。" />
        <div className="dlc-branch-cost-table">
          <b>主行动基础价值成本</b><NumberField label="主资源" value={form.mainPrimaryCost} onChange={value => set('mainPrimaryCost', value)} /><NumberField label="副资源" value={form.mainSecondaryCost} onChange={value => set('mainSecondaryCost', value)} />
          <b>恢复行动基础价值成本</b><NumberField label="主资源" value={form.recoveryPrimaryCost} onChange={value => set('recoveryPrimaryCost', value)} /><NumberField label="副资源" value={form.recoverySecondaryCost} onChange={value => set('recoverySecondaryCost', value)} />
        </div>
      </fieldset>

      <fieldset>
        <legend>3. 即时收益与延迟代价</legend>
        <TextField label="即时收益指标" value={form.benefitMetricName} onChange={value => set('benefitMetricName', value)} placeholder="稳定度" help="主行动提高该指标；恢复行动会让它承受小幅回撤，避免免费撤销。" />
        <div className="dlc-branch-inline"><NumberField label="初始值" value={form.benefitInitial} onChange={value => set('benefitInitial', value)} max={100} /><NumberField label="主行动提升" value={form.mainBenefitGain} onChange={value => set('mainBenefitGain', value)} max={100} /></div>
        <NumberField label="恢复行动副作用" value={form.recoveryBenefitPenalty} onChange={value => set('recoveryBenefitPenalty', value)} max={100} help="恢复后从即时收益指标中扣除的数量。" />
        <TextField label="取舍/恢复指标" value={form.tradeoffMetricName} onChange={value => set('tradeoffMetricName', value)} placeholder="通行度" help="主行动即时下降，延迟事件再次下降，恢复行动再将其提高。" />
        <div className="dlc-branch-inline"><NumberField label="初始值" value={form.tradeoffInitial} onChange={value => set('tradeoffInitial', value)} max={100} /><NumberField label="即时下降" value={form.immediateTradeoffLoss} onChange={value => set('immediateTradeoffLoss', value)} max={100} /></div>
        <div className="dlc-branch-inline"><NumberField label="延迟再次下降" value={form.delayedTradeoffLoss} onChange={value => set('delayedTradeoffLoss', value)} max={100} /><NumberField label="恢复行动提高" value={form.recoveryTradeoffGain} onChange={value => set('recoveryTradeoffGain', value)} max={100} /></div>
        <NumberField label="延迟触发天数" value={form.delayDays} onChange={value => set('delayDays', value)} min={0.01} max={3650} step={0.25} help="使用游戏内时间，不依赖现实等待；推进世界模拟后触发因果事件。" />
      </fieldset>

      <fieldset>
        <legend>4. 玩家可见名称</legend>
        <TextField label="主行动名称" value={form.mainActionName} onChange={value => set('mainActionName', value)} placeholder="执行紧急处置" help="用于初始 Issue 的高风险决策选项和主 Capability。" />
        <TextField label="恢复行动名称" value={form.recoveryActionName} onChange={value => set('recoveryActionName', value)} placeholder="恢复受控通道" help="用于后续 Issue 的恢复选项；仍会消耗两种锚定资产。" />
        <TextField label="初始 Issue 名称" value={form.openingIssueName} onChange={value => set('openingIssueName', value)} placeholder="高风险处置请求" />
        <TextField label="后续 Issue 名称" value={form.followupIssueName} onChange={value => set('followupIssueName', value)} placeholder="处置后的延迟压力" />
        <div className="dlc-branch-generated-ids">
          <b>将生成的稳定引用</b>
          <code>{preview.ids.mainActionId}</code>
          <code>{preview.ids.recoveryActionId}</code>
          <code>{preview.ids.openingIssueId}</code>
          <code>{preview.ids.followupIssueId}</code>
          <details><summary>查看全部规则与事件 ID</summary><pre>{JSON.stringify(preview.ids, null, 2)}</pre></details>
        </div>
      </fieldset>
    </div>

    <section className="dlc-branch-review">
      <div className="dlc-branch-review-head">
        <div><h3>生成前校验与差异摘要</h3><p>默认拒绝任何同 ID 内容，确保整套机械链原子生成；只有明确选择覆盖时才替换旧声明。</p></div>
        <label><span>冲突处理</span><select value={conflictPolicy} onChange={event => setConflictPolicy(event.target.value)}><option value="reject">拒绝冲突并要求修改前缀（推荐）</option><option value="replace">覆盖整套同前缀分支（高级）</option></select></label>
      </div>
      <div className="dlc-branch-preview-grid">
        <PreviewCount label="资源 / 指标" value={preview.counts.resources} />
        <PreviewCount label="库存 / 实体值" value={{ added: (preview.counts.inventory?.added || 0) + (preview.counts.entity_values?.added || 0), replaced: (preview.counts.inventory?.replaced || 0) + (preview.counts.entity_values?.replaced || 0) }} />
        <PreviewCount label="Action" value={preview.counts.actions} />
        <PreviewCount label="Issue / 初始事件" value={{ added: (preview.counts.issue_types?.added || 0) + (preview.counts.initial_events?.added || 0), replaced: (preview.counts.issue_types?.replaced || 0) + (preview.counts.initial_events?.replaced || 0) }} />
        <PreviewCount label="因果 / 节奏" value={{ added: (preview.counts.causal_event_rules?.added || 0) + (preview.counts.rhythm_rules?.added || 0), replaced: (preview.counts.causal_event_rules?.replaced || 0) + (preview.counts.rhythm_rules?.replaced || 0) }} />
        <PreviewCount label="Capability / 反馈" value={{ added: (preview.counts.capabilities?.added || 0) + (preview.counts.feedback_rules?.added || 0), replaced: (preview.counts.capabilities?.replaced || 0) + (preview.counts.feedback_rules?.replaced || 0) }} />
      </div>
      {preview.errors.length > 0 && <div className="dlc-quick-create-messages error"><h3>无法生成，请先修正</h3><ul>{preview.errors.map((message, index) => <li key={String(message) + ':' + index}>{message}</li>)}</ul></div>}
      {preview.warnings.length > 0 && <div className="dlc-quick-create-messages warning"><h3>需要确认的设计提醒</h3><ul>{preview.warnings.map((message, index) => <li key={String(message) + ':' + index}>{message}</li>)}</ul></div>}
      <div className="dlc-branch-actions"><button type="button" onClick={reset} disabled={busy}>恢复题材无关默认值</button><button type="button" className="primary" onClick={submit} disabled={!preview.valid || !preview.changed || busy}>{busy ? '处理中…' : '生成玩法分支差异预览'}</button></div>
      <p className="dlc-quick-create-footnote">向导不会修改 Prompt、模型配置或 Settings；它只生成 DLC 内容草稿。应用差异后，仍建议运行 Registry 校验、七步可玩性验收和一次真实游戏内时间推进测试。</p>
    </section>
  </section>
}
