export const MAIN_VIEWS = new Set(['dashboard', 'world', 'relations', 'debug'])
export const SHELL_VIEWS = new Set(['gameplay', 'editor', 'dlc_studio', 'assistant'])
export const DASHBOARD_WIDGETS = new Set(['objectives', 'derived_metrics', 'outcome_feedback', 'issue_list', 'issue_detail', 'player_message', 'in_transit'])
export const DASHBOARD_REGIONS = new Set(['top', 'left', 'main', 'right', 'bottom'])
export const ISSUE_LIST_FIELDS = new Set(['title', 'source', 'urgency', 'created_at', 'status', 'issue_type', 'case_id'])
export const ISSUE_DETAIL_FIELDS = new Set(['title', 'source', 'summary', 'status', 'case_id', 'deadline', 'evidence', 'decisions', 'urgency', 'created_at', 'issue_type', 'external_id'])
export const ENTITY_VISIBLE_FIELDS = new Set(['id', 'type', 'name', 'description', 'current_status', 'updated_at', 'version'])
export const ENTITY_SECTIONS = new Set(['summary', 'ongoing_events', 'location', 'inventory', 'metrics', 'parameters'])

export const SHELL_ROUTES = Object.freeze({
  gameplay: { href: '/', match: (path) => path === '/' || path === '' || path.endsWith('/index.html') },
  editor: { href: '/editor.html', match: (path) => path.includes('editor') },
  dlc_studio: { href: '/dlc.html', match: (path) => path.includes('dlc') },
  assistant: { href: '/tianji.html', match: (path) => path.includes('tianji') },
})

const DEFAULT_TERMS = Object.freeze({
  player: '玩家', issue: '事项', issue_plural: '待处理事项', case: '事件档案',
  decision: '处置方案', evidence: '证据', source: '来源', deadline: '期限',
  message: '消息', in_transit: '在途消息', world: '世界状态', entity: '实体',
  entities: '实体', relations: '关系', objective: '目标', objectives: '目标',
  dashboard: '控制台', debug: '调试', status: '状态', content: '内容',
  urgency: '紧急度', created_at: '创建时间', issue_type: '事项类型',
  inventory: '库存', metrics: '指标', parameters: '参数', ongoing_events: '持续事件',
  location: '位置', search: '搜索', publish_message: '发布消息',
})

const DEFAULT_NAVIGATION = Object.freeze([
  { nav_id: 'dashboard', surface: 'main', view: 'dashboard', label: '控制台', icon: '台', order: 0, visible: true },
  { nav_id: 'world', surface: 'main', view: 'world', label: '世界状态', icon: '界', order: 10, visible: true },
  { nav_id: 'relations', surface: 'main', view: 'relations', label: '关系', icon: '联', order: 20, visible: true },
  { nav_id: 'debug', surface: 'main', view: 'debug', label: '调试', icon: '析', order: 30, visible: true },
  { nav_id: 'gameplay', surface: 'shell', view: 'gameplay', label: '模拟', icon: '世', order: 0, visible: true },
  { nav_id: 'editor', surface: 'shell', view: 'editor', label: '内容编辑', icon: '编', order: 10, visible: true },
  { nav_id: 'dlc_studio', surface: 'shell', view: 'dlc_studio', label: 'DLC Studio', icon: '扩', order: 20, visible: true },
  { nav_id: 'assistant', surface: 'shell', view: 'assistant', label: '智能助手', icon: '智', order: 30, visible: true },
])

const DEFAULT_WIDGETS = Object.freeze([
  { widget_id: 'objectives', kind: 'objectives', region: 'top', order: 0, visible: true },
  { widget_id: 'derived_metrics', kind: 'derived_metrics', region: 'top', order: 5, visible: true },
  { widget_id: 'outcome_feedback', kind: 'outcome_feedback', region: 'right', order: 35, visible: true },
  { widget_id: 'issues', kind: 'issue_list', region: 'left', order: 10, visible: true },
  { widget_id: 'issue_detail', kind: 'issue_detail', region: 'main', order: 20, visible: true },
  { widget_id: 'message', kind: 'player_message', region: 'main', order: 30, visible: true },
  { widget_id: 'transit', kind: 'in_transit', region: 'right', order: 40, visible: true },
])

