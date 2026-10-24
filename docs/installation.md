# Installing Placet

> **A single point of human supervision and a cross-session communication bus for AI agents.**

Placet lets agents running in different sessions (Claude Code, Gemini CLI /
Antigravity, OpenAI Codex) cooperate by exchanging tasks and prompts, and gives
you a control point on your phone (through Tailscale and Web Push
notifications) to review, edit and approve every prompt before it runs.

---

## Architecture

```text
 ┌────────────────────────────────────────────────────────┐
 │                         Phone                          │
 │  • Web Push notifications linking to the request       │
 │  • PWA (Tailscale HTTPS): decisions, edits, history    │
 └───────────────────────────▲────────────────────────────┘
                             │ REST API / SSE
 ┌───────────────────────────┴────────────────────────────┐
 │                Placet backend (FastAPI)                │
 │  • SQLite: requests, replies, audit log                │
 │  • Routing rules and catalogue from AGENTS.md          │
 └──────────────┬──────────────────────────┬──────────────┘
                │                          │
                ▼                          ▼
     ┌──────────────────────┐   ┌──────────────────────┐
     │  Placet MCP server   │   │  Local bridge daemon │
     │  (stdio, per agent)  │   │  (wakes sessions)    │
     └──────────▲───────────┘   └──────────▲───────────┘
                │                          │
        ┌───────┴───────┐          ┌───────┴───────┐
        ▼               ▼          ▼               ▼
   [agent-claude]  [agent-gemini] [agent-claude]  [agent-gemini]
    Claude Code     Gemini CLI     Claude Code     Gemini CLI
```

The full design is in the [architecture](architecture.md).

---

## Quick start

### 1. Prerequisites
- Linux (macOS and Windows are planned, not supported yet)
- Python 3.11+
- Node.js 22.13+ (required by the frontend dependencies)
- `tmux`, only for the optional tmux channel

### 2. Installation
```bash
# Python virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Build the PWA
cd frontend
npm install
npm run build
cd ..
```

### 3. Environment variables

