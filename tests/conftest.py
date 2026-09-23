import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """테스트가 실제 앱 데이터 폴더(DB·매니페스트)를 건드리지 않게 한다."""
    monkeypatch.setenv("PM_AGENT_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture(autouse=True)
def fake_keychain(monkeypatch):
    """실제 키체인(Telegram·OpenAI 키)을 읽지 않게 한다. 읽으면 테스트 중에 진짜 메시지가 나갈 수 있다."""
    import keyring

    store: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(keyring, "get_password", lambda s, n: store.get((s, n)))
    monkeypatch.setattr(keyring, "set_password", lambda s, n, v: store.__setitem__((s, n), v))
    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "OPENAI_API_KEY", "DR_SITE_URL"):
        monkeypatch.delenv(f"PM_SECRET_{name}", raising=False)
    return store
