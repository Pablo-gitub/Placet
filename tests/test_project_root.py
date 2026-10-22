"""
The hooks report the project root, not the current directory: working inside
a git worktree must not move the session to another project folder.
"""
import subprocess

import pytest

from hooks import placet_common
from hooks.antigravity import build_event as agy_event
from hooks.claude_code import build_event


def git(*args, cwd):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True,
                   capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = (tmp_path / "placet").resolve()
    root.mkdir()
    git("init", "-q", "-b", "main", cwd=root)
    (root / "sub").mkdir()
    (root / "sub" / "f.txt").write_text("x")
    git("add", ".", cwd=root)
    git("commit", "-q", "-m", "init", cwd=root)
    git("worktree", "add", "-q", "-b", "feature", str(root / ".worktrees" / "rf29d"), cwd=root)
    return root


def test_a_linked_worktree_reports_the_main_repository(repo):
    assert placet_common.project_root(str(repo / ".worktrees" / "rf29d")) == str(repo)
    assert placet_common.project_root(str(repo / ".worktrees" / "rf29d" / "sub")) == str(repo)


def test_the_main_checkout_and_its_subfolders_are_left_as_they_are(repo):
    assert placet_common.project_root(str(repo)) == str(repo)
    assert placet_common.project_root(str(repo / "sub")) == str(repo / "sub")


def test_no_git_no_folder_or_a_slow_git_fall_back_to_the_raw_cwd(tmp_path, monkeypatch):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert placet_common.project_root(str(plain)) == str(plain)
    assert placet_common.project_root(str(tmp_path / "missing")) == str(tmp_path / "missing")
    assert placet_common.project_root(None) is None
    assert placet_common.project_root("") == ""

    def slow(*a, **k):
        raise subprocess.TimeoutExpired("git", 2)
    monkeypatch.setattr(placet_common.subprocess, "run", slow)
    assert placet_common.project_root(str(plain)) == str(plain)
    monkeypatch.setattr(placet_common.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    assert placet_common.project_root(str(plain)) == str(plain)


def test_the_hooks_send_the_project_root(repo):
    worktree = str(repo / ".worktrees" / "rf29d")
    claude = build_event({"hook_event_name": "UserPromptSubmit", "cwd": worktree, "prompt": "ciao"})
    assert claude["cwd"] == str(repo)
    codex = build_event({"hook_event_name": "SessionStart", "cwd": worktree}, provider="codex")
    assert codex["cwd"] == str(repo)
    agy = agy_event("SessionStart", {"workspacePaths": [worktree], "transcriptPath": ""})
    assert agy["cwd"] == str(repo)


def test_the_steer_hook_reports_the_project_root_too(repo, monkeypatch):
    import io
    import json
    from hooks import steer
    sent = []
    monkeypatch.setattr(steer, "take", lambda sid: {"text": "ferma", "request_id": 3})
    monkeypatch.setattr(steer.spool, "write_event", lambda agent, sid, body: sent.append(body))
    monkeypatch.setattr("sys.argv", ["steer.py", "--agent", "claude"])
    event = {"session_id": "s1", "cwd": str(repo / ".worktrees" / "rf29d"), "hook_event_name": "PostToolUse"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    steer.main()
    assert sent[0]["cwd"] == str(repo)
