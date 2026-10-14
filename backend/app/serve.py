"""
Runs the Placet app on two listeners sharing one process (and therefore the
same in-memory long-polling waiters and SSE subscribers):

- agent listener: TCP, loopback by default. Bearer tokens only.
- human listener: Unix socket meant to be the target of
  `tailscale serve unix:<socket>`. Only here is the Tailscale identity header
  trusted, because only tailscaled is supposed to connect to it.
"""
import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

import uvicorn

from backend.app.auth import HumanListener

logger = logging.getLogger(__name__)


class _SkipBridgePolling(logging.Filter):
    """The bridge polls /api/bridge/work every 2 seconds: keep it out of the access log."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "/api/bridge/work" not in record.getMessage()


def default_human_socket() -> str:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return str(Path(runtime_dir) / "placet" / "human.sock")


async def serve(
    app,
    host: Optional[str] = None,
    port: Optional[int] = None,
    human_socket: Optional[str] = None,
    log_level: str = "info",
):
    host = host or os.environ.get("PLACET_HOST", "127.0.0.1")
    port = port or int(os.environ.get("PLACET_PORT", "8000"))
    human_socket = human_socket or os.environ.get("PLACET_HUMAN_SOCKET") or default_human_socket()

    socket_path = Path(human_socket)
    # sun_path is limited to 108 bytes on Linux (104 on macOS).
    if len(os.fsencode(socket_path)) > 100:
        raise SystemExit(
            f"PLACET_HUMAN_SOCKET path is too long for a Unix socket ({socket_path}); "
            "use a shorter one, e.g. $XDG_RUNTIME_DIR/placet/human.sock."
        )
    socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # uvicorn makes the socket itself world-writable (0666): access is
    # restricted by its directory instead (tailscaled runs as root).
    if socket_path.parent.stat().st_mode & 0o077:
        logger.warning(
            f"{socket_path.parent} is accessible to other users: anyone who can "
            "reach the human socket can act as you. Use a private directory (0700)."
        )
    if socket_path.exists():
        socket_path.unlink()

    logging.getLogger("uvicorn.access").addFilter(_SkipBridgePolling())
    agent_server = uvicorn.Server(
        uvicorn.Config(app, host=host, port=port, log_level=log_level)
    )
    human_server = uvicorn.Server(
        uvicorn.Config(
            HumanListener(app),
            uds=str(socket_path),
            log_level=log_level,
            # The agent server already runs the app lifespan (expiry sweeper).
            lifespan="off",
        )
    )

    logger.info(f"Agent listener on http://{host}:{port}")
    logger.info(
        f"Human listener on unix:{socket_path} "
        f"(expose it with: tailscale serve --bg unix:{socket_path})"
    )
    servers = [agent_server, human_server]
    tasks = [asyncio.create_task(server.serve()) for server in servers]
    # If either listener stops (e.g. its port is taken), stop the other too:
    # half a backend must not look healthy to systemd.
    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for server in servers:
        server.should_exit = True
    await asyncio.gather(*tasks, return_exceptions=True)
    failed = [t for t in done if t.exception() is not None]
    if failed:
        raise SystemExit(f"Placet listener stopped: {failed[0].exception()!r}")
