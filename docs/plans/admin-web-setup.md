# Administrative web service and passkey setup

Status: dedicated page, staging mailbox, root-terminal verification, service
template and fresh-install bootstrap implemented and tested locally. Production
installation and actual iPhone registration require the human steps below.
No privileged action can be executed through this service yet.

## Scope and privileges

The page runs as `placet-admin-web`, on a filesystem Unix socket only. Its
database stages registration responses and contains no enrolled public keys or
root ceremony authority. The terminal command verifies registration against
`/var/lib/placet-admin/authorization.sqlite`, inaccessible to the web process.
Mailbox I/O from the terminal drops to the web UID before opening any web-owned
SQLite path; root never initializes that untrusted database directly.

The web directory is 0710, with a group-accessible socket. The separate root
Tailscale daemon has an empty capability set, so UID0 alone does not give it
permission to traverse another UID's private directory. The installer adds only
the web group to that dedicated daemon via a protected systemd drop-in and
restarts that transport. The existing desktop Tailscale instance is unchanged.
The desktop user and Placet backend must not belong to the web group.

## Review and installation

`web_bootstrap.py` previews the exact origin, bundle digest and intended work.
It parses the source manifest as data before executing checkout modules. Applying
requires root and the matching bundle digest. Initial bootstrap code itself must
also be reviewed and held unchanged: when handing over an install command, pin
the bootstrap file's SHA256 and compile those checked bytes rather than reopening
the pathname after checking. Preview:

```bash
/usr/bin/python3 -I admin_executor/web_bootstrap.py --origin https://placet-admin.YOUR-TAILNET.ts.net
```

The fresh installation refuses existing code, runtime, state, user and service
paths. It installs the frozen reviewed modules and disabled catalogue, creates
a non-login web UID, builds the pinned administrative dependencies through a
private `DynamicUser` systemd job and copies only regular output after shutdown.
No pip or application requirements run as root. Dependency fetching uses PyPI
and requires network access; unlike the application build worker, this is not
an offline job. The job does not inherit the human terminal. Runtime relocation
is checked using the unprivileged web UID at the final protected path.

The installer then starts the UDS-only web service and restarts only the dedicated
admin transport for its supplementary group. It does not log Tailscale in, change
Serve configuration, deploy Placet or enable any command. Failures leave the
catalogue disabled; partial installation must be inspected manually before
retrying. Do not delete state or overwrite an installation blindly.

### Recovery from the first dependency build failure

