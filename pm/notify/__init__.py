"""알림 정책.

- 새로 생긴 critical(빨간불)만 즉시 보낸다.
- 나머지는 하루 한 번 요약(digest)으로 묶는다. 승인 피로·알림 피로를 줄이기 위한 규칙이다.
"""
from __future__ import annotations

import html
from datetime import datetime

from ..config import Config
from ..manifest import Manifest
from ..store import Store, now_iso
from . import telegram

ICON = {"red": "🔴", "yellow": "🟡", "green": "🟢", "gray": "⚪"}
KIND = {"maintenance": "유지보수", "cost": "비용 절감", "feature": "기능", "experiment": "검증 실험"}
RISK = {"low": "낮음", "medium": "중간", "high": "높음"}
DECISION = {"approved": "✅ 승인됨", "rejected": "❌ 거절됨", "deferred": "⏸ 보류됨", "done": "🏁 완료"}
SEV_ICON = {"critical": "🔴", "warning": "🟡", "info": "⚪"}
MAX_ITEMS = 10


def esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def project_health(findings: list[dict]) -> str:
    sev = {f["severity"] for f in findings if f["status"] in ("open", "acknowledged")}
    return "red" if "critical" in sev else "yellow" if "warning" in sev else "green"


def format_alerts(rows: list[dict], names: dict[str, str], port: int) -> str:
    lines = [f"<b>🔴 새 위험 신호 {len(rows)}건</b>"]
    for r in rows[:MAX_ITEMS]:
        lines.append(f"\n<b>{esc(names.get(r['project_id'], r['project_id']))}</b> · {esc(r['title'])}")
        if r["detail"]:
            lines.append(f"<code>{esc(r['detail'][:300])}</code>")
        if r["url"]:
            lines.append(f'<a href="{esc(r["url"])}">로그 보기</a>')
    if len(rows) > MAX_ITEMS:
        lines.append(f"\n… 외 {len(rows) - MAX_ITEMS}건")
    lines.append(f"\n대시보드: http://localhost:{port}")
    return "\n".join(lines)


def push_alerts(st: Store, manifests: list[Manifest], cfg: Config) -> int:
    """아직 알리지 않은 open critical을 보낸다. 전송에 성공한 것만 '알림 완료'로 표시한다."""
    if not telegram.configured():
        return 0
    rows = st.unnotified("critical")
    if not rows:
        return 0
    names = {m.id: m.name for m in manifests}
    telegram.send(format_alerts(rows, names, cfg.dashboard_port))
    st.mark_notified([r["fingerprint"] for r in rows])
    return len(rows)


def format_digest(st: Store, manifests: list[Manifest], since: str | None, port: int) -> str:
    today = datetime.now().strftime("%m-%d")
    lines = [f"<b>📋 프로젝트 점검 요약 ({today})</b>", ""]
    for m in manifests:
        active = st.findings(m.id, ("open", "acknowledged"))
        health = project_health(active)
        top = next((f for f in active if f["severity"] in ("critical", "warning")), None)
        line = f"{ICON[health]} <b>{esc(m.name)}</b>"
        if top:
            more = sum(1 for f in active if f["severity"] in ("critical", "warning")) - 1
            line += f" — {esc(top['title'])}" + (f" 외 {more}건" if more > 0 else "")
        lines.append(line)
    if since:
        ch = st.changed_since(since)
        opened = [f for f in ch["opened"] if f["severity"] != "info"]
        lines.append("")
        lines.append(f"지난 요약 이후: 새 문제 {len(opened)}건 · 해결 {len(ch['resolved'])}건")
        for f in opened[:5]:
            lines.append(f"  {SEV_ICON[f['severity']]} {esc(f['title'])}")
    waiting = st.proposals(statuses=("proposed", "deferred"))
    if waiting:
        lines.append(f"\n💡 결정을 기다리는 제안 {len(waiting)}건")
    lines.append(f"\n대시보드: http://localhost:{port}")
    return "\n".join(lines)


