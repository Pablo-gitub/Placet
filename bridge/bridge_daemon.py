import time
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Set
from backend.app.database import Database
from backend.app.config import PlacetConfig, load_agents_config
from backend.app.models import RequestModel, RequestStatus
from bridge.tmux_controller import TmuxController

logger = logging.getLogger(__name__)

# A settled outcome is injected only after this delay, leaving time to a
# long-polling sender to receive it directly (and mark it delivered).
RESULT_GRACE_SECONDS = 10.0
# Outcomes older than this are not injected (e.g. a session restarted hours
# later should not be flooded with stale results).
RESULT_MAX_AGE = timedelta(hours=6)


class BridgeDaemon:
    def __init__(
        self,
        db: Optional[Database] = None,
        config: Optional[PlacetConfig] = None,
        tmux: Optional[TmuxController] = None,
    ):
        self.db = db or Database()
        self.config = config or load_agents_config()
        self.tmux = tmux or TmuxController()

    def format_injection_envelope(self, req: RequestModel) -> str:
        prompt_to_send = req.edited_prompt if req.edited_prompt is not None else req.prompt
        context_part = f"\n[Contesto]: {req.context}" if req.context else ""
        return (
            f"[Placet Request #{req.id} da {req.sender_id}]: {prompt_to_send}{context_part}\n"
            f"Quando hai terminato, rispondi usando lo strumento MCP placet_reply(request_id={req.id}, response=\"...\")."
        )

    def format_result_envelope(self, req: RequestModel) -> str:
        if req.recipient_id == "human":
            lines = [f"[Placet] Decisione dell'utente sulla richiesta #{req.id}: {req.status.value}."]
            if req.selected_options:
                lines.append(f"Opzioni scelte: {', '.join(req.selected_options)}")
            if req.custom_text:
                lines.append(f"Testo libero: {req.custom_text}")
            if req.edited_prompt is not None and req.edited_prompt != req.prompt:
                lines.append(f"Testo modificato dall'utente: {req.edited_prompt}")
        else:
            lines = [f"[Placet] Esito della richiesta #{req.id} a {req.recipient_id}: {req.status.value}."]
            if req.response is not None:
                lines.append(f"Risposta di {req.response.responder_id}: {req.response.content}")
        lines.append(
            "Riprendi il lavoro che avevi sospeso in attesa di questo esito "
            f"(dettagli con placet_check_status(request_id={req.id}))."
        )
        return "\n".join(lines)

    def _session_ready(self, agent_id: str, busy_sessions: Set[str]) -> Optional[str]:
        """Returns the agent's tmux session if it can receive text now."""
        agent = self.config.get_agent(agent_id)
        if not agent:
            logger.warning(f"Agent {agent_id} not defined in config.")
            return None
        session_name = agent.tmux_session
        if session_name in busy_sessions:
            return None
        if not self.tmux.session_exists(session_name):
            logger.debug(f"Tmux session {session_name} for agent {agent_id} is not running.")
            return None
        if not self.tmux.is_session_idle(session_name):
            logger.debug(f"Session {session_name} is currently busy.")
            return None
        return session_name

    def process_pending_results(self, busy_sessions: Optional[Set[str]] = None) -> int:
        """
        RF-22: injects settled outcomes into the sender's session when the
        sender did not receive them through long-polling (its wait expired).
        """
        busy_sessions = busy_sessions if busy_sessions is not None else set()
        now = datetime.now(timezone.utc)
        results = self.db.list_undelivered_results(
            settled_before=now - timedelta(seconds=RESULT_GRACE_SECONDS),
            settled_after=now - RESULT_MAX_AGE,
        )
        delivered = 0
        for req in results:
            session_name = self._session_ready(req.sender_id, busy_sessions)
            if session_name is None:
                continue
            # Claim first: if a long-poll delivered it meanwhile, skip.
            if not self.db.mark_delivered(req.id):
                continue
            logger.info(f"Delivering outcome of request #{req.id} to session {session_name}...")
            if self.tmux.send_keys(session_name, self.format_result_envelope(req), enter=True):
                busy_sessions.add(session_name)
                delivered += 1
            else:
                self.db.clear_delivered(req.id)
                logger.error(f"Could not deliver outcome of request #{req.id} to {session_name}.")
        return delivered

    def process_pending_injections(self, busy_sessions: Optional[Set[str]] = None) -> int:
        """
        Scans for APPROVED requests directed at agents, verifies terminal idle status,
        and injects the prompt envelope.
        Throttles injections so at most ONE request is injected into the same tmux session
        per loop iteration.
        """
        approved_requests = self.db.list_requests(
            status=RequestStatus.APPROVED, oldest_first=True
        )
        processed = 0
        injected_sessions_this_tick = busy_sessions if busy_sessions is not None else set()

        for req in approved_requests:
            if req.recipient_id == "human":
                continue

            target_agent = self.config.get_agent(req.recipient_id)
            if not target_agent:
                logger.warning(f"Target agent {req.recipient_id} not defined in config.")
                continue

            session_name = target_agent.tmux_session
            if session_name in injected_sessions_this_tick:
                # Already injected a task into this session in the current pass; wait for next tick
                continue

            if not self.tmux.session_exists(session_name):
                logger.warning(
                    f"Tmux session {session_name} for agent {req.recipient_id} is not running."
                )
                continue

            if not self.tmux.is_session_idle(session_name):
                logger.info(
                    f"Session {session_name} is currently busy. Queueing request #{req.id}."
                )
                continue

            envelope = self.format_injection_envelope(req)
            logger.info(f"Injecting request #{req.id} into session {session_name}...")
            sent = self.tmux.send_keys(session_name, envelope, enter=True)

            if sent:
                injected_sessions_this_tick.add(session_name)
                # Mark as EXECUTING so it's not reinjected
                self.db.update_request_decision(
                    request_id=req.id,
                    status=RequestStatus.EXECUTING,
                    edited_prompt=req.edited_prompt,
                    selected_options=req.selected_options,
                    custom_text=req.custom_text,
                )
                processed += 1


        return processed

    def run_loop_step(self) -> int:
        # At most one injection per session per tick, outcomes first: they
        # unblock work that is already waiting.
        busy_sessions: Set[str] = set()
        delivered = self.process_pending_results(busy_sessions)
        return delivered + self.process_pending_injections(busy_sessions)

    def run_forever(self, interval_seconds: float = 2.0):
        logger.info("Starting Placet Bridge Daemon...")
        while True:
            try:
                self.run_loop_step()
            except Exception as e:
                logger.error(f"Error in bridge daemon step: {e}")
            time.sleep(interval_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    daemon = BridgeDaemon()
    daemon.run_forever()
