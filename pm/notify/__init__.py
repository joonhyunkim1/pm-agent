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
    lines.append(f"\n대시보드: http://localhost:{port}")
    return "\n".join(lines)


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
