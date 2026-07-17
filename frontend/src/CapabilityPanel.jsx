import { useState } from 'react'
import { buildCapabilityInvocation, capabilityFormDefaults } from './CapabilityTools.js'

const HANDLER_NAMES = {
  noop: '\u7a7a\u64cd\u4f5c',
  message: '\u5355\u76ee\u6807\u6d88\u606f',
  broadcast: '\u591a\u76ee\u6807\u5e7f\u64ad',
  world_action: '\u4e16\u754c\u884c\u52a8',
  contract_action: '\u5951\u7ea6 Action',
}

function ParameterInput({ parameter, value, onChange }) {
  if (parameter.type === 'boolean') {
    return (
      <label className="capability-check">
        <input type="checkbox" checked={Boolean(value)} onChange={event => onChange(event.target.checked)} />
        <span>{parameter.description || '\u542f\u7528\u8be5\u9009\u9879'}</span>
      </label>
    )
  }
  if ((parameter.options || []).length > 0) {
    return (
      <select value={value ?? ''} onChange={event => onChange(event.target.value)} required={parameter.required}>
        <option value="">请选择</option>
        {parameter.options.map(option => <option key={option} value={option}>{option}</option>)}
      </select>
    )
  }
  return (
    <input
      type={parameter.type === 'integer' || parameter.type === 'number' ? 'number' : 'text'}
      step={parameter.type === 'integer' ? 1 : parameter.type === 'number' ? 'any' : undefined}
      min={parameter.minimum ?? undefined}
      max={parameter.maximum ?? undefined}
      value={value ?? ''}
      required={parameter.required}
      placeholder={parameter.description || parameter.display_name || parameter.name}
      onChange={event => onChange(event.target.value)}
    />
  )
}

