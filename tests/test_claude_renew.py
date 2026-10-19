"""Tests for Claude credential renewal (Part A: automatic renewal)."""
import json
import logging
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.app.database import Database
from bridge.bridge_daemon import BridgeDaemon
from bridge.stores import HttpStore, LocalStore, Work
from bridge.usage import (
    CLAUDE_RENEW_COOLDOWN_SECONDS,
    claude_token_needs_renew,
    renew_claude_credentials,
)
from tests.helpers import agent_client, human_client
from tests.test_session_messages import setup  # noqa: F401


@pytest.mark.asyncio
async def test_claude_renew_setting_endpoints_require_human(setup):
    app, db = setup
    # Default is disabled (False)
    async with human_client(app) as human:
        res = await human.get("/api/settings/claude-renew")
        assert res.status_code == 200
        assert res.json() == {"enabled": False}

        # Human can enable it
        res = await human.put("/api/settings/claude-renew", json={"enabled": True})
        assert res.status_code == 200
        assert res.json() == {"enabled": True}

        # Check GET reflects update
        res = await human.get("/api/settings/claude-renew")
        assert res.status_code == 200
        assert res.json() == {"enabled": True}

        # Human can disable it
        res = await human.put("/api/settings/claude-renew", json={"enabled": False})
        assert res.status_code == 200
        assert res.json() == {"enabled": False}

    # Agents and bridge cannot read or write setting
    for token in ["tok-claude", "tok-bridge"]:
        async with agent_client(app, token) as caller:
            assert (await caller.get("/api/settings/claude-renew")).status_code == 403
            assert (await caller.put("/api/settings/claude-renew", json={"enabled": True})).status_code == 403


@pytest.mark.asyncio
async def test_work_carries_claude_auto_renew(setup):
    app, db = setup
    # Initially False
    async with agent_client(app, "tok-bridge") as bridge_api:
        work_data = (await bridge_api.get("/api/bridge/work")).json()
        assert work_data.get("claude_auto_renew") is False

    # Enable via human
    async with human_client(app) as human:
        await human.put("/api/settings/claude-renew", json={"enabled": True})

    async with agent_client(app, "tok-bridge") as bridge_api:
        work_data = (await bridge_api.get("/api/bridge/work")).json()
        assert work_data.get("claude_auto_renew") is True

    # Test LocalStore
    local_store = LocalStore(db, MagicMock())
    assert local_store.work().claude_auto_renew is True

    db.set_setting("claude_auto_renew", "false")
    assert local_store.work().claude_auto_renew is False


def test_claude_token_needs_renew(tmp_path):
    creds_file = tmp_path / ".claude" / ".credentials.json"
    creds_file.parent.mkdir(parents=True, exist_ok=True)

    # Missing file -> False
    assert claude_token_needs_renew(home=tmp_path) is False

    # Token expires in 2 hours -> False
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    creds_file.write_text(json.dumps({"claudeAiOauth": {"expiresAt": now_ms + 7200 * 1000}}))
    assert claude_token_needs_renew(home=tmp_path) is False

    # Token expires in 5 minutes (< 10 min) -> True
    creds_file.write_text(json.dumps({"claudeAiOauth": {"expiresAt": now_ms + 300 * 1000}}))
    assert claude_token_needs_renew(home=tmp_path) is True

    # Token already expired -> True
    creds_file.write_text(json.dumps({"claudeAiOauth": {"expiresAt": now_ms - 1000}}))
    assert claude_token_needs_renew(home=tmp_path) is True


def test_renew_command_no_shell_and_no_output_capture():
    with patch("shutil.which", return_value="/usr/local/bin/claude"):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            ok = renew_claude_credentials()
            assert ok is True
            mock_run.assert_called_once()
            args, kwargs = mock_run.call_args
            # Must be a list of arguments, no shell
            assert args[0] == ["/usr/local/bin/claude", "auth", "status"]
            assert kwargs.get("shell") is not True
            # Stdout and stderr must be DEVNULL (discarded, no leaks)
            assert kwargs.get("stdout") == subprocess.DEVNULL
            assert kwargs.get("stderr") == subprocess.DEVNULL
            assert kwargs.get("timeout") == 30.0


def test_renew_missing_claude_logs_warning_once(caplog):
    # Reset warned state if any
    import bridge.usage as bu
    bu._claude_missing_warned = False

    with patch("shutil.which", return_value=None):
        with patch("subprocess.run") as mock_run:
            with caplog.at_level(logging.WARNING):
                # First attempt logs warning
                ok1 = renew_claude_credentials()
                assert ok1 is False
                assert mock_run.call_count == 0
                assert any("claude CLI binary not found" in record.message for record in caplog.records)

                caplog.clear()
                # Second attempt does NOT log warning again
                ok2 = renew_claude_credentials()
                assert ok2 is False
                assert mock_run.call_count == 0
                assert not any("claude CLI binary not found" in record.message for record in caplog.records)


def test_bridge_auto_renew_condition_and_cooldown(monkeypatch):
    monkeypatch.setattr("bridge.bridge_daemon.read_usage", lambda claude=None, **kw: [claude()] if claude else [])
    store = MagicMock()
    daemon = BridgeDaemon(store=store)

    # 1. When auto_renew is disabled, renewal is never called
    with patch("bridge.bridge_daemon.claude_token_needs_renew", return_value=True):
        with patch("bridge.bridge_daemon.renew_claude_credentials") as mock_renew:
            work = Work(claude_auto_renew=False)
            store.work.return_value = work
            daemon.run_loop_step()
            assert mock_renew.call_count == 0

    # 2. When auto_renew is enabled and token needs renew, renewal is called
    with patch("bridge.bridge_daemon.claude_token_needs_renew", return_value=True):
        with patch("bridge.bridge_daemon.renew_claude_credentials", return_value=True) as mock_renew:
            with patch("bridge.bridge_daemon.claude_usage", return_value={"provider": "claude-code", "buckets": []}) as mock_usage:
                work = Work(claude_auto_renew=True)
                store.work.return_value = work
                daemon.run_loop_step()
                assert mock_renew.call_count == 1
                # Should have reset claude usage cache and reported fresh usage
                assert mock_usage.call_count >= 1
                store.report_usage.assert_called()

                # 3. Next tick within cooldown (e.g. 5 seconds later) does NOT call renew again
                mock_renew.reset_mock()
                daemon.run_loop_step()
                assert mock_renew.call_count == 0

                # 4. After 10 minutes (601 seconds), cooldown expires and renewal can run again
                daemon._claude_renew_at -= (CLAUDE_RENEW_COOLDOWN_SECONDS + 1)
                daemon.run_loop_step()
                assert mock_renew.call_count == 1
