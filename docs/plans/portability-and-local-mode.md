# Portability (macOS, Windows) and local mode without Tailscale

Status: **study**, no code. Requested by the maintainer on October 22, 2026, at
the end of the priority list: friends asked for macOS and Windows, and a local
mode lets people try Placet without setting up Tailscale.

## 1. Local mode (no Tailscale)

### What changes

Today the PWA reaches the backend only through `tailscale serve`, which proxies
to the human listener (a Unix socket) and adds `Tailscale-User-Login`; that
header is the human's identity (`backend/app/auth.py`, `backend/app/serve.py`).
Agents use a separate loopback TCP listener with Bearer tokens.

Local mode adds a **third way in for the human only**: a loopback HTTP
listener (`127.0.0.1`, its own port) serving the PWA and the human API to a
browser on the same PC.

- `localhost` is a secure context: service worker, PWA install and Web Push
  work in desktop browsers without certificates.
- The phone cannot reach it. That is the point of the mode (try it on the PC);
  phone access stays Tailscale (or, later, LAN with HTTPS — out of scope here).

### Security: the real work

Without Tailscale there is no identity, and **writing into a session is remote
control of the PC** (RS-05). On loopback, every local process and every web
page open in the browser can send requests to `127.0.0.1`.

Trust model (MVP): **a personal PC with trusted local accounts and
services.** Local mode does not promise isolation from other local users or
hostile local services: cookies are shared across ports of the same host
([RFC 6265 §8.5](https://www.rfc-editor.org/rfc/rfc6265#section-8.5)),
`HttpOnly` stops page scripts but not the server that receives the cookie,
and a native process that obtains it can forge `Host`/`Origin`. Browser
extensions allowed on the site and same-UID code stay inside the trusted
perimeter.

Required:

1. **Mode chosen explicitly at install: local-only or Tailscale-only.** No
   coexistence in the MVP; coexistence later, with tests separating
   principals and capabilities. Installing local mode never changes an
   existing Tailscale configuration.
2. **Listener-bound identity and capabilities, enforced server-side.** Each
   listener has its own authentication source: the local cookie is accepted
   only on the local listener; `Tailscale-User-Login` is trusted only on the
   protected Unix-socket transport; agent Bearer tokens are never promoted to
   a human. Identity is never inferred from `Host`, loopback IP or
   `X-Forwarded-*`. `require_human` today separates human from agent, not the
   channel's capabilities (`backend/app/auth.py`,
   `backend/app/admin_commands.py`): reserved commands must be refused
   server-side for `human:local`, even when the administrative service is
   installed, not merely hidden in the PWA.
3. **Pairing and session lifecycle.** A pairing link with at least 128 random
   bits, short expiry, atomic single use, attempt limits; never in access
   logs, query strings, referrers or telemetry (a URL fragment exchanged via
   POST and removed from the URL at once). Opaque, host-only cookie, rotated
   at login; logout and revocation; session expiry. No credentials in
   `localStorage`. Origin checked on pairing/login/logout too, rejecting a
   missing or `null` Origin on browser mutations; no state-changing GET.
4. **One canonical URL and a stable port**, loopback-only bind, explicit IPv6
   handling and port collisions: `localhost` and `127.0.0.1` are different
   origins, so pick one.
5. **Reads, SSE and attachments are authenticated too.** Conversations are
   sensitive without any mutation. Revocation must close or revalidate open
   `/api/events` streams; no tokens in EventSource URLs; connection and queue
   limits. Private responses are `no-store`; the service worker keeps its
   notification-only scope and never caches pairing data or conversations;
   logout clears private UI state.
6. **Strict `Host` check** (exact host and port: DNS rebinding), no CORS, CSP
   as today. `Secure` cookies on `http://localhost` depend on the browser:
   verify per browser rather than generalize.
7. **Reserved commands stay off** in local mode; deploys by hand.
8. Human identity in the audit becomes `human:local`.

Required negative tests: expired or reused pairing, wrong Host and Origin, a
request from another port, the cookie on the wrong listener, a forged
Tailscale header on the local listener, local access to the administrative
API refused, revocation with an open SSE stream.

### Effort estimate

Small to medium: a listener and an auth path in the backend, pairing page in
the PWA, an installer option (`--local`), tests for Host/Origin/cookie, docs.
It is also the natural first step for macOS/Windows trials.

## 2. macOS and Windows

### Already portable

Backend (FastAPI + SQLite), PWA, MCP server, most hook logic, the bridge's
HTTP and spool logic: Python and JavaScript without OS calls.

### Linux-specific today

| Area | Linux today | macOS | Windows |
| --- | --- | --- | --- |
| Services | systemd system unit (backend) + user unit (bridge), `deploy/install.sh` | launchd agents (`~/Library/LaunchAgents`) | Task Scheduler at logon, or a service via a wrapper |
| Human listener | Unix socket for `tailscale serve` | works (Unix sockets, Tailscale supports it) | Unix sockets exist but `tailscale serve` target support must be checked; fallback: loopback TCP + local mode rules |
| Spool watcher | inotify via libc `ctypes` (`bridge/spool_forwarder.py`) | polling fallback already exists; FSEvents optional | polling fallback; `ReadDirectoryChangesW` optional |
| File locks | `fcntl` | works | needs `msvcrt`/`portalocker` |
| Antigravity channel | scans `/proc` for `language_server`, its CSRF token and ports (`bridge/channels.py`) | `ps`/`lsof` or `psutil` | `psutil`; process/arg access rules differ |
| Claude desktop wake | `/usr/bin/claude-desktop` and Linux desktop files (`bridge/claude_desktop.py`) | `open -a Claude` / URL scheme — to verify | URL scheme / app path — to verify |
| CLI sessions | tmux (`bridge/tmux_controller.py`) | tmux available (Homebrew) | no tmux: CLI injection unsupported at first (Windows Terminal has no equivalent API); desktop apps only |
| Paths | `~/.config`, `~/.cache`, `/run/user/<uid>` | `~/Library/Application Support`, `~/Library/Caches` | `%APPDATA%`, `%LOCALAPPDATA%` |
| Reserved commands | root services, systemd, NNP worker | would need a new privileged design (launchd daemons) | would need a new design (Windows services, UAC) |

Also Linux-specific (second review, October 22, 2026):

- `bridge/spool_forwarder.py` imports `fcntl` unconditionally: the polling
  fallback alone is not enough on Windows.
- `hooks/placet_waiter.py` uses `fcntl` and `/proc` beyond the Antigravity
  channel: process identity and survival need a per-OS implementation, on
  macOS too (as do the RF-62 pane checks in `bridge/procs.py`).
- The administrative client uses Linux `SO_PEERCRED`
  (`backend/app/admin_commands.py`): with the feature disabled it must not
  break import or startup elsewhere.
- File permissions and protection: Windows ACLs, junctions/reparse points, UNC
  paths, case-insensitive names, rename/delete of open files — not just a
  different cache folder.
- Services: a LaunchAgent or a logon task fits the interactive bridge; a
  Windows service in Session 0 cannot open apps in the user's session.
  Specify backend and bridge separately.
- The macOS Unix socket as a `tailscale serve` target must be verified with
  the chosen Tailscale distribution.
- "Secure context" does not guarantee PWA install and Web Push in every
  desktop browser: keep a real compatibility matrix.
- Better than "desktop apps supported": a per-provider matrix of reading,
  hooks, delivery, wake-up and attachments for each OS.

### Recommended path

1. **Local mode first** (section 1): removes the Tailscale requirement for
   trials on any OS.
2. **macOS next:** closest to Linux (Unix sockets, tmux, fcntl). Work:
   `psutil` for the Antigravity scan, platform paths module, launchd
   installer, Claude desktop wake on macOS, polling spool. Lab checks on the
   maintainer's Mac (32 GB, already planned as a node in the multi-machine
   goal).
