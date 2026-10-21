# Placet application requirements

> **Version:** 2.1, 2026-10-16. **Baseline:** consolidated after the multi-provider laboratory and two Codex reviews.
>
> **Sources:** ChatGPT discussion on 2026-10-09; Claude Code sessions on October 10–16; `/grill-me` interview on October 14; discussion and laboratory on October 15–16 ([results](experiments/provider-integration.md)).
>
> **Documentation migration, 2026-10-18:** requirement IDs and the original baseline are preserved. Later implementation notes below distinguish subsequent changes from the baseline. See [architecture](architecture.md), [provider integration record](experiments/2026-10-18-session-integration.md) and [localization](localization.md) for newer behavior. A status in this document is not a guarantee about an installed deployment or every provider version.

Each requirement has a status:

- **[Implemented]:** present in the code and verified within the stated scope.
- **[Decided]:** agreed and confirmed, not yet implemented.
- **[Superseded]:** replaced by the identified newer requirement.
- **[Future]:** planned for a later version.
- **[Planned]:** agreed with the maintainer and scheduled, design still open.
- **[Known limit]:** an accepted limitation of the current design, stated so it is not mistaken for a guarantee.

## 1. Purpose

Placet is a **single point for human supervision and AI-agent cooperation** and, since v2, a **phone interface for following and directing your sessions**.

1. **Human-in-the-loop supervision:** agents from any provider ask for decisions and approvals; you answer from your phone. Relevant external actions and public messages require your consent.
2. **Controlled cooperation across sessions:** sessions from Claude Code, Codex and Antigravity/Gemini (and eventually other providers) exchange prompts and tasks through **relations** you define. Each relation specifies human review (`manual`) or direct delivery (`automatic`). Replies return to the calling session subject to the return policy.
3. **Sessions on your phone:** read a conversation, answer its questions within it, write unsolicited messages and wake the session. Continue on the PC or direct work remotely.

Problems addressed:

- Provider mobile apps do not expose all sessions running on the PC or sessions from other providers.
- Providers operate in silos; collaboration otherwise requires copying and pasting.
- Placet combines mobile access, inter-agent messaging and session visibility through MCP and REST.

**Provider priority:** Claude Code (desktop and CLI), Codex (desktop and CLI), Antigravity 2.0 (app and `agy` CLI). Claude Cowork/Projects and ChatGPT Work are desirable but lower priority [Future].

## 2. Actors

| Actor | Role |
| --- | --- |
| **User** | Receives phone notifications, reads sessions, approves or edits requests, writes to sessions, defines projects and relations. |
| **Sessions** | Provider conversations in desktop apps or CLIs. Send and receive requests; each belongs to an agent (`claude`, `codex`, `gemini`). |
| **Node** | A stable named Placet session (name, provider, folder) to which a real session attaches; survives `/clear` and reopening. |
| **Placet backend** | FastAPI and SQLite over Tailscale, with Web Push notifications (self-hosted ntfy optional); source of truth and coordinator. Never reads provider conversation files in production. |
| **Placet MCP server** | Tools exposed to each agent through stdio, with identity derived from its token. |
| **Local bridge** | User daemon on the PC: delivers through provider channels, reads conversations on demand, collects usage and context. |
| **Executor** | Separate service performing external actions, such as publishing, only after approval [Future]. |

## 3. Functional requirements

### 3.1 Agent requests

| ID | Requirement | Status |
| --- | --- | --- |
| RF-01 | Agents contact Placet **through MCP or REST**, never by writing to its database. | [Implemented] |
| RF-02 | Human questions support **single choice, multiple choice, confirmation and prompt review**. | [Implemented] |
| RF-03 | Every choice question always includes a free-text **Other** option. | [Implemented] |
| RF-04 | MCP tools: `placet_ask_human`, `placet_send_to_agent`, `placet_reply`, `placet_ack`, `placet_list_agents`, `placet_check_status`. | [Implemented] |
| RF-05 | Each request shows sender (including project/session origin), recipient, prompt and options. | [Implemented] |
| RF-05b | `context` becomes **unnecessary** because context can be read in the sender's chat (RF-30). Remove it from `placet_send_to_agent`; make it optional for `placet_ask_human`. | [Implemented] October 18, 2026: removed from `placet_send_to_agent`, optional for `placet_ask_human`; the API still accepts it from older clients. |
| RF-06 | An agent can cancel a pending request. | [Implemented] |
| RF-07 | Configurable expiration and notification priority. | [Implemented] |
| RF-08 | A prompt for another agent may come from a **Markdown file** (`prompt_file`); the PWA renders it and allows text editing. | [Implemented] |
| RF-09 | Human questions have a **server-enforced minimum wait**; agents cannot choose a few seconds. On timeout the tool explains that the result will arrive through a wakeup. | [Implemented], minimum 60 seconds; `note` in the PENDING result. |

### 3.2 Session communication: projects, relations and types

| ID | Requirement | Status |
| --- | --- | --- |
| RFC-01 | Agents address a specific **session** (node), not merely a provider. | [Implemented] for sessions; [Decided] for nodes. |
| RFC-02 | Sessions have **stable named nodes** (name, provider, folder). Real sessions attach and reattach after `/clear` or reopening. | [Implemented] October 18, 2026: naming, attach/detach and rename from the chat header; automatic reattach after Claude Code `/clear`. Reopening keeps the same session id, so the node stays. Other providers' equivalents of `/clear` attach by hand. |
| RFC-03 | Reusable **relation types** (ID, name, description); the AI reads the description to understand when and why to contact that session. | [Implemented] October 18, 2026: types managed from the PWA; agents will read descriptions in phase 3, step 3. |
| RFC-04 | **Global relations** A→B have a type and `manual` or `automatic` approval. Unique by (A, B, type); several types may connect the same sessions. | [Implemented] October 18, 2026: relations between nodes; the request is matched on the sender's and recipient's nodes, and on the type when several connect them. |
| RFC-05 | `manual`: user approves, edits or rejects. `automatic`: prompt goes directly to B. | [Implemented] October 18, 2026. The sender session must be known when the request is created: the MCP tool passes the calling `session_id`, and without it the backend uses the agent's only busy session. October 19, 2026: a `target_node` send without an identifiable sender or a matching relation is refused (409 `relation.sender_unknown` / `relation.not_found`) instead of falling back to `manual`; an ad hoc `target_agent` send without a known sender is still reviewed. |
| RFC-06 | **Agents see the delivery policy**: automatic delivery without human review, or human review required with possible editing/rejection. Recipients see sender and review/edit provenance. Delivery approval does not authorize everything requested in the prompt. | [Implemented] October 18, 2026: the send result reports `delivery_policy`; the recipient's preamble says whether the user reviewed (or edited) the prompt or it arrived automatically, and that delivery does not authorize everything asked. |
| RFC-07 | Without a relation, delivery is always `manual`. **Ad hoc requests**, including those prompted by phone `@` mentions (RF-39), remain available. A **Create relation** action on the request card lets the user choose type and policy; the current request proceeds normally and the relation is available for future requests. | [Implemented] October 18, 2026, including **Create relation** on the request card (names missing nodes inline). |
| RFC-07b | **Change recipient on approval:** when approving an agent's prompt, the user may send it to any open session, including another agent's (e.g. a report meant for `@y` goes to `z`). The relation is re-evaluated for the new pair; the reply still returns to the sender; the audit records a `redirected` event. | [Implemented] October 18, 2026. A reply awaiting approval can also be forwarded to another open session: it becomes a new, already approved prompt from the replying agent; the original sender gets only a notice that the reply went elsewhere (`return_decision = forwarded`). |
| RF-39 | **Mentions:** `@` in the composer references other sessions. The agent receives references as data and prepares `placet_send_to_agent` requests under ordinary approval rules. A mention alone sends nothing. | [Implemented] |
| RFC-08 | Agent API: `send(target, type_id, prompt)`; a tool lists outgoing relations with destination, type, description and delivery policy. | [Implemented] October 18, 2026: `placet_send_to_agent(target_node, relation_type, prompt)` and `placet_list_relations`. A relation also opens a route `AGENTS.md` does not list. |
| RFC-08b | **General rule for automatic relations:** a text the user writes in the PWA. On every send over an automatic relation, the first call delivers nothing and returns the effective rule (the global rule, followed by each project rule formatted as `[ProjectName]\n<rule>`); the agent sends again with `rule_acknowledged` only if the rule does not tell it to stop or ask first. Placet guarantees the rule is shown each time, not that it is followed: loop limits (RFC-14) remain the safety net. Global and per-project (RFC-09); `/api/agents/me/relations` returns containing projects and the effective rule. | [Implemented] October 18, 2026; per-project rules October 19, 2026. |
| RFC-13 | **Replies** follow the reverse relation's policy: approving A→B does not approve B→A. | [Superseded] by RFC-16 (October 21, 2026): the reply policy is now a setting of the relation itself; the reverse relation no longer decides it. |
| RFC-14 | **Loop limits:** at most 3 consecutive automatic hops per chain before asking the user, and 20 automatic messages/hour/relation. Project settings can override defaults; the effective limit is the minimum among containing projects, falling back to global defaults when null. PWA **Stop all** action. | [Implemented] October 18, 2026; per-project limits October 19, 2026. A chain links a request to the request whose turn sent it; a question the user answered for the session during that turn resets the count. Over a limit, or while paused, the prompt is created but waits for review, and the sender is told not to resend. **Stop all** pauses automatic delivery and returns automatic prompts not yet taken for delivery to review; prompts already being delivered or running are not interrupted. Replies returned automatically do not carry the chain: the hourly limit bounds that case. **Usage guard** (October 19, 2026, Settings, off by default): when a provider's 5h window reaches a threshold (default 90%) or its 7d window another (default 95%), every automatic relation with a session of that provider at either end needs review, replies included, until usage goes down; relations are not modified. Model groups count; stale readings of polled providers do not, so for Claude it requires automatic login renewal, offered next to it. The Home automation bar names the providers under review. |
| RFC-16 | **Reply policy and duplicate replies:** each relation gets two settings, outbound (manual/automatic) and reply: a toggle "automatic reply" / "reply with approval" that defaults to the outbound setting (automatic relations reply automatically, manual relations reply with approval), for new relations and when existing ones are migrated, and that the user can switch per relation; both are shown in the relation form, and the graph marks relations whose replies need approval. The usage guard still holds replies for review when a provider is near its limit; the reply no longer depends on the reverse relation, which carries only new tasks. A reply to a request always goes through `placet_reply`; a send from B to A over a relation while B still has an open request received from A is refused (409, "reply with placet_reply to #N") and sends nothing. | **Part 1, reply policy: [Implemented] October 21, 2026.** `relations.reply_approval`, migrated equal to `approval`; set in the relation forms (an "automatic replies" toggle that follows the outbound choice until changed) and per relation in the lists; the graph marks relations whose replies need approval with a ring at the sender's end; `placet_list_relations` reports it as `reply_policy`; the usage guard still holds replies back. **The rest: [Planned].** Decided with the maintainer on October 19, 2026, after the first live test of an automatic relation (#150), whose reply needed approval because the reverse relation is manual. To be done after RFC-15. Observed during RFC-15 (#152–#159): the implementing agent reported every step as a new message over the reverse automatic relation instead of `placet_reply`; such messages start a new chain (`parent_request_id` empty, hop 1), so the hop limit never applies, and replies to them reach the agent only after its turn ends. The guard must also link these sends to the open request's chain. Decided with the maintainer on October 20, 2026: the reply default follows the outbound setting and the reply no longer depends on the reverse relation (today a reply needs approval whenever the reverse relation has a different type, e.g. Developer out, Reviewer back). Also in scope, from review #191: a turn end or silence never marks a collaboration COMPLETED by itself (Antigravity fires `Stop` between internal steps, and the adapter drops later invocations); only `placet_reply` concludes, a `Stop` yields an observed, provisional state, and the queue is not released while the provider is still active. Progress updates that do not close the request; states for "waiting for you in the provider app" and "cancelled in the app" when the adapter can tell. First record the real Antigravity event sequence in the lab. Node instructions: one request is one task with one concluding `placet_reply`; plans, progress and single parts never close it. |
| RFC-17 | **Project view for coordinator nodes:** a read-only MCP tool lets an Architect node (plans who does what) and a Project Manager node (assigns and steers work) see their project: nodes with a public role written by the human, relations (type, description, policy) and, per node, session, reachability and busy/idle state; open requests attributed explicitly to that project, as metadata only (IDs, participants, relation, chain root/parent, separate approval/delivery/execution/reply states and times, blocking reason codes), never prompts, replies, project context or summaries. The grant is per project–node pair, off by default, set and revoked only by the human and checked on every read; it requires a verified caller–session–node binding first, because today `session_id` is declared by the caller and checked only against the provider token. The view is a dedicated projection: no private node instructions, no `shared_with` or other projects' names, transcripts, tokens or attachments; a node shared with another project shows only a generic unavailable state. Text in the view is labelled with its origin and treated as data, never injected as instructions. Reading a relation does not authorize using it: sends recheck relation, grant, policy, limits and approvals. The coordinator's loop has an operating budget, an expiry and a human stop. Results are paginated and carry `generated_at` and the last observation time, with an explicit unknown state. | [Planned] Proposed by the maintainer on October 20, 2026; reviewed by the second reviewer (#181). Order: RFC-16 and chain tests, then the identity/grant/projection contract, a minimal overview, a trial with one PM and a few nodes, then the work-area registry (RFC-17b). Required tests: same-provider node impersonation, grant revocation, nodes and relations shared across projects, unapproved requests, stale state, a read grant that does not authorize sends, concurrent assignments. |
| RFC-17b | **Work-area registry against collisions:** when assigning a task the coordinator declares the resources it touches (repository or worktree ID, normalized relative paths, read or write, shared databases, ports, deploys and services), stored with the request ID and state and compared atomically at assignment; Placet warns on overlaps. Advisory and cooperative: busy/idle is not a lock, agents may touch undeclared files, and no hard lock is promised without an enforcer. A queued or approved task differs from a running one, and a timeout or the end of a turn never frees a resource whose outcome is uncertain. Parallel tasks should use separate worktrees and branches, with one owner of integration. | [Planned] After RFC-17. |
| RFC-09 | **Projects** group nodes and relations; both can belong to multiple projects. Reusing an existing A→B type warns that the relation is shared and allows association. Each project can define its own rule (appended to the global rule) and anti-loop limits. | [Implemented] October 19, 2026. |
| RFC-15 | **Project and node instructions:** each project can configure general project instructions (`projects.instructions`), and each node within a project can configure node-specific role instructions (`project_nodes.instructions`). Crucially, these instructions represent behavioral and contextual guidelines for agents, not technical sandbox constraints or hard security boundaries. When a message is sent with project context—explicitly selected via `#Project` mention in the composer, or by clicking a node in the project detail/diagram—Placet formats and prepends the project context in the prompt envelope. For inter-agent requests over relations, the instructions of all projects containing the relation are composed and delivered deterministically. In the PWA, active project context is shown as a header chip (removable), and delivered instructions appear as an expandable preview above outgoing messages. | [Implemented] October 19, 2026. |
| RFC-10 | Relations replace per-agent-pair rules in `AGENTS.md`; the file keeps agent definitions, tokens and descriptions. | [Decided]. Until migrated, an `AGENTS.md` rule without approval still auto-approves; relations only add automatic delivery. |
| RFC-11 | B receives **only the prompt**, with a preamble whose origin can be verified using a Placet tool. | [Implemented] prompt-only delivery; [Decided] verifiable-origin protocol. |
| RFC-12 | Sessions never receive their own requests, and a request for one session is never delivered to another. | [Implemented] |

### 3.3 Delivery and wakeup

| ID | Requirement | Status |
| --- | --- | --- |
| RF-20 | Long-polling with asynchronous fallback: tool waits, then returns `PENDING` and `request_id`. | [Implemented] |
| RF-21 | **Waiting callers are woken** when an authorized result arrives, using their provider channel (RF-24); do not depend on a tool still waiting. | [Implemented] for Claude waiter and Codex/Antigravity bridge, subject to provider reachability; generalizes RF-22. |
| RF-22 | On completion the result is delivered to the calling session, subject to return approval. | [Implemented] |
| RF-23 | Requests survive agent, bridge and backend restarts through SQLite. | [Implemented] |
| RF-24 | **Per-provider channels:** Claude hooks and asynchronous waiter; Codex `codex queue`; Antigravity language-server API; CLI tmux `send-keys` fallback. See [architecture §3](architecture.md#3-delivery-and-wakeup). | [Implemented] |
| RF-25 | Acknowledged delivery and response collection without `placet_reply`, using the turn's final message as an implicit response. | [Implemented]: Claude/Codex Stop hooks; Antigravity conversation file. |
| RF-26 | Delivery does not depend on an agent session process: bridge runs as a service; dead waiters are detected and replaced. | [Implemented] detection/warning after 2 minutes of waiting for Claude without a waiter; replacement requires a session hook. Desktop recovery details in [Claude wake](providers/claude-wake.md). |
| RF-27 | Detect and report a **missing MCP connection**, both in the session and PWA. | [Decided] |
| RF-28 | **Local event spool:** hooks write files and exit without calling the backend. Bridge forwards with retries; backend downtime does not lose events, except disk exhaustion or power loss. Turn overhead is limited to starting the hook script. | [Implemented] since the baseline; [architecture §3.6](architecture.md#36-local-event-spool). |
| RF-29 | **Three aspects**, each with evidence/timestamps: delivery (waiting, delivering, accepted, uncertain, failed, cancelled); execution (unobserved, started, completed, interrupted); reply (none, explicit, implicit, ambiguous). Uncertain delivery is not automatically retried; PWA offers Retry/Cancel. | [Implemented] |
| RF-29b | **One request per session until conclusion**: observed turn end or explicit user decision (“Confirm finished”, “Queue the next anyway”). Time only warns. Direct app messages remain possible; mixed turns produce ambiguous implicit replies. **Now** is the explicit exception. | [Implemented] |
| RF-29c | **Suspended sessions and retractable messages:** desktop Codex `SessionEnd` means unloaded (idle/restart/update), not permanently closed. Keep it listed/readable as **Suspended**; only a start or turn reactivates it, not delayed events. Messages still held by Placet can be cancelled or edited (cancel and restore text/mentions; reselect attachments). Once accepted by Codex, show “Queued in Codex; execution not observed”; retraction is not guaranteed. Never use `codex exec resume` as a headless writer on the desktop thread without exclusive ownership. | [Implemented]. The older wait-for-manual-reopen design was superseded on October 18: queue once, then request native-link opening for suspended sessions; see [Codex wake](providers/codex-wake.md). |
| RF-29d | **Continued conversations:** when a Claude Code conversation runs out of context, the desktop app continues it in a new session (new ID, same title, first message referring to the previous transcript) while showing one conversation. Placet should recognize the link, close the previous session as continued, move its Home pin and node to the new one, and keep the old one readable in history. Separately, sessions sharing a title must be told apart (ID prefix or last activity) in Home and in the `@` picker. | [Implemented] October 19, 2026: detected on the first prompt of a new session; closes the previous session as continued, transfers pin and node, disambiguates duplicate titles with `[ID]`. Note: live verification with the desktop app is still required to confirm whether `UserPromptSubmit` forwards the continuation summary in all cases. **Open bug (October 19, 2026), node transfer only; sender identification is fixed (see below):** in live use the node was not transferred: after a context continuation the maintainer found the node and its relations gone from the active session and had to reassign it by hand; afterwards `placet_list_relations` with this session's ID still reported no node. To investigate: whether the continuation was detected, which session ID received the node, and how the MCP server identifies the calling session. Related fix (October 19, 2026): with several busy sessions of the same agent the backend could not tell the sender, so `target_node` silently became an ad hoc, manual request; now `placet_send_to_agent` accepts `session_id`, and addressing a node without an identifiable sender or without a matching relation is refused (409 `relation.sender_unknown` / `relation.not_found`) and sends nothing. |

### 3.4 Sessions on the phone

| ID | Requirement | Status |
| --- | --- | --- |
| RF-30 | PWA displays the **conversation**, opening at the latest message/question; scroll upward for earlier pages. | [Implemented] |
| RF-31 | **On-demand reads through the bridge**; no backend copy of provider conversations. | [Implemented] for Claude, Codex and Antigravity. |
| RF-32 | **Privacy:** show user messages and assistant text; collapse actions to a line (for example, “ran `cmd`”). Hide tool results and internal reasoning. Display compaction separators. | [Implemented] |
| RF-33 | Answer a question **inside the conversation**. | [Implemented] |
| RF-33b | **Unsent drafts:** text typed in the chat composer and unsaved project or node instructions stay on the device (per chat, per project) until sent or saved, so a live refresh, a notification or another screen does not lose them; drafts older than seven days are dropped. Attachments must be picked again. | [Implemented] October 20, 2026, after the maintainer lost unsaved instructions to the project page's live refresh. |
| RF-34 | **Write to a session** without a pending question; wake it as if typing on the PC. Show the message as the user's in the PWA. | [Implemented], within provider wake limits. |
| RF-35 | While working: **Queue** (after the turn) or **Now** (within the turn after the next action). Configurable default, overridable per message. | [Implemented] for Claude PostToolUse and Antigravity `NEXT_INVOCATION` (both verified live; Antigravity on October 19, 2026). Codex Now unavailable. If the turn ends first, return to Queue. Default is the device's last selection. |
| RF-36 | Launch **skills** from the phone, including desktop sessions. `/` lists provider skills/commands with skill highlighting. | [Decided] for picker (useful, optional); Claude desktop skill invocation verified in lab. |
| RF-37 | Show **PC unreachable** with retry when offline. | [Implemented] |
| RF-38 | **Conversation attachments:** thumbnails/fullscreen/download for pasted and generated images. Phone sends IDs, never file paths. Only adapter-recognized app/conversation files, real JPEG/PNG/WebP, bounded sizes. Arbitrary paths stay text; images from other tool outputs (such as browser screenshots) remain hidden under RF-32. | [Implemented], including phone uploads since the baseline. |
| RF-38b | **Agent-shared files:** `placet_share_file(path, title)` presents generated PDFs/images in the conversation. Must be in the user's home, outside hidden folders and excluded projects, regular user-owned files; valid PDF up to 20 MB or permitted image. Tool immediately reports refusal. | [Implemented] |

### 3.5 Home, session list and notifications

| ID | Requirement | Status |
| --- | --- | --- |
| RF-10 | Installable phone **PWA**, served through Tailscale. | [Implemented] |
| RF-40 | **Home:** open questions first, then **pinned sessions** ordered by activity, with most recently finished near the top. | [Implemented] |
| RF-41 | **Add / view all:** choose a provider and select sessions to pin; others stay off Home. | [Implemented] per session; nodes in phase 3. Closed sessions remain removable from selection. |
| RF-42 | Sessions that **never used Placet** still register through global hooks, except excluded projects (RF-45). | [Implemented], `hooks/install.py`. |
| RF-43 | Two notification types: **question** (high priority, sound) and **turn ended** (quiet, pinned sessions only). Title includes provider/session; tap opens session. | [Implemented], at most one turn-end notification/minute/session. |
| RF-43b | **Notification preferences:** Settings allow filtering notifications by type: requests that need you (`needs_you`, questions, prompt reviews, return approvals), problem alerts (`problems`, delivery failures/uncertainty, stalled work, held queues, unreachable agents), and turn-end notifications (`turn_end`: all, only Placet work, or off). Test notifications are never filtered. Applied globally across devices. | [Implemented] 2026-10-20 |
| RF-11 | Notifications with only **Open**; decide in the PWA with full text. | [Implemented]: Web Push to the installed PWA is the default and needs no server; self-hosted ntfy on the tailnet remains optional, for devices without Web Push. |
| RF-12 | Edit a prompt before approval; sender is informed of the edit. | [Implemented] |
| RF-13 | Complete history/audit with per-request timeline. | [Implemented] |
| RF-45 | **Present but quiet:** sessions register, then act only when Placet messages arrive, returning to silence after the turn. `.placet-off` excludes a folder and descendants across hooks, spool, reads, delivery and PWA. Undelivered messages are cancelled with a reason; stored data is **hidden**, retained for audit and shown again if exclusion is removed. | [Implemented] |
| RF-44 | **Per-project graph:** nodes and typed directed edges; manual/automatic policy visible to the user. Editor for projects, nodes, relations and types. | [Implemented] October 19, 2026 (first version: Projects PWA view with project memberships, per-project rules and limits, and relation types management). The project view draws its relations as a read-only diagram (solid green automatic, dashed manual), beside the project in landscape and on wide screens, at the bottom in portrait; editing stays in the lists. Live activity (October 19, 2026): nodes whose session is working pulse; a ball runs along a relation while a prompt is delivered (sender to recipient) and back while an automatic reply returns; the edge is lit while the recipient executes; a yellow ball stops halfway, with a legend entry, while the chain waits for the user (prompt to review or reply to approve). Updated from the live event stream; still under `prefers-reduced-motion`. |

### 3.6 Creating sessions

| ID | Requirement | Status |
| --- | --- | --- |
| RF-50 | Create a session from the PWA by choosing provider, existing/new folder, model and effort. | [Decided] |
| RF-51 | Placet-created sessions are **tmux CLIs with Remote Control**: genuine `/compact`/`/effort`, resumable through desktop, phone and web. | [Decided]; verified for Claude. |
| RF-52 | **Temporary sessions:** work in the originating session's folder with a chosen provider (such as Codex reviewing Claude), return results, close automatically, can be deleted. | [Decided] |
| RF-53 | Creating a session in a **new folder** requires confirmation. | [Decided] |

### 3.7 Usage, context and controls

| ID | Requirement | Status |
| --- | --- | --- |
| RF-60 | **Home usage bar**, one row/provider, e.g. `Gemini 5h 42% · reset 1h20 — 7d 60% · reset 3d5h`. | [Implemented] Codex (latest rollout `rate_limits`) and Antigravity (`RetrieveUserQuotaSummary`, one row per model group), read by the bridge every minute; Claude from Anthropic's usage endpoint used by `/usage`, with Claude Code's OAuth token (sent only to Anthropic, never refreshed by Placet; every 5 minutes; undocumented). While an account-wide window is used up, messages to that provider wait in Placet until the reset (a turn started at the limit fails and the app does not retry it); per-model-group limits (Antigravity) do not hold messages Claude's token in `~/.claude/.credentials.json` is renewed only by the Claude Code CLI; the desktop app authenticates another way, so with desktop-only use it expires and the reading stops. Polled providers older than 20 minutes are marked stale; a window whose reset passed meanwhile shows an unknown value, not 0%. |
| RF-61 | Session shows **context** (`72.7k / 1M · 7%`), model and effort. | [Implemented] Claude (model, effort, context tokens from the transcript; window size not known), Codex (model, effort, context and window from the rollout), Antigravity (model from the hooks' `modelName`) |
| RF-62 | Remote **compact/effort** where supported (tmux CLI; Antigravity needs verification; Codex: `codex queue` ignores `-m`/`-c`, see [experiment](experiments/2026-10-18-codex-effort.md)). Claude desktop: “available only on PC”. | [Decided] |
| RF-63 | Model switching is not planned: rereading context consumes usage. | [Decided] |
| RF-64 | **Statistics in Settings:** requests per day, week, month or custom range; by relation type, project, node and provider; automatic versus manual; approved, edited or rejected; mean time to reply. Computed by the backend in one call from existing requests, so history is available backwards. Each request stores its relation type name, so statistics survive a deleted relation. | [Implemented] October 20, 2026: `GET /api/stats` (human only, local-day buckets, at most 400 days) and Settings → Statistics (`#/statistiche`). The response shape is documented in the architecture. |
| RF-64b | **Usage history:** the backend records each provider usage reading over time (today it keeps only the latest), so Statistics can show daily peaks per window, usage-guard threshold crossings and how long automatic relations were held back. History starts when recording ships; nothing earlier can be recovered, so recording comes before the charts. | [Implemented] October 20, 2026: `usage_samples` records a reading when a bucket moved by at least one point, its reset changed or 10 minutes passed, with canonical UTC timestamps and 400-day retention. Recording starts with this version. |

### Later implementation: collaboration return approval and localization

Approving a collaboration permits **outbound delivery only** by default. A completed task stays `COMPLETED`, but its explicit/implicit response is withheld until human approval. The PWA allows forwarding, editing or withholding it; the original remains in the audit history. Initial approval may explicitly allow automatic return **for that request only**. Direct human decisions and technical outcomes without response content do not need a second approval. This implements a per-request boundary while typed relations (RFC-13) remain future work. See [return approval](architecture.md#collaboration-return-approval).

The interface supports English/Italian, English fallback and a local browser preference. Background notifications and generated Placet envelopes have a separate shared human-only setting. User content is not translated. See [localization](localization.md) for the implemented scope and known limitations.

## 4. Security requirements

| ID | Requirement | Status |
| --- | --- | --- |
| RS-01 | No external action or inter-session forwarding without manual authorization or an explicitly user-created `automatic` relation. | [Decided] for relations; manual request/return enforcement implemented. |
| RS-02 | Backend/PWA reachable **only over tailnet**, no public IP exposure. | [Implemented] |
| RS-03 | Agent identity comes from `PLACET_API_TOKEN`, never request body. | [Implemented] |
| RS-04 | **Proposer is not executor:** agents do not hold credentials for public/destructive actions. | [Decided] target boundary, not a guarantee for a shared desktop user; see [reserved commands plan](plans/reserved-commands.md). |
| RS-05 | **Writing to or creating a session is remote PC control**, allowed only to the authorized Tailscale identity. | [Decided] overall; existing phone writes enforce the human channel. |
| RS-06 | Bridge reads only registered providers' conversation files (`~/.claude/projects`, `~/.codex/sessions`, `~/.gemini/antigravity/brain`), never a backend-supplied file path. | [Implemented] |
| RS-07 | **Undocumented APIs** (Antigravity language server) are isolated behind version-aware adapters, with tmux/CLI as an alternative channel where configured. | [Implemented], `bridge/channels.py`; not a guarantee of automatic failover to a different session. |
| RS-08 | Use `automatic` relations for low-risk exchanges; all remain visible in history. | [Decided] |
| RS-09 | **Session identity is declared, not proven:** the API token identifies the provider; an agent's `session_id` (sender, relation listing) is checked only against that provider, so a session could claim another session of the same provider. Accepted for routing between the user's own agents; any per-node permission (RFC-17) needs a verified caller–session–node binding first. Agents under the same UID can also read each other's credentials, so this remains application-level separation, not a barrier against a hostile agent. | [Known limit] October 20, 2026, from review #181. |

## 5. Technical decisions and stack

| Component | Technology |
| --- | --- |
| Backend/API | Python 3.11+, FastAPI, SQLite WAL, SSE |
| MCP server | Official Python `mcp` SDK over stdio |
| Local bridge | Python daemon, systemd user service, provider adapters for delivery/reads/usage/commands |
| Hooks | Claude `~/.claude/settings.json`, Codex `~/.codex/hooks.json`, Antigravity `~/.gemini/config/hooks.json`; project configuration was also used in the laboratory |
| Notifications | Web Push to the PWA (self-hosted ntfy optional); Open action only |
| Frontend | React/Vite, `react-markdown` with raw HTML ignored; `react-i18next` and `Intl` |
| Network | Tailscale Serve HTTPS to the backend Unix socket |
| Agent registry | `AGENTS.md` definitions/tokens/descriptions; project/node/relation database management is planned |
| Laboratory | `~/Projects/placet-hook-lab`; [experiment record](experiments/provider-integration.md) |

## 6. Roadmap

| Phase | Scope |
| --- | --- |
| 1 | **Laboratory**, completed October 15–16; [results](experiments/provider-integration.md). |
| 2b | **Reliability:** local spool (RF-28), three request aspects/one request per session (RF-29), exclusions throughout (RF-45). |
| 2 | **Phone sessions:** chat (RF-30…33), writes/wakeup (RF-34/35), Home/questions/pinned sessions (RF-40…43), provider bridge adapters (RF-24…27). |
| 3 | **Nodes, relations and types** (RFC-02…10), `send(target, type_id, prompt)`, removal of `context` (RF-05b). Completed October 18, 2026, with change of recipient (RFC-07b), the general rule (RFC-08b), loop limits (RFC-14), projects (RFC-09, October 19, 2026) and project/node instructions (RFC-15, October 19, 2026); RFC-10 (retiring `AGENTS.md` rules) and RFC-16 (reply policies) remain. |
| 4 | **Context, effort, compact, usage and statistics** (RF-60…64b). RF-60/61 and RF-64/64b completed October 20, 2026; RF-62 open (not possible through `codex queue`). |
| 5 | **Session creation and temporary sessions** (RF-50…53). |
| 6 | **Project graph/editor** (RF-44), `/` skill picker (RF-36). |

### Long-term goal: Placet across machines

Recorded with the maintainer on October 20, 2026 as direction only: not scheduled, no design yet, and it must not displace the open work above. When it is designed it becomes its own RFC.

- **Central hub on an always-on host** (a home NAS): backend, database, PWA and Web Push move off the desktop, so the phone keeps working when a PC is off.
- **One satellite per machine** (Linux PC, Mac and later others): bridge, hooks and MCP server stay local to the agent sessions and talk to the hub over Tailscale; each machine has its own revocable identity and cannot act for another. Reserved commands stay per machine.
- **Cross-machine relations:** nodes are tied to a machine, and agents on different machines exchange tasks through the same relations, rules and approvals.
- **Machine availability:** the hub detects when a machine is off or unreachable and shows its sessions and nodes as unavailable, instead of queueing work silently.
- **Local models as nodes:** local model servers on each machine (for example image models on the desktop GPU, larger general models on the Mac's unified memory) appear as nodes like any agent.
- **MCP gateway:** the hub connects to external MCP servers and exposes their tools through Placet, with the user choosing which agents or nodes may see each tool; agents then configure only Placet instead of every MCP server.

After phase 2, acceptance tests cover all provider pairs (Claude, Codex, Antigravity in both directions). Laboratory observations do not establish all those combinations.

## Reserved-action implementation note (RS-04)

The command path is implemented through the human-only proposal API and
**Settings → Commands**, with signing isolated on the administrative origin.
Only fixed backend/bridge restart actions are candidates. A proposal is not an
execution permit; the passkey binds the protected description and the broker
creates one durable job per intent. The PWA retains a lost-acknowledgement key
for explicit retries, recovers state after disconnection, labels user-service
bridge evidence, and directs uncertain outcomes to terminal reconciliation.
The UI never provides a shell or performs reconciliation itself.

This is repository implementation status, not operational activation. The reviewed activation installer adds root-only
reconciliation, supervised units, capability/group checks and a fail-closed
root-equivalent-access preflight. These are repository implementations, not an
installed or enabled configuration. Activation and real iPhone acceptance remain
human operations.
See the [plan and trust boundaries](plans/reserved-commands.md) and
[installation guide](installation.md#reserved-administrative-commands).
