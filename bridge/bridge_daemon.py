import time
import logging
from typing import Optional
from backend.app.database import Database
from backend.app.config import PlacetConfig, load_agents_config
from backend.app.models import RequestModel, RequestStatus
from bridge.tmux_controller import TmuxController

logger = logging.getLogger(__name__)


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

    def process_pending_injections(self) -> int:
        """
        Scans for APPROVED requests directed at agents, verifies terminal idle status,
        and injects the prompt envelope.
        """
        approved_requests = self.db.list_requests(status=RequestStatus.APPROVED)
        processed = 0

        for req in approved_requests:
            if req.recipient_id == "human":
                continue

            target_agent = self.config.get_agent(req.recipient_id)
            if not target_agent:
                logger.warning(f"Target agent {req.recipient_id} not defined in config.")
                continue

            session_name = target_agent.tmux_session
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
        return self.process_pending_injections()

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