| Variable | Meaning |
|---|---|
| `PLACET_ALLOWED_USERS` | Tailscale logins allowed to use the PWA, comma separated (e.g. `you@github`). If empty, nobody can use the PWA |
| `PLACET_TOKEN_CLAUDE`, `PLACET_TOKEN_GEMINI`, `PLACET_TOKEN_CODEX` | Agent tokens, interpolated into `AGENTS.md`. An agent without a token cannot authenticate |
| `PLACET_HOST` / `PLACET_PORT` | TCP listener for the agents (default `127.0.0.1:8000`) |
| `PLACET_HUMAN_SOCKET` | Unix socket for the PWA (default `$XDG_RUNTIME_DIR/placet/human.sock`) |
| `PLACET_CORS_ORIGINS` | Origins allowed through CORS, only for a separate dev server (default: none) |
| `PLACET_DB_PATH` | Path of the SQLite database |
| `PLACET_AGENTS_FILE` | Path of `AGENTS.md` (in production `/etc/placet/AGENTS.md`) |
| `PLACET_BRIDGE_TOKEN` | Bridge token, accepted only on the `/api/bridge/*` endpoints |
| `PLACET_API_URL` | For the bridge and the MCP server: the backend address (default `http://127.0.0.1:8000`) |
| `PLACET_CODEX_BIN` | For the bridge: the `codex` binary with the `queue` command (default: the app's own, `/usr/lib/chatgpt/resources/codex`, then the one in `PATH`) |
| `PLACET_CLAUDE_BIN` | For the bridge: the Claude Code CLI binary used for credential renewal (default: `PATH`, then `~/.local/bin/claude`) |
| `PLACET_TRANSCRIPTS_LOCAL` | `1` = the backend reads session conversations itself instead of asking the bridge. Single-process development only (`run_all.py` sets it); never in production, where the backend has no access to the user's files |
| `PLACET_BASE_URL` | PWA address as seen from the phone (e.g. `https://server.tail1234.ts.net`), used in notification links |
| `PLACET_NTFY_URL`, `PLACET_NTFY_TOPIC`, `PLACET_NTFY_TOKEN` | ntfy server, topic and access token. Without URL, topic and `PLACET_BASE_URL`, notifications are off |

Generate tokens with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

### 4. Running Placet (all in one)
```bash
# Starts the backend (agent listener + PWA socket) and the local bridge
.venv/bin/python run_all.py
```

The backend has two entry points:
- **Agents:** `http://127.0.0.1:8000`, authenticated only with a Bearer token. The agent's identity is the token's.
- **PWA (you):** the Unix socket `PLACET_HUMAN_SOCKET`, exposed on the tailnet with:
  ```bash
  tailscale serve --bg unix:$XDG_RUNTIME_DIR/placet/human.sock
  ```
  Tailscale Serve adds the `Tailscale-User-Login` header, accepted **only** on this socket and only for the users in `PLACET_ALLOWED_USERS`.

The PWA is then available at `https://<machine-name>.<tailnet>.ts.net`.

`run_all.py` is the development mode: backend and bridge in the same process, as your user. The agents, which also run as you, could read the database and approve their own requests. For real use, install the services as described below.

### 4b. Installing on the server (production)

```bash
sudo deploy/install.sh
```
The Tailscale login allowed into the PWA is detected from `tailscale status`. If detection fails, pass it explicitly with `--login <login>`, using the real Tailscale login.

The script, which can be run again to update:
- **Code:** builds the PWA and copies the code to `/opt/placet`, owned by root. Agents cannot modify the code that approves requests.
- **Backend:** creates the system user `placet` and installs `placet-backend.service`. The database lives in `/var/lib/placet` and the PWA socket in `/run/placet`, both accessible only to `placet`.
- **Configuration:** creates `/etc/placet/AGENTS.md` (from now on you edit this one, not the copy in the repository) and `/etc/placet/placet.env` with random tokens.
- **Bridge:** installs `placet-bridge.service` as a service of your user. The bridge talks to the backend only over REST, with the token in `~/.config/placet/bridge.env`, and has no access to the database.
- **Agent tokens:** in `~/.config/placet/agent-tokens.env`, to copy into the MCP configurations.

Then, once:
```bash
sudo tailscale serve --bg unix:/run/placet/human.sock
```

**What this protects and what it does not.** An agent cannot approve requests, because approvals only come from the socket reserved for tailscaled. Nor can it modify code, routing rules or the database. However, the agents all run as you, so they can read each other's tokens: tokens tell them apart in the audit log, they do not protect them from each other. Do not give your user passwordless sudo: it would cancel the isolation.

> **Known limit.** `deploy/install.sh` itself, and files it uses such as `requirements.txt`, live in the repository, which agents can modify; running it with `sudo` runs that content as root. Review changes before deploying. A fixed, root-owned deploy script outside the repository is planned: see [reserved commands](plans/reserved-commands.md).

### 5. Frontend development

The Vite dev server forwards `/api` to the PWA socket and adds the identity header that `tailscale serve` adds in production:
```bash
cd frontend
PLACET_DEV_LOGIN=you@github npx vite
```
`PLACET_DEV_LOGIN` must be listed in `PLACET_ALLOWED_USERS`. The dev server listens on `127.0.0.1` only.

### 6. Optional: push notifications with self-hosted ntfy

Web Push (next section) needs no extra server and is the recommended channel; set up ntfy only for devices without Web Push.

A notification announces the request and opens the PWA. It has no Approve/Reject buttons: decisions are made only in the PWA, with the full text in view. Prompt and context are included in the notification only if the ntfy server is private; with the public `ntfy.sh` the notification only says who wrote.

1. Start ntfy on the server, reachable only locally:
   ```bash
   docker run -d --name ntfy --restart unless-stopped \
     -p 127.0.0.1:2586:80 -v ntfy-cache:/var/cache/ntfy -v ntfy-etc:/etc/ntfy \
     -e NTFY_BASE_URL=https://server.tail1234.ts.net:8443 \
     -e NTFY_UPSTREAM_BASE_URL=https://ntfy.sh \
     -e NTFY_AUTH_FILE=/etc/ntfy/user.db -e NTFY_AUTH_DEFAULT_ACCESS=deny-all \
     binwiederhier/ntfy serve
   ```
   `NTFY_UPSTREAM_BASE_URL` enables instant notifications on iOS: Apple delivers the push through ntfy.sh, which only receives a message ID with no content. The app on the phone then downloads the message from your server over Tailscale.
2. Create a user and a token for Placet:
   ```bash
   docker exec -it ntfy ntfy user add --role=admin <user>
   docker exec -it ntfy ntfy token add <user>
   ```
3. Expose ntfy on the tailnet on an HTTPS port separate from the PWA's:
   ```bash
   tailscale serve --bg --https=8443 http://127.0.0.1:2586
   ```
4. In the ntfy app on iOS add the server `https://server.tail1234.ts.net:8443` with your user and subscribe to the topic.
5. Set `PLACET_NTFY_URL=https://server.tail1234.ts.net:8443`, `PLACET_NTFY_TOPIC`, `PLACET_NTFY_TOKEN` and `PLACET_BASE_URL`.

### 7. Notifications that open the PWA (Web Push)

On iPhone, tapping an ntfy notification opens Safari, not the installed PWA: iOS does not let another app open a Home Screen web app. Placet can therefore notify the PWA itself through Web Push (iOS 16.4 or later, Android, desktop browsers); tapping such a notification opens Placet on the request.

1. Add Placet to the Home Screen (Share → Add to Home Screen) and open it from there.
2. Tap the bell in the app bar, then **Enable notifications** and allow them.
3. **Send a test** from the same dialog.

Nothing to configure on the server: the VAPID keys are generated on first use and kept in Placet's database. `PLACET_BASE_URL` is used as the contact Apple requires. Messages pass through the browser vendor's push service (Apple, Google, Mozilla), encrypted end to end, and carry only a short title, never the text of a request. The backend sends only to those push services. ntfy keeps working alongside, for devices without Web Push.

---

## Agent configuration

### 1. Start the tmux sessions (optional channel)
Create tmux sessions with the names defined in `AGENTS.md`. `PLACET_HOOK_AGENT` tells the hooks (see below) which agent the session belongs to:
```bash
tmux new -s agent-claude -e PLACET_HOOK_AGENT=claude claude
tmux new -s agent-gemini gemini
```

### 1a. Desktop app and CLI sessions (hook channel, recommended)

Claude Code sessions, from the desktop app (Code tab) and from the CLI, connect to Placet through hooks, without tmux:
- **Registration:** every session registers at startup. In the PWA, "Add / see all" lists each provider's open sessions with project, title and state (working, waiting, suspended, unreachable): the ones you tick appear on the home screen, and tapping them shows the conversation.
- **Outcomes to the right session:** request outcomes go back to the session that made the request.
- **Wakeup:** a background listener (`hooks/placet_waiter.py`, `asyncRewake` hook) waits on the session's mailbox and wakes it when an outcome or a prompt from another agent arrives. While the session is working, messages wait in the queue.
- **Visibility:** the desktop app does not show the wakeup text, so Placet asks the agent to first report who is writing and what they ask.

**Present but silent.** With the hooks installed, every session registers with Placet but stays silent: it only reports when a turn starts and ends, writes nothing into the conversation, and its last reply is recorded only if the turn was a Placet request. A session "activates" when a Placet message reaches it (from you or another session) and goes back to silent at the end of the turn. In the desktop app you work as usual. To exclude a project entirely, create a `.placet-off` file in its folder: it also covers subfolders.

To install the hooks in every session of every provider (Claude, Codex, Antigravity) use the installer. It touches only Placet's entries, backs up every file before changing it, and can be run again safely:
```bash
python3 /opt/placet/hooks/install.py
```
- **Claude Code** (`~/.claude/settings.json`): registration, turn state, implicit reply, "Now" delivery and the waiter that wakes the session.
- **Codex** (`~/.codex/hooks.json`): the same script (`claude_code.py --provider codex`), because Codex passes hooks the same fields as Claude. Codex has no asynchronous hooks: the bridge wakes its sessions with `codex queue`. The hooks must be approved once: open `codex` in a terminal and accept them with `/hooks`.
- **Antigravity 2.0** (`~/.gemini/config/hooks.json`): `antigravity.py`. At the end of a turn the last reply is read from the conversation file. The bridge wakes its sessions through the app's internal API.

`--dry-run` shows what would change, `--uninstall` removes only Placet's entries, `--providers claude,codex` picks the providers.

Placet's MCP tools are also needed in every session:
```bash
claude mcp add placet --scope user -e PYTHONPATH=/opt/placet -e PLACET_API_URL=http://127.0.0.1:8000 -e PLACET_AGENT_ID=claude -e PLACET_API_TOKEN=<PLACET_TOKEN_CLAUDE> -- /opt/placet/.venv/bin/python -m mcp_server.server
```

The tmux channel (below) remains available, for CLIs without hooks and for tmux sessions without an active listener.

**Writing to a session from the phone.** At the bottom of a session's conversation there is a field to write to it. The message needs no approval. It arrives through the session's channel:
- **Claude:** the waiter;
- **Codex:** `codex queue`, with the `codex` shipped with the app (from 0.159; otherwise set the path with `PLACET_CODEX_BIN`);
- **Antigravity:** the app's language-server API. It is undocumented: if an update changes it, the bridge logs it with the app version and the message keeps waiting.

If the session is working, the message waits for the end of the turn. In the conversation it appears as yours, marked "from the phone".

**Suspended sessions.** The Codex and Claude desktop apps unload idle conversations. Placet then shows the session as suspended and, when a message is waiting for it, reopens the conversation through the app's own link (`codex://threads/<id>`, `claude://code/continue?session=<id>`); the app may come to the front on the PC. Details: [Codex](providers/codex-wake.md), [Claude](providers/claude-wake.md).

### 1b. Claude Code hooks: state, acknowledgement, implicit reply

Instead of inferring everything from the screen, Claude Code reports its own events to Placet:
- **`UserPromptSubmit`**: the agent starts a turn. If the prompt is a Placet request, receipt is acknowledged at once, without depending on the model.
- **`Stop`**: the turn is over. If the turn was a Placet request and the model did not call `placet_reply`, its last message is recorded as an **implicit** reply, marked as such in the PWA and in the message to the sender.

The installer above configures these. For reference, a minimal manual setup in `~/.claude/settings.json`:
```json
{
  "hooks": {
    "UserPromptSubmit": [
      {"hooks": [{"type": "command", "command": "python3 /opt/placet/hooks/claude_code.py", "timeout": 5}]}
    ],
    "Stop": [
      {"hooks": [{"type": "command", "command": "python3 /opt/placet/hooks/claude_code.py", "timeout": 5}]}
    ]
  }
}
```
The script acts only in sessions started with `PLACET_HOOK_AGENT` and outside projects excluded with `.placet-off`. It never prints anything and always exits without errors. It reads its token from `~/.config/placet/agent-tokens.env`.

**Acknowledgement (`ack_mode` in `AGENTS.md`):**
- **`mcp`** (default): the injected message asks the model to call `placet_ack` before starting. With hooks active the acknowledgement comes from the hook anyway, and the model's call becomes harmless.
- **`hook`**: no instruction in the message; the acknowledgement comes only from the hook. Use it once hooks are verified to work.
- **`none`**: a successful send counts as received (previous behavior).

For tmux delivery, missing acknowledgement within 90 seconds permits up to 3 attempts, then `DELIVERY_FAILED`. Waiter/app delivery without confirmation becomes uncertain and is not automatically resent; the user chooses Retry or Cancel. If the agent acknowledges but does not reply within `execution_timeout_seconds` (default 30 minutes), the request becomes `STALLED` and you are notified too. A timeout warns; it does not free the session queue. Technical outcomes can return without response content; collaboration reply text requires separate human approval unless automatic return was explicitly allowed for that request.

The bridge does not write into a tmux session if:
- the agent has declared a turn in progress;
- a key was pressed in the last 15 seconds (you are typing);
- the screen does not show the empty prompt. This check applies even when the hook says "free", because a menu might be open.

### 2. Calibrate prompt detection (tmux channel)

The bridge writes into a session only if:
- the agent is idle on an **empty** input prompt;
- the screen shows no work-in-progress indicator (e.g. "esc to interrupt");
- the screen does not change between two close readings.

In every other case it waits. The most delicate case is an open menu or dialog: the final Enter would confirm an option.

The empty-prompt patterns are in `idle_patterns` in `AGENTS.md`, and the ones provided must be checked against your version of the TUIs. Start the agent in its tmux session and check how the bridge sees it:
```bash
.venv/bin/python -m bridge.calibrate claude --watch
```
Try four situations: agent idle, agent working, while you type, and with a menu open. Only the first must be reported as free.

### 3. Configure the Placet MCP server
Add Placet to your client's MCP configuration (e.g. `claude_desktop_config.json`, `.claude.json` or the Antigravity configuration):

```json
{
  "mcpServers": {
    "placet": {
      "command": "/opt/placet/.venv/bin/python",
      "args": ["-m", "mcp_server.server"],
      "env": {
        "PYTHONPATH": "/opt/placet",
        "PLACET_API_URL": "http://127.0.0.1:8000",
        "PLACET_AGENT_ID": "claude",
        "PLACET_API_TOKEN": "<value of PLACET_TOKEN_CLAUDE>"
      }
    }
  }
}
```

`PLACET_AGENT_ID` is optional: the identity comes from the token, and if the ID is set the backend checks that it matches.

Once the hooks are installed, Placet checks at each session start whether the client's configuration lists a `placet` server (Claude Code: `~/.claude.json` and `.mcp.json`; Codex: `~/.codex/config.toml`; Antigravity: `~/.gemini/config/mcp_config.json`, not `~/.gemini/antigravity/`). A session where none is found shows "Placet MCP non rilevato" in the PWA (the same warning appears when a Placet turn ends without any Placet tool call having been seen); check the client's MCP configuration and its available tools, add the entry above if it is missing and start a new session. A server provided by a plugin or managed settings is not looked for and can trigger a false warning.

---

## Available MCP tools

1. `placet_ask_human(question, options, multiple, context, timeout_seconds)` (`context` optional)
   - Asks the human for a decision or a choice on the phone.
   - Without `options` it is a confirmation question (approve/reject) with an optional note. With `options` it is a single choice, or a multiple one with `multiple=True`. The free-text "Other" option is always available and must not be passed among the options. The server waits at least 60 seconds for questions to the human.
2. `placet_send_to_agent(target_node | target_agent, prompt, relation_type, rule_acknowledged, session_id, timeout_seconds)`
   - Sends a task to another session. `target_node` (with `relation_type` when several types connect the two nodes) uses the relations the user created; `target_agent` (optionally with `target_session`) is an ad hoc request, always reviewed. `prompt_file` can supply a Markdown prompt. The result reports `delivery_policy` (`automatic` or `manual`). `session_id` is the caller's own session: pass it so the backend knows which node is sending when the agent has several busy sessions; a `target_node` send without an identifiable sender or matching relation is refused with 409 and sends nothing.
   - On an automatic relation, when the user has set a general rule, the first call sends nothing and returns `RULE_ACKNOWLEDGEMENT_REQUIRED` with the rule; the agent sends again with `rule_acknowledged=true` only if the rule does not tell it to stop or ask first. This happens on every automatic send.
   - The tool no longer takes `context` (RF-05b): the reviewer can read the sender's chat.
3. `placet_list_relations(session_id)`
   - Lists the relations the calling session can use: target node, type and description, approval, reply policy, and the general rule.
4. `placet_ack(request_id)`
   - Acknowledges a request injected into the session (with `ack_mode: mcp`).
5. `placet_reply(request_id, response)`
   - Returns the reply once the task is done. A request is one task with one concluding reply. While a session runs a request, a new request from it back to that request's sender is refused (409 `request.reply_expected`, nothing sent): the answer goes through `placet_reply`, progress through `placet_progress`, decisions through `placet_ask_human`.
   - `placet_project_view(project_id, session_token, cursor?)` is the read-only coordinator view of RFC-17: the project's nodes, relations and open requests as metadata (never message text). The user must have switched "Vista coordinatore" on for the caller's node in the project page, and the call needs the newest Placet session credential of the caller's context (switch "Chiave di sessione verificata" on in Settings → Sessioni; `PLACET_SESSION_CREDENTIALS=1` or `0` overrides it; see [session binding](plans/session-binding.md)); without it, or with a revoked grant, the call is refused. It authorizes no send.
   - `placet_progress(request_id, message)` reports progress ("part 1 of 4 done") without concluding: the latest update is shown in the history and returned by `placet_check_status`.
6. `placet_share_file(path, title)`
   - Shows an allowed PDF or image on the phone. Files must satisfy the home, hidden-folder, ownership, type, size and exclusion checks in the architecture.
7. `placet_list_agents()`
   - Lists the active agents, their roles and routing rules.
8. `placet_check_status(request_id)`
   - Checks the state of a request still pending after the timeout.

`timeout_seconds` is only how long the tool waits (5–600 s for agent requests; at least 60 s for human questions). The request stays valid until it expires (`expires_at`, default 24 hours), so you can decide even after the tool has returned `PENDING`.

If the tool's wait ends before the outcome, the agent ends its turn. When the outcome arrives (your decision, an authorized collaboration reply, a rejection or an expiry), it is delivered to the sending session through its channel, in a message starting with `[Placet]`. Each outcome is delivered only once, and outcomes older than 6 hours are ignored.

---

## Reserved administrative commands

The reserved-command implementation is under review and is **not enabled** by
the ordinary installer. The existing passkey enrollment page does not itself
enable command execution. See the [administrative setup](plans/admin-web-setup.md)
and [trust boundaries](plans/reserved-commands.md#trust-boundaries) before any
activation. A desktop/agent account with rootful Docker or other root-equivalent
access cannot satisfy those boundaries; the administrative activation installer
must reject that configuration. No automatic group/container changes are made.

The PWA entry is **Settings → Commands**. Run prepares a fixed-action proposal;
then a separate confirmation link displays its host and opens Placet Admin on
an explicit tap. Passkey authorization happens there. Only backend/bridge
restart actions are candidates; deploy and arbitrary shell commands are absent.
An expired proposal requires a new explicit proposal. A lost creation response
must instead be retried using its saved reference. The PWA retains that reference
across reloads and never retries execution automatically.

During a backend restart, temporary disconnection/503s are expected: the screen
checks saved state again with backoff. An uncertain outcome blocks further
actions and requires a human terminal reconciliation. The displayed terminal
contract will be installed in the administrative activation step; it must not
be treated as operational before that separately reviewed upgrade. Ordinary
Placet deploys must not replace the protected administrative package.

## Running the tests

```bash
npm --prefix frontend test                      # PWA
npm --prefix frontend run build                 # needed by the API integration tests
.venv/bin/python -m pytest -q                    # backend, bridge, hooks
```
The suites cover configuration and `AGENTS.md` parsing, the SQLite database and state transitions, the REST API, SSE and notifications, the bridge and its provider channels, the hooks, the MCP tools and the PWA.

## Activating the two fixed restart commands

This is a **separate root-reviewed upgrade**, not `deploy/install.sh`. It applies
only to the existing enrollment-only administrative installation with all actions
disabled, an enrolled passkey and the protected dependency runtime already built.
It preserves the administrative origin, enrollment database and Tailscale setup.
It installs no dependencies and changes no account, group, sudoers or policy.
Do not run it until its pull request and digests have been reviewed.

Before running the installer, inspect the actual desktop/agent account's groups
(replace `paolo` with that account if different):

```bash
id -nG paolo
```

Both `docker` and `libvirt` independently block the current preflight. For
Docker, choose rootless Docker or remove desktop access to the rootful daemon
and use `sudo docker` only from a trusted administrative session. For libvirt,
remove membership or design a dedicated restrictive policy for separate review.
The current installer treats libvirt as unassessed and always rejects that
membership; retaining it requires reviewed policy and matching preflight support,
not a bypass flag. Group changes and their impact on existing workloads remain
a human decision. The installer also checks retained groups in live processes,
which `id -nG` alone does not show.

First resolve desktop root-equivalent access. Membership in `docker`, `lxd`,
`lxc` or `disk`, privileged daemon socket access, retained groups in running
processes, unattended sudo or unassessed virtualization administration blocks
activation **before changes**. `kvm` alone is not classified as root access.
Socket peer groups must have no supplementary members and must be distinct
from the desktop group. Existing ACLs on privileged daemon sockets require review.
The check is conservative, not a proof that arbitrary local privilege paths do
not exist. Previously compromised root state cannot be repaired by removing a
group. Use a trusted machine and review any other agent identities separately.

Human choices include rootless Docker, root-managed containers without desktop
access to the daemon, or a genuinely separate unprivileged agent account. Merely
removing a group in account settings is insufficient: existing processes retain
it until a complete logout/restart. The installer never makes this choice for you.

**Password sudo does not fully isolate a shared UID.** Agents can modify shell
startup files, aliases, wrappers or PATH, or fake a password prompt, to capture
a password later typed by the operator. Perform administrative authentication
from a session outside agent control: a trusted TTY/console with a trusted shell
environment, a separate operator account, or with agents on a distinct
unprivileged account. A second terminal that reads the same agent-writable shell
configuration does not solve this. Passkeys and a passing preflight assume these
administrator credentials remain outside agent reach; see the
[trust boundaries](plans/reserved-commands.md#trust-boundaries).

Read the installer, then run its read-only preview from the checkout:

```bash
python3 -I admin_executor/activation_bootstrap.py \
  --desktop-user paolo \
  --return-url https://YOUR-PLACET-HOST.ts.net/ \
  --health-port 8000
sha256sum admin_executor/activation_bootstrap.py
```

Use the actual backend loopback port from the protected installation; the port
is written to root-owned `health.json`, never accepted from a proposal. Replace
the example PWA host with the existing PWA origin, **not** the admin origin.
The preview prints `bundle_sha256` and `installed_sha256` (which includes the
previous Host-header fix). If it cannot read the protected installation, run
only this preview as a human from a root terminal. Review both exact digests.

For applying, use the reviewed standalone source digest as well as both bundle
pins. The following is a template, **not a command with usable digests**:

```bash
sudo /usr/bin/python3 -I -c 'import hashlib,pathlib; p=pathlib.Path("admin_executor/activation_bootstrap.py").resolve(); s=p.read_bytes(); assert hashlib.sha256(s).hexdigest()=="REVIEWED_INSTALLER_SHA256"; exec(compile(s,str(p),"exec"),{"__name__":"__main__","__file__":str(p)})' \
  --desktop-user paolo \
  --return-url https://YOUR-PLACET-HOST.ts.net/ \
  --health-port 8000 \
  --bundle-sha256 REVIEWED_BUNDLE_SHA256 \
  --installed-sha256 REVIEWED_INSTALLED_SHA256 \
  --enable-restarts --apply
```

The upgrade stops only the administrative web service, retains the old protected
package as `admin_executor.before-activation-<installed_digest>`, takes a
root-private SQLite backup `activation-before.sqlite`, tightens the live database
to 0600, writes fixed configuration and installs broker/supervisor units. Only
after starting them and repeating the access checks does it enable the two
restart actions. `deploy` remains disabled. There is no restart job created by
installation. The supervisor has only SETUID/SETGID capabilities for the dropped
bridge helper; the broker has CHOWN only for peer sockets. Neither has DAC override.
The broker runtime is root-owned 0711; sockets are root:peer-group 0660, with
SO_PEERCRED checks in addition. No general root access to the user bus is granted.
The bridge helper contacts that bus only after dropping to the desktop identity.

An interrupted upgrade refuses automatic rerun. Keep actions disabled and ask for
an inspection of the retained package, backup, units and live database; do not
restore a stale authorization database or repeat an uncertain job. Rollback is a
human-reviewed procedure, not an automatic rewrite of passkeys/authorizations.
Do not use the old pre-service `--resume-failed-bootstrap` recovery here.

### iPhone acceptance check

1. Open the installed PWA, **Settings → Commands**, and prepare `restart-backend`.
2. Tap the separate confirmation link after checking its admin hostname. The
   protected page must show the fixed backend action; authenticate with Face ID.
3. Return manually to the installed Placet app. A browser return link opens
   Safari and cannot reopen the installed PWA. Temporary reconnection is expected;
   the same job must become succeeded after health/stability checks, without a
   second POST or a second job. Confirm the actual service invocation changed.
4. Repeat for `restart-bridge`. Its result is explicitly **reported by the user
   service**, not trusted root evidence. Confirm sessions reconnect.
5. Verify cancellation, expired proposal, lost connection and reopening the PWA.
   Background/hidden command screens must not poll. Never approve a fake test
   action that would affect unrelated services.

### Reconciling an uncertain outcome

Only after inspecting the real service state, a human uses one of these exact
protected terminal commands. Replace `<job_id>` with the full 32-character job
ID displayed in Commands. No job is rerun; an audit event records the decision.
A live supervisor holds the lock and reconciliation is refused until it releases.

The command requires Python 3.11 or newer: `-P` prevents the working directory
from being prepended to the import path; `env -i`, the single absolute protected
`PYTHONPATH` and `-s` remain required. The installed protected interpreter was
checked with `-I -P` (Python 3.14.4). Until the isolated administrative launcher
is available, also change to `/` in the trusted operator terminal before running
these commands. The command itself must remain safe from any working directory;
changing directories is an additional operational precaution, not the fix.

Do not remove `-P`: importing an agent-writable checkout can execute its package
initializers before the terminal's protected-installation refusal is reached.
Do not replace these flags with `-I` alone: isolated mode ignores `PYTHONPATH`,
and the administrative package may then be unavailable. These command/UI changes
do not modify the installed administrative package or require a root upgrade.

```bash
cd /
sudo /usr/bin/env -i PATH=/usr/bin:/bin PYTHONPATH=/usr/local/lib/placet-admin /usr/local/lib/placet-admin-venv/bin/python -P -s -m admin_executor.terminal reconcile --id <job_id> --outcome succeeded
sudo /usr/bin/env -i PATH=/usr/bin:/bin PYTHONPATH=/usr/local/lib/placet-admin /usr/local/lib/placet-admin-venv/bin/python -P -s -m admin_executor.terminal reconcile --id <job_id> --outcome failed
```

Choose the outcome you actually established. Repeating the same reconciliation
is idempotent; changing an already recorded decision is rejected. An agent, PWA,
bridge or backend cannot invoke this terminal operation through the broker.

### Bridge worker maintenance (separate from application deploy)

This step is **not installed by `deploy/install.sh`**. First review the worker
PR and run [probe 4](experiments/2026-10-21-bridge-worker-probe.md) against the
disposable target. Stop on invariant/report failures. Do not use
`activation_bootstrap.py` to update an already activated host.

The new worker retains NNP and has no capabilities. Its supplementary groups
are the exact approved NSS group set, not an empty set: systemd cannot remove
NSS memberships with an empty SupplementaryGroups directive. Review all groups,
including password-protected sudo as a residual shared-UID risk. The installer
refuses root-equivalent/unassessed groups and accessible privileged sockets;
it does not change accounts, sudo rules, Tailscale or production app units.

Preview from the reviewed checkout is unprivileged and read-only:

```bash
python3 -I admin_executor/worker_maintenance.py
sha256sum admin_executor/worker_maintenance.py
```

The protected configuration may be unreadable to the desktop user. After
reviewing the full installer and recording its digest, the operator copies it
without executing checkout Python as root:

```bash
/usr/bin/sudo /usr/bin/install -d -o root -g root -m 0755 /run/placet-reviewed-probes
/usr/bin/sudo /usr/bin/install -o root -g root -m 0644 admin_executor/worker_maintenance.py /run/placet-reviewed-probes/worker_maintenance.py
sha256sum /run/placet-reviewed-probes/worker_maintenance.py
```

Require the copied digest to match the reviewed commit. From a trusted terminal
independent of agents, preview the protected state with the copied installer:

```bash
/usr/bin/sudo /usr/bin/python3 -I /run/placet-reviewed-probes/worker_maintenance.py --source /home/paolo/Projects/placet/admin_executor
```

Review the four full SHA-256 values (installer, new bundle, installed bundle,
new worker unit), UID/GID, and expected group IDs. Replace each placeholder
below with that reviewed value; GROUPS is the sorted comma-separated numeric
list, not a shell variable. Neither preview executes the source bundle: its
manifest and literal unit template are parsed as data.

```bash
/usr/bin/sudo /usr/bin/python3 -I /run/placet-reviewed-probes/worker_maintenance.py \
  --source /home/paolo/Projects/placet/admin_executor \
  --apply --installer-sha256 INSTALLER_SHA256 \
  --bundle-sha256 NEW_BUNDLE_SHA256 --installed-sha256 INSTALLED_BUNDLE_SHA256 \
  --unit-sha256 WORKER_UNIT_SHA256 --expected-groups GROUPS
```

No restart job may be pending, running, or uncertain. Let authorized work finish
first; reconcile uncertain outcomes only after inspecting effects. Do not delete
rows or replay a job to unblock maintenance. The installer takes the execution
lock, stops the administrative authorization writers and supervisor, rechecks
for authorizations that arrived during freeze, and refuses an untracked/live
worker. It preserves the live authorization database, passkeys, jobs, audit,
catalogue, protected identities and network configuration. It updates only
allowlisted worker-related package files/locales and the single worker unit;
control/supervisor/web unit definitions are left unchanged. It does not start
or enable the worker, nor restart the backend or user bridge as a test.

Backups: `/usr/local/lib/placet-admin/admin_executor.before-worker-<installed_digest>`
and root-private `/var/lib/placet-admin/worker-maintenance-<installed_digest>/`.
On a failure after mutation, administrative services stay stopped for explicit
operator recovery; the record says `frozen` until successful completion. Do not
rerun blindly or restore an older authorization DB: restoring spent challenges
or jobs can authorize replays. Inspect backups, unit, policy and installed bundle
with a reviewer, repair the protected code/configuration deliberately, and keep
the live database. A no-mutation failure restores the administrative services.
The exact same installed digest is deliberately not applied twice automatically.

After a successful install, use the PWA to authorize **one** `restart-bridge`
through Face ID. Check that the bridge InvocationID changes and remains active,
and the result is explicitly user-service evidence. A worker report is not an
independent root health check. Test `restart-backend` separately for regression.
If anything is uncertain, the queue remains blocked until terminal inspection
and reconciliation. Installer success alone never proves a real restart works.

The bridge worker now synchronizes STATUS with an sd_notify barrier (three
seconds maximum). If it cannot confirm notification processing it exits with
an error; the job remains uncertain and is never automatically repeated. Its
60-second service timeout is shorter than the supervisor's 75-second command
limit. Always use the digest from the current reviewed probe/installer: earlier
review digests do not authorize the updated script or unit.

The worker maintenance scope includes the two administrative confirmation-page
files (`web_static/app.js`, `web_static/index.html`) needed to display the closed
restart diagnostics, as well as their locale catalogs. This permits upgrading
from the activation bundle at commit `69330af`, whose installed digest remains
`580f9b4d63ab971b7059e1f5bc96e4d0bb78d196cf93579521ba8c04044324cb`.
The regression fixture reconstructs that exact public bundle without requiring
Git history in CI. `bridge_probe.py` is operator-only and is not in the installed
manifest; a Git diff of the entire directory does not describe bundle contents.
Changes to unrelated modules, removals, ownership, pins and job-state guards
remain rejected. A new installer/bundle digest is required for this scope fix;
the worker unit and installed bundle digests are not changed by a refused apply.
