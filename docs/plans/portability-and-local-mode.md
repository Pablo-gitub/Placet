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

Required:

1. **Local login.** First run prints a one-time pairing link with a random
   code (terminal only); the browser exchanges it for a session cookie
   (`HttpOnly`, `SameSite=Strict`, `Secure` not available on http but scoped
   to the loopback origin). Optional passkey enrollment afterwards. No default
   password, no "no auth" switch.
2. **Browser attacks:** strict `Host` check (`localhost`/`127.0.0.1` with the
   exact port: blocks DNS rebinding), `Origin` check on every state-changing
   request (blocks CSRF from other sites), no CORS, CSP as today.
3. **Same-UID processes** can read the cookie store and the database anyway:
   local mode is as strong as the user account, like the rest of Placet
   (RS-09 limits). Document it plainly.
4. **Reserved commands stay off** in local mode (no administrative listener,
   no passkey/Tailscale admin transport). Deploys are done by hand.
5. Human identity in the audit becomes `human:local` instead of a Tailscale
   login.

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

## 3. Open questions for review

1. Is a pairing code + cookie enough for local mode, or should passkey be
   mandatory from the start?
2. Should local mode and Tailscale mode coexist on one install (two human
   listeners), or be exclusive to keep the attack surface small?
3. Windows without tmux: acceptable to support only desktop apps there?
4. Packaging for friends: plain `pip`/script installer, or a bundled app
   (PyInstaller/Briefcase) later?
