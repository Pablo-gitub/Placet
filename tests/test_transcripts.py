"""Reading a session's conversation from the providers' files (RF-30, RF-32, RS-06)."""
import json
import os
from pathlib import Path

import pytest

from bridge import transcripts
from bridge.transcripts import TranscriptError, locate, read_page, read_transcript

CLAUDE_ID = "a2c3ef2a-6d99-4567-9909-9fc96489ebf7"
CODEX_ID = "01a1418c-516f-7213-a863-ef11a6b36c20"
AGY_ID = "e85472b3-f688-44ff-9b1a-688a74c5f8ac"


@pytest.fixture(autouse=True)
def _no_cache():
    transcripts._located.clear()


def write_jsonl(path: Path, rows) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def claude_user(text, **extra):
    return {"type": "user", "timestamp": "2026-10-15T20:00:00Z", "message": {"role": "user", "content": text}, **extra}


def claude_assistant(*blocks, **extra):
    return {"type": "assistant", "timestamp": "2026-10-15T20:00:01Z",
            "message": {"role": "assistant", "content": list(blocks)}, **extra}


CLAUDE_ROWS = [
    {"type": "attachment", "attachment": {"type": "date"}},
    claude_user("ciao"),
    claude_assistant({"type": "thinking", "thinking": "segreto"},
                     {"type": "text", "text": "Ciao! Come posso aiutarti?"}),
    claude_assistant({"type": "tool_use", "name": "Bash", "input": {"command": "git status\n--short"}}),
    {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "x", "content": "output privato"}]}},
    claude_assistant({"type": "tool_use", "name": "mcp__placet__placet_reply", "input": {"request_id": 7}}),
    claude_user("<task-notification>\n<summary>Stop hook feedback</summary>\n</task-notification>\n"
                "<system-reminder>\nStop hook blocking error from command \"Stop\": "
                "[Placet Request #7 da claude]: Scrivi hola\n</system-reminder>"),
    claude_user("<command-name>/compact</command-name>\n<command-message>compact</command-message>\n"
                "<command-args></command-args>"),
    claude_user("<local-command-stdout>Compacted</local-command-stdout>"),
    {"type": "system", "subtype": "compact_boundary", "timestamp": "2026-10-15T20:05:00Z"},
    claude_user("This session is being continued...", isCompactSummary=True),
    claude_user("istruzioni interne", isMeta=True),
    claude_assistant({"type": "text", "text": "risposta di un subagente"}, isSidechain=True),
    {"type": "system", "subtype": "stop_hook_summary"},
    claude_user([{"type": "text", "text": "guarda questa"}, {"type": "image", "source": {}}]),
    claude_assistant({"type": "text", "text": "Fatto."}),
]


def claude_home(tmp_path, rows=CLAUDE_ROWS) -> Path:
    write_jsonl(tmp_path / ".claude" / "projects" / "-home-paolo-lab" / f"{CLAUDE_ID}.jsonl", rows)
    return tmp_path


def summary(page):
    return [(m["role"], m["kind"] if m["role"] == "action" else m["text"]) for m in page["messages"]]


def test_claude_shows_messages_and_actions_only(tmp_path):
    page = read_transcript("claude-code", CLAUDE_ID, home=claude_home(tmp_path))
    assert summary(page) == [
        ("user", "ciao"),
        ("assistant", "Ciao! Come posso aiutarti?"),
        ("action", "command"),
        ("action", "mcp"),
        ("placet", "[Placet Request #7 da claude]: Scrivi hola"),
        ("command", "/compact"),
        ("compact", ""),
        ("user", "guarda questa\n[immagine]"),
        ("assistant", "Fatto."),
    ]
    text = json.dumps(page)
    for private in ("segreto", "output privato", "istruzioni interne", "subagente", "continued"):
        assert private not in text
    bash = page["messages"][2]
    assert bash["tool"] == "Bash" and bash["detail"] == "git status --short"
    assert page["messages"][3]["tool"] == "placet_reply" and page["messages"][3]["detail"] == "placet"
    assert page["before"] is None and page["provider"] == "claude-code"


