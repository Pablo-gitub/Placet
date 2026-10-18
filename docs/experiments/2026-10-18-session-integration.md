# Session integration: state verified on October 18, 2026

## Codex

The active path uses the native binary: queue once, confirm in the backend,
then open `codex://threads/<uuid>` through `/usr/bin/chatgpt` for suspended
sessions. No headless fallback. A successful open command does not certify
that the turn started. An error after queuing does not put the request back
to wait and causes no automatic resend; RF-29b keeps one request in progress
per session.

Real tests without a proxy:
- "Rispondi ok", initially `notLoaded`: a single message, opened through the
  link, reply WAKE-OK-18 and then delivery of the old request #39.
- "Progettista": PWA request #50, queuing and opening logged by the bridge at
  11:06:37, wakeup confirmed by the user.
- Afterwards the user confirmed the full path from the phone through the
  bridge: one turn started and the Codex queue stayed empty.

The provider queue was only read. No guaranteed withdrawal of items already
accepted by Codex has been implemented.

## Shelved experiment

The stdio interposition between the desktop app and its app-server
(`desktop_proxy.py`, `desktop_client.py`, a wrapper through `CODEX_CLI_PATH`)
was removed from `main`: handshake recognition never worked with the real app,
and an intermediary in the channel that runs the agents was a
disproportionate risk. The code remains on branch `experiment/desktop-proxy`.
The native path does not need it.

## Claude

asyncRewake timeout set to seven days, internal 24-hour limit removed; flock
lock and a check of the owning engine process's identity. Old listeners that
have terminated need a restart from a Claude hook. Request #46 was completed
after Claude was restarted/opened: this does not separate the contribution of
the changes from that of the restart. Later the user confirmed delivery after
a long real inactivity (at least 15 minutes), with the conversation reopened
through its native link ([Claude wakeup](../providers/claude-wake.md)).

## Mentions and collaboration

Human mentions share references; they do not send requests. Routing
permissions are checked on the agent's actual request. The registry allows
every provider, including towards sessions of the same provider. The
Claude→Claude, Codex→Codex and Gemini→Gemini rules require human approval.
Delivery to the same session remains forbidden. The migration script also
updates the installed registry, making backups; an ordinary deploy does not
overwrite `/etc/placet/AGENTS.md`. PWA mention #51 was received correctly. Real
Codex→Codex collaboration was verified with request #54, approved and completed
with the reply TEST-COLLAB-OK.

## Adopted choice: references without added operating instructions

The operating text added to mentions made messages hard to read. The paragraph
explaining how to use `placet_send_to_agent` was removed: the user's text and
the JSON references remain, separated by a blank line. Full IDs prevent
confusing sessions with similar titles. The short notice common to user
messages and the attachment metadata remain.

The agent works out from the message whether the user is just citing a session
or asking for a task. Permissions, approvals and routing are applied by Placet
when the request is sent anyway: they do not depend on the removed text.

Real test: #52, a reference to the session "Rispondi ok", received without
creating a request; #53, an explicit task, turned into request #54 to the same
ID and completed with TEST-COLLAB-OK. The result validates these two cases in
Codex; it does not guarantee the interpretation of every ambiguous message nor
is it an equivalent check on the other providers.

## PWA updates and caching

The server sends `Cache-Control: no-store` for the start page, direct access
to `index.html` and the SPA routes. Manifest, icons and unversioned assets
require revalidation; Vite assets with a hash in their name keep the one-year
immutable cache. A new build changes the file names. Missing files under
`/assets` stay 404, without returning HTML in their place.

The rule applies once the client receives the updated response: it cannot
revoke an old page already kept by iPadOS/iOS, nor reload an open page. The
automated HTTP check does not replace actually reopening the PWA on the phone
after an install. The project has no service worker.