3. **Windows last:** desktop-app channels only (no tmux), file locks, Task
   Scheduler installer, paths, `psutil`. Lab work needed for every provider's
   hook and channel on Windows.
4. **Reserved commands:** Linux only for the foreseeable future; macOS/Windows
   ship without them (as in local mode).

Cross-cutting refactor that pays off for all three: a small `platform` layer
(paths, process discovery, file lock, service installer, app wake) with the
Linux implementation extracted unchanged first, under the existing tests.

## 3. Decisions after review (October 22, 2026)

1. **Pairing + cookie** is enough for the pilot on a trusted personal PC;
   passkey optional at first, and not presented as a fix for loopback
   isolation.
2. **Exclusive modes** in the MVP (local-only or Tailscale-only);
   coexistence later with separation tests.
3. **Windows without tmux** is acceptable, with unavailable channels declared
   and not breaking startup. Later: ConPTY for CLIs started and owned by
   Placet ([pseudoconsoles](https://learn.microsoft.com/en-us/windows/console/pseudoconsoles)),
   distinct from injecting into an open Windows Terminal tab.
4. **Packaging for friends:** a user-level installer with pinned venv
   dependencies, the prebuilt frontend, a capability diagnostic and a clear
   uninstall; no administrative privileges to try local mode. A bundled app
   after the pilot.

Order: define the local trust model and its negative tests on Linux first;
then extract the minimal OS boundaries; macOS pilot; Windows.
