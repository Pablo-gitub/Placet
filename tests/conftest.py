import pytest

from tests.helpers import HUMAN_LOGIN


@pytest.fixture(autouse=True)
def _allowed_human(monkeypatch):
    # Apps built without an explicit allowed_users read this variable.
    monkeypatch.setenv("PLACET_ALLOWED_USERS", HUMAN_LOGIN)
    # Legacy scenarios explicitly select Italian; server-locale tests override it.
    monkeypatch.setenv("PLACET_DEFAULT_LANGUAGE", "it")


@pytest.fixture(autouse=True)
def _no_real_notifications(monkeypatch):
    # Never publish to a real ntfy server from tests.
    for var in ("PLACET_NTFY_URL", "PLACET_NTFY_TOPIC", "PLACET_NTFY_TOKEN", "PLACET_BASE_URL",
                "PLACET_BRIDGE_TOKEN"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _no_real_usage(monkeypatch):
    # The bridge must not read this machine's provider usage (or ask Anthropic)
    # in tests: a real limit would hold the test's messages.
    monkeypatch.setattr("bridge.bridge_daemon.read_usage", lambda **kw: [])
