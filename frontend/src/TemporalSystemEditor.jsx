import { useMemo, useState } from 'react'
import { CausalConditionBuilder } from './DLCContentEditors.jsx'
import {
  TEMPORAL_BOUNDARY_TRIGGERS,
  TEMPORAL_CYCLE_KINDS,
  TEMPORAL_PAUSE_TRIGGERS,
  createTemporalItem,
  normalizeTimeSystemForEditor,
  serializeTimeSystem,
  summarizeTimeSystem,
} from './TemporalSystemTools.js'

function Section({ title, description, badge, children }) {
  return <section className="temporal-section">
    <header><div><h3>{title}</h3><p>{description}</p></div>{badge !== undefined && <span>{badge}</span>}</header>
    {children}
  </section>
}

function Toggle({ checked, onChange, label, help }) {
  return <label className="temporal-toggle"><input type="checkbox" checked={Boolean(checked)} onChange={event => onChange(event.target.checked)} /><span><strong>{label}</strong>{help && <small>{help}</small>}</span></label>
}

function Empty({ children }) {
  return <div className="temporal-empty">{children}</div>
}

function ItemToolbar({ title, code, index, length, onMove, onRemove }) {
  return <div className="temporal-item-toolbar">
    <div><strong>{title}</strong>{code && <code>{code}</code>}</div>
    <div><button type="button" disabled={index === 0} onClick={() => onMove(index, -1)}>↑ 上移</button><button type="button" disabled={index === length - 1} onClick={() => onMove(index, 1)}>↓ 下移</button><button type="button" className="danger" onClick={() => onRemove(index)}>删除</button></div>
  </div>
}

function BoundaryFilters({ value, calendar, seasons, cycles, onChange }) {
  const month = calendar.months.find(item => item.month_id === value.month_id)
  return <div className="temporal-grid temporal-filter-grid">
    <label>限定周期<select value={value.cycle_id || ''} onChange={event => onChange('cycle_id', event.target.value)}><option value="">不限周期</option>{cycles.map(item => <option key={item.cycle_id} value={item.cycle_id}>{item.display_name || item.cycle_id} · {item.cycle_id}</option>)}</select><small>仅 cycle_start / cycle_end 通常需要填写；留空表示所有周期。</small></label>
    <label>限定季节<select value={value.season_id || ''} onChange={event => onChange('season_id', event.target.value)}><option value="">不限季节</option>{seasons.map(item => <option key={item.season_id} value={item.season_id}>{item.display_name || item.season_id} · {item.season_id}</option>)}</select><small>仅 season_start / season_end 通常需要填写。</small></label>
    <label>限定月份<select value={value.month_id || ''} onChange={event => onChange('month_id', event.target.value)}><option value="">不限月份</option>{calendar.months.map(item => <option key={item.month_id} value={item.month_id}>{item.display_name || item.month_id} · {item.month_id}</option>)}</select><small>可将“每日开始”缩小为某个月内的每日。</small></label>
    <label>限定日期<input type="number" min="1" max={month?.days || calendar.months.reduce((sum, item) => sum + (Number(item.days) || 0), 0) || 3660} value={value.day ?? ''} onChange={event => onChange('day', event.target.value)} placeholder="留空表示不限" /><small>选择月份时表示月内日期；未选月份时也可匹配年度序日。</small></label>
  </div>
}

