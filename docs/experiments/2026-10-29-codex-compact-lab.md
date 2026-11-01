# Codex compaction laboratory — October 29, 2026

Status (updated after follow-up): **genuine desktop compaction verified through
an experimental coordination-bus adapter**, as well as owned app-server and
native CLI control. The initial negative access finding below is retained as
history and superseded by the final follow-up section. Literal queue slash text
still does not compact. No product code or global configuration was changed.

## Boundaries and versions

- App-bundled executable: `/usr/lib/chatgpt/resources/codex`, `codex-cli 0.159.2`.
- PATH executable: `/home/paolo/.local/bin/codex`, `codex-cli 0.145.0`;
  not used for the compaction trials.
- Repository baseline: `f2a3dd7`, branch `lab/codex-compact` in
  `.worktrees/codex-compact`.
- The runtime selected its configured default, `gpt-6-luna`, effort `high`;
  the probe did not override the model.
- Dedicated laboratory threads only, including the existing desktop laboratory
  selected explicitly by the human. No second runtime resumed or wrote an
  existing conversation; no private proxy or GUI automation was used.
- Provider state was written only by Codex itself through its normal interfaces.
  Rollouts were read; a derived prefix was written only into laboratory scratch.
  No credentials or conversation exports are included in this document.

## Dedicated app-server: verified

Launch the app-bundled binary with `app-server --stdio`, complete
`initialize`/`initialized`, then create a fresh thread with `thread/start`
(`approvalPolicy: never`, `sandbox: read-only`, laboratory working directory).
The test prompts explicitly forbid tools and file changes.

The definitive trial used thread `01a188e7-da36-77c2-8ff5-7f939754f0d5`.
`thread/loaded/list` returned only that new thread for this server. The sequence:

1. `turn/start`: request the fixed reply `COMPACT-LAB-SEED-OK` and retain the
   harmless marker `LANTERN-593`. Wait for `turn/completed`.
2. `thread/compact/start` with that same thread ID on that same connection.
3. The immediate response is `{}`: **acceptance, not completion**.
4. Observe `item/started` and `item/completed` for `contextCompaction`, followed
   by `turn/completed` with status `completed`.
5. Start the following turn through the same connection. The reply is
   `LANTERN-593`; the turn completes successfully.
6. Close the server's stdin after all turns complete. It exits normally with
   code zero. No signal is sent.

The first exploratory thread, `01a188e6-8c33-7fb1-a7f1-d8a81ba9bb0a`, also
emitted compaction completion and preserved the marker. Its initial probe reader
combined buffered `readline` and descriptor readiness: a final notification could
remain in the Python buffer. This probe timed out waiting; do not interpret that
client-reader limitation as a provider compaction failure. The definitive reader
uses unbuffered reads and checks its own newline buffer before waiting.

### Reliable completion evidence

Use the matching thread ID, compaction item ID and turn ID. Successful compaction
item completion plus successful turn completion is stronger evidence than an
accepted RPC, an idle-looking window or a hook alone. Handle errors and lost
responses separately; do not automatically repeat an uncertain operation.

The rollout contains one `compacted` record. This is useful as a persistent
completion marker, correlated with a pre-operation file position and the known
session. It does not alone identify which requester caused the compaction.

Global hooks emitted `hook/started` and `hook/completed`, including `sessionStart`
after compaction and before the next user turn. The event's `run.source: user`
means the hook configuration source, **not** SessionStart input `source: compact`.
This probe did not capture the raw hook stdin, so that input field is not claimed
as locally verified.

## Queue slash command: ordinary message, verified

After the successful compaction and follow-up turn, run:

```sh
/usr/lib/chatgpt/resources/codex queue \
  --thread 01a188e7-da36-77c2-8ff5-7f939754f0d5 --message '/compact'
```

The command returns zero and a queue acknowledgement. The owning test runtime
then emits a normal `userMessage`, reasoning and an `agentMessage`; its reply is
`Placet compaction lab; marker: LANTERN-593.` There is no second
`contextCompaction` item, and the rollout still has exactly one `compacted`
record. Therefore queueing the literal slash text is **not a genuine compaction
control on this tested runtime**. Do not extrapolate this result to the desktop
without its own trial.

## Token observations and the Placet reader

The definitive run emitted these last-usage samples:

| Point | Input tokens | Last total tokens | Context window |
| --- | ---: | ---: | ---: |
| Before compaction | 16,217 | 16,271 | 258,400 |
| Immediately after compaction | 0 | 4,803 | 258,400 |
| First following turn | 16,876 | 16,903 | 258,400 |
| Literal queue slash turn | 16,911 | 17,304 | 258,400 |

