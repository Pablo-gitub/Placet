"""RFC-17 part 3: the read-only MCP tool for the coordinator view."""
import inspect
import json
from unittest.mock import MagicMock, patch

from mcp_server import server
from mcp_server.client_api import PlacetClient

CRED = "plst_" + "c" * 22


def client():
    return PlacetClient(base_url="http://localhost:8000", agent_id="claude", api_token="secret-token-claude")


def response(body, status=200):
    res = MagicMock()
    res.status_code = status
    res.json.return_value = body
    res.text = json.dumps(body)
    res.raise_for_status.return_value = None
    return res


def test_the_client_reads_with_the_session_credential_in_a_header_never_in_the_url():
    with patch("httpx.Client.get") as get:
        get.return_value = response({"project": {"id": 7}, "requests": [], "next_cursor": None})
        out = client().project_view(7, CRED, cursor="abc", limit=20)
    assert out["project"]["id"] == 7
    args, kwargs = get.call_args
    assert args[0] == "http://localhost:8000/api/projects/7/coordinator-view"
    assert kwargs["headers"]["X-Placet-Session"] == CRED
    assert kwargs["params"] == {"cursor": "abc", "limit": 20}
    assert CRED not in args[0] and CRED not in json.dumps(kwargs["params"])


def test_the_tool_passes_the_page_through_and_never_writes():
    with patch("httpx.Client.get") as get, patch("httpx.Client.post") as post, patch("httpx.Client.put") as put, \
            patch("httpx.Client.delete") as delete:
        get.return_value = response({"project": {"id": 7}, "requests": [{"id": 1}], "next_cursor": "n"})
        text = server.placet_project_view(7, CRED, cursor=None)
    body = json.loads(text)
    assert body["requests"] == [{"id": 1}] and body["next_cursor"] == "n"
    assert not post.called and not put.called and not delete.called
    assert CRED not in text


def test_the_tool_refuses_without_a_credential_before_calling_the_backend():
    with patch("httpx.Client.get") as get:
        for token in ("", "   ", None):
            body = json.loads(server.placet_project_view(7, token))
            assert body["status"] == "ERROR" and "session_token" in body["detail"]
    assert not get.called


def test_a_refusal_from_the_backend_is_reported_with_its_code():
    import httpx
    request = httpx.Request("GET", "http://localhost:8000/api/projects/7/coordinator-view")
    forbidden = httpx.Response(403, request=request, json={"code": "coordinator.not_granted", "params": {}, "detail": "no grant"})
    with patch("httpx.Client.get", return_value=forbidden):
        body = json.loads(server.placet_project_view(7, CRED))
    assert body["code"] == 403 and body["product_code"] == "coordinator.not_granted"


def test_the_description_says_it_is_read_only_and_authorizes_no_send():
    doc = inspect.getdoc(server.placet_project_view).lower()
    assert "read-only" in doc and "does not authorize" in doc and "send" in doc
    assert "session_token" in doc
    params = inspect.signature(server.placet_project_view).parameters
    assert list(params) == ["project_id", "session_token", "cursor"] and params["cursor"].default is None