function plainText(value, fallback, max = 80) {
  if (typeof value !== 'string') return fallback
  const trimmed = value.trim()
  if (!trimmed || /<|>|javascript:|data:/i.test(trimmed)) return fallback
  return trimmed.slice(0, max)
}

function integerOrder(value, fallback) {
  const number = Number(value)
  return Number.isFinite(number) ? Math.max(-10000, Math.min(10000, Math.trunc(number))) : fallback
}

function defaultManifest() {
  return {
    configured: false,
    generation: 0,
    package_id: '',
    package_name: '',
    source: 'default',
    title: '世界模拟器',
    subtitle: '',
    brand: { short_mark: '世' },
    terminology: { ...DEFAULT_TERMS },
    navigation: DEFAULT_NAVIGATION.map(item => ({ ...item })),
    dashboard: { layout: 'three_column', widgets: DEFAULT_WIDGETS.map(item => ({ ...item })) },
    issue_page: {
      list_fields: ['title', 'source', 'urgency', 'created_at', 'status'],
      detail_fields: ['source', 'summary', 'status', 'case_id', 'deadline', 'evidence', 'decisions'],
      show_resolved: true,
    },
    entity_detail: {
      visible_fields: ['id', 'type', 'name', 'description', 'current_status'],
      sections: ['summary', 'ongoing_events', 'location', 'inventory', 'metrics', 'parameters'],
    },
  }
}

function safeUniqueList(value, allowed, fallback) {
  if (!Array.isArray(value)) return [...fallback]
  const seen = new Set()
  return value.filter(item => {
    if (typeof item !== 'string' || !allowed.has(item) || seen.has(item)) return false
    seen.add(item)
    return true
  })
}

