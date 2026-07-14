import { useEffect, useMemo, useState } from 'react'
import {
  createSettingsDraft, nextUniqueName, parseAdvancedModelJson, parseProfileList,
  renameMappingKey, settingsFingerprint, summarizeSettingsPreview,
} from './SettingsSchemaTools'
import './SettingsCenter.css'

const API_BASE = '/api'
const TAB_ICONS = { overview: '总', providers: '接', models: '模', routing: '路', rounds: '轮', memory: '忆', simulation: '世', security: '安', advanced: '码' }
const clone = value => JSON.parse(JSON.stringify(value))

async function readResponse(response) {
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = data.detail || data.error || `请求失败（HTTP ${response.status}）`
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail, null, 2))
  }
  return data
}

function NameEditor({ value, label, onCommit }) {
  const [text, setText] = useState(value)
  const commit = () => {
    const next = text.trim()
    if (!next || next === value) { setText(value); return }
    try { onCommit(next) } catch (error) { window.alert(error.message); setText(value) }
  }
  return <label className="settings-name-editor"><span>{label}</span><input value={text} onChange={e => setText(e.target.value)} onBlur={commit} onKeyDown={e => {
    if (e.key === 'Enter') e.currentTarget.blur()
    if (e.key === 'Escape') { setText(value); e.currentTarget.blur() }
  }} /></label>
}

function MetaNote({ meta, risk }) {
  if (!meta && !risk) return null
  return <div className={`settings-meta-note ${risk ? 'risk' : ''}`}>
    <span>{meta?.description || risk}</span>
    {meta?.minimum !== undefined && <small>合法范围：{meta.minimum} ～ {meta.maximum}</small>}
    {meta?.hot_reload && <small className="hot">保存后热更新，无需重启</small>}
    {risk && meta?.description && <small>{risk}</small>}
  </div>
}

function NumberSetting({ meta, value, onChange, step = 1, risk }) {
  return <label className="settings-field"><span className="settings-field-title">{meta?.label || meta?.path}</span>
    <input type="number" min={meta?.minimum} max={meta?.maximum} step={step} value={value ?? ''} onChange={e => onChange(Number(e.target.value))} />
    <MetaNote meta={meta} risk={risk} />
  </label>
}

function TextSetting({ label, description, value, onChange, placeholder, type = 'text' }) {
  return <label className="settings-field"><span className="settings-field-title">{label}</span>
    <input type={type} value={value ?? ''} onChange={e => onChange(e.target.value)} placeholder={placeholder} />
    {description && <div className="settings-meta-note"><span>{description}</span></div>}
  </label>
}

function StatusPill({ tone = 'neutral', children }) { return <span className={`settings-pill ${tone}`}>{children}</span> }
function EmptyState({ children }) { return <div className="settings-empty">{children}</div> }
function SectionHeader({ title, description, action }) {
  return <div className="settings-section-header"><div><h3>{title}</h3>{description && <p>{description}</p>}</div>{action}</div>
}

function PolicyRows({ policies, taskTypes, onChange }) {
  const entries = Object.entries(policies || {})
  return <div className="settings-policy-list">
    {!entries.length && <EmptyState>尚未配置任务路由。此作用域将继续使用下一级回退规则。</EmptyState>}
    {entries.map(([task, profiles]) => {
      const taskInfo = taskTypes.find(item => item.id === task)
      return <div className="settings-policy-row" key={task}>
        <div><NameEditor value={task} label="任务类型" onCommit={next => onChange(renameMappingKey(policies, task, next))} />
          <small>{taskInfo?.description || '自定义任务类型；调用方必须使用完全相同的标识。'}</small></div>
        <label><span>候选 Profile（按回退顺序，逗号分隔）</span>
          <input value={(profiles || []).join(', ')} onChange={e => onChange({ ...policies, [task]: parseProfileList(e.target.value) })} placeholder="deep_sim, fast_tools" />
          <small>从左到右依次尝试；前一个不可用或失败后才使用后一个。</small></label>
        <button className="settings-icon-btn danger" type="button" onClick={() => { const next = { ...policies }; delete next[task]; onChange(next) }}>删除</button>
      </div>
    })}
    <button className="btn secondary" type="button" onClick={() => { const task = nextUniqueName('custom.task', policies); onChange({ ...policies, [task]: [] }) }}>＋ 添加任务路由</button>
  </div>
}

