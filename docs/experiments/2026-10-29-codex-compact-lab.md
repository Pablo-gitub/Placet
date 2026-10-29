# Codex compaction laboratory — October 29, 2026

Status: **partial**. Genuine compaction is verified in a dedicated app-server
runtime and in the native interactive CLI. The desktop queue trial is negative:
slash text is an ordinary message, and no accessible genuine desktop control
was established. No product code or provider configuration was changed.

## Boundaries and versions

- App-bundled executable: `/usr/lib/chatgpt/resources/codex`, `codex-cli 0.159.2`.
- PATH executable: `/home/paolo/.local/bin/codex`, `codex-cli 0.145.0`;
  not used for the compaction trials.
- Repository baseline: `f2a3dd7`, branch `lab/codex-compact` in
  `.worktrees/codex-compact`.
- The runtime selected its configured default, `gpt-6-luna`, effort `high`;
  the probe did not override the model.
- New dedicated threads only. No existing user thread was resumed, no second
  runtime wrote the same thread, no private proxy or GUI automation was used.
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

## Recommendation and remaining scope

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
