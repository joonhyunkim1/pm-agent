"""플래너(P1): 스캔 결과를 보고 LLM이 개선 제안을 만든다.

- 변화(열린 문제·커밋·매니페스트·사용 모델)가 있는 프로젝트만 호출한다. 변화가 없으면 0원.
- LLM에는 매니페스트, 열린 문제, 체크 요약, 최근 커밋, README 앞부분, 파일 구조만 보낸다.
  .env, 제외 경로(data/ 등), 코드 본문은 보내지 않는다.
- LLM은 작업 규모(S/M/L)와 가정만 판단하고, 금액은 pricing이 계산한다.
- 한 번에 프로젝트당 최대 3개, 전체 최대 5개만 승인함에 올리고 나머지는 백로그로 둔다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from . import llm, notify, pricing, proc
from . import manifest as mf
from .checks.llm_models import _SKIP_DIRS
from .config import Config, load_config
from .scanner import scan_lock
from .store import Store, now_iso

KIND_LABEL = {"maintenance": "유지보수", "cost": "비용 절감", "feature": "기능", "experiment": "검증 실험"}
SEV_LABEL = {"critical": "위험", "warning": "주의", "info": "정보"}
# 같은 우선순위면 장애·보안(유지보수)과 비용 절감을 먼저 올린다
KIND_ORDER = {"maintenance": 0, "cost": 1, "experiment": 2, "feature": 3}
# 실측 기록이 없을 때 쓰는 '보통' 출력 토큰 수 (추론 토큰 포함). 기록이 쌓이면 원장의 중앙값을 쓴다.
TYPICAL_OUTPUT_TOKENS = 4000


def typical_output_tokens(st: Store, model: str) -> tuple[int, str]:
    """(보통 출력 토큰, 근거). 최근 성공한 계획 호출의 중앙값."""
    outs = sorted(c["output_tokens"] for c in st.llm_calls(100)
                  if c["ok"] and c["model"] == model and c["purpose"] == "plan")[-20:]
    if len(outs) < 3:
        return TYPICAL_OUTPUT_TOKENS, "기본 가정"
    return outs[len(outs) // 2], f"최근 {len(outs)}회 실측 중앙값"

SYSTEM_PROMPT = """\
너는 1인 개발자의 프로젝트 포트폴리오를 관리하는 시니어 엔지니어이자 PM이다.
프로젝트 하나의 관찰 데이터(매니페스트, 자동 점검 결과, 최근 커밋, README, 파일 구조,
코드에서 쓰는 LLM 모델과 가격표, 이전 제안)를 보고, 지금 실행할 가치가 있는 개선 제안을 0~3개 만든다.

원칙
1. "최신이라서"는 이유가 아니다. 모든 제안에는 측정 가능한 이득이 있어야 한다:
   장애 해소, 보안 위험 제거, 월 운영비 절감, 분명한 품질·기능 향상.
2. 근거 없이 제안하지 않는다. 가능하면 입력의 문제 목록 ID([xxxxxxxx])를 finding_ids에 연결한다.
3. 금액을 직접 계산하지 않는다. 실행 비용은 작업 규모로만 표시한다.
   S = 파일 몇 개, 한두 시간 / M = 여러 파일, 반나절 / L = 구조 변경, 하루 이상.
   단 월 운영비 변화(예: 모델 교체에 따른 API 비용)는 입력의 실측값·가격표를 근거로 대략적인 달러 값과
   가정을 적을 수 있다. 근거가 없으면 ops_cost_change_usd_month는 null.
4. 수익 모델(revenue_model)이 none이면 수익화 기능을 제안하지 않는다. 필요하면 작은 '검증 실험'만 제안한다.
5. 보호 경로(protected_paths)를 건드리는 제안은 touches_protected=true로 표시한다.
6. 이미 올라온 제안과 겹치는 제안은 만들지 않는다. 거절된 제안과 사유는 존중한다.
7. 정보 등급 문제는 묶어서 하나로 만들거나 무시한다. 의존성 업데이트는 보안·호환성 이득이 있을 때만 제안한다.
8. 모든 텍스트는 한국어로, 짧고 구체적으로 쓴다. steps는 다른 코딩 에이전트가 그대로 따라 할 수 있게
   파일·명령 수준으로 쓰고, verification에는 완료를 확인하는 구체적인 방법을 쓴다.
