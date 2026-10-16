import time
import logging
import subprocess
import threading
from typing import List, Optional, Set

from backend.app.config import PlacetConfig
from backend.app.database import Database
from backend.app import envelopes
from backend.app.models import RequestModel
from bridge.channels import ChannelError, ChannelUncertain, default_channels
from bridge.stores import BridgeStore, LocalStore, Work
from bridge.tmux_controller import SessionSpec, TmuxController
from bridge.transcripts import TranscriptError, read_transcript
from hooks.placet_common import active

logger = logging.getLogger(__name__)

# Do not inject into a session where someone pressed a key this recently:
# the text would mix with what the user is typing.
RECENT_INPUT_SECONDS = 15.0
# How often the conversation of a session is read to settle an uncertain delivery.
RECONCILE_EVERY_SECONDS = 20.0
# How often the folders of known sessions are checked for `.placet-off`.
FOLDERS_EVERY_SECONDS = 15.0



class BridgeDaemon:
    def __init__(
        self,
        db: Optional[Database] = None,
        config: Optional[PlacetConfig] = None,
        tmux: Optional[TmuxController] = None,
        store: Optional[BridgeStore] = None,
        channels: Optional[dict] = None,
        spool=None,
    ):
        # Direct database access (db/config) is the single-process dev mode;
        # in production pass an HttpStore.
        self.store = store or LocalStore(db, config)
        self.tmux = tmux or TmuxController()
        # provider -> channel that queues a message into the provider's app
        self.channels = channels if channels is not None else default_channels()
        # forwards the hooks' local event queue to the backend
        self.spool = spool
        self._reconciled_at: dict = {}
        self._folders_at = -FOLDERS_EVERY_SECONDS
        self._excluded: Optional[Set[str]] = None

    def format_injection_envelope(self, req: RequestModel, ack_mode: str = "mcp") -> str:
        return envelopes.injection(req, ack_mode=ack_mode, channel="tmux")

    def format_result_envelope(self, req: RequestModel) -> str:
        return envelopes.outcome(req, channel="tmux")

    def _session_ready(
        self, agent_id: str, busy_sessions: Set[str], busy_agents: Set[str]
    ) -> Optional[SessionSpec]:
        """Returns the agent's session if text can be written into it now."""
        spec = self.store.session_for(agent_id)
        if not spec:
            logger.warning(f"Agent {agent_id} not defined in config.")
            return None
        if spec.name in busy_sessions:
            return None
        if agent_id in busy_agents:
            logger.debug(f"Agent {agent_id} declared a turn in progress.")
            return None
        if not self.tmux.session_exists(spec.name):
            logger.debug(f"Tmux session {spec.name} for agent {agent_id} is not running.")
            return None
        # Even an agent that declared itself idle may show a menu or dialog:
        # the screen check stays as a safety net.
        if not self.tmux.is_session_idle(spec.name, spec.idle_patterns, spec.busy_patterns):
            logger.debug(f"Session {spec.name} is currently busy.")
            return None
        recent = self.tmux.seconds_since_input(spec.name)
        if isinstance(recent, (int, float)) and recent < RECENT_INPUT_SECONDS:
            logger.debug(f"Session {spec.name}: user input {recent:.0f}s ago, waiting.")
            return None
        return spec

    def process_pending_results(
        self,
        busy_sessions: Optional[Set[str]] = None,
        results: Optional[List[RequestModel]] = None,
        busy_agents: Optional[Set[str]] = None,
    ) -> int:
        """
        RF-22: injects settled outcomes into the sender's session when the
        sender did not receive them through long-polling (its wait expired).
        """
        busy_sessions = busy_sessions if busy_sessions is not None else set()
        if results is None or busy_agents is None:
            work = self.store.work()
            results = work.results if results is None else results
            busy_agents = set(work.busy_agents) if busy_agents is None else busy_agents
        delivered = 0
        for req in results:
            spec = self._session_ready(req.sender_id, busy_sessions, busy_agents)
            if spec is None:
                continue
            # Claim first: if a long-poll delivered it meanwhile, skip.
            if not self.store.claim_delivery(req.id):
                continue
            logger.info(f"Delivering outcome of request #{req.id} to session {spec.name}...")
            if self.tmux.send_keys(spec.name, self.format_result_envelope(req), enter=True):
                busy_sessions.add(spec.name)
                delivered += 1
            else:
                self.store.release_delivery(req.id)
                logger.error(f"Could not deliver outcome of request #{req.id} to {spec.name}.")
        return delivered

    def process_pending_injections(
        self,
        busy_sessions: Optional[Set[str]] = None,
        injections: Optional[List[RequestModel]] = None,
        busy_agents: Optional[Set[str]] = None,
    ) -> int:
        """
        Injects APPROVED agent-to-agent requests into the recipient's session.
        The request is claimed (DELIVERING) before sending, so a crash cannot
        lead to a silent second injection; it becomes EXECUTING when the agent
        acknowledges it, or right away for agents with ack_mode none.
        """
        busy_sessions = busy_sessions if busy_sessions is not None else set()
        if injections is None or busy_agents is None:
            work = self.store.work()
            injections = work.injections if injections is None else injections
            busy_agents = set(work.busy_agents) if busy_agents is None else busy_agents
        processed = 0
        for req in injections:
            spec = self._session_ready(req.recipient_id, busy_sessions, busy_agents)
            if spec is None:
                continue
            if not self.store.claim_injection(req.id):
                logger.info(f"Request #{req.id} is no longer APPROVED, skipping.")
                continue
            logger.info(f"Injecting request #{req.id} into session {spec.name}...")
            envelope = self.format_injection_envelope(req, spec.ack_mode)
            if not self.tmux.send_keys(spec.name, envelope, enter=True):
                self.store.release_injection(req.id)
                logger.error(f"Could not inject request #{req.id} into {spec.name}.")
                continue
            busy_sessions.add(spec.name)
            if spec.ack_mode == "none":
                self.store.confirm_injection(req.id)
            processed += 1
        return processed

    def process_session_mail(self, sessions: List[dict]) -> int:
        """
        Wakes Codex and Antigravity sessions in their app (RF-24): takes the
        next message of each session and queues it into the conversation.
        An injection counts as received once queued (the app shows it like a
        user message); if queuing fails the message goes back to wait.
        """
        delivered = 0
        for target in sessions:
            channel = self.channels.get(target.get("provider"))
            if channel is None:
                continue
            mail = self.store.take_session_mail(target["id"])
            if mail is None:
                continue
            try:
                channel.send(target["id"], mail.text)
            except ChannelUncertain as e:
                # It may be in the conversation already: never sent twice by itself.
                logger.warning(f"Delivery of request #{mail.request_id} to {target['id']} is uncertain: {e}")
                if mail.kind == "injection":
                    self.store.mark_uncertain(mail.request_id)
                continue
            except (ChannelError, OSError, subprocess.SubprocessError) as e:
                logger.error(f"Could not wake session {target['id']} ({target.get('provider')}): {e}")
                if mail.kind == "injection":
                    self.store.release_injection(mail.request_id)
                else:
                    self.store.release_delivery(mail.request_id)
                continue
            if mail.kind == "injection":
                self.store.confirm_injection(mail.request_id)
            logger.info(f"Queued request #{mail.request_id} ({mail.kind}) into session {target['id']}.")
            delivered += 1
        return delivered

    def reconcile_uncertain(self, uncertain: List[dict]) -> int:
        """
        Looks for uncertain deliveries in the recipient's conversation: a
        message found there was delivered (RF-29). Returns how many settled.
        """
        settled = 0
        now = time.monotonic()
        for item in uncertain:
            request_id = item["request_id"]
            if now - self._reconciled_at.get(request_id, -RECONCILE_EVERY_SECONDS) < RECONCILE_EVERY_SECONDS:
                continue
            self._reconciled_at[request_id] = now
            try:
                page = read_transcript(item.get("provider"), item["session_id"],
                                       agent_id=item.get("agent_id"), limit=80)
            except TranscriptError as e:
                logger.debug(f"Cannot read {item['session_id']} to reconcile #{request_id}: {e}")
                continue
            if any(m.get("request_id") == request_id for m in page.get("messages", [])):
                self.store.mark_seen(request_id)
                self._reconciled_at.pop(request_id, None)
                logger.info(f"Request #{request_id} found in session {item['session_id']}: delivered.")
                settled += 1
        return settled

    def check_folders(self, folders: List[str]) -> Set[str]:
        """
        Looks for `.placet-off` in the folders of known sessions (RF-45) and
        tells the backend, which hides them and stops their deliveries. The
        backend runs as another user and cannot look itself.
        """
        now = time.monotonic()
        if self._excluded is not None and now - self._folders_at < FOLDERS_EVERY_SECONDS:
            return self._excluded
        self._folders_at = now
        excluded = {cwd for cwd in folders if not active(cwd)}
        self.store.report_folders(sorted(set(folders)), sorted(excluded))
        self._excluded = excluded
        return excluded

    def run_loop_step(self) -> int:
        # At most one injection per session per tick, outcomes first: they
        # unblock work that is already waiting.
        work: Work = self.store.work()
        self.check_folders(work.folders)
        busy_sessions: Set[str] = set()
        busy_agents = set(work.busy_agents)
        delivered = self.process_pending_results(busy_sessions, work.results, busy_agents)
        delivered += self.process_pending_injections(busy_sessions, work.injections, busy_agents)
        delivered += self.process_session_mail(work.sessions)
        self.reconcile_uncertain(work.uncertain)
        return delivered

    # --- Jobs someone is waiting for (conversation pages) ---------------------

    def handle_job(self, job: dict) -> dict:
        """The result of one job, never an exception: the backend is waiting."""
        kind = job.get("kind")
        try:
            if kind == "transcript":
                if not active(job.get("cwd")):
                    return {"ok": False, "error": "excluded", "message": "Project excluded (.placet-off)."}
                page = read_transcript(
                    job.get("provider"), job.get("session_id", ""), agent_id=job.get("agent_id"),
                    before=job.get("before"), after=job.get("after"),
                    limit=job.get("limit") or 40,
                )
            else:
                return {"ok": False, "error": "unknown_job", "message": f"Unknown job {kind!r}."}
            return {"ok": True, "page": page}
        except TranscriptError as e:
            return {"ok": False, "error": e.code, "message": str(e)}
        except Exception as e:
            logger.exception("Job %s failed", job.get("id"))
            return {"ok": False, "error": "internal", "message": str(e)}

    def serve_jobs_forever(self):
        backoff = 1.0
        while True:
            try:
                for job in self.store.take_jobs():
                    self.store.post_job_result(job["id"], self.handle_job(job))
                backoff = 1.0
            except Exception as e:
                logger.warning(f"Bridge jobs: {e}")
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    def run_forever(self, interval_seconds: float = 2.0):
        logger.info("Starting Placet Bridge Daemon...")
        if hasattr(self.store, "take_jobs"):
            threading.Thread(target=self.serve_jobs_forever, name="bridge-jobs", daemon=True).start()
        if self.spool is not None:
            threading.Thread(target=self.spool.run_forever, name="bridge-spool", daemon=True).start()
        while True:
            try:
                self.run_loop_step()
            except Exception as e:
                logger.error(f"Error in bridge daemon step: {e}")
            time.sleep(interval_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from bridge.stores import HttpStore
    from bridge.spool_forwarder import SpoolForwarder, http_sender
    BridgeDaemon(store=HttpStore(), spool=SpoolForwarder(http_sender())).run_forever()