The first live installation reached dependency preparation but failed creating
the venv with EACCES on its copied interpreter in the DynamicUser state tree.
The host-visible owner65534 is expected for id-mapped state, not a reason to
chown the tree or weaken its0700 permissions. Executing from this tree is
consistent with a [reported systemd issue](https://github.com/systemd/systemd/issues/36296);
the exact kernel path remains unconfirmed. The worker now builds and executes
the venv in its private `/tmp` and copies only the result into persistent state
as `venv-ready`. It does not execute binaries from persistent build state.

`--resume-failed-bootstrap <installed-bundle-digest>` is a narrow, explicit
recovery mode. It requires the newly reviewed source digest as usual, verifies
the exact installed bundle, protected files, unchanged origin, disabled catalogue
and expected non-login web user, and refuses an active build or evidence of a
later installation phase (runtime, web unit, credential store or prepared output).
It archives the previous package under its installed digest before replacing
the package, retaining the failed `venv` and all configuration/state. The same
isolated dependency job then produces a separate candidate for acquisition.
It does not recreate the user, delete directories or enable commands. This is
not a general upgrade or an automatic retry mechanism.

Offline tests cover code preservation, recovery rejection and actual venv/pip
bootstrap in `/tmp`; the pip package download is simulated in that test. The
corrected worker must still pass the live DynamicUser job before claiming that
this failure is resolved.

## Verify before publishing

Check `placet-admin-web.service`, the socket permissions, and request `/api/status`
over the Unix socket using `Host: localhost`, `X-Forwarded-Host` set to the
configured Service hostname, and `X-Forwarded-Proto: https`. Tailscale 1.102.4
rewrites Host for Unix targets and overwrites these forwarded headers. The result
must show `execution_enabled: false`. Confirm that the desktop UID and backend
UID cannot connect. Then use only the dedicated Tailscale socket to advertise
`svc:placet-admin`, forwarding HTTPS443 to `unix:/run/placet-admin-web/http.sock`,
and approve that specific tagged host in the Tailscale Service console. Verify
access policies against existing broad grants; a new narrow allow rule does not
remove previously granted access.

The web process ignores proxy authentication headers and enables no privileged
actions. Initial enrollment requires a root-created five-minute invitation.
The protected UDS deployment validates the exact forwarded HTTPS hostname;
other deployments do not trust forwarded headers. This requires the socket's
restricted directory and dedicated transport group to remain intact. The page
also enforces the exact Origin on mutations, rejects cross-origin API requests and
embedding, uses no third-party assets and does not cache responses. Invitation
tokens live in the URL fragment and are removed from browser history before API
use; access logging is disabled. Do not send private invitation URLs through
Placet or agents.

## Register from the phone

After protected installation, the root terminal starts the ceremony by invoking
`admin_executor.terminal begin-enrollment` with the protected administrative
interpreter and module path. Open its private URL directly on the phone. The page
requests a passkey with user verification and stages the response. It does not
enroll a credential or enable commands.

Back in the root terminal, `finish-enrollment --id <ceremony-id>` reads that
response through the unprivileged helper and verifies registration before saving
the credential. No registration endpoint can finish enrollment as root. Follow
the same two steps for a recovery credential before relying on remote operation.

Revocation logic is available in the authorization core but an operator-facing
revocation/listing CLI and full recovery workflow remain to be completed.

## Verification and remaining work

### Confirmation broker (implemented, not installed)

The standalone page accepts a public `?proposal=<32-hex-id>` reference. It reads
the protected operation, requests a challenge only on a human click, compares
that challenge with the operation reviewed before the click, and then requests
WebAuthn authentication. Changed operations require a reload and review.
Authorized/running states are polled without repeating the authorization POST;
uncertain outcomes explicitly require terminal reconciliation. Bridge evidence
is labelled as a report from the user service, not an independent guarantee.

The web process uses `/run/placet-admin-control/web.sock`, verifying that its
peer is root. `admin_executor.control` is a separate root entry point, accepts
no arguments and refuses execution from the checkout. The backend socket is
`/run/placet-admin-control/backend.sock`. Each root-owned socket permits only
its configured UID via `SO_PEERCRED`; socket groups alone grant no authority.
The web role has catalogue/view/challenge/authorize methods. The backend role
has catalogue/propose/view methods. Neither role has an execute method.
Requests/replies are limited to 100 KB and incomplete clients time out.

The root broker requires protected `peers.json` (distinct `web` and `backend`
UIDs), `return-url` (a fixed separate HTTPS origin/path), the existing origin and
catalogue, and a private authorization database. These settings and the runtime
directory are deliberately not provisioned by the existing installer. A future
reviewed upgrade must install the units, protect the database permissions,
set socket groups, provide reconciliation and configure restart rate limits
before enabling an action. Do not rerun the pre-service failed-bootstrap recovery
against the active installation. Existing credentials must be preserved.

The activation preflight must reject root-equivalent desktop/agent access,
including a writable rootful Docker socket or retained `docker`/`lxd`/`disk`
group access, and desktop membership in either administrative socket group.
Account membership and running-process supplementary groups both matter.
Rootless Docker or a separate agent account are operator decisions, never an
automatic remediation. Review other virtualization/admin privileges explicitly.
The installer must also provision protected `health.json` with only a validated
backend port (1–65535); the health hostname and `/api/health` path stay fixed.

### Human backend proposal API (implemented, not deployed)

`GET /api/admin/catalogue` returns enabled actions and up to ten recent public
proposal references. If the broker is missing, unreachable or malformed, it
returns `status: not_configured`, no actions and `execution_enabled: false`.
`POST /api/admin/proposals` accepts only `request_key`, a fixed restart action
and empty parameters; its result contains the protected confirmation URL.
`GET /api/admin/proposals/{id}` recovers durable state after backend restart.
All three routes require the human listener/identity; agent and bridge tokens
are refused. No route accepts an assertion or dispatches a service restart.

Preserve the same request key after a timeout/lost acknowledgement. Retrying
creation recovers the original intent; it does not renew the 15-minute lifetime
or authorize it. A different key is a different explicit human intent. Expired
proposals remain readable, show an expiration message and cannot be signed.
The lifetime is checked inside the cryptographic transaction as well as before
challenge generation; authorized jobs remain visible after that lifetime.

Tests cover HTTP origin/host boundaries, invitations, single-use response staging,
actual WebAuthn signatures/registration, privilege checks, runtime copying and
systemd syntax. Chromium at 360/390px was checked in English and Italian,
including language switching and a simulated credential response. This is not
a physical iPhone/Face ID test. The actual system Python runtime relocation was
tested; the privileged DynamicUser installation still needs a live check.

The confirmation endpoints and restart execution core have offline coverage;
they are not installed or operational. Backend integration is implemented but
not deployed. The Commands screen,
root-only reconciliation and supervised activation remain pending, along with
the complete isolated deploy/build path. An enrolled passkey alone grants no
ability to run a command.
