export function timeAgo(iso?: string | null): string {
  if (!iso) return '—'
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 60) return '방금'
  if (s < 3600) return `${Math.floor(s / 60)}분 전`
  if (s < 86400) return `${Math.floor(s / 3600)}시간 전`
  if (s < 86400 * 30) return `${Math.floor(s / 86400)}일 전`
  return new Date(iso).toLocaleDateString('ko-KR')
}

export function clock(iso?: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('ko-KR', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export const STAGE: Record<string, { label: string; hint: string }> = {
  idea: { label: '아이디어', hint: '구상 중' },
  prototype: { label: '프로토타입', hint: '실험·검증' },
  mvp: { label: 'MVP', hint: '핵심 기능 동작' },
  deployed: { label: '운영 중', hint: '배포되어 도는 중' },
  users: { label: '사용자 확보', hint: '실사용자 있음' },
  revenue: { label: '수익화', hint: '매출 발생' },
}

export const SEV = { critical: '위험', warning: '주의', info: '정보' } as const
export const FSTATUS = { open: '열림', acknowledged: '확인함', ignored: '무시함', resolved: '해결됨' } as const
export const CSTATUS = { ok: '정상', warn: '주의', fail: '위험', error: '오류', skipped: '보류' } as const

export const AUTONOMY: Record<string, string> = {
  L0: '관찰만',
  L1: '제안까지',
  L2: 'PR까지',
  L3: '저위험 자동 머지',
}

export const VERIFY: Record<string, string> = {
  V0: '검증 수단 없음',
  V1: '일부 검증 (테스트 또는 헬스체크)',
  V2: '테스트 + 운영 헬스체크',
}

const EVENT: Record<string, string> = {
  finding_opened: '새 문제',
  finding_reopened: '재발',
  finding_resolved: '해결',
  finding_acknowledged: '확인',
  finding_ignored: '무시',
  finding_open: '다시 열기',
}

export function eventLabel(type: string): string {
  return EVENT[type] ?? type
}

export function checkLabel(id: string): string {
  const [kind, rest] = id.split(/:(.*)/s)
  switch (kind) {
    case 'git':
      return 'Git'
    case 'npm':
      return 'npm 의존성'
    case 'python':
      return 'Python 의존성'
    case 'gh':
      return `워크플로우 ${rest}`
    case 'http':
      return `헬스 ${rest}`
    case 'deploy':
      return `배포 ${rest}`
    case 'verify':
      return `검증 ${rest}`
    case 'llm_models':
      return 'LLM 모델'
    case 'path':
      return '경로'
    default:
      return id
  }
}
