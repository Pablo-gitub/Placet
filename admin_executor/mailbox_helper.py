"""Unprivileged mailbox I/O for the human terminal, never an authorization API."""
import json
import os
import sys

from .web import EnrollmentMailbox


def main():
    if os.geteuid() == 0:
        raise ValueError("Mailbox transport must run as the unprivileged web user")
    if len(sys.argv) != 2 or sys.argv[1] not in ("provision", "pending"):
        raise ValueError("Unknown mailbox operation")
    raw = sys.stdin.read(100001)
    if len(raw) > 100000:
        raise ValueError("Mailbox payload exceeds limit")
    payload = json.loads(raw)
    mailbox = EnrollmentMailbox("/var/lib/placet-admin-web/enrollment.sqlite")
    if sys.argv[1] == "provision":
        print(json.dumps({"token": mailbox._write_invitation(payload)}))
    else:
        print(json.dumps({"credential": mailbox._read_pending(payload["id"])}))


if __name__ == "__main__":
    main()
