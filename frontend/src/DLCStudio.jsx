import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import './DLCStudio.css'
import { PersonaEditor, PromptEditor, StructuredContentEditor } from './DLCContentEditors.jsx'
import ModelPromotionWorkbench from './ModelPromotionWorkbench.jsx'
import {
  commitContentHistory,
  createContentExport,
  createContentHistory,
  normalizeImportedContent,
  redoContentHistory,
  summarizeContentDiff,
  undoContentHistory,
} from './DLCStudioTools.js'

const API = '/api/dlcs'
const MODEL_CONFIG_API = '/api/debug/model-config'
const MODEL_TASKS = [
  { key: 'agent.turn', label: 'Agent turn' },
  { key: 'router.ambiguous', label: 'Router ambiguous' },
  { key: 'action.simulation', label: 'Action simulation' },
  { key: 'memory.compress', label: 'Memory compress' },
  { key: 'tianji.chat', label: 'Tianji chat' },
]

const UI = {
  addDlc: '\uff0b \u65b0\u5efa DLC',
  addItem: '\uff0b \u65b0\u5efa',
  agentEnabled: '\u542f\u7528\u8be5 Agent',
  agentModel: 'Agent \u5185\u7f6e\u6a21\u578b',
  agentModelHint: '\u4f18\u5148\u7ea7\u6700\u9ad8\uff1b\u7559\u7a7a\u5219\u4f7f\u7528 Manifest \u7ed1\u5b9a\u6216\u5168\u5c40\u7b56\u7565\u3002',
  agentType: 'Agent \u7c7b\u578b',
  agents: 'Agent',
  asset: '\u8d44\u4ea7',
  bindingAgent: 'Manifest Agent \u7ed1\u5b9a',
  bindingTask: 'DLC \u4efb\u52a1\u7ed1\u5b9a',
  capabilities: '\u80fd\u529b',
  contentPackages: '\u5185\u5bb9\u5305',
  create: '\u521b\u5efa',
  createDraft: '\u65b0\u5efa DLC \u8349\u7a3f',
  currentStatus: '\u5f53\u524d\u72b6\u6001',
  defaultValue: '\u9ed8\u8ba4\u503c',
  delete: '\u5220\u9664',
  description: '\u63cf\u8ff0',
  dependencies: '\u4f9d\u8d56 DLC',
  dependencyConstraint: '\u7248\u672c\u7ea6\u675f',
  dependencyHint: '\u652f\u6301 >=1.0.0,<2.0.0\u3001^1.2.0\u3001~1.2.0 \u548c ||\u3002',
  disable: '\u505c\u7528',
  edit: '\u7f16\u8f91',
  embeddedOverride: '\u88ab Agent \u5185\u7f6e\u914d\u7f6e\u8986\u76d6',
  enable: '\u542f\u7528',
  enabled: '\u5df2\u542f\u7528',
  entities: '\u5b9e\u4f53',
  entityLocation: '\u6240\u5728\u5b9e\u4f53',
  entityType: '\u5b9e\u4f53\u7c7b\u578b',
  extraPrompt: '\u989d\u5916 Prompt',
  failed: '\u5931\u8d25',
  fullData: '\u5b8c\u6574\u6570\u636e',
  importContent: '\u5bfc\u5165 Content',
  exportContent: '\u5bfc\u51fa Content',
  undo: '\u64a4\u9500',
  redo: '\u91cd\u505a',
  reviewChanges: '\u786e\u8ba4 Content \u53d8\u66f4',
  applyChanges: '\u5e94\u7528\u5e76\u4fdd\u5b58',
  cancel: '\u53d6\u6d88',
  noContentChanges: '\u5185\u5bb9\u6ca1\u6709\u53d8\u5316',
  importFailed: '\u5bfc\u5165\u5931\u8d25',
  contentImported: 'Content \u5df2\u5bfc\u5165',
  contentUndone: 'Content \u5df2\u64a4\u9500',
  contentRedone: 'Content \u5df2\u91cd\u505a',
  added: '\u65b0\u589e',
  removed: '\u5220\u9664',
  changed: '\u4fee\u6539',
  changePreview: '\u53d8\u66f4\u540e Content \u9884\u89c8',
  globalFallback: '\u5168\u5c40 fallback',
  inherit: '\u7ee7\u627f\u4e0b\u4e00\u5c42',
  institution: '\u673a\u6784',
  invalidProfile: '\u5f53\u524d\u7ed1\u5b9a\u672a\u51fa\u73b0\u5728\u5df2\u914d\u7f6e profile \u4e2d',
  kind: '\u8d44\u6e90\u7c7b\u578b',
  migrate: '\u8fc1\u79fb Content',
  migrationReady: '\u8fc1\u79fb\u9884\u89c8\u5df2\u751f\u6210',
  latestSchema: '\u5df2\u662f\u6700\u65b0 Content Schema',
  legacyReadonly: '\u5185\u7f6e legacy DLC \u4e3a\u53ea\u8bfb\u517c\u5bb9\u5305\uff1b\u65b0\u5efa bundle-v1 DLC \u540e\u5373\u53ef\u4f7f\u7528\u5feb\u901f\u7f16\u8f91\u3002',
  location: '\u5730\u70b9',
  manifestSaved: 'Manifest \u5df2\u4fdd\u5b58',
  maxValue: '\u6700\u5927\u503c',
  metric: '\u6307\u6807',
  minValue: '\u6700\u5c0f\u503c',
  modelConfigUnavailable: '\u6a21\u578b\u914d\u7f6e\u4e0d\u53ef\u7528',
  modelPolicies: '\u6a21\u578b\u7b56\u7565',
  modelProfiles: '\u53ef\u7528 Model Profiles',
  name: '\u540d\u79f0',
  noAgents: '\u5f53\u524d DLC \u8fd8\u6ca1\u6709 Agent\u3002',
  noProfile: '\u672a\u914d\u7f6e',
  parameter: '\u53c2\u6570',
  personality: '\u4eba\u683c\u79cd\u5b50',
  priority: '\u4f18\u5148\u7ea7',
  priorityHelp: '\u5b9e\u9645\u9009\u62e9\u987a\u5e8f\uff1aAgent \u5185\u7f6e model_profile \u2192 Manifest Agent \u7ed1\u5b9a \u2192 DLC agent.turn \u7ed1\u5b9a \u2192 \u5168\u5c40 task policy\u3002',
  profileLoadHint: '\u4e0b\u62c9\u5217\u8868\u53ea\u6765\u81ea\u540e\u7aef\u5df2\u914d\u7f6e profile\u3002',
  provider: 'Provider',
  publish: '\u53d1\u5e03',
  published: 'DLC \u5df2\u53d1\u5e03',
  rank: '\u54c1\u7ea7',
  rawSaved: '\u5b8c\u6574\u5185\u5bb9\u5df2\u4fdd\u5b58',
  region: '\u533a\u57df',
  resource: '\u8d44\u6e90',
  resources: '\u8d44\u6e90',
  role: '\u89d2\u8272',
  safety: '\u5b89\u5168\u8fb9\u754c\uff1a\u6b64\u9875\u53ea\u9009\u62e9\u5df2\u914d\u7f6e\u7684 profile\uff0c\u4e0d\u4f1a\u8bfb\u53d6\u6216\u4fee\u6539 API Key\u3001Provider URL \u6216 secret env\u3002',
  save: '\u4fdd\u5b58',
  saveManifest: '\u4fdd\u5b58 Manifest',
  selectDlc: '\u9009\u62e9\u6216\u65b0\u5efa\u4e00\u4e2a DLC',
  statusValid: '\u6709\u6548',
  superior: '\u76f4\u5c5e\u4e0a\u7ea7',
  taskEffective: '\u5f53\u524d\u6709\u6548 profile',
  unit: '\u5355\u4f4d',
  validate: '\u6821\u9a8c',
  validated: '\u6821\u9a8c\u5b8c\u6210',
  valueAnchor: '\u4ef7\u503c\u951a',
  version: '\u7248\u672c',
}

