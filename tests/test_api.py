import pytest
from fastapi.testclient import TestClient

from pm import api, scanner


@pytest.fixture
def client(monkeypatch):
    # 실제 스캔·계획 대신 호출 여부만 기록한다
    started = []
    monkeypatch.setattr(scanner, "scan", lambda *a, **k: started.append("scan"))
    c = TestClient(api.app, base_url="http://localhost")
    c.started = started
    return c


def test_cross_site_post_is_rejected(client):
    r = client.post("/api/scan", headers={"Origin": "https://evil.example", "X-PM-Agent": "1"})
    assert r.status_code == 403


def test_post_without_dashboard_header_is_rejected(client):
    # 본문 없는 POST는 브라우저가 사전 요청 없이 다른 사이트에서도 보낼 수 있다
    assert client.post("/api/scan").status_code == 403
    assert client.post("/api/plan").status_code == 403
    assert client.delete("/api/ideas/1").status_code == 403
    assert client.started == []


def test_dashboard_requests_still_work(client):
    h = {"X-PM-Agent": "1", "Origin": "http://localhost:5173"}
    assert client.post("/api/ideas", json={"title": "메모"}, headers=h).status_code == 200
    assert client.get("/api/overview").status_code == 200  # 읽기는 헤더 없이도 된다
