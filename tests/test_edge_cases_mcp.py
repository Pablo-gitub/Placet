import json
import pytest
from unittest.mock import patch, MagicMock
import httpx

from mcp_server.client_api import PlacetClient
from mcp_server.server import (
    placet_ask_human,
    placet_send_to_agent,
    placet_reply,
    placet_check_status,
)


@pytest.fixture
def mock_client():
    return PlacetClient(
        base_url="http://localhost:8000",
        agent_id="claude",
        api_token="token-claude",
    )


# Test 1: Backend Unreachable / Connection error returns structured JSON error
def test_mcp_backend_down_returns_structured_error():
    with patch("httpx.Client.post", side_effect=httpx.ConnectError("Connection refused")):
        res_str = placet_ask_human("Can I proceed?", context="Deploy in corso", options=["Yes", "No"])
        res = json.loads(res_str)
        assert res["status"] == "ERROR"
        assert "Connection refused" in res["error"]
        assert "backend unreachable" in res["detail"].lower() or "error" in res["detail"].lower()


# Test 2: Timeout bounds clamping
def test_timeout_bounds_clamping(mock_client):
    with patch("httpx.Client.post") as mock_post:
        mock_response = MagicMock(status_code=201)
        mock_response.json.return_value = {"id": 1, "status": "PENDING"}
        mock_post.return_value = mock_response

        # Pass 0 or negative timeout -> should clamp to min safe timeout (e.g. 5s)
        mock_client.send_to_agent("gemini", "Task", timeout_seconds=-50)
        params = mock_post.call_args[1]["params"]
        assert params["wait_seconds"] >= 5

        # Pass huge timeout -> should clamp to max safe timeout (e.g. 600s)
        mock_client.send_to_agent("gemini", "Task", timeout_seconds=99999)
        params = mock_post.call_args[1]["params"]
        assert params["wait_seconds"] <= 600


# Test 3: HTTP 403 route forbidden returns structured JSON error
def test_mcp_forbidden_route_returns_structured_error():
    mock_resp = MagicMock(status_code=403)
    mock_resp.json.return_value = {"detail": "Route from claude to unknown not allowed."}
    http_error = httpx.HTTPStatusError("Forbidden", request=MagicMock(), response=mock_resp)

    with patch("httpx.Client.post", side_effect=http_error):
        res_str = placet_send_to_agent("unknown", "Task")
        res = json.loads(res_str)
        assert res["status"] == "ERROR"
        assert res["code"] == 403
        assert "not allowed" in res["detail"]


# Test 4: Check status on non-existent request returns 404 structured error
def test_mcp_check_status_not_found():
    mock_resp = MagicMock(status_code=404)
    mock_resp.json.return_value = {"detail": "Request not found"}
    http_error = httpx.HTTPStatusError("Not Found", request=MagicMock(), response=mock_resp)

    with patch("httpx.Client.get", side_effect=http_error):
        res_str = placet_check_status(9999)
        res = json.loads(res_str)
        assert res["status"] == "ERROR"
        assert res["code"] == 404
