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
  proposals_waiting: number
}

export type ProposalStatus = 'proposed' | 'backlog' | 'deferred' | 'approved' | 'rejected' | 'done'

export interface ProposalData {
  title: string
  kind: 'maintenance' | 'cost' | 'feature' | 'experiment'
  priority: number
  summary: string
  evidence: string[]
  finding_fps: string[]
  size: 'S' | 'M' | 'L'
  risk: 'low' | 'medium' | 'high'
  touches_protected: boolean
  expected_effect: string
  ops_cost_change_usd_month: number | null
  ops_cost_assumption: string
  steps: string[]
  verification: string[]
  exec_cost: { low: number; high: number; cap: number; model: string; basis: string }
  low_risk: boolean
  over_task_budget: boolean
  task_budget_usd: number
  planner_model: string
}

export interface Proposal {
  id: string
  project_id: string
  created_at: string
  status: ProposalStatus
  title: string
  kind: string
  priority: number
  data: ProposalData
  decided_at: string | null
  decided_via: string | null
  decision_note: string | null
}

export interface LlmSummary {
  configured: boolean
  planner_model: string
  month_spend: number
  budget: number
  last_plan_at: string | null
  planning: boolean
  plan_error: string | null
  pricing: { source: string; as_of: string; tier: string; unit: string }
}

export interface PlanEstimate {
  model: string
  tier: string
  output_basis: string
  projects: { project_id: string; name: string; will_call: boolean; input_tokens: number; typical_usd: number; worst_usd: number }[]
  typical_usd: number
  worst_usd: number
  month_spend: number
  budget: number
  pricing: { as_of: string; tier: string }
}

export interface LlmCall {
  id: number
  ts: string
  purpose: string
  project_id: string | null
  model: string
  input_tokens: number
  cached_tokens: number
  output_tokens: number
  reasoning_tokens: number
  cost_usd: number
  ok: number
  error: string | null
  duration_ms: number
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
  schedule: { installed: boolean; state?: string; bot?: { installed: boolean; state?: string } }
  stages: string[]
  llm: LlmSummary
  proposals_waiting: number
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
  proposals: Proposal[]
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
  proposals: (status: string) => j<Proposal[]>(`/api/proposals?status=${status}`),
  decide: (id: string, action: 'approve' | 'reject' | 'defer', note = '') =>
    j<Proposal>(`/api/proposals/${id}`, { method: 'POST', body: JSON.stringify({ action, note }) }),
  task: (id: string) => j<{ markdown: string; approved: boolean; path: string | null }>(`/api/proposals/${id}/task`),
  planEstimate: (force = false) => j<PlanEstimate>(`/api/plan/estimate?force=${force}`),
  plan: (force = false) => j<{ started: boolean }>('/api/plan', { method: 'POST', body: JSON.stringify({ force }) }),
  llmCalls: () => j<LlmCall[]>('/api/llm/calls'),
}
