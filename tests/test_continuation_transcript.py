"""
RF-29d fallback: when the continuation summary does not reach the
UserPromptSubmit text, the hook reads the head of the session's own
transcript. Only an explicit reference to the previous transcript counts;
never a title or folder resemblance.
"""
import json

import pytest

from hooks.claude_code import build_event, continued_from_transcript
from tests.test_continuation_node import CWD, NEW, OLD, SUMMARY, old_session_with_node, post, relations_of, env  # noqa: F401


def write_transcript(path, lines):
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return str(path)


def user(text):
    return {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def test_the_previous_session_is_read_from_the_head_of_the_transcript(tmp_path):
    path = write_transcript(tmp_path / f"{NEW}.jsonl", [{"type": "summary"}, user(SUMMARY), user("poi lavoriamo")])
    assert continued_from_transcript(path) == OLD


def test_only_the_first_lines_count(tmp_path):
    lines = [user("ciao")] * 6 + [user(SUMMARY)]
    assert continued_from_transcript(write_transcript(tmp_path / f"{NEW}.jsonl", lines)) is None


def test_an_ordinary_transcript_or_a_missing_file_says_nothing(tmp_path):
    assert continued_from_transcript(write_transcript(tmp_path / f"{NEW}.jsonl", [user("ciao")])) is None
    assert continued_from_transcript(str(tmp_path / "missing.jsonl")) is None
    assert continued_from_transcript(None) is None


def test_a_reference_to_its_own_transcript_is_not_a_continuation(tmp_path):
    own = SUMMARY.replace(OLD, NEW)
    assert continued_from_transcript(write_transcript(tmp_path / f"{NEW}.jsonl", [user(own)])) is None


def test_the_prompt_text_wins_and_other_providers_are_left_alone(tmp_path):
    path = write_transcript(tmp_path / f"{NEW}.jsonl", [user(SUMMARY.replace(OLD, NEW[:-1] + "0"))])
    from_text = build_event({"hook_event_name": "UserPromptSubmit", "cwd": CWD, "prompt": SUMMARY,
                             "transcript_path": path})
    assert from_text["continues"] == OLD
    plain = build_event({"hook_event_name": "UserPromptSubmit", "cwd": CWD, "prompt": "ciao",
                         "transcript_path": path})
    assert plain["continues"] == NEW[:-1] + "0"
    codex = build_event({"hook_event_name": "UserPromptSubmit", "cwd": CWD, "prompt": "ciao",
                         "transcript_path": path}, provider="codex")
    assert "continues" not in codex


@pytest.mark.asyncio
async def test_the_node_follows_even_when_the_prompt_text_lacks_the_summary(env, tmp_path):  # noqa: F811
    app, db = env
    planner, _, _ = await old_session_with_node(app, db)
    path = write_transcript(tmp_path / f"{NEW}.jsonl", [user(SUMMARY), user("continua")])
    await post(app, "tok-claude", NEW, build_event({"hook_event_name": "SessionStart", "cwd": CWD, "source": "startup"}))
    payload = build_event({"hook_event_name": "UserPromptSubmit", "cwd": CWD, "prompt": "continua",
                           "transcript_path": path})
    assert (await post(app, "tok-claude", NEW, payload))["continued_from"] == OLD
    assert (await relations_of(app, NEW))["node"] == "planner"
    assert db.get_node(planner.id).session_id == NEW


def test_a_short_continued_transcript_is_still_read(tmp_path):
    path = write_transcript(tmp_path / f"{NEW}.jsonl", [{"type": "summary"}, user(SUMMARY), user("continua")])
    assert continued_from_transcript(path) == OLD


def test_nothing_found_leaves_the_key_out(tmp_path):
    plain = write_transcript(tmp_path / f"{NEW}.jsonl", [user("ciao")])
    event = {"hook_event_name": "UserPromptSubmit", "cwd": CWD, "prompt": "ciao", "transcript_path": plain}
    assert "continues" not in build_event(event)
    assert "continues" not in build_event({**event, "transcript_path": str(tmp_path / "missing.jsonl")})
    assert "continues" not in build_event({k: v for k, v in event.items() if k != "transcript_path"})


# --- only the structure Claude Code writes for a continuation counts --------------------------


def assistant(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def tool_result(text):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": text}]}}


def head(tmp_path, *records):
    return write_transcript(tmp_path / f"{NEW}.jsonl", list(records))


def test_the_observed_shapes_are_accepted(tmp_path):
    as_string = {"type": "user", "isCompactSummary": True, "message": {"role": "user", "content": SUMMARY}}
    assert continued_from_transcript(head(tmp_path, as_string)) == OLD
    assert continued_from_transcript(head(tmp_path, {"type": "system", "subtype": "compact_boundary"}, user(SUMMARY))) == OLD
    flagged = {**user(SUMMARY), "isCompactSummary": True}
    assert continued_from_transcript(head(tmp_path, {"type": "summary"}, flagged, user("continua"))) == OLD


@pytest.mark.parametrize("record", [
    assistant(SUMMARY),                       # an assistant message quoting the summary
    tool_result(SUMMARY),                     # a tool result (a file or command output containing it)
    {"type": "system", "content": SUMMARY},   # metadata
    {"type": "summary", "summary": SUMMARY},
    {**user(SUMMARY), "isSidechain": True},   # a subagent's own conversation
    user("Guarda questo: " + SUMMARY),        # the phrase in the middle of a user message
    {"type": "user", "message": {"role": "assistant", "content": SUMMARY}},
])
def test_other_records_quoting_the_summary_are_not_proof(tmp_path, record):
    assert continued_from_transcript(head(tmp_path, record)) is None


def test_a_summary_that_is_not_the_first_user_message_is_not_a_continuation(tmp_path):
    assert continued_from_transcript(head(tmp_path, user("ciao"), assistant("ok"), user(SUMMARY))) is None


def test_several_distinct_references_are_ambiguous_and_one_repeated_is_not(tmp_path):
    other = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    two = SUMMARY + f"See also /home/paolo/.claude/projects/x/{other}.jsonl\n"
    assert continued_from_transcript(head(tmp_path, user(two))) is None
    twice = SUMMARY + f"Again: /home/paolo/.claude/projects/x/{OLD}.jsonl\n"
    assert continued_from_transcript(head(tmp_path, user(twice))) == OLD


def test_a_malformed_line_is_skipped_not_trusted(tmp_path):
    path = tmp_path / f"{NEW}.jsonl"
    flat = SUMMARY.replace("\n", " ")
    path.write_text("not json " + flat + "\n" + json.dumps(user(SUMMARY)) + "\n", encoding="utf-8")
    assert continued_from_transcript(str(path)) == OLD
    path.write_text("not json " + flat + "\n", encoding="utf-8")
    assert continued_from_transcript(str(path)) is None


# --- realistic transcripts: the summary comes after a pile of service records -----------------


def service_records(n=10, snapshot_bytes=300_000):
    """What Claude Code writes before the first conversation record of a continued session."""
    records = [
        {"type": "custom-title", "customTitle": "Sviluppatore", "sessionId": NEW},
        {"type": "agent-name", "agentName": "dev", "sessionId": NEW},
        {"type": "mode", "mode": "normal", "sessionId": NEW},
        {"type": "atis-latch", "sessionId": NEW},
        {"type": "system", "subtype": "compact_boundary", "content": "Conversation compacted", "sessionId": NEW},
        {"type": "file-history-snapshot", "messageId": "m1", "isSnapshotUpdate": False,
         "snapshot": {"trackedFileBackups": {"big.txt": "x" * snapshot_bytes}}},
    ]
    records += [{"type": "mode", "mode": "normal", "n": i} for i in range(max(0, n - len(records)))]
    return records


def real_summary(text=SUMMARY):
    return {"type": "user", "isCompactSummary": True, "isVisibleInTranscriptOnly": True, "isSidechain": False,
            "message": {"role": "user", "content": text}}


def test_the_summary_after_many_service_records_and_a_huge_snapshot_is_found(tmp_path):
    path = head(tmp_path, *service_records(12), real_summary(), assistant("ok"))
    assert (tmp_path / f"{NEW}.jsonl").stat().st_size > 256 * 1024
    assert continued_from_transcript(path) == OLD


def test_a_long_continued_session_is_still_recognised_from_its_head(tmp_path):
    """The backend decides whether it is the first prompt; the hook only reads the head."""
    path = head(tmp_path, *service_records(12), real_summary(), *[user("avanti")] * 40)
    assert continued_from_transcript(path) == OLD


def test_the_first_conversation_record_decides_after_the_service_ones(tmp_path):
    ordinary = head(tmp_path, *service_records(12), user("ciao"), real_summary())
    assert continued_from_transcript(ordinary) is None
    first_assistant = head(tmp_path, *service_records(12), assistant("ok"), real_summary())
    assert continued_from_transcript(first_assistant) is None
    sidechain_first = head(tmp_path, *service_records(12), {**user("x"), "isSidechain": True}, real_summary())
    assert continued_from_transcript(sidechain_first) == OLD   # a subagent's record is not the conversation


def test_an_explicitly_unflagged_user_record_is_not_a_summary(tmp_path):
    assert continued_from_transcript(head(tmp_path, {**real_summary(), "isCompactSummary": False})) is None


def test_the_search_is_bounded(tmp_path, monkeypatch):
    from hooks import claude_code
    many = head(tmp_path, *[{"type": "mode", "n": i} for i in range(claude_code.TRANSCRIPT_MAX_RECORDS + 5)],
                real_summary())
    assert continued_from_transcript(many) is None                      # too many service records
    monkeypatch.setattr(claude_code, "TRANSCRIPT_MAX_BYTES", 1000)
    heavy = head(tmp_path, {"type": "file-history-snapshot", "snapshot": "x" * 5000}, real_summary())
    assert continued_from_transcript(heavy) is None                     # the byte budget ran out first


def test_the_hook_sends_continues_for_a_realistic_transcript(tmp_path):
    path = head(tmp_path, *service_records(12), real_summary(), user("continua"))
    event = {"hook_event_name": "UserPromptSubmit", "cwd": CWD, "prompt": "continua", "transcript_path": path}
    assert build_event(event)["continues"] == OLD
