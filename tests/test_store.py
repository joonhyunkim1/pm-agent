from pm.checks import Finding, Outcome
from pm.store import Store, fingerprint


def _apply(st, findings, status=None):
    out = Outcome.of("x", findings)
    if status:
        out.status = status
    return st.apply_findings("p", "c", out)


def _status(st, key):
    return st.finding(fingerprint("p", "c", key))["status"]


def test_new_finding_opens_and_is_reported_once():
    st = Store()
    f = Finding("a", "critical", "문제")
    assert _apply(st, [f]) == [fingerprint("p", "c", "a")]
    assert _apply(st, [f]) == []
    assert _status(st, "a") == "open"


def test_missing_finding_resolves_then_reopens():
    st = Store()
    f = Finding("a", "warning", "문제")
    _apply(st, [f])
    _apply(st, [])
    assert _status(st, "a") == "resolved"
    assert _apply(st, [f]) == [fingerprint("p", "c", "a")]
    assert _status(st, "a") == "open"


def test_error_or_skipped_run_does_not_resolve():
    st = Store()
    _apply(st, [Finding("a", "warning", "문제")])
    _apply(st, [], status="error")
    _apply(st, [], status="skipped")
    assert _status(st, "a") == "open"


def test_acknowledged_reopens_only_on_escalation():
    st = Store()
    fp = fingerprint("p", "c", "a")
    _apply(st, [Finding("a", "warning", "문제")])
    st.set_finding_status(fp, "acknowledged")
    assert _apply(st, [Finding("a", "warning", "문제")]) == []
    assert _status(st, "a") == "acknowledged"
    assert _apply(st, [Finding("a", "critical", "더 심각")]) == [fp]
    assert _status(st, "a") == "open"


def test_ignored_stays_ignored():
    st = Store()
    fp = fingerprint("p", "c", "a")
    _apply(st, [Finding("a", "info", "문제")])
    st.set_finding_status(fp, "ignored")
    _apply(st, [])
    _apply(st, [Finding("a", "critical", "문제")])
    assert _status(st, "a") == "ignored"


def test_unnotified_and_mark_notified():
    st = Store()
    _apply(st, [Finding("a", "critical", "x"), Finding("b", "warning", "y")])
    rows = st.unnotified("critical")
    assert [r["key"] for r in rows] == ["a"]
    st.mark_notified([r["fingerprint"] for r in rows])
    assert st.unnotified("critical") == []


def test_scan_due_ignores_light_runs():
    st = Store()
    full = st.start_run("schedule")
    st.finish_run(full, "ok", {})
    light = st.start_run("schedule-light")
    st.finish_run(light, "ok", {})
    assert st.last_finished_run()["id"] == light
    assert st.last_finished_run(full_only=True)["id"] == full
