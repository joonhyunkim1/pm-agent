"""Telegram Bot API 클라이언트.

봇 토큰과 chat id는 키체인(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)에 둔다.
토큰이 URL에 들어가므로, 오류 메시지에 URL이 섞여 나가지 않게 예외를 정리해서 올린다.
"""
from __future__ import annotations

import httpx

from .. import secrets

TOKEN = "TELEGRAM_BOT_TOKEN"
CHAT = "TELEGRAM_CHAT_ID"
MAX_LEN = 4000  # Telegram 한도 4096자에서 여유를 둔다

# [[("라벨", "콜백 데이터"), ...], ...] — 줄 단위 버튼 배치
Buttons = list[list[tuple[str, str]]]


class TelegramError(Exception):
    pass


def configured() -> bool:
    return bool(secrets.get(TOKEN) and secrets.get(CHAT))


def chat_id() -> str | None:
    return secrets.get(CHAT)


def _call(method: str, http_timeout: float = 20, **params) -> dict:
    token = secrets.get(TOKEN)
    if not token:
        raise TelegramError(f"{TOKEN} 미설정")
    try:
        r = httpx.post(f"https://api.telegram.org/bot{token}/{method}", json=params, timeout=http_timeout)
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise TelegramError(f"{method} 호출 실패: {type(e).__name__}") from None
    if not data.get("ok"):
        raise TelegramError(f"{method}: {data.get('description', '알 수 없는 오류')}")
    return data


def _markup(buttons: Buttons | None) -> dict:
    if buttons is None:
        return {"inline_keyboard": []}
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in buttons]}


def _fit(text: str) -> str:
    return text if len(text) <= MAX_LEN else text[: MAX_LEN - 20] + "\n…(생략)"


def send(text: str, buttons: Buttons | None = None) -> int:
    chat = chat_id()
    if not chat:
        raise TelegramError(f"{CHAT} 미설정")
    params = dict(chat_id=chat, text=_fit(text), parse_mode="HTML", disable_web_page_preview=True)
    if buttons:
        params["reply_markup"] = _markup(buttons)
    return _call("sendMessage", **params)["result"]["message_id"]


def edit(message_id: int, text: str, buttons: Buttons | None = None) -> None:
    _call("editMessageText", chat_id=chat_id(), message_id=message_id, text=_fit(text), parse_mode="HTML",
          disable_web_page_preview=True, reply_markup=_markup(buttons))


def answer_callback(callback_id: str, text: str = "") -> None:
    _call("answerCallbackQuery", callback_query_id=callback_id, text=text[:190])


def get_updates(offset: int | None, timeout: int = 50) -> list[dict]:
    params: dict = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
    if offset is not None:
        params["offset"] = offset
    return _call("getUpdates", http_timeout=timeout + 20, **params)["result"]


def bot_name() -> str:
    return _call("getMe")["result"].get("username", "")


def latest_chat() -> tuple[str, str] | None:
    """봇에게 가장 최근에 메시지를 보낸 대화의 (chat_id, 이름)."""
    updates = _call("getUpdates")["result"]
    for u in reversed(updates):
        msg = u.get("message") or u.get("edited_message") or {}
        chat = msg.get("chat")
        if chat:
            name = chat.get("title") or chat.get("first_name") or chat.get("username") or ""
            return str(chat["id"]), name
    return None
