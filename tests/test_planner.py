import subprocess

import pytest

from pm import llm, manifest as mf, notify, planner, pricing, proposals
from pm.config import Config, save_config
from pm.planner import PlanOutput, ProposalDraft
from pm.store import Store


def test_cost_counts_cache_hits_and_writes():
    # gpt-6-sol: 입력 $2, 캐시 입력 $0.2, 캐시 쓰기 $2.5, 출력 $10 (1M 토큰당)
    usd = pricing.cost("gpt-6-sol", input_tokens=1_000_000, cached_tokens=500_000,
                       output_tokens=100_000, cache_write_tokens=100_000)
    assert usd == pytest.approx(0.4 * 2 + 0.5 * 0.2 + 0.1 * 2.5 + 0.1 * 10)


def test_exec_estimate_ranges_grow_with_size():
    s, m, l = (pricing.exec_estimate(x, "gpt-5.3-codex") for x in "SML")
    assert s["low"] < s["high"] <= m["high"] <= l["high"]
    assert s["cap"] == pytest.approx(s["high"] * pricing.CAP_MULTIPLIER, abs=0.01)
    assert 0.1 < s["low"] < 0.3 and 0.5 < s["high"] < 0.8


def _draft(title, **kw):
    base = dict(title=title, kind="maintenance", priority=2, summary="요약", evidence=["근거"],
                finding_ids=[], size="S", risk="low", touches_protected=False, expected_effect="효과",
                ops_cost_change_usd_month=None, ops_cost_assumption="", steps=["단계"], verification=["확인"])
    base.update(kw)
    return ProposalDraft(**base)


class FakeBackend:
    def __init__(self, drafts, usage=None):
        self.drafts = drafts
        self.calls = 0
        self.usage = usage or {"input": 10_000, "cached": 0, "output": 2_000, "reasoning": 1_000}

    def __call__(self, model, system, user, schema, max_output_tokens, options=None):
        self.calls += 1
        return llm.Reply(PlanOutput(proposals=self.drafts, notes=""), dict(self.usage), f"resp_{self.calls}")


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "README.md").write_text("# 데모\n설명")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    m = mf.Manifest(id="demo", name="데모", path=str(root), autonomy="L3",
                    verify={"test": "pytest"}, health=[{"type": "gh_workflow", "workflow": "a.yml"}])
    mf.save(m)
    save_config(Config())
    return m


def test_budget_guard_blocks_before_calling():
    cfg = Config()
    cfg.llm.monthly_budget_usd = 0.001
    backend = FakeBackend([])
    with Store() as st:
        client = llm.Client(st, cfg, backend)
        with pytest.raises(llm.BudgetExceeded):
            client.structured(purpose="plan", project_id="x", model="gpt-6-sol", system="s", user="u",
                              schema=PlanOutput, max_output_tokens=16000)
        assert backend.calls == 0
        assert st.llm_calls() == []


def test_ledger_records_success_and_failure():
    cfg = Config()
    with Store() as st:
        client = llm.Client(st, cfg, FakeBackend([]))
        _, cost = client.structured(purpose="plan", project_id="x", model="gpt-6-sol", system="s", user="u",
                                    schema=PlanOutput, max_output_tokens=1000)
        assert cost == pytest.approx(pricing.cost("gpt-6-sol", 10_000, 0, 2_000))

        def broken(*a):
            raise RuntimeError("인증 실패")

        with pytest.raises(llm.LlmError):
            llm.Client(st, cfg, broken).structured(purpose="plan", project_id="x", model="gpt-6-sol",
                                                   system="s", user="u", schema=PlanOutput, max_output_tokens=1000)
        calls = st.llm_calls()
        assert [c["ok"] for c in calls] == [0, 1]
        assert st.month_spend() == pytest.approx(cost)


def test_plan_caps_dedupes_and_skips_unchanged(project):
    drafts = [_draft("캡션 길이 제한"), _draft("캡션 길이 제한 "), _draft("의존성 정리", priority=4),
              _draft("모델 교체", kind="cost", priority=3), _draft("네 번째")]
    backend = FakeBackend(drafts)
    r = planner.plan("test", backend=backend, notify_=False)
    assert backend.calls == 1
    assert r["projects"]["demo"]["new"] == 3  # 중복 제목은 건너뛰고, 프로젝트당 최대 3개
    with Store() as st:
        titles = sorted(p["title"] for p in st.proposals("demo"))
        assert titles == ["모델 교체", "의존성 정리", "캡션 길이 제한"]
        p = next(p for p in st.proposals("demo") if p["title"] == "캡션 길이 제한")
        assert p["status"] == "proposed"
        assert p["data"]["low_risk"] is True  # 유지보수·S·낮음·보호 경로 없음·실효 L3
        assert p["data"]["exec_cost"]["cap"] > 0

    # 변화가 없으면 호출하지 않는다
    r = planner.plan("test", backend=backend, notify_=False)
    assert backend.calls == 1 and r["projects"]["demo"]["status"] == "unchanged"

    # 강제로 다시 불러도 이미 있는 제안은 새로 만들지 않는다
    planner.plan("test", backend=backend, force=True, notify_=False)
    assert backend.calls == 2
    with Store() as st:
        assert len(st.proposals("demo")) == 4  # 새로 남은 건 '네 번째' 하나


