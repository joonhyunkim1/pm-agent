import { useCallback, useEffect, useState } from 'react'
import { api, type Overview } from './api'
import { Board } from './Board'
import { Inbox } from './Inbox'
import { ProjectPanel } from './ProjectPanel'
import { Runs } from './Runs'
import { timeAgo } from './util'

type Tab = 'board' | 'inbox' | 'runs'
interface Route {
  tab: Tab
  project?: string
}

// #/board, #/inbox, #/runs 뒤에 /p/<id>가 붙으면 프로젝트 패널을 연다
function parseHash(): Route {
  const [tab, p, id] = location.hash.replace(/^#\/?/, '').split('/')
  const t: Tab = tab === 'inbox' || tab === 'runs' ? tab : 'board'
  return { tab: t, project: p === 'p' && id ? decodeURIComponent(id) : undefined }
}

function toHash(r: Route): string {
  return `#/${r.tab}${r.project ? `/p/${encodeURIComponent(r.project)}` : ''}`
}

const TABS: { v: Tab; label: string }[] = [
  { v: 'board', label: '보드' },
  { v: 'inbox', label: '알림함' },
  { v: 'runs', label: '스캔 이력' },
]

export function App() {
  const [route, setRoute] = useState<Route>(parseHash)
  const [ov, setOv] = useState<Overview | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [bump, setBump] = useState(0)

  useEffect(() => {
    const on = () => setRoute(parseHash())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])

  const go = (r: Route) => {
    location.hash = toHash(r)
  }

  const load = useCallback(async () => {
    try {
      setOv(await api.overview())
      setErr(null)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  // 스캔 중에는 자주, 평소에는 30초마다 새로 고친다
  useEffect(() => {
    const t = setInterval(load, ov?.scanning ? 1500 : 30000)
    return () => clearInterval(t)
  }, [load, ov?.scanning])

  const changed = useCallback(() => {
    setBump((b) => b + 1)
    load()
  }, [load])

  async function scan() {
    try {
      await api.scan()
      await load()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }

  // 스캔이 끝나 last_run이 바뀌면 하위 화면도 새로 불러온다
  const refreshKey = bump * 100000 + (ov?.last_run?.id ?? 0)
  const projects = ov?.projects ?? []
  const red = projects.filter((p) => p.health === 'red').length
  const yellow = projects.filter((p) => p.health === 'yellow').length
  const unacked = projects.reduce((n, p) => n + p.unacked, 0)
  const running = projects.filter((p) => p.stage === 'deployed' || p.stage === 'users' || p.stage === 'revenue').length

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <span className="logo" aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
          <span className="brand-name">PM Agent</span>
          <span className="mode" title="P0: 관찰만 한다. LLM을 호출하지 않으므로 비용이 들지 않는다.">
            관찰 모드 · LLM 비용 0원
          </span>
        </div>
        <nav className="tabs" aria-label="화면">
          {TABS.map((t) => (
            <button
              key={t.v}
              className={route.tab === t.v ? 'on' : ''}
              aria-current={route.tab === t.v ? 'page' : undefined}
              onClick={() => go({ tab: t.v, project: route.project })}
            >
              {t.label}
              {t.v === 'inbox' && unacked > 0 && <span className="tab-badge">{unacked}</span>}
            </button>
          ))}
        </nav>
        <div className="scan-box">
          <span className="last-scan">
            {ov?.scanning
              ? `스캔 중 ${ov.progress ? `${ov.progress.done}/${ov.progress.total}` : ''}`
              : `마지막 스캔 ${timeAgo(ov?.last_run?.finished_at)}`}
          </span>
          <button className="btn primary" onClick={scan} disabled={!ov || ov.scanning}>
            {ov?.scanning ? '스캔 중…' : '지금 스캔'}
          </button>
        </div>
      </header>

      <main className="main">
        {err && <div className="notice notice-red">API 오류: {err} — `pm serve`가 실행 중인지 확인하세요.</div>}
        {ov?.scan_error && <div className="notice notice-red">스캔 실패: {ov.scan_error}</div>}

        {ov && (
          <>
            <div className="stats">
              <Stat label="프로젝트" value={projects.length} sub={`운영 중 ${running}`} />
              <Stat label="위험" value={red} tone={red ? 'red' : undefined} sub="빨간불 프로젝트" />
              <Stat label="주의" value={yellow} tone={yellow ? 'yellow' : undefined} sub="노란불 프로젝트" />
              <Stat
                label="확인 안 한 항목"
                value={unacked}
                sub="위험·주의"
                onClick={() => go({ tab: 'inbox' })}
              />
            </div>
            <Setup ov={ov} />
            {route.tab === 'board' && <Board ov={ov} onOpen={(id) => go({ ...route, project: id })} onChanged={changed} />}
            {route.tab === 'inbox' && (
              <Inbox ov={ov} refreshKey={refreshKey} onOpen={(id) => go({ ...route, project: id })} onChanged={changed} />
            )}
            {route.tab === 'runs' && <Runs ov={ov} refreshKey={refreshKey} />}
          </>
        )}
      </main>

      {route.project && (
        <ProjectPanel
          id={route.project}
          refreshKey={refreshKey}
          onClose={() => go({ tab: route.tab })}
          onChanged={changed}
        />
      )}
    </div>
  )
}

function Stat({
  label,
  value,
  sub,
  tone,
  onClick,
}: {
  label: string
  value: number
  sub?: string
  tone?: 'red' | 'yellow'
  onClick?: () => void
}) {
  const Tag = onClick ? 'button' : 'div'
  return (
    <Tag className={`stat ${tone ? `stat-${tone}` : ''}`} onClick={onClick}>
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
      {sub && <span className="stat-sub">{sub}</span>}
    </Tag>
  )
}

// 아직 끝나지 않은 설정을 한 줄씩 보여준다
function Setup({ ov }: { ov: Overview }) {
  const items: { key: string; text: React.ReactNode }[] = []
  if (!ov.telegram)
    items.push({
      key: 'tg',
      text: (
        <>
          Telegram 알림 미연결 — <code>pm secret set TELEGRAM_BOT_TOKEN</code> 후 <code>pm telegram link</code>
        </>
      ),
    })
  if (!ov.schedule.installed)
    items.push({
      key: 'sched',
      text: (
        <>
          자동 실행 꺼짐 — <code>pm schedule install</code> (1시간마다 확인, 6시간마다 스캔)
        </>
      ),
    })
  if (ov.unregistered.length)
    items.push({
      key: 'unreg',
      text: (
        <>
          등록 안 된 폴더 {ov.unregistered.length}개 ({ov.unregistered.join(', ')}) — <code>pm init --all</code>
        </>
      ),
    })
  for (const f of ov.loose_files)
    items.push({ key: f.path, text: <>Projects 폴더에 큰 파일: {f.path} ({f.size_mb}MB)</> })
  for (const e of ov.manifest_errors) items.push({ key: e, text: <>매니페스트 오류: {e}</> })
  if (!items.length) return null
  return (
    <ul className="setup" aria-label="설정 점검">
      {items.map((i) => (
        <li key={i.key}>{i.text}</li>
      ))}
    </ul>
  )
}