`bridge/transcripts.py:session_meta` uses `last_token_usage.input_tokens`.
Replaying the dedicated rollout prefix ending at the post-compaction token
record makes the existing reader report `context_tokens: 0`. This is **not proof
that the resumed context is empty**: the same usage sample has nonzero
`total_tokens`. The next model call also incorporates instructions and other
context; the short seed deliberately does not establish a large-context savings
ratio.

For an automatic threshold, treat this post-compaction sample distinctly and
validate the semantics of the runtime token fields. Never sum lifetime account
usage or use this zero as a blanket guarantee that the next message fits.

## Desktop transport: read-only findings

A process inspection restricted to executable, PID and listen flags shows the
running desktop Codex app-server with its default stdio transport. The shared
control socket `~/.codex/app-server-control/app-server-control.sock` is absent;
`~/.codex/ipc/ipc.sock` exists, but existence does not establish that it belongs
to or controls this desktop runtime. It was not used.

`app-server daemon status` is not a command in 0.159.2 (it returns a usage error).
The `app-server proxy --help` command describes a connection to the shared
control socket (or an explicit `--sock`), not a way to attach to the desktop
stdio pipe. No daemon was started, restarted or reconfigured. No private stdio
stream was attached to. The available `--listen` options on a separately launched server
do not expose a thread already owned by the desktop.

## Desktop queue trial: negative

The operator selected the dedicated laboratory conversation **Rispondi ok**,
`01a1467c-2982-78a0-8246-61283d1c3e80`, in Placet decision #597. No other
desktop conversation was tested. Its model was `gpt-6.1-sol`, effort `low`.

The app's read-only thread API reported `notLoaded`. The bridge's existing
`codex://threads/<id>` opener loaded it. Opening also consumed an old, already
queued Placet message #575 (`Ciao`), which completed before this experiment.
The probe did not resend it. This is a reminder that opening a thread is not
side-effect-free when it already has queued work.

After confirming idle and recording a fresh rollout byte offset, send exactly
one literal `/compact` with the app-bundled `codex queue` command. Between
20:34:05 and 20:34:09 UTC the rollout records a normal user message `/compact`,
a normal assistant response summarizing the conversation, token usage and
`task_complete`. There is **no new `compacted` record**. Placet's context reader
reports 42,516 input tokens afterwards (39,560 before loading, before the old
queued `Ciao` also ran). The assistant's prose summary is not compaction.

The ordinary thread-opening link was tested, not an invented command parameter.
No documented deep-link compaction route was established. No private app-server
transport was intercepted, no second runtime resumed this thread, and no GUI
input was automated. The inability to call the owning desktop server is the
remaining access limitation; it does not mean the desktop's own Compact UI
cannot compact.

## Native interactive CLI: verified

A fresh CLI thread `01a1895f-1fd6-79a1-8647-a8dc97e5c4ab` was started with
the same 0.159.2 binary, `--no-daemon --no-alt-screen`, read-only sandbox and
approval policy `never`. The existing default model was used. The terminal
warned that TERM was dumb; continuing did not change global configuration.

After its harmless seed turn, enter `/compact` into that same terminal and send
Enter separately. The terminal displays `Compacting context`, then
`Context compacted`; the same rollout records `compacted` at
20:35:12.950 UTC, followed by token usage and `task_complete`.
A follow-up receives the original harmless marker `LANTERN-CLI-596`; its
rollout ends in `task_complete`. The CLI is then closed with `/exit`, without
sending a process signal.
Terminal text and Enter must be separate operations: this TUI can treat a bulk
write as paste rather than a submitted command.

The native CLI reproduces the token caveat: input tokens are 16,313 before,
zero immediately after compaction (last total tokens: 4,781), and 16,417 on the
following model call.

This proves native same-instance CLI slash handling, not a ready-made Placet
adapter. A tmux adapter still needs the existing stable pane/process identity,
idle/input checks, shared delivery lock, fresh rollout boundary and completion
verification. A CLI without an owned terminal/control channel is not remotely
controlled merely by knowing its thread ID.

## Initial recommendation and remaining scope (superseded below)

- **Available:** same-instance compaction for a session owned by Placet's
  app-server connection, with an explicit completion signal and a successful
  subsequent turn.
- **Not a control:** literal queue `/compact` on the tested app-server runtime.
- **Available:** native interactive CLI `/compact` in the same owned terminal;
  product integration still needs the tmux identity and delivery safeguards.
- **Not established:** a remotely callable genuine desktop compaction channel.
  The desktop queue slash trial produced ordinary text, not compaction.
- Keep the product's pre-delivery barrier closed until operation completion is
  verified. Do not consume or queue the work message just to wake the desktop
  before trying to compact it.
