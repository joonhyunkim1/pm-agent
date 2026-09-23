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
# llm: 제안(P1) 설정. monthly_budget_usd에 닿으면 플래너가 멈춘다.
#      plan_interval_hours마다, 변화가 있는 프로젝트만 LLM을 호출한다.
"""


class LlmConfig(BaseModel):
    planner_model: str = "gpt-6-sol"
    helper_model: str = "gpt-6-luna"
    # P2 실행기(코딩 에이전트)가 쓸 모델. 제안 카드의 실행 비용 추정에 쓴다.
    executor_model: str = "gpt-5.3-codex"
    monthly_budget_usd: float = 10.0
    plan_interval_hours: float = 48
    max_proposals_per_project: int = 3
    max_proposals_per_run: int = 5
    max_output_tokens: int = 16000
    # 비용 최적화 옵션. 2026-09-23 실측(README 참고): Flex + 명시 캐시로 품질 그대로 −54%.
    # 추론 강도를 낮추면 더 싸지지만 작업 단계의 안전 주의사항이 빠져서 기본값을 유지한다.
    service_tier: Literal["standard", "flex"] = "flex"   # flex: 반값, 자원이 없으면 Standard로 재시도
    reasoning_effort: str | None = None      # none | minimal | low | medium | high (None이면 모델 기본값)
    verbosity: Literal["low", "medium", "high"] | None = None
    # 공통 지시문 끝에만 캐시 지점을 둔다. 지시문+출력 형식이 1,024토큰 이상이어야 캐시가 걸린다.
    explicit_cache: bool = True


class Config(BaseModel):
    roots: list[str] = ["~/Projects"]
    ignore_dirs: list[str] = []
    scan_interval_hours: float = 6
    digest_hour: int = 9
    default_autonomy: Autonomy = "L3"
    git_fetch: bool = True
    large_file_mb: int = 50
    dashboard_port: int = 8765
    llm: LlmConfig = LlmConfig()


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
