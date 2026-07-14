import { useEffect, useMemo, useState } from 'react'
import {
  DASHBOARD_REGIONS,
  DASHBOARD_WIDGETS,
  ENTITY_SECTIONS,
  ENTITY_VISIBLE_FIELDS,
  ISSUE_DETAIL_FIELDS,
  ISSUE_LIST_FIELDS,
  MAIN_VIEWS,
  SHELL_VIEWS,
  normalizeUIManifestResponse,
} from './UIManifestTools.js'

const TERM_FIELDS = [
  ['player', '玩家身份', '玩家在当前题材中的称呼，例如“指挥官”或“领主”。'],
  ['issue', '单个事项', '一条待处理事项的名称。'],
  ['issue_plural', '事项集合', '事项列表或待办区的标题。'],
  ['case', '事件档案', '事项对应的档案、案件或任务记录。'],
  ['decision', '处置方案', '玩家可选择的决定或处理方案。'],
  ['evidence', '证据', '支撑事项判断的信息。'],
  ['source', '来源', '事项、消息或证据的来源。'],
  ['deadline', '期限', '事项截止日期或剩余时间。'],
  ['message', '消息', '玩家发送或接收的消息。'],
  ['in_transit', '在途消息', '尚未送达的通信、命令或信使。'],
  ['world', '世界状态', '世界状态页面的题材化名称。'],
  ['entity', '单个实体', '一个地点、组织、人物或单位的统称。'],
  ['entities', '实体集合', '多个实体的统称。'],
  ['relations', '关系', '关系网络页面的名称。'],
  ['objective', '单个目标', '一个任务目标的称呼。'],
  ['objectives', '目标集合', '目标面板的标题。'],
  ['dashboard', '控制台', '主控制台视图的名称。'],
  ['debug', '调试', '调试视图名称。'],
  ['status', '状态', '通用状态字段。'],
  ['content', '内容', '通用内容字段。'],
  ['urgency', '紧急度', '事项优先级或紧迫程度。'],
  ['created_at', '创建时间', '事项创建时间字段。'],
  ['issue_type', '事项类型', '事项分类字段。'],
  ['inventory', '库存', '实体库存或资产区。'],
  ['metrics', '指标', '实体数值指标区。'],
  ['parameters', '参数', '实体扩展参数区。'],
  ['ongoing_events', '持续事件', '实体当前正在经历的事件。'],
  ['location', '位置', '实体所在位置。'],
  ['search', '搜索', '搜索输入或按钮文字。'],
  ['publish_message', '发布消息', '玩家发送消息的动作名称。'],
]

const VIEW_OPTIONS = {
  main: [
    ['dashboard', '控制台'], ['world', '世界状态'], ['relations', '关系网络'], ['debug', '调试'],
  ],
  shell: [
    ['gameplay', '游戏主界面'], ['editor', '内容编辑器'], ['dlc_studio', 'DLC Studio'], ['assistant', '智能助手'],
  ],
}
const WIDGET_OPTIONS = [
  ['objectives', '目标面板'], ['issue_list', '事项列表'], ['issue_detail', '事项详情'],
  ['player_message', '玩家消息输入'], ['in_transit', '在途消息'],
]
const REGION_OPTIONS = [
  ['top', '顶部'], ['left', '左栏'], ['main', '主栏'], ['right', '右栏'], ['bottom', '底部'],
]
const ISSUE_LIST_OPTIONS = [
  ['title', '标题'], ['source', '来源'], ['urgency', '紧急度'], ['created_at', '创建时间'],
  ['status', '状态'], ['issue_type', '事项类型'], ['case_id', '档案 ID'],
]
const ISSUE_DETAIL_OPTIONS = [
  ['title', '标题'], ['source', '来源'], ['summary', '摘要'], ['status', '状态'], ['case_id', '档案 ID'],
  ['deadline', '期限'], ['evidence', '证据'], ['decisions', '处置方案'], ['urgency', '紧急度'],
  ['created_at', '创建时间'], ['issue_type', '事项类型'], ['external_id', '外部 ID'],
]
const ENTITY_FIELD_OPTIONS = [
  ['id', 'ID'], ['type', '类型'], ['name', '名称'], ['description', '描述'],
  ['current_status', '当前状态'], ['updated_at', '更新时间'], ['version', '版本'],
]
const ENTITY_SECTION_OPTIONS = [
  ['summary', '摘要'], ['ongoing_events', '持续事件'], ['location', '位置'],
  ['inventory', '库存'], ['metrics', '指标'], ['parameters', '参数'],
]
const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/

