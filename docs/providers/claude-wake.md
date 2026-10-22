# Reopening Claude Desktop conversations

When deliverable mail exists for an idle Claude Desktop session with no recent
waiter heartbeat, `/api/bridge/work` lists it in `claude_wake_sessions`.
Sessions that are definitively closed or busy, excluded projects and CLI
sessions are left out. `SessionEnd` of desktop sessions means suspension; a
one-time migration also recovers those previously classified as closed. New
requests must be addressed to a precise ID; a generic request to the provider
does not open every conversation. An outcome to return to the sending session
may also require opening it. RF-29b keeps blocking a second request while the
first is in progress.

The bridge reads the local metadata under
`~/.config/Claude/claude-code-sessions/<account>/<org>/local_*.json`, looks for
`cliSessionId` and requires a single match with the same project, not
archived. It does not modify these files. It then invokes, through argv and
without a shell:

```
/usr/bin/claude-desktop claude://code/continue?session=local_<desktop-id>
```

This format was verified in the installed package and in the real test on
"Ciao", but it is not a guaranteed public API. If the mapping is missing,
ambiguous or does not match, no other session is chosen and no headless CLI is
started. An error stays in the logs; the PWA keeps the message waiting and
explains that the conversation must be opened in the app if it does not become
reachable again.

## Delivery and limits

The bridge does not take Claude's mail, does not confirm it and does not
declare a turn started. The waiter launched by Claude through asyncRewake
remains solely responsible for delivery. Opening the link does not guarantee
that the waiter starts: Placet waits for a heartbeat and then for the turn's
real events. The link may bring a window to the front.

## Episodes and states (October 22, 2026)

The launcher's exit code 0 means the app accepted the link, nothing more: the chat
shows, but the engine may not start, so no hook and no waiter, and the message
stays pending. Retrying every five minutes forever (it happened 32 times in a
day) only reopened the chat. The attempts are now budgeted per **wake episode**,
kept in the backend per session (`sessions.wake_state`, `wake_attempts`,
`wake_at`), so they survive a bridge restart, an extra message or a backend delay:

- One automatic open per episode. A second only if the first certainly failed
  before the link was handed to the app (no mapping, launcher not started, link
  refused); two such failures end the episode as `wake_failed`. A launcher that
  timed out may have handed the link over, so it counts as the open.
- After an accepted open, a 60-second window waits for evidence: a `SessionStart`
  event or a fresh waiter heartbeat for that session. None: `wake_unconfirmed`, and
  no further automatic open until the user asks.
- An episode ends when the session becomes reachable or its pending mail is gone;
  the next message starts a fresh one.

The states are kept apart and shown in the session view: `open_requested` (the
launcher accepted the link, for a few seconds), `waiting_for_session` (the window
runs), `wake_unconfirmed` (no evidence), `wake_failed` (the link never reached the
app) and `reachable`. rc=0 is never called "started"; the log keeps "Requested
... opening". The bridge reports each outcome
(`POST /api/bridge/sessions/{id}/wake`, from its loop: waking itself never touches
the mail or the store).

### Why the session may not start: the app's capacity limit (October 22, 2026)

Findings from the app's own log, read only (Claude Desktop 2.9939.4, `~/.config/Claude/logs/main.log`),
not from documentation; the format is not an API and may change with any version:

- The link **does** start the engine when the app has room: on October 19 the log shows
  `[CCD] Session local_<id> warmed successfully in <n>ms` for a link opened by Placet.
- When the app is at its cap of warm sessions it logs, after `[CCD] Warming session local_<id>`,
  `[CliGovernor] at cap=7; would evict local_<victim> (idle Ns) for warm spawn` and then
  `[CliGovernor] at cap; yielding warm spawn`, and the engine never starts. The governor only
  evicts for a warm spawn when the active count equals the cap exactly: once the count is above the
  cap (spawns the user typed may exceed it) a link wake-up is always yielded.
- In this app version the per-session idle timeout is no longer armed (the last `Starting idle
  timeout` for sessions is from September 21), so idle engines are not reaped by it.
- Placet cannot call the app's internal pause, and does not try to evict or close anything.

`bridge/claude_desktop_log.py` is an isolated adapter that, for a few minutes after an accepted open,
reads the tail of that log (a plain file of the user, never a symlink, at most 256 KB, tolerant of
truncation and rotation) and names **one** cause, `warm_capacity_denied`, only when the full sequence
for that very session appears after the attempt: its warm spawn, then `at cap; yielding warm spawn`,
before any `warmed successfully` for it. A `would evict` line alone, another session's refusal, older
lines, an unknown format or an unreadable log give no diagnosis, and a later success, a `SessionStart`
or a waiter heartbeat supersedes it. Only the closed code, the time the app logged it and the attempt
it belongs to reach the backend (`POST /api/bridge/sessions/{id}/wake-cause`): never a line, a path, an
argument or chat content. It is stored with the wake state (`sessions.wake_cause`, `wake_cause_at`) and
the notice adds that Claude Desktop reported its limit on preloaded sessions during this attempt, that
the start is not confirmed and that writing in the chat on the PC starts the session. The cause informs
only: it never changes the delivery or the queue, and never triggers a retry or an eviction.

### Conversations archived in the app

