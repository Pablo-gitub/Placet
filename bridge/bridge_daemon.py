import time
import logging
from typing import List, Optional, Set

from backend.app.config import PlacetConfig
from backend.app.database import Database
from backend.app.models import RequestModel, RequestStatus
from bridge.stores import BridgeStore, LocalStore, Work
from bridge.tmux_controller import SessionSpec, TmuxController

logger = logging.getLogger(__name__)

# Do not inject into a session where someone pressed a key this recently:
# the text would mix with what the user is typing.
RECENT_INPUT_SECONDS = 15.0

_OUTCOME_NOTES = {
    RequestStatus.DELIVERY_FAILED: "Il prompt non è stato confermato dall'agente destinatario dopo più tentativi.",
    RequestStatus.STALLED: "L'agente destinatario ha ricevuto il prompt ma non ha risposto entro il tempo massimo.",
    RequestStatus.EXPIRED: "La richiesta è scaduta.",
}


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

    def format_injection_envelope(self, req: RequestModel, ack_mode: str = "mcp") -> str:
        prompt_to_send = req.edited_prompt if req.edited_prompt is not None else req.prompt
        context_part = f"\n[Contesto]: {req.context}" if req.context else ""
        ack_part = (
            f"Prima di iniziare chiama placet_ack(request_id={req.id}) per confermare la ricezione. "
            if ack_mode == "mcp" else ""
        )
        return (
            f"[Placet Request #{req.id} da {req.sender_id}]: {prompt_to_send}{context_part}\n"
            f"{ack_part}Quando hai terminato, rispondi usando lo strumento MCP "
            f"placet_reply(request_id={req.id}, response=\"...\")."
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
            if req.status in _OUTCOME_NOTES:
                lines.append(_OUTCOME_NOTES[req.status])
            if req.response is not None:
                origin = (
                    " (risposta implicita: ultimo messaggio dell'agente, non inviato con placet_reply)"
                    if req.response.implicit else ""
                )
                lines.append(f"Risposta di {req.response.responder_id}{origin}: {req.response.content}")
        lines.append(
            "Riprendi il lavoro che avevi sospeso in attesa di questo esito "
            f"(dettagli con placet_check_status(request_id={req.id}))."
        )
        return "\n".join(lines)

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

    def run_loop_step(self) -> int:
        # At most one injection per session per tick, outcomes first: they
        # unblock work that is already waiting.
        work: Work = self.store.work()
        busy_sessions: Set[str] = set()
        busy_agents = set(work.busy_agents)
        delivered = self.process_pending_results(busy_sessions, work.results, busy_agents)
        return delivered + self.process_pending_injections(busy_sessions, work.injections, busy_agents)

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