def proposal_text(p: dict, project_name: str) -> str:
    """제안 카드. 결정된 제안이면 결과 줄이 붙는다."""
    d = p["data"]
    c = d["exec_cost"]
    lines = [
        f"💡 <b>[{esc(project_name)}] {esc(d['title'])}</b>",
        f"{KIND.get(d['kind'], d['kind'])} · 규모 {d['size']} · 위험 {RISK.get(d['risk'], d['risk'])}"
        f" · 우선순위 {d['priority']}" + (" · 보호 경로 포함" if d.get("touches_protected") else ""),
        "",
        esc(d["summary"]),
    ]
    if d.get("evidence"):
        lines += ["", "<b>근거</b>", *[f"• {esc(e)}" for e in d["evidence"][:4]]]
    lines += ["", f"<b>기대효과</b> {esc(d['expected_effect'])}"]
    lines.append(f"💰 실행 비용(P2) ${c['low']:.2f}~{c['high']:.2f}, 상한 ${c['cap']:.2f} · {c['basis']}")
    if d.get("over_task_budget"):
        lines.append(f"⚠️ 프로젝트 작업당 예산 ${d['task_budget_usd']:.2f}을 넘음")
    ops = d.get("ops_cost_change_usd_month")
    if ops is not None:
        sign = "+" if ops > 0 else ""
        lines.append(f"📈 월 운영비 {sign}${ops:.2f} (LLM 추정: {esc(d.get('ops_cost_assumption') or '가정 없음')})")
    if d.get("low_risk"):
        lines.append("🟢 저위험 — P2 이후 사전 승인 예산 안에서 자동 처리 대상")
    lines.append(f"<code>{p['id']}</code>")
    if p["status"] in DECISION:
        when = (p.get("decided_at") or "")[:16].replace("T", " ")
        via = {"telegram": "Telegram", "dashboard": "대시보드", "cli": "CLI"}.get(p.get("decided_via") or "", "")
        lines.append(f"\n<b>{DECISION[p['status']]}</b> {when} UTC {via}".rstrip())
        if p.get("decision_note"):
            lines.append(f"메모: {esc(p['decision_note'])}")
        if p["status"] == "approved":
            lines.append(f"작업 지시서: <code>pm task {p['id']}</code>")
    return "\n".join(lines)


def proposal_buttons(p: dict):
    """열려 있는 제안에만 버튼을 단다. 보류된 제안은 승인·거절만."""
    pid = p["id"]
    if p["status"] in ("proposed", "backlog"):
        return [[("✅ 승인", f"p:{pid}:approve"), ("⏸ 보류", f"p:{pid}:defer"), ("❌ 거절", f"p:{pid}:reject")]]
    if p["status"] == "deferred":
        return [[("✅ 승인", f"p:{pid}:approve"), ("❌ 거절", f"p:{pid}:reject")]]
    return None


def push_proposals(st: Store, manifests: list[Manifest], limit: int = 5) -> int:
    """승인함(proposed)에 새로 올라온 제안을 버튼과 함께 보낸다."""
    if not telegram.configured():
        return 0
    names = {m.id: m.name for m in manifests}
    pending = [p for p in st.proposals(statuses=("proposed",)) if not p.get("tg_message_id")][:limit]
    for p in pending:
        mid = telegram.send(proposal_text(p, names.get(p["project_id"], p["project_id"])), proposal_buttons(p))
        st.set_proposal_message(p["id"], mid)
    return len(pending)


def budget_alert(st: Store, message: str) -> None:
    """월 예산 상한 알림은 한 달에 한 번만."""
    month = datetime.now().strftime("%Y-%m")
    if st.get_kv("budget_alert_month") == month or not telegram.configured():
        return
    telegram.send(f"💸 <b>LLM 월 예산 상한 도달</b>\n{esc(message)}\n이번 달 남은 기간에는 제안을 만들지 않습니다.")
    st.set_kv("budget_alert_month", month)


def maybe_digest(st: Store, manifests: list[Manifest], cfg: Config, force: bool = False) -> bool:
    if not telegram.configured():
        return False
    last = st.get_kv("last_digest")
    now = datetime.now()
    if not force:
        if now.hour < cfg.digest_hour:
            return False
        if last and datetime.fromisoformat(last["local"]).date() == now.date():
            return False
    telegram.send(format_digest(st, manifests, last["utc"] if last else None, cfg.dashboard_port))
    st.set_kv("last_digest", {"local": now.isoformat(timespec="seconds"), "utc": now_iso()})
    return True
