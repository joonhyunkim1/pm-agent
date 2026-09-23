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


class TelegramError(Exception):
    pass


def configured() -> bool:
    return bool(secrets.get(TOKEN) and secrets.get(CHAT))


def _call(method: str, **params) -> dict:
    token = secrets.get(TOKEN)
    if not token:
        raise TelegramError(f"{TOKEN} 미설정")
    try:
        r = httpx.post(f"https://api.telegram.org/bot{token}/{method}", json=params, timeout=20)
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise TelegramError(f"{method} 호출 실패: {type(e).__name__}") from None
    if not data.get("ok"):
        raise TelegramError(f"{method}: {data.get('description', '알 수 없는 오류')}")
    return data


def send(text: str) -> None:
    chat = secrets.get(CHAT)
    if not chat:
        raise TelegramError(f"{CHAT} 미설정")
    if len(text) > MAX_LEN:
        text = text[: MAX_LEN - 20] + "\n…(생략)"
    _call("sendMessage", chat_id=chat, text=text, parse_mode="HTML", disable_web_page_preview=True)


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
