import { useEffect, useState } from 'react'
import { api, type Finding, type FindingStatus, type Overview } from './api'
import { FindingItem } from './components'

const STATUS_TABS = [
  { v: 'active', label: '열림·확인함' },
  { v: 'ignored', label: '무시함' },
  { v: 'resolved', label: '해결됨' },
] as const

const SEV_TABS = [
  { v: 'important', label: '위험·주의' },
  { v: 'info', label: '정보' },
  { v: 'all', label: '전체' },
] as const

export function Inbox({
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
  const [status, setStatus] = useState<(typeof STATUS_TABS)[number]['v']>('active')
  const [sev, setSev] = useState<(typeof SEV_TABS)[number]['v']>('important')
  const [items, setItems] = useState<Finding[] | null>(null)

  useEffect(() => {
    let alive = true
    api.findings(status).then((x) => alive && setItems(x))
    return () => {
      alive = false
    }
  }, [status, refreshKey])

  const names = Object.fromEntries(ov.projects.map((p) => [p.id, p.name]))
  const shown = (items ?? []).filter((f) =>
    sev === 'all' ? true : sev === 'info' ? f.severity === 'info' : f.severity !== 'info',
  )

  async function act(f: Finding, s: FindingStatus) {
    await api.setFinding(f.fingerprint, s)
    onChanged()
  }

  return (
    <div className="inbox">
      <div className="filters">
        <div className="seg" role="tablist" aria-label="상태">
          {STATUS_TABS.map((t) => (
            <button key={t.v} role="tab" aria-selected={status === t.v} onClick={() => setStatus(t.v)}>
              {t.label}
            </button>
          ))}
        </div>
        <div className="seg" role="tablist" aria-label="심각도">
          {SEV_TABS.map((t) => (
            <button key={t.v} role="tab" aria-selected={sev === t.v} onClick={() => setSev(t.v)}>
              {t.label}
            </button>
          ))}
        </div>
      </div>
      {items === null ? (
        <div className="empty-line">불러오는 중…</div>
      ) : shown.length === 0 ? (
        <div className="empty-block">해당하는 항목이 없습니다.</div>
      ) : (
        <div className="finding-list">
          {shown.map((f) => (
            <FindingItem
              key={f.fingerprint}
              f={f}
              projectName={names[f.project_id] ?? f.project_id}
              onOpenProject={() => onOpen(f.project_id)}
              onAction={act}
            />
          ))}
        </div>
      )}
    </div>
  )
}