export default function TemporalSystemEditor({ content, onSaveContent, busy = false }) {
  const [form, setForm] = useState(() => normalizeTimeSystemForEditor(content.time_system))
  const [error, setError] = useState('')
  const summary = useMemo(() => summarizeTimeSystem(form), [form])
  const eventTypes = useMemo(() => [...new Set([
    ...(content.initial_events || []).map(item => item.event_type),
    ...(content.ongoing_events || []).map(item => item.event_type),
    ...(content.random_events || []).map(item => item.event_type),
    ...form.boundary_events.map(item => item.event_type),
  ].filter(Boolean))], [content, form.boundary_events])

  const setCalendar = (name, value) => setForm(current => ({ ...current, calendar: { ...current.calendar, [name]: value } }))
  const setItem = (section, index, name, value) => setForm(current => ({ ...current, [section]: current[section].map((item, itemIndex) => itemIndex === index ? { ...item, [name]: value } : item) }))
  const addItem = kind => {
    const section = ({ month: 'months', season: 'seasons', cycle: 'cycles', boundary_event: 'boundary_events', auto_pause: 'auto_pause_conditions' })[kind]
    if (kind === 'month') {
      const item = createTemporalItem(kind, form.calendar.months.length)
      setForm(current => ({ ...current, calendar: { ...current.calendar, months: [...current.calendar.months, item] } }))
      return
    }
    setForm(current => ({ ...current, [section]: [...current[section], createTemporalItem(kind, current[section].length)] }))
  }
  const removeMonth = index => setForm(current => {
    if (current.calendar.months.length <= 1) return current
    const months = current.calendar.months.filter((_, itemIndex) => itemIndex !== index)
    const startMonthId = months.some(item => item.month_id === current.calendar.start_month_id) ? current.calendar.start_month_id : months[0].month_id
    return { ...current, calendar: { ...current.calendar, months, start_month_id: startMonthId } }
  })
  const removeItem = (section, index) => setForm(current => ({ ...current, [section]: current[section].filter((_, itemIndex) => itemIndex !== index) }))
  const moveItem = (section, index, delta) => setForm(current => {
    const next = [...current[section]]
    const target = index + delta
    if (target < 0 || target >= next.length) return current
    ;[next[index], next[target]] = [next[target], next[index]]
    return { ...current, [section]: next }
  })
  const moveMonth = (index, delta) => setForm(current => {
    const next = [...current.calendar.months]
    const target = index + delta
    if (target < 0 || target >= next.length) return current
    ;[next[index], next[target]] = [next[target], next[index]]
    return { ...current, calendar: { ...current.calendar, months: next } }
  })
  const addWeekday = () => setCalendar('weekdays', [...form.calendar.weekdays, `星期 ${form.calendar.weekdays.length + 1}`])
  const setWeekday = (index, value) => setCalendar('weekdays', form.calendar.weekdays.map((item, itemIndex) => itemIndex === index ? value : item))
  const removeWeekday = index => {
    if (form.calendar.weekdays.length <= 1) return
    const weekdays = form.calendar.weekdays.filter((_, itemIndex) => itemIndex !== index)
    setForm(current => ({ ...current, calendar: { ...current.calendar, weekdays, start_weekday: weekdays.includes(current.calendar.start_weekday) ? current.calendar.start_weekday : weekdays[0] } }))
  }
  const save = () => {
    try {
      const timeSystem = serializeTimeSystem(form, content)
      setError('')
      onSaveContent({ ...content, time_system: timeSystem }, '历法与周期已保存')
    } catch (saveError) {
      setError(saveError.message)
    }
  }

  return <div className="temporal-editor">
    <div className="temporal-hero">
      <div><span>WORLD TIME CONTRACT</span><h2>历法、季节与周期</h2><p>由主 DLC 声明世界时间契约。模拟器只解释规则，不写死题材；依赖 DLC 可以引用世界日期，但不会争夺历法所有权。</p></div>
      <Toggle checked={form.enabled} onChange={value => setForm(current => ({ ...current, enabled: value }))} label={form.enabled ? '时间系统已启用' : '时间系统未启用'} help="停用时仍保留编辑内容，但运行时不会产生边界事件或自动暂停。" />
    </div>

    <div className="temporal-summary">
      <article><strong>{summary.year_days}</strong><span>每年天数</span></article>
      <article><strong>{summary.months}</strong><span>月份</span></article>
      <article><strong>{summary.seasons}</strong><span>季节</span></article>
      <article><strong>{summary.cycles}</strong><span>回合 / 会议 / 任务周期</span></article>
      <article><strong>{summary.boundary_events}</strong><span>边界事件</span></article>
      <article><strong>{summary.auto_pauses}</strong><span>自动暂停</span></article>
    </div>

    <Section title="一、历法基本信息" description="定义模拟第 0 天显示为什么日期，以及一年、星期如何循环。所有日期均由模拟时间确定，不使用现实系统时钟。" badge={form.calendar.calendar_id || '未填写 ID'}>
      <div className="temporal-grid temporal-grid-3">
        <label>历法 ID<input value={form.calendar.calendar_id || ''} onChange={event => setCalendar('calendar_id', event.target.value)} placeholder="frontier_calendar" /><small>稳定技术 ID；发布后不建议更改。</small></label>
        <label>中文显示名称<input value={form.calendar.display_name || ''} onChange={event => setCalendar('display_name', event.target.value)} placeholder="边境驻防历" /><small>显示在主界面日期旁。</small></label>
        <label>纪元名称<input value={form.calendar.epoch_label || ''} onChange={event => setCalendar('epoch_label', event.target.value)} placeholder="边境纪元" /><small>例如“公元”“帝国历”“灾后纪元”。</small></label>
        <label>起始年份<input type="number" min="-1000000" max="1000000" value={form.calendar.start_year ?? 1} onChange={event => setCalendar('start_year', event.target.value)} /><small>模拟第 0 天所属年份，可为负数。</small></label>
        <label>起始月份<select value={form.calendar.start_month_id || ''} onChange={event => setCalendar('start_month_id', event.target.value)}>{form.calendar.months.map(item => <option key={item.month_id} value={item.month_id}>{item.display_name || item.month_id} · {item.month_id}</option>)}</select><small>模拟第 0 天所属月份。</small></label>
        <label>起始日<input type="number" min="1" value={form.calendar.start_day ?? 1} onChange={event => setCalendar('start_day', event.target.value)} /><small>必须落在起始月份的有效天数内。</small></label>
        <label>起始星期<select value={form.calendar.start_weekday || ''} onChange={event => setCalendar('start_weekday', event.target.value)}>{form.calendar.weekdays.map((item, index) => <option key={`${item}-${index}`} value={item}>{item}</option>)}</select><small>模拟第 0 天显示的星期名称。</small></label>
      </div>
    </Section>

    <Section title="二、月份列表" description="月份可以拥有不同天数。列表顺序就是一年中的月份顺序，全年天数会自动求和。" badge={`${summary.months} 个月 / ${summary.year_days} 天`}>
      <div className="temporal-list-actions"><button type="button" onClick={() => addItem('month')}>＋ 添加月份</button></div>
      <div className="temporal-list">
        {form.calendar.months.map((month, index) => <article className="temporal-item" key={`month-${index}`}>
          <ItemToolbar title={`月份 ${index + 1}`} code={month.month_id} index={index} length={form.calendar.months.length} onMove={moveMonth} onRemove={removeMonth} />
          <div className="temporal-grid temporal-grid-3"><label>月份 ID<input value={month.month_id || ''} onChange={event => setForm(current => ({ ...current, calendar: { ...current.calendar, months: current.calendar.months.map((item, itemIndex) => itemIndex === index ? { ...item, month_id: event.target.value } : item) } }))} /></label><label>显示名称<input value={month.display_name || ''} onChange={event => setForm(current => ({ ...current, calendar: { ...current.calendar, months: current.calendar.months.map((item, itemIndex) => itemIndex === index ? { ...item, display_name: event.target.value } : item) } }))} /></label><label>天数<input type="number" min="1" max="3660" value={month.days ?? 30} onChange={event => setForm(current => ({ ...current, calendar: { ...current.calendar, months: current.calendar.months.map((item, itemIndex) => itemIndex === index ? { ...item, days: event.target.value } : item) } }))} /></label></div>
        </article>)}
      </div>
    </Section>

    <Section title="三、星期名称" description="可定义任意长度的星期循环，例如 7 天星期、10 天旬或题材专用轮值日。第一项是 week_start 边界。" badge={`${form.calendar.weekdays.length} 天循环`}>
      <div className="temporal-weekdays">{form.calendar.weekdays.map((weekday, index) => <div key={`weekday-${index}`}><span>{index + 1}</span><input value={weekday} onChange={event => setWeekday(index, event.target.value)} /><button type="button" disabled={form.calendar.weekdays.length <= 1} onClick={() => removeWeekday(index)}>×</button></div>)}<button type="button" className="add" onClick={addWeekday}>＋ 添加星期</button></div>
    </Section>

    <Section title="四、季节" description="季节按年度序日定义，允许跨年（开始日大于结束日），但任意一天不能同时属于两个季节。" badge={form.seasons.length}>
      <div className="temporal-list-actions"><button type="button" onClick={() => addItem('season')}>＋ 添加季节</button></div>
      {!form.seasons.length && <Empty>尚未定义季节。没有季节时，主界面只显示日期，不显示当前季节。</Empty>}
      <div className="temporal-list">{form.seasons.map((season, index) => <article className="temporal-item" key={`season-${index}`}>
        <ItemToolbar title={season.display_name || `季节 ${index + 1}`} code={season.season_id} index={index} length={form.seasons.length} onMove={(itemIndex, delta) => moveItem('seasons', itemIndex, delta)} onRemove={itemIndex => removeItem('seasons', itemIndex)} />
        <div className="temporal-grid temporal-grid-3"><label>季节 ID<input value={season.season_id || ''} onChange={event => setItem('seasons', index, 'season_id', event.target.value)} /></label><label>中文名称<input value={season.display_name || ''} onChange={event => setItem('seasons', index, 'display_name', event.target.value)} /></label><label>界面颜色<input value={season.color || ''} onChange={event => setItem('seasons', index, 'color', event.target.value)} placeholder="#c88b45 或 amber" /></label><label>年度开始日<input type="number" min="1" max={summary.year_days || 1} value={season.start_day ?? 1} onChange={event => setItem('seasons', index, 'start_day', event.target.value)} /></label><label>年度结束日<input type="number" min="1" max={summary.year_days || 1} value={season.end_day ?? 1} onChange={event => setItem('seasons', index, 'end_day', event.target.value)} /></label><label className="wide">季节说明<textarea rows="2" value={season.description || ''} onChange={event => setItem('seasons', index, 'description', event.target.value)} placeholder="说明该季节对题材和玩法的含义；具体效果仍应由因果规则声明。" /></label></div>
      </article>)}</div>
    </Section>

    <Section title="五、回合 / 会议 / 任务周期" description="周期基于模拟天数重复。间隔控制多久开启一次，持续天数控制何时结束，偏移决定第一次开始发生在第几天。" badge={form.cycles.length}>
      <div className="temporal-callout">例：每 30 天召开 2 天会议：间隔 30、持续 2、偏移 0；运行时会产生 cycle_start 和 cycle_end 两种确定性边界。</div>
      <div className="temporal-list-actions"><button type="button" onClick={() => addItem('cycle')}>＋ 添加周期</button></div>
      {!form.cycles.length && <Empty>尚未定义周期。可添加指挥回合、定期会议、巡防任务或自定义周期。</Empty>}
      <div className="temporal-list">{form.cycles.map((cycle, index) => <article className="temporal-item" key={`cycle-${index}`}>
        <ItemToolbar title={cycle.display_name || `周期 ${index + 1}`} code={cycle.cycle_id} index={index} length={form.cycles.length} onMove={(itemIndex, delta) => moveItem('cycles', itemIndex, delta)} onRemove={itemIndex => removeItem('cycles', itemIndex)} />
        <Toggle checked={cycle.enabled} onChange={value => setItem('cycles', index, 'enabled', value)} label="启用该周期" help="停用后不会生成开始/结束边界，但配置仍保留。" />
        <div className="temporal-grid temporal-grid-3"><label>周期 ID<input value={cycle.cycle_id || ''} onChange={event => setItem('cycles', index, 'cycle_id', event.target.value)} /></label><label>中文名称<input value={cycle.display_name || ''} onChange={event => setItem('cycles', index, 'display_name', event.target.value)} /></label><label>周期类型<select value={cycle.kind || 'custom'} onChange={event => setItem('cycles', index, 'kind', event.target.value)}>{TEMPORAL_CYCLE_KINDS.map(item => <option key={item.key} value={item.key}>{item.label}</option>)}</select></label><label>间隔天数<input type="number" min="1" value={cycle.period_days ?? 1} onChange={event => setItem('cycles', index, 'period_days', event.target.value)} /><small>两次 cycle_start 之间的天数。</small></label><label>持续天数<input type="number" min="1" max={cycle.period_days || 1} value={cycle.duration_days ?? 1} onChange={event => setItem('cycles', index, 'duration_days', event.target.value)} /><small>必须小于等于间隔天数。</small></label><label>首次偏移天数<input type="number" min="0" value={cycle.offset_days ?? 0} onChange={event => setItem('cycles', index, 'offset_days', event.target.value)} /><small>0 表示模拟第 0 天开始。</small></label><label>界面颜色<input value={cycle.color || ''} onChange={event => setItem('cycles', index, 'color', event.target.value)} placeholder="#4f86c6" /></label><label className="wide">周期说明<textarea rows="2" value={cycle.description || ''} onChange={event => setItem('cycles', index, 'description', event.target.value)} /></label></div>
      </article>)}</div>
    </Section>

    <Section title="六、时间边界事件" description="将日期或周期边界投影为通用世界事件，可唤醒 Agent，也可通过 Issue 类型进入玩家待办。事件 ID 由 generation、定义和边界确定，重试不会重复生成。" badge={form.boundary_events.length}>
      <div className="temporal-list-actions"><button type="button" onClick={() => addItem('boundary_event')}>＋ 添加边界事件</button></div>
      {!form.boundary_events.length && <Empty>尚未定义边界事件。时间仍会推进，但不会主动唤醒 Agent 或创建 Issue。</Empty>}
      <div className="temporal-list">{form.boundary_events.map((item, index) => <article className="temporal-item" key={`boundary-${index}`}>
        <ItemToolbar title={item.boundary_event_id || `边界事件 ${index + 1}`} code={item.trigger} index={index} length={form.boundary_events.length} onMove={(itemIndex, delta) => moveItem('boundary_events', itemIndex, delta)} onRemove={itemIndex => removeItem('boundary_events', itemIndex)} />
        <Toggle checked={item.enabled} onChange={value => setItem('boundary_events', index, 'enabled', value)} label="启用该边界事件" />
        <div className="temporal-grid temporal-grid-3"><label>边界事件 ID<input value={item.boundary_event_id || ''} onChange={event => setItem('boundary_events', index, 'boundary_event_id', event.target.value)} /></label><label>触发边界<select value={item.trigger || 'day_start'} onChange={event => setItem('boundary_events', index, 'trigger', event.target.value)}>{TEMPORAL_BOUNDARY_TRIGGERS.map(trigger => <option key={trigger.key} value={trigger.key}>{trigger.label} · {trigger.key}</option>)}</select></label><label>事件类型<input list="temporal-event-types" value={item.event_type || ''} onChange={event => setItem('boundary_events', index, 'event_type', event.target.value)} placeholder="cycle_notice" /></label><label>严重度 1～10<input type="number" min="1" max="10" value={item.severity ?? 5} onChange={event => setItem('boundary_events', index, 'severity', event.target.value)} /></label><label>Issue 类型<select value={item.issue_type_id || ''} onChange={event => setItem('boundary_events', index, 'issue_type_id', event.target.value)}><option value="">不自动创建 Issue</option>{(content.issue_types || []).map(issue => <option key={issue.issue_type_id} value={issue.issue_type_id}>{issue.display_name || issue.issue_type_id} · {issue.issue_type_id}</option>)}</select></label><label>目标 Agent ID<input value={item._target_agents_text || ''} onChange={event => setItem('boundary_events', index, '_target_agents_text', event.target.value)} placeholder="warden, clerk" /><small>逗号分隔；留空表示不定向唤醒。</small></label></div>
        <BoundaryFilters value={item} calendar={form.calendar} seasons={form.seasons} cycles={form.cycles} onChange={(name, value) => setItem('boundary_events', index, name, value)} />
        <label className="temporal-json">事件 Payload JSON<textarea rows="5" spellCheck="false" value={item._payload_text || '{}'} onChange={event => setItem('boundary_events', index, '_payload_text', event.target.value)} /><small>用于携带题材数据；最多 128 个顶层字段。时间边界、日期和周期身份会由运行时追加。</small></label>
      </article>)}</div>
      <datalist id="temporal-event-types">{eventTypes.map(item => <option key={item} value={item} />)}</datalist>
    </Section>

    <Section title="七、自动暂停条件" description="可在时间边界发生时暂停，也可持续评估世界条件。暂停原因会以中文展示给玩家；同 ID 的触发回执会在热重载时保留。" badge={form.auto_pause_conditions.length}>
      <div className="temporal-callout warning">条件触发建议保持“仅上升沿触发”，避免条件持续为真时每个 Tick 重复暂停。冷却天数和“仅触发一次”可进一步限制频率。</div>
      <div className="temporal-list-actions"><button type="button" onClick={() => addItem('auto_pause')}>＋ 添加自动暂停</button></div>
      {!form.auto_pause_conditions.length && <Empty>尚未配置自动暂停。游戏只会在玩家手动暂停时停止。</Empty>}
      <div className="temporal-list">{form.auto_pause_conditions.map((item, index) => <article className="temporal-item temporal-pause-item" key={`pause-${index}`}>
        <ItemToolbar title={item.display_name || `自动暂停 ${index + 1}`} code={item.pause_id} index={index} length={form.auto_pause_conditions.length} onMove={(itemIndex, delta) => moveItem('auto_pause_conditions', itemIndex, delta)} onRemove={itemIndex => removeItem('auto_pause_conditions', itemIndex)} />
        <div className="temporal-toggle-row"><Toggle checked={item.enabled} onChange={value => setItem('auto_pause_conditions', index, 'enabled', value)} label="启用" /><Toggle checked={item.once} onChange={value => setItem('auto_pause_conditions', index, 'once', value)} label="仅触发一次" /><Toggle checked={item.edge_triggered} onChange={value => setItem('auto_pause_conditions', index, 'edge_triggered', value)} label="仅上升沿触发" help="只对持续条件触发方式生效。" /></div>
        <div className="temporal-grid temporal-grid-3"><label>暂停 ID<input value={item.pause_id || ''} onChange={event => setItem('auto_pause_conditions', index, 'pause_id', event.target.value)} /></label><label>中文名称<input value={item.display_name || ''} onChange={event => setItem('auto_pause_conditions', index, 'display_name', event.target.value)} /></label><label>触发方式<select value={item.trigger || 'condition'} onChange={event => setItem('auto_pause_conditions', index, 'trigger', event.target.value)}>{TEMPORAL_PAUSE_TRIGGERS.map(trigger => <option key={trigger.key} value={trigger.key}>{trigger.label} · {trigger.key}</option>)}</select></label><label>条件组合<select value={item.mode || 'all'} onChange={event => setItem('auto_pause_conditions', index, 'mode', event.target.value)}><option value="all">全部成立（all）</option><option value="any">任一成立（any）</option></select><small>边界触发也可叠加条件过滤。</small></label><label>冷却天数<input type="number" min="0" step="0.1" value={item.cooldown_days ?? 0} onChange={event => setItem('auto_pause_conditions', index, 'cooldown_days', event.target.value)} /><small>距离上次触发至少经过多少模拟天。</small></label><label className="wide">玩家可见中文原因<textarea rows="2" value={item.reason || ''} onChange={event => setItem('auto_pause_conditions', index, 'reason', event.target.value)} placeholder="新的指挥回合已经开始，请处理待办。" /></label></div>
        {item.trigger !== 'condition' && <BoundaryFilters value={item} calendar={form.calendar} seasons={form.seasons} cycles={form.cycles} onChange={(name, value) => setItem('auto_pause_conditions', index, name, value)} />}
        <CausalConditionBuilder
          value={item.conditions}
          entities={content.entities || []}
          resources={content.resources || []}
          actors={content.actors || []}
          agents={content.agents || []}
          relationshipTypes={content.relationship_types || []}
          objectives={content.objectives || []}
          derivedMetrics={content.derived_metrics || []}
          ongoingEvents={content.ongoing_events || []}
          eventTypes={eventTypes}
          onChange={conditions => setItem('auto_pause_conditions', index, 'conditions', conditions)}
        />
      </article>)}</div>
    </Section>

    {error && <div className="temporal-error"><strong>无法保存时间系统</strong><span>{error}</span></div>}
    <div className="temporal-savebar"><div><strong>保存前会执行本地结构校验</strong><span>后端发布校验仍会再次检查跨 DLC 引用、Agent、Issue 类型和资源目录。</span></div><button type="button" className="primary" disabled={busy} onClick={save}>{busy ? '正在处理…' : '保存历法与周期'}</button></div>
  </div>
}
