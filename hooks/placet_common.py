"""Shared helpers for the Placet hooks (stdlib only: hooks run with python3)."""
import json
import os
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


def opted_out(cwd: Optional[str]) -> bool:
    """A `.placet-off` file in the project keeps its sessions out of Placet."""
    return bool(cwd) and (Path(cwd) / ".placet-off").exists()


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
