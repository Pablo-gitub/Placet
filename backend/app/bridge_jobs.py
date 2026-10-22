"""
Jobs the backend hands to the bridge and waits for, such as reading a page
of a session's conversation (the backend cannot read those files; RF-31).

The bridge long-polls GET /api/bridge/jobs and posts each result back; the
request that created the job waits for it with a short timeout. Jobs live
in memory: a lost job only means one failed page load, which the PWA
retries.
"""
import asyncio
import contextlib
import time
import uuid
from collections import deque
from typing import Deque, Dict, List, Optional, Set

# The bridge polls continuously; if it has not done so for this long it is
# considered offline (PC asleep, service stopped) and jobs fail at once.
BRIDGE_ALIVE_SECONDS = 60.0
JOB_TIMEOUT_SECONDS = 8.0
MAX_JOBS_PER_POLL = 8


class BridgeOffline(Exception):
    pass


class BridgeTimeout(Exception):
    pass


class BridgeJobs:
    def __init__(self, alive_seconds: float = BRIDGE_ALIVE_SECONDS):
        self.alive_seconds = alive_seconds
        self._pending: Deque[dict] = deque()
        self._results: Dict[str, asyncio.Future] = {}
        self._pollers: Set[asyncio.Event] = set()
        self._seen_at: Optional[float] = None

    def bridge_alive(self) -> bool:
        return self._seen_at is not None and time.monotonic() - self._seen_at < self.alive_seconds

    async def run(self, kind: str, payload: dict, timeout: Optional[float] = None) -> dict:
        """Queues a job and returns the bridge's result."""
        if not self.bridge_alive():
            raise BridgeOffline()
        timeout = JOB_TIMEOUT_SECONDS if timeout is None else timeout
        job_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._results[job_id] = future
        job = {"id": job_id, "kind": kind, **payload}
        self._pending.append(job)
        for ev in list(self._pollers):
            ev.set()
        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            raise BridgeTimeout() from None
        finally:
            self._results.pop(job_id, None)
            with contextlib.suppress(ValueError):
                self._pending.remove(job)

    async def take(self, wait_seconds: float) -> List[dict]:
        """For the bridge: the queued jobs, waiting up to `wait_seconds` for one."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_seconds
        while True:
            self._seen_at = time.monotonic()
            if self._pending:
                return [self._pending.popleft() for _ in range(min(MAX_JOBS_PER_POLL, len(self._pending)))]
            remaining = deadline - loop.time()
            if remaining <= 0:
                return []
            ev = asyncio.Event()
            self._pollers.add(ev)
            try:
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(ev.wait(), timeout=remaining)
            finally:
                self._pollers.discard(ev)

    def resolve(self, job_id: str, result: dict) -> bool:
        """Delivers the bridge's result; False if nobody waits for it any more."""
        self._seen_at = time.monotonic()
        future = self._results.get(job_id)
        if future is None or future.done():
            return False
        future.set_result(result)
        return True
