export type Severity = 'critical' | 'warning' | 'info'
export type Health = 'red' | 'yellow' | 'green' | 'gray'
export type FindingStatus = 'open' | 'acknowledged' | 'ignored' | 'resolved'
export type CheckStatus = 'ok' | 'warn' | 'fail' | 'error' | 'skipped'

export interface Finding {
  fingerprint: string
  project_id: string
  check_id: string
  key: string
  severity: Severity
  title: string
  detail: string
  url: string | null
  status: FindingStatus
  first_seen: string
  last_seen: string
  resolved_at: string | null
}

export interface GitInfo {
  is_git: boolean
  branch?: string
  dirty?: number
  ahead?: number | null
  behind?: number | null
  last_commit_at?: string | null
  last_commit_msg?: string
}

export interface ProjectSummary {
  id: string
  name: string
  path: string
  repo: string | null
  stage: string
  stack: string[]
  goal: string
  autonomy: string
  effective_autonomy: string
  verification: string
  health: Health
  counts: Record<Severity, number>
  unacked: number
  top: Finding | null
  git: GitInfo | null
  last_checked: string | null
}

export interface Run {
  id: number
  trigger: string
  started_at: string
  finished_at: string | null
  status: string
  summary: {
    projects?: Record<string, { status: string; checks?: number }>
    duration_ms?: number
    alerts_sent?: number
  }
}

export interface Idea {
  id: number
  title: string
  note: string
  created_at: string
}

export interface Overview {
  projects: ProjectSummary[]
  ideas: Idea[]
  last_run: Run | null
  scanning: boolean
  progress: { done: number; total: number } | null
  scan_error: string | null
  unregistered: string[]
  loose_files: { path: string; size_mb: number }[]
  manifest_errors: string[]
  telegram: boolean
  schedule: { installed: boolean; state?: string }
  stages: string[]
}

export interface CheckResult {
  check_id: string
  label: string
  status: CheckStatus
  summary: string
  value: Record<string, any>
  duration_ms: number
  created_at: string
}

export interface EventItem {
  id: number
  ts: string
  project_id: string | null
  type: string
  payload: Record<string, any>
}

export interface ProjectDetail {
  project: ProjectSummary
  manifest: Record<string, any>
  manifest_file: string
  checks: CheckResult[]
  findings: Finding[]
  events: EventItem[]
}

async function j<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })
  if (!r.ok) {
    let msg = `HTTP ${r.status}`
    try {
      msg = (await r.json()).detail ?? msg
    } catch {
      /* 본문이 JSON이 아닐 때는 상태 코드만 보여준다 */
    }
    throw new Error(msg)
  }
  return r.json()
}

export const api = {
  overview: () => j<Overview>('/api/overview'),
  project: (id: string) => j<ProjectDetail>(`/api/projects/${encodeURIComponent(id)}`),
  findings: (status: string) => j<Finding[]>(`/api/findings?status=${status}`),
  setFinding: (fp: string, status: FindingStatus) =>
    j<Finding>(`/api/findings/${fp}`, { method: 'POST', body: JSON.stringify({ status }) }),
  scan: (projects?: string[]) =>
    j<{ started: boolean }>('/api/scan', {
      method: 'POST',
      body: JSON.stringify({ projects: projects ?? null }),
    }),
  runs: () => j<Run[]>('/api/runs'),
  addIdea: (title: string, note: string) =>
    j<{ id: number }>('/api/ideas', { method: 'POST', body: JSON.stringify({ title, note }) }),
  archiveIdea: (id: number) => j<{ ok: boolean }>(`/api/ideas/${id}`, { method: 'DELETE' }),
}
