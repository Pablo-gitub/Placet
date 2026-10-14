import pytest

from tests.helpers import HUMAN_LOGIN


@pytest.fixture(autouse=True)
def _allowed_human(monkeypatch):
    # Apps built without an explicit allowed_users read this variable.
    monkeypatch.setenv("PLACET_ALLOWED_USERS", HUMAN_LOGIN)
