# Verified session binding (RS-09)

Status: **proposal**, not implemented. Prerequisite of the coordinator view
([RFC-17](../requirements.md)): a per-node grant is meaningless while a session
can claim to be another one.

## Problem

An agent authenticates with its **provider token** (`claude`, `codex`,
`gemini`). The session it acts for is **declared**: `placet_send_to_agent` and
`placet_list_relations` take an optional `session_id`, and the backend only
checks that the declared session belongs to the same provider. Without it,
the backend guesses (`created_sender_session`: the only busy session of that
agent). So:

- a Claude session can act as any other Claude session, and therefore use that
  session's node, relations and (with RFC-17) project grants;
- with two busy sessions of the same agent the guess fails, and the agent has
  to pass an ID it may have copied wrongly or taken from another conversation;
- a prompt injected into one session ("use session_id X") is enough to
  impersonate X.

## Threat model and honest limits

All agents run under the same Unix user. Any process of that user can read
another agent's files, transcripts and tokens. **No application-level scheme
is a barrier against a hostile local process**, and this design does not claim
one (see RS-09 in the requirements).

What the binding must stop:

1. **Mistakes:** a wrong or stale `session_id`, a copied ID from another
   conversation, the busy-session guess picking the wrong one.
2. **Cross-session prompt injection:** text that reaches session A (a file, a
   web page, another agent's reply) cannot make A act as session B, because A
   cannot learn B's credential from its own context.
3. **Confused deputies through Placet itself:** no Placet API returns another
   session's credential, ever (not to the PWA, not in `placet_list_agents`).

Out of scope: a malicious local process reading transcripts or the database;
provider apps that do not let Placet put anything into the session's context.

## Proposal: a per-session capability delivered in-context

1. **Issue.** When the backend first registers a session (hook `start`, or the
   first event of a session it did not know), it creates a random capability:
   128 bits, URL-safe, stored **hashed** (SHA-256) in `sessions.binding_hash`
   with `binding_issued_at`. The plaintext exists only in the hook response.
2. **Deliver into the conversation, not into a file.** The hook returns the
   capability to the provider as context the model sees, and only that session
   sees: Claude Code `additionalContext` on `SessionStart`/first
   `UserPromptSubmit` (already used by `hooks/steer.py`); the equivalent
   mechanism for Codex and Antigravity where one exists (see "Providers").
   The text says what it is: "Placet session credential for this conversation
   only; pass it as `session_token`; never repeat it elsewhere".
3. **Present.** MCP tools gain `session_token` (replacing, in time,
   `session_id`). The backend hashes it, looks the session up, and requires it
   to belong to the authenticated provider. The session is then **proven**,
   not declared.
4. **Grade.** Each request records `sender_binding`: `verified` (token),
   `declared` (legacy `session_id`), `guessed` (busy-session fallback). The
   PWA and the audit show it. Features that grant power per node (RFC-17
   grants, automatic relations once enforcement is on) require `verified`.
5. **Rotate and revoke.** A new capability on `/clear` and on continuation
   (RF-29d: the old session's hash is cleared, the new session gets its own;
   the node moves only on a verified continuation). The user can revoke a
   session's binding from the PWA (forces re-issue on the next hook event).
   Capabilities never expire on time alone: a long conversation keeps working.

### Why not the alternatives

- **Parent PID / process tree:** the caller reports it, so it proves nothing
  to the backend; also differs between desktop apps and CLIs.
- **A per-session file read by the MCP server:** the MCP server does not know
  which session it serves (one stdio server per app, sometimes shared), and a
  file is readable by every agent, including through tool calls driven by an
  injected prompt.
- **Per-session provider tokens in `AGENTS.md`:** sessions are created by the
  apps at will; static secrets cannot follow them.

## Providers (to verify in the lab before coding)

| Provider | Delivery channel for the capability | Status |
| --- | --- | --- |
| Claude Code (CLI and desktop) | hook `additionalContext` on `SessionStart` / `UserPromptSubmit` | used today by `steer.py`; verify on desktop after compaction/continuation |
| Codex | hook output or the delivered prompt preamble for Placet-started turns | to verify; fallback: `declared` only |
| Antigravity | hook response / prompt preamble | to verify; fallback: `declared` only |

A provider without an in-context channel stays `declared`: it keeps working as
today but cannot hold per-node grants.

## Migration

1. Ship issuing, storage, `session_token` and `sender_binding` recording; keep
   accepting `session_id` (`declared`). No behaviour change.
2. PWA shows the binding grade per request and per session.
3. RFC-17 grants require `verified`.
4. Later, with the user's decision: refuse `declared` for automatic relations.

## Tests required

- Session A presenting B's `session_id` is `declared`, never `verified`;
  presenting B's token from A's provider token works only if A really holds it
  (the test proves no API ever returns it).
- Wrong provider + valid token of another provider: refused.
- Token rotation on `/clear` and continuation; old token refused afterwards.
- Revocation from the PWA; re-issue on next hook event.
- No endpoint, SSE event, log line or audit entry contains a plaintext token
  (grep test over responses and logs in the suite).
- Hash comparison in constant time.

## Open questions for review

1. Is in-context delivery acceptable given that the token then lives in the
   provider's transcript on disk (same-UID readable, already out of scope)?
2. Should the first token be issued only after the user confirms a new
   session in the PWA (stronger, but one more step for every conversation)?
3. Codex and Antigravity channels: lab work needed before committing to them.
