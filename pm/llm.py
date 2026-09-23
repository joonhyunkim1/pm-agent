"""LLM 호출: 예산 확인 → 호출 → 비용 원장 기록.

모든 호출은 이 모듈을 거친다. 그래서
- 호출 전에 '최악의 경우 비용'이 월 예산을 넘는지 보고, 넘으면 호출하지 않는다.
- 성공·실패와 상관없이 실제 토큰과 비용을 llm_calls에 남긴다.
OpenAI 키는 pm-agent 전용 키(키체인 OPENAI_API_KEY)를 쓴다.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from pydantic import BaseModel

from . import pricing, secrets
from .config import Config
from .store import Store

KEY = "OPENAI_API_KEY"


class LlmError(Exception):
    pass


class BudgetExceeded(LlmError):
    def __init__(self, spent: float, budget: float, worst: float, message: str | None = None):
        super().__init__(message or f"월 예산 초과 우려: 이번 달 ${spent:.2f} + 이번 호출 최대 ${worst:.2f} > 한도 ${budget:.2f}")
        self.spent, self.budget, self.worst = spent, budget, worst


# OpenAI 프로젝트·조직의 하드 한도에 걸리면 429와 함께 이 코드가 온다.
# 우리 쪽 예산 가드와 별개인 이중 안전장치라서, 걸리면 이번 계획의 남은 호출을 모두 멈춘다.
_PROVIDER_LIMIT_CODES = ("project_spend_limit_exceeded", "organization_spend_limit_exceeded")


@dataclass
class Reply:
    parsed: BaseModel | None
    usage: dict = field(default_factory=dict)
    response_id: str | None = None
    status: str = "completed"
    detail: str = ""
    tier: str | None = None  # 실제로 청구된 요금 등급 (API가 돌려준 service_tier)


# (model, system, user, schema, max_output_tokens, options) -> Reply. 테스트에서는 가짜 백엔드를 넣는다.
Backend = Callable[..., Reply]


def default_options(cfg: Config) -> dict:
    c = cfg.llm
    return {"service_tier": c.service_tier, "reasoning_effort": c.reasoning_effort,
            "verbosity": c.verbosity, "explicit_cache": c.explicit_cache}


def configured() -> bool:
    return bool(secrets.get(KEY))


def estimate_tokens(text: str) -> int:
    """토크나이저 없이 넉넉하게 어림한다. 한글은 글자당 약 1토큰, 영문은 4글자당 1토큰 안팎이라
    UTF-8 바이트 수 / 3이면 한글은 비슷하고 영문은 과대평가된다(예산 확인에는 과대평가가 안전)."""
    return len(text.encode("utf-8")) // 3 + 1


def worst_case_cost(model: str, system: str, user: str, max_output_tokens: int,
                    tier: str = "standard") -> float:
    # 캐시 쓰기 할증(1.25배)까지 가정한 최악의 경우
    tokens = estimate_tokens(system + user)
    return pricing.cost(model, tokens, 0, max_output_tokens, cache_write_tokens=tokens, tier=tier)


def _request_kwargs(system: str, user: str, options: dict) -> dict:
    kw: dict = {}
    if options.get("explicit_cache"):
        # 모든 프로젝트에 공통인 지시문 끝에만 캐시 지점을 둔다. 그 뒤(프로젝트별 내용)는
        # 다시 쓰일 일이 없으므로 캐시 쓰기 할증 없이 일반 입력 요금으로 청구된다.
        kw["input"] = [
            {"role": "developer", "content": [
                {"type": "input_text", "text": system, "prompt_cache_breakpoint": {"mode": "explicit"}}]},
            {"role": "user", "content": [{"type": "input_text", "text": user}]},
        ]
        kw["prompt_cache_options"] = {"mode": "explicit"}
    else:
        kw["instructions"] = system
        kw["input"] = user
    if options.get("service_tier") == "flex":
        kw["service_tier"] = "flex"
    if options.get("reasoning_effort"):
        kw["reasoning"] = {"effort": options["reasoning_effort"]}
    if options.get("verbosity"):
        # Responses API에서는 text.verbosity. SDK가 여기에 구조화 출력 형식(format)을 합쳐 준다.
        kw["text"] = {"verbosity": options["verbosity"]}
    return kw


def openai_backend(model: str, system: str, user: str, schema: type[BaseModel],
                   max_output_tokens: int, options: dict | None = None) -> Reply:
    import openai
    from openai import OpenAI

    key = secrets.get(KEY)
    if not key:
        raise LlmError(f"{KEY} 미설정 — `pm secret set {KEY}`")
    options = options or {}
    flex = options.get("service_tier") == "flex"
    # Flex는 대기열에서 기다릴 수 있어 응답이 느리다
    client = OpenAI(api_key=key, timeout=900 if flex else 240, max_retries=2)
    kw = _request_kwargs(system, user, options)
    try:
        resp = client.responses.parse(model=model, text_format=schema, max_output_tokens=max_output_tokens,
                                      store=False, **kw)  # store=False: OpenAI 쪽에 대화를 남기지 않는다
    except openai.RateLimitError as e:
        # Flex 자원이 부족하면 Standard로 한 번 더 시도한다. 예산 한도 초과는 재시도하지 않는다.
        if not flex or "spend_limit" in str(e):
            raise
        kw.pop("service_tier", None)
        resp = client.responses.parse(model=model, text_format=schema, max_output_tokens=max_output_tokens,
                                      store=False, **kw)
    u = resp.usage
    usage = {}
    if u:
        usage = {
            "input": u.input_tokens,
            "cached": getattr(u.input_tokens_details, "cached_tokens", 0) or 0,
            "cache_write": getattr(u.input_tokens_details, "cache_write_tokens", 0) or 0,
            "output": u.output_tokens,
            "reasoning": getattr(u.output_tokens_details, "reasoning_tokens", 0) or 0,
        }
    detail = ""
    if resp.incomplete_details is not None:
        detail = str(getattr(resp.incomplete_details, "reason", resp.incomplete_details))
    return Reply(resp.output_parsed, usage, resp.id, resp.status or "completed", detail,
                 getattr(resp, "service_tier", None))


class Client:
    def __init__(self, store: Store, cfg: Config, backend: Backend | None = None):
        self.store = store
        self.cfg = cfg
        self.backend = backend or openai_backend

    def structured(self, *, purpose: str, project_id: str | None, model: str, system: str, user: str,
                   schema: type[BaseModel], max_output_tokens: int,
                   options: dict | None = None) -> tuple[BaseModel, float]:
        options = default_options(self.cfg) if options is None else options
        budget = self.cfg.llm.monthly_budget_usd
        spent = self.store.month_spend()
        # Flex가 거절되면 Standard로 넘어갈 수 있으므로 예산 확인은 Standard 가격으로 한다
        worst = worst_case_cost(model, system, user, max_output_tokens)
        if spent + worst > budget:
            raise BudgetExceeded(spent, budget, worst)

        t0 = time.monotonic()
        try:
            reply = self.backend(model, system, user, schema, max_output_tokens, options)
        except LlmError:
            raise
        except Exception as e:  # 네트워크·인증·모델 오류 등. 토큰을 썼는지 알 수 없으니 0원으로 기록
            self.store.add_llm_call(purpose=purpose, project_id=project_id, model=model, usage={},
                                    cost_usd=0, ok=False, error=f"{type(e).__name__}: {e}"[:500],
                                    duration_ms=int((time.monotonic() - t0) * 1000),
                                    tier=options.get("service_tier"))
            code = next((c for c in _PROVIDER_LIMIT_CODES if c in str(e)), None)
            if code:
                raise BudgetExceeded(spent, budget, worst,
                                     f"OpenAI 쪽 월 한도 도달({code}) — OpenAI 프로젝트 Limits에서 확인") from e
            raise LlmError(f"{type(e).__name__}: {e}") from e

        tier = pricing.normalize_tier(reply.tier)
        cost = pricing.cost(model, reply.usage.get("input", 0), reply.usage.get("cached", 0),
                            reply.usage.get("output", 0), reply.usage.get("cache_write", 0), tier=tier)
        ok = reply.parsed is not None
        error = None if ok else f"응답 미완료({reply.status} {reply.detail})".strip()
        self.store.add_llm_call(purpose=purpose, project_id=project_id, model=model, usage=reply.usage,
                                cost_usd=cost, ok=ok, error=error, response_id=reply.response_id,
                                duration_ms=int((time.monotonic() - t0) * 1000), tier=tier)
        if not ok:
            raise LlmError(error or "구조화 출력 파싱 실패")
        return reply.parsed, cost
