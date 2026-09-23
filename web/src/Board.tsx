import { useState } from 'react'
import { api, type Idea, type Overview, type ProjectSummary } from './api'
import { AutonomyBadge, HealthDot } from './components'
import { STAGE, timeAgo } from './util'

export function Board({
  ov,
  onOpen,
  onChanged,
}: {
  ov: Overview
  onOpen: (id: string) => void
  onChanged: () => void
}) {
  return (
    <div className="board" role="list">
      {ov.stages.map((s) => {
        const items = ov.projects.filter((p) => p.stage === s)
        const n = items.length + (s === 'idea' ? ov.ideas.length : 0)
        return (
          <section
            className={`col ${n === 0 && s !== 'idea' ? 'col-collapsed' : ''}`}
            key={s}
            role="listitem"
            aria-label={STAGE[s]?.label ?? s}
          >
            <header className="col-head">
              <span className="col-title">{STAGE[s]?.label ?? s}</span>
              <span className="col-count">{n}</span>
              <span className="col-hint">{STAGE[s]?.hint}</span>
            </header>
            <div className="col-body">
              {items.map((p) => (
                <ProjectCard key={p.id} p={p} onClick={() => onOpen(p.id)} />
              ))}
              {s === 'idea' && <Ideas ideas={ov.ideas} onChanged={onChanged} />}
              {n === 0 && s !== 'idea' && <div className="col-empty">비어 있음</div>}
            </div>
          </section>
        )
      })}
    </div>
  )
}

function ProjectCard({ p, onClick }: { p: ProjectSummary; onClick: () => void }) {
  const g = p.git
  return (
    <button className={`card card-${p.health}`} onClick={onClick}>
      <div className="card-top">
        <HealthDot health={p.health} />
        <span className="card-name">{p.name}</span>
        <AutonomyBadge p={p} />
      </div>
      {p.goal && <div className="card-goal">{p.goal}</div>}
      {p.top ? (
        <div className={`card-issue sev-${p.top.severity}`}>{p.top.title}</div>
      ) : p.health === 'green' ? (
        <div className="card-ok">열린 문제 없음</div>
      ) : null}
      <div className="card-meta">
        {p.counts.critical > 0 && <span className="cnt cnt-critical">위험 {p.counts.critical}</span>}
        {p.counts.warning > 0 && <span className="cnt cnt-warning">주의 {p.counts.warning}</span>}
        {g?.is_git ? (
          <span className="meta-git" title={g.last_commit_msg}>
            {g.branch}
            {g.dirty ? ` · 미커밋 ${g.dirty}` : ''} ·{' '}
            {g.last_commit_at ? `${timeAgo(g.last_commit_at)} 커밋` : '커밋 없음'}
          </span>
        ) : g ? (
          <span>git 없음</span>
        ) : null}
      </div>
      {p.stack.length > 0 && (
        <div className="chips">
          {p.stack.map((s) => (
            <span className="chip" key={s}>
              {s}
            </span>
          ))}
        </div>
      )}
    </button>
  )
}

function Ideas({ ideas, onChanged }: { ideas: Idea[]; onChanged: () => void }) {
  const [adding, setAdding] = useState(false)
  const [title, setTitle] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!title.trim()) return
    setBusy(true)
    try {
      await api.addIdea(title, note)
      setTitle('')
      setNote('')
      setAdding(false)
      onChanged()
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      {ideas.map((i) => (
        <div className="idea" key={i.id}>
          <div className="idea-top">
            <span className="idea-title">{i.title}</span>
            <button
              className="icon-btn"
              aria-label={`${i.title} 보관`}
              title="보관"
              onClick={async () => {
                await api.archiveIdea(i.id)
                onChanged()
              }}
            >
              ×
            </button>
          </div>
          {i.note && <div className="idea-note">{i.note}</div>}
          <div className="idea-meta">{timeAgo(i.created_at)} 메모</div>
        </div>
      ))}
      {adding ? (
        <form className="idea-form" onSubmit={submit}>
          <input
            autoFocus
            placeholder="아이디어 한 줄"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            aria-label="아이디어 제목"
          />
          <textarea
            placeholder="메모 (선택) — 누가 왜 돈을 낼까?"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={3}
            aria-label="아이디어 메모"
          />
          <div className="row-end">
            <button type="button" className="btn ghost sm" onClick={() => setAdding(false)}>
              취소
            </button>
            <button className="btn primary sm" disabled={busy || !title.trim()}>
              추가
            </button>
          </div>
        </form>
      ) : (
        <button className="add-idea" onClick={() => setAdding(true)}>
          + 아이디어
        </button>
      )}
    </>
  )
}
