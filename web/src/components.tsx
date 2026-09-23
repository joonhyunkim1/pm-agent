import { useState } from 'react'
import type { Finding, FindingStatus, Health, ProjectSummary } from './api'
import { AUTONOMY, FSTATUS, SEV, VERIFY, checkLabel, timeAgo } from './util'

const HEALTH_LABEL: Record<Health, string> = {
  red: '위험',
  yellow: '주의',
  green: '정상',
  gray: '아직 스캔 안 함',
}

export function HealthDot({ health }: { health: Health }) {
  return <span className={`dot dot-${health}`} role="img" aria-label={HEALTH_LABEL[health]} title={HEALTH_LABEL[health]} />
}

export function AutonomyBadge({ p }: { p: ProjectSummary }) {
  const capped = p.autonomy !== p.effective_autonomy
  const title = capped
    ? `설정 ${p.autonomy}(${AUTONOMY[p.autonomy]})이지만 ${VERIFY[p.verification]} 상태라 ${p.effective_autonomy}로 제한됨`
    : `${p.autonomy}: ${AUTONOMY[p.autonomy]}`
  return (
    <span className={`badge ${capped ? 'badge-capped' : ''}`} title={title}>
      {capped ? (
        <>
          <s>{p.autonomy}</s>
          {p.effective_autonomy}
        </>
      ) : (
        p.autonomy
      )}
    </span>
  )
}

export function RunDots({ runs }: { runs: { t: string; c: string; url: string }[] }) {
  return (
    <span className="run-dots">
      {runs.map((r, i) => (
        <a
          key={i}
          href={r.url}
          target="_blank"
          rel="noreferrer"
          className={`run-dot run-${r.c === 'success' ? 'ok' : r.c === 'failure' ? 'fail' : 'other'}`}
          title={`${new Date(r.t).toLocaleString('ko-KR')} · ${r.c}`}
        />
      ))}
    </span>
  )
}

export function FindingItem({
  f,
  projectName,
  onAction,
  onOpenProject,
}: {
  f: Finding
  projectName?: string
  onAction: (f: Finding, status: FindingStatus) => void
  onOpenProject?: () => void
}) {
  const [open, setOpen] = useState(false)
  const live = f.status !== 'resolved'
  return (
    <div className={`finding sev-${f.severity} st-${f.status}`}>
      <div className="finding-main">
        <span className={`sev-pill sev-${f.severity}`}>{SEV[f.severity]}</span>
        <div className="finding-text">
          <div className="finding-title">
            {projectName && (
              <button className="finding-proj" onClick={onOpenProject}>
                {projectName}
              </button>
            )}
            {f.title}
          </div>
          <div className="finding-meta">
            {checkLabel(f.check_id)} · 처음 발견 {timeAgo(f.first_seen)}
            {f.status !== 'open' && ` · ${FSTATUS[f.status]}`}
            {f.status === 'resolved' && ` ${timeAgo(f.resolved_at)}`}
          </div>
        </div>
        <div className="finding-actions">
          {f.detail && (
            <button className="btn ghost sm" onClick={() => setOpen(!open)} aria-expanded={open}>
              {open ? '접기' : '자세히'}
            </button>
          )}
          {f.url && (
            <a className="btn ghost sm" href={f.url} target="_blank" rel="noreferrer">
              로그
            </a>
          )}
          {f.status === 'open' && (
            <button className="btn sm" onClick={() => onAction(f, 'acknowledged')} title="봤음. 해결될 때까지 목록에는 남는다">
              확인
            </button>
          )}
          {live && f.status !== 'open' && (
            <button className="btn sm" onClick={() => onAction(f, 'open')}>
              다시 열기
            </button>
          )}
          {live && f.status !== 'ignored' && (
            <button className="btn ghost sm" onClick={() => onAction(f, 'ignored')} title="앞으로 이 항목은 알리지 않는다">
              무시
            </button>
          )}
        </div>
      </div>
      {open && f.detail && <pre className="detail">{f.detail}</pre>}
    </div>
  )
}
