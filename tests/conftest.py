import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """테스트가 실제 앱 데이터 폴더(DB·매니페스트)를 건드리지 않게 한다."""
    monkeypatch.setenv("PM_AGENT_HOME", str(tmp_path / "home"))
    return tmp_path / "home"
