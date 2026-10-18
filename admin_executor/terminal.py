"""Human-only passkey provisioning; run from a reviewed protected installation.

The web process stages an untrusted registration response; this terminal command
verifies it against the root-private ceremony before storing a credential. No
credentials database or authorization capability is shared with the web process.
"""
import argparse
import os
import stat
import json
import subprocess
from pathlib import Path
from urllib.parse import urlencode

from .authorization import AuthorizationStore


CONFIG = Path("/etc/placet-admin/origin")
DATABASE = Path("/var/lib/placet-admin/authorization.sqlite")
MAILBOX = Path("/var/lib/placet-admin-web/enrollment.sqlite")


def mailbox_call(operation, payload):
    # Never open or initialize a web-owned SQLite pathname as root: the web UID
    # could replace it or its parent while enrollment is being finalized.
    result = subprocess.run([
        "/usr/sbin/runuser", "-u", "placet-admin-web", "--", "/usr/bin/env", "-i",
        "PATH=/usr/bin:/bin", "PYTHONPATH=/usr/local/lib/placet-admin",
        "/usr/local/lib/placet-admin-venv/bin/python", "-s", "-m", "admin_executor.mailbox_helper", operation,
    ], input=json.dumps(payload), capture_output=True, text=True, timeout=15, check=True)
    if len(result.stdout) > 100000:
        raise ValueError("Mailbox response exceeds limit")
    return json.loads(result.stdout)


def configured_origin():
    for path in (CONFIG, *CONFIG.parents):
        metadata = path.lstat()
        if metadata.st_uid != 0 or metadata.st_mode & 0o022 or stat.S_ISLNK(metadata.st_mode):
            raise ValueError("Administrative origin configuration must be root-protected")
    return CONFIG.read_text().strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("begin-enrollment", "finish-enrollment"))
    parser.add_argument("--id")
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        parser.error("Use a root-authorized human terminal; agents cannot provision passkeys")
    if args.operation == "finish-enrollment" and (not args.id or len(args.id) != 32 or any(c not in "0123456789abcdef" for c in args.id)):
        parser.error("A complete enrollment ID is required")
    if not MAILBOX.is_file() or MAILBOX.is_symlink():
        parser.error("The administrative web service must create its mailbox first")
    origin = configured_origin()
    store = AuthorizationStore(DATABASE, rp_id=origin.removeprefix("https://"), origin=origin)
    if args.operation == "begin-enrollment":
        ceremony = store.begin_enrollment()
        token = mailbox_call("provision", ceremony)["token"]
        print("Open this private, five-minute invitation directly on your device.")
        print("Do not send the URL through Placet, agents, logs or issue trackers.")
        print(origin + "/#" + urlencode({"enroll": ceremony["id"], "token": token}))
        print("After registering on the device, finish in this terminal with enrollment ID:", ceremony["id"])
    else:
        store.finish_enrollment(args.id, mailbox_call("pending", {"id": args.id})["credential"])
        print("Passkey verified and enrolled. Administrative commands remain disabled.")


if __name__ == "__main__":
    main()
