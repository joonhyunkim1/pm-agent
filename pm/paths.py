"""앱 데이터 경로와 경로 정규화.

설정·매니페스트·DB는 관리 대상 레포가 아니라 OS별 앱 데이터 폴더에 둔다.
관리 대상 중 public 레포가 있어서, 예산·정책을 레포에 넣으면 그대로 공개되기 때문이다.
  macOS   ~/Library/Application Support/pm-agent
  Windows %LOCALAPPDATA%\\pm-agent
PM_AGENT_HOME 환경변수로 위치를 바꿀 수 있다(테스트용).
"""
from __future__ import annotations

import os
import unicodedata
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "pm-agent"


def home() -> Path:
    override = os.environ.get("PM_AGENT_HOME")
    return Path(override) if override else Path(user_data_dir(APP_NAME, appauthor=False))


def config_file() -> Path:
    return home() / "config.yaml"


def projects_dir() -> Path:
    return home() / "projects"


def db_file() -> Path:
    return home() / "pm.db"


def logs_dir() -> Path:
    return home() / "logs"


def tasks_dir() -> Path:
    return home() / "tasks"


def lock_file(name: str = "scan") -> Path:
    return home() / f"{name}.lock"


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def resolve(p: str) -> Path:
    """'~'를 펼치고 NFC로 맞춘다.

    macOS는 한글 파일명을 NFD(분해형)로 돌려줄 때가 있고 Windows는 NFC라서,
    경로를 비교·저장하기 전에 항상 NFC로 통일한다.
    """
    return Path(nfc(os.path.expanduser(p)))


def display(p: Path) -> str:
    """홈 디렉터리를 '~'로 줄인 표기. 매니페스트에는 이 형태로 저장한다."""
    s = nfc(str(p))
    h = nfc(str(Path.home()))
    return "~" + s[len(h):] if s == h or s.startswith(h + os.sep) else s


def same(a: Path, b: Path) -> bool:
    return nfc(str(a)) == nfc(str(b))
