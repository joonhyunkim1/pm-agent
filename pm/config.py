"""전역 설정 (config.yaml)."""
from __future__ import annotations

from typing import Literal

import yaml
from pydantic import BaseModel

from . import paths

Autonomy = Literal["L0", "L1", "L2", "L3"]

_HEADER = """\
# pm-agent 전역 설정
# roots: 프로젝트를 찾을 폴더 목록 (한 단계 아래 폴더를 프로젝트로 본다)
# scan_interval_hours: 전체 스캔 주기. 운영 헬스체크(워크플로우·HTTP·배포)는 이와 별개로 매시간 돈다.
#                      LLM을 쓰는 플래닝 주기(P1)와도 별개다.
# digest_hour: 이 시각(로컬) 이후 첫 tick에서 일일 요약을 Telegram으로 보낸다.
"""


class Config(BaseModel):
    roots: list[str] = ["~/Projects"]
    ignore_dirs: list[str] = []
    scan_interval_hours: float = 6
    digest_hour: int = 9
    default_autonomy: Autonomy = "L3"
    git_fetch: bool = True
    large_file_mb: int = 50
    dashboard_port: int = 8765


def load_config() -> Config:
    f = paths.config_file()
    if not f.exists():
        cfg = Config()
        save_config(cfg)
        return cfg
    data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    return Config.model_validate(data)


def save_config(cfg: Config) -> None:
    f = paths.config_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(cfg.model_dump(), allow_unicode=True, sort_keys=False)
    f.write_text(_HEADER + body, encoding="utf-8")
