import logging

from bridge.bridge_daemon import BridgeDaemon
from bridge.spool_forwarder import SpoolForwarder, http_sender
from bridge.stores import HttpStore

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # One request every 2 seconds: do not log each one.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # Talks to the backend over REST (PLACET_API_URL, PLACET_BRIDGE_TOKEN):
    # the bridge needs no access to the database.
    # The hooks queue their events locally: the bridge forwards them.
    BridgeDaemon(store=HttpStore(), spool=SpoolForwarder(http_sender())).run_forever()
