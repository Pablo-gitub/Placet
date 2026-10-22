"""
The cause of a Claude Desktop wake-up that never started, read from the app's own log: an
undocumented, isolated adapter. A diagnosis needs the full sequence for THIS session after the
attempt; anything else (another session, a hint without the refusal, a success, an unreadable or
unexpected log) says nothing.
"""
import os
import time
from datetime import datetime

import pytest

from bridge.claude_desktop_log import ClaudeDesktopLog

X = "local_11111111-1111-4111-8111-111111111111"
Y = "local_22222222-2222-4222-8222-222222222222"
ATTEMPT = 1_790_000_000.0          # an instant in October 2026


def line(offset, text, level="info"):
    stamp = datetime.fromtimestamp(ATTEMPT + offset).strftime("%Y-%m-%d %H:%M:%S")
    return f"{stamp} [{level}] {text}"


def warming(offset, who):
    return line(offset, f"[CCD] Warming session {who}")


def yielded(offset):
    return line(offset, "[CliGovernor] at cap; yielding warm spawn")


def would_evict(offset, victim=Y):
    return line(offset, f"[CliGovernor] at cap=7; would evict {victim} (idle 3600s) for warm spawn")


def warmed(offset, who):
    return line(offset, f"[CCD] Session {who} warmed successfully in 812ms")


def log_of(tmp_path, lines, name="main.log"):
    path = tmp_path / "logs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def diagnose(path, who=X, since=ATTEMPT):
    return ClaudeDesktopLog(path=path).diagnose(who, since)


def test_the_refused_warm_spawn_of_this_session_is_named(tmp_path):
    path = log_of(tmp_path, [line(-30, "[info] boot"), warming(2, X), would_evict(3), yielded(3)])
    found = diagnose(path)
    assert found.code == "warm_capacity_denied"
    assert abs(found.observed_at.timestamp() - (ATTEMPT + 3)) < 1.5


@pytest.mark.parametrize("lines", [
    [warming(2, Y), yielded(3)],                                          # another session's refusal
    [warming(2, X), would_evict(3)],                                      # a hint alone is not a refusal
    [would_evict(3), yielded(3)],                                         # a refusal for nobody we know
    [warming(2, X), yielded(3), warmed(9, X)],                            # started after all
    [warming(2, X), warming(2, Y), yielded(3)],                           # the refusal belongs to the latest warm spawn
    [warming(2, X), would_evict(3), yielded(3), warmed(9, X)],
    [warming(-60, X), yielded(-59)],                                      # before the attempt
    [warmed(2, X)],
    [],
    ["garbage", "\x00\x01 not a log line", line(2, "something else")],
])
def test_anything_else_says_nothing(tmp_path, lines):
    assert diagnose(log_of(tmp_path, lines)) is None


def test_a_later_warm_spawn_of_the_same_session_can_be_refused_again(tmp_path):
    lines = [warming(2, X), yielded(3), warmed(8, X), warming(20, X), yielded(21)]
    found = diagnose(log_of(tmp_path, lines))
    assert found is not None and abs(found.observed_at.timestamp() - (ATTEMPT + 21)) < 1.5


def test_an_unknown_format_is_no_diagnosis(tmp_path):
    other = [f"[{ATTEMPT + 2}] WARM {X} refused by governor", f"{X}: at cap; yielding"]
    assert diagnose(log_of(tmp_path, other)) is None


# --- the file: never trusted, never raising --------------------------------------------------


def test_a_missing_symlinked_or_non_regular_log_says_nothing(tmp_path):
    assert diagnose(tmp_path / "logs" / "main.log") is None
    real = log_of(tmp_path, [warming(2, X), yielded(3)], name="real.log")
    link = tmp_path / "logs" / "main.log"
    link.symlink_to(real)
    assert diagnose(link) is None
    directory = tmp_path / "logs" / "dir.log"
    directory.mkdir()
    assert diagnose(directory) is None


def test_a_log_of_another_user_says_nothing(tmp_path, monkeypatch):
    path = log_of(tmp_path, [warming(2, X), yielded(3)])
    assert diagnose(path) is not None
    monkeypatch.setattr("bridge.claude_desktop_log.os.getuid", lambda: os.getuid() + 1)
    assert diagnose(path) is None


def test_an_unreadable_or_binary_log_says_nothing(tmp_path):
    path = log_of(tmp_path, [warming(2, X), yielded(3)])
    path.chmod(0)
    try:
        if os.access(path, os.R_OK):
            pytest.skip("running as a user that ignores permissions")
        assert diagnose(path) is None
    finally:
        path.chmod(0o600)
    path.write_bytes(os.urandom(4096))
    assert diagnose(path) is None


def test_only_the_tail_is_read_and_a_cut_first_line_is_dropped(tmp_path):
    filler = [line(-500 + i, "[CCD] noise " + "x" * 200) for i in range(2000)]      # ~460 KB
    tail = [warming(2, X), yielded(3)]
    path = log_of(tmp_path, filler + tail)
    assert path.stat().st_size > 256 * 1024
    assert diagnose(path) is not None                                    # the evidence is in the tail
    early = log_of(tmp_path, [warming(2, X), yielded(3)] + filler)
    assert diagnose(early) is None                                       # beyond the bounded read
    # a line cut in half by the bound must not be mistaken for a record
    cut = log_of(tmp_path, ["x" * 300_000, yielded(3)])
    assert diagnose(cut) is None


def test_a_truncated_or_rotated_log_is_just_a_smaller_log(tmp_path):
    path = log_of(tmp_path, [warming(2, X), yielded(3)])
    assert diagnose(path) is not None
    path.write_text("")                                                  # truncated
    assert diagnose(path) is None
    path.write_text(warming(40, X) + "\n" + yielded(41) + "\n")        # rotated: a fresh file
    assert diagnose(path, since=ATTEMPT + 30) is not None
    assert diagnose(path, since=ATTEMPT + 50) is None


def test_the_default_path_is_the_apps_main_log(tmp_path):
    adapter = ClaudeDesktopLog(home=tmp_path)
    assert adapter.path == tmp_path / ".config" / "Claude" / "logs" / "main.log"
