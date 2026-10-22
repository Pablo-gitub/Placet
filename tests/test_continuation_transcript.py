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


def test_a_transcript_past_the_first_prompt_is_not_read(tmp_path, monkeypatch):
    """The backend uses `continues` only on the first prompt: later prompts must not pay for a read."""
    from hooks import claude_code
    long_session = write_transcript(tmp_path / f"{NEW}.jsonl", [user(SUMMARY)] + [user("avanti")] * 12)
    assert continued_from_transcript(long_session) is None

    big = tmp_path / "big.jsonl"
    big.write_text(json.dumps(user(SUMMARY)) + "\n" + "x" * (claude_code.TRANSCRIPT_MAX_BYTES + 1), encoding="utf-8")
    opened = []
    monkeypatch.setattr(claude_code, "open", lambda *a, **k: opened.append(a) or (_ for _ in ()).throw(AssertionError),
                        raising=False)
    assert continued_from_transcript(str(big)) is None
    assert opened == []  # decided from the file size, without opening it


def test_a_short_continued_transcript_is_still_read(tmp_path):
    path = write_transcript(tmp_path / f"{NEW}.jsonl", [{"type": "summary"}, user(SUMMARY), user("continua")])
    assert continued_from_transcript(path) == OLD


def test_nothing_found_leaves_the_key_out(tmp_path):
    plain = write_transcript(tmp_path / f"{NEW}.jsonl", [user("ciao")])
    event = {"hook_event_name": "UserPromptSubmit", "cwd": CWD, "prompt": "ciao", "transcript_path": plain}
    assert "continues" not in build_event(event)
    assert "continues" not in build_event({**event, "transcript_path": str(tmp_path / "missing.jsonl")})
    assert "continues" not in build_event({k: v for k, v in event.items() if k != "transcript_path"})
