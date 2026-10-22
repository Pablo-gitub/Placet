import asyncio
import threading
import logging
from bridge.bridge_daemon import BridgeDaemon
from bridge.claude_desktop_engines import EngineCounter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("placet")


def start_bridge():
    logger.info("Initializing Bridge Daemon thread...")
    try:
        from bridge.spool_forwarder import SpoolForwarder, http_sender
        daemon = BridgeDaemon(spool=SpoolForwarder(http_sender()), engine_counter=EngineCounter())
        daemon.run_forever()
    except Exception as e:
        logger.error(f"Bridge daemon error: {e}")


if __name__ == "__main__":
    import os
    # One process, running as the user: the backend reads conversation
    # pages itself instead of asking the bridge (production never does).
    os.environ.setdefault("PLACET_TRANSCRIPTS_LOCAL", "1")
    from backend.app.main import app
    from backend.app.serve import serve

    # Start bridge daemon in daemon thread
    bridge_thread = threading.Thread(target=start_bridge, daemon=True)
    bridge_thread.start()

    asyncio.run(serve(app))