def test_paging_backwards_and_forwards(tmp_path):
    rows = [claude_user(f"messaggio {i}") for i in range(10)]
    home = claude_home(tmp_path, rows)
    last = read_transcript("claude-code", CLAUDE_ID, limit=4, home=home)
    assert [m["text"] for m in last["messages"]] == [f"messaggio {i}" for i in range(6, 10)]
    older = read_transcript("claude-code", CLAUDE_ID, before=last["before"], limit=4, home=home)
    assert [m["text"] for m in older["messages"]] == [f"messaggio {i}" for i in range(2, 6)]
    first = read_transcript("claude-code", CLAUDE_ID, before=older["before"], limit=4, home=home)
    assert [m["text"] for m in first["messages"]] == ["messaggio 0", "messaggio 1"]
    assert first["before"] is None

    # Nothing new, then two new lines and a third still being written.
    assert read_transcript("claude-code", CLAUDE_ID, after=last["end"], home=home)["messages"] == []
    path = locate("claude-code", CLAUDE_ID, home)
    with open(path, "a") as f:
        f.write(json.dumps(claude_user("nuovo 1")) + "\n" + json.dumps(claude_user("nuovo 2")) + "\n")
        f.write('{"type": "user", "message": {"content": "a metà')
    new = read_transcript("claude-code", CLAUDE_ID, after=last["end"], home=home)
    assert [m["text"] for m in new["messages"]] == ["nuovo 1", "nuovo 2"]
    assert read_transcript("claude-code", CLAUDE_ID, after=new["end"], home=home)["messages"] == []
    # The latest page ignores the incomplete line too.
    assert read_transcript("claude-code", CLAUDE_ID, limit=1, home=home)["messages"][0]["text"] == "nuovo 2"


def test_cursors_are_unique_and_ordered(tmp_path):
    page = read_transcript("claude-code", CLAUDE_ID, home=claude_home(tmp_path))
    cursors = [m["cursor"] for m in page["messages"]]
    assert len(set(cursors)) == len(cursors)
    keys = [tuple(int(x) for x in c.split(":")) for c in cursors]
    assert keys == sorted(keys)


def test_lines_longer_than_a_block_and_garbage(tmp_path, monkeypatch):
    monkeypatch.setattr(transcripts, "BLOCK_BYTES", 64)
    rows = [claude_user("x" * 500), claude_user("breve")]
    home = claude_home(tmp_path, rows)
    path = locate("claude-code", CLAUDE_ID, home)
    with open(path, "a") as f:
        f.write("not json\n[1, 2]\n")
    page = read_transcript("claude-code", CLAUDE_ID, home=home)
    assert [len(m["text"]) for m in page["messages"]] == [500, 5]


