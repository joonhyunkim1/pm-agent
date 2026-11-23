import pytest

from pm import manifest as mf, notify, planner, scanner
from pm.checks import Finding, Outcome
from pm.config import Config, save_config
from pm.store import Store


@pytest.fixture
def two_projects(tmp_path):
    save_config(Config())
    for pid in ("live", "frozen"):
        (tmp_path / pid).mkdir()
        mf.save(mf.Manifest(id=pid, name=pid, path=str(tmp_path / pid), autonomy="L2"))
    m = mf.load("frozen")
    m.paused_at, m.paused_reason = "2026-11-23T13:00:00+00:00", "OpenAI 사용 중지"
    mf.save(m)


def test_paused_project_is_not_scanned_or_planned(two_projects):
    r = scanner.scan("test", notify_=False)
    assert set(r["projects"]) == {"live"}
    assert [m.id for m in planner._targets(mf.load_all()[0], None)] == ["live"]


def test_llm_pause_blocks_planning(two_projects):
    cfg = Config()
    cfg.llm.paused = True
    save_config(cfg)
    with pytest.raises(planner.LlmPaused):
        planner.plan("test", notify_=False)


def test_paused_project_alerts_are_held_and_digest_marks_it(two_projects, monkeypatch):
    from pm.notify import telegram

    sent = []
    monkeypatch.setattr(telegram, "configured", lambda: True)
    monkeypatch.setattr(telegram, "send", lambda text, buttons=None: sent.append(text) or 1)
    with Store() as st:
        for pid in ("live", "frozen"):
            st.apply_findings(pid, "c", Outcome.of("x", [Finding("a", "critical", f"{pid} 장애")]))
        manifests = mf.load_all()[0]
        assert notify.push_alerts(st, manifests, Config()) == 1
        assert "live 장애" in sent[0] and "frozen" not in sent[0]
        digest = notify.format_digest(st, manifests, None, 8765)
        assert "⏸ <b>frozen</b> — 일시정지 (OpenAI 사용 중지)" in digest
