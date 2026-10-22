"""Shared helpers for the Placet hooks (stdlib only: hooks run with python3)."""
import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

CONFIG_DIR = Path.home() / ".config" / "placet"


def option(argv, name: str) -> Optional[str]:
    """The value after `name` on the command line, if any."""
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def agent_id(argv) -> str:
    """--agent <id> on the command line, else PLACET_HOOK_AGENT."""
    return option(argv, "--agent") or os.environ.get("PLACET_HOOK_AGENT", "")


def token_for(agent: str) -> str:
    token = os.environ.get("PLACET_API_TOKEN", "")
    if token:
        return token
    key = f"PLACET_TOKEN_{agent.upper()}="
    try:
        for line in (CONFIG_DIR / "agent-tokens.env").read_text().splitlines():
            if line.startswith(key):
                return line[len(key):].strip()
    except OSError:
        pass
    return ""


def api_url() -> str:
    return os.environ.get("PLACET_API_URL", "http://127.0.0.1:8000").rstrip("/")


OFF_MARKER = ".placet-off"


def active(cwd: Optional[str]) -> bool:
    """
    Whether sessions in this folder go through Placet: all of them, quietly
    (they only report turn starts and ends, and act on a Placet message when
    one arrives), except projects with a `.placet-off` file in the folder or
    in one containing it.
    """
    if not cwd:
        return True
    try:
        folder = Path(cwd).resolve()
    except (OSError, RuntimeError):
        return True
    return not any((current / OFF_MARKER).exists() for current in (folder, *folder.parents))


def tmux_server_pid() -> Optional[str]:
    """The pid of the tmux server this process runs under ($TMUX is `socket,server_pid,index`)."""
    parts = os.environ.get("TMUX", "").split(",")
    return parts[1] if len(parts) >= 3 and parts[1].isdigit() else None


def agent_process(start_pid: Optional[int] = None, proc_root: Path = Path("/proc")):
    """
    (pid, start time) of the nearest `claude` process above this one: the agent
    the session runs in, identified by the kernel's start time so that a reused pid
    is not mistaken for it. None when there is none (or no /proc).
    """
    pid = os.getppid() if start_pid is None else start_pid
    for _ in range(32):
        if pid <= 1:
            return None
        try:
            stat = (proc_root / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
            if (proc_root / str(pid) / "comm").read_text().strip() == "claude":
                return pid, stat[19]
            pid = int(stat[1])
        except (OSError, IndexError, ValueError):
            return None
    return None


GIT_TIMEOUT_SECONDS = 2.0


def project_root(cwd: Optional[str]) -> Optional[str]:
    """
    The folder that identifies a session's project, for grouping sessions and
    comparing folders: the top level of the git checkout (so `cd backend` changes
    nothing) or, inside a linked worktree (`.worktrees/<x>`, `.claude/worktrees/<x>`),
    the main repository's root. Real paths (symlinks resolved). Outside git, or on
    any failure, the real path of `cwd` (or `cwd` itself if it does not exist).
    The raw working directory is reported separately: the desktop wake-up,
    transcripts, uploads and `.placet-off` depend on it.
    """
    if not cwd:
        return cwd
    env = {k: v for k, v in os.environ.items()
           if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_PREFIX")}
    try:
        folder = os.path.realpath(cwd)
        out = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-dir", "--git-common-dir"],
            cwd=folder, capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS, check=True, env=env,
        ).stdout.split("\n")
        top, git_dir, common_dir = (os.path.realpath(line.strip()) for line in out[:3])
    except (OSError, ValueError, subprocess.SubprocessError):
        return os.path.realpath(cwd) if os.path.exists(cwd) else cwd
    if git_dir != common_dir and Path(common_dir).name == ".git":
        return str(Path(common_dir).parent)   # a linked worktree: the main repository
    return top


def call(method: str, path: str, token: str, body=None, timeout: float = 3) -> Tuple[int, Optional[dict]]:
    request = urllib.request.Request(
        f"{api_url()}{path}",
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, None
