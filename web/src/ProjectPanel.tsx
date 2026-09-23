import { useEffect, useState } from 'react'
import { api, type Finding, type FindingStatus, type ProjectDetail } from './api'
import { AutonomyBadge, FindingItem, HealthDot, RunDots } from './components'
import { ProposalCard, TaskModal } from './Proposals'
import type { Proposal } from './api'
import { AUTONOMY, CSTATUS, STAGE, VERIFY, clock, eventLabel, timeAgo } from './util'

export function ProjectPanel({
  id,
  refreshKey,
  onClose,
  onChanged,
}: {
  id: string
  refreshKey: number
  onClose: () => void
  onChanged: () => void
}) {
  const [d, setD] = useState<ProjectDetail | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [showResolved, setShowResolved] = useState(false)
  const [task, setTask] = useState<{ p: Proposal; md: string; approved: boolean } | null>(null)

  useEffect(() => {
    let alive = true
    api
      .project(id)
      .then((x) => alive && (setD(x), setErr(null)))
      .catch((e) => alive && setErr(String(e.message ?? e)))
    return () => {
      alive = false
    }
  }, [id, refreshKey])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  async function act(f: Finding, status: FindingStatus) {
    await api.setFinding(f.fingerprint, status)
    onChanged()
  }

  async function decide(p: Proposal, action: 'approve' | 'reject' | 'defer', note = '') {
    await api.decide(p.id, action, note)
    onChanged()
  }

  async function openTask(p: Proposal) {
    const t = await api.task(p.id)
    setTask({ p, md: t.markdown, approved: t.approved })
  }

  const p = d?.project
  const active = d?.findings.filter((f) => f.status === 'open' || f.status === 'acknowledged') ?? []
  const important = active.filter((f) => f.severity !== 'info')
  const info = active.filter((f) => f.severity === 'info')
  const others = d?.findings.filter((f) => f.status === 'ignored' || f.status === 'resolved') ?? []
  const models = (d?.checks.find((c) => c.check_id === 'llm_models')?.value.models ?? {}) as Record<string, string[]>

  return (
    <div className="panel-wrap" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside className="panel" role="dialog" aria-modal="true" aria-label={p?.name ?? '프로젝트'}>
        <header className="panel-head">
          <div className="panel-title">
            {p && <HealthDot health={p.health} />}
            <h2>{p?.name ?? '불러오는 중…'}</h2>
            {p && <AutonomyBadge p={p} />}
          </div>
          <button className="icon-btn lg" onClick={onClose} aria-label="닫기">
            ×
          </button>
        </header>
        {err && <div className="notice notice-red">{err}</div>}
        {p && d && (
          <div className="panel-body">
            {p.goal && <p className="panel-goal">{p.goal}</p>}
            <dl className="facts">
              <dt>단계</dt>
              <dd>{STAGE[p.stage]?.label ?? p.stage}</dd>
              <dt>자율 수준</dt>
              <dd>
                {p.effective_autonomy} · {AUTONOMY[p.effective_autonomy]}
                {p.autonomy !== p.effective_autonomy && (
                  <span className="muted"> (설정 {p.autonomy}, 검증 부족으로 제한)</span>
                )}
              </dd>
              <dt>검증 수준</dt>
              <dd>
                {p.verification} · {VERIFY[p.verification]}
              </dd>
              <dt>경로</dt>
              <dd className="mono">{p.path}</dd>
              {p.repo && (
                <>
                  <dt>저장소</dt>
                  <dd>
                    <a href={`https://github.com/${p.repo}`} target="_blank" rel="noreferrer">
                      {p.repo}
                    </a>
                  </dd>
                </>
              )}
              <dt>마지막 점검</dt>
              <dd>{timeAgo(p.last_checked)}</dd>
            </dl>

            <section className="panel-sec">
              <h3>
                열린 문제 <span className="sec-count">{important.length}</span>
              </h3>
              {important.length === 0 && <div className="empty-line">위험·주의 항목 없음</div>}
              {important.map((f) => (
                <FindingItem key={f.fingerprint} f={f} onAction={act} />
              ))}
              {info.length > 0 && (
                <details className="sub">
                  <summary>참고 정보 {info.length}건</summary>
                  {info.map((f) => (
                    <FindingItem key={f.fingerprint} f={f} onAction={act} />
                  ))}
                </details>
              )}
              {others.length > 0 && (
                <button className="link-btn" onClick={() => setShowResolved(!showResolved)}>
                  {showResolved ? '해결·무시 항목 숨기기' : `해결·무시 항목 ${others.length}건 보기`}
                </button>
              )}
              {showResolved &&
                others.map((f) => <FindingItem key={f.fingerprint} f={f} onAction={act} />)}
            </section>

            {d.proposals.length > 0 && (
              <section className="panel-sec">
                <h3>
                  제안 <span className="sec-count">{d.proposals.length}</span>
                </h3>
                {d.proposals.map((p) => (
                  <ProposalCard key={p.id} p={p} onAct={decide} onTask={() => openTask(p)} compact />
                ))}
              </section>
            )}

            <section className="panel-sec">
              <h3>체크 결과</h3>
              <div className="checks">
                {d.checks.map((c) => (
                  <div className="check" key={c.check_id}>
                    <span className={`st st-${c.status}`}>{CSTATUS[c.status]}</span>
                    <span className="check-label">{c.label}</span>
                    <span className="check-sum">
                      {c.summary}
                      {Array.isArray(c.value.runs) && <RunDots runs={c.value.runs} />}
                      {c.value.cached && <span className="muted"> · 같은 커밋이라 지난 결과 재사용</span>}
                    </span>
                    <span className="check-time" title={clock(c.created_at)}>
                      {(c.duration_ms / 1000).toFixed(1)}s
                    </span>
                  </div>
                ))}
              </div>
            </section>

            {Object.keys(models).length > 0 && (
              <section className="panel-sec">
                <h3>
                  코드에서 쓰는 LLM 모델 <span className="sec-count">{Object.keys(models).length}</span>
                </h3>
                <p className="muted small">P3에서 이 목록을 새 모델·지원 종료 정보와 대조해 교체 제안을 만든다.</p>
                <div className="chips wrap">
                  {Object.entries(models).map(([m, locs]) => (
                    <span className="chip chip-model" key={m} title={locs.join('\n')}>
                      {m}
                    </span>
                  ))}
                </div>
              </section>
            )}

            {d.events.length > 0 && (
              <section className="panel-sec">
                <h3>최근 변화</h3>
                <ol className="timeline">
                  {d.events.slice(0, 15).map((e) => (
                    <li key={e.id} className={`ev ev-${e.type}`}>
                      <span className="ev-time" title={clock(e.ts)}>
                        {timeAgo(e.ts)}
                      </span>
                      <span className="ev-type">{eventLabel(e.type)}</span>
                      <span className="ev-title">{e.payload.title ?? ''}</span>
                    </li>
                  ))}
                </ol>
              </section>
            )}

            <section className="panel-sec">
              <details className="sub">
                <summary>매니페스트</summary>
                <p className="muted small mono">{d.manifest_file}</p>
                <p className="muted small">이 파일을 고치면 다음 스캔부터 반영된다.</p>
                <pre className="detail">{JSON.stringify(d.manifest, null, 2)}</pre>
              </details>
            </section>
          </div>
        )}
      </aside>
      {task && <TaskModal {...task} onClose={() => setTask(null)} />}
    </div>
  )
}
