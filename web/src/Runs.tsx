import { useEffect, useState } from 'react'
import { api, type LlmCall, type Overview, type Run } from './api'
import { clock, usd } from './util'

const TRIGGER: Record<string, string> = {
  manual: '수동(CLI)',
  dashboard: '대시보드',
  schedule: '자동(전체)',
  'schedule-light': '자동(헬스만)',
}
const RESULT: Record<string, string> = { ok: '정상', warn: '주의', fail: '위험', error: '오류' }

export function Runs({ ov, refreshKey }: { ov: Overview; refreshKey: number }) {
  const [runs, setRuns] = useState<Run[] | null>(null)
  const [calls, setCalls] = useState<LlmCall[]>([])
  useEffect(() => {
    api.runs().then(setRuns)
    api.llmCalls().then(setCalls)
  }, [refreshKey])
  const names = Object.fromEntries(ov.projects.map((p) => [p.id, p.name]))

  if (!runs) return <div className="empty-line">불러오는 중…</div>
  if (runs.length === 0) return <div className="empty-block">아직 스캔 기록이 없습니다.</div>
  return (
    <>
      <LlmLedger calls={calls} names={names} ov={ov} />
      <h3 className="sec-title">스캔</h3>
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
    </>
  )
}

function LlmLedger({ calls, names, ov }: { calls: LlmCall[]; names: Record<string, string>; ov: Overview }) {
  return (
    <section className="ledger">
      <h3 className="sec-title">
        LLM 호출 원장{' '}
        <span className="muted small">
          이번 달 {usd(ov.llm.month_spend, 4)} / {usd(ov.llm.budget, 0)} · 가격표 {ov.llm.pricing.as_of} {ov.llm.pricing.tier}
        </span>
      </h3>
      {calls.length === 0 ? (
        <div className="empty-line">아직 LLM 호출이 없습니다.</div>
      ) : (
        <div className="table-wrap">
          <table className="runs">
            <thead>
              <tr>
                <th>시각</th>
                <th>용도</th>
                <th>프로젝트</th>
                <th>모델</th>
                <th>입력 (캐시)</th>
                <th>출력 (추론)</th>
                <th>비용</th>
                <th>결과</th>
              </tr>
            </thead>
            <tbody>
              {calls.map((c) => (
                <tr key={c.id}>
                  <td>{clock(c.ts)}</td>
                  <td>{c.purpose === 'plan' ? '제안' : c.purpose.startsWith('experiment') ? '실험' : c.purpose}</td>
                  <td>{c.project_id ? (names[c.project_id] ?? c.project_id) : '—'}</td>
                  <td className="mono">{c.model}</td>
                  <td className="mono">
                    {c.input_tokens.toLocaleString()} ({c.cached_tokens.toLocaleString()})
                  </td>
                  <td className="mono">
                    {c.output_tokens.toLocaleString()} ({c.reasoning_tokens.toLocaleString()})
                  </td>
                  <td className="mono">{usd(c.cost_usd, 4)}</td>
                  <td>{c.ok ? <span className="rr rr-ok">성공</span> : <span className="rr rr-fail" title={c.error ?? ''}>실패</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
