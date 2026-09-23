"""LLM 가격표와 비용 계산.

금액 계산은 LLM에게 맡기지 않고 여기서 한다. LLM은 계산을 자주 틀리기 때문이다.
가격표는 공식 페이지에서 추출한 pm/data/openai_prices.json이 기본이고,
앱 데이터 폴더의 pricing.yaml로 모델별 값을 덮어쓸 수 있다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from . import paths

_DATA = Path(__file__).parent / "data" / "openai_prices.json"
_DATE_SUFFIX = re.compile(r"-\d{4}-\d{2}-\d{2}$")

# P2 실행기(코딩 에이전트)의 작업 규모별 토큰 가정. 실측 원장이 쌓이기 전까지 쓰는 기본값이다.
# 에이전트는 같은 맥락을 반복해서 보내므로 입력의 대부분(80%)이 캐시로 처리된다고 본다.
SIZE_TOKENS = {
    "S": {"input": (150_000, 500_000), "output": (8_000, 30_000)},
    "M": {"input": (500_000, 2_000_000), "output": (30_000, 100_000)},
    "L": {"input": (2_000_000, 6_000_000), "output": (100_000, 300_000)},
}
CACHED_RATIO = 0.8
CAP_MULTIPLIER = 1.5


@dataclass(frozen=True)
class Price:
    input: float
    cached_input: float | None
    output: float | None
    cache_write: float | None = None


@lru_cache(maxsize=1)
def _table() -> dict:
    data = json.loads(_DATA.read_text(encoding="utf-8"))
    override = paths.home() / "pricing.yaml"
    if override.exists():
        extra = yaml.safe_load(override.read_text(encoding="utf-8")) or {}
        data["models"].update(extra.get("models", {}))
    return data


def meta() -> dict:
    t = _table()
    return {"source": t["source"], "as_of": t["as_of"], "tier": t["tier"], "unit": t["unit"]}


def normalize_tier(tier: str | None) -> str:
    """API가 돌려주는 service_tier 값('default', 'auto' 등)을 가격표의 등급 이름으로 맞춘다."""
    return tier if tier in ("flex", "batch") else "standard"


def price(model: str, tier: str | None = "standard") -> Price | None:
    models = _table()["models"]
    p = models.get(model) or models.get(_DATE_SUFFIX.sub("", model))
    if not p or p.get("input") is None:
        return None
    t = normalize_tier(tier)
    if t != "standard" and t in p.get("tiers", {}):
        p = p["tiers"][t]
    return Price(p["input"], p.get("cached_input"), p.get("output"), p.get("cache_write"))


def cost(model: str, input_tokens: int, cached_tokens: int = 0, output_tokens: int = 0,
         cache_write_tokens: int = 0, tier: str | None = "standard") -> float:
    """실제 사용량 → 달러. 캐시 적중·캐시 쓰기 토큰은 input_tokens에 포함된 것으로 본다."""
    p = price(model, tier)
    if p is None:
        raise KeyError(f"가격표에 없는 모델: {model}")
    plain = max(input_tokens - cached_tokens - cache_write_tokens, 0)
    usd = plain * p.input
    usd += cached_tokens * (p.cached_input if p.cached_input is not None else p.input)
    usd += cache_write_tokens * (p.cache_write if p.cache_write is not None else p.input)
    usd += output_tokens * (p.output or 0)
    return usd / 1_000_000


def exec_estimate(size: str, model: str) -> dict:
    """작업 규모 → P2 실행 비용 범위와 상한(넘으면 중단)."""
    p = price(model)
    if p is None:
        raise KeyError(f"가격표에 없는 모델: {model}")
    t = SIZE_TOKENS[size]
    cached = p.cached_input if p.cached_input is not None else p.input
    per_input = (1 - CACHED_RATIO) * p.input + CACHED_RATIO * cached

    def usd(i: int, o: int) -> float:
        return (i * per_input + o * (p.output or 0)) / 1_000_000

    low = usd(t["input"][0], t["output"][0])
    high = usd(t["input"][1], t["output"][1])
    return {
        "low": round(low, 2),
        "high": round(high, 2),
        "cap": round(high * CAP_MULTIPLIER, 2),
        "model": model,
        "basis": "기본 가정 (실측 없음)",
    }
