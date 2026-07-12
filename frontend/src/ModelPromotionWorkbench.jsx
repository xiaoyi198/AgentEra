import { useMemo, useRef, useState } from 'react'
import {
  createPromotionMapping,
  parsePromotionArtifact,
  parsePromotionMapping,
  promotionInputFingerprint,
} from './DLCStudioTools.js'

const PREVIEW_API = '/api/debug/model-promotion/preview'
const APPLY_API = '/api/debug/model-promotion/apply'

async function adminRequest(url, token, body) {
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Admin-Token': token,
    },
    body: JSON.stringify(body),
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
  </div>
}
