from httpx import AsyncClient, ASGITransport

from backend.app.auth import HumanListener

HUMAN_LOGIN = "paolo@test"


def agent_client(app, token=None) -> AsyncClient:
    """Client on the agent listener, optionally with a Bearer token."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers)


def human_client(app, login=HUMAN_LOGIN) -> AsyncClient:
    """Client on the human listener, as proxied by `tailscale serve`."""
    headers = {"Tailscale-User-Login": login} if login else {}
    return AsyncClient(
        transport=ASGITransport(app=HumanListener(app)), base_url="http://test", headers=headers
    )
