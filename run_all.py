import asyncio
import threading
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
    from backend.app.main import app
    from backend.app.serve import serve

    # Start bridge daemon in daemon thread
    bridge_thread = threading.Thread(target=start_bridge, daemon=True)
    bridge_thread.start()

    asyncio.run(serve(app))
