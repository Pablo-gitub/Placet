import logging

from bridge.bridge_daemon import BridgeDaemon
from bridge.stores import HttpStore

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # Talks to the backend over REST (PLACET_API_URL, PLACET_BRIDGE_TOKEN):
    # the bridge needs no access to the database.
    BridgeDaemon(store=HttpStore()).run_forever()
