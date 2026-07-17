import { derivedMetricStatusLabel, formatDerivedMetricValue } from './DerivedMetricTools.js'

function DerivedMetricPanel({ state, onRefresh, title = '世界派生指标' }) {
  const metrics = state?.metrics || []
  return <section className="derived-metric-panel">
    <header>
      <div><span className="derived-metric-eyebrow">DETERMINISTIC WORLD PROJECTION</span><h2>{title}</h2></div>
      <div className="derived-metric-actions"><small>Generation {state?.generation || 0}</small><button type="button" className="btn ghost" onClick={onRefresh}>刷新</button></div>
    </header>
    {!state?.configured && <div className="derived-metric-empty">当前 DLC 未配置玩家可见的派生指标。</div>}
    {state?.configured && !metrics.length && <div className="derived-metric-empty">已配置指标，但当前没有 player 受众可读项。</div>}
    {!!metrics.length && <div className="derived-metric-grid">{metrics.map(metric => <article className={`derived-metric-card ${metric.status}`} key={metric.metric_id} title={metric.description}>
      <div className="derived-metric-card-title"><strong>{metric.display_name}</strong><span>{derivedMetricStatusLabel(metric.status)}</span></div>
      <div className="derived-metric-value">{formatDerivedMetricValue(metric)}{metric.unit && <small>{metric.unit}</small>}</div>
      <code>{metric.metric_id}</code>
      {metric.description && <p>{metric.description}</p>}
    </article>)}</div>}
  </section>
}

export default DerivedMetricPanel
