"""상시 Telegram 봇: 제안 카드의 버튼(승인·보류·거절)과 간단한 명령을 처리한다.

launchd가 계속 띄워 두고(KeepAlive), long polling으로 업데이트를 받는다.
연결된 대화(TELEGRAM_CHAT_ID) 외의 메시지와 버튼 입력은 모두 무시한다.
봇이 도는 동안에는 다른 프로세스가 getUpdates를 부르면 충돌하므로, 업데이트 수신은 이 봇만 한다.
"""
from __future__ import annotations

import time

from .. import manifest as mf
from .. import proposals
from ..config import load_config
from ..store import Store
from . import esc, format_digest, proposal_text, telegram

HELP = (
    "<b>pm-agent 봇</b>\n"
    "/pending — 결정을 기다리는 제안\n"
    "/status — 프로젝트 상태 요약\n"
    "제안 카드의 버튼으로 승인·보류·거절할 수 있습니다."
)
ACTION_LABEL = {"approve": "승인했습니다", "reject": "거절했습니다", "defer": "보류했습니다"}


def _names() -> dict[str, str]:
    return {m.id: m.name for m in mf.load_all()[0]}


def handle(update: dict, st: Store, chat: str) -> None:
    cq = update.get("callback_query")
    if cq:
        if str(cq.get("message", {}).get("chat", {}).get("id")) != chat:
            return
        parts = (cq.get("data") or "").split(":")
        if len(parts) == 3 and parts[0] == "p" and parts[2] in proposals.ACTIONS:
            p = proposals.decide(st, parts[1], parts[2], "telegram")
            if p:
                telegram.answer_callback(cq["id"], ACTION_LABEL[parts[2]])
            else:
                current = st.proposal(parts[1])
                telegram.answer_callback(cq["id"], "이미 결정된 제안입니다" if current else "없는 제안입니다")
                if current and current.get("tg_message_id"):
                    try:
                        telegram.edit(current["tg_message_id"],
                                      proposal_text(current, _names().get(current["project_id"], "")), None)
                    except telegram.TelegramError:
                        pass
        return

    msg = update.get("message") or {}
    if str(msg.get("chat", {}).get("id")) != chat:
        return
    text = (msg.get("text") or "").strip().split("@")[0]
    if text == "/pending":
        waiting = st.proposals(statuses=("proposed", "deferred"))
        if not waiting:
            telegram.send("결정을 기다리는 제안이 없습니다.")
            return
        names = _names()
        lines = [f"💡 <b>결정을 기다리는 제안 {len(waiting)}건</b>"]
        lines += [f"• [{esc(names.get(p['project_id'], p['project_id']))}] {esc(p['title'])} "
                  f"<code>{p['id']}</code>" for p in waiting[:15]]
        telegram.send("\n".join(lines))
    elif text == "/status":
        cfg = load_config()
        telegram.send(format_digest(st, mf.load_all()[0], None, cfg.dashboard_port))
    elif text in ("/start", "/help"):
        telegram.send(HELP)


def run_forever(log=print) -> None:
    backoff = 5
    while True:
        chat = telegram.chat_id()
        if not telegram.configured() or not chat:
            log("Telegram 미설정 — 60초 뒤 다시 확인")
            time.sleep(60)
            continue
        try:
            with Store() as st:
                offset = st.get_kv("tg_offset")
                for u in telegram.get_updates(offset):
                    st.set_kv("tg_offset", u["update_id"] + 1)
                    try:
                        handle(u, st, chat)
                    except Exception as e:  # 업데이트 하나의 오류로 봇이 멈추지 않게 한다
                        log(f"업데이트 처리 실패: {type(e).__name__}: {e}")
            backoff = 5
        except telegram.TelegramError as e:
            log(f"{e} — {backoff}초 뒤 재시도")
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)
