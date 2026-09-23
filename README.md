# pm-agent

내 프로젝트들을 지켜보고, 비용을 따져 개선하는 AI 프로젝트 매니저.

현재 단계는 **P0 (관찰 전용)** 이다. LLM을 호출하지 않으므로 운영 비용이 0원이고,
결정론적 체크만으로 "조용히 죽어 있는 것"을 찾아 알린다.

| 단계 | 내용 | 상태 |
|---|---|---|
| P0 관찰 | 매니페스트 · 스캐너 · Finding · 알림 · 대시보드 · 자동 실행 | ✅ |
| P1 제안 | LLM 플래닝 → 제안 카드(비용 범위·기대효과) → 승인 인박스 | 예정 |
| P2 실행 | 코딩 에이전트 실행기 · worktree 격리 · 검증 · PR · 비용 원장 | 예정 |
| P3 LLM 앱 특화 | 새 모델 감지 → 평가셋 A/B → 교체 제안 | 예정 |
| P4 사업 레이어 | 실제 지표 기반 ROI · 검증 실험 보고서 | 보류 (수익 프로젝트 생기면) |

## 설치

```bash
uv tool install --editable ~/Projects/pm-agent   # 어디서나 `pm` 사용
cd ~/Projects/pm-agent/web && npm install && npm run build   # 대시보드
```

## 자주 쓰는 명령

```bash
pm init --all            # Projects 아래 새 폴더를 찾아 매니페스트 초안 생성
pm scan                  # 지금 스캔 (LLM 호출 없음)
pm status                # 프로젝트별 신호등
pm serve --open          # 대시보드 http://localhost:8765
pm digest                # 일일 요약 미리보기
pm idea add "제목" -n "메모"
```

## 처음 한 번 설정

```bash
# 1) Telegram 알림 — @BotFather에서 /newbot으로 봇을 만든 뒤
pm secret set TELEGRAM_BOT_TOKEN   # 토큰 입력 (화면에 안 보임, 키체인에 저장)
#    새 봇에게 아무 메시지나 보낸 뒤
pm telegram link

# 2) DR 헬스체크 — Vercel 배포 주소 (GitHub secret SITE_URL과 같은 값)
pm secret set DR_SITE_URL

# 3) 자동 실행 — 매시간 운영 헬스체크, 6시간마다 전체 스캔, 매일 09시 이후 요약 발송
pm schedule install
```

## 구조

```
pm/
  manifest.py      프로젝트별 목표·정책·체크 정의 (pydantic)
  discovery.py     폴더를 보고 매니페스트 초안 생성 (규칙 기반)
  checks/          git · npm · python · gh_workflow · http_json · gh_deployment · verify · llm_models
  scanner.py       체크 실행 → Finding 갱신 → 알림 / tick(스케줄러 진입점)
  store.py         SQLite: 스캔 이력, Finding 수명주기, append-only 이벤트
  notify/          즉시 알림(critical만) + 일일 요약
  schedule.py      launchd (macOS) / 작업 스케줄러 (Windows, 미검증)
  api.py           대시보드용 로컬 API (127.0.0.1)
web/               React + Vite 대시보드 (나중에 Tauri로 감싸기 쉬운 정적 SPA)
```

설정·매니페스트·DB는 레포가 아니라 앱 데이터 폴더에 있다(`pm config`로 위치 확인).
관리 대상 중 public 레포가 있어서, 예산·정책을 레포에 넣으면 그대로 공개되기 때문이다.

## 스캔 주기

| 종류 | 주기 | 체크 |
|---|---|---|
| 헬스 스캔 | 매시간 (tick마다) | 예약 워크플로우 성공률, HTTP 헬스 엔드포인트, 배포 연속 실패 |
| 전체 스캔 | 6시간 (`scan_interval_hours`) | 위 항목 + git 상태, 의존성·취약점, 테스트·빌드, LLM 모델 목록 |

서비스 장애는 가볍고 급한 헬스 체크로 1시간 안에 잡고, 느리고 급하지 않은 점검은 전체 스캔에 모았다.
노트북이 잠들어 있어도 다음 tick에서 밀린 스캔을 따라잡는다.

## 알림 규칙

- **새로 생긴 위험(critical)만 즉시** Telegram으로 보낸다. 같은 문제는 한 번만 알린다.
- 주의·정보 항목은 하루 한 번 요약에 묶는다.
- 대시보드에서 `확인`하면 해결될 때까지 목록에 남지만 다시 알리지 않는다. 더 심각해지면 다시 연다.
- `무시`하면 앞으로 알리지 않는다.

## 자율 수준

`autonomy`는 L0 관찰 / L1 제안 / L2 PR까지 / L3 저위험 자동 머지.
L3는 테스트 + 운영 헬스체크(V2)가 있는 프로젝트에서만 실제로 적용되고, 모자라면 자동으로 L2로 묶인다.
(P0에서는 아직 코드를 고치지 않으므로 표시만 한다.)
