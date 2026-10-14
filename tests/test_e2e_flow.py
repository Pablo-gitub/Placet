import os
import tempfile
import pytest
from unittest.mock import MagicMock
from backend.app.database import Database
from backend.app.config import PlacetConfig, AgentSpec, RouteRule
from backend.app.models import RequestCreate, RequestStatus, QuestionType, RequestDecision
from bridge.tmux_controller import TmuxController
from bridge.bridge_daemon import BridgeDaemon


def test_complete_end_to_end_flow():
    # 1. Setup Database and Config
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(db_path)
    db.init_db()

    config = PlacetConfig(
        agents={
            "claude": AgentSpec(name="Claude Code", tmux_session="agent-claude", allowed_targets=["gemini", "human"]),
            "gemini": AgentSpec(name="Gemini CLI", tmux_session="agent-gemini", allowed_targets=["claude", "human"]),
        },
        rules={
            "claude -> gemini": RouteRule(require_approval=True),
        },
    )

    # 2. Agent A (Claude) creates a request for Agent B (Gemini)
    assert config.is_route_allowed("claude", "gemini") is True
    assert config.requires_approval("claude", "gemini") is True

    req = db.create_request(RequestCreate(
        sender_id="claude",
        recipient_id="gemini",
        question_type=QuestionType.PROMPT_REVIEW,
        prompt="Implementa Fibonacci",
        context="Modulo di utility matematiche",
    ))
    assert req.id is not None
    assert req.status == RequestStatus.PENDING

    # 3. Paolo receives notification and approves with edited prompt on iPhone
    updated_req = db.update_request_decision(
        request_id=req.id,
        status=RequestStatus.APPROVED,
        edited_prompt="Implementa Fibonacci con memoization in Python",
    )
    assert updated_req.status == RequestStatus.APPROVED
    assert updated_req.edited_prompt == "Implementa Fibonacci con memoization in Python"

    # 4. Bridge daemon inspects target session (agent-gemini), checks idle, and injects
    mock_tmux = MagicMock(spec=TmuxController)
    mock_tmux.session_exists.return_value = True
    mock_tmux.is_session_idle.return_value = True
    mock_tmux.send_keys.return_value = True

    bridge = BridgeDaemon(db=db, config=config, tmux=mock_tmux)
    injected_count = bridge.process_pending_injections()
    assert injected_count == 1

    # Verify tmux send-keys call
    mock_tmux.send_keys.assert_called_once()
    session_arg, envelope_arg = mock_tmux.send_keys.call_args[0]
    assert session_arg == "agent-gemini"
    assert f"[Placet Request #{req.id} da claude]" in envelope_arg
    assert "Implementa Fibonacci con memoization in Python" in envelope_arg
    assert f"placet_reply(request_id={req.id}" in envelope_arg

    # Verify request transitioned to EXECUTING
    executing_req = db.get_request(req.id)
    assert executing_req.status == RequestStatus.EXECUTING

    # 5. Agent B (Gemini) completes task and calls placet_reply
    final_req = db.record_reply(
        request_id=req.id,
        responder_id="gemini",
        content="def fib(n, memo={}): return n if n <= 1 else memo.setdefault(n, fib(n-1, memo) + fib(n-2, memo))",
    )
    assert final_req.status == RequestStatus.COMPLETED
    assert final_req.response is not None
    assert final_req.response.responder_id == "gemini"
    assert "memo.setdefault" in final_req.response.content

    if os.path.exists(db_path):
        os.remove(db_path)
