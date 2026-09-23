"""제안 결정(승인·거절·보류)과 작업 지시서.

대시보드·CLI·Telegram 어디서 결정하든 이 함수를 거친다. 그래서 어디서 결정해도
- 승인 시 작업 지시서가 만들어지고
- Telegram 카드가 결정 결과로 바뀐다.
P2 실행기가 생기기 전까지, 작업 지시서를 Codex·Claude 같은 코딩 에이전트에 그대로 붙여넣어 쓸 수 있다.
"""
from __future__ import annotations

from pathlib import Path

from . import manifest as mf
from . import notify, paths
from .notify import telegram
from .store import Store

ACTIONS = {"approve": "approved", "reject": "rejected", "defer": "deferred"}


def work_order(p: dict, m: mf.Manifest | None) -> str:
    d = p["data"]
    c = d["exec_cost"]
    name = m.name if m else p["project_id"]
    lines = [
        f"# 작업 지시서: {d['title']}",
        "",
        f"- 제안 ID: `{p['id']}` · 프로젝트: {name}" + (f" (`{m.path}`)" if m else ""),
    ]
    if m and m.repo:
        lines.append(f"- 저장소: {m.repo}")
    lines += [
        f"- 유형: {notify.KIND.get(d['kind'], d['kind'])} · 규모: {d['size']} · 위험: {notify.RISK.get(d['risk'], d['risk'])}",
        f"- 비용 상한: **${c['cap']:.2f}** — 넘으면 작업을 멈추고 보고한다 (예상 ${c['low']:.2f}~{c['high']:.2f}, {c['basis']})",
        "",
        "## 배경",
        d["summary"],
        "",
        "근거:",
        *[f"- {e}" for e in d.get("evidence", [])],
        "",
        f"기대효과: {d['expected_effect']}",
        "",
        "## 작업 단계",
        *[f"{i}. {s}" for i, s in enumerate(d.get("steps", []), 1)],
        "",
        "## 검증",
        *[f"- {v}" for v in d.get("verification", [])],
        "",
        "## 지켜야 할 것",
        f"- 새 브랜치 `pm/{p['id']}`에서 작업하고 **PR까지만** 올린다. main에 직접 커밋하거나 머지하지 않는다.",
        "- 작업 트리에 이미 있는 미커밋 변경은 건드리지 않는다.",
        "- 커밋 메시지와 PR 설명은 한국어로 쓴다.",
    ]
    if m and m.protected_paths:
        lines.append("- 다음 경로를 바꿔야 하면 멈추고 사람에게 확인받는다: "
                     + ", ".join(f"`{x}`" for x in m.protected_paths))
    if m and m.exclude:
        lines.append("- 다음은 읽거나 외부로 보내지 않는다: " + ", ".join(f"`{x}`" for x in m.exclude))
    return "\n".join(lines) + "\n"


def work_order_path(pid: str) -> Path:
    return paths.tasks_dir() / f"{pid}.md"


def _manifest(project_id: str) -> mf.Manifest | None:
    try:
        return mf.load(project_id)
    except (FileNotFoundError, ValueError):
        return None


def decide(st: Store, pid: str, action: str, via: str, note: str = "") -> dict | None:
    p = st.decide_proposal(pid, ACTIONS[action], via, note)
    if not p:
        return None
    m = _manifest(p["project_id"])
    if p["status"] == "approved":
        f = work_order_path(pid)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(work_order(p, m), encoding="utf-8")
    if p.get("tg_message_id") and telegram.configured():
        try:
            telegram.edit(p["tg_message_id"], notify.proposal_text(p, m.name if m else p["project_id"]),
                          notify.proposal_buttons(p))
        except telegram.TelegramError:
            pass  # 메시지가 너무 오래됐거나 지워졌으면 수정할 수 없다. 결정 자체는 이미 반영됐다.
    return p
