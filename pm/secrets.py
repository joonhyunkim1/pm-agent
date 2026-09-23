"""비밀값 저장: OS 키체인(macOS Keychain / Windows Credential Manager)을 쓴다.

매니페스트에는 값 대신 ${keychain:NAME} 자리표시자만 적는다.
PM_SECRET_<NAME> 환경변수가 있으면 그것을 우선한다(테스트·헤드리스 환경용).
키체인은 이름 목록을 돌려주지 않으므로, 이름만 secrets.json에 따로 적어 둔다(값은 저장하지 않음).
"""
from __future__ import annotations

import json
import os
import re

import keyring
from keyring.errors import PasswordDeleteError

from . import paths

SERVICE = "pm-agent"
_REF = re.compile(r"\$\{keychain:([A-Za-z0-9_]+)\}")


class MissingSecret(Exception):
    def __init__(self, name: str):
        super().__init__(name)
        self.name = name


def _index_file():
    return paths.home() / "secrets.json"


def names() -> list[str]:
    f = _index_file()
    if not f.exists():
        return []
    return sorted(json.loads(f.read_text(encoding="utf-8")))


def _save_names(items: list[str]) -> None:
    f = _index_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(sorted(set(items))), encoding="utf-8")


def get(name: str) -> str | None:
    env = os.environ.get(f"PM_SECRET_{name}")
    if env:
        return env
    try:
        return keyring.get_password(SERVICE, name)
    except Exception:
        return None


def put(name: str, value: str) -> None:
    keyring.set_password(SERVICE, name, value)
    _save_names([*names(), name])


def remove(name: str) -> None:
    try:
        keyring.delete_password(SERVICE, name)
    except PasswordDeleteError:
        pass
    _save_names([n for n in names() if n != name])


# 알려진 비밀값의 형식. 키 '이름'을 값 대신 붙여넣는 실수를 저장 전에 잡는다.
_FORMATS = {
    "OPENAI_API_KEY": (re.compile(r"^sk-[A-Za-z0-9_\-]{20,}$"), "OpenAI 키는 `sk-`로 시작하는 긴 문자열입니다"),
    "TELEGRAM_BOT_TOKEN": (re.compile(r"^\d{6,}:[A-Za-z0-9_\-]{30,}$"), "봇 토큰은 `숫자:영문숫자` 형식입니다"),
    "TELEGRAM_CHAT_ID": (re.compile(r"^-?\d+$"), "chat id는 숫자입니다"),
}


def format_problem(name: str, value: str) -> str | None:
    fmt = _FORMATS.get(name)
    if not value:
        return "값이 비어 있습니다."
    if fmt and not fmt[0].match(value):
        return f"형식이 예상과 다릅니다 (길이 {len(value)}자). {fmt[1]}. 키의 이름이 아니라 값을 붙여넣었는지 확인하세요."
    return None


def verify(name: str) -> tuple[bool | None, str]:
    """저장 직후 비용 없는 호출로 확인한다. 확인 방법이 없는 이름이면 (None, '')."""
    import httpx

    value = get(name)
    try:
        if name == "OPENAI_API_KEY":
            r = httpx.get("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {value}"},
                          timeout=20)
            if r.status_code == 200:
                return True, f"OpenAI 키 유효 (모델 {len(r.json().get('data', []))}개 접근 가능)"
            code = (r.json().get("error") or {}).get("code", "") if r.headers.get("content-type", "").startswith(
                "application/json") else ""
            return False, f"OpenAI가 거부함: HTTP {r.status_code} {code}".strip()
        if name == "TELEGRAM_BOT_TOKEN":
            r = httpx.get(f"https://api.telegram.org/bot{value}/getMe", timeout=20)
            ok = r.status_code == 200 and r.json().get("ok")
            return bool(ok), (f"봇 @{r.json()['result'].get('username')}" if ok else f"Telegram이 거부함: HTTP {r.status_code}")
    except httpx.HTTPError as e:
        return False, f"확인 요청 실패: {type(e).__name__}"
    return None, ""


def refs(s: str) -> list[str]:
    return _REF.findall(s)


def expand(s: str) -> str:
    def sub(m: re.Match) -> str:
        v = get(m.group(1))
        if v is None:
            raise MissingSecret(m.group(1))
        return v

    return _REF.sub(sub, s)