function SettingsCenter({ open, onClose, onSaved }) {
  const [schema, setSchema] = useState(null)
  const [document, setDocument] = useState(null)
  const [draft, setDraft] = useState(null)
  const [baseline, setBaseline] = useState('')
  const [activeTab, setActiveTab] = useState('overview')
  const [phase, setPhase] = useState('idle')
  const [message, setMessage] = useState('')
  const [validation, setValidation] = useState(null)
  const [validationFingerprint, setValidationFingerprint] = useState('')
  const [saveReady, setSaveReady] = useState(false)
  const [advancedJson, setAdvancedJson] = useState('')
  const [advancedError, setAdvancedError] = useState('')

  useEffect(() => {
    if (!open) return undefined
    let cancelled = false
    // eslint-disable-next-line react-hooks/set-state-in-effect -- opening starts an external settings fetch.
    setPhase('loading'); setMessage('正在读取配置与中文说明……')
    Promise.all([
      fetch(`${API_BASE}/settings/schema`).then(readResponse),
      fetch(`${API_BASE}/settings`).then(readResponse),
    ]).then(([nextSchema, nextDocument]) => {
      if (cancelled) return
      const nextDraft = createSettingsDraft(nextDocument)
      setSchema(nextSchema); setDocument(nextDocument); setDraft(nextDraft)
      setBaseline(settingsFingerprint(nextDraft)); setAdvancedJson(JSON.stringify(nextDraft.model_gateway, null, 2))
      setValidation(null); setValidationFingerprint(''); setSaveReady(false); setAdvancedError('')
      setPhase('idle'); setMessage('配置已加载。敏感密钥不会由服务器回传。')
    }).catch(error => { if (!cancelled) { setPhase('error'); setMessage(error.message) } })
    return () => { cancelled = true }
  }, [open])

  const fieldMap = useMemo(() => Object.fromEntries((schema?.fields || []).map(item => [item.path, item])), [schema])
  if (!open) return null

  const fingerprint = settingsFingerprint(draft)
  const dirty = Boolean(draft && baseline && fingerprint !== baseline)
  const validationIsCurrent = Boolean(validation && validationFingerprint === fingerprint)
  const tabs = schema?.categories || []
  const providers = draft?.model_gateway?.providers || {}
  const models = draft?.model_gateway?.models || {}
  const taskTypes = schema?.task_types || []

  const setModelGateway = updater => {
    setDraft(current => {
      if (!current) return current
      const nextModel = typeof updater === 'function' ? updater(clone(current.model_gateway)) : updater
      return { ...current, model_gateway: nextModel }
    })
    setSaveReady(false)
  }
  const setRuntime = (section, name, value) => {
    setDraft(current => ({ ...current, runtime: { ...current.runtime, [section]: { ...current.runtime[section], [name]: value } } }))
    setSaveReady(false)
  }
  const selectTab = id => {
    if (id === 'advanced' && draft) { setAdvancedJson(JSON.stringify(draft.model_gateway, null, 2)); setAdvancedError('') }
    setActiveTab(id)
  }
  const close = () => {
    if (dirty && !window.confirm('存在尚未保存的设置，确定关闭并放弃这些改动吗？')) return
    onClose()
  }

  const updateProvider = (name, patch) => setModelGateway(model => ({ ...model, providers: { ...model.providers, [name]: { ...model.providers[name], ...patch } } }))
  const addProvider = () => {
    const name = nextUniqueName('provider', providers)
    setModelGateway(model => ({ ...model, providers: { ...model.providers, [name]: { base_url: 'https://api.openai.com/v1', api_key_env: '', timeout_s: 30 } } }))
  }
  const renameProvider = (oldName, nextName) => {
    renameMappingKey(providers, oldName, nextName)
    setModelGateway(model => ({
      ...model,
      providers: renameMappingKey(model.providers, oldName, nextName),
      models: Object.fromEntries(Object.entries(model.models).map(([name, profile]) => [name, profile.provider === oldName ? { ...profile, provider: nextName } : profile])),
    }))
    setDraft(current => ({ ...current,
      runtime_keys: renameMappingKey(current.runtime_keys || {}, oldName, nextName),
      clear_runtime_keys: (current.clear_runtime_keys || []).map(name => name === oldName ? nextName : name),
    }))
  }
  const removeProvider = name => {
    const usedBy = Object.entries(models).filter(([, p]) => p.provider === name).map(([id]) => id)
    if (usedBy.length) { window.alert(`不能删除：Profile ${usedBy.join('、')} 仍引用此服务商。`); return }
    if (!window.confirm(`确定删除接口服务商“${name}”吗？`)) return
    setModelGateway(model => { const next = { ...model.providers }; delete next[name]; return { ...model, providers: next } })
    setDraft(current => ({ ...current,
      runtime_keys: Object.fromEntries(Object.entries(current.runtime_keys || {}).filter(([key]) => key !== name)),
      clear_runtime_keys: [...new Set([...(current.clear_runtime_keys || []), name])],
    }))
  }

  const updateProfile = (name, patch) => setModelGateway(model => ({ ...model, models: { ...model.models, [name]: { ...model.models[name], ...patch } } }))
  const addModel = () => {
    const name = nextUniqueName('model_profile', models)
    setModelGateway(model => ({ ...model, models: { ...model.models, [name]: { provider: Object.keys(providers)[0] || '', model: '', capabilities: [], max_concurrent: 4, max_retries: 2 } } }))
  }
  const replaceProfileRefs = (value, oldName, nextName) => {
    if (Array.isArray(value)) return value.map(item => item === oldName ? nextName : item)
    if (!value || typeof value !== 'object') return value
    return Object.fromEntries(Object.entries(value).map(([key, nested]) => [key, replaceProfileRefs(nested, oldName, nextName)]))
  }
  const renameModel = (oldName, nextName) => {
    renameMappingKey(models, oldName, nextName)
    setModelGateway(model => ({
    ...model, models: renameMappingKey(model.models, oldName, nextName),
    task_policies: replaceProfileRefs(model.task_policies, oldName, nextName),
    dlc_task_policies: replaceProfileRefs(model.dlc_task_policies, oldName, nextName),
    agent_task_policies: replaceProfileRefs(model.agent_task_policies, oldName, nextName),
    }))
  }
  const removeModel = name => {
    if (!window.confirm(`确定删除模型 Profile“${name}”吗？保存前校验会检查路由引用。`)) return
    setModelGateway(model => { const next = { ...model.models }; delete next[name]; return { ...model, models: next } })
  }

  const validate = async (prepareSave = false) => {
    setPhase('validating'); setMessage('正在由后端执行完整校验……'); setSaveReady(false)
    try {
      const result = await fetch(`${API_BASE}/settings/validate`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(draft) }).then(readResponse)
      setValidation(result); setValidationFingerprint(settingsFingerprint(draft)); setSaveReady(prepareSave)
      setPhase('valid'); setMessage(prepareSave ? '校验通过。请查看变更预览，然后点击“确认并保存”。' : '校验通过，可以安全保存。')
      return result
    } catch (error) {
      setValidation(null); setValidationFingerprint(''); setPhase('error'); setMessage(error.message)
      return null
    }
  }
  const save = async () => {
    if (!validationIsCurrent || !saveReady) { await validate(true); return }
    setPhase('saving'); setMessage('正在原子写入配置并热更新运行时……')
    try {
      const result = await fetch(`${API_BASE}/settings`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(draft) }).then(readResponse)
      const nextDocument = result.settings; const nextDraft = createSettingsDraft(nextDocument); const nextFingerprint = settingsFingerprint(nextDraft)
      setDocument(nextDocument); setDraft(nextDraft); setBaseline(nextFingerprint); setAdvancedJson(JSON.stringify(nextDraft.model_gateway, null, 2))
      setValidation({ valid: true, preview: result.preview, warnings: result.warnings || [] }); setValidationFingerprint(nextFingerprint); setSaveReady(false)
      setPhase('saved'); setMessage(`设置已保存并热更新。模型配置代次：${nextDocument.generation}。会话密钥输入已清空。`)
      onSaved?.(nextDocument)
    } catch (error) { setPhase('error'); setMessage(error.message) }
  }
  const applyAdvancedJson = () => {
    try { const parsed = parseAdvancedModelJson(advancedJson); setModelGateway(parsed); setAdvancedJson(JSON.stringify(parsed, null, 2)); setAdvancedError(''); setMessage('高级 JSON 已应用到表单草稿；尚未保存。') }
    catch (error) { setAdvancedError(error.message) }
  }
  const renderOverview = () => {
    const state = document?.runtime_state || {}
    return <div className="settings-page">
      <SectionHeader title="配置总览" description="集中查看配置来源、热重载代次、运行状态与路由优先级。" />
      <div className="settings-stat-grid">
        <div className="settings-stat"><strong>{document?.generation ?? '—'}</strong><span>模型配置代次</span></div>
        <div className="settings-stat"><strong>{state.active_dlc_generation ?? '—'}</strong><span>DLC 活跃代次</span></div>
        <div className="settings-stat"><strong>{state.agent_count ?? '—'}</strong><span>当前 Agent 数量</span></div>
        <div className="settings-stat"><strong>{Object.keys(models).length}</strong><span>模型 Profile</span></div>
      </div>
      <div className="settings-card-grid">
        <section className="settings-card"><h4>运行状态</h4>
          <div className="settings-kv"><span>世界主循环</span><StatusPill tone={state.simulation_running ? 'ok' : 'warn'}>{state.simulation_running ? '正在运行' : '未运行'}</StatusPill></div>
          <div className="settings-kv"><span>模拟状态</span><StatusPill tone={state.simulation_paused ? 'warn' : 'ok'}>{state.simulation_paused ? '已暂停' : '推进中'}</StatusPill></div>
          <div className="settings-kv"><span>配置文件</span><code>{document?.model_gateway?.source_path || 'config/models.json'}</code></div>
          <div className="settings-kv"><span>兼容模式</span><span>{document?.model_gateway?.legacy_mode ? '旧版 llm.json（保存后迁移）' : '多模型网关'}</span></div>
        </section>
        <section className="settings-card"><h4>密钥安全原则</h4>
          <p>API Key 明文不会通过设置 API 回传，也不会写入配置文件或浏览器存储。</p>
          <p>“本次会话密钥”仅驻留服务进程内存；服务重启后需要重新输入。</p>
          <p>长期密钥应使用 Provider 的环境变量名，由启动进程注入。</p>
        </section>
      </div>
      <section className="settings-card"><h4>模型路由优先级</h4>
        <ol className="settings-priority-list">{(schema?.routing_priority || []).map(item => <li key={item}>{item}</li>)}</ol>
        <p className="settings-callout">候选 Profile 数组的顺序就是故障回退顺序。Agent 级规则最精确，全局 default 是最后的常规兜底。</p>
      </section>
    </div>
  }

  const renderProviders = () => <div className="settings-page">
    <SectionHeader title="接口服务商" description="管理 OpenAI 兼容接口。密钥只在本次服务进程中生效，不落盘、不回显。" action={<button className="btn primary" type="button" onClick={addProvider}>＋ 新增服务商</button>} />
    {!Object.keys(providers).length && <EmptyState>至少需要一个接口服务商才能调用模型。</EmptyState>}
    {Object.entries(providers).map(([name, provider]) => {
      const configured = document?.model_gateway?.providers?.[name]?.api_key_configured
      const sessionValue = draft.runtime_keys?.[name] || ''
      const markedClear = draft.clear_runtime_keys?.includes(name)
      return <section className="settings-card settings-entity-card" key={name}>
        <div className="settings-entity-title"><NameEditor value={name} label="Provider 名称" onCommit={next => renameProvider(name, next)} />
          <div className="settings-inline-actions"><StatusPill tone={(sessionValue || (configured && !markedClear)) ? 'ok' : 'warn'}>{sessionValue ? '已输入会话密钥' : configured && !markedClear ? '密钥已配置' : '密钥未配置'}</StatusPill>
            <button className="settings-icon-btn danger" type="button" onClick={() => removeProvider(name)}>删除服务商</button></div>
        </div>
        <div className="settings-form-grid two">
          <TextSetting label="接口地址（Base URL）" description="OpenAI 兼容 API 根地址。更换已配置密钥的主机需要安全确认。" value={provider.base_url} onChange={value => updateProvider(name, { base_url: value })} placeholder="https://api.openai.com/v1" />
          <NumberSetting meta={fieldMap['providers.*.timeout_s']} value={provider.timeout_s} onChange={value => updateProvider(name, { timeout_s: value })} />
          <TextSetting label="API Key 环境变量名" description="这里只保存变量名，不读取或展示变量值；修改环境变量通常需要重启服务。" value={provider.api_key_env} onChange={value => updateProvider(name, { api_key_env: value })} placeholder="WORLD_SIM_API_KEY" />
          <TextSetting label="本次会话密钥" description="仅写入服务进程内存；输入框不会预填，保存成功后立即清空。" type="password" value={sessionValue} onChange={value => setDraft(current => ({ ...current, runtime_keys: { ...current.runtime_keys, [name]: value }, clear_runtime_keys: (current.clear_runtime_keys || []).filter(item => item !== name) }))} placeholder="输入新密钥" />
        </div>
        <label className="settings-checkbox danger-zone"><input type="checkbox" checked={markedClear} onChange={e => setDraft(current => ({ ...current,
          runtime_keys: { ...current.runtime_keys, [name]: '' },
          clear_runtime_keys: e.target.checked ? [...new Set([...(current.clear_runtime_keys || []), name])] : (current.clear_runtime_keys || []).filter(item => item !== name),
        }))} /><span><strong>保存时清除内存中的会话密钥</strong><small>不会删除操作系统环境变量中的密钥。</small></span></label>
      </section>
    })}
    <label className="settings-checkbox security-confirm"><input type="checkbox" checked={Boolean(draft.allow_secret_host_change)} onChange={e => setDraft(current => ({ ...current, allow_secret_host_change: e.target.checked }))} />
      <span><strong>我已核对并允许“已配置密钥的 Provider”更换接口主机</strong><small>错误地址可能把密钥发送给非预期服务器。仅在确认新主机可信时勾选。</small></span></label>
  </div>

  const renderModels = () => <div className="settings-page">
    <SectionHeader title="模型 Profile" description="把 Provider、真实模型 ID、能力与并发策略组合成可供路由复用的模型档案。" action={<button className="btn primary" type="button" onClick={addModel}>＋ 新增 Profile</button>} />
    {!Object.keys(models).length && <EmptyState>至少需要一个模型 Profile。删除被引用的 Profile 会在校验阶段被拒绝。</EmptyState>}
    {Object.entries(models).map(([name, profile]) => <section className="settings-card settings-entity-card" key={name}>
      <div className="settings-entity-title"><NameEditor value={name} label="Profile 名称" onCommit={next => renameModel(name, next)} /><button className="settings-icon-btn danger" type="button" onClick={() => removeModel(name)}>删除 Profile</button></div>
      <div className="settings-form-grid three">
        <label className="settings-field"><span className="settings-field-title">接口服务商</span><select value={profile.provider} onChange={e => updateProfile(name, { provider: e.target.value })}><option value="">请选择 Provider</option>{Object.keys(providers).map(id => <option value={id} key={id}>{id}</option>)}</select><MetaNote meta={fieldMap['models.*.provider']} /></label>
        <TextSetting label="模型 ID" description={fieldMap['models.*.model']?.description} value={profile.model} onChange={value => updateProfile(name, { model: value })} placeholder="gpt-5-mini / local-model" />
        <NumberSetting meta={fieldMap['models.*.max_concurrent']} value={profile.max_concurrent} onChange={value => updateProfile(name, { max_concurrent: value })} />
        <NumberSetting meta={fieldMap['models.*.max_retries']} value={profile.max_retries} onChange={value => updateProfile(name, { max_retries: value })} />
      </div>
      <div className="settings-capability-group"><span>能力标签</span><p>{fieldMap['models.*.capabilities']?.description}</p><div className="settings-chip-list">
        {(schema?.capabilities || []).map(cap => { const checked = (profile.capabilities || []).includes(cap.id); return <label className={`settings-chip ${checked ? 'selected' : ''}`} key={cap.id}><input type="checkbox" checked={checked} onChange={e => updateProfile(name, { capabilities: e.target.checked ? [...new Set([...(profile.capabilities || []), cap.id])] : (profile.capabilities || []).filter(item => item !== cap.id) })} />{cap.label}<small>{cap.id}</small></label> })}
      </div></div>
    </section>)}
  </div>

  const addDlcScope = () => {
    const id = nextUniqueName('dlc_id', draft.model_gateway.dlc_task_policies)
    setModelGateway(model => ({ ...model, dlc_task_policies: { ...model.dlc_task_policies, [id]: { 'agent.turn': Object.keys(models).slice(0, 1) } } }))
  }
  const addAgentScope = () => {
    const dlcId = window.prompt('请输入 DLC ID（例如 example_frontier）')?.trim()
    if (!dlcId) return
    const agents = draft.model_gateway.agent_task_policies[dlcId] || {}
    const agentId = window.prompt('请输入 Agent ID（例如 governor）', nextUniqueName('agent_id', agents))?.trim()
    if (!agentId) return
    if (agents[agentId]) { window.alert('该 DLC 下已存在同名 Agent 路由。'); return }
    setModelGateway(model => ({ ...model, agent_task_policies: { ...model.agent_task_policies, [dlcId]: { ...(model.agent_task_policies[dlcId] || {}), [agentId]: { 'agent.turn': Object.keys(models).slice(0, 1) } } } }))
  }

  const renderRouting = () => <div className="settings-page">
    <SectionHeader title="任务模型路由" description="配置全局、DLC 与 Agent 三级候选 Profile。候选顺序会被原样保留。" />
    <section className="settings-card"><h4>全局任务路由</h4><p>未命中 Agent 或 DLC 专用规则时使用。建议保留 default。</p>
      <PolicyRows policies={draft.model_gateway.task_policies} taskTypes={taskTypes} onChange={policies => setModelGateway(model => ({ ...model, task_policies: policies }))} />
    </section>
    <section className="settings-card"><SectionHeader title="DLC 级任务路由" description="仅影响指定 DLC，比全局规则优先。" action={<button className="btn secondary" type="button" onClick={addDlcScope}>＋ 添加 DLC 路由域</button>} />
      {!Object.keys(draft.model_gateway.dlc_task_policies).length && <EmptyState>尚无 DLC 专用路由。</EmptyState>}
      {Object.entries(draft.model_gateway.dlc_task_policies).map(([dlcId, policies]) => <div className="settings-scope" key={dlcId}>
        <div className="settings-scope-title"><NameEditor value={dlcId} label="DLC ID" onCommit={next => { const renamed = renameMappingKey(draft.model_gateway.dlc_task_policies, dlcId, next); setModelGateway(model => ({ ...model, dlc_task_policies: renamed })) }} />
          <button className="settings-icon-btn danger" type="button" onClick={() => setModelGateway(model => { const next = { ...model.dlc_task_policies }; delete next[dlcId]; return { ...model, dlc_task_policies: next } })}>删除路由域</button></div>
        <PolicyRows policies={policies} taskTypes={taskTypes} onChange={next => setModelGateway(model => ({ ...model, dlc_task_policies: { ...model.dlc_task_policies, [dlcId]: next } }))} />
      </div>)}
    </section>
    <section className="settings-card"><SectionHeader title="Agent 级任务路由" description="最精确的持久路由规则，由 DLC ID + Agent ID 组成。" action={<button className="btn secondary" type="button" onClick={addAgentScope}>＋ 添加 Agent 路由域</button>} />
      {!Object.keys(draft.model_gateway.agent_task_policies).length && <EmptyState>尚无 Agent 专用路由。</EmptyState>}
      {Object.entries(draft.model_gateway.agent_task_policies).flatMap(([dlcId, agents]) => Object.entries(agents).map(([agentId, policies]) => <div className="settings-scope" key={`${dlcId}:${agentId}`}>
        <div className="settings-agent-scope-title">
          <NameEditor value={dlcId} label="DLC ID" onCommit={next => { const renamed = renameMappingKey(draft.model_gateway.agent_task_policies, dlcId, next); setModelGateway(model => ({ ...model, agent_task_policies: renamed })) }} />
          <NameEditor value={agentId} label="Agent ID" onCommit={next => { const renamed = renameMappingKey(draft.model_gateway.agent_task_policies[dlcId], agentId, next); setModelGateway(model => ({ ...model, agent_task_policies: { ...model.agent_task_policies, [dlcId]: renamed } })) }} />
          <button className="settings-icon-btn danger" type="button" onClick={() => setModelGateway(model => { const nextAgents = { ...model.agent_task_policies[dlcId] }; delete nextAgents[agentId]; const root = { ...model.agent_task_policies }; if (Object.keys(nextAgents).length) root[dlcId] = nextAgents; else delete root[dlcId]; return { ...model, agent_task_policies: root } })}>删除路由域</button>
        </div>
        <PolicyRows policies={policies} taskTypes={taskTypes} onChange={next => setModelGateway(model => ({ ...model, agent_task_policies: { ...model.agent_task_policies, [dlcId]: { ...model.agent_task_policies[dlcId], [agentId]: next } } }))} />
      </div>))}
    </section>
  </div>
  const renderRounds = () => {
    const groups = [
      { title: '普通 Agent 默认预算', section: 'agent_defaults', description: '新建 DLC Agent、旧版 Agent 以及现有 Agent 热更新后的默认多轮预算。' },
      { title: '世界执行 Agent 预算', section: 'action_agent', description: '负责把动作意图转换为世界状态修改，通常需要更高预算。' },
    ]
    const names = ['max_rounds', 'max_tool_calls', 'max_turn_tokens', 'max_turn_seconds', 'no_progress_rounds']
    return <div className="settings-page"><SectionHeader title="Agent 多轮调用" description="限制一次任务的模型轮次、工具调用、Token 与现实时间，避免失控循环。" />
      {groups.map(group => <section className="settings-card" key={group.section}><h4>{group.title}</h4><p>{group.description}</p><div className="settings-form-grid two">
        {names.map(name => <NumberSetting key={name} meta={fieldMap[`${group.section}.${name}`]} value={draft.runtime[group.section][name]} step={name === 'max_turn_seconds' ? 0.1 : 1} onChange={value => setRuntime(group.section, name, value)} risk={name === 'max_turn_tokens' ? '过高会增加成本和上下文压力；过低可能截断复杂任务。' : undefined} />)}
      </div></section>)}
      <div className="settings-callout warning">停止条件采用“先到先停”：轮次、工具调用、Token、现实时间或连续无进展任一预算耗尽，当前任务都会结束。</div>
    </div>
  }

  const renderMemory = () => <div className="settings-page"><SectionHeader title="多轮记忆" description="控制短期对话压缩、结构化长期记忆容量与每轮召回提示词预算。" />
    <section className="settings-card"><h4>短期对话记忆</h4><div className="settings-form-grid two">
      {['compress_threshold', 'keep_recent', 'max_memory'].map(name => <NumberSetting key={name} meta={fieldMap[`agent_defaults.${name}`]} value={draft.runtime.agent_defaults[name]} onChange={value => setRuntime('agent_defaults', name, value)} />)}
      <NumberSetting meta={fieldMap['memory.compression_temperature']} value={draft.runtime.memory.compression_temperature} step={0.05} onChange={value => setRuntime('memory', 'compression_temperature', value)} />
    </div><div className="settings-callout">关联约束：压缩后保留消息数必须小于压缩阈值，否则后端拒绝保存。</div></section>
    <section className="settings-card"><h4>结构化长期记忆</h4><div className="settings-form-grid two">
      {['structured_memory_max_records', 'structured_memory_retrieval_limit', 'structured_memory_char_budget'].map(name => <NumberSetting key={name} meta={fieldMap[`agent_defaults.${name}`]} value={draft.runtime.agent_defaults[name]} onChange={value => setRuntime('agent_defaults', name, value)} />)}
    </div></section>
    <div className="settings-callout warning">更大的召回条数和字符预算不一定更聪明：无关记忆会稀释任务重点并增加上下文成本。</div>
  </div>

  const renderSimulation = () => <div className="settings-page"><SectionHeader title="世界模拟" description="调整游戏时间与现实时间换算，以及世界主循环检查频率。" />
    <section className="settings-card"><div className="settings-form-grid two">
      <NumberSetting meta={fieldMap['simulation.seconds_per_day']} value={draft.runtime.simulation.seconds_per_day} step={0.1} onChange={value => setRuntime('simulation', 'seconds_per_day', value)} risk="数值越小，游戏内一天越快；过小会让事件、期限和 Agent 工作密集触发。" />
      <NumberSetting meta={fieldMap['simulation.tick_interval']} value={draft.runtime.simulation.tick_interval} step={0.01} onChange={value => setRuntime('simulation', 'tick_interval', value)} risk="Tick 越小响应越及时，但 CPU、事件扫描和 WebSocket 广播压力越高。" />
    </div><div className="settings-sim-preview"><strong>当前草稿换算</strong><span>游戏内 1 天 = 现实 {draft.runtime.simulation.seconds_per_day} 秒</span><span>每秒约检查 {(1 / draft.runtime.simulation.tick_interval).toFixed(2)} 次世界状态</span></div></section>
  </div>

  const renderSecurity = () => {
    const environment = document?.environment || { items: [], alerts: [] }
    return <div className="settings-page"><SectionHeader title="安全与运维" description="只读展示启动环境注入的管理、指标、归档签名和告警配置。" />
      <div className="settings-callout warning">{environment.note || '环境变量不能在此修改；调整后需要按部署方式重启服务。'}</div>
      <section className="settings-card"><h4>服务与敏感凭证状态</h4><div className="settings-env-list">{(environment.items || []).map(item => <div className="settings-env-row" key={item.name}>
        <div><strong>{item.label}</strong><code>{item.name}</code><p>{item.description}</p></div><div>{item.kind === 'boolean' ? <StatusPill tone={item.value ? 'ok' : 'neutral'}>{item.value ? '已启用' : '未启用'}</StatusPill> : <StatusPill tone={item.configured ? 'ok' : 'warn'}>{item.configured ? '已配置' : '未配置'}</StatusPill>}{item.restart_required && <StatusPill>修改后重启</StatusPill>}</div>
      </div>)}</div></section>
      <section className="settings-card"><h4>告警阈值</h4><div className="settings-env-list">{(environment.alerts || []).map(item => <div className="settings-env-row" key={item.name}>
        <div><strong>{item.label}</strong><code>{item.name}</code><p>{item.description}</p></div><div><StatusPill tone={item.configured ? 'ok' : 'neutral'}>{item.configured ? `当前值：${item.value}` : '使用程序默认值'}</StatusPill><StatusPill>环境变量</StatusPill></div>
      </div>)}</div></section>
    </div>
  }

  const renderAdvanced = () => <div className="settings-page settings-advanced-page"><SectionHeader title="高级 JSON" description="只编辑模型网关五个公开配置段。运行时密钥、环境变量和敏感值不会出现。" />
    <div className="settings-callout warning">适合批量调整和复制路由。点击“应用到表单”后仍需后端校验并确认保存。</div>
    <textarea className="settings-json-editor" spellCheck="false" value={advancedJson} onChange={e => { setAdvancedJson(e.target.value); setAdvancedError('') }} />
    {advancedError && <div className="settings-error-box">{advancedError}</div>}
    <div className="settings-inline-actions"><button className="btn secondary" type="button" onClick={() => { setAdvancedJson(JSON.stringify(draft.model_gateway, null, 2)); setAdvancedError('') }}>从表单重新生成</button><button className="btn primary" type="button" onClick={applyAdvancedJson}>应用到表单草稿</button></div>
  </div>

  const renderContent = () => {
    if (!draft || !schema) return <div className="settings-loading">{message || '正在加载……'}</div>
    switch (activeTab) {
      case 'overview': return renderOverview()
      case 'providers': return renderProviders()
      case 'models': return renderModels()
      case 'routing': return renderRouting()
      case 'rounds': return renderRounds()
      case 'memory': return renderMemory()
      case 'simulation': return renderSimulation()
      case 'security': return renderSecurity()
      case 'advanced': return renderAdvanced()
      default: return renderOverview()
    }
  }

  return <div className="modal-overlay settings-overlay" onMouseDown={e => { if (e.target === e.currentTarget) close() }}>
    <div className="settings-center" role="dialog" aria-modal="true" aria-label="详细设置中心">
      <header className="settings-header"><div><span className="settings-eyebrow">WORLD SIMULATOR CONTROL PLANE</span><h2>详细设置中心</h2><p>模型、路由、Agent 多轮预算、记忆、世界模拟与运维状态</p></div>
        <div className="settings-header-status"><StatusPill tone={dirty ? 'warn' : 'ok'}>{dirty ? '有未保存改动' : '已与服务器同步'}</StatusPill><StatusPill>模型代次 {document?.generation ?? '—'}</StatusPill><button className="modal-close" type="button" onClick={close} aria-label="关闭">×</button></div>
      </header>
      <div className="settings-layout"><nav className="settings-nav" aria-label="设置分类">{tabs.map(tab => <button type="button" className={activeTab === tab.id ? 'active' : ''} key={tab.id} onClick={() => selectTab(tab.id)} title={tab.description}><span>{TAB_ICONS[tab.id] || '设'}</span><div><strong>{tab.label}</strong><small>{tab.description}</small></div></button>)}</nav><main className="settings-content">{renderContent()}</main></div>
      <footer className="settings-footer"><div className={`settings-save-status ${phase}`}><span className="settings-status-dot" /><div><strong>{phase === 'saving' ? '正在保存' : phase === 'validating' ? '正在校验' : phase === 'saved' ? '保存成功' : phase === 'error' ? '需要处理' : validationIsCurrent ? '校验成功' : dirty ? '草稿已修改' : '配置已同步'}</strong><small>{message}</small></div></div>
        {validationIsCurrent && <div className="settings-preview"><strong>{summarizeSettingsPreview(validation.preview)}</strong>{(validation.warnings || []).map(warning => <span key={`${warning.code}:${warning.path}`}>{warning.message}</span>)}{!(validation.warnings || []).length && <span>未发现安全或可用性警告。</span>}</div>}
        <div className="settings-footer-actions"><button className="btn ghost" type="button" onClick={close}>关闭</button><button className="btn secondary" type="button" onClick={() => validate(false)} disabled={!draft || phase === 'validating' || phase === 'saving'}>校验配置</button><button className="btn primary" type="button" onClick={save} disabled={!draft || !dirty || phase === 'validating' || phase === 'saving'}>{phase === 'saving' ? '保存中……' : validationIsCurrent && saveReady ? '确认并保存' : '保存前校验'}</button></div>
      </footer>
    </div>
  </div>
}

export default SettingsCenter