function clone(value) {
  return JSON.parse(JSON.stringify(value))
}

function createUIManifestEditorValue(value) {
  const source = value && typeof value === 'object' && !Array.isArray(value) ? value : {}
  return clone(normalizeUIManifestResponse({ configured: true, manifest: source }).manifest)
}

function unsafeText(value, max) {
  return typeof value !== 'string' || !value.trim() || value.trim().length > max || /<|>|javascript:|data:/i.test(value)
}

function validateUIManifestEditorValue(value) {
  const issues = []
  if (unsafeText(value?.title, 80)) issues.push('界面标题必须是 1–80 个纯文本字符。')
  if (typeof value?.subtitle !== 'string' || value.subtitle.length > 240 || /<|>|javascript:|data:/i.test(value.subtitle)) issues.push('副标题最多 240 个纯文本字符。')
  if (unsafeText(value?.brand?.short_mark, 4)) issues.push('品牌短标识必须是 1–4 个纯文本字符。')
  for (const [key] of TERM_FIELDS) {
    if (unsafeText(value?.terminology?.[key], 40)) issues.push(`术语 ${key} 必须是 1–40 个纯文本字符。`)
  }
  const navSeen = new Set()
  for (const [index, item] of (value?.navigation || []).entries()) {
    if (!SAFE_ID.test(String(item.nav_id || ''))) issues.push(`导航 #${index + 1} 的 ID 不合法。`)
    if (navSeen.has(item.nav_id)) issues.push(`导航 ID 重复：${item.nav_id}`)
    navSeen.add(item.nav_id)
    const allowed = item.surface === 'main' ? MAIN_VIEWS : item.surface === 'shell' ? SHELL_VIEWS : new Set()
    if (!allowed.has(item.view)) issues.push(`导航 ${item.nav_id || `#${index + 1}`} 的页面能力不在白名单中。`)
    if (unsafeText(item.label, 40) || unsafeText(item.icon, 4)) issues.push(`导航 ${item.nav_id || `#${index + 1}`} 的标签或图标不合法。`)
  }
  if (!(value?.navigation || []).some(item => item.surface === 'main' && item.visible !== false)) issues.push('至少保留一个可见的游戏内主导航。')
  if (!(value?.navigation || []).some(item => item.surface === 'shell' && item.visible !== false)) issues.push('至少保留一个可见的应用级导航。')
  const widgetSeen = new Set()
  for (const [index, item] of (value?.dashboard?.widgets || []).entries()) {
    if (!SAFE_ID.test(String(item.widget_id || ''))) issues.push(`组件 #${index + 1} 的 ID 不合法。`)
    if (widgetSeen.has(item.widget_id)) issues.push(`组件 ID 重复：${item.widget_id}`)
    widgetSeen.add(item.widget_id)
    if (!DASHBOARD_WIDGETS.has(item.kind) || !DASHBOARD_REGIONS.has(item.region)) issues.push(`组件 ${item.widget_id || `#${index + 1}`} 使用了未知能力或区域。`)
  }
  return [...new Set(issues)]
}

function nextId(items, prefix, field) {
  const used = new Set(items.map(item => item[field]))
  let index = items.length + 1
  while (used.has(`${prefix}_${index}`)) index += 1
  return `${prefix}_${index}`
}

function ChoiceList({ title, help, options, selected, onChange }) {
  const values = new Set(Array.isArray(selected) ? selected : [])
  return <fieldset className="ui-manifest-choice-list">
    <legend>{title}</legend>
    <p>{help}</p>
    <div>{options.map(([key, label]) => <label key={key}>
      <input type="checkbox" checked={values.has(key)} onChange={event => {
        const next = options.map(([option]) => option).filter(option => option === key ? event.target.checked : values.has(option))
        onChange(next)
      }} />
      <span><strong>{label}</strong><code>{key}</code></span>
    </label>)}</div>
  </fieldset>
}

export default function UIManifestEditor({ content, onSaveContent, busy = false }) {
  const source = useMemo(() => content?.ui_manifest || {}, [content?.ui_manifest])
  const [form, setForm] = useState(() => createUIManifestEditorValue(source))
  const [jsonText, setJsonText] = useState(() => JSON.stringify(createUIManifestEditorValue(source), null, 2))
  const [jsonError, setJsonError] = useState('')

  useEffect(() => {
    const next = createUIManifestEditorValue(source)
    setForm(next)
    setJsonText(JSON.stringify(next, null, 2))
    setJsonError('')
  }, [source])

  const issues = useMemo(() => validateUIManifestEditorValue(form), [form])
  const update = updater => setForm(current => {
    const next = typeof updater === 'function' ? updater(current) : updater
    setJsonText(JSON.stringify(next, null, 2))
    setJsonError('')
    return next
  })
  const setField = (key, value) => update(current => ({ ...current, [key]: value }))
  const setTerm = (key, value) => update(current => ({ ...current, terminology: { ...current.terminology, [key]: value } }))
  const setNavigation = navigation => update(current => ({ ...current, navigation }))
  const setWidgets = widgets => update(current => ({ ...current, dashboard: { ...current.dashboard, widgets } }))
  const updateNav = (index, key, value) => setNavigation(form.navigation.map((item, itemIndex) => {
    if (itemIndex !== index) return item
    if (key === 'surface') return { ...item, surface: value, view: VIEW_OPTIONS[value][0][0] }
    return { ...item, [key]: value }
  }))
  const updateWidget = (index, key, value) => setWidgets(form.dashboard.widgets.map((item, itemIndex) => itemIndex === index ? { ...item, [key]: value } : item))
  const applyJson = () => {
    try {
      const parsed = JSON.parse(jsonText)
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('JSON 根节点必须是对象。')
      const next = createUIManifestEditorValue(parsed)
      setForm(next)
      setJsonText(JSON.stringify(next, null, 2))
      setJsonError('')
    } catch (error) {
      setJsonError(error.message)
    }
  }
  const resetDefaults = () => {
    if (!window.confirm('确定恢复通用默认界面配置吗？当前未保存修改会被覆盖。')) return
    update(createUIManifestEditorValue({}))
  }
  const save = () => {
    if (issues.length) return
    onSaveContent({ ...content, ui_manifest: clone(form) }, '界面配置已保存')
  }

  return <div className="ui-manifest-editor">
    <section className="ui-manifest-intro">
      <div><span>安全能力清单</span><h2>DLC 界面配置</h2><p>为当前 DLC 定义标题、题材术语、内置导航、控制台布局和可见字段。所有页面与组件都来自框架白名单，不能填写 URL、HTML、CSS、脚本或 React 组件名。</p></div>
      <button type="button" onClick={resetDefaults}>恢复通用默认值</button>
    </section>

    <section className="ui-manifest-section">
      <header><div><h3>1. 品牌与标题</h3><p>显示在游戏主界面顶栏。短标识建议使用 1 个汉字或 1–4 个字符。</p></div></header>
      <div className="ui-manifest-fields three">
        <label>界面标题<input maxLength="80" value={form.title} onChange={event => setField('title', event.target.value)} /><small>最多 80 字，不允许 HTML。</small></label>
        <label>副标题<input maxLength="240" value={form.subtitle} onChange={event => setField('subtitle', event.target.value)} /><small>说明当前世界或玩法，可留空。</small></label>
        <label>品牌短标识<input maxLength="4" value={form.brand.short_mark} onChange={event => update(current => ({ ...current, brand: { ...current.brand, short_mark: event.target.value } }))} /><small>用于顶栏图章或窄屏标识。</small></label>
      </div>
    </section>

    <section className="ui-manifest-section">
      <header><div><h3>2. 中文术语</h3><p>把框架通用概念替换成符合 DLC 题材的叫法。配置键固定，显示文字可自定义。</p></div></header>
      <div className="ui-manifest-terms">{TERM_FIELDS.map(([key, label, help]) => <label key={key}><span><strong>{label}</strong><code>{key}</code><small>{help}</small></span><input maxLength="40" value={form.terminology[key] || ''} onChange={event => setTerm(key, event.target.value)} /></label>)}</div>
    </section>

    <section className="ui-manifest-section">
      <header><div><h3>3. 导航</h3><p>“游戏内主导航”切换 React 内置视图；“应用级导航”只能打开框架预置页面。DLC 不能注入任意链接。</p></div><button type="button" onClick={() => setNavigation([...form.navigation, { nav_id: nextId(form.navigation, 'nav', 'nav_id'), surface: 'main', view: 'dashboard', label: '新导航', icon: '页', order: form.navigation.length * 10, visible: true }])}>＋ 添加导航</button></header>
      <div className="ui-manifest-table navigation">{form.navigation.map((item, index) => <article key={`${item.nav_id}-${index}`}>
        <label>导航 ID<input value={item.nav_id} onChange={event => updateNav(index, 'nav_id', event.target.value)} /></label>
        <label>作用范围<select value={item.surface} onChange={event => updateNav(index, 'surface', event.target.value)}><option value="main">游戏内主导航</option><option value="shell">应用级导航</option></select></label>
        <label>内置页面<select value={item.view} onChange={event => updateNav(index, 'view', event.target.value)}>{VIEW_OPTIONS[item.surface].map(([key, label]) => <option key={key} value={key}>{label} · {key}</option>)}</select></label>
        <label>显示名称<input maxLength="40" value={item.label} onChange={event => updateNav(index, 'label', event.target.value)} /></label>
        <label>图标<input maxLength="4" value={item.icon} onChange={event => updateNav(index, 'icon', event.target.value)} /></label>
        <label>排序<input type="number" min="-10000" max="10000" value={item.order} onChange={event => updateNav(index, 'order', Number(event.target.value))} /></label>
        <label className="ui-manifest-toggle"><input type="checkbox" checked={item.visible !== false} onChange={event => updateNav(index, 'visible', event.target.checked)} />显示</label>
        <button className="danger" type="button" onClick={() => setNavigation(form.navigation.filter((_, itemIndex) => itemIndex !== index))}>删除</button>
      </article>)}</div>
    </section>

    <section className="ui-manifest-section">
      <header><div><h3>4. 控制台布局与组件</h3><p>选择列布局，并把目标、事项和消息等框架组件放入指定区域。组件类型是固定白名单。</p></div><button type="button" onClick={() => setWidgets([...form.dashboard.widgets, { widget_id: nextId(form.dashboard.widgets, 'widget', 'widget_id'), kind: 'objectives', region: 'main', order: form.dashboard.widgets.length * 10, visible: true }])}>＋ 添加组件</button></header>
      <label className="ui-manifest-layout">控制台布局<select value={form.dashboard.layout} onChange={event => update(current => ({ ...current, dashboard: { ...current.dashboard, layout: event.target.value } }))}><option value="three_column">三栏布局</option><option value="two_column">双栏布局</option><option value="single_column">单栏布局</option></select><small>窄屏设备会自动折叠为单栏。</small></label>
      <div className="ui-manifest-table widgets">{form.dashboard.widgets.map((item, index) => <article key={`${item.widget_id}-${index}`}>
        <label>组件 ID<input value={item.widget_id} onChange={event => updateWidget(index, 'widget_id', event.target.value)} /></label>
        <label>组件类型<select value={item.kind} onChange={event => updateWidget(index, 'kind', event.target.value)}>{WIDGET_OPTIONS.map(([key, label]) => <option key={key} value={key}>{label} · {key}</option>)}</select></label>
        <label>显示区域<select value={item.region} onChange={event => updateWidget(index, 'region', event.target.value)}>{REGION_OPTIONS.map(([key, label]) => <option key={key} value={key}>{label} · {key}</option>)}</select></label>
        <label>排序<input type="number" min="-10000" max="10000" value={item.order} onChange={event => updateWidget(index, 'order', Number(event.target.value))} /></label>
        <label className="ui-manifest-toggle"><input type="checkbox" checked={item.visible !== false} onChange={event => updateWidget(index, 'visible', event.target.checked)} />显示</label>
        <button className="danger" type="button" onClick={() => setWidgets(form.dashboard.widgets.filter((_, itemIndex) => itemIndex !== index))}>删除</button>
      </article>)}</div>
    </section>

    <section className="ui-manifest-section">
      <header><div><h3>5. 事项页面字段</h3><p>控制事项列表和详情中展示哪些已有字段。勾选顺序即显示顺序。</p></div></header>
      <div className="ui-manifest-choices two">
        <ChoiceList title="列表字段" help="用于左侧事项列表或事项摘要。" options={ISSUE_LIST_OPTIONS} selected={form.issue_page.list_fields} onChange={list_fields => update(current => ({ ...current, issue_page: { ...current.issue_page, list_fields } }))} />
        <ChoiceList title="详情字段" help="用于选中事项后的完整详情区域。" options={ISSUE_DETAIL_OPTIONS} selected={form.issue_page.detail_fields} onChange={detail_fields => update(current => ({ ...current, issue_page: { ...current.issue_page, detail_fields } }))} />
      </div>
      <label className="ui-manifest-switch"><input type="checkbox" checked={form.issue_page.show_resolved !== false} onChange={event => update(current => ({ ...current, issue_page: { ...current.issue_page, show_resolved: event.target.checked } }))} /><span><strong>显示已解决事项</strong><small>关闭后，已解决的事项不会出现在主事项列表。</small></span></label>
    </section>

    <section className="ui-manifest-section">
      <header><div><h3>6. 实体详情</h3><p>控制世界状态页实体卡片的基础字段与扩展区块。</p></div></header>
      <div className="ui-manifest-choices two">
        <ChoiceList title="基础字段" help="显示在实体标题和基础信息区。" options={ENTITY_FIELD_OPTIONS} selected={form.entity_detail.visible_fields} onChange={visible_fields => update(current => ({ ...current, entity_detail: { ...current.entity_detail, visible_fields } }))} />
        <ChoiceList title="详情区块" help="显示持续事件、位置、库存、指标和参数等区块。" options={ENTITY_SECTION_OPTIONS} selected={form.entity_detail.sections} onChange={sections => update(current => ({ ...current, entity_detail: { ...current.entity_detail, sections } }))} />
      </div>
    </section>

    <details className="ui-manifest-json">
      <summary>7. 高级 JSON（专家模式）</summary>
      <p>用于批量复制或调整配置。点击“解析并应用”后才会覆盖上方表单；未知能力会在前端归一化时移除，后端保存时还会执行最终安全验证。</p>
      <textarea rows="24" spellCheck="false" value={jsonText} onChange={event => { setJsonText(event.target.value); setJsonError('') }} />
      {jsonError && <div className="ui-manifest-error">JSON 解析失败：{jsonError}</div>}
      <div><button type="button" onClick={() => setJsonText(JSON.stringify(form, null, 2))}>从表单重新生成</button><button className="primary" type="button" onClick={applyJson}>解析并应用</button></div>
    </details>

    {issues.length > 0 && <section className="ui-manifest-validation"><strong>保存前请修正以下问题：</strong><ul>{issues.map(issue => <li key={issue}>{issue}</li>)}</ul></section>}
    <div className="ui-manifest-save"><span>保存后会进入 Content 变更预览，可确认差异后再写入 DLC。</span><button className="primary" type="button" disabled={busy || issues.length > 0} onClick={save}>保存界面配置</button></div>
  </div>
}
