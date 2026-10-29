# Placet laboratory

Log of the experiments run on October 15–16, 2026 on the test bench
`~/Projects/placet-hook-lab`. The decisions they led to are in the
[requirements](../requirements.md) (v2.0) and the [architecture](../architecture.md)
(v2.0); this page keeps the tests: what was done and what happened.

> **Historical record.** These are observations from October 15–16, 2026, on the
> provider versions installed then. Later findings are in the
> [October 18 session integration record](2026-10-18-session-integration.md): for
> example, `codex queue` only delivers to conversations the Codex app has
> loaded, and suspended Codex and Claude Desktop conversations are now reopened
> through the apps' native links.

Laboratory material (not in the repository): debug hooks for Claude, Codex
(`.codex/hooks.json`) and Antigravity (`.agents/hooks.json`), test skills
(`placet-banana`, `placet-command-interface`), `steer_hook.py`,
`codex_lab_bridge.py`.

## Claude Code

- **Exp. 1a — built-in command through the hook channel (desktop)**: `/compact`
  sent as a message is NOT executed; the agent recognizes it as an interface
  command and replies that it cannot run it. Compact / effort on the desktop:
  "at the PC only".
- **Exp. 1d — `/effort` and `/compact` typed into a CLI in tmux (October 22, 2026, Claude Code 2.1.303,
  disposable tmux session, one short turn first)**: WORKS, with one dialog.
  - `/effort high` on a conversation that already has a turn opens a confirmation within about 0.2 s
    ("Change effort level? ... This conversation is cached for the current effort level ... 1. Yes,
    switch to high / 2. No, go back"); Escape answers "Kept effort level as auto". Nothing is applied until
    it is confirmed. The dialog replaces the prompt box and shows no "esc to interrupt".
  - `/effort auto` applies at once ("Effort level set to auto") and the prompt is back.
  - `/compact` shows "Compacting conversation..." with "esc to interrupt" in the footer (the prompt box is
    still drawn), and "Compacted" when done, about a few seconds later.
  - The idle prompt is `❯ ` (with the dim `Try "edit <filepath> to..."` suggestion on a fresh session): the
    `idle_patterns` of AGENTS.md match it. Trimmed captures are kept as test fixtures
    (`tests/fixtures/claude_cli/`).
- **Problem found — dead waiter**: the waiter of session B stopped at 07:49
  (terminated from outside while waiting, no trace in the logs) and nobody
  restarted it until the next turn: the session stayed unreachable for hours.
  It must be made robust (restart, or a stable process per session).
- **Exp. 1b — skill through the hook channel (desktop)**: WORKS. Request #11
  "run the skill /placet-banana": the session invoked the skill ("Ran skill")
  and replied `BANANA-SKILL-OK`. A skill created while the session is open is
  seen at the next turn (no need to reopen it). So skills can be launched from
  the phone on the desktop too; built-in commands (`/compact`, `/effort`,
  `/model`) cannot.
- **Exp. 2 — "Now" in the middle of a turn (desktop)**: WORKS. A PostToolUse
  hook returning `additionalContext` inserted the message after the 4th read
  (about 7 s after it was written); the session stopped and wrote
  `BANANA-STEER`. "Now" delivery can be implemented this way; if the turn has
  no more tool calls, the message must be delivered at the end of the turn.
- **Exp. 1c — `/compact` from a terminal on a desktop session**: DOES NOT WORK.
  - From the skill: `claude -p --resume <id> "/compact"` is blocked by the
    session's permission check (auto mode).
  - Run by hand: the file really is compacted (68.0k → 3.3k, manual
    `compact_boundary`), but the desktop app ignores it: the next turn still
    uses 68k and attaches to the previous conversation (a parallel branch in
    the file). Conclusion: compact and effort on the desktop stay "at the PC
    only"; remotely they can only be done on CLI sessions in tmux.
- **Exp. 3 — CLI session (tmux) resumed in the desktop app**: DOES NOT APPEAR.
  Session `8078480a…`, started in tmux on `placet-hook-lab`, is not in the
  app's sidebar (which only lists sessions created by the app), not even after
  F5. Alternative path: Remote Control (`/remote-control` in the CLI) to see it
  in claude.ai/code / the app.
- **Exp. 3b — CLI in tmux + Remote Control**: WORKS.
  `/remote-control` in the tmux session makes it visible and writable from
  claude.ai/code and from the Claude app. Placet request #14: delivered by the
  waiter (hook channel), the session replied `ARANCIA-REMOTE`, the answer came
  back as an implicit reply (Stop hook) even without the MCP server, and
  everything was visible in the Remote Control view, opened **from the Claude
  desktop app** (so the CLI session can be recovered and written to from the
  desktop too, although it does not appear among the project's local
  sessions). Path for sessions created by Placet: CLI in tmux + Remote Control
  → resumable from every device.
- **Observation — "suspicious" prompts**: the old request #6, delivered via
  tmux to a session that had never seen it, was rejected as possible prompt
  injection. The delivery preamble must make the origin verifiable (e.g. the
  session can check the request with a Placet tool), and old requests must not
  be delivered to sessions other than the intended one.
- **Observation — MCP disconnected**: the laboratory sessions lost the Placet
  MCP server. Real cause (found later): when starting the CLI in tmux, the
  answer "Continue without using this MCP server" was given for the project
  server, and the choice ended up in `.claude/settings.local.json`
  (`disabledMcpjsonServers: ["placet"]`) for ALL the project's sessions,
  desktop included; disabled wins over enabled. Also, the desktop app
  overwrites `~/.claude.json` (editing it by hand while the app is open does
  not help). One session also looked for the short id among the app's
  sessions. Needed: stable names (point 3), a check that reports "Placet is not
  connected to this session" (e.g. from the SessionStart hook), and sessions
  created by Placet must approve the server explicitly.
- **Exp. 4 — context, effort and usage limits (Claude)**:
  - **CLI**: the statusline receives everything as JSON: `context_window`
    (e.g. 39.5k / 1M · 4%), `effort.level`, `model`, `cost`, `session_name`
    and `rate_limits.five_hour` / `seven_day` (`used_percentage`,
    `resets_at`). Limits belong to the account: one active CLI session (e.g.
    Placet's service session) is enough to get Claude's usage.
  - **Desktop**: the app does NOT run the statusline. Context, model and
    effort are read from the transcript: every `assistant` entry has
    `message.usage` (context = input + cache_read + cache_creation),
    `message.model`, `effort` and `perTurnEffort`. Window size from the model.

## Codex and Antigravity

### Survey (2026-10-15)

**Codex** — CLI `codex-cli 0.145.0` + desktop app (ChatGPT/Codex, running).
- Sessions: `~/.codex/sessions/YYYY/MM/DD/rollout-…-<id>.jsonl` (JSONL, like
  Claude): `response_item` (message, reasoning, tool call/output),
  `event_msg` (task_started/complete, token_count), `compacted`.
- Every `token_count` holds the context (`last_token_usage`,
  `model_context_window` = 475k) and the **limits**: `rate_limits.primary`
  (5h: 14%, reset) and `secondary` (7d: 2%, reset). No service session needed:
  reading the latest rollout is enough.
- `hooks` feature stable and enabled; config in `~/.codex/config.toml`
  (model, effort).
- `codex app-server` (JSON-RPC protocol with a daemon and the socket
  `~/.codex/ipc/ipc.sock`): a possible official channel to start turns on an
  existing thread, including the desktop app's. To be explored.
- `codex resume`, `codex exec`, `codex mcp` to register Placet.

**Antigravity** — IDE `antigravity` 1.107 + agent CLI `~/.gemini/bin/agy`.
- Conversations: `~/.gemini/antigravity/conversations/<id>.db` (SQLite:
  `steps`, `trajectory_meta`, …) plus `brain/<id>/…/messages/*.json`.
- `agy` has: `--conversation <id>` (resume), `--effort`, `--model`,
  `-p` with `--output-format stream-json`, `--remote-control`, `agy mcp`
  (to register Placet), plugins.
- Hooks: to be verified. `mcp_config.json` empty.

### Proposed experiments
- C1. Register Placet as an MCP server in Codex and in `agy`; call
  `placet_ask_human` from each.
- C2. Codex hooks: which events exist (turn start, turn end, tool) and whether
  one can wake the session (equivalent of asyncRewake).
- C3. `codex app-server`: send a message to a thread open in the Codex desktop
  app.
- A1. Format of `agy` conversations (reading the chat) and where context and
  limits are.
- A2. Hooks or a wakeup mechanism in `agy`; failing that, tmux.
- **Exp. C1 — Placet in Codex**: WORKS (MCP registered as agent `codex`;
  question received on the phone). Codex chose a short wait and the answer
  arrived later: it only saw it by calling `placet_check_status`.
- **Requirement that emerged**: whoever waits for an answer must ALWAYS be
  woken when it arrives (Claude: waiter; Codex: to be defined; tmux:
  send-keys). A minimum wait enforced server-side for questions to the human.
- **Exp. C2 — Codex hooks**: events like Claude's (SessionStart,
  UserPromptSubmit, PreToolUse, PostToolUse, Stop, …) in
  `<project>/.codex/hooks.json`, but **async hooks are not supported**
  ("async hooks are not supported yet"): no equivalent of asyncRewake. Project
  hooks must be approved (trusted) in Codex. Paths to waking: a synchronous
  Stop with `decision: block` while the session waits for an answer; or
  sessions managed by Placet through `codex app-server` (`turn/start`,
  `turn/steer`, `thread/compact/start`, `thread/read`,
  `account/rateLimits/read`). The Codex desktop app uses a private app-server
  (stdio): its threads cannot be attached to.
- **Exp. C3 — `codex queue` (waking Codex desktop)**: WORKS (on file). The
  `codex` shipped with the desktop app (`/usr/lib/chatgpt/resources/codex`,
  0.159.2; the CLI in PATH is 0.145 and lacks the command) accepts
  `codex queue --thread <id> --message <text>`. The message entered as a user
  message in the thread open in the app and started a turn (reply `KIWI-CODEX`
  in 3 s). It is Codex's wakeup channel: the equivalent of Claude's waiter, but
  without a waiting process. Confirmed: message and reply appear on their own
  in the app, in real time.
- **Exp. A1 — Antigravity chats**: readable.
  `~/.gemini/antigravity/brain/<conversation_id>/.system_generated/logs/transcript.jsonl`:
  JSON per step (`step_index`, `source`, `type`, `status`, `created_at`,
  `content`, `thinking`, `tool_calls`). Types: `USER_INPUT`,
  `PLANNER_RESPONSE` (model replies), `GENERIC` (tool results),
  `SYSTEM_MESSAGE`, `CHECKPOINT`, `ERROR_MESSAGE`. The SQLite database
  `conversations/<id>.db` holds the same steps as protobuf. Context and usage
  limits are not in the transcript: to be found.
- **Survey A2 — waking Antigravity 2.0** (`/opt/antigravity-2`):
  - The app starts `agy --hub --hub-port=<port> --csrf_token=<token>`: a local
    server with internal APIs, including `SendUserCascadeMessage`,
    `SendAllQueuedMessages`, `GetCascadeTrajectory`, `CancelCascadeInvocation`.
    Undocumented: they would work but may change with every version.
  - Hooks supported by `agy` (SessionStart, PreToolUse, PostToolUse, Stop,
    Notification) in `.agents/hooks.json` (project) or
    `~/.gemini/config/hooks.json` (user). Async/rewake to be verified.
- **Exp. C4 — Claude → Codex through Placet**: WORKS. Request #16 from a Claude
  desktop chat of the laboratory to the Codex desktop session `01a1418c…`,
  approved from the phone. A laboratory bridge
  (`placet-hook-lab/codex_lab_bridge.py`) registers the thread as a session of
  agent `codex`, takes the message from the inbox, delivers it with
  `codex queue`, waits for `task_complete` in the rollout and sends the stop
  with the last reply. Codex created `codex-was-here.txt` and replied on its
  own with `placet_reply`; the outcome went back to the Claude session. Time
  from delivery to reply: 24 s.
- **Problem found — bridge tied to the session**: the bridge started as a
  background process of a Claude Code session dies when the app is restarted
  (happened twice). In the implementation the Codex channel goes into Placet's
  bridge (systemd user service), not into a session.

- **Exp. A2 — Antigravity hooks (`agy`, documentation bundled in the
  binary)**: file `.agents/hooks.json` (project) with named hooks; events
  `SessionStart`, `PreToolUse`, `PostToolUse`, `PreInvocation` (before every
  model call), `PostInvocation`, `Stop`. camelCase JSON payload with
  `conversationId`, `workspacePaths`, `transcriptPath`, `modelName`. Hooks are
  **synchronous only** ("no async execution"): no waking of an idle session.
  However:
  - `PreInvocation` → `injectSteps: [{"userMessage": "..."}]` or
    `ephemeralMessage`: a "Now" channel even more precise than Claude's.
  - `Stop` → `"decision": "continue"` + `reason`: for a waiting Stop.
  - Waking idle sessions: only through the app's internal API
    (`SendUserCascadeMessage` on the `agy --hub` server) or the `agy` CLI in
    tmux.
- **Research (by Codex) on Antigravity 2.0**:
  - No public equivalent of `codex queue`. Candidate: the internal RPC
    `exa.language_server_pb.LanguageServerService/SendUserCascadeMessage`
    (Connect, JSON POST, header `x-codeium-csrf-token`) on the app's language
    server (`/opt/antigravity-2/resources/bin/language_server`, local ports,
    token on the command line). Candidate body:
    `{"cascadeId", "items": [{"text"}], "deliveryStrategy":
    "MESSAGE_DELIVERY_STRATEGY_WHEN_IDLE"}` (`NEXT_INVOCATION` also exists,
    useful for "Now"). Undocumented: to be validated and isolated behind an
    adapter, because it may change with every version.
  - `agy --conversation <id> -p …` resumes the conversation in another
    process: live visibility in the app is not guaranteed.
  - Context and quotas: `/context` and `/usage` in the CLI; the CLI statusline
    has JSON with `context_window.used_percentage` and
    `quota.<bucket>.remaining_fraction` / `reset_time` (5h windows for
    Pro/Ultra, weekly ones). As for Claude: a CLI service session.
  - Hooks: enabled in Settings → Customizations → Hooks (app) or `/hooks`
    (CLI); no approval by hash.
- **Exp. A2b — Antigravity 2.0 hooks in the app**: WORK, without approval
  (adding `placet-hook-lab` as a project was enough). Sequence of a turn:
  `SessionStart` → (`PreInvocation` → `PostToolUse`* → `PostInvocation`)×N →
  `Stop` (`terminationReason: NO_TOOL_CALL`, `fullyIdle: true`). Every payload
  has `conversationId`, `workspacePaths`, `modelName` (e.g.
  `gemini-3.8-flash-medium`), `transcriptPath`. The Stop does not contain the
  last reply: it must be read from the transcript.
- **Exp. A3 — waking Antigravity 2.0 through the internal API**: WORKS (on
  file). `SendUserCascadeMessage` on the app's language server
  (`http://127.0.0.1:<port>`, header `x-codeium-csrf-token` read from the
  command line of the `language_server` process) with `cascadeId`,
  `items: [{"text"}]`, `deliveryStrategy: WHEN_IDLE` **and `cascadeConfig`**:
  without the config the message gets in but the turn fails ("neither
  PlanModel nor RequestedModel specified"). With the `userConfig` of the
  conversation's last `USER_INPUT` (read with `GetCascadeTrajectory`) the turn
  starts: reply `MELONE-GEMINI` in 1 s, PreInvocation/Stop hooks run.
  Confirmed: messages and reply appear on their own in the app. Undocumented
  API: isolated adapter, with a version check and a fallback (tmux + `agy`).

- **Exp. A4 — Placet in Antigravity 2.0 (sender)**: WORKS. MCP server in
  `~/.gemini/config/mcp_config.json` (`mcpServers` format like Claude; NOT
  `~/.gemini/antigravity/mcp_config.json`, which the app does not read), agent
  `gemini`. Request #17 `placet_ask_human` with a 300 s wait: question on the
  phone, answer "Yes" received directly by the tool.

## Summary

| | Claude desktop | Claude CLI in tmux (+ Remote Control) | Codex desktop | Antigravity |
|---|---|---|---|---|
| Read the chat | ✅ JSONL transcript | ✅ JSONL transcript | ✅ JSONL rollout | ✅ transcript.jsonl |
| Message from Placet (wakeup) | ✅ waiter + asyncRewake | ✅ waiter / send-keys | ✅ `codex queue` | ✅ internal API (A3) |
| Reply to Placet | ✅ Stop hook / placet_reply | ✅ | ✅ placet_reply / rollout | to be tested (Stop hook + transcript) |
| Skills remotely | ✅ | ✅ (presumably) | to be tested | to be tested |
| "Now" mid-turn | ✅ PostToolUse | to be tested | `turn/steer` to be tested | PreInvocation `userMessage` (documented, to be tested) |
| `/compact`, `/effort` remotely | ❌ at the PC only | ✅ send-keys | to be tested | to be tested |
| Context | ✅ from the transcript | ✅ statusline | ✅ rollout | `agy` statusline (documented, to be tested) |
| 5h / 7d limits | through a CLI session | ✅ statusline | ✅ rollout | `agy` statusline (documented, to be tested) |
| Visible on other devices | ✅ | ✅ (also from the desktop app) | — | Remote Control (documented) |
| Sends through Placet (MCP) | ✅ | ✅ | ✅ (C1) | ✅ (A4) |

### Combinations across providers

Every A → B exchange is made of three pieces: A sends (Placet MCP server in
A), B is woken (provider B's channel), B's reply returns to Placet.

| Piece | Claude | Codex | Antigravity |
|---|---|---|---|
| Sends through Placet (MCP) | ✅ | ✅ (C1) | ✅ (A4) |
| Is woken | ✅ waiter | ✅ `codex queue` | ✅ internal API |
| Reply returns to Placet | ✅ Stop hook | ✅ placet_reply / rollout | to be tested (Stop hook + transcript) |

Tested end to end: Claude → Claude, Claude → Codex. The other combinations
(Codex → Claude, Antigravity ↔ Claude/Codex) become acceptance tests once the
adapters are implemented in the bridge.

### Codex same-instance compaction

See the [October 29 compaction laboratory](2026-10-29-codex-compact-lab.md)
for an owned app-server trial, literal queue slash behaviour, completion signals
and the post-compaction token-reading caveat. Native CLI compaction works;
desktop queue slash text does not compact, and no genuine remote desktop
compaction channel was established.