async function request(url, options = {}) {
  const response = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`)
  return data
}

function normalizeModelBindings(value) {
  const source = value && typeof value === 'object' && !Array.isArray(value) ? value : {}
  const tasks = source.tasks && typeof source.tasks === 'object' && !Array.isArray(source.tasks) ? { ...source.tasks } : {}
  const agents = source.agents && typeof source.agents === 'object' && !Array.isArray(source.agents) ? { ...source.agents } : {}
  for (const [key, profile] of Object.entries(source)) {
    if (key !== 'tasks' && key !== 'agents' && typeof profile === 'string' && profile.trim()) tasks[key] ??= profile
  }
  return { tasks, agents }
}

const emptyEntity = { id: '', type: 'location', name: '', description: '', current_status: '' }
const emptyAgent = { id: '', name: '', rank: 1, location_id: '', role: 'official', agent_type: 'official', superior_id: '', personality_seed: '', model_profile: '', enabled: true, extra_prompt: '' }
const emptyResource = { name: '', unit: '', kind: 'asset', default_quantity: 0, min_quantity: 0, max_quantity: null, anchor_rate: 1 }

function Badge({ children, tone = '' }) {
  return <span className={`dlc-badge ${tone}`}>{children}</span>
}

function ProfileSelect({ value = '', profiles, onChange, disabled = false, inheritLabel = UI.inherit }) {
  return (
    <select value={value} disabled={disabled || !profiles.length} onChange={event => onChange(event.target.value)}>
      <option value="">{profiles.length ? inheritLabel : UI.noProfile}</option>
      {profiles.map(profile => <option key={profile} value={profile}>{profile}</option>)}
    </select>
  )
}

function ItemEditor({ kind, items, selected, onSelect, onSave, onDelete, entities, agents, modelProfiles }) {
  const templates = { entities: emptyEntity, agents: emptyAgent, resources: emptyResource }
  const keyName = kind === 'resources' ? 'name' : 'id'
  const title = { entities: UI.entities, agents: UI.agents, resources: UI.resources }[kind]
  const found = items.find(item => item[keyName] === selected)
  const [form, setForm] = useState(found ? { ...found } : { ...templates[kind] })

  const set = (name, value) => setForm(current => ({ ...current, [name]: value }))
  const key = String(form[keyName] || '').trim()
  const unknownAgentProfile = kind === 'agents' && form.model_profile && !modelProfiles.includes(form.model_profile)

  return (
    <div className="dlc-content-grid">
      <div className="dlc-item-list">
        <button className="dlc-new" onClick={() => { onSelect(''); setForm({ ...templates[kind] }) }}>{UI.addItem}{title}</button>
        {items.map(item => (
          <button key={item[keyName]} className={selected === item[keyName] ? 'active' : ''} onClick={() => onSelect(item[keyName])}>
            <strong>{item.name || item.id}</strong>
            <small>{kind === 'resources' ? item.kind : (item.type || item.agent_type)}</small>
          </button>
        ))}
      </div>
      <div className="dlc-form">
        <div className="dlc-form-title"><h3>{key ? `${UI.edit}${title}` : `${UI.addItem}${title}`}</h3>{key && <code>{key}</code>}</div>
        <label>{kind === 'resources' ? UI.name : 'ID'}<input value={form[keyName] || ''} disabled={Boolean(selected)} onChange={event => set(keyName, event.target.value)} placeholder={kind === 'resources' ? 'mana_crystal' : 'unique_id'} /></label>
        {kind === 'entities' && <>
          <label>{UI.name}<input value={form.name || ''} onChange={event => set('name', event.target.value)} /></label>
          <label>{UI.entityType}<select value={form.type || 'location'} onChange={event => set('type', event.target.value)}><option value="location">{UI.location}</option><option value="institution">{UI.institution}</option><option value="region">{UI.region}</option><option value="character">character</option><option value="faction">faction</option><option value="object">object</option></select></label>
          <label>{UI.currentStatus}<input value={form.current_status || ''} onChange={event => set('current_status', event.target.value)} /></label>
          <label>{UI.description}<textarea rows="5" value={form.description || ''} onChange={event => set('description', event.target.value)} /></label>
        </>}
        {kind === 'agents' && <>
          <div className="dlc-row"><label>{UI.name}<input value={form.name || ''} onChange={event => set('name', event.target.value)} /></label><label>{UI.rank}<input type="number" value={form.rank ?? 1} onChange={event => set('rank', Number(event.target.value))} /></label></div>
          <div className="dlc-row"><label>{UI.agentType}<select value={form.agent_type || 'official'} onChange={event => set('agent_type', event.target.value)}><option value="official">official</option><option value="institution">institution</option></select></label><label>{UI.role}<select value={form.role || 'official'} onChange={event => set('role', event.target.value)}><option value="official">official</option><option value="player">player</option><option value="eunuch">eunuch</option></select></label></div>
          <label>{UI.entityLocation}<input list="dlc-entities" value={form.location_id || ''} onChange={event => set('location_id', event.target.value)} /><datalist id="dlc-entities">{entities.map(item => <option key={item.id} value={item.id} />)}</datalist></label>
          <label>{UI.superior}<input list="dlc-agents" value={form.superior_id || ''} onChange={event => set('superior_id', event.target.value)} /><datalist id="dlc-agents">{agents.map(item => <option key={item.id} value={item.id} />)}</datalist></label>
          <label>{UI.personality}<input value={form.personality_seed || ''} onChange={event => set('personality_seed', event.target.value)} /></label>
          <label>{UI.agentModel}<ProfileSelect value={form.model_profile || ''} profiles={modelProfiles} onChange={value => set('model_profile', value)} /><small className="dlc-field-help">{UI.agentModelHint}</small>{unknownAgentProfile && <small className="dlc-field-warning">{UI.invalidProfile}: <code>{form.model_profile}</code></small>}</label>
          <label>{UI.extraPrompt}<textarea rows="4" value={form.extra_prompt || ''} onChange={event => set('extra_prompt', event.target.value)} /></label>
          <label className="dlc-check"><input type="checkbox" checked={form.enabled !== false} onChange={event => set('enabled', event.target.checked)} /> {UI.agentEnabled}</label>
        </>}
        {kind === 'resources' && <>
          <div className="dlc-row"><label>{UI.kind}<select value={form.kind || 'asset'} onChange={event => set('kind', event.target.value)}><option value="asset">{UI.asset}</option><option value="metric">{UI.metric}</option><option value="parameter">{UI.parameter}</option></select></label><label>{UI.unit}<input value={form.unit || ''} onChange={event => set('unit', event.target.value)} /></label></div>
          <div className="dlc-row"><label>{UI.defaultValue}<input type="number" value={form.default_quantity ?? 0} onChange={event => set('default_quantity', Number(event.target.value))} /></label>{form.kind === 'asset' && <label>{UI.valueAnchor}<input type="number" min="0.000001" step="any" value={form.anchor_rate ?? 1} onChange={event => set('anchor_rate', Number(event.target.value))} /></label>}</div>
          <div className="dlc-row"><label>{UI.minValue}<input type="number" value={form.min_quantity ?? ''} onChange={event => set('min_quantity', event.target.value === '' ? null : Number(event.target.value))} /></label><label>{UI.maxValue}<input type="number" value={form.max_quantity ?? ''} onChange={event => set('max_quantity', event.target.value === '' ? null : Number(event.target.value))} /></label></div>
        </>}
        <div className="dlc-form-actions">
          {selected && <button className="danger" onClick={() => onDelete(selected)}>{UI.delete}</button>}
          <button className="primary" disabled={!key} onClick={() => onSave(key, form)}>{UI.save}{title}</button>
        </div>
      </div>
    </div>
  )
}

function ModelPolicyEditor({ manifest, agents, modelConfig, modelConfigError, readOnly, onTaskBinding, onAgentBinding }) {
  const profiles = useMemo(() => Object.keys(modelConfig?.models || {}).sort(), [modelConfig])
  const bindings = normalizeModelBindings(manifest.model_bindings)
  const taskBinding = bindings.tasks['agent.turn'] || ''
  const globalPolicies = modelConfig?.task_policies || {}
  const configured = profile => !profile || profiles.includes(profile)

  const fallbackFor = task => {
    const candidates = globalPolicies[task]?.length ? globalPolicies[task] : (globalPolicies.default || [])
    return candidates.join(' \u2192 ') || UI.noProfile
  }

  return (
    <div className="dlc-model-policy">
      <div className="dlc-policy-intro">
        <div><h3>{UI.modelPolicies}</h3><p>{UI.priorityHelp}</p></div>
        <Badge tone="ok">{profiles.length} profiles</Badge>
      </div>
      <div className="dlc-safety-note"><strong>{UI.profileLoadHint}</strong><span>{UI.safety}</span></div>
      {modelConfigError && <div className="dlc-model-error"><strong>{UI.modelConfigUnavailable}</strong><span>{modelConfigError}</span></div>}

      <section className="dlc-policy-section">
        <h4>{UI.bindingTask}</h4>
        <div className="dlc-policy-table dlc-task-table">
          <div className="dlc-policy-head"><span>Task</span><span>DLC profile</span><span>{UI.globalFallback}</span></div>
          {MODEL_TASKS.map(task => {
            const value = bindings.tasks[task.key] || ''
            return <div className="dlc-policy-row" key={task.key}>
              <div><strong>{task.label}</strong><code>{task.key}</code></div>
              <div><ProfileSelect value={value} profiles={profiles} disabled={readOnly} onChange={profile => onTaskBinding(task.key, profile)} />{!configured(value) && <small className="dlc-field-warning">{UI.invalidProfile}: {value}</small>}</div>
              <small>{fallbackFor(task.key)}</small>
            </div>
          })}
        </div>
      </section>

      <section className="dlc-policy-section">
        <h4>{UI.bindingAgent}</h4>
        {!agents.length ? <div className="dlc-policy-empty">{UI.noAgents}</div> : <div className="dlc-policy-table dlc-agent-table">
          <div className="dlc-policy-head"><span>Agent</span><span>Manifest profile</span><span>{UI.taskEffective}</span></div>
          {agents.map(agent => {
            const embedded = typeof agent.model_profile === 'string' ? agent.model_profile : ''
            const value = bindings.agents[agent.id] || ''
            const effective = embedded || value || taskBinding || (globalPolicies['agent.turn'] || globalPolicies.default || [])[0] || UI.noProfile
            return <div className="dlc-policy-row" key={agent.id}>
              <div><strong>{agent.name || agent.id}</strong><code>{agent.id}</code></div>
              <div><ProfileSelect value={value} profiles={profiles} disabled={readOnly} onChange={profile => onAgentBinding(agent.id, profile)} inheritLabel={taskBinding ? `${UI.inherit}: ${taskBinding}` : UI.inherit} />{embedded && <small className="dlc-field-help">{UI.embeddedOverride}: <code>{embedded}</code></small>}{!configured(value) && <small className="dlc-field-warning">{UI.invalidProfile}: {value}</small>}</div>
              <div className="dlc-effective-profile"><Badge tone={configured(effective) ? 'on' : 'bad'}>{effective}</Badge><small>{embedded ? 'content.model_profile' : value ? 'manifest.agents' : taskBinding ? 'manifest.tasks' : 'global policy'}</small></div>
            </div>
          })}
        </div>}
      </section>

      <section className="dlc-policy-section">
        <h4>{UI.modelProfiles}</h4>
        <div className="dlc-profile-grid">
          {profiles.map(name => {
            const profile = modelConfig.models[name]
            return <article key={name}><div><strong>{name}</strong><Badge>{profile.provider}</Badge></div><code>{profile.model}</code><small>{UI.capabilities}: {(profile.capabilities || []).join(', ') || 'default'} &middot; concurrency {profile.max_concurrent} &middot; retries {profile.max_retries}</small></article>
          })}
        </div>
      </section>
    </div>
  )
}


function DependencyEditor({ manifest, packages, readOnly, onChange }) {
  const dependencies = Array.isArray(manifest.dependencies) ? manifest.dependencies : []
  const constraints = manifest.dependency_constraints && typeof manifest.dependency_constraints === 'object'
    ? manifest.dependency_constraints : {}
  const packageMap = new Map(packages.map(item => [item.manifest.id, item.manifest]))
  const available = packages.map(item => item.manifest.id).filter(id => id !== manifest.id && !dependencies.includes(id))
  const updateDependency = (currentId, nextId) => {
    if (!nextId || nextId === currentId) return
    const nextDependencies = dependencies.map(id => id === currentId ? nextId : id)
    const nextConstraints = { ...constraints }
    const previousConstraint = nextConstraints[currentId] || '*'
    delete nextConstraints[currentId]
    nextConstraints[nextId] = previousConstraint
    onChange({ ...manifest, dependencies: nextDependencies, dependency_constraints: nextConstraints })
  }
  const updateConstraint = (id, value) => onChange({
    ...manifest,
    dependency_constraints: { ...constraints, [id]: value },
  })
  const removeDependency = id => {
    const nextConstraints = { ...constraints }
    delete nextConstraints[id]
    onChange({
      ...manifest,
      dependencies: dependencies.filter(item => item !== id),
      dependency_constraints: nextConstraints,
    })
  }
  const addDependency = () => {
    const id = available[0]
    if (!id) return
    onChange({
      ...manifest,
      dependencies: [...dependencies, id],
      dependency_constraints: { ...constraints, [id]: '*' },
    })
  }
  return (
    <section className="dlc-dependencies">
      <div className="dlc-section-heading"><div><h2>{UI.dependencies}</h2><p>{UI.dependencyHint}</p></div><button disabled={readOnly || !available.length} onClick={addDependency}>{UI.addItem}</button></div>
      {!dependencies.length && <div className="dlc-dependency-empty">No dependencies</div>}
      {dependencies.map(id => {
        const target = packageMap.get(id)
        const options = packages.map(item => item.manifest.id).filter(candidate => candidate !== manifest.id && (candidate === id || !dependencies.includes(candidate)))
        return <div className="dlc-dependency-row" key={id}>
          <select value={id} disabled={readOnly} onChange={event => updateDependency(id, event.target.value)}>{options.map(option => <option key={option} value={option}>{option}</option>)}</select>
          <span className="dlc-dependency-version">{target ? `v${target.version}` : 'missing'}</span>
          <label><span>{UI.dependencyConstraint}</span><input value={constraints[id] || '*'} disabled={readOnly} onChange={event => updateConstraint(id, event.target.value)} placeholder=">=1.0.0,<2.0.0" /></label>
          <button className="danger" disabled={readOnly} onClick={() => removeDependency(id)}>{UI.delete}</button>
        </div>
      })}
    </section>
  )
}

function ContentDiffModal({ change, busy, onCancel, onApply }) {
  if (!change) return null
  const { diff } = change
  return (
    <div className="dlc-modal-backdrop" onClick={() => !busy && onCancel()}>
      <div className="dlc-modal dlc-diff-modal" onClick={event => event.stopPropagation()}>
        <div className="dlc-diff-title"><div><h2>{UI.reviewChanges}</h2><p>{change.label}</p></div><Badge>{change.operation}</Badge></div>
        <div className="dlc-diff-totals">
          <span className="diff-added">{UI.added}<strong>{diff.totals.added}</strong></span>
          <span className="diff-removed">{UI.removed}<strong>{diff.totals.removed}</strong></span>
          <span className="diff-changed">{UI.changed}<strong>{diff.totals.changed}</strong></span>
        </div>
        <div className="dlc-diff-list">
          {diff.sections.map(section => <section key={section.section}><h3>{section.section}</h3>{section.entries.map(entry => <div key={`${entry.type}:${entry.key}`} className={`diff-${entry.type}`}><Badge>{entry.type}</Badge><code>{entry.key}</code></div>)}</section>)}
        </div>
        <details className="dlc-json-preview"><summary>{UI.changePreview}</summary><pre>{JSON.stringify(change.nextContent, null, 2)}</pre></details>
        <div className="dlc-diff-actions"><button disabled={busy} onClick={onCancel}>{UI.cancel}</button><button className="primary" disabled={busy} onClick={onApply}>{UI.applyChanges}</button></div>
      </div>
    </div>
  )
}

export default function DLCStudio() {
  const [packages, setPackages] = useState([])
  const [selectedId, setSelectedId] = useState('')
  const [detail, setDetail] = useState(null)
  const [tab, setTab] = useState('entities')
  const [selectedItem, setSelectedItem] = useState('')
  const [createForm, setCreateForm] = useState({ id: '', name: '', version: '0.1.0' })
  const [showCreate, setShowCreate] = useState(false)
  const [manifest, setManifest] = useState({})
  const [raw, setRaw] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [modelConfig, setModelConfig] = useState(null)
  const [modelConfigError, setModelConfigError] = useState('')
  const [contentHistory, setContentHistory] = useState(() => createContentHistory({}))
  const [pendingChange, setPendingChange] = useState(null)
  const importInputRef = useRef(null)

  const notify = useCallback(text => { setMessage(text); window.setTimeout(() => setMessage(''), 2600) }, [])
  const loadList = useCallback(async () => {
    const list = await request(API)
    setPackages(list)
    setSelectedId(current => current || list[0]?.manifest.id || '')
  }, [])
  const loadDetail = useCallback(async (id, resetHistory = true) => {
    if (!id) return
    const data = await request(`${API}/${encodeURIComponent(id)}`)
    const normalizedContent = normalizeImportedContent(data.content)
    setDetail({ ...data, content: normalizedContent })
    setManifest(data.manifest)
    setRaw(JSON.stringify(normalizedContent, null, 2))
    setContentHistory(current => resetHistory
      ? createContentHistory(normalizedContent)
      : { ...current, present: normalizedContent })
    setPendingChange(null)
    setSelectedItem('')
  }, [])
  const loadModelConfig = useCallback(async () => {
    try {
      const data = await request(MODEL_CONFIG_API)
      if (data.error) throw new Error(data.error)
      setModelConfig(data)
      setModelConfigError('')
    } catch (error) {
      setModelConfig(null)
      setModelConfigError(error.message)
    }
  }, [])

  useEffect(() => { loadList().catch(error => notify(error.message)); loadModelConfig() }, [loadList, loadModelConfig, notify])
  useEffect(() => { if (selectedId) loadDetail(selectedId).catch(error => notify(error.message)) }, [loadDetail, notify, selectedId])

  const content = contentHistory.present
  const counts = useMemo(() => ({
    entities: content.entities?.length || 0,
    agents: content.agents?.length || 0,
    resources: content.resources?.length || 0,
    inventory: content.inventory?.length || 0,
    entity_values: content.entity_values?.length || 0,
    relations: content.relations?.length || 0,
    initial_events: content.initial_events?.length || 0,
    ongoing_events: content.ongoing_events?.length || 0,
    random_events: content.random_events?.length || 0,
    causal_event_rules: content.causal_event_rules?.length || 0,
    time_rules: content.time_rules?.length || 0,
    personas: Object.keys(content.personas || {}).length,
    prompts: Object.keys(content.prompts || {}).length,
  }), [content])
  const modelProfiles = useMemo(() => Object.keys(modelConfig?.models || {}).sort(), [modelConfig])

  const run = async (job, success, options = {}) => {
    setBusy(true)
    try {
      await job()
      await loadList()
      if (options.reloadDetail !== false && selectedId) await loadDetail(selectedId, false)
      notify(success)
    } catch (error) {
      notify(`${UI.failed}: ${error.message}`)
    } finally {
      setBusy(false)
    }
  }
  const create = () => run(async () => {
    const data = await request(API, { method: 'POST', body: JSON.stringify(createForm) })
    setSelectedId(data.manifest.id)
    setShowCreate(false)
    setCreateForm({ id: '', name: '', version: '0.1.0' })
  }, 'DLC draft created', { reloadDetail: false })
  const saveManifest = () => run(() => request(`${API}/${selectedId}/manifest`, { method: 'PUT', body: JSON.stringify(manifest) }), UI.manifestSaved)
  const validate = () => run(() => request(`${API}/${selectedId}/validate`, { method: 'POST' }), UI.validated)
  const publish = () => run(() => request(`${API}/${selectedId}/publish`, { method: 'POST' }), UI.published)
  const toggleEnabled = () => run(() => request(`${API}/${selectedId}/enabled`, { method: 'POST', body: JSON.stringify({ enabled: !manifest.enabled }) }), manifest.enabled ? 'DLC disabled' : 'DLC enabled')
  const stageContentChange = (nextValue, success, options = {}) => {
    try {
      const nextContent = normalizeImportedContent(nextValue)
      const diff = summarizeContentDiff(contentHistory.present, nextContent)
      if (!diff.hasChanges) {
        notify(UI.noContentChanges)
        return false
      }
      setPendingChange({
        nextContent,
        diff,
        success,
        label: options.label || success,
        operation: options.operation || 'save',
        historyAfter: options.historyAfter || null,
      })
      return true
    } catch (error) {
      notify(`${UI.failed}: ${error.message}`)
      return false
    }
  }
  const previewMigration = async () => {
    setBusy(true)
    try {
      const report = await request(`${API}/${selectedId}/migration/preview`, {
        method: 'POST',
        body: JSON.stringify({ target_schema_version: '1.0' }),
      })
      if (!report.changed) {
        notify(report.warnings?.[0] || UI.latestSchema)
        return
      }
      const stepLabel = (report.steps || []).map(step => step.description).join('; ')
      stageContentChange(report.content, UI.migrationReady, {
        operation: 'migration',
        label: `${report.source_schema_version} -> ${report.target_schema_version}${stepLabel ? `: ${stepLabel}` : ''}`,
      })
    } catch (error) {
      notify(`${UI.failed}: ${error.message}`)
    } finally {
      setBusy(false)
    }
  }

  const applyPendingChange = async () => {
    if (!pendingChange) return
    const change = pendingChange
    setBusy(true)
    try {
      const data = await request(`${API}/${selectedId}/content`, { method: 'PUT', body: JSON.stringify({ content: change.nextContent }) })
      const savedContent = normalizeImportedContent(data.content)
      setDetail({ ...data, content: savedContent })
      setManifest(data.manifest)
      setRaw(JSON.stringify(savedContent, null, 2))
      setContentHistory(current => {
        if (!change.historyAfter) return commitContentHistory(current, savedContent)
        return { ...change.historyAfter, present: savedContent }
      })
      setPendingChange(null)
      setSelectedItem('')
      await loadList()
      notify(change.success)
    } catch (error) {
      notify(`${UI.failed}: ${error.message}`)
    } finally {
      setBusy(false)
    }
  }
  const saveRaw = () => {
    try {
      stageContentChange(JSON.parse(raw), UI.rawSaved, { operation: 'raw' })
    } catch (error) {
      notify(`${UI.failed}: ${error.message}`)
    }
  }
  const saveStructuredContent = (nextContent, success) => stageContentChange(nextContent, success, { operation: tab })
  const saveItem = (key, data) => {
    const payload = { ...data }
    if (tab === 'agents') {
      const profile = typeof payload.model_profile === 'string' ? payload.model_profile.trim() : ''
      if (profile) payload.model_profile = profile
      else delete payload.model_profile
    }
    const keyName = tab === 'resources' ? 'name' : 'id'
    const items = [...(content[tab] || [])]
    const index = items.findIndex(item => item[keyName] === key)
    if (index >= 0) items[index] = payload
    else items.push(payload)
    return stageContentChange({ ...content, [tab]: items }, `${key} saved`, { operation: tab })
  }
  const deleteItem = key => {
    if (!window.confirm(`Delete ${key}?`)) return
    const keyName = tab === 'resources' ? 'name' : 'id'
    const items = (content[tab] || []).filter(item => item[keyName] !== key)
    stageContentChange({ ...content, [tab]: items }, `${key} deleted`, { operation: tab })
  }
  const stageUndo = () => {
    const historyAfter = undoContentHistory(contentHistory)
    if (historyAfter) stageContentChange(historyAfter.present, UI.contentUndone, { operation: 'undo', historyAfter })
  }
  const stageRedo = () => {
    const historyAfter = redoContentHistory(contentHistory)
    if (historyAfter) stageContentChange(historyAfter.present, UI.contentRedone, { operation: 'redo', historyAfter })
  }
  const exportContent = () => {
    const envelope = createContentExport(manifest, contentHistory.present)
    const blob = new Blob([`${JSON.stringify(envelope, null, 2)}\n`], { type: 'application/json;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    const safeId = String(manifest.id || selectedId || 'dlc').replace(/[^a-zA-Z0-9_-]+/g, '_')
    anchor.href = url
    anchor.download = `${safeId}-content.json`
    anchor.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 0)
  }
  const importContent = async event => {
    const file = event.target.files?.[0]
    if (!file) return
    try {
      const imported = normalizeImportedContent(JSON.parse(await file.text()))
      stageContentChange(imported, UI.contentImported, { operation: 'import', label: file.name })
    } catch (error) {
      notify(`${UI.importFailed}: ${error.message}`)
    } finally {
      event.target.value = ''
    }
  }
  const updateModelBinding = (section, key, profile) => setManifest(current => {
    const bindings = normalizeModelBindings(current.model_bindings)
    const mapping = { ...bindings[section] }
    if (profile) mapping[key] = profile
    else delete mapping[key]
    return { ...current, model_bindings: { ...bindings, [section]: mapping } }
  })

  const tabs = [
    { key: 'entities', label: `${UI.entities} ${counts.entities}` },
    { key: 'agents', label: `${UI.agents} ${counts.agents}` },
    { key: 'resources', label: `${UI.resources} ${counts.resources}` },
    { key: 'inventory', label: `\u5e93\u5b58 ${counts.inventory}` },
    { key: 'entity_values', label: `\u6307\u6807 ${counts.entity_values}` },
    { key: 'relations', label: `\u5173\u7cfb ${counts.relations}` },
    { key: 'initial_events', label: `\u521d\u59cb\u4e8b\u4ef6 ${counts.initial_events}` },
    { key: 'ongoing_events', label: `\u6301\u7eed\u4e8b\u4ef6 ${counts.ongoing_events}` },
    { key: 'random_events', label: `\u968f\u673a\u4e8b\u4ef6 ${counts.random_events}` },
    { key: 'causal_event_rules', label: `\u56e0\u679c\u4e8b\u4ef6\u94fe ${counts.causal_event_rules}` },
    { key: 'time_rules', label: `\u8017\u65f6\u89c4\u5219 ${counts.time_rules}` },
    { key: 'personas', label: `Persona ${counts.personas}` },
    { key: 'prompts', label: `Prompt ${counts.prompts}` },
    { key: 'models', label: UI.modelPolicies },
    { key: 'promotion', label: '\u664b\u7ea7\u5de5\u4f5c\u53f0' },
    { key: 'raw', label: UI.fullData },
  ]

  return <div className="dlc-studio">
    <header className="dlc-header"><div><span className="dlc-seal">D</span><h1>DLC Studio</h1><p>Content packages, entities, agents, anchored assets and model policies</p></div><button onClick={() => setShowCreate(true)}>{UI.addDlc}</button></header>
    {message && <div className="dlc-toast">{message}</div>}
    <main className="dlc-layout">
      <aside className="dlc-sidebar">
        <div className="dlc-sidebar-title">{UI.contentPackages} <span>{packages.length}</span></div>
        {packages.map(item => <button key={item.manifest.id} className={selectedId === item.manifest.id ? 'active' : ''} onClick={() => setSelectedId(item.manifest.id)}>
          <strong>{item.manifest.name}</strong><small>{item.manifest.id} &middot; v{item.manifest.version}</small><div><Badge tone={item.valid ? 'ok' : 'bad'}>{item.valid ? UI.statusValid : `${item.issues.length} issues`}</Badge><Badge>{item.manifest.status}</Badge>{item.manifest.enabled && <Badge tone="on">{UI.enabled}</Badge>}</div>
        </button>)}
      </aside>
      <section className="dlc-main">
        {!detail ? <div className="dlc-empty">{UI.selectDlc}</div> : <>
          <div className="dlc-manifest-bar"><div><input className="dlc-name" value={manifest.name || ''} disabled={!detail.editable} onChange={event => setManifest(current => ({ ...current, name: event.target.value }))} /><code>{manifest.id}</code></div><div className="dlc-actions"><button onClick={validate} disabled={busy}>{UI.validate}</button><button onClick={previewMigration} disabled={busy || !detail.editable}>{UI.migrate}</button><button onClick={saveManifest} disabled={busy || !detail.editable}>{UI.saveManifest}</button><button onClick={publish} disabled={busy || !detail.editable || manifest.status === 'published'}>{UI.publish}</button><button className={manifest.enabled ? 'danger' : 'primary'} onClick={toggleEnabled} disabled={busy || manifest.status !== 'published'}>{manifest.enabled ? UI.disable : UI.enable}</button></div></div>
          <div className="dlc-meta"><label>{UI.version}<input value={manifest.version || ''} disabled={!detail.editable} onChange={event => setManifest(current => ({ ...current, version: event.target.value }))} /></label><label>{UI.priority}<input type="number" value={manifest.priority ?? 0} disabled={!detail.editable} onChange={event => setManifest(current => ({ ...current, priority: Number(event.target.value) }))} /></label><label className="wide">{UI.description}<input value={manifest.description || ''} disabled={!detail.editable} onChange={event => setManifest(current => ({ ...current, description: event.target.value }))} /></label></div>
          <DependencyEditor manifest={manifest} packages={packages} readOnly={!detail.editable} onChange={setManifest} />
          {(detail.issues || []).length > 0 && <div className="dlc-issues">{detail.issues.map((issue, index) => <div key={`${issue.code}-${index}`}><code>{issue.code}</code><span>{issue.message}</span><small>{issue.path}</small></div>)}</div>}
          <div className="dlc-content-toolbar">
            <input ref={importInputRef} type="file" accept="application/json,.json" hidden onChange={importContent} />
            <button onClick={exportContent}>{UI.exportContent}</button>
            <button disabled={busy || !detail.editable} onClick={() => importInputRef.current?.click()}>{UI.importContent}</button>
            <span className="dlc-history-count">{contentHistory.past.length} / {contentHistory.future.length}</span>
            <button disabled={busy || !detail.editable || !contentHistory.past.length} onClick={stageUndo}>{UI.undo}</button>
            <button disabled={busy || !detail.editable || !contentHistory.future.length} onClick={stageRedo}>{UI.redo}</button>
          </div>
          <nav className="dlc-tabs">{tabs.map(item => <button key={item.key} className={tab === item.key ? 'active' : ''} onClick={() => { setTab(item.key); setSelectedItem('') }}>{item.label}</button>)}</nav>
          {tab === 'models' ? <ModelPolicyEditor manifest={manifest} agents={content.agents || []} modelConfig={modelConfig} modelConfigError={modelConfigError} readOnly={!detail.editable} onTaskBinding={(task, profile) => updateModelBinding('tasks', task, profile)} onAgentBinding={(agent, profile) => updateModelBinding('agents', agent, profile)} />
            : tab === 'promotion' ? <ModelPromotionWorkbench key={selectedId} dlcId={selectedId} agents={content.agents || []} modelConfig={modelConfig} modelConfigError={modelConfigError} onModelConfigReload={loadModelConfig} />
              : !detail.editable ? <div className="dlc-readonly">{UI.legacyReadonly}</div>
              : tab === 'raw' ? <div className="dlc-raw"><textarea value={raw} onChange={event => setRaw(event.target.value)} spellCheck="false" /><button className="primary" onClick={saveRaw} disabled={busy}>{UI.save}{UI.fullData}</button></div>
                : ['entities', 'agents', 'resources'].includes(tab) ? <ItemEditor key={`${tab}:${selectedItem}`} kind={tab} items={content[tab] || []} selected={selectedItem} onSelect={setSelectedItem} onSave={saveItem} onDelete={deleteItem} entities={content.entities || []} agents={content.agents || []} modelProfiles={modelProfiles} />
                  : tab === 'personas' ? <PersonaEditor key={`personas:${selectedItem}`} content={content} selected={selectedItem} onSelect={setSelectedItem} onSaveContent={saveStructuredContent} busy={busy} />
                    : tab === 'prompts' ? <PromptEditor key={`prompts:${selectedItem}`} content={content} selected={selectedItem} onSelect={setSelectedItem} onSaveContent={saveStructuredContent} busy={busy} />
                      : <StructuredContentEditor key={`${tab}:${selectedItem}`} kind={tab} content={content} selected={selectedItem} onSelect={setSelectedItem} onSaveContent={saveStructuredContent} busy={busy} />}
        </>}
      </section>
    </main>
    {showCreate && <div className="dlc-modal-backdrop" onClick={() => setShowCreate(false)}><div className="dlc-modal" onClick={event => event.stopPropagation()}><h2>{UI.createDraft}</h2><label>ID<input value={createForm.id} onChange={event => setCreateForm(current => ({ ...current, id: event.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, '') }))} placeholder="my_dlc" /></label><label>{UI.name}<input value={createForm.name} onChange={event => setCreateForm(current => ({ ...current, name: event.target.value }))} /></label><label>{UI.version}<input value={createForm.version} onChange={event => setCreateForm(current => ({ ...current, version: event.target.value }))} /></label><div><button onClick={() => setShowCreate(false)}>Cancel</button><button className="primary" disabled={!createForm.id || !createForm.name || busy} onClick={create}>{UI.create}</button></div></div></div>}
    <ContentDiffModal change={pendingChange} busy={busy} onCancel={() => setPendingChange(null)} onApply={applyPendingChange} />
  </div>
}
