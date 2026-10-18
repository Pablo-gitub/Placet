# Waking Codex through its native link

The bridge includes suspended Codex sessions among those with mail waiting.
For each request it takes the mail through the backend, runs `codex queue`
once, confirms the acceptance and then invokes:

```
/usr/bin/chatgpt codex://threads/<uuid>
```

Why both steps: `codex queue` only delivers to conversations the app has
loaded, and loading a conversation does not by itself fire `SessionStart`.
With the message already queued, opening the conversation makes the app run
it; that turn's events bring the session back in Placet.

The local test on October 18, 2026 loaded a `notLoaded` chat and produced the
expected reply without a proxy; the full path from the phone through the
bridge was then confirmed. It depends on the user's graphical session and on
the installed app; acceptance of the link does not confirm that the turn
started. It may bring the chat to the front. No headless start is ever run.

An error or timeout of the link after queuing is logged, but it does not undo
the acceptance and causes no resend. The PWA says that execution has not been
observed yet; in that case the user can reopen the chat by hand. RF-29b keeps
blocking the next request. Suspension ends only with the session's events: the
link does not invent execution events. Once accepted into the Codex queue, a
message cannot be guaranteed to be withdrawn.

No proxy and no environment variables: the experiment of interposing between
the app and its app-server was removed from `main` and remains on branch
`experiment/desktop-proxy`.

After updating the code in `/opt/placet`, restarting the user bridge is
enough. Backend, launcher and Codex app need no further changes.

Final check: write a single request from the PWA to a suspended chat, verify
a single turn in the rollout, the reply in the PWA and an empty provider queue.
