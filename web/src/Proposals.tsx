import { useEffect, useState } from 'react'
import { api, type Overview, type PlanEstimate, type Proposal } from './api'
import { KIND, PSTATUS, RISK, clock, timeAgo, usd } from './util'

const FILTERS = [
  { v: 'waiting', label: '결정 대기' },
  { v: 'backlog', label: '백로그' },
  { v: 'approved', label: '승인됨' },
  { v: 'rejected', label: '거절됨' },
  { v: 'all', label: '전체' },
] as const

export function Proposals({
  ov,
  refreshKey,
  onOpen,
  onChanged,
}: {
  ov: Overview
  refreshKey: number
  onOpen: (id: string) => void
  onChanged: () => void
}) {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]['v']>('waiting')
  const [items, setItems] = useState<Proposal[] | null>(null)
  const [task, setTask] = useState<{ p: Proposal; md: string; approved: boolean } | null>(null)

  useEffect(() => {
    let alive = true
    api.proposals(filter).then((x) => alive && setItems(x))
    return () => {
      alive = false
    }
  }, [filter, refreshKey])

  const names = Object.fromEntries(ov.projects.map((p) => [p.id, p.name]))

  async function act(p: Proposal, action: 'approve' | 'reject' | 'defer', note = '') {
    const done = await api.decide(p.id, action, note)
    onChanged()
    if (action === 'approve') openTask(done)
  }

  async function openTask(p: Proposal) {
    const t = await api.task(p.id)
    setTask({ p, md: t.markdown, approved: t.approved })
  }

  return (
    <div className="proposals">
      <PlanBar ov={ov} onChanged={onChanged} />
      <div className="filters">
        <div className="seg" role="tablist" aria-label="제안 상태">
          {FILTERS.map((f) => (
            <button key={f.v} role="tab" aria-selected={filter === f.v} onClick={() => setFilter(f.v)}>
              {f.label}
            </button>
          ))}
        </div>
      </div>
      {items === null ? (
        <div className="empty-line">불러오는 중…</div>
      ) : items.length === 0 ? (
        <div className="empty-block">
          {filter === 'waiting' ? '결정을 기다리는 제안이 없습니다.' : '해당하는 제안이 없습니다.'}
        </div>
      ) : (
        <div className="proposal-list">
          {items.map((p) => (
            <ProposalCard
              key={p.id}
              p={p}
              projectName={names[p.project_id] ?? p.project_id}
              onOpenProject={() => onOpen(p.project_id)}
              onAct={act}
              onTask={() => openTask(p)}
            />
          ))}
        </div>
      )}
      {task && <TaskModal {...task} onClose={() => setTask(null)} />}
    </div>
  )
}