Archiving a conversation in Claude Desktop stops its engine, yet Placet kept showing the session as
waiting. Once a minute the bridge reads the app's own session records
(`~/.config/Claude/claude-code-sessions/<account>/<org>/local_*.json`, field `isArchived`, mapped to the
Placet session by `cliSessionId`) with the safety of `resolve()`: read only, no symlinks, plain files, at
most 2,000 records of at most 1 MB, and a session with several records, an unreadable one or an odd id is
left out. It reports which Placet sessions are archived and which no longer are
(`POST /api/bridge/desktop-state`); the backend keeps `sessions.desktop_archived` for Claude Desktop
sessions only. The PWA says "Archiviata in Claude" in the Home row and the session header instead of the
idle state, and the session view and the wake notice say to restore it in the app, the message staying
waiting. An archived session is never listed for a wake-up (`resolve()` refuses it as well) and "Riprova
apertura" is refused for it; restoring it in the app clears the flag by itself.

### Loaded conversations and idle sessions the app dropped

An idle Claude Desktop session can lose its engine without a `SessionEnd` (the app unloads it), and Placet kept
showing it as waiting. The bridge now also reports which Placet sessions the app really holds loaded:

- Each engine counted above carries `CLAUDE_CODE_HOST_SESSION_ID=local_<uuid>` in its environment.
  `EngineCounter.loaded()` keeps **only** that value, and only if it matches `local_<36 hex/dash>`; the rest of the
  environment (it also holds an OAuth token) is never returned, logged or stored. Several engines with the same
  host id (a temporary query next to the loaded one) collapse into one, and the 10 s age filter still applies.
- `ClaudeDesktopWake.local_to_cli()` maps the host id to the Placet session id (`cliSessionId`) through the same
  hardened record reader as `archived_states()`.
- The list goes out with the existing `POST /api/bridge/desktop-state` as `loaded`. If `/proc` cannot tell, an
  engine has no valid host id, or a host id has no readable record, the field is left out: no claim, nothing is
  suspended on a guess.
- The backend suspends a Claude Desktop session that is idle, has no live waiter, is not archived or ended and is
  missing from two consecutive reports (`suspended_at`, like a `SessionEnd`: it stays listed and its messages
  wait). A report that finds it loaded resets the count and undoes a suspension made this way (not one made by
  the app's own `SessionEnd`); a `start` or `prompt` hook event resets both as well.

### Preventive capacity indicator

The cap is not a setting Placet can read, but the numbers behind it can be counted (Linux only):

- **Cap** (as observed in app 2.9939.4): `max(6, floor(total RAM / 3 GiB))`, from `/proc/meminfo`.
- **Loaded sessions**: the user's own processes whose `/proc/<pid>/environ` has
  `CLAUDE_CODE_ENTRYPOINT=claude-desktop`, whose command line is the bundled engine (`claude…`) with
  `--output-format stream-json`, and that have lived at least 10 seconds (from `/proc/<pid>/stat`).
  The app also spawns short-lived "temporary query" engines several times a minute (verified live); they
  must not count, and a terminal `claude` session has another entrypoint.

`bridge/claude_desktop_engines.py` does the counting (bounded reads, nothing signalled, never raises) and the
bridge reports only the two numbers with the archived state, once a minute; the backend keeps the latest
(`GET /api/claude-desktop/capacity`, human only). The Claude group of Home shows "Claude Desktop: 6/7
sessioni caricate", in warning style at or above the cap with the hint that wake-ups from Placet may not
start and that an inactive conversation can be archived in the app. Numbers older than three minutes are not
shown; elsewhere than Linux nothing is reported.

For `wake_unconfirmed` and `wake_failed` the session view shows one notice (opening
requested but the session did not start listening; the message is still in
Placet; write in the chat on the PC or retry) with the time of the last attempt and
**Riprova apertura** (`POST /api/sessions/{id}/wake`, human only): a new episode, one
automatic open that ignores the bridge's cooldown once. One needs-you push per
episode, never repeated, and not sent (nor consumed) while that kind is off. The
message's delivery is never marked failed: it has not happened.

Every attempt, even a failed one, is spaced at least five minutes apart per
session. The private record in `~/.cache/placet/claude-wake/` uses flock and is
written before opening, so the limit survives a bridge restart. Message
content is not recorded. If the waiter is still reachable, the backend does
not ask for an opening.

An engine still alive but without a waiter might not rerun the hooks on a mere
opening: the session then needs a manual check, possibly a local turn or a
controlled restart when it is not working. No attempt is made to attach a
waiter to an existing engine from outside.

## Prerequisites and checks

- Backend and bridge code updated, services restarted.
- Claude hooks reinstalled with `python3 hooks/install.py --providers claude`:
  deploying the code does not update `~/.claude/settings.json` by itself.
- asyncRewake hook with a seven-day timeout, configuration reread by Claude.
- A graphical session of the same user and the `/usr/bin/claude-desktop`
  launcher.

Manual test passed on October 18, 2026: app restarted, "Ciao" not open, no
engine/waiter for the session; the precise link opened, same CLI ID, reply to
the pending message #57 and a new live waiter after Stop. The message was not
resent. This test preceded the bridge automation. Afterwards the user also
confirmed delivery from the PWA after at least 15 minutes of inactivity.

For a rollback, restore the previous backend/bridge version: the link writes
no history, and messages not withdrawn stay in the Placet queue.

## Opening only when needed

Claude already waiting with a recent heartbeat: no opening. Claude busy: no
opening. Codex already waiting: queue only, no link. Codex suspended: queue
and link ([Codex wakeup](codex-wake.md)). Placet relies on the events it
observes; for Codex the bridge has no reliable query to the private runtime,
so a stale state can still cause an unnecessary opening. For Claude a recent
heartbeat can delay recovery until the TTL expires. The migration only
affects sessions recognized as `claude-desktop`: closed CLI sessions are not
made sendable. Archiving is checked in the app's mapping before opening;
suspension does not imply that an archived or deleted conversation can be
resumed automatically.
