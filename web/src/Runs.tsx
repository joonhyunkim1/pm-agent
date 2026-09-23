import { useEffect, useState } from 'react'
import { api, type Overview, type Run } from './api'
import { clock } from './util'

const TRIGGER: Record<string, string> = {
  manual: '수동(CLI)',
  dashboard: '대시보드',
  schedule: '자동(전체)',
  'schedule-light': '자동(헬스만)',
}
const RESULT: Record<string, string> = { ok: '정상', warn: '주의', fail: '위험', error: '오류' }

export function Runs({ ov, refreshKey }: { ov: Overview; refreshKey: number }) {
  const [runs, setRuns] = useState<Run[] | null>(null)
  useEffect(() => {
    api.runs().then(setRuns)
  }, [refreshKey])
  const names = Object.fromEntries(ov.projects.map((p) => [p.id, p.name]))

  if (!runs) return <div className="empty-line">불러오는 중…</div>
  if (runs.length === 0) return <div className="empty-block">아직 스캔 기록이 없습니다.</div>
  return (
    <div className="table-wrap">
      <table className="runs">
        <thead>
          <tr>
            <th>#</th>
            <th>시작</th>
            <th>방식</th>
            <th>소요</th>
            <th>프로젝트별 결과</th>
            <th>알림</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.id}>
              <td className="mono">{r.id}</td>
              <td>{clock(r.started_at)}</td>
              <td>{TRIGGER[r.trigger] ?? r.trigger}</td>
              <td className="mono">
                {r.summary.duration_ms != null ? `${(r.summary.duration_ms / 1000).toFixed(1)}s` : '진행 중'}
              </td>
              <td>
                <span className="run-results">
                  {Object.entries(r.summary.projects ?? {}).map(([pid, x]) => (
                    <span key={pid} className={`rr rr-${x.status}`} title={`${names[pid] ?? pid}: ${RESULT[x.status] ?? x.status}`}>
                      {names[pid] ?? pid}
                    </span>
                  ))}
                </span>
              </td>
              <td className="mono">{r.summary.alerts_sent ?? 0}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