function PlanBar({ ov, onChanged }: { ov: Overview; onChanged: () => void }) {
  const l = ov.llm
  const [est, setEst] = useState<PlanEstimate | null>(null)
  const [force, setForce] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const ratio = Math.min(l.month_spend / l.budget, 1)

  async function preview(force = false) {
    setErr(null)
    setBusy(true)
    try {
      setEst(await api.planEstimate(force))
      setForce(force)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function run() {
    setErr(null)
    try {
      await api.plan(force)
      setEst(null)
      onChanged()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }

  const calls = est?.projects.filter((p) => p.will_call) ?? []
  return (
    <section className="planbar">
      <div className="planbar-top">
        <div className="meter-wrap">
          <div className="meter-label">
            이번 달 LLM 비용 <b>{usd(l.month_spend)}</b> / {usd(l.budget, 0)}
            <span className="muted"> · {l.planner_model} · 마지막 계획 {timeAgo(l.last_plan_at)}</span>
          </div>
          <div className="meter" role="meter" aria-valuemin={0} aria-valuemax={l.budget} aria-valuenow={l.month_spend}>
            <span style={{ width: `${ratio * 100}%` }} className={ratio > 0.8 ? 'hot' : ''} />
          </div>
        </div>
        <button className="btn primary" disabled={busy || l.planning || !l.configured} onClick={() => preview(false)}>
          {l.planning ? '제안 만드는 중…' : '지금 제안 받기'}
        </button>
      </div>
      {!l.configured && (
        <div className="notice notice-amber">
          OpenAI 키가 없습니다. 터미널에서 <code>pm secret set OPENAI_API_KEY</code>
        </div>
      )}
      {l.plan_error && <div className="notice notice-red">지난 계획 실패: {l.plan_error}</div>}
      {err && <div className="notice notice-red">{err}</div>}
      {est && (
        <div className="estimate">
          <div className="estimate-head">
            {calls.length ? (
              <>
                변화가 있는 <b>{calls.length}개</b> 프로젝트만 호출합니다 · 예상 <b>{usd(est.typical_usd, 3)}</b> (최대{' '}
                {usd(est.worst_usd, 3)})
              </>
            ) : (
              <>지난 계획 이후 변화가 있는 프로젝트가 없습니다.</>
            )}
          </div>
          <ul className="estimate-list">
            {est.projects.map((p) => (
              <li key={p.project_id} className={p.will_call ? '' : 'muted'}>
                <span>{p.name}</span>
                <span>
                  {p.will_call
                    ? `입력 약 ${p.input_tokens.toLocaleString()}토큰 · ${usd(p.typical_usd, 3)} (최대 ${usd(p.worst_usd, 3)})`
                    : '변화 없음 — 호출 안 함'}
                </span>
              </li>
            ))}
          </ul>
          <p className="muted small">
            {est.model} ({est.tier}) · {est.output_basis} · 가격표 {est.pricing.as_of} 기준. 실제 비용은 호출 후
            원장에 기록됩니다.
          </p>
          <div className="row-end">
            <button className="btn ghost sm" onClick={() => setEst(null)}>
              취소
            </button>
            {!calls.length && (
              <button className="btn sm" onClick={() => preview(true)}>
                변화 없어도 전부 계획
              </button>
            )}
            {calls.length > 0 && (
              <button className="btn primary sm" onClick={run}>
                실행
              </button>
            )}
          </div>
        </div>
      )}
    </section>
  )
}

export function ProposalCard({
  p,
  projectName,
  onOpenProject,
  onAct,
  onTask,
  compact = false,
}: {
  p: Proposal
  projectName?: string
  onOpenProject?: () => void
  onAct: (p: Proposal, action: 'approve' | 'reject' | 'defer', note?: string) => void
  onTask: () => void
  compact?: boolean
}) {
  const d = p.data
  const c = d.exec_cost
  const [more, setMore] = useState(false)
  const [rejecting, setRejecting] = useState(false)
  const [note, setNote] = useState('')
  const open = p.status === 'proposed' || p.status === 'backlog' || p.status === 'deferred'
  const ops = d.ops_cost_change_usd_month

  return (
    <article className={`proposal kind-${d.kind} pst-${p.status}`}>
      <header className="proposal-head">
        <span className={`kind-pill kind-${d.kind}`}>{KIND[d.kind] ?? d.kind}</span>
        {projectName && (
          <button className="finding-proj" onClick={onOpenProject}>
            {projectName}
          </button>
        )}
        <h3>{d.title}</h3>
        <span className={`pstatus pst-${p.status}`}>{PSTATUS[p.status]}</span>
      </header>
      {!compact && <p className="proposal-summary">{d.summary}</p>}
      <div className="tags">
        <span className="tag">우선순위 {d.priority}</span>
        <span className="tag">규모 {d.size}</span>
        <span className={`tag risk-${d.risk}`}>위험 {RISK[d.risk]}</span>
        {d.touches_protected && <span className="tag tag-warn">보호 경로 포함</span>}
        {d.low_risk && (
          <span className="tag tag-ok" title="P2 이후 사전 승인 예산 안에서 자동 처리 대상">
            저위험
          </span>
        )}
        {d.over_task_budget && <span className="tag tag-warn">작업당 예산 {usd(d.task_budget_usd)} 초과</span>}
      </div>
      {!compact && (
        <dl className="costs">
          <dt>기대효과</dt>
          <dd>{d.expected_effect}</dd>
          <dt>실행 비용 (P2)</dt>
          <dd>
            {usd(c.low)}~{usd(c.high)} · 상한 <b>{usd(c.cap)}</b>
            <span className="muted"> · {c.basis}, {c.model}</span>
          </dd>
          <dt>월 운영비</dt>
          <dd>
            {ops === null ? (
              <span className="muted">변화 없음 또는 근거 없음</span>
            ) : (
              <>
                <b className={ops < 0 ? 'good' : 'bad'}>
                  {ops > 0 ? '+' : ''}
                  {usd(ops)}
                </b>
                <span className="muted"> · LLM 추정: {d.ops_cost_assumption || '가정 없음'}</span>
              </>
            )}
          </dd>
        </dl>
      )}
      {more && (
        <div className="proposal-more">
          {d.evidence.length > 0 && (
            <>
              <h4>근거</h4>
              <ul>
                {d.evidence.map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
            </>
          )}
          <h4>작업 단계</h4>
          <ol>
            {d.steps.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ol>
          <h4>검증</h4>
          <ul>
            {d.verification.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </div>
      )}
      {p.decision_note && <p className="muted small">메모: {p.decision_note}</p>}
      {rejecting && (
        <div className="reject-box">
          <textarea
            autoFocus
            rows={2}
            placeholder="거절 사유 (선택) — 다음 계획에서 같은 제안을 피하는 데 쓰입니다"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            aria-label="거절 사유"
          />
          <div className="row-end">
            <button className="btn ghost sm" onClick={() => setRejecting(false)}>
              취소
            </button>
            <button className="btn sm danger" onClick={() => onAct(p, 'reject', note)}>
              거절
            </button>
          </div>
        </div>
      )}
      <footer className="proposal-foot">
        <span className="muted small" title={clock(p.created_at)}>
          {timeAgo(p.created_at)} · <code>{p.id}</code>
          {p.decided_at && ` · ${PSTATUS[p.status]} ${timeAgo(p.decided_at)}`}
        </span>
        <div className="finding-actions">
          <button className="btn ghost sm" onClick={() => setMore(!more)} aria-expanded={more}>
            {more ? '접기' : '단계·검증'}
          </button>
          <button className="btn ghost sm" onClick={onTask}>
            {p.status === 'approved' ? '작업 지시서' : '지시서 미리보기'}
          </button>
          {open && !rejecting && (
            <>
              {p.status !== 'deferred' && (
                <button className="btn ghost sm" onClick={() => onAct(p, 'defer')}>
                  보류
                </button>
              )}
              <button className="btn ghost sm" onClick={() => setRejecting(true)}>
                거절
              </button>
              <button className="btn primary sm" onClick={() => onAct(p, 'approve')}>
                승인
              </button>
            </>
          )}
        </div>
      </footer>
    </article>
  )
}

export function TaskModal({ p, md, approved, onClose }: { p: Proposal; md: string; approved: boolean; onClose: () => void }) {
  const [copied, setCopied] = useState(false)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  async function copy() {
    try {
      await navigator.clipboard.writeText(md)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* 클립보드 권한이 없으면 사용자가 직접 선택해 복사한다 */
    }
  }

  return (
    <div className="panel-wrap center" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label="작업 지시서">
        <header className="panel-head">
          <div className="panel-title">
            <h2>{approved ? '작업 지시서' : '작업 지시서 미리보기'}</h2>
            {!approved && <span className="muted small">아직 승인 전</span>}
          </div>
          <div className="row-end">
            <button className="btn sm" onClick={copy}>
              {copied ? '복사됨' : '복사'}
            </button>
            <button className="icon-btn lg" onClick={onClose} aria-label="닫기">
              ×
            </button>
          </div>
        </header>
        <p className="muted small modal-hint">
          P2 실행기가 생기기 전까지는 이 지시서를 Codex나 Claude Code에 그대로 붙여넣어 쓰면 됩니다. (
          <code>pm task {p.id}</code>)
        </p>
        <pre className="detail task-md">{md}</pre>
      </div>
    </div>
  )
}
