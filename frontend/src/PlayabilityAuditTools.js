const DEFAULT_TOTAL = 7

function text(value, fallback = '') {
  const normalized = typeof value === 'string' ? value.trim() : ''
  return normalized || fallback
}

export function normalizePlayabilityAudit(report) {
  if (!report || typeof report !== 'object' || Array.isArray(report)) {
    return {
      available: false,
      auditVersion: '',
      playable: false,
      passedCount: 0,
      totalCount: DEFAULT_TOTAL,
      percent: 0,
      checks: [],
    }
  }

  const checks = Array.isArray(report.checks)
    ? report.checks.filter(item => item && typeof item === 'object' && !Array.isArray(item)).map((item, index) => {
      const passed = item.passed === true || item.status === 'pass'
      return {
        checkId: text(item.check_id, `check-${index + 1}`),
        title: text(item.title, `验收项 ${index + 1}`),
        passed,
        summary: text(item.summary, passed ? '该项已通过。' : '该项仍需补充配置。'),
        gaps: Array.isArray(item.gaps) ? item.gaps.map(value => text(value)).filter(Boolean) : [],
        evidence: Array.isArray(item.evidence)
          ? item.evidence.filter(value => value && typeof value === 'object' && !Array.isArray(value)).map(value => ({
            kind: text(value.kind, '配置'),
            itemId: text(value.item_id),
            label: text(value.label, text(value.item_id, '未命名配置')),
          }))
          : [],
      }
    })
    : []

  const totalCandidate = Number(report.total_count)
  const totalCount = Number.isFinite(totalCandidate) && totalCandidate > 0
    ? Math.trunc(totalCandidate)
    : (checks.length || DEFAULT_TOTAL)
  const derivedPassed = checks.filter(item => item.passed).length
  const passedCandidate = Number(report.passed_count)
  const passedCount = Math.max(0, Math.min(
    totalCount,
    Number.isFinite(passedCandidate) ? Math.trunc(passedCandidate) : derivedPassed,
  ))

  return {
    available: true,
    auditVersion: text(report.audit_version),
    playable: report.playable === true && passedCount === totalCount,
    passedCount,
    totalCount,
    percent: totalCount ? Math.round((passedCount / totalCount) * 100) : 0,
    checks,
  }
}

export function playabilityBadge(report) {
  const audit = normalizePlayabilityAudit(report)
  if (!audit.available) return { tone: 'bad', label: '未审计' }
  return {
    tone: audit.playable ? 'ok' : 'bad',
    label: `${audit.passedCount}/${audit.totalCount} 可玩性`,
  }
}
