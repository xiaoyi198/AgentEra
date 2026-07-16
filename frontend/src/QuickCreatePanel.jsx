import { useMemo, useState } from 'react'
import { buildQuickCreateDraft, summarizeQuickCreate } from './QuickCreateTools.js'

const ENTITY_EXAMPLE = `# 实体 ID | 名称 | 类型 | 当前状态 | 中文说明
headquarters | 总部 | location | 正常运转 | 玩家与 Agent 的主要活动地点
field_team | 外勤小组 | organization | 等待任务 | 负责执行调查与处置`

const AGENT_EXAMPLE = `# Agent ID | 显示名 | Actor ID | 所在实体 ID | 角色 | 中文说明 | Actor 类型
player_operator | 总调度员 | player_operator_actor | headquarters | player | 玩家直接控制、接收报告并作出决定
field_specialist | 外勤专家 | field_specialist_actor | field_team | specialist | 接受调度、执行现场任务并持续回报 | person`

function CountCard({ title, value }) {
  return <article>
    <strong>{title}</strong>
    <span>新增 {value?.added || 0}</span>
    <span>覆盖 {value?.replaced || 0}</span>
    <span>跳过 {value?.skipped || 0}</span>
  </article>
}

export default function QuickCreatePanel({ content, onSaveContent, busy = false, onNavigate }) {
  const [entitiesText, setEntitiesText] = useState('')
  const [agentsText, setAgentsText] = useState('')
  const [conflictPolicy, setConflictPolicy] = useState('skip')
  const preview = useMemo(() => buildQuickCreateDraft(content, { entitiesText, agentsText, conflictPolicy }), [agentsText, conflictPolicy, content, entitiesText])
  const hasInput = Boolean(entitiesText.trim() || agentsText.trim())

  const submit = () => {
    if (!preview.valid || !preview.changed || busy) return
    onSaveContent(preview.nextContent, `快速创建完成：${summarizeQuickCreate(preview)}`)
  }

  return <section className="dlc-quick-create" aria-label="DLC 实体与 Agent 快速创建">
    <header className="dlc-quick-create-hero">
      <div>
        <span>AUTHORING ACCELERATOR</span>
        <h2>实体、Actor 与 Agent 批量创建</h2>
        <p>把一组世界实体和可运行 Agent 一次写入 DLC 草稿。Agent 行会自动同时创建 Actor 组件，避免出现“Agent 已存在但 Actor 缺失”的断裂引用；最终仍会进入差异审阅，不会直接覆盖文件。</p>
      </div>
      <div className="dlc-quick-create-route">
        <b>推荐创作顺序</b>
        <ol><li>先添加地点与组织实体</li><li>再添加 Actor / Agent 套件</li><li>配置玩家身份和控制对象</li><li>继续配置行动、Issue 与因果反馈</li></ol>
      </div>
    </header>

    <div className="dlc-quick-create-grid">
      <article className="dlc-quick-create-card">
        <div className="dlc-quick-create-card-title">
          <div><h3>世界实体</h3><p>每行一个地点、组织、人物、物品或系统实体。</p></div>
          <button type="button" onClick={() => setEntitiesText(ENTITY_EXAMPLE)}>填入中文示例</button>
        </div>
        <code>实体 ID | 名称 | 类型 | 当前状态 | 中文说明</code>
        <textarea value={entitiesText} onChange={event => setEntitiesText(event.target.value)} rows={12} spellCheck="false" placeholder="station | 中继站 | location | 警戒 | 主要活动地点" />
        <details><summary>字段说明与可用类型</summary><ul>
          <li><b>实体 ID：</b>稳定引用键，发布后不建议修改；不能含空格或斜杠。</li>
          <li><b>类型：</b>推荐使用 location、organization、institution、region、character、faction、object、person、group、creature、system、item、other。</li>
          <li><b>当前状态：</b>面向玩家的短状态，例如“戒备”“停运”“迁徙中”。</li>
          <li>可以用竖线或 Tab 分隔；空行和以 # 开头的说明行会被忽略。</li>
        </ul></details>
      </article>

      <article className="dlc-quick-create-card">
        <div className="dlc-quick-create-card-title">
          <div><h3>Agent / Actor 套件</h3><p>每行同时生成一个运行 Agent 和一个通用 Actor 组件。</p></div>
          <button type="button" onClick={() => setAgentsText(AGENT_EXAMPLE)}>填入中文示例</button>
        </div>
        <code>Agent ID | 显示名 | Actor ID | 所在实体 ID | 角色 | 中文说明 | Actor 类型</code>
        <textarea value={agentsText} onChange={event => setAgentsText(event.target.value)} rows={12} spellCheck="false" placeholder="scout | 侦察员 | scout_actor | outpost | specialist | 负责外勤侦察 | person" />
        <details><summary>字段说明与生成内容</summary><ul>
          <li><b>Agent ID：</b>路由、事件目标和唤醒操作使用的稳定标识，可使用中文；不能含空格或斜杠。</li>
          <li><b>Actor ID：</b>规则、能力、关系与目标使用的角色组件标识；留空时自动使用“Agent ID_actor”。</li>
          <li><b>所在实体 ID：</b>必须引用已有实体或本页同时新增的实体；不需要位置时可留空。</li>
          <li><b>角色：</b>写入 Agent.role 和 Actor 的公开身份，后续可在详细编辑器中扩展 Trait、需求、目标和能力。</li>
          <li><b>Actor 类型：</b>默认为 person，也可使用 organization、institution、group、creature、system、other。</li>
        </ul></details>
      </article>
    </div>

    <section className="dlc-quick-create-review">
      <div className="dlc-quick-create-options">
        <label><span>遇到同 ID 内容时</span><select value={conflictPolicy} onChange={event => setConflictPolicy(event.target.value)}>
          <option value="skip">跳过冲突（推荐，不覆盖已有细节）</option>
          <option value="replace">覆盖冲突（用本页基础模板替换原条目）</option>
        </select></label>
        <p>{conflictPolicy === 'skip' ? '若 Agent 或 Actor 任一 ID 冲突，会整套跳过，避免半套引用。' : '覆盖会重置该实体、Actor 或 Agent 的高级字段；应用前请仔细检查差异预览。'}</p>
      </div>

      <div className="dlc-quick-create-counts">
        <CountCard title="实体" value={preview.counts.entities} />
        <CountCard title="Actor" value={preview.counts.actors} />
        <CountCard title="Agent" value={preview.counts.agents} />
      </div>

      {preview.errors.length > 0 && <div className="dlc-quick-create-messages error"><h3>请先修正以下问题</h3><ul>{preview.errors.map((item, index) => <li key={`${item.line}:${index}`}>第 {item.line} 行：{item.message}</li>)}</ul></div>}
      {preview.warnings.length > 0 && <div className="dlc-quick-create-messages warning"><h3>预览提醒</h3><ul>{preview.warnings.map((message, index) => <li key={`${message}:${index}`}>{message}</li>)}</ul></div>}

      <div className="dlc-quick-create-actions">
        <button type="button" onClick={() => { setEntitiesText(''); setAgentsText('') }} disabled={!hasInput || busy}>清空输入</button>
        {onNavigate && <button type="button" onClick={() => onNavigate('gameplay')}>下一步：配置玩家身份</button>}
        <button type="button" className="primary" onClick={submit} disabled={!hasInput || !preview.valid || !preview.changed || busy}>{busy ? '处理中…' : '生成差异预览'}</button>
      </div>
      <p className="dlc-quick-create-footnote">本工具只生成题材无关的基础骨架，不会自动写入人格 Prompt、模型配置或玩法数值。保存后请进入 Actor、Agent、玩家身份、行动和七步可玩性验收继续完善。</p>
    </section>
  </section>
}