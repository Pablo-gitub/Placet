# Placet system architecture and technical specification

> **Baseline:** version 2.1, 2026-10-16, following the [multi-provider laboratory](experiments/provider-integration.md).
> **English documentation update:** 2026-10-18, incorporating the later delivery, wakeup, attachment, return-approval and localization decisions.
> **Role:** human-in-the-loop hub, cross-session bus for Claude Code, Codex and Antigravity/Gemini, and phone session viewer.

Items marked **planned** are agreed designs, not implemented capabilities. Historical observations and provider-version limits remain in the [experiments](experiments/provider-integration.md); they are not public provider API guarantees.

## 1. Overview

```mermaid
flowchart TB
    phone["Phone: Web Push / ntfy notifications; PWA sessions/chat/relations"]
    backend["Backend: FastAPI, system user placet; SQLite, routing, long-polling, SSE"]
    mcp["MCP server: stdio per client session"]
    hooks["Provider hooks: turn events, Claude waiter"]
    bridge["Bridge: desktop user's systemd service; provider delivery/read adapters"]
    sessions["Claude desktop/CLI · Codex desktop/CLI · Antigravity"]
    phone <-- "HTTPS: Tailscale Serve → Unix socket" --> backend
    sessions --> mcp -->|REST, agent token| backend
    sessions --> hooks -->|local event spool| bridge
    hooks <-- "Claude inbox polling" --> backend
    bridge <-- "REST /api/bridge/*, bridge token" --> backend
    bridge <-- "provider channels and files" --> sessions
```

Three flows:

1. **Questions/prompts:** sessions → MCP → backend → human PWA or another session's provider channel.
2. **Turn events:** provider hooks report start/end, final response and relevant tool use, registering sessions and observing busy/idle state.
3. **Chat/usage:** bridge reads provider files on demand; backend relays results to the PWA. Usage/context/control adapters are partly planned, not all exposed in the product.

## 2. Components

### 2.1 Backend (`backend/app`)

- Python 3.11+, FastAPI, SQLite WAL, Pydantic, Uvicorn.
- **Two listeners in one process** (`serve.py`): TCP `127.0.0.1:8000` for agents/bridge; Unix socket `/run/placet/human.sock` in production for the PWA through Tailscale Serve. If either listener stops, the process stops.
- Requests/lifecycle, hook-driven session registry, per-session inboxes, routing, SSE (`request_update`, `session_update`, `usage_update`, `automation_update`), notifications and a 30-second expiration/redelivery sweeper. Notifications go through Web Push to the installed PWA (VAPID keys generated on first use and kept in the database; only browser vendors' push services are contacted; payloads carry a short title, never request text) and through ntfy when configured. Notification preferences are managed via human-only `GET/PUT /api/settings/notifications` and stored in `app_settings` under `notification_preferences` (`needs_you`, `problems`, `queue_held`, `turn_end`); preferences currently apply globally to all devices.
- Relays bridge-read conversation pages (§5).
- **Planned:** nodes, projects and relations/types. Provider usage is now reported by the bridge and shown in the PWA.

### 2.2 MCP server (`mcp_server`)

- stdio process configured in each client: Claude `.mcp.json`/`claude mcp add`; Codex `codex mcp add`/`~/.codex/config.toml`; Antigravity 2.0 `~/.gemini/config/mcp_config.json`, **not** `~/.gemini/antigravity/mcp_config.json`.
- Authentication through `PLACET_API_TOKEN`; optional `PLACET_AGENT_ID` must match token identity.
- Tools: `placet_ask_human`, `placet_send_to_agent` (inline prompt or Markdown `prompt_file`), `placet_reply`, `placet_progress` (progress without concluding, RFC-16), `placet_ack`, `placet_list_agents` (reachable/unreachable sessions), `placet_check_status`, `placet_share_file`.
- Human questions wait at least 60 seconds (RF-09). A timeout ends tool waiting, not the request's lifetime.
- **Planned:** typed `send(target, type_id, prompt)` and outgoing-relation discovery (RFC-08). Human-question `context` is still required; removing it remains RF-05b.

### 2.3 Provider hooks (`hooks/`)

Convert client events into Placet observations (`POST /api/sessions/{id}/events`: `start`, `prompt`, `tool`, `stop`, `end`). Normal event hooks write to the local spool rather than wait on HTTP. `hooks/install.py` installs globally and changes only Placet entries (RF-42). Sessions are **present but quiet**: ordinary turns report lifecycle events; only Placet work collects a response. `placet_common.active(cwd)` excludes folders with `.placet-off` in that folder or an ancestor (RF-45).

| Provider | User configuration | Events | Notes |
| --- | --- | --- | --- |
| Claude Code desktop/CLI | `~/.claude/settings.json` | SessionStart, UserPromptSubmit, Placet PostToolUse, Stop, SessionEnd; steering PostToolUse | Stop supplies `last_assistant_message`; asynchronous `asyncRewake` waiter. Desktop SessionEnd suspends; CLI SessionEnd closes. Continued conversations (RF-29d) are detected on the first prompt, closing the previous session as continued and transferring its Home pin and node. Recovery opens only eligible desktop sessions with mail and no reachable waiter; [details](providers/claude-wake.md). |
| Codex | `~/.codex/hooks.json` | SessionStart, UserPromptSubmit, PostToolUse, Stop, SessionEnd | Shared `claude_code.py --provider codex` adapter for compatible fields. Synchronous only in tested versions (“async hooks are not supported yet”); review with `/hooks`. Desktop SessionEnd unloads/suspends (RF-29c); bridge queues once and requests a native-link reopen without asserting execution started. |
| Antigravity 2.0 | `~/.gemini/config/hooks.json` | SessionStart, PreInvocation (first invocation begins the turn), Stop | `antigravity.py`; camelCase `conversationId`, `transcriptPath`, `modelName`. Synchronous hooks must print `{}`. Stop lacks final response; adapter reads it from the conversation file. |

Hook approval and invocation must be verified against the installed app version. A configuration file alone is not evidence that execution events are being observed.

### 2.4 Bridge (`bridge/`)

- **systemd user service** of the session owner. Production communication is only through `/api/bridge/*` using a bridge token; that token cannot approve human decisions or read arbitrary requests.
- tmux delivery uses `send-keys` after idle checks: busy markers, `idle_patterns`, stable screen, dim hints ignored, no recent keyboard input.
- Provider adapters in `bridge/channels.py` and `bridge/transcripts.py`. For Codex/Antigravity, backend work lists sessions with mail; bridge claims it (`POST /api/bridge/sessions/{id}/take`), queues it in the app and confirms (`…/injected`), or releases it after a definite rejection. Uncertain outcomes follow §3.7 rather than blind retry.

| Capability | Claude | Codex | Antigravity 2.0 |
| --- | --- | --- | --- |
| Delivery/wakeup | Hook waiter or configured tmux `send-keys`; private desktop recovery link if waiter absent | App `codex queue --thread <id> --message <text>` (tested app binary ≥0.159); native link for suspended desktop sessions | Internal `SendUserCascadeMessage` language-server API |
| Now during a turn | PostToolUse `additionalContext` | Not enabled; `turn/steer` needs adapter verification | `MESSAGE_DELIVERY_STRATEGY_NEXT_INVOCATION`; live validation still needed |
| Chat read | `~/.claude/projects/<folder>/<id>.jsonl` | `~/.codex/sessions/YYYY/MM/DD/rollout-…-<id>.jsonl` | `~/.gemini/antigravity/brain/<id>/.system_generated/logs/transcript.jsonl` |
| Context source (planned display) | Latest assistant `message.usage` | `token_count.info.last_token_usage`, `model_context_window` | Verify `/context`/`agy` statusline |
| 5h/7d limit source (planned display) | Service CLI statusline `rate_limits` | Latest rollout `token_count.rate_limits` | Service `agy` statusline `quota.<bucket>` |
| Compact/effort (planned controls) | tmux CLI only; desktop PC-only | Needs verification | Needs verification |

Sources observed in experiments do not imply all corresponding product controls are implemented. A tmux alternative must target an explicitly configured session; it is not permission to resume a desktop transcript in another writer.

### 2.5 PWA (`frontend/`)

- React/Vite, served by backend. Markdown via `react-markdown`/`remark-gfm`; raw HTML ignored and `javascript:` links blocked.
- **Hash routes:** `#/` Home, `#/progetti` and `#/progetti/<id>` projects, `#/impostazioni` settings (app and background language, Web Push, and the way to the history), `#/storico` history (opened from Settings), `#/sessione/<id>[?project=<id>]` session. The navigation has Home, Projects and Settings; the app bar keeps only the live indicator. Notification `#request-N` links resolve to the originating conversation, Home or history. Route identifiers remain unchanged across languages.
- **Home** (`Home.jsx`): current questions, then pinned sessions ordered by `activity_at` (turn start/end, not waiter polls), provider/state/question count. **Add / view all** (`AddSessionsDialog.jsx`) handles one provider at a time; checkbox controls `PUT /api/sessions/{id}/pin`, including removal of closed sessions.
- **Conversation** (`SessionView.jsx`, `useTranscript.js`): pages from `/api/sessions/{id}/transcript`, initial bottom position, older pages on upward scroll with position retention; `session_update` requests new entries through `after`. Consecutive actions collapse into groups. Open session questions appear at the bottom.
- **History** (`History.jsx`): per-request timeline and three-aspect state.
- **Composer** (`Composer.jsx`): `POST /api/sessions/{id}/messages`; pending/delivering/failed state remains visible. Mentions, phone uploads and supported Queue/Now selection are implemented.
- **Localization:** English/Italian with English fallback and device-local preference; separate shared background-message language. [Full contract](localization.md).
- **Usage bar:** provider windows, with account-wide exhausted windows holding delivery until reset. **Planned:** project graph/editor. No service worker is installed; [cache behavior](experiments/2026-10-18-session-integration.md#pwa-updates-and-caching).

## 3. Delivery and wakeup

**RF-21:** when an authorized outcome, addressed prompt or human PWA message is deliverable, Placet attempts delivery through the recipient's provider channel. Return approval and reachability still apply.

### 3.1 Claude Code: asynchronous waiter

1. SessionStart and Stop launch `placet_waiter.py` with `async: true, asyncRewake: true`.
2. Waiter long-polls `GET /api/sessions/{id}/inbox` (240 seconds), with one lock per session.
3. On mail it writes the text to stderr and exits with code 2; Claude wakes the session (“Stop hook feedback” in the app).
4. Waiter death was observed in the laboratory. Timeout is now seven days; lock/engine-owner identity checks guard replacement. Bridge checks `waiter_seen_at`; an unavailable eligible desktop session with mail may be opened through the private continuation link. A waiter must still originate from a Claude hook, not an external attachment to an existing engine. [Recovery and limits](providers/claude-wake.md).

### 3.2 Codex: `codex queue`

- Bridge uses the app's Codex binary (`/usr/lib/chatgpt/resources/codex` in the tested Linux package). A **loaded** thread receives a user message and starts a turn visible live in the app.
- Suspended/unloaded threads require queueing **once**, then `/usr/bin/chatgpt codex://threads/<uuid>`. Opening can bring the window forward. Accepted queue items are not guaranteed retractable; link success is not turn-start proof. [Details](providers/codex-wake.md).
- Response: explicit `placet_reply` or observed final turn text, including rollout `task_complete.last_agent_message` in the tested format. Completion/return approval remain separate.
- No waiting process per session; no headless `exec resume` fallback and no app-server proxy.

### 3.3 Antigravity 2.0: language-server API

- Discover the app's `language_server` process (`--csrf_token`, `--override_ide_version` command line, ports from `/proc`). Select/cache the server that returns the target conversation.
- `POST /exa.language_server_pb.LanguageServerService/SendUserCascadeMessage`, Connect JSON, `x-codeium-csrf-token` header; body has `cascadeId`, `items: [{text}]`, `deliveryStrategy: MESSAGE_DELIVERY_STRATEGY_WHEN_IDLE` and **`cascadeConfig` copied from the latest `USER_INPUT`** returned by `GetCascadeTrajectory`. Without it the tested turn fails.
- Undocumented, version-sensitive API isolated in an adapter. Configured `agy`/tmux is an alternative channel, not a guarantee of safe automatic desktop-thread failover (RS-07).

### 3.4 CLI in tmux

- `send-keys` only after idle verification.
- **Planned Placet-created sessions:** launch CLI with Remote Control so it can be accessed from desktop, phone and web; experimentally verified for Claude. Session creation is not currently implemented.

### 3.5 Delivery preamble

- Recipient receives only the task prompt, with an identifiable `[Placet Request #N ...]` prefix. Generated prose follows the shared background language; IDs/tool names are stable.
- Claude hook delivery asks the agent to report the incoming sender/task because the desktop app may hide wakeup text.
- Human PWA messages (`question_type: message`) use an English or historical Italian user prefix and a brief note: reply directly, no `placet_reply`; the turn itself is the response. PWA displays them as user messages.
- **Planned:** stronger verifiable-origin protocol so an unexpected prompt can be checked through Placet rather than trusted solely because of its text.

### 3.6 Local event spool

RF-28: `hooks/spool.py`, `bridge/spool_forwarder.py`.

- Hooks write to `~/.cache/placet/spool/` (0700 directory, 0600 files) using a same-directory temporary file and atomic rename, then exit. No `fsync`; power loss may lose an event.
- Filename `<boot_id>-<monotonic-counter>-<event_id>.json`; payload includes `event_id`, `boot_id`, `seq`, `agent`, `provider`, `session_id`, `kind` (start, prompt, Placet-tool event, stop, end), `at`, `cwd`, event data. Order is observed per session.
- Final assistant text is captured at turn end into the event; Antigravity reads the transcript then, with brief retries, rather than later during forwarding.
- One locked bridge consumer watches with inotify plus startup/periodic full scans. It forwards in per-session order using agent authentication, removing files only after backend persistence/acknowledgement. Backend deduplicates `event_id` for 14 days.
- Retry 429, 5xx, 401/403 and network failures with backoff. Malformed/permanently rejected events go to `spool/failed/` without blocking others. At 50 MB, new events are discarded; a dedicated PWA capacity warning is still planned. Abandoned temporary files older than an hour are cleaned up.
- Claude waiter polling and Now steering use separate paths. Steering consumes a local file written by the bridge.

### 3.7 Delivery, execution and reply

RF-29/RF-29b. Independent aspects, each with evidence/timestamps; observations may arrive out of order:

- **Delivery:** waiting → delivering (attempt with `attempt_id`) → accepted (channel command succeeded), uncertain, failed or cancelled. Seeing the message in the conversation adds evidence.
- **Execution:** unobserved → started (turn opened by the message) → completed/interrupted.
- **Reply:** none, explicit (`placet_reply`), implicit (final turn response), ambiguous (other messages entered the turn).

The recognizable `[Placet Request #N` prefix supports reconciliation. The design includes `attempt_id`, but the current conversation text does **not** include it; any matching request prefix is treated as delivery evidence, regardless of attempt.

One request remains in flight per session until conclusion. Short acknowledgement deadlines trigger reconciliation; long execution deadlines only warn. Uncertain delivery does not free the next request; the user decides Retry/Cancel. `placet_reply` alone is not proof that execution ended.

Implementation:

- `requests` columns include `delivery_state`, `delivery_at`, `delivery_channel` (tmux/waiter/bridge), `attempt_id`, `execution_state`, `execution_at`, `execution_source`, `reply_state`, `turn_mixed`. `status` remains the summary exposed to agents/MCP.
- Claim by waiter/bridge → delivering. Turn-start hook, `placet_ack`, response or matching transcript message → accepted. Turn start → execution started; turn end → completed; session end during a turn → interrupted.
- Channel timeout (Codex queue beyond 60 seconds; Antigravity without a response), or no acknowledgement within 90 seconds for waiter/bridge → **uncertain**, with notification. Only tmux retains three automatic acknowledgement attempts. Bridge reconciles every 20 seconds by reading the conversation for the prefix.
- A prompt without `request_id` during a request's open turn marks it mixed; implicit response becomes **ambiguous**.
- Next request stays blocked while delivery is underway/uncertain, or accepted with execution unobserved/started, even if `STALLED`. **Time never releases the queue.** After 120 seconds without observed start, warn “start unconfirmed”; STALLED means “needs checking”. Require observed turn end or explicit user action.
- Human release actions: **Confirm finished** (`marked_finished_by_user`) or **Queue the next anyway** (`queue_overridden`), both `POST /api/requests/{id}/queue/release` with `reason`. Neither fabricates execution evidence. Cancel after delivery stops Placet waiting; it cannot stop the agent. Now (RF-35) explicitly inserts into the current turn.
- **Continued Claude Code conversations (RF-29d):** the hook sets `continues` on a prompt from the continuation summary in the prompt text or, failing that, from the first lines of the session's own transcript (`continued_from_transcript`, Claude Code only: the decoded first user record must start with the continuation phrase and name exactly one other transcript, never the transcript's own id; only a short transcript is read, a longer one is past the first prompt). On the first prompt the backend (`continue_session`) closes the previous session as continued and moves its pin and `node_id`; relations, `placet_list_relations` and project membership follow the node, so nothing else moves.
- **Held-queue alerts (RF-29e):** the periodic sweep (`expire_and_notify`) that flags unseen starts and stalled work also sends one Web Push per held request (`hold_alerted_at`) once it has held the queue for 10 minutes with an approved message for the same session waiting; nothing is marked while `queue_held` is off. The PWA mirrors the rule (`queueStuck`, built on `holdsQueueUnseen`) for the Home badge.
- **Release on evidence (RF-29f):** the Stop hook (without `pause`) and a `prompt` that starts a new turn call `release_on_observed_turn_end`: answered requests (status COMPLETED with a response) still holding the session's queue get `queue_released_at` and a `released_on_observed_turn_end` event by `system`. Elapsed time never releases. A `turn_paused` without a later `turn_resumed` for 30 minutes sets `start_unconfirmed_at` once (`pause_unresumed`); the queue stays held.
- Hooks send adapter identity (`claude_code.py/2`, `antigravity.py/3`) and `execution_source` records observer provenance. Alternative signals such as Codex app-server `turn/started`/`turn/completed` or Antigravity runtime status require verification before use. Assistant prose, absence of file writes and elapsed time are not turn-end evidence. **Antigravity pauses (RFC-16):** the app ends a turn while a background task runs (it tells the model to report and stop) and after the model writes `implementation_plan.md` for the user to approve; the work continues in a turn started by the task's completion `SYSTEM_MESSAGE` or by the approval input. The adapter reads both from the conversation file: such a Stop carries `pause` (`background_task` | `app_review`) and the next prompt `resumes`. The backend keeps the request EXECUTING and the session busy with the same `turn_request_id`, takes no implicit reply, logs `turn_paused` / `turn_resumed`, and for `app_review` sends a "waiting for your approval" push (needs-you category). A task that never resumes is caught by the existing execution timeout.
- Requests predating this state-model version do not hold the queue.
- PWA: Retry with duplicate-risk confirmation and Cancel for uncertain messages; Cancel while waiting; history displays all three aspects. APIs: human `POST /api/requests/{id}/delivery/retry|cancel`, bridge `POST /api/bridge/requests/{id}/uncertain|seen`.

### 3.8 Project exclusion

RF-45: `.placet-off` in a folder or ancestor.

Hooks do not spool events. Bridge discards excluded-session spool events, does not read conversations or deliver. Undelivered messages are cancelled with the historical audit reason `progetto escluso`. PWA hides sessions/requests; database audit records remain.

- Backend user `placet` cannot inspect the desktop home. Bridge checks known session `folders` from `/api/bridge/work` at most every 15 seconds and reports through `POST /api/bridge/folders`. Backend sets `excluded_at`, cancels undelivered messages and restores visibility when exclusion is removed.
- Excluded sessions are omitted from `/api/sessions` and SSE; their requests from `/api/requests`. Writes/reads/targeted requests fail with the appropriate 409/422 exclusion code. Bridge independently refuses reads even before backend learns of exclusion.
- Already-delivered requests remain unchanged; Placet cannot stop the agent.

### 3.9 Attachments

RF-38: `bridge/attachments.py`.

- Provider adapters recognize images in their native formats: Claude user-message base64; Codex `local_image` temporary `/tmp/codex-clipboard-<uuid>` (may disappear after reboot); Antigravity `brain/<id>/.user_uploaded/` referenced in message metadata and `generate_image` results in `brain/<id>/`.
- Transcript entries expose only attachment ID/name. ID combines entry cursor and position (`offset:entry:n`); retrieval rereads that entry with the same parser, serving only adapter-recognized content.
- Resolve symlinks; require a regular user-owned file within the provider's conversation roots, at most 10 MB, actual JPEG/PNG/WebP magic bytes, at most 50 megapixels. Never SVG/HTML. Both backend and bridge enforce `.placet-off`.
- **Shared files** (`placet_share_file`): transcript tool call is the evidence (Claude `tool_use mcp__placet__…`, Codex `McpToolCall`, Antigravity `call_mcp_tool` with JSON `Arguments`), rendered with shared-file label metadata. `check_shared` requires home containment, no hidden real-path components, no exclusion, regular user ownership, valid image or `%PDF-` PDF up to 20 MB. MCP validates immediately as well.
- Human-only `GET /api/sessions/{id}/attachments/{offset:entry:n}` uses bridge jobs; `Cache-Control: no-store`, `nosniff`, restrictive CSP; no disk copy on backend and no service worker. PDF responses allow inline use without the image response's `sandbox` CSP. PWA renders PDFs through PDF.js; image/PDF viewer controls are localized.
- Missing/offline files receive a localized unavailable message. An arbitrary path written in conversation prose is not a sharing authorization.

### 3.10 Now delivery

RF-35: `hooks/steer.py`, `bridge/steer.py`.

- **Inter-agent approval:** Queue (end of turn, default) versus Now (current turn) when a specific working Claude/Antigravity session is selected. Automatic target selection and Codex remain queued. Human selects timing; never infer it from prompt text or retroactively change approved requests.
- `POST /api/requests/{id}/decision` accepts `delivery_mode: "queue" | "now"`; approval, timing and recipient commit atomically with audit. Backend rejects Now without a supported specific session. If turn ends before insertion, return to queue. Inter-agent prompts retain sender, human-edited prompt and `placet_reply` instructions, not human attribution.
- Request `mode: "now"` maps to `delivery_mode`; bridge takes it for a **busy**, supported `STEER_PROVIDERS` session; otherwise ordinary end-of-turn delivery. Explicit exception to one-request-at-a-time.
- **Claude:** bridge writes one message/session to `~/.cache/placet/steer/<session>.json` (0600). PostToolUse on all tools, gated by a shell check (~12 ms when absent), claims via atomic rename, supplies `additionalContext`, spools `steered`; backend marks accepted/started. Transcript `hook_additional_context` displays as a user message.
- **Turn ends first:** bridge claims the same file using rename and returns it to `APPROVED` queue. Inserted or requeued, never both. Claimed file without event within two minutes → uncertain delivery.
- **Antigravity:** `SendUserCascadeMessage` with `MESSAGE_DELIVERY_STRATEGY_NEXT_INVOCATION`; definite failure returns to queue. Needs live validation on the installed app.
- **Codex:** not enabled; no verified PostToolUse `additionalContext` support. Consuming without proof would lose the message.
- At turn end, inserted Now requests complete with an **ambiguous** implicit response because the turn answered multiple messages.

## 4. Request lifecycle

```mermaid
sequenceDiagram
    autonumber
    participant A as Session A (sender)
    participant M as Placet MCP
    participant B as Backend
    participant U as Human (PWA)
    participant Br as Bridge/provider channel
    participant C as Session B (recipient)
    A->>M: placet_send_to_agent(target, prompt)
    M->>B: POST /api/requests
    Note over B: Current registry policy; typed relations planned
    B-->>U: push notification
    U->>B: approve / edit / reject
    Note over M,B: Tool long-poll, then PENDING
    B->>Br: authorized mail for B
    Br->>C: waiter / queue / API / send-keys
    C->>B: ack (hook or placet_ack)
    C->>B: response and turn-end observations
    Note over B: COMPLETED does not mean response forwarded
    alt automatic return explicitly approved for this request
        B->>Br: authorized outcome for A
    else return requires review
        B-->>U: review response
        U->>B: forward / edit / withhold
        B->>Br: authorized outcome only if forwarded
    end
    Br->>A: wake with authorized outcome
```

Agent-facing summary states:

```text
PENDING --decision--> APPROVED / REJECTED
    | expiration: EXPIRED; sender cancellation: CANCELLED
APPROVED --claim--> DELIVERING --ack/start--> EXECUTING
    |                   |                       |
    |             failed/uncertain          turn completion
    |             delivery evidence         and response
    |                                           v
    |                                       COMPLETED
    |                           execution timeout: STALLED (warning)
    +-- eligible tmux retries only, at most 3 acknowledgement attempts
```

This is a summary, not a replacement for the independent evidence in §3.7. `placet_reply`, `COMPLETED` and response-forwarding authorization are distinct.

## 5. Reading conversations

```text
PWA → GET /api/sessions/{id}/transcript?before=<offset>|after=<offset>&limit=N → backend
backend → transcript job (/api/bridge/jobs long-poll) → bridge
bridge → find/read/filter provider file → POST /api/bridge/jobs/{id}/result → backend → PWA
```

- **Job channel** (`backend/app/bridge_jobs.py`): bridge long-polls for 25 seconds in its own thread; backend queues/wakes/waits up to 8 seconds. No bridge contact in the last minute → immediate 503; result timeout → 504. Jobs are memory-only: lost page jobs can be retried. Laboratory timing: 16–46 ms/page, not a performance guarantee.
- **Readers** (`bridge/transcripts.py`): provider-specific JSON Lines. A page is a byte interval; `before` scans backward in 64 KB blocks to `limit`, `after` reads new complete lines. Entries have `role` (`user`, `assistant`, `action`, `placet`, `command`, `compact`, `notice`), `text`, `at`, `cursor`; actions add `tool`, `kind` (`command`, `read`, `edit`, `write`, `search`, `web`, `mcp`, `skill`, `agent`, …), `detail`. An unfinished line without newline is deferred.
- **Single-process development:** `run_all.py` sets `PLACET_TRANSCRIPTS_LOCAL=1`; backend uses the same readers directly. Never use this shortcut in isolated production.
- **Cursor:** byte/step position of oldest returned entry; bridge scans backward in blocks.
- **Filtering (RF-32):** user/assistant text, summarized actions, no tool results/reasoning. Drop service records such as Claude `attachment`, `queue-operation`, `file-history-snapshot`.
- **Live updates:** session stop emits SSE `session_update`; PWA requests new entries only.
- **Safety (RS-06):** backend sends provider/registered session ID, not arbitrary paths. Bridge searches provider roots (`~/.claude/projects/*/<id>.jsonl`, `~/.codex/sessions/**/rollout-*-<id>.jsonl`, `~/.gemini/antigravity/brain/<id>/…/transcript.jsonl`), accepts IDs matching `[A-Za-z0-9_-]` and rejects symlinks escaping those roots.

## 6. SQLite data model

### Existing core tables

- **`requests`:** `id`, `sender_id`, `recipient_id`, `question_type`, `prompt`, `edited_prompt`, `prompt_source`, `context`, `options`, `selected_options`, `custom_text`, `status`, `priority`, `created_at`, `expires_at`, `decided_at`, `completed_at`, `updated_at`, `delivered_at`, `delivery_attempts`, `delivering_since`, `executing_since`, `sender_session_id`, `recipient_session_id`; three-aspect columns in §3.7; `delivery_mode`; return policy fields `allow_auto_return`, `return_decision`, `return_text`; `project_id` (explicit user project context, RFC-15), `project_context` (rendered instructions prepended to the prompt), and `relation_type_name` (denormalized immutable relation type name captured upon assignment or redirection for analytics, RF-64).
- **`responses`:** unique `request_id`, `responder_id`, `content`, `implicit`, `created_at`.
- **`usage_samples`:** `id`, `provider`, `bucket_id`, `window`, `bucket_group`, `used_percent`, `resets_at`, `observed_at`, `created_at` (RF-64b). Index `idx_usage_samples_provider_observed` on `(provider, observed_at)`. Records provider quota samples with 400-day retention.
- **`request_events`:** per-request `actor`, `event`, `details`, `at` timeline.
- **`sessions`:** provider `id`, `agent_id`, `provider`, `entrypoint`, `cwd`, `title`, `tmux_session`, `state`, `turn_request_id`, `started_at`, `last_seen_at`, `waiter_seen_at`, `ended_at`, `activity_at` (turn activity), `pinned_at`, `model`, `node_id`, and suspension/exclusion metadata. Closed/suspended/reachable are derived using lifecycle observations, not simply an idle label.
- **`nodes`:** `id`, `name` (unique, case-insensitive), `provider`, `cwd`, `created_at` (RFC-02). A node's current session is its attached session seen most recently. The user names a session from the chat header, attaches it to an existing node of the same provider or detaches it. A Claude Code `SessionStart` with source `clear` takes the node of the conversation that ended within two minutes for the same agent, provider, folder and tmux session, unless the node already has an open conversation.
- **`relation_types`:** `id`, `name` (unique, case-insensitive), `description` (RFC-03). **`relations`:** `id`, `from_node`, `to_node`, `type_id`, `approval` (`manual` | `automatic`), `reply_approval` (`manual` | `automatic`, defaults to `approval`; whether a reply returns to the sender without review, RFC-16), unique by (from, to, type) (RFC-04). `requests.relation_id` records the relation a request travelled on and `requests.approval` whether it went `automatic` or `manual` (shown to the recipient, RFC-06), and `parent_request_id`/`chain_hops` the automatic chain it belongs to (RFC-14); the approval event's actor is `system:relation#<id>` when it was automatic. Deleting a node deletes its relations; a type in use cannot be deleted.
- **`projects`:** `id`, `name` (unique, case-insensitive), `description`, `instructions` (RFC-15), `relation_rule`, `max_auto_hops`, `max_auto_per_hour`, `created_at` (RFC-09, RF-44). Groups nodes and relations into workspaces with dedicated policies and instructions.
- **`project_nodes`:** composite primary key `(project_id, node_id)`, `instructions` (role of the node within this project, RFC-15). Nodes can belong to multiple projects.
- **`project_relations`:** composite primary key `(project_id, relation_id)` (RFC-09). Relations can belong to multiple projects. Adding a relation to a project automatically ensures its `from_node` and `to_node` are in `project_nodes`. Removing a node from a project removes its associated project relations. Deletions are cleaned up explicitly without relying on `PRAGMA foreign_keys`.
- **`agent_states`:** per-agent tmux state.
- **`app_settings`:** persistent key/value settings; `background_language` is human-controlled, independent of local UI preference; `relation_rule` is the general rule for automatic relations (RFC-08b); `automation_paused`, `max_auto_hops` and `max_auto_per_hour` hold Stop all and the loop limits (RFC-14); `notification_preferences` holds JSON-encoded notification preferences (`needs_you`, `problems`, `turn_end`), applied to all devices.

### Effective relation policy

For any relation, its effective policy combines global defaults and containing projects:
- **Effective rule:** The global relation rule always applies first if non-empty; project rules for each project containing the relation are appended sequentially, formatted as `[ProjectName]\n<rule>`.
- **Effective loop limits:** `max_hops` and `max_per_hour` are computed as the minimum across all projects containing the relation; a project specifying `NULL` inherits the global default. If a relation belongs to no projects, global limits apply. Delivery pause (`automation_paused`) is always global.
- **Agent exposure:** `/api/agents/me/relations` includes the list of containing `projects` and the composite `rule` for each outgoing relation.

### Project and node instructions (RFC-15)

Projects provide persistent, shared context and node-specific instructions:
- **Scope & Purpose:** Project and node instructions represent **behavioral and contextual guidance** for agents, not technical sandbox constraints or hard security boundaries.
- **Data model:**
  - `projects.instructions`: general project context, guidelines, and conventions (up to 4000 characters).
  - `project_nodes.instructions`: role and specific instructions for a node within the project (up to 4000 characters).
  - `requests.project_id`: the project ID associated with an explicit user prompt (NULL for relation requests).
  - `requests.project_context`: the rendered project instructions block prepended to the prompt envelope.
- **Composition & Ordering:**
  - Instructions are structured into distinct blocks:
    1. Project guidelines: `[Placet · Project <name>]` (or localized `Progetto`).
    2. Node role: `[Placet · Your role in <name>]` (or localized `Il tuo ruolo in`).
    3. Outgoing relations available to the node in that project, sorted deterministically (`relation_type.name` ASC, `node.name` ASC, `relation.id` ASC).
  - For user prompts, context is applied when explicitly selected via `#Project` hash mention in the composer or when entering the session via the project detail/diagram.
  - For inter-agent requests over relations, instructions are composed for all projects containing that relation.
- **Prompt Envelope:**
  When project context is present, the envelope formatted for delivery follows this strict sequence:
  1. Header line: `[Placet Request #N from <sender>]`
  2. Delivery note (if automatic or reviewed): `envelope.automatic` / `envelope.reviewed`
  3. Context header: `envelope.project_context`
  4. Formatted project blocks
  5. Divider line: `---`
  6. Prompt body
  Without project context, lines 3–5 are omitted.

This is a conceptual inventory; `backend/app/database.py` is the schema/migration source of truth.

### Planned additions

```text
sessions.transcript_path                                first-hook metadata for bridge
```

`context` removal remains planned (RF-05b); do not remove it from existing MCP calls yet. Relation delivery policy is visible to agents (RFC-06), with project-configurable relation/chain loop limits (RFC-14).

## 7. Agent configuration: `AGENTS.md`

YAML frontmatter defines agents, environment token references and technical parameters; Markdown descriptions are exposed to agents. Preserve the parser-compatible frontmatter.

```yaml
agents:
  claude:
    tmux_session: "agent-claude"
    token: "${PLACET_TOKEN_CLAUDE}"
    allowed_targets: ["claude", "gemini", "codex", "human"]
    idle_patterns: ['^\s*[│|]?\s*[>❯]\s*(Try ".*")?\s*[│|]?\s*$']
  codex:
    tmux_session: "agent-codex"
    token: "${PLACET_TOKEN_CODEX}"
    allowed_targets: ["claude", "gemini", "codex", "human"]
  gemini:
    tmux_session: "agent-gemini"
    token: "${PLACET_TOKEN_GEMINI}"
    allowed_targets: ["claude", "gemini", "codex", "human"]
rules:
  codex -> codex:
    require_approval: true
```

Illustrative excerpt only; use the complete repository registry for actual configuration. Same-provider requests are allowed, self-session delivery is not. Production reads `/etc/placet/AGENTS.md` via `PLACET_AGENTS_FILE`; ordinary deploy preserves it. Registry rule replacement by PWA-managed relations remains **planned** (RFC-10).

## 8. Security and access

1. **Network:** no public backend listener; phone access only over tailnet.
2. **Agents:** Bearer tokens, identity from token. An agent reads/cancels its own requests and answers requests addressed to it. `/api/agents` never exposes tokens.
3. **Human:** trust `Tailscale-User-Login` only on the Unix listener exposed through Tailscale Serve, only for `PLACET_ALLOWED_USERS`. Writing/creating sessions is remote PC control and human-only (RS-05).
4. **Process separation:** system user `placet`; root-owned `/opt/placet`, `/etc/placet` configuration, `/var/lib/placet` DB, private `/run/placet` socket directory. Membership in the Docker group is root-equivalent, not a substitute for this boundary.
5. **Bridge:** desktop user, restricted bridge token, on-demand registered-provider conversation reads. It cannot approve human requests.
6. **Private APIs:** adapter isolation/version checks; fallback only when explicitly supported/configured.
7. **Known limit:** agents share the desktop Linux user and can read one another's tokens/files. Tokens support attribution, not isolation.
8. **Least privilege target (RS-04):** proposers should not hold public/destructive-action credentials. This is not an established guarantee for the desktop user. The current privileged installer executes repository/dependency code as root; hardening is planned in [reserved commands](plans/reserved-commands.md).

### Session mentions in PWA chat

`@` opens a searchable picker of other sessions by provider, agent, project, title and ID. Multiple references can be included. Each retains its full `session_id` and visible token; removing token text removes that reference from submission. Message recipient stays the current session.

`POST /api/sessions/{id}/messages` accepts `mentions: [{session_id, token}]`. Backend checks token presence, session availability/exclusion and no self-reference. Routing permissions are checked on the agent's actual request, not the human's citation. Resolved references are stored in request context and injected as JSON separated by a blank line. No operational `placet_send_to_agent` instruction paragraph is appended: the agent interprets whether the user is citing or delegating.

Mentions do not create requests. The agent prepares each task under normal approval/delivery rules; if its tool is unavailable it must report that. Selection does not guarantee model compliance; the created Placet request is evidence of dispatch.

Verified October 18: citation #52 created no request; explicit task #53 became approved Codex→Codex request #54, completed with `TEST-COLLAB-OK`. This validates those cases, not every ambiguous prompt or provider. Full IDs prevent confusing similarly titled sessions.

### Phone-to-session attachments

Paperclip accepts JPEG/PNG/WebP/PDF, up to 4 files, 10 MiB/file and 20 MiB total. Attachment-only messages receive a generated read-files prompt. Images have thumbnails; files can be removed before sending.

PWA submits base64 `attachments: [{name, data}]` with text/mentions to `POST /api/sessions/{id}/messages`. HTTP body limit is 29 MiB before parsing, even without Content-Length. Backend validates bytes and sends an `upload` bridge job; bridge rechecks content, sizes and exclusion. Backend does not store bytes on disk. Create the approved request only after all saves succeed; a session closed/excluded during transfer receives no message.

Bridge saves privately under `~/.cache/placet/uploads/<session-hash>/<content-hash>.<type>`. Phone names/IDs never become paths. Identical files are reused. Total storage limit 256 MiB; cleanup is currently manual, avoiding automatic deletion of files needed by queued requests. Interrupted transfers can leave unreferenced files.

Request metadata retains filenames/local paths in recipient context. Agent opens files with its own tools; this is not a simulated native provider attachment. Provider filesystem permissions still apply.

If bridge is offline/times out, no message is queued. PWA retains current-page composer text/files for retry, not persistent drafts across reload/chat change/closing. Lost HTTP confirmation after request creation has the same uncertainty as text messages: file deduplication does not make request creation idempotent.

### Collaboration return approval

Initial approval authorizes **outbound delivery only** by default. Task completion remains `COMPLETED`, while response forwarding waits for the supervisor. PWA shows original response, permits editing, and offers Forward response/Withhold. Human-only decision is audited and single-use; withholding does not undo completed work.

Initial approval may explicitly allow automatic return for **that request only**, default off. Explicit/implicit replies share the rule. Bridge, hook inboxes and agent status reads withhold response content before authorization. On editing, original remains in human history and only approved text returns. Completion metadata remains available.

Direct human decisions and technical outcomes without response content (rejection, expiration, failure) need no second approval. Migration does not resend already-delivered responses; completed but undelivered inter-agent replies require review. This protects Placet channels, not processes sharing a Linux user that can read one another's files.

### Reserved commands from the phone (planned)

Future Commands screen exposes only administrator-enabled human-reserved actions, each authorized with a passkey. Ordinary autonomous commits/tests/builds gain no new approval. Chat proposals open the same protected confirmation; shell text is not authorization. Catalog/executor/confirmation are separate from application deployment; backend cannot independently authorize privileged execution.

[Plan, trust boundaries, phases and acceptance checks](plans/reserved-commands.md): isolated deployment first, independent passkey confirmation second, PWA controls and chat proposals afterward. Not installed or enabled by this documentation work.

## 9. Usage and session metadata

The bridge reports normalized 5-hour/weekly windows to `POST /api/bridge/usage`; the human-only `GET /api/usage` and `usage_update` SSE feed the Home usage bar. Claude uses its undocumented OAuth usage endpoint every five minutes, sending Claude Code's existing token only to Anthropic and never refreshing it. Codex reads the latest rollout limits; Antigravity queries its private quota summary, grouped by model family. Local readers run approximately once per minute. Missing data is unavailable, not zero usage.

Account-wide 100% windows with future timezone-aware reset timestamps hold provider delivery until reset. Per-model-group limits do not hold an entire provider. These are observations, not a guarantee that the provider will accept the next request.

Newest transcript pages carry optional model/effort/context metadata for Claude and Codex; Antigravity model comes from hook `modelName`. Unreadable optional metadata does not prevent chat reads. Claude context-window size is not inferred; unavailable values stay absent. Session changes clear previous metadata. Provider model IDs and effort values remain provider data, while surrounding labels and numbers follow the interface locale.

### Usage history recording and retention (RF-64b)

The bridge reports quota windows to `POST /api/bridge/usage`, and Claude manual renew calls `/api/usage/claude/renew`. To track utilization over time without unbounded growth, samples are recorded in `usage_samples` (`provider`, `bucket_id`, `window`, `bucket_group`, `used_percent`, `resets_at`, `observed_at`) under four criteria:
1. Significant change in `used_percent` compared to the previous recorded sample for that bucket.
2. The turn boundaries of any active conversation (first sample after `executing_since` or `completed_at`).
3. Transition across the configured usage guard threshold.
4. Periodic heartbeat (recorded at least once every 30 minutes even without changes).

Samples older than 400 days are automatically purged in the background, throttled to execute at most once per hour.

### Statistics API and Dashboard (RF-64)

The human supervisor can inspect aggregated usage, automation efficiency, and collaboration metrics from Settings → Records → Statistics (`#/statistiche`).

- **Backend Aggregation (`GET /api/stats`):** Accepts `from` (YYYY-MM-DD), `to` (YYYY-MM-DD), and `tz` (IANA time zone name) parameters. Aggregates:
  - Total requests, automation rate (`automatic` / `total`), mean reply time in seconds, and days exceeding provider quota thresholds.
  - Request volume and mean reply time grouped per day in the client timezone.
  - Distribution by relation type using `requests.relation_type_name`, ensuring historical requests remain correctly classified even if relations or types are edited or deleted.
  - Distribution by project and by sender/recipient provider.
  - Decision outcomes (approved, edited, rejected, expired, cancelled) and human user messages (requests sent vs. direct replies).
  - Daily peak quota percentages per provider window from `usage_samples`, annotated with threshold crossing alerts.
- **Frontend Presentation:** Implemented with lightweight SVG charts and responsive CSS grids in `frontend/src/Statistics.jsx`. Supports period presets (`today`, `7d` default, `30d`, and `custom` date inputs), responsive down to 375 px mobile viewports, keyboard accessibility, and full localization.