CODEX_ROWS = [
    {"type": "session_meta", "payload": {"id": CODEX_ID}},
    {"timestamp": "t1", "type": "response_item",
     "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "<environment_context>"}]}},
    {"timestamp": "t2", "type": "event_msg", "payload": {"type": "item_completed", "item": {
        "type": "UserMessage", "content": [{"type": "text", "text": "ciao\n"}]}}},
    {"timestamp": "t3", "type": "event_msg", "payload": {"type": "item_completed", "item": {
        "type": "Reasoning", "summary_text": ["pensiero"]}}},
    {"timestamp": "t4", "type": "event_msg", "payload": {"type": "item_completed", "item": {
        "type": "CommandExecution", "command": ["/bin/bash", "-lc", "rg -n placet ."]}}},
    {"timestamp": "t5", "type": "response_item", "payload": {"type": "function_call_output", "output": "segreto"}},
    {"timestamp": "t6", "type": "event_msg", "payload": {"type": "item_completed", "item": {
        "type": "McpToolCall", "server": "placet", "tool": "placet_ask_human", "arguments": {}}}},
    {"timestamp": "t7", "type": "compacted", "payload": {"message": ""}},
    {"timestamp": "t8", "type": "event_msg", "payload": {"type": "item_completed", "item": {
        "type": "UserMessage", "content": [{"type": "text", "text": "[Placet Request #16 da claude]: crea il file"}]}}},
    {"timestamp": "t9", "type": "event_msg", "payload": {"type": "item_completed", "item": {
        "type": "AgentMessage", "content": [{"type": "Text", "text": "FATTO"}], "phase": "final_answer"}}},
    {"timestamp": "t10", "type": "event_msg", "payload": {"type": "task_complete", "last_agent_message": "FATTO"}},
]


def test_codex(tmp_path):
    write_jsonl(tmp_path / ".codex" / "sessions" / "2026" / "10" / "15" /
                f"rollout-2026-10-15T23-51-17-{CODEX_ID}.jsonl", CODEX_ROWS)
    page = read_transcript("codex", CODEX_ID, home=tmp_path)
    assert summary(page) == [
        ("user", "ciao"),
        ("action", "command"),
        ("action", "mcp"),
        ("compact", ""),
        ("placet", "[Placet Request #16 da claude]: crea il file"),
        ("assistant", "FATTO"),
    ]
    assert page["messages"][1]["detail"] == "rg -n placet ."
    assert "segreto" not in json.dumps(page) and "pensiero" not in json.dumps(page)


def test_codex_matches_the_whole_id(tmp_path):
    day = tmp_path / ".codex" / "sessions" / "2026" / "10" / "15"
    write_jsonl(day / f"rollout-2026-10-15T23-51-17-{CODEX_ID}0.jsonl", CODEX_ROWS)
    with pytest.raises(TranscriptError) as e:
        locate("codex", CODEX_ID, tmp_path)
    assert e.value.code == "not_found"
    # A fork: rollout-<date>-<parent>_<id>.jsonl
    write_jsonl(day / f"rollout-2026-10-15T23-51-17-01a07509-7d21-7522-bf2b-7631d5e09f84_{CODEX_ID}.jsonl", CODEX_ROWS)
    assert locate("codex", CODEX_ID, tmp_path).name.endswith(f"_{CODEX_ID}.jsonl")


AGY_ROWS = [
    {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "created_at": "t0",
     "content": "<USER_REQUEST>\nciao\n</USER_REQUEST>\n<ADDITIONAL_METADATA>ora locale</ADDITIONAL_METADATA>"},
    {"step_index": 1, "source": "SYSTEM", "type": "SYSTEM_MESSAGE", "content": "istruzioni di sistema"},
    {"step_index": 2, "source": "SYSTEM", "type": "CHECKPOINT", "content": "# Resuming from a compaction\n..."},
    {"step_index": 3, "source": "MODEL", "type": "PLANNER_RESPONSE", "created_at": "t3", "content": None,
     "thinking": "ragionamento",
     "tool_calls": [{"name": "run_command", "args": {"CommandLine": "\"gcloud --version\"",
                                                     "toolSummary": "\"Check gcloud\""}},
                    {"name": "schedule", "args": {"toolSummary": "\"Wait for tests\""}}]},
    {"step_index": 4, "source": "MODEL", "type": "GENERIC", "content": "The command exited with code 127. segreto"},
    {"step_index": 5, "source": "MODEL", "type": "PLANNER_RESPONSE", "created_at": "t5", "content": "MELONE-GEMINI"},
    {"step_index": 6, "source": "SYSTEM", "type": "ERROR_MESSAGE", "content": "Error: The stream was interrupted."},
]


def test_antigravity(tmp_path):
    write_jsonl(tmp_path / ".gemini" / "antigravity" / "brain" / AGY_ID / ".system_generated" / "logs" /
                "transcript.jsonl", AGY_ROWS)
    page = read_transcript("antigravity", AGY_ID, home=tmp_path)
    assert summary(page) == [
        ("user", "ciao"),
        ("compact", ""),
        ("action", "command"),
        ("action", "other"),
        ("assistant", "MELONE-GEMINI"),
        ("notice", "Error: The stream was interrupted."),
    ]
    assert page["messages"][2]["detail"] == "gcloud --version"
    assert page["messages"][3]["detail"] == "Wait for tests"
    text = json.dumps(page)
    assert "segreto" not in text and "ragionamento" not in text and "istruzioni di sistema" not in text


def test_provider_from_agent_when_missing(tmp_path):
    page = read_transcript(None, CLAUDE_ID, agent_id="claude", home=claude_home(tmp_path))
    assert page["provider"] == "claude-code"
    with pytest.raises(TranscriptError) as e:
        read_transcript("ollama", CLAUDE_ID, agent_id="ollama", home=tmp_path)
    assert e.value.code == "unsupported"


@pytest.mark.parametrize("bad", ["../x", "a/b", "*", "", "a" * 200, "id with space"])
def test_session_ids_cannot_escape(tmp_path, bad):
    with pytest.raises(TranscriptError) as e:
        locate("claude-code", bad, claude_home(tmp_path))
    assert e.value.code == "invalid"


def test_symlink_outside_the_provider_folder_is_refused(tmp_path):
    secret = tmp_path / "secret.jsonl"
    write_jsonl(secret, [claude_user("password")])
    link = tmp_path / ".claude" / "projects" / "-x" / f"{CLAUDE_ID}.jsonl"
    link.parent.mkdir(parents=True)
    os.symlink(secret, link)
    with pytest.raises(TranscriptError) as e:
        locate("claude-code", CLAUDE_ID, tmp_path)
    assert e.value.code == "not_found"


def test_missing_file(tmp_path):
    with pytest.raises(TranscriptError) as e:
        read_transcript("claude-code", CLAUDE_ID, home=tmp_path)
    assert e.value.code == "not_found"


def test_empty_file(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text("")
    assert read_page(path, transcripts.parse_claude) == {"messages": [], "before": None, "end": 0}


def test_the_users_own_messages_from_the_phone(tmp_path):
    """Messages written in the PWA (RF-34) show as the user's, in every provider."""
    note = "\nÈ un messaggio che l'utente ti scrive dal telefono tramite Placet: rispondigli direttamente."
    wake = ("<task-notification>\n</task-notification>\n<system-reminder>\nStop hook blocking error from "
            f"command \"Stop\": [Placet Request #9 dall'utente]: a che punto sei?{note}\nQuesto non è un errore"
            "\n</system-reminder>")
    home = claude_home(tmp_path, [claude_user(wake)])
    entry = read_transcript("claude-code", CLAUDE_ID, home=home)["messages"][0]
    assert (entry["role"], entry["text"], entry["via"]) == ("user", "a che punto sei?", "placet")

    write_jsonl(tmp_path / ".gemini" / "antigravity" / "brain" / AGY_ID / ".system_generated" / "logs" /
                "transcript.jsonl", [
        {"type": "USER_INPUT", "content": f"<USER_REQUEST>\n[Placet Request #10 dall'utente]: riga 1\nriga 2{note}\n"
                                          "</USER_REQUEST>"},
        {"type": "PLANNER_RESPONSE", "content": None, "tool_calls": [{"name": "call_mcp_tool", "args": {
            "ServerName": "\"placet\"", "ToolName": "\"placet_reply\"", "Arguments": "{}"}}]},
    ])
    page = read_transcript("antigravity", AGY_ID, home=tmp_path)
    assert page["messages"][0]["role"] == "user" and page["messages"][0]["text"] == "riga 1\nriga 2"
    mcp = page["messages"][1]
    assert (mcp["kind"], mcp["tool"], mcp["detail"]) == ("mcp", "placet_reply", "placet")