9. priority는 1(가장 시급)~5. 운영 중 서비스의 장애·보안이 가장 시급하다.
10. 제안할 것이 없으면 proposals를 빈 배열로 두고 notes에 이유를 쓴다.
"""


class ProposalDraft(BaseModel):
    title: str = Field(description="한 줄 제목")
    kind: Literal["maintenance", "cost", "feature", "experiment"]
    priority: int = Field(description="1(가장 시급)~5")
    summary: str = Field(description="무엇을 왜 하는지 2~4문장")
    evidence: list[str] = Field(description="근거가 되는 관찰")
    finding_ids: list[str] = Field(description="관련 문제 ID (대괄호 안 8자리)")
    size: Literal["S", "M", "L"]
    risk: Literal["low", "medium", "high"]
    touches_protected: bool
    expected_effect: str = Field(description="기대효과, 가능하면 수치로")
    ops_cost_change_usd_month: float | None = Field(description="월 운영비 변화(절감은 음수), 근거 없으면 null")
    ops_cost_assumption: str = Field(description="운영비 추정의 가정. 없으면 빈 문자열")
    steps: list[str] = Field(description="실행 단계")
    verification: list[str] = Field(description="검증 방법")


class PlanOutput(BaseModel):
    proposals: list[ProposalDraft]
    notes: str


# ---------- 맥락 만들기 ----------

def _readme(root: Path, limit: int = 4000) -> str:
    for name in ("README.md", "readme.md", "README.MD", "README"):
        f = root / name
        if f.exists():
            text = f.read_text(encoding="utf-8", errors="replace").strip()
            return text[:limit] + ("\n…(생략)" if len(text) > limit else "")
    return "(README 없음)"


def _git_files(root: Path) -> list[str] | None:
    """추적 중이거나 무시되지 않은 파일 목록 (.gitignore를 따른다). git 저장소가 아니면 None."""
    try:
        r = proc.run([proc.tool("git"), "ls-files", "-z", "-co", "--exclude-standard"], cwd=root, timeout=30)
    except proc.ToolMissing:
        return None
    return [p for p in r.out.split("\0") if p] if r.ok else None


def _tree(root: Path, exclude: list[str], limit: int = 80) -> str:
    excl = [e.rstrip("/") for e in exclude if not e.startswith(".env")]
    files = _git_files(root)
    if files is not None:
        top: list[str] = []
        dirs: dict[str, int] = {}
        for f in files:
            parts = f.split("/")
            if any(p.startswith(".") and p not in (".github",) for p in parts) or parts[-1].startswith(".env"):
                continue
            if any(f == e or f.startswith(e + "/") for e in excl):
                continue
            if len(parts) == 1:
                top.append(f)
            else:
                key = "/".join(parts[: min(len(parts) - 1, 2)])
                dirs[key] = dirs.get(key, 0) + 1
        lines = ["./ " + ", ".join(sorted(top)[:25])]
        lines += [f"{d}/ (파일 {n}개)" for d, n in sorted(dirs.items())][:limit]
        return "\n".join(lines)
    excl_set = set(excl)
    lines: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in _SKIP_DIRS and not d.startswith(".")
            and os.path.normpath(os.path.join(rel, d)) not in excl_set
        )
        files = [f for f in filenames if not f.startswith(".")]
        if rel == ".":
            lines.append("./ " + ", ".join(sorted(files)[:25]))
        else:
            lines.append(f"{rel}/ (파일 {len(files)}개)")
        if depth >= 2:
            dirnames[:] = []
        if len(lines) >= limit:
            lines.append("…(생략)")
            break
    return "\n".join(lines)


def _git_log(root: Path) -> str:
    try:
        r = proc.run([proc.tool("git"), "log", "-15", "--format=%h %cs %s"], cwd=root, timeout=15)
    except proc.ToolMissing:
        return "(git 없음)"
    return r.out.strip() if r.ok and r.out.strip() else "(커밋 기록 없음)"


def _price_line(model: str) -> str:
    p = pricing.price(model)
    if not p:
        return f"- {model}: 가격표에 없음"
    cached = f", 캐시 입력 ${p.cached_input}" if p.cached_input is not None else ""
    return f"- {model}: 입력 ${p.input}{cached}, 출력 ${p.output}"


def build_context(m: mf.Manifest, st: Store, cfg: Config) -> tuple[str, str, dict[str, str]]:
    """(LLM에 보낼 본문, 변화 감지용 서명, 짧은 문제 ID → fingerprint)."""
    results = st.latest_results(m.id)
    labels = {r["check_id"]: r["label"] for r in results}
    active = st.findings(m.id, ("open", "acknowledged"))
    idmap: dict[str, str] = {}
    f_lines = []
    for f in active[:30]:
        sid = f["fingerprint"][:8]
        idmap[sid] = f["fingerprint"]
        detail = (f["detail"] or "").replace("\n", " / ")[:300]
        f_lines.append(f"- [{sid}] ({SEV_LABEL[f['severity']]}, {labels.get(f['check_id'], f['check_id'])}) "
                       f"{f['title']}" + (f" — {detail}" if detail else ""))
    c_lines = [f"- {r['label']}: {r['status']} — {r['summary']}" for r in results]

    git = next((r["value"] for r in results if r["check_id"] == "git"), {}) or {}
    models = sorted((next((r["value"] for r in results if r["check_id"] == "llm_models"), {}) or {})
                    .get("models", {}))
    reference = ["gpt-6-sol", "gpt-6-luna", "gpt-6-astra"]
    price_lines = [_price_line(x) for x in dict.fromkeys(models + reference)]

    prev = st.proposals(m.id, limit=20)
    p_lines = [f"- [{p['status']}] {p['title']}"
               + (f" (결정 메모: {p['decision_note']})" if p.get("decision_note") else "") for p in prev]

    meta = {
        "name": m.name, "goal": m.goal, "stage": m.stage, "stack": m.stack, "repo": m.repo,
        "autonomy": m.autonomy, "effective_autonomy": m.effective_autonomy,
        "verification_level": m.verification_level, "revenue_model": m.revenue_model,
        "task_budget_usd": m.budget.per_task_usd, "protected_paths": m.protected_paths, "notes": m.notes,
    }
    text = "\n\n".join([
        "## 매니페스트\n" + json.dumps(meta, ensure_ascii=False, indent=1),
        "## 열린 문제 (자동 점검)\n" + ("\n".join(f_lines) or "(없음)"),
        "## 체크 결과 요약\n" + ("\n".join(c_lines) or "(없음)"),
        "## 최근 커밋\n" + _git_log(m.abs_path),
        "## 코드에서 쓰는 LLM 모델\n" + (", ".join(models) or "(없음)"),
        f"## 참고 가격표 (USD / 1M 토큰, {pricing.meta()['tier']}, {pricing.meta()['as_of']} 기준)\n"
        + "\n".join(price_lines),
        "## 이전 제안\n" + ("\n".join(p_lines) or "(없음)"),
        "## 파일 구조 (제외 경로 빼고 2단계까지)\n" + _tree(m.abs_path, m.exclude),
        "## README 앞부분\n" + _readme(m.abs_path),
    ])

    sig_src = {
        "findings": sorted((f["fingerprint"], f["severity"]) for f in active),
        "head": git.get("head"),
        "manifest": m.model_dump(mode="json"),
        "models": models,
    }
    sig = hashlib.sha1(json.dumps(sig_src, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return text, sig, idmap


# ---------- 계획 ----------

def _targets(manifests: list[mf.Manifest], only: list[str] | None) -> list[mf.Manifest]:
    # L0(관찰만)과 아이디어 단계는 제안 대상이 아니다
    return [m for m in manifests
            if m.autonomy != "L0" and m.stage != "idea" and (not only or m.id in only)]


def _norm(title: str) -> str:
    return re.sub(r"\s+", " ", title.strip().lower())


def to_data(m: mf.Manifest, d: ProposalDraft, idmap: dict[str, str], cfg: Config) -> dict:
    exec_cost = pricing.exec_estimate(d.size, cfg.llm.executor_model)
    low_risk = (d.kind == "maintenance" and d.size == "S" and d.risk == "low"
                and not d.touches_protected and m.effective_autonomy == "L3")
    data = d.model_dump()
    data.update(
        priority=min(max(d.priority, 1), 5),
        finding_fps=[idmap[s.strip("[] ")] for s in d.finding_ids if s.strip("[] ") in idmap],
        exec_cost=exec_cost,
        low_risk=low_risk,
        task_budget_usd=m.budget.per_task_usd,
        over_task_budget=exec_cost["cap"] > m.budget.per_task_usd,
        planner_model=cfg.llm.planner_model,
    )
    return data


def estimate(only: list[str] | None = None, force: bool = False) -> dict:
    """호출 전 비용 미리보기. 실제로 호출될 프로젝트와 예상 비용(보통/최대)."""
    cfg = load_config()
    manifests, _ = mf.load_all()
    model = cfg.llm.planner_model
    tier = cfg.llm.service_tier
    rows = []
    with Store() as st:
        typical_out, basis = typical_output_tokens(st, model)
        for m in _targets(manifests, only):
            text, sig, _ = build_context(m, st, cfg)
            changed = force or st.get_kv(f"plan_sig:{m.id}") != sig
            tokens = llm.estimate_tokens(SYSTEM_PROMPT + text)
            rows.append({
                "project_id": m.id, "name": m.name, "will_call": changed, "input_tokens": tokens,
                # 보통: 설정한 요금 등급 / 최대: Standard로 재시도되고 캐시 할증까지 붙는 경우
                "typical_usd": round(pricing.cost(model, tokens, 0, typical_out, tier=tier), 4),
                "worst_usd": round(llm.worst_case_cost(model, SYSTEM_PROMPT, text, cfg.llm.max_output_tokens), 4),
            })
        spent = st.month_spend()
    calls = [r for r in rows if r["will_call"]]
    return {
        "model": model, "tier": tier, "output_basis": f"출력 {typical_out:,}토큰 ({basis})", "projects": rows,
        "typical_usd": round(sum(r["typical_usd"] for r in calls), 4),
        "worst_usd": round(sum(r["worst_usd"] for r in calls), 4),
        "month_spend": round(spent, 4), "budget": cfg.llm.monthly_budget_usd,
        "pricing": pricing.meta(),
    }


def plan(trigger: str = "manual", only: list[str] | None = None, force: bool = False,
         backend: llm.Backend | None = None, notify_: bool = True, log=lambda s: None) -> dict:
    cfg = load_config()
    manifests, _ = mf.load_all()
    report: dict = {"trigger": trigger, "projects": {}, "cost_usd": 0.0}
    drafts: list[tuple[mf.Manifest, ProposalDraft, dict[str, str]]] = []

    with scan_lock("plan"), Store() as st:
        client = llm.Client(st, cfg, backend)
        for m in _targets(manifests, only):
            text, sig, idmap = build_context(m, st, cfg)
            if not force and st.get_kv(f"plan_sig:{m.id}") == sig:
                report["projects"][m.id] = {"status": "unchanged"}
                log(f"  [{m.name}] 지난 계획 이후 변화 없음 — 호출 안 함")
                continue
            try:
                out, cost = client.structured(
                    purpose="plan", project_id=m.id, model=cfg.llm.planner_model, system=SYSTEM_PROMPT,
                    user=text, schema=PlanOutput, max_output_tokens=cfg.llm.max_output_tokens)
            except llm.BudgetExceeded as e:
                report["projects"][m.id] = {"status": "budget", "error": str(e)}
                report["budget_exceeded"] = str(e)
                log(f"  [{m.name}] {e} — 계획 중단")
                break
            except llm.LlmError as e:
                report["projects"][m.id] = {"status": "error", "error": str(e)}
                log(f"  [{m.name}] LLM 호출 실패: {e}")
                continue

            report["cost_usd"] += cost
            existing = {_norm(p["title"]) for p in st.proposals(
                m.id, statuses=("proposed", "backlog", "deferred", "approved"))}
            kept = 0
            for d in out.proposals:
                if kept >= cfg.llm.max_proposals_per_project:
                    break
                # 중복은 먼저 걸러서 한도 자리를 차지하지 않게 한다
                if _norm(d.title) in existing:
                    continue
                existing.add(_norm(d.title))
                drafts.append((m, d, idmap))
                kept += 1
            st.set_kv(f"plan_sig:{m.id}", sig)
            report["projects"][m.id] = {"status": "ok", "cost_usd": round(cost, 4), "new": kept,
                                        "notes": out.notes}
            log(f"  [{m.name}] 제안 {kept}개 (${cost:.4f}){' — ' + out.notes if not kept and out.notes else ''}")

        drafts.sort(key=lambda x: (min(max(x[1].priority, 1), 5), KIND_ORDER[x[1].kind]))
        created = {"proposed": 0, "backlog": 0}
        for i, (m, d, idmap) in enumerate(drafts):
            status = "proposed" if i < cfg.llm.max_proposals_per_run else "backlog"
            st.add_proposal("p" + uuid.uuid4().hex[:6], m.id, status, to_data(m, d, idmap, cfg))
            created[status] += 1
        st.set_kv("last_plan_at", now_iso())

        sent = 0
        if notify_:
            try:
                sent = notify.push_proposals(st, manifests)
                if report.get("budget_exceeded"):
                    notify.budget_alert(st, report["budget_exceeded"])
            except Exception as e:
                log(f"알림 전송 실패: {e}")
        report.update(created=created, notified=sent, month_spend=round(st.month_spend(), 4),
                      cost_usd=round(report["cost_usd"], 4))
        return report


def plan_due(st: Store, cfg: Config) -> bool:
    from datetime import datetime, timedelta, timezone

    from .store import parse_iso

    last = st.get_kv("last_plan_at")
    if last and datetime.now(timezone.utc) - parse_iso(last) < timedelta(hours=cfg.llm.plan_interval_hours):
        return False
    # 계획은 최신 스캔 결과를 보고 세워야 하므로, 전체 스캔이 최근(주기 이내)에 끝났을 때만 한다
    full = st.last_finished_run(full_only=True)
    if not full:
        return False
    return datetime.now(timezone.utc) - parse_iso(full["finished_at"]) < timedelta(hours=cfg.scan_interval_hours)
