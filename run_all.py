import os
import sys
import threading
import uvicorn
import logging
from bridge.bridge_daemon import BridgeDaemon

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("placet")


def start_bridge():
    logger.info("Initializing Bridge Daemon thread...")
    try:
        daemon = BridgeDaemon()
        daemon.run_forever()
    except Exception as e:
        logger.error(f"Bridge daemon error: {e}")


if __name__ == "__main__":
    host = os.environ.get("PLACET_HOST", "0.0.0.0")
    port = int(os.environ.get("PLACET_PORT", "8000"))

    # Start bridge daemon in daemon thread
    bridge_thread = threading.Thread(target=start_bridge, daemon=True)
    bridge_thread.start()

    logger.info(f"Placet all-in-one running on http://{host}:{port}")
    uvicorn.run("backend.app.main:app", host=host, port=port, log_level="info")
