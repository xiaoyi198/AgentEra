import { describeOutcomeChange, feedbackKindLabel } from './OutcomeFeedbackTools.js'

function FeedbackCard({ item }) {
  const changes = [...(item.changes || []), ...(item.metric_changes || [])]
  return (
    <article className={`outcome-card outcome-card-${item.kind}`}>
      <header>
        <span className="outcome-kind">{feedbackKindLabel(item.kind)}</span>
        <strong>{item.title || item.source?.display_name || '结果反馈'}</strong>
      </header>
      {item.summary && <p>{item.summary}</p>}
      {item.reason && <p className="outcome-reason"><span>原因</span>{item.reason}</p>}
      {changes.length > 0 && (
        <ul className="outcome-change-list">
          {changes.map((change, index) => (
            <li key={`${item.feedback_id}:change:${index}`}>
              <span>{describeOutcomeChange(change)}</span>
              {change.reason && change.reason !== item.reason && <small>{change.reason}</small>}
            </li>
          ))}
        </ul>
      )}
      {item.reveal_text && <blockquote>{item.reveal_text}</blockquote>}
    </article>
  )
}

export default function OutcomeFeedbackPanel({ state, onRefresh }) {
  const recent = state?.recent || []
  return (
    <section className="panel outcome-feedback-panel" aria-label="结果反馈">
      <div className="outcome-feedback-heading">
        <div>
          <h2>结果反馈</h2>
          <p>即时变化、延迟报告、结案总结与事后因果揭示</p>
        </div>
        <button type="button" className="btn ghost" onClick={onRefresh}>刷新</button>
      </div>
      <div className="outcome-feedback-status">
        <span>{state?.enabled ? '反馈已启用' : '反馈未启用'}</span>
        <span>待交付 {state?.pending_count || 0}</span>
      </div>
      <div className="outcome-feedback-live" aria-live="polite" aria-atomic="false">
        {recent.length === 0 && <div className="empty">当前还没有可展示的结果反馈。</div>}
        {recent.map(item => <FeedbackCard key={item.feedback_id} item={item} />)}
      </div>
    </section>
  )
}
