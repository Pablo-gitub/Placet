"""Dedicated UDS-only web process. No root execution or privileged actions."""
import os
from pathlib import Path


def main():
    if os.geteuid() == 0:
        raise ValueError("The administrative web service must run unprivileged")
    import uvicorn
    from .web import EnrollmentMailbox, create_app
    from .control import BrokerClient

    origin = Path("/run/placet-admin-web/origin").read_text().strip()
    mailbox = EnrollmentMailbox(Path("/var/lib/placet-admin-web/enrollment.sqlite"))
    app = create_app(origin=origin, mailbox=mailbox, trusted_unix_proxy=True,
                     broker=BrokerClient('/run/placet-admin-control/web.sock'))
    # The service manager creates this directory. Its group must grant access
    # only to Tailscale's supplementary web group and this web UID, never the
    # desktop or backend UID. The directory is 0710; it is not world-traversable.
    uvicorn.run(app, uds="/run/placet-admin-web/http.sock", proxy_headers=False,
                access_log=False, server_header=False)


if __name__ == "__main__":
    main()
