import time
import logging
from typing import List, Optional, Set

from backend.app.config import PlacetConfig
from backend.app.database import Database
from backend.app.models import RequestModel
from bridge.stores import BridgeStore, LocalStore
from bridge.tmux_controller import TmuxController

logger = logging.getLogger(__name__)


class BridgeDaemon:
    def __init__(
        self,
        db: Optional[Database] = None,
        config: Optional[PlacetConfig] = None,
        tmux: Optional[TmuxController] = None,
        store: Optional[BridgeStore] = None,
    ):
        # Direct database access (db/config) is the single-process dev mode;
        # in production pass an HttpStore.
        self.store = store or LocalStore(db, config)
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
                label = "Altro" if req.options else "Nota dell'utente"
                lines.append(f"{label}: {req.custom_text}")
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
        session_name = self.store.session_for(agent_id)
        if not session_name:
            logger.warning(f"Agent {agent_id} not defined in config.")
            return None
        if session_name in busy_sessions:
            return None
        if not self.tmux.session_exists(session_name):
            logger.debug(f"Tmux session {session_name} for agent {agent_id} is not running.")
            return None
        if not self.tmux.is_session_idle(session_name):
            logger.debug(f"Session {session_name} is currently busy.")
            return None
        return session_name

    def process_pending_results(
        self,
        busy_sessions: Optional[Set[str]] = None,
        results: Optional[List[RequestModel]] = None,
    ) -> int:
        """
        RF-22: injects settled outcomes into the sender's session when the
        sender did not receive them through long-polling (its wait expired).
        """
        busy_sessions = busy_sessions if busy_sessions is not None else set()
        if results is None:
            _, results = self.store.work()
        delivered = 0
        for req in results:
            session_name = self._session_ready(req.sender_id, busy_sessions)
            if session_name is None:
                continue
            # Claim first: if a long-poll delivered it meanwhile, skip.
            if not self.store.claim_delivery(req.id):
                continue
            logger.info(f"Delivering outcome of request #{req.id} to session {session_name}...")
            if self.tmux.send_keys(session_name, self.format_result_envelope(req), enter=True):
                busy_sessions.add(session_name)
                delivered += 1
            else:
                self.store.release_delivery(req.id)
                logger.error(f"Could not deliver outcome of request #{req.id} to {session_name}.")
        return delivered

    def process_pending_injections(
        self,
        busy_sessions: Optional[Set[str]] = None,
        injections: Optional[List[RequestModel]] = None,
    ) -> int:
        """
        Injects APPROVED agent-to-agent requests into the recipient's session
        when it is idle, at most one per session per tick.
        """
        busy_sessions = busy_sessions if busy_sessions is not None else set()
        if injections is None:
            injections, _ = self.store.work()
        processed = 0
        for req in injections:
            session_name = self._session_ready(req.recipient_id, busy_sessions)
            if session_name is None:
                continue
            logger.info(f"Injecting request #{req.id} into session {session_name}...")
            if not self.tmux.send_keys(session_name, self.format_injection_envelope(req), enter=True):
                logger.error(f"Could not inject request #{req.id} into {session_name}.")
                continue
            busy_sessions.add(session_name)
            # Mark as EXECUTING so it is not injected again.
            if self.store.mark_injected(req.id):
                processed += 1
            else:
                logger.warning(f"Request #{req.id} changed state while being injected.")
        return processed

    def run_loop_step(self) -> int:
        # At most one injection per session per tick, outcomes first: they
        # unblock work that is already waiting.
        injections, results = self.store.work()
        busy_sessions: Set[str] = set()
        delivered = self.process_pending_results(busy_sessions, results)
        return delivered + self.process_pending_injections(busy_sessions, injections)

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
    from bridge.stores import HttpStore
    BridgeDaemon(store=HttpStore()).run_forever()
