"""
How the bridge wakes a session inside a provider's own app (RF-24): it
queues a user message into the conversation, which starts a turn and shows
up in the app like a message typed there.

- Codex: `codex queue --thread <id> --message <text>`, with the codex binary
  shipped in the desktop app (the command exists from 0.159).
- Antigravity 2.0: the app's language server, `SendUserCascadeMessage`. This
  API is not documented (RS-07): it is isolated here, every failure is
  reported with the app version, and nothing else depends on it.
"""
import logging
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)


class ChannelError(Exception):
    """The message was not queued: it can wait for another attempt."""


class ChannelUncertain(ChannelError):
    """
    The command ran but its outcome is unknown (a timeout after sending):
    the message may be in the conversation already, so it is not re-sent
    automatically (RF-29).
    """


# --- Codex ------------------------------------------------------------------------

CODEX_CANDIDATES = ("/usr/lib/chatgpt/resources/codex", "/opt/codex/codex")


class CodexChannel:
    can_wake_suspended = True

    def __init__(self, binary: Optional[str] = None, run: Callable = subprocess.run):
        self._binary = binary or os.environ.get("PLACET_CODEX_BIN")
        self._run = run
        self._checked: Optional[str] = None

    def binary(self) -> str:
        """A codex binary that has `queue`: the app's own, else the one in PATH."""
        if self._checked:
            return self._checked
        candidates = [self._binary] if self._binary else [*CODEX_CANDIDATES, shutil.which("codex")]
        for path in filter(None, candidates):
            if not os.access(path, os.X_OK):
                continue
            res = self._run([path, "queue", "--help"], capture_output=True, text=True, timeout=20)
            if res.returncode == 0:
                self._checked = path
                return path
        raise ChannelError(
            "No codex binary with the `queue` command (Codex 0.159 or later); "
            "set PLACET_CODEX_BIN to the one shipped with the Codex app."
        )

    def send(self, session_id: str, text: str) -> None:
        try:
            res = self._run(
                [self.binary(), "queue", "--thread", session_id, "--message", text],
                capture_output=True, text=True, timeout=60,
            )
        except subprocess.TimeoutExpired:
            raise ChannelUncertain("codex queue did not answer within 60 s") from None
        if res.returncode != 0:
            raise ChannelError(f"codex queue failed ({res.returncode}): {(res.stderr or res.stdout).strip()[:300]}")

    def open_conversation(self, session_id: str) -> None:
        """Best effort after durable acceptance; failure must never requeue mail."""
        thread_id = str(uuid.UUID(session_id))
        res = self._run(['/usr/bin/chatgpt', f'codex://threads/{thread_id}'],
                        capture_output=True, text=True, timeout=20)
        if res.returncode != 0:
            raise RuntimeError('Codex conversation link was not accepted')


# --- Antigravity 2.0 ----------------------------------------------------------------

LS_SERVICE = "exa.language_server_pb.LanguageServerService"


def _arg(args: List[str], name: str) -> Optional[str]:
    for i, arg in enumerate(args):
        if arg == name and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith(name + "="):
            return arg.split("=", 1)[1]
    return None