- A desktop limitation must remain visible; do not work around it with concurrent
  resume, direct provider DB edits, private-channel interception or GUI typing.

## References

- [Official app-server protocol](https://learn.chatgpt.com/docs/app-server):
  `thread/compact/start`, context-compaction items and turn lifecycle.
- [Official hooks](https://learn.chatgpt.com/docs/hooks): documented SessionStart
  compaction context, distinct from the locally observed hook-run metadata.
- [Provider integration experiments](provider-integration.md).

## Follow-up: desktop compaction through the coordination bus

**This supersedes the earlier access conclusion:** a genuine same-runtime
Codex desktop compaction channel was found and tested after the first report.
The negative result for a literal queue slash command remains valid.

### Mechanism and discovery

Read-only inspection of the installed application package (0.159.2 runtime)
found the desktop's coordination request `thread-follower-compact-thread`.
The owner handler calls its own `compactThread`, which delegates to the owning
app-server's `thread/compact/start`. This is different from intercepting or
proxying that server's private stdio connection.

The existing Unix socket `~/.codex/ipc/ipc.sock` is a coordination **router**.
The process owning the listening socket need not own the conversation: other
clients, including the desktop, can register with that router. Consequently,
the earlier assumption that an extension-owned socket cannot reach the desktop
was incorrect. No router or daemon was started by the probe.

The observed wire format is a four-byte little-endian length followed by UTF-8
JSON. A client registers with an `initialize` request and uses the returned
client ID. The probe called only:

1. `thread-owner-discovery`, version 1, with `hostId: local` and the exact
   authorized laboratory conversation ID;
2. `thread-follower-compact-thread`, version 1, with that conversation ID and
   `targetClientId` bound to the owner returned by discovery.

No other client was impersonated. No request handler, conversation runtime,
proxy, hook or application configuration was installed. The probe validated
socket/directory ownership and permissions, connected directly to the existing
router, and closed its own client connection normally.

### Same-instance result

The desktop laboratory thread remained
`01a1467c-2982-78a0-8246-61283d1c3e80`. Its desktop state was idle before the
operation. Owner discovery succeeded. The targeted compaction request returned
`resultType: success`, `result: {ok: true}`, from that same owner client.
This reply is **dispatch acceptance**, not sufficient evidence of completion.

The existing thread's rollout then recorded:

- 21:10:39.635 UTC: `task_started`;
- 21:10:59.641 UTC: a genuine `compacted` record;
- token usage, `item_completed` and 21:10:59.686 UTC: `task_complete`.

A single ordinary follow-up message, delivered with the existing queue channel,
returned `DESKTOP-COMPACT-IPC-OK` and completed on the desktop. No second runtime
loaded or wrote that conversation. Placet's last-input metric was 42,516 tokens
before the operation, zero immediately after, and 29,466 on the following model
call. The zero again demonstrates the post-compaction measurement caveat.

The raw Codex hook input was not captured in this desktop trial. In particular,
a `SessionStart` source of `compact` is not established for this provider. The
Claude-specific request-binding fix must not be assumed to cover Codex unless
that event shape is separately verified; the observed rollout completion is the
evidence used here.

### Integration risks and scope

This is an **experimental, undocumented adapter**, not a stable public API.
The installed package locations and request versions are version-specific.
The relevant installed program modules were
`.vite/build/bootstrap-B7ariqxX.js` (router/client and owner dispatch),
`.vite/build/src-ghAWefM3.js` (request versions), and
`.vite/build/application-network-startup-D74LEWDz.js` (framing/socket checks).
Only program files were inspected; provider databases were not edited.

A production adapter should:

- restrict commands to the exact authorized session and discovered owner;
- verify socket ownership, restrictive modes and peer identity; never create or
  unlink the application's socket or start an alternative router;
- rediscover ownership per operation and fail closed on protocol mismatch;
- require idle, use the delivery barrier and bound all frames/timeouts;
- distinguish accepted, completed, failed and uncertain; never retry dispatch
  after a timeout without reconciling the known operation;
- correlate new rollout events from a captured boundary; an unrelated native
  or user-triggered compaction must not be blindly attributed to this request;
- handle owner disconnects and unloaded conversations explicitly.

Only a loaded idle desktop laboratory conversation was compacted. Busy,
unloaded, owner-disconnect and application-update cases remain separate tests.
The same-UID bus also carries more powerful requests: Placet must expose only
the fixed compaction operation, never a generic IPC passthrough.

For a context threshold, Placet can use this channel when its own measurement
and policy require compaction, then wait for completion before releasing the
work message. No slash text or second inference runtime is needed.

For the successful Claude Desktop countercheck, its setup and different trigger
semantics, see [desktop compaction alternatives](2026-10-29-desktop-compact-alternatives.md).
