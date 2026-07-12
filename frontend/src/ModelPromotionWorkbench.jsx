import { useEffect, useMemo, useRef, useState } from 'react'
import {
  createPromotionMapping,
  parsePromotionArtifact,
  parsePromotionMapping,
  promotionInputFingerprint,
  generationRollbackFingerprint,
  summarizeGenerationStatus,
} from './DLCStudioTools.js'

const PREVIEW_API = '/api/debug/model-promotion/preview'
const APPLY_API = '/api/debug/model-promotion/apply'
const GENERATIONS_API = '/api/debug/model-generations'
const ROLLBACK_PREVIEW_API = '/api/debug/model-config/rollback/preview'
const ROLLBACK_APPLY_API = '/api/debug/model-config/rollback/apply'

async function adminRequest(url, token, body) {
  const method = body === undefined ? 'GET' : 'POST'
  const response = await fetch(url, {
    method,
    headers: {
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      'X-Admin-Token': token,
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`)
  return data
}

function shortFingerprint(value) {
  const text = String(value || '')
  return text.length > 18 ? `${text.slice(0, 10)}…${text.slice(-8)}` : text || '—'
}

function SummaryCard({ label, value, code = false }) {
  return <article><span>{label}</span>{code ? <code title={String(value || '')}>{shortFingerprint(value)}</code> : <strong>{value ?? '—'}</strong>}</article>
}

function ModelGenerationPanel({ adminToken, generation, onModelConfigReload }) {
  const [observation, setObservation] = useState(null)
  const [sourceGeneration, setSourceGeneration] = useState(null)
  const [rollbackPreview, setRollbackPreview] = useState(null)
  const [previewFingerprint, setPreviewFingerprint] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const currentGeneration = observation?.currentGeneration ?? generation
  const rollbackFingerprint = useMemo(() => generationRollbackFingerprint({
    sourceGeneration,
    currentGeneration,
  }), [currentGeneration, sourceGeneration])
  const previewCurrent = Boolean(rollbackPreview && previewFingerprint === rollbackFingerprint)

  const refresh = async ({ quiet = false } = {}) => {
    if (!adminToken) return
    if (!quiet) setBusy(true)
    setError('')
    try {
      const payload = summarizeGenerationStatus(await adminRequest(GENERATIONS_API, adminToken))
      setObservation(payload)
      setSourceGeneration(current => payload.generations.some(item => item.generation === current && !item.current)
        ? current
        : payload.generations.find(item => !item.current)?.generation ?? null)
      setRollbackPreview(null)
      setPreviewFingerprint('')
      setStatus(`已刷新 ${payload.generations.length} 个可用 Generation。`)
    } catch (cause) {
      setError(cause.message)
    } finally {
      if (!quiet) setBusy(false)
    }
  }

  useEffect(() => {
    setRollbackPreview(null)
    setPreviewFingerprint('')
    if (adminToken && generation) refresh({ quiet: true })
    // Admin Token changes should not trigger background requests; operators explicitly refresh.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [generation])

  const previewRollback = async () => {
    if (!sourceGeneration || !currentGeneration) return
    setBusy(true)
    setError('')
    setStatus('')
    try {
      const result = await adminRequest(ROLLBACK_PREVIEW_API, adminToken, {
        source_generation: sourceGeneration,
        expected_current_generation: currentGeneration,
      })
      setRollbackPreview(result)
      setPreviewFingerprint(rollbackFingerprint)
      setStatus('回滚计划预览已通过；Generation 选择或当前代变化后必须重新预览。')
    } catch (cause) {
      setRollbackPreview(null)
      setPreviewFingerprint('')
      setError(cause.message)
    } finally {
      setBusy(false)
    }
  }

  const applyRollback = async () => {
    if (!previewCurrent || !window.confirm(`将 Generation ${sourceGeneration} 的配置恢复为新的 Generation ${rollbackPreview.next_generation}？`)) return
    setBusy(true)
    setError('')
    setStatus('')
    try {
      const result = await adminRequest(ROLLBACK_APPLY_API, adminToken, {
        source_generation: sourceGeneration,
        expected_current_generation: currentGeneration,
        expected_plan_fingerprint: rollbackPreview.plan_fingerprint,
      })
      await onModelConfigReload()
      const payload = summarizeGenerationStatus(await adminRequest(GENERATIONS_API, adminToken))
      setObservation(payload)
      setSourceGeneration(sourceGeneration)
      setRollbackPreview(null)
      setPreviewFingerprint('')
      setStatus(`安全回滚已作为新 Generation ${result.generation} 原子激活。`)
    } catch (cause) {
      setError(cause.message)
    } finally {
      setBusy(false)
    }
  }

  return <section className="dlc-promotion-panel dlc-generation-panel">
    <div className="dlc-section-heading">
      <div><h2>4. Generation 观测与安全回滚</h2><p>按代查看调用、错误、回退、延迟与 Token；历史配置始终加密，界面只接收安全摘要。</p></div>
      <button disabled={busy || !adminToken} onClick={() => refresh()}>刷新观测</button>
    </div>
    {error && <div className="dlc-model-error"><strong>Generation 操作失败</strong><span>{error}</span></div>}
    {status && <div className="dlc-promotion-status">{status}</div>}
    {observation && <>
      <div className="dlc-promotion-summary">
        <SummaryCard label="Current Generation" value={observation.currentGeneration} />
        <SummaryCard label="Retained" value={observation.generations.length} />
        <SummaryCard label="Encrypted archives" value={observation.persistedGenerationCount} />
        <SummaryCard label="Archive issues" value={observation.archiveIssueCount} />
      </div>
      <div className="dlc-generation-table-wrap"><table className="dlc-generation-table">
        <thead><tr><th>代</th><th>状态</th><th>Calls</th><th>Errors</th><th>Fallback</th><th>Tokens</th><th>Mean / Max ms</th><th>Active</th><th>Config</th></tr></thead>
        <tbody>{observation.generations.map(item => <tr key={item.generation} className={item.current ? 'current' : ''}>
          <td>{item.generation}</td><td>{item.current ? 'CURRENT' : 'RETAINED'}</td><td>{item.calls}</td><td>{item.errors}</td><td>{item.fallbacks}</td><td>{item.tokens}</td><td>{item.meanLatencyMs} / {item.maxLatencyMs}</td><td>{item.activeCalls}</td><td><code title={item.fingerprint}>{shortFingerprint(item.fingerprint)}</code></td>
        </tr>)}</tbody>
      </table></div>
      <div className="dlc-generation-rollback">
        <label>恢复来源<select value={sourceGeneration ?? ''} onChange={event => {
          setSourceGeneration(Number(event.target.value) || null)
          setRollbackPreview(null)
          setPreviewFingerprint('')
          setStatus('')
        }}><option value="">选择非当前 Generation</option>{observation.generations.filter(item => !item.current).map(item => <option key={item.generation} value={item.generation}>Generation {item.generation}</option>)}</select></label>
        <button disabled={busy || !adminToken || !sourceGeneration} onClick={previewRollback}>Preview rollback</button>
        <button className="primary" disabled={busy || !previewCurrent} onClick={applyRollback}>Apply rollback</button>
      </div>
      {rollbackPreview && !previewCurrent && <div className="dlc-promotion-stale">来源或当前 Generation 已变化，旧回滚 Preview 已失效。</div>}
      {previewCurrent && <div className="dlc-promotion-summary">
        <SummaryCard label="Source Generation" value={rollbackPreview.source_generation} />
        <SummaryCard label="Next Generation" value={rollbackPreview.next_generation} />
        <SummaryCard label="Providers add / remove / change" value={`${rollbackPreview.providers_added_count} / ${rollbackPreview.providers_removed_count} / ${rollbackPreview.providers_changed_count}`} />
        <SummaryCard label="Profiles add / remove / change" value={`${rollbackPreview.profiles_added_count} / ${rollbackPreview.profiles_removed_count} / ${rollbackPreview.profiles_changed_count}`} />
        <SummaryCard label="Task changes" value={rollbackPreview.task_policy_changes} />
        <SummaryCard label="DLC changes" value={rollbackPreview.dlc_policy_changes} />
        <SummaryCard label="Agent changes" value={rollbackPreview.agent_policy_changes} />
        <SummaryCard label="Plan fingerprint" value={rollbackPreview.plan_fingerprint} code />
        <SummaryCard label="Source config" value={rollbackPreview.source_config_fingerprint} code />
      </div>}
    </>}
  </section>
}

export default function ModelPromotionWorkbench({ dlcId, agents, modelConfig, modelConfigError, onModelConfigReload }) {
  const [promotionText, setPromotionText] = useState('')
  const [mappingText, setMappingText] = useState('')
  const [promotionId, setPromotionId] = useState(() => `promotion-${new Date().toISOString().slice(0, 10)}`)
  const [selectedAgents, setSelectedAgents] = useState([])
  const [adminToken, setAdminToken] = useState('')
  const [preview, setPreview] = useState(null)
  const [previewFingerprint, setPreviewFingerprint] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const promotionInputRef = useRef(null)
  const generation = Number.isInteger(modelConfig?.generation) ? modelConfig.generation : null
  const inputFingerprint = useMemo(() => promotionInputFingerprint({
    promotionText,
    mappingText,
    generation,
    dlcId,
  }), [dlcId, generation, mappingText, promotionText])
  const previewCurrent = Boolean(preview && previewFingerprint === inputFingerprint)

  const updatePromotion = value => {
    setPromotionText(value)
    setError('')
    setStatus('')
  }
  const updateMapping = value => {
    setMappingText(value)
    setError('')
    setStatus('')
  }
  const toggleAgent = agentId => setSelectedAgents(current => current.includes(agentId)
    ? current.filter(item => item !== agentId)
    : [...current, agentId])

  const importPromotion = async event => {
    const file = event.target.files?.[0]
    if (!file) return
    try {
      const text = await file.text()
      parsePromotionArtifact(text)
      updatePromotion(text)
      setStatus(`已导入 ${file.name}`)
    } catch (cause) {
      setError(cause.message)
    } finally {
      event.target.value = ''
    }
  }

  const generateMapping = () => {
    try {
      const mapping = createPromotionMapping({
        promotion: promotionText,
        dlcId,
        agentIds: selectedAgents,
        generation,
        promotionId,
      })
      updateMapping(JSON.stringify(mapping, null, 2))
      setStatus(`已为 ${Object.keys(mapping.cohort_targets).length} 个 cohort 生成映射`)
    } catch (cause) {
      setError(cause.message)
    }
  }

  const runPreview = async () => {
    setBusy(true)
    setError('')
    setStatus('')
    try {
      const promotion = parsePromotionArtifact(promotionText)
      const mapping = parsePromotionMapping(mappingText)
      const result = await adminRequest(PREVIEW_API, adminToken, { promotion, mapping })
      setPreview(result)
      setPreviewFingerprint(inputFingerprint)
      setStatus('晋级计划预览已通过；修改任何输入后必须重新预览。')
    } catch (cause) {
      setPreview(null)
      setPreviewFingerprint('')
      setError(cause.message)
    } finally {
      setBusy(false)
    }
  }

  const applyPromotion = async () => {
    if (!previewCurrent || !window.confirm(`应用晋级计划并将 Gateway Generation 提升到 ${preview.next_generation}？`)) return
    setBusy(true)
    setError('')
    setStatus('')
    try {
      const promotion = parsePromotionArtifact(promotionText)
      const mapping = parsePromotionMapping(mappingText)
      const result = await adminRequest(APPLY_API, adminToken, {
        promotion,
        mapping,
        expected_plan_fingerprint: preview.plan_fingerprint,
      })
      setPreview(null)
      setPreviewFingerprint('')
      await onModelConfigReload()
      setStatus(`晋级已原子应用，当前 Gateway Generation ${result.generation}。`)
    } catch (cause) {
      setError(cause.message)
    } finally {
      setBusy(false)
    }
  }

  return <div className="dlc-promotion-workbench">
    <div className="dlc-policy-intro">
      <div><h3>模型晋级工作台</h3><p>将已批准的 Promotion Gate 绑定到当前 DLC/Agent，先 Preview，再以同一计划指纹原子 Apply。</p></div>
      <span className={`dlc-badge ${generation ? 'ok' : 'bad'}`}>Generation {generation ?? '不可用'}</span>
    </div>
    <div className="dlc-safety-note"><strong>Admin Token 仅保存在当前页面内存中。</strong><span>不会写入浏览器存储、DLC、导出文件或请求正文；工作台只展示安全摘要与指纹。</span></div>
    {modelConfigError && <div className="dlc-model-error"><strong>模型配置不可用</strong><span>{modelConfigError}</span></div>}
    {error && <div className="dlc-model-error"><strong>操作失败</strong><span>{error}</span></div>}
    {status && <div className="dlc-promotion-status">{status}</div>}

    <div className="dlc-promotion-grid">
      <section className="dlc-promotion-panel">
        <div className="dlc-section-heading"><div><h2>1. Promotion Gate</h2><p>仅接受 schema 1.0、decision approved 且带 SHA-256 policy fingerprint 的 JSON。</p></div><button onClick={() => promotionInputRef.current?.click()}>导入 JSON</button></div>
        <input ref={promotionInputRef} hidden type="file" accept="application/json,.json" onChange={importPromotion} />
        <textarea value={promotionText} onChange={event => updatePromotion(event.target.value)} spellCheck="false" placeholder="粘贴已批准的 Promotion Gate artifact" />
      </section>

      <section className="dlc-promotion-panel">
        <div className="dlc-section-heading"><div><h2>2. Cohort Mapping</h2><p>快速映射默认只包含当前 DLC；Agent 必须显式勾选，避免策略范围过宽。</p></div><button disabled={!promotionText || !generation || !dlcId} onClick={generateMapping}>生成映射</button></div>
        <label>Promotion ID<input value={promotionId} onChange={event => setPromotionId(event.target.value)} /></label>
        <div className="dlc-promotion-agents">
          <span>Agent targets</span>
          <div>{agents.length ? agents.map(agent => <label key={agent.id}><input type="checkbox" checked={selectedAgents.includes(agent.id)} onChange={() => toggleAgent(agent.id)} />{agent.name || agent.id}<code>{agent.id}</code></label>) : <small>当前 DLC 没有 Agent；映射将只作用于 DLC 级策略。</small>}</div>
        </div>
        <textarea value={mappingText} onChange={event => updateMapping(event.target.value)} spellCheck="false" placeholder="生成或粘贴 cohort mapping JSON" />
      </section>
    </div>

    <section className="dlc-promotion-panel dlc-promotion-execute">
      <div className="dlc-promotion-auth">
        <label>Admin Token<input type="password" autoComplete="off" value={adminToken} onChange={event => setAdminToken(event.target.value)} placeholder="仅用于 X-Admin-Token header" /></label>
        <button disabled={busy || !adminToken || !promotionText || !mappingText || !generation} onClick={runPreview}>Preview</button>
        <button className="primary" disabled={busy || !adminToken || !previewCurrent} onClick={applyPromotion}>Apply</button>
      </div>
      {preview && !previewCurrent && <div className="dlc-promotion-stale">输入或 Gateway Generation 已变化，旧 Preview 已失效，请重新预览。</div>}
      {previewCurrent && <>
        <div className="dlc-promotion-summary">
          <SummaryCard label="Current Generation" value={preview.current_generation} />
          <SummaryCard label="Next Generation" value={preview.next_generation} />
          <SummaryCard label="Global changes" value={preview.global_task_changes} />
          <SummaryCard label="DLC changes" value={preview.dlc_task_changes} />
          <SummaryCard label="Agent changes" value={preview.agent_task_changes} />
          <SummaryCard label="DLC targets" value={preview.dlc_target_count} />
          <SummaryCard label="Agent targets" value={preview.agent_target_count} />
          <SummaryCard label="Plan fingerprint" value={preview.plan_fingerprint} code />
          <SummaryCard label="Current config" value={preview.current_config_fingerprint} code />
          <SummaryCard label="Candidate policy" value={preview.candidate_policy_fingerprint} code />
        </div>
      </>}
    </section>
    <ModelGenerationPanel adminToken={adminToken} generation={generation} onModelConfigReload={onModelConfigReload} />
  </div>
}
