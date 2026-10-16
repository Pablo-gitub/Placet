"""Hooks for Codex and Antigravity, and their installation (RF-42)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from hooks import install
from hooks.antigravity import build_event as agy_event
from hooks.claude_code import build_event

HOOKS = Path(__file__).resolve().parent.parent / "hooks"


# --- Codex: same fields as Claude Code ------------------------------------------------


def test_codex_events_use_the_claude_hook():
    start = build_event({"hook_event_name": "SessionStart", "cwd": "/p"}, provider="codex")
    assert start["provider"] == "codex" and start["entrypoint"] == "codex"
    prompt = build_event({"hook_event_name": "UserPromptSubmit",
                          "prompt": "[Placet Request #4 dall'utente]: ciao"}, provider="codex")
    assert prompt["request_id"] == 4
    stop = build_event({"hook_event_name": "Stop", "last_assistant_message": "FATTO"}, provider="codex")
    assert stop["last_message"] == "FATTO"
    # Placet tools, whatever prefix the client gives them.
    for name in ("mcp__placet__placet_ask_human", "placet.placet_send_to_agent", "placet_ask_human"):
        tool = build_event({"hook_event_name": "PostToolUse", "tool_name": name,
                            "tool_response": json.dumps({"id": 8, "sender_id": "codex"})}, provider="codex")
        assert tool["request_id"] == 8
    assert build_event({"hook_event_name": "PostToolUse", "tool_name": "my_placet_ask_humans"}) is None


# --- Antigravity ------------------------------------------------------------------------


def transcript(tmp_path, rows) -> str:
    path = tmp_path / "transcript_full.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return str(path)


def payload(path, **extra):
    return {"conversationId": "conv-1", "workspacePaths": ["/home/p/lab"], "transcriptPath": path,
            "modelName": "gemini-x", **extra}


def test_antigravity_turn_lifecycle(tmp_path):
    path = transcript(tmp_path, [
        {"type": "USER_INPUT", "content": "<USER_REQUEST>\nvecchia\n</USER_REQUEST>"},
        {"type": "PLANNER_RESPONSE", "content": "risposta vecchia"},
        {"type": "USER_INPUT", "content": "<USER_REQUEST>\n[Placet Request #12 da claude]: rivedi\n</USER_REQUEST>"},
        {"type": "PLANNER_RESPONSE", "content": "Leggo il file."},
        {"type": "GENERIC", "content": "output"},
        {"type": "PLANNER_RESPONSE", "content": None, "tool_calls": [{"name": "view_file"}]},
        {"type": "PLANNER_RESPONSE", "content": "Tutto a posto."},
    ])
    assert agy_event("SessionStart", payload(path)) == {
        "event": "start", "provider": "antigravity", "entrypoint": "antigravity", "cwd": "/home/p/lab"}
    first = agy_event("PreInvocation", payload(path, invocationNum=0))
    assert first["event"] == "prompt" and first["request_id"] == 12
    assert agy_event("PreInvocation", payload(path, invocationNum="1")) is None
    stop = agy_event("Stop", payload(path, terminationReason="NO_TOOL_CALL"))
    assert stop["event"] == "stop" and stop["last_message"] == "Tutto a posto."
    assert agy_event("PostToolUse", payload(path)) is None


def test_antigravity_turn_without_answer_or_request(tmp_path):
    path = transcript(tmp_path, [
        {"type": "PLANNER_RESPONSE", "content": "di un turno precedente"},
        {"type": "USER_INPUT", "content": "<USER_REQUEST>\nciao\n</USER_REQUEST>"},
    ])
    assert "request_id" not in agy_event("PreInvocation", payload(path, invocationNum=0))
    assert "last_message" not in agy_event("Stop", payload(path))
    assert "last_message" not in agy_event("Stop", payload(str(tmp_path / "missing.jsonl")))


def test_antigravity_hook_always_answers_json_and_exits_0(tmp_path):
    env = {"PATH": "/usr/bin", "HOME": str(tmp_path), "PLACET_API_TOKEN": "t",
           "PLACET_API_URL": "http://127.0.0.1:9"}
    for stdin in ["not json", json.dumps(payload(str(tmp_path / "t.jsonl")))]:
        res = subprocess.run([sys.executable, str(HOOKS / "antigravity.py"), "--agent", "gemini",
                              "--event", "Stop"], input=stdin, capture_output=True, text=True, env=env,
                             timeout=10)
        assert res.returncode == 0 and res.stdout.strip() == "{}"


# --- Installer ------------------------------------------------------------------------------

OTHER_CLAUDE = {
    "model": "opus",
    "hooks": {
        "Stop": [{"hooks": [{"type": "command", "command": "notify-send done"}]}],
        "PostToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "lint.sh"}]}],
    },
}


def run_install(home, *args):
    return [install.apply(p, "/opt/placet/hooks", "--uninstall" in args, "--dry-run" in args, home=home)
            for p in ("claude", "codex", "antigravity")]


def read(home, rel):
    return json.loads((home / rel).read_text())


def test_install_merges_and_is_idempotent(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps(OTHER_CLAUDE))
    (tmp_path / ".gemini" / "config").mkdir(parents=True)
    (tmp_path / ".gemini" / "config" / "hooks.json").write_text(json.dumps({
        "lint": {"PostToolUse": [{"matcher": "run_command", "hooks": [{"command": "./lint.sh"}]}]}}))
    run_install(tmp_path)

    claude = read(tmp_path, ".claude/settings.json")
    assert claude["model"] == "opus"
    stop = [h["command"] for g in claude["hooks"]["Stop"] for h in g["hooks"]]
    assert stop[0] == "notify-send done"
    assert "python3 /opt/placet/hooks/claude_code.py --agent claude" in stop
    waiter = [h for g in claude["hooks"]["SessionStart"] for h in g["hooks"] if "waiter" in h["command"]][0]
    assert waiter["async"] and waiter["asyncRewake"]
    assert claude["hooks"]["PostToolUse"][0]["matcher"] == "Bash"

    codex = read(tmp_path, ".codex/hooks.json")
    assert codex["hooks"]["Stop"][0]["hooks"][0]["command"].endswith("--agent codex --provider codex")
    assert not any(h.get("async") for g in codex["hooks"].values() for x in g for h in x["hooks"])

    agy = read(tmp_path, ".gemini/config/hooks.json")
    assert set(agy) == {"lint", "placet"}
    assert agy["placet"]["Stop"][0]["command"].endswith("antigravity.py --agent gemini --event Stop")

    # A second run changes nothing, and makes no backup.
    assert all("già a posto" in line for line in run_install(tmp_path))
    backups = list(tmp_path.rglob("*.bak-placet-*"))
    assert len(backups) == 2  # claude and antigravity existed before the first run


def test_uninstall_removes_only_placet(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps(OTHER_CLAUDE))
    run_install(tmp_path)
    run_install(tmp_path, "--uninstall")
    assert read(tmp_path, ".claude/settings.json") == OTHER_CLAUDE
    assert read(tmp_path, ".codex/hooks.json") == {}
    assert read(tmp_path, ".gemini/config/hooks.json") == {}


def test_a_broken_file_is_never_overwritten(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text("{ broken")
    with pytest.raises(ValueError):
        install.apply("claude", "/opt/placet/hooks", False, False, home=tmp_path)
    assert (tmp_path / ".claude" / "settings.json").read_text() == "{ broken"


def test_dry_run_writes_nothing(tmp_path):
    out = run_install(tmp_path, "--dry-run")
    assert all("verrebbe aggiornato" in line for line in out)
    assert not list(tmp_path.rglob("*.json"))


# --- Opt-in per project -----------------------------------------------------------------


def test_only_projects_that_opt_in_go_through_placet(tmp_path, monkeypatch):
    from hooks.placet_common import active
    monkeypatch.delenv("PLACET_HOOK_FORCE", raising=False)
    project = tmp_path / "proj"
    (project / "src" / "deep").mkdir(parents=True)
    assert not active(str(project)) and not active(None)
    (project / ".placet-on").touch()
    assert active(str(project)) and active(str(project / "src" / "deep"))
    (project / "src" / ".placet-off").touch()
    assert not active(str(project / "src" / "deep"))
    monkeypatch.setenv("PLACET_HOOK_FORCE", "1")
    assert active(str(tmp_path / "anything"))


def test_hook_stays_silent_outside_opted_in_projects(tmp_path):
    seen = tmp_path / "calls"
    env = {"PATH": "/usr/bin", "HOME": str(tmp_path), "PLACET_API_TOKEN": "t",
           "PLACET_API_URL": "http://127.0.0.1:9"}
    event = {"hook_event_name": "UserPromptSubmit", "session_id": "s", "cwd": str(tmp_path), "prompt": "x"}
    res = subprocess.run([sys.executable, str(HOOKS / "claude_code.py"), "--agent", "claude"],
                         input=json.dumps(event), capture_output=True, text=True, env=env, timeout=10)
    assert res.returncode == 0 and res.stdout == "" and not seen.exists()
