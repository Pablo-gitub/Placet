import logging
from bridge.bridge_daemon import BridgeDaemon

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    print("Starting Placet Local Bridge Daemon...")
    daemon = BridgeDaemon()
    daemon.run_forever()
