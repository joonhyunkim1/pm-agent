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


def refs(s: str) -> list[str]:
    return _REF.findall(s)


def expand(s: str) -> str:
    def sub(m: re.Match) -> str:
        v = get(m.group(1))
        if v is None:
            raise MissingSecret(m.group(1))
        return v

    return _REF.sub(sub, s)