def _language_servers() -> List[Tuple[int, str, str]]:
    """(pid, csrf token, app version) of the Antigravity language servers of this user."""
    found = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            argv = (proc / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        args = [a.decode(errors="replace") for a in argv if a]
        if not args or os.path.basename(args[0]) != "language_server":
            continue
        token = _arg(args, "--csrf_token")
        if token:
            found.append((int(proc.name), token, _arg(args, "--override_ide_version") or "?"))
    return found


def _listening_ports(pid: int) -> List[int]:
    """Local TCP ports a process listens on (from /proc, no external tools)."""
    inodes = set()
    try:
        for fd in (Path("/proc") / str(pid) / "fd").iterdir():
            target = os.readlink(fd)
            match = re.match(r"socket:\[(\d+)\]", target)
            if match:
                inodes.add(match.group(1))
    except OSError:
        return []
    ports = []
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(table).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            # local_address, state 0A = LISTEN, inode
            if len(fields) > 9 and fields[3] == "0A" and fields[9] in inodes:
                ports.append(int(fields[1].split(":")[1], 16))
    return sorted(set(ports))


class AntigravityChannel:
    def __init__(
        self,
        client: Optional[httpx.Client] = None,
        discover: Callable[[], List[Tuple[int, str, str]]] = _language_servers,
        ports_of: Callable[[int], List[int]] = _listening_ports,
    ):
        self.client = client or httpx.Client(timeout=30.0)
        self._discover = discover
        self._ports_of = ports_of
        # cascade id -> (base url, token) of the server that owns it
        self._servers: Dict[str, Tuple[str, str]] = {}
        self.version = "?"

    def _call(self, base: str, token: str, method: str, body: dict) -> httpx.Response:
        return self.client.post(
            f"{base}/{LS_SERVICE}/{method}",
            json=body,
            headers={"Connect-Protocol-Version": "1", "x-codeium-csrf-token": token},
        )

    def _trajectory(self, cascade_id: str) -> Tuple[str, str, dict]:
        """The conversation, from the language server that has it."""
        cached = self._servers.get(cascade_id)
        candidates = [cached] if cached else []
        for pid, token, version in self._discover():
            self.version = version
            for port in self._ports_of(pid):
                candidates.append((f"http://127.0.0.1:{port}", token))
        for base, token in candidates:
            try:
                resp = self._call(base, token, "GetCascadeTrajectory", {"cascadeId": cascade_id})
            except httpx.HTTPError:
                continue
            if resp.status_code == 200:
                data = resp.json()
                if data.get("trajectory", {}).get("steps"):
                    self._servers[cascade_id] = (base, token)
                    return base, token, data["trajectory"]
        self._servers.pop(cascade_id, None)
        raise ChannelError(
            f"Antigravity is not running, or has no conversation {cascade_id} "
            f"(Antigravity {self.version})."
        )

    def quota_summary(self) -> dict:
        """The account's usage windows (RF-60), from any running language server."""
        for pid, token, version in self._discover():
            self.version = version
            for port in self._ports_of(pid):
                try:
                    resp = self._call(f"http://127.0.0.1:{port}", token, "RetrieveUserQuotaSummary", {})
                except httpx.HTTPError:
                    continue
                if resp.status_code == 200:
                    return resp.json()
        return {}

    def titles(self, ids) -> Dict[str, str]:
        """The titles ("summary") Antigravity gave these conversations, from any running server."""
        wanted, titles = set(ids), {}
        for pid, token, version in self._discover():
            self.version = version
            for port in self._ports_of(pid):
                try:
                    resp = self._call(f"http://127.0.0.1:{port}", token, "GetAllCascadeTrajectories", {})
                except httpx.HTTPError:
                    continue
                if resp.status_code != 200:
                    continue
                for cascade_id, summary in (resp.json().get("trajectorySummaries") or {}).items():
                    title = str((summary or {}).get("summary") or "").strip()
                    if cascade_id in wanted and title:
                        titles[cascade_id] = title
        return titles

    @staticmethod
    def _config(trajectory: dict) -> dict:
        """The configuration of the last user turn: model, tools, rules.
        Without it the language server accepts the message but cannot run it."""
        inputs = [s.get("userInput") or {} for s in reversed(trajectory.get("steps", []))]
        for key in ("userConfig", "lastUserConfig"):
            for user_input in inputs:
                if user_input.get(key):
                    return user_input[key]
        raise ChannelError("No previous user turn to copy the model configuration from.")

    def send(self, session_id: str, text: str, now: bool = False) -> None:
        """Queues the message for when the agent is idle, or with now=True for
        its next model call inside the running turn (RF-35)."""
        base, token, trajectory = self._trajectory(session_id)
        body = {
            "cascadeId": session_id,
            "items": [{"text": text}],
            "cascadeConfig": self._config(trajectory),
            "deliveryStrategy": ("MESSAGE_DELIVERY_STRATEGY_NEXT_INVOCATION" if now
                                 else "MESSAGE_DELIVERY_STRATEGY_WHEN_IDLE"),
        }
        try:
            resp = self._call(base, token, "SendUserCascadeMessage", body)
        except (httpx.ReadTimeout, httpx.WriteTimeout) as e:
            raise ChannelUncertain(f"Antigravity did not answer: {e}") from None
        except httpx.HTTPError as e:
            raise ChannelError(f"Antigravity language server unreachable: {e}") from None
        if resp.status_code != 200:
            raise ChannelError(
                f"SendUserCascadeMessage answered {resp.status_code} (Antigravity {self.version}): "
                f"{resp.text[:300]}"
            )


def default_channels() -> Dict[str, object]:
    return {"codex": CodexChannel(), "antigravity": AntigravityChannel()}
