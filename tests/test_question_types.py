"""
RF-02 / RF-03: single and multiple choice, always with "Altro" (free text),
confirmation questions, and prompt review for agent-to-agent requests.
"""
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from mcp_server.client_api import PlacetClient
from tests.helpers import agent_client, human_client

CONFIG = PlacetConfig(
    agents={
        "claude": AgentSpec(tmux_session="agent-claude", token="tok-claude",
                            allowed_targets=["gemini", "human"]),
        "gemini": AgentSpec(tmux_session="agent-gemini", token="tok-gemini",
                            allowed_targets=["claude", "human"]),
    }
)


@pytest.fixture
def app():
    db = Database(os.path.join(tempfile.mkdtemp(), "placet.db"))
    db.init_db()
    return create_app(db=db, config=CONFIG)


async def _create(app, **body):
    body.setdefault("recipient_id", "human")
    body.setdefault("prompt", "Domanda?")
    async with agent_client(app, "tok-claude") as client:
        return await client.post("/api/requests", json=body)


async def _decide(app, req_id, **body):
    body.setdefault("status", "APPROVED")
    async with human_client(app) as human:
        return await human.post(f"/api/requests/{req_id}/decision", json=body)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"question_type": "single_choice", "options": []},
    {"question_type": "multi_choice"},
    {"question_type": "confirmation", "options": ["A"]},
    {"question_type": "single_choice", "options": ["A", "A"]},
    {"question_type": "single_choice", "options": ["A", "  "]},
    {"recipient_id": "gemini", "question_type": "single_choice", "options": ["A"]},
])
async def test_invalid_questions_rejected(app, body):
    assert (await _create(app, **body)).status_code == 422


@pytest.mark.asyncio
async def test_open_question_to_human_becomes_confirmation(app):
    resp = await _create(app, question_type="prompt_review")
    assert resp.json()["question_type"] == "confirmation"


@pytest.mark.asyncio
@pytest.mark.parametrize("decision, ok", [
    ({"selected_options": ["A"]}, True),
    ({"custom_text": "Né A né B"}, True),                        # "Altro"
    ({"selected_options": ["A", "B"]}, False),
    ({"selected_options": ["A"], "custom_text": "anche altro"}, False),
    ({}, False),
    ({"custom_text": "   "}, False),
    ({"selected_options": ["Z"]}, False),
    ({"selected_options": ["A"], "edited_prompt": "x"}, False),
    ({"status": "REJECTED"}, True),
    ({"status": "REJECTED", "custom_text": "Non ora"}, True),
    ({"status": "REJECTED", "selected_options": ["A"]}, False),
])
async def test_single_choice_answers(app, decision, ok):
    req = (await _create(app, question_type="single_choice", options=["A", "B"])).json()
    resp = await _decide(app, req["id"], **decision)
    assert resp.status_code == (200 if ok else 422), resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("decision, ok", [
    ({"selected_options": ["A", "B"]}, True),
    ({"selected_options": ["A"], "custom_text": "e anche C"}, True),
    ({"custom_text": "solo C"}, True),
    ({"selected_options": ["A", "A"]}, False),
    ({}, False),
])
async def test_multi_choice_answers(app, decision, ok):
    req = (await _create(app, question_type="multi_choice", options=["A", "B"])).json()
    resp = await _decide(app, req["id"], **decision)
    assert resp.status_code == (200 if ok else 422), resp.text
    if ok:
        assert resp.json()["selected_options"] == decision.get("selected_options", [])
        assert resp.json()["custom_text"] == decision.get("custom_text")


@pytest.mark.asyncio
async def test_confirmation_accepts_note_but_no_options(app):
    req = (await _create(app, question_type="confirmation")).json()
    assert (await _decide(app, req["id"], selected_options=["A"])).status_code == 422
    resp = await _decide(app, req["id"], custom_text="Ok, ma solo in bozza")
    assert resp.status_code == 200
    assert resp.json()["custom_text"] == "Ok, ma solo in bozza"


@pytest.mark.asyncio
async def test_prompt_review_allows_editing(app):
    req = (await _create(app, recipient_id="gemini", question_type="prompt_review")).json()
    resp = await _decide(app, req["id"], edited_prompt="Prompt corretto")
    assert resp.status_code == 200
    assert resp.json()["edited_prompt"] == "Prompt corretto"


@pytest.mark.parametrize("options, multiple, expected", [
    (None, False, "confirmation"),
    (["A", "B"], False, "single_choice"),
    (["A", "B"], True, "multi_choice"),
])
def test_mcp_client_question_type(options, multiple, expected):
    client = PlacetClient(base_url="http://placet", api_token="tok")
    with patch("httpx.Client.post") as post:
        post.return_value = MagicMock(json=lambda: {}, raise_for_status=lambda: None)
        client.ask_human("Domanda?", options=options, multiple=multiple)
    assert post.call_args.kwargs["json"]["question_type"] == expected