export function normalizeUIManifestResponse(payload) {
  const fallback = defaultManifest()
  const envelope = payload && typeof payload === 'object' ? payload : {}
  const raw = envelope.manifest && typeof envelope.manifest === 'object' ? envelope.manifest : envelope
  const result = {
    ...fallback,
    configured: Boolean(envelope.configured),
    generation: Number.isFinite(Number(envelope.generation)) ? Number(envelope.generation) : 0,
    package_id: plainText(envelope.package_id, '', 64),
    package_name: plainText(envelope.package_name, '', 80),
    source: plainText(envelope.source, 'default', 20),
    title: plainText(raw.title, plainText(envelope.package_name, fallback.title, 80), 80),
    subtitle: plainText(raw.subtitle, '', 240),
  }
  const brand = raw.brand && typeof raw.brand === 'object' ? raw.brand : {}
  result.brand = { short_mark: plainText(brand.short_mark, result.title.slice(0, 1) || '世', 4) }
  const terms = raw.terminology && typeof raw.terminology === 'object' ? raw.terminology : {}
  result.terminology = { ...DEFAULT_TERMS }
  for (const key of Object.keys(DEFAULT_TERMS)) result.terminology[key] = plainText(terms[key], result.terminology[key], 40)

  if (Array.isArray(raw.navigation)) {
    const seen = new Set()
    result.navigation = raw.navigation.flatMap((item, index) => {
      if (!item || typeof item !== 'object') return []
      const surface = item.surface === 'shell' ? 'shell' : item.surface === 'main' ? 'main' : ''
      const allowed = surface === 'main' ? MAIN_VIEWS : surface === 'shell' ? SHELL_VIEWS : null
      const view = typeof item.view === 'string' ? item.view : ''
      const navId = plainText(item.nav_id, `nav_${index}`, 64)
      if (!allowed?.has(view) || seen.has(navId)) return []
      seen.add(navId)
      return [{
        nav_id: navId, surface, view,
        label: plainText(item.label, result.terminology[view] || view, 40),
        icon: plainText(item.icon, '•', 4),
        order: integerOrder(item.order, index * 10),
        visible: item.visible !== false,
      }]
    })
  }
  if (result.navigation.filter(item => item.surface === 'main' && item.visible).length === 0) {
    result.navigation.push({ ...DEFAULT_NAVIGATION[0] })
  }
  if (result.navigation.filter(item => item.surface === 'shell' && item.visible).length === 0) {
    result.navigation.push({ ...DEFAULT_NAVIGATION[4] })
  }

  const dashboard = raw.dashboard && typeof raw.dashboard === 'object' ? raw.dashboard : {}
  result.dashboard = {
    layout: ['three_column', 'two_column', 'single_column'].includes(dashboard.layout) ? dashboard.layout : 'three_column',
    widgets: DEFAULT_WIDGETS.map(item => ({ ...item })),
  }
  if (Array.isArray(dashboard.widgets)) {
    const seen = new Set()
    result.dashboard.widgets = dashboard.widgets.flatMap((item, index) => {
      if (!item || typeof item !== 'object' || !DASHBOARD_WIDGETS.has(item.kind) || !DASHBOARD_REGIONS.has(item.region)) return []
      const widgetId = plainText(item.widget_id, `widget_${index}`, 64)
      if (seen.has(widgetId)) return []
      seen.add(widgetId)
      return [{
        widget_id: widgetId, kind: item.kind, region: item.region,
        order: integerOrder(item.order, index * 10), visible: item.visible !== false,
      }]
    })
  }

  const issuePage = raw.issue_page && typeof raw.issue_page === 'object' ? raw.issue_page : {}
  result.issue_page = {
    list_fields: safeUniqueList(issuePage.list_fields, ISSUE_LIST_FIELDS, fallback.issue_page.list_fields),
    detail_fields: safeUniqueList(issuePage.detail_fields, ISSUE_DETAIL_FIELDS, fallback.issue_page.detail_fields),
    show_resolved: issuePage.show_resolved !== false,
  }
  const entityDetail = raw.entity_detail && typeof raw.entity_detail === 'object' ? raw.entity_detail : {}
  result.entity_detail = {
    visible_fields: safeUniqueList(entityDetail.visible_fields, ENTITY_VISIBLE_FIELDS, fallback.entity_detail.visible_fields),
    sections: safeUniqueList(entityDetail.sections, ENTITY_SECTIONS, fallback.entity_detail.sections),
  }
  result.manifest = {
    title: result.title, subtitle: result.subtitle, brand: result.brand,
    terminology: result.terminology, navigation: result.navigation,
    dashboard: result.dashboard, issue_page: result.issue_page, entity_detail: result.entity_detail,
  }
  return result
}

export function visibleNavigation(manifest, surface) {
  const allowed = surface === 'main' ? MAIN_VIEWS : SHELL_VIEWS
  return (manifest?.navigation || [])
    .filter(item => item?.surface === surface && item.visible !== false && allowed.has(item.view))
    .sort((a, b) => (a.order - b.order) || a.nav_id.localeCompare(b.nav_id))
}

export function visibleWidgets(manifest, region) {
  return (manifest?.dashboard?.widgets || [])
    .filter(item => item?.region === region && item.visible !== false && DASHBOARD_WIDGETS.has(item.kind))
    .sort((a, b) => (a.order - b.order) || a.widget_id.localeCompare(b.widget_id))
}

export function shellPages(manifest, pathname = '/') {
  return visibleNavigation(manifest, 'shell').flatMap(item => {
    const route = SHELL_ROUTES[item.view]
    return route ? [{ ...item, ...route, active: route.match(pathname) }] : []
  })
}
