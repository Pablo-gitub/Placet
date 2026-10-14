import pytest
from unittest.mock import patch, MagicMock
from mcp_server.client_api import PlacetClient


@pytest.fixture
def mock_client():
    client = PlacetClient(
        base_url="http://localhost:8000",
        agent_id="claude",
        api_token="secret-token-claude",
    )
    return client


def test_ask_human_tool_logic(mock_client):
    with patch("httpx.Client.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "id": 42,
            "status": "APPROVED",
            "edited_prompt": None,
            "custom_text": "Procedi pure",
            "selected_options": ["Approccio B"],
        }
        mock_post.return_value = mock_response

        res = mock_client.ask_human(
            question="Quale approccio preferisci?",
            options=["Approccio A", "Approccio B"],
            context="Refactoring del modulo bridge",
            timeout_seconds=120,
        )

        assert res["status"] == "APPROVED"
        assert res["selected_options"] == ["Approccio B"]

        # Verify POST payload sent to Placet backend
        assert mock_post.called
        call_args, call_kwargs = mock_post.call_args
        payload = call_kwargs["json"]
        assert payload["sender_id"] == "claude"
        assert payload["recipient_id"] == "human"
        assert payload["options"] == ["Approccio A", "Approccio B"]


def test_send_to_agent_tool_logic(mock_client):
    with patch("httpx.Client.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "id": 99,
            "status": "COMPLETED",
            "response": {
                "responder_id": "gemini",
                "content": "Ho analizzato i file, tutto ok.",
            },
        }
        mock_post.return_value = mock_response

        res = mock_client.send_to_agent(
            target_agent="gemini",
            prompt="Analizza i file di test",
            context="Task di verifica",
            timeout_seconds=60,
        )

        assert res["status"] == "COMPLETED"
        assert "Ho analizzato" in res["response"]["content"]


def test_reply_tool_logic(mock_client):
    with patch("httpx.Client.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": 99,
            "status": "COMPLETED",
            "response": {"responder_id": "claude", "content": "Risposta completata"},
        }
        mock_post.return_value = mock_response

        res = mock_client.reply(request_id=99, response="Risposta completata")
        assert res["status"] == "COMPLETED"


def test_list_agents_tool_logic(mock_client):
    with patch("httpx.Client.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "agents": {
                "claude": {"name": "Claude Code", "tmux_session": "agent-claude"},
                "gemini": {"name": "Gemini CLI", "tmux_session": "agent-gemini"},
            },
            "rules": {"claude -> gemini": {"require_approval": True}},
        }
        mock_get.return_value = mock_response

        res = mock_client.list_agents()
        assert "claude" in res["agents"]
        assert "gemini" in res["agents"]


def test_server_tools_wrapper():
    from mcp_server.server import placet_ask_human, placet_list_agents, client
    with patch.object(client, "ask_human") as mock_ask:
        mock_ask.return_value = {"id": 10, "status": "APPROVED"}
        res_str = placet_ask_human("Test question?", ["Yes", "No"])
        import json
        data = json.loads(res_str)
        assert data["id"] == 10
        assert data["status"] == "APPROVED"

