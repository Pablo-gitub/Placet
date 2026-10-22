"""
Hooks report the raw working directory (the desktop wake-up, transcripts,
uploads and .placet-off depend on it) AND a project root, used only to group
sessions in Home and to compare folders when routing a request.
"""
import os
import subprocess

import pytest

from hooks import placet_common
from hooks.antigravity import build_event as agy_event
from hooks.claude_code import build_event
from tests.helpers import agent_client, human_client


def git(*args, cwd):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True,
                   capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = (tmp_path / "placet").resolve()
    root.mkdir()
    git("init", "-q", "-b", "main", cwd=root)
    (root / "backend").mkdir()
    (root / "backend" / "f.txt").write_text("x")
    git("add", ".", cwd=root)
    git("commit", "-q", "-m", "init", cwd=root)
    git("worktree", "add", "-q", "-b", "feature", str(root / ".worktrees" / "rf29d"), cwd=root)
    git("worktree", "add", "-q", "-b", "desktop", str(root / ".claude" / "worktrees" / "x"), cwd=root)
    return root


def test_linked_worktrees_report_the_main_repository(repo):
    for inside in (repo / ".worktrees" / "rf29d", repo / ".claude" / "worktrees" / "x",
                   repo / ".worktrees" / "rf29d" / "backend"):
        assert placet_common.project_root(str(inside)) == str(repo)


def test_the_main_checkout_and_any_of_its_subfolders_report_its_root(repo):
    assert placet_common.project_root(str(repo)) == str(repo)
    assert placet_common.project_root(str(repo / "backend")) == str(repo)      # `cd backend` changes nothing


def test_symlinked_paths_are_normalized(repo, tmp_path):
    link = tmp_path / "link"
    link.symlink_to(repo)
    assert placet_common.project_root(str(link / "backend")) == str(repo)
    assert placet_common.project_root(str(link / ".worktrees" / "rf29d")) == str(repo)
    plain = tmp_path / "plain"
    plain.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(plain)
    assert placet_common.project_root(str(alias)) == str(plain)               # outside git: the real path


def test_git_environment_variables_are_ignored(repo, tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    assert placet_common.project_root(str(repo / "backend")) == str(repo)


def test_failures_fall_back_to_the_raw_directory(tmp_path, monkeypatch):
    missing = str(tmp_path / "missing")
    assert placet_common.project_root(missing) == missing
    assert placet_common.project_root(None) is None
    assert placet_common.project_root("") == ""

    def slow(*a, **k):
        raise subprocess.TimeoutExpired("git", 2)
    monkeypatch.setattr(placet_common.subprocess, "run", slow)
    assert placet_common.project_root(str(tmp_path)) == str(tmp_path)
    monkeypatch.setattr(placet_common.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    assert placet_common.project_root(str(tmp_path)) == str(tmp_path)


def test_the_hooks_keep_the_raw_cwd_and_add_the_project_root(repo):
    worktree = str(repo / ".claude" / "worktrees" / "x")
    claude = build_event({"hook_event_name": "UserPromptSubmit", "cwd": worktree, "prompt": "ciao"})
    assert (claude["cwd"], claude["project_root"]) == (worktree, str(repo))
    codex = build_event({"hook_event_name": "SessionStart", "cwd": worktree}, provider="codex")
    assert (codex["cwd"], codex["project_root"]) == (worktree, str(repo))
    from hooks.antigravity import with_project_root
    agy = with_project_root(agy_event("SessionStart", {"workspacePaths": [worktree], "transcriptPath": ""}))
    assert (agy["cwd"], agy["project_root"]) == (worktree, str(repo))
    assert "project_root" not in with_project_root({"event": "stop", "cwd": None})


def test_the_steer_hook_reports_both(repo, monkeypatch):
    import io
    import json
    from hooks import steer
    sent = []
    monkeypatch.setattr(steer, "take", lambda sid: {"text": "ferma", "request_id": 3})
    monkeypatch.setattr(steer.spool, "write_event", lambda agent, sid, body: sent.append(body))
    monkeypatch.setattr("sys.argv", ["steer.py", "--agent", "claude"])
    worktree = str(repo / ".worktrees" / "rf29d")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"session_id": "s1", "cwd": worktree, "hook_event_name": "PostToolUse"})))
    steer.main()
    assert (sent[0]["cwd"], sent[0]["project_root"]) == (worktree, str(repo))