def test_overflow_goes_to_backlog(project, monkeypatch):
    cfg = Config()
    cfg.llm.max_proposals_per_run = 1
    save_config(cfg)
    planner.plan("test", backend=FakeBackend([_draft("급함", priority=1), _draft("덜 급함", priority=5)]),
                 notify_=False)
    with Store() as st:
        status = {p["title"]: p["status"] for p in st.proposals("demo")}
    assert status == {"급함": "proposed", "덜 급함": "backlog"}


def test_approve_writes_work_order_once(project):
    planner.plan("test", backend=FakeBackend([_draft("캡션 길이 제한")]), notify_=False)
    with Store() as st:
        pid = st.proposals("demo")[0]["id"]
        p = proposals.decide(st, pid, "approve", "cli")
        assert p["status"] == "approved"
        md = proposals.work_order_path(pid).read_text(encoding="utf-8")
        assert "# 작업 지시서: 캡션 길이 제한" in md and f"pm/{pid}" in md and "PR까지만" in md
        assert proposals.decide(st, pid, "reject", "cli") is None  # 이미 결정됨


def test_buttons_follow_status():
    p = {"id": "pabc", "status": "proposed"}
    assert len(notify.proposal_buttons(p)[0]) == 3
    assert len(notify.proposal_buttons({**p, "status": "deferred"})[0]) == 2
    assert notify.proposal_buttons({**p, "status": "approved"}) is None


def test_bot_ignores_other_chats_and_handles_buttons(project, monkeypatch):
    from pm.notify import bot, telegram

    answered = []
    monkeypatch.setattr(telegram, "answer_callback", lambda cid, text="": answered.append(text))
    monkeypatch.setattr(telegram, "edit", lambda *a, **k: None)
    planner.plan("test", backend=FakeBackend([_draft("캡션 길이 제한")]), notify_=False)
    with Store() as st:
        pid = st.proposals("demo")[0]["id"]

        def cb(chat):
            return {"callback_query": {"id": "1", "data": f"p:{pid}:approve", "message": {"chat": {"id": chat}}}}

        bot.handle(cb(999), st, chat="111")  # 연결되지 않은 대화 → 무시
        assert st.proposal(pid)["status"] == "proposed" and answered == []
        bot.handle(cb(111), st, chat="111")
        assert st.proposal(pid)["status"] == "approved"
        assert answered == ["승인했습니다"]


def test_get_updates_passes_long_poll_timeout_to_telegram(monkeypatch):
    from pm.notify import telegram

    seen = {}
    monkeypatch.setattr(telegram, "_call", lambda method, http_timeout=20, **p: seen.update(
        method=method, http_timeout=http_timeout, **p) or {"result": []})
    telegram.get_updates(offset=7, timeout=50)
    assert seen["timeout"] == 50 and seen["http_timeout"] == 70 and seen["offset"] == 7


def test_openai_hard_limit_stops_planning():
    cfg = Config()

    def limited(*a):
        raise RuntimeError("Error code: 429 - {'error': {'code': 'project_spend_limit_exceeded'}}")

    with Store() as st:
        with pytest.raises(llm.BudgetExceeded, match="OpenAI 쪽 월 한도"):
            llm.Client(st, cfg, limited).structured(purpose="plan", project_id="x", model="gpt-6-sol",
                                                    system="s", user="u", schema=PlanOutput, max_output_tokens=1000)


def test_flex_tier_is_half_price_and_recorded():
    assert pricing.cost("gpt-6-sol", 10_000, 0, 2_000, tier="flex") == pytest.approx(
        pricing.cost("gpt-6-sol", 10_000, 0, 2_000) / 2)
    assert pricing.normalize_tier("default") == "standard"

    class FlexBackend(FakeBackend):
        def __call__(self, model, system, user, schema, max_output_tokens, options=None):
            r = super().__call__(model, system, user, schema, max_output_tokens, options)
            r.tier = (options or {}).get("service_tier")
            return r

    cfg = Config()
    cfg.llm.service_tier = "flex"
    with Store() as st:
        _, cost = llm.Client(st, cfg, FlexBackend([])).structured(
            purpose="plan", project_id="x", model="gpt-6-sol", system="s", user="u", schema=PlanOutput,
            max_output_tokens=1000)
        assert cost == pytest.approx(pricing.cost("gpt-6-sol", 10_000, 0, 2_000, tier="flex"))
        assert st.llm_calls()[0]["tier"] == "flex"


def test_explicit_cache_puts_breakpoint_only_after_shared_instructions():
    kw = llm._request_kwargs("공통 지시문", "프로젝트별 내용", {"explicit_cache": True, "service_tier": "flex",
                                                           "reasoning_effort": "low", "verbosity": "low"})
    dev, user = kw["input"]
    assert dev["content"][0]["prompt_cache_breakpoint"] == {"mode": "explicit"}
    assert "prompt_cache_breakpoint" not in user["content"][0]
    assert kw["prompt_cache_options"] == {"mode": "explicit"} and "instructions" not in kw
    assert kw["service_tier"] == "flex" and kw["reasoning"] == {"effort": "low"}
    assert kw["text"] == {"verbosity": "low"} and "verbosity" not in kw
