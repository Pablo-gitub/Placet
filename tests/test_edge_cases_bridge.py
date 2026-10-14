import os
import tempfile
import time
import pytest
from unittest.mock import MagicMock, patch, call
from bridge.tmux_controller import TmuxController
from bridge.bridge_daemon import BridgeDaemon
from backend.app.database import Database
from backend.app.config import PlacetConfig, AgentSpec
from backend.app.models import RequestCreate, RequestStatus, QuestionType


# Test 1: Multiline Prompt Injection via tmux paste-buffer
def test_multiline_prompt_injection_uses_paste_buffer():
    controller = TmuxController()
    multiline_code = (
        "def hello():\n"
        "    print('Line 1')\n"
        "    print('Line 2')\n"
    )

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        success = controller.send_keys("agent-claude", multiline_code, enter=True)
        assert success is True

        # Should use tmux load-buffer / set-buffer and paste-buffer
        called_cmds = [c[0][0] for c in mock_run.call_args_list]
        
        # Verify paste-buffer was used for multiline input
        has_buffer_op = any("load-buffer" in cmd or "set-buffer" in cmd for cmd in called_cmds)
        has_paste_op = any("paste-buffer" in cmd for cmd in called_cmds)
        assert has_buffer_op, f"Expected set/load-buffer in commands: {called_cmds}"
        assert has_paste_op, f"Expected paste-buffer in commands: {called_cmds}"


# Test 2: ANSI escape sequences stripping in idle detection
def test_ansi_escape_sequences_handled_in_idle_check():
    controller = TmuxController()

    with patch.object(controller, "capture_pane") as mock_capture:
        # Terminal output with ANSI color codes before prompt
        colored_prompt = "\x1b[32muser@host\x1b[0m:\x1b[34m~/placet\x1b[0m$ "
        mock_capture.return_value = f"Some text\n{colored_prompt}"
        assert controller.is_session_idle("agent-claude") is True


# Test 3: Concurrency Throttling - Multiple requests for the same session
def test_bridge_daemon_throttles_multiple_requests_for_same_session():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(db_path)
    db.init_db()

    config = PlacetConfig(
        agents={
            "claude": AgentSpec(tmux_session="agent-claude", allowed_targets=["gemini"]),
            "gemini": AgentSpec(tmux_session="agent-gemini", allowed_targets=["claude"]),
        }
    )

    mock_tmux = MagicMock(spec=TmuxController)
    mock_tmux.session_exists.return_value = True
    mock_tmux.is_session_idle.return_value = True
    mock_tmux.send_keys.return_value = True

    daemon = BridgeDaemon(db=db, config=config, tmux=mock_tmux)

    # Create TWO approved requests both targeting "gemini"
    req1 = db.create_request(RequestCreate(
        sender_id="claude", recipient_id="gemini", prompt="Task 1"
    ))
    db.update_request_decision(req1.id, RequestStatus.APPROVED)

    req2 = db.create_request(RequestCreate(
        sender_id="claude", recipient_id="gemini", prompt="Task 2"
    ))
    db.update_request_decision(req2.id, RequestStatus.APPROVED)

    # In one pass, daemon should only inject AT MOST ONE task into agent-gemini
    # to avoid stomping the same terminal pane simultaneously!
    processed = daemon.process_pending_injections()
    assert processed == 1

    # First request should be EXECUTING
    assert db.get_request(req1.id).status == RequestStatus.EXECUTING
    # Second request should still be APPROVED (queued for next pass)
    assert db.get_request(req2.id).status == RequestStatus.APPROVED

    if os.path.exists(db_path):
        os.remove(db_path)


# Test 4: Missing tmux binary handling
def test_missing_tmux_binary_handled_gracefully():
    controller = TmuxController()
    with patch("subprocess.run", side_effect=FileNotFoundError("No tmux")):
        assert controller.list_sessions() == []
        assert controller.session_exists("agent-claude") is False
        assert controller.capture_pane("agent-claude") == ""
        assert controller.is_session_idle("agent-claude") is False
        assert controller.send_keys("agent-claude", "test") is False
