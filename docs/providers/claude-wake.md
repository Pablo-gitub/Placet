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