function CapabilityForm({ selected, state, gameplay, title, onInvoke }) {
  const [form, setForm] = useState(() => capabilityFormDefaults(selected))
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)

  const updateParameter = (name, value) => setForm(current => ({
    ...current,
    parameters: { ...current.parameters, [name]: value },
  }))

  const submit = async () => {
    if (busy) return
    setBusy(true)
    setResult(null)
    try {
      const payload = buildCapabilityInvocation(selected, form, gameplay, state.generation)
      const response = await onInvoke(selected.capability_id, payload)
      setResult(response)
      if (response?.ok) setForm(capabilityFormDefaults(selected))
    } catch (error) {
      setResult({ ok: false, error: error?.message || String(error) })
    } finally {
      setBusy(false)
    }
  }

  const target = selected.target || { kind: 'none', mode: 'none', allowed_ids: [] }
  const targetRequired = target.mode === 'required' || target.mode === 'multiple'
  const targetMultiple = target.mode === 'multiple'
  const contentInput = selected.input || {}

  return (
    <div className="capability-panel">
      <div className="capability-heading">
        <div>
          <h3>{title}</h3>
          <small>Generation {state.generation} · {state.capabilities.length} 项</small>
        </div>
      </div>
      <div className="capability-summary">
        <strong>{selected.display_name || selected.capability_id}</strong>
        <span className={`capability-handler handler-${selected.handler}`}>{HANDLER_NAMES[selected.handler] || selected.handler}</span>
        <code>{selected.capability_id}</code>
        {selected.description && <p>{selected.description}</p>}
        {selected.handler === 'contract_action' && <small>绑定 Action：{selected.action_id}</small>}
      </div>
      {contentInput.content_required || selected.handler !== 'noop' ? (
        <label className="capability-field">
          <span>{contentInput.content_label || '\u5185\u5bb9'}{contentInput.content_required ? ' *' : ''}</span>
          <textarea
            value={form.content}
            required={contentInput.content_required}
            placeholder={contentInput.content_placeholder || '\u8bf7\u8f93\u5165\u8be6\u7ec6\u5185\u5bb9'}
            onChange={event => setForm(current => ({ ...current, content: event.target.value }))}
          />
        </label>
      ) : null}
      {target.kind !== 'none' && (
        <label className="capability-field">
          <span>{target.kind === 'agent' ? '\u76ee\u6807 Agent' : '\u76ee\u6807\u5b9e\u4f53'}{targetRequired ? ' *' : ''}</span>
          {(target.allowed_ids || []).length > 0 && !targetMultiple ? (
            <select value={form.targets} required={targetRequired} onChange={event => setForm(current => ({ ...current, targets: event.target.value }))}>
              {!targetRequired && <option value="">自动选择 / 留空</option>}
              {target.allowed_ids.map(id => <option key={id} value={id}>{id}</option>)}
            </select>
          ) : (
            <input
              value={form.targets}
              required={targetRequired}
              placeholder={targetMultiple ? '\u591a\u4e2a ID \u7528\u9017\u53f7\u5206\u9694' : '\u8f93\u5165 Agent / \u5b9e\u4f53 ID\uff0c\u53ef\u9009\u65f6\u53ef\u7559\u7a7a'}
              onChange={event => setForm(current => ({ ...current, targets: event.target.value }))}
            />
          )}
          <small>目标类型：{target.kind}；模式：{target.mode}{(target.allowed_ids || []).length > 0 ? `\uff1b\u5141\u8bb8 ${target.allowed_ids.join('\u3001')}` : ''}</small>
        </label>
      )}
      {(selected.parameters || []).map(parameter => (
        <label key={parameter.name} className="capability-field">
          <span>{parameter.display_name || parameter.name}{parameter.required ? ' *' : ''}</span>
          <ParameterInput parameter={parameter} value={form.parameters?.[parameter.name]} onChange={value => updateParameter(parameter.name, value)} />
          {parameter.description && parameter.type !== 'boolean' && <small>{parameter.description}</small>}
        </label>
      ))}
      {selected.handler !== 'noop' && (
        <label className="capability-field capability-urgency">
          <span>紧急度</span>
          <select value={form.urgency} onChange={event => setForm(current => ({ ...current, urgency: event.target.value }))}>
            <option value="normal">普通</option>
            <option value="urgent">紧急</option>
            <option value="critical">危急</option>
          </select>
        </label>
      )}
      <button
        className="btn decree capability-submit"
        onClick={submit}
        disabled={busy || (contentInput.content_required && !form.content.trim()) || (targetRequired && !form.targets.trim())}
      >{busy ? '\u6267\u884c\u4e2d...' : `\u6267\u884c\u300c${selected.display_name || selected.capability_id}\u300d`}</button>
      {result && (
        <div className={`capability-result ${result.ok ? 'success' : 'error'}`}>
          {result.ok ? '\u80fd\u529b\u5df2\u63d0\u4ea4\u5e76\u8fdb\u5165\u4e16\u754c\u65f6\u5e8f\u3002' : `\u6267\u884c\u5931\u8d25\uff1a${result.detail?.message || result.detail?.code || result.error || '\u672a\u77e5\u9519\u8bef'}`}
          {result.invocation_id && <code>{result.invocation_id}</code>}
        </div>
      )}
    </div>
  )
}

export default function CapabilityPanel({ state, gameplay, title = '\u53ef\u7528\u80fd\u529b', onInvoke }) {
  const capabilities = state?.capabilities || []
  const [selectedId, setSelectedId] = useState('')
  const selected = capabilities.find(item => item.capability_id === selectedId) || capabilities[0] || null

  if (!selected) {
    return <div className="empty">当前 DLC 没有向玩家开放能力。可在 DLC Studio 的「能力」页签中新建，并勾选 player audience。</div>
  }

  return (
    <>
      <label className="capability-picker">
        <span>选择能力</span>
        <select value={selected.capability_id} onChange={event => setSelectedId(event.target.value)}>
          {capabilities.map(item => <option key={item.capability_id} value={item.capability_id}>{item.display_name || item.capability_id}</option>)}
        </select>
      </label>
      <CapabilityForm
        key={`${state?.generation || 0}:${selected.capability_id}`}
        selected={selected}
        state={state}
        gameplay={gameplay}
        title={title}
        onInvoke={onInvoke}
      />
    </>
  )
}
