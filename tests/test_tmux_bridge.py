import pytest
from unittest.mock import MagicMock, patch
from bridge.tmux_controller import TmuxController
from bridge.bridge_daemon import BridgeDaemon
from backend.app.database import Database
from backend.app.config import PlacetConfig, AgentSpec
from backend.app.models import RequestCreate, RequestStatus, QuestionType
import tempfile
import os


def test_tmux_controller_is_idle_detection():
    controller = TmuxController()

    with patch.object(controller, "capture_pane") as mock_capture:
        # Standard bash prompt
        mock_capture.return_value = "line 1\nline 2\nuser@host:~/placet$ "
        assert controller.is_session_idle("agent-claude") is True

        # Zsh / starship prompt
        mock_capture.return_value = "workspace on main\n❯ "
        assert controller.is_session_idle("agent-claude") is True

        # Claude Code prompt
        mock_capture.return_value = "╭─── Claude Code ───╮\n│ Welcome           │\n╰───────────────────╯\n> "
        assert controller.is_session_idle("agent-claude") is True

        # Busy process compiling or thinking
        mock_capture.return_value = "Generating response...\n[Thinking] Analyzing files..."
        assert controller.is_session_idle("agent-claude") is False


def test_tmux_controller_send_keys():
    controller = TmuxController()
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        
        success = controller.send_keys("agent-gemini", "echo 'hello world'", enter=True)
        assert success is True
        
        # Verify tmux send-keys was called with -l (literal) and Enter
        assert mock_run.call_count == 2
        first_call = mock_run.call_args_list[0]
        assert first_call[0][0] == ["tmux", "send-keys", "-t", "agent-gemini", "-l", "echo 'hello world'"]
        second_call = mock_run.call_args_list[1]
        assert second_call[0][0] == ["tmux", "send-keys", "-t", "agent-gemini", "Enter"]


def test_bridge_daemon_process_approved_request():
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

    # Create approved request
    req = db.create_request(RequestCreate(
        sender_id="claude",
        recipient_id="gemini",
        question_type=QuestionType.PROMPT_REVIEW,
        prompt="Controlla questo algoritmo",
    ))
    db.update_request_decision(req.id, RequestStatus.APPROVED, edited_prompt="Controlla algoritmo e calcola big-O")

    # Run one pass of bridge daemon
    processed_count = daemon.process_pending_injections()
    assert processed_count == 1

    # Check that tmux send-keys was invoked with envelope
    mock_tmux.send_keys.assert_called_once()
    args, kwargs = mock_tmux.send_keys.call_args
    target_session, injected_text = args[0], args[1]
    assert target_session == "agent-gemini"
    assert f"[Placet Request #{req.id} da claude]" in injected_text
    assert "Controlla algoritmo e calcola big-O" in injected_text
    assert f"placet_reply(request_id={req.id}" in injected_text

    # Verify request status in DB transitioned to EXECUTING
    updated_req = db.get_request(req.id)
    assert updated_req.status == RequestStatus.DELIVERING

    if os.path.exists(db_path):
        os.remove(db_path)
