# Desktop compaction alternatives — October 29, 2026

Follow-up to the [Codex compaction laboratory](2026-10-29-codex-compact-lab.md).
This research excludes literal slash messages, private-stream proxies, GUI
input automation, concurrent conversation writers and provider database edits.

## Verified scope

| Runtime | Same-conversation route | Completion evidence | Status |
| --- | --- | --- | --- |
| Codex Desktop, loaded and idle | Existing owner's coordination-bus compact request | New `compacted` record and `task_complete`; subsequent normal reply | Verified, undocumented adapter |
| Claude Desktop 2.1.284 | Watched local native threshold, next Placet-driven inference | New auto `compact_boundary`, explicit Placet reply, observed Stop | Verified after one-time channel onboarding |
| Exclusively owned Claude CLI 2.1.303 stream | Watched local native threshold | Auto `compact_boundary` and marker response | Verified; not attachment to another client's session |

These results do not establish arbitrary idle-session availability, a stable
public desktop compaction API, or a cross-provider product feature.

## Codex: a genuine existing-desktop control was verified

The linked laboratory now documents a successful direct coordination-bus
request to the desktop conversation's existing owner, genuine compaction and a
successful subsequent delivery. The earlier negative access conclusion is
superseded; the queue slash negative result is unchanged.

## Claude: native threshold through watched project settings

Claude Code documents `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` and
`CLAUDE_CODE_AUTO_COMPACT_WINDOW`. They adjust the engine's native threshold;
they do not send a user message or compact a copied conversation. Project/local
settings can supply environment values. Whether a particular value reloads must
be tested rather than inferred from generic file watching.

### Same-process CLI probe: verified

Installed standalone Claude Code: 2.1.303. Installed Claude Desktop package:
2.9939.4. The standalone version must not be assumed to equal the desktop's
embedded engine version.

A new, exclusively owned CLI/SDK stream session
`8858a599-b217-4268-b952-ca92bbce2d39` used only temporary project/local settings.
Tools and MCP were disabled for the probe. Three harmless seed turns established
history, including inert numbered filler and marker `CLAUDE-AUTO-608`.

Initial local settings:

```json
{
  "env": {
    "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "100",
    "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "50000"
  }
}
```

Without restarting or resuming that process, the probe changed only the
percentage to `1`, waited three seconds for the watcher, then supplied the next
ordinary user turn. Observed output:

- `system/status` with `status: compacting`;
- a real `system/compact_boundary`, `trigger: auto`, with 21,332 pre-tokens,
  1,157 post-tokens and duration 10,784 ms;
- successful final reply retaining `CLAUDE-AUTO-608`.

The process exited normally after stdin EOF. No global settings were edited.
Two earlier very short histories emitted a compacting status but no persisted
boundary or context reduction. Therefore **status: compacting alone is not
success evidence**, and a low threshold does not guarantee that tiny histories
will actually be compacted.

The deliberately extreme `1` value is a laboratory trigger, not a recommended
user policy. It must not be left enabled on normal work. A percent threshold is
not an exact per-session token promise; window, model, minimum useful history,
context-preservation rules and already inherited environment values matter.

### Desktop-only-Placet countercheck: verified

The dedicated desktop conversation `d5a8b1b4-2959-403a-a2c0-6fd4206d3faf`
("Laboratorio Placet") used the desktop's embedded engine **2.1.284**, not the
standalone 2.1.303. Its isolated project was
`.worktrees/codex-compact/.lab-followup/claude-desktop-probe`.

The human created the conversation and explicitly authorized the Placet channel
once in the desktop. After that setup, neither trial required desktop input:

1. Placet request **#636** received the explicit `placet_reply` result
   `PLACET-ONLY-RECEIVE-OK`.
2. An observer captured the current end of this conversation's own transcript,
   saved the laboratory settings, and changed only its local threshold to
   `1` percent with window `50000`.
3. Placet request **#637** asked for `PLACET-COMPACT-ONLY-637`; it did not send
   `/compact`, start another runtime, or ask the model to perform compaction.
4. At **22:46:22.117 UTC** the same conversation recorded a real
   `system` / `compact_boundary`: `trigger: auto`, **58,089 pre-tokens →
   4,582 post-tokens**, duration **14,557 ms**.
5. Immediately after observing the boundary, the observer raised the local
   threshold to `100` percent/window `200000` to avoid another artificial
   compaction during the reply. At **22:46:33.533 UTC** it observed
   `stop_hook_summary`, then restored the original laboratory settings
   (`100` percent/window `50000`) in a `finally` block. A 180-second deadline
   also led to restoration. The restored file was checked afterward.
6. The result was an **explicit** `placet_reply` with the requested marker;
   request #637 became `COMPLETED`, execution `completed` at
   **22:46:33.610215 UTC**.

This verifies **same-conversation native compaction plus explicit completion
through Placet**, without desktop intervention after setup. Compaction token
metadata describes the compacted history: it does **not** imply that the next
inference's total input, including the fixed system/tool prefix, is only 4,582
tokens. No cost claim is inferred from those counts.

### Failed trials and prerequisites

The older conversation `b7dea4dd-6b86-4b1b-ba44-e5d9059f4419` produced actual
native boundaries, but the end-to-end test was not successful: desktop messages
were interleaved, and the agent refused hook-carried requests as lacking desktop
approval. Adding a local instruction file after that conversation was already
running did not fix its behavior (#632 returned a refusal implicitly). The
exact cause of that behavior was not established; stale loaded instructions or
compacted summaries are hypotheses, not proven provider defects. The human
replaced it with the new dedicated conversation and initial channel authorization
above. Generalizing this onboarding to other conversations remains product work.

A separate Placet bug lost an active request's ownership on `SessionStart`
with source `compact`, setting the session idle before its actual Stop. The
fix, **`ddb621a`**, preserves `busy` and `turn_request_id` through that internal
start. It was independently reviewed and confirmed installed before #637.
The old #612 timed out as `STALLED`; the human released its queue explicitly.
A later `placet_reply` to it was rejected with HTTP 409. Neither a timeout nor a
queue release is evidence of a successful compaction request or a late reply.
These unsuccessful trials must not be substituted for the #637 countercheck.

### What a product adapter still needs

This route is **native auto-compaction at the next model inference**, not a
verified public remote compact-now endpoint. The work message has already been
handed to the runtime when its inference triggers maintenance. A strict
"compact completely before handoff" contract is therefore not established.

Local settings are scoped to the project folder, not a single session. Other
sessions sharing that folder can see the override. A production implementation
needs explicit scope/permission, serialization, restoration on failure and
restart recovery, and protection against overwriting concurrent human edits.
The laboratory observer's `finally` restoration is not a crash-safe product
transaction. Thresholds must account for the fixed prefix and avoid repeated
compactions. No threshold override should be installed globally or inherited
by unrelated child sessions. Prefer the provider's normal policy when a safe
per-project override cannot be established.

## Other routes and limits

- **Codex native threshold:** `model_auto_compact_token_limit` is documented;
  it could reduce large contexts without external commands. Loading/reloading
  it for an existing desktop thread and its project/session scope need proof.
  The exported 0.159.2 schema has no threshold field in
  `ThreadSettingsUpdateParams`; a generic settings RPC must not be assumed to
  update that value. No global configuration was changed to test it.
- **Claude session settings control:** the installed desktop package exposes an
  internal `applyFlagSettings(sessionId, settings)` service and an SDK
  `apply_flag_settings` control request, with an `autoCompactWindow` schema
  field. This is program inspection, not proof of an external usable endpoint.
  Injecting into Electron or intercepting its existing SDK connection would
  violate this laboratory's rules and was not attempted.
- **Remote Control:** a supported client could in principle control the same
  native runtime if that runtime is actually shared through the product's
  remote transport. Presence of a remote feature does not grant Placet an API
  or guarantee attachment to an already-running desktop conversation. No remote
  feature, daemon or global configuration was enabled.
- **A separate SDK/API compaction call:** it compacts the history managed by that
  client, not automatically the desktop owner's in-memory state. Without a
  supported import into the owner, it does not satisfy the same-instance goal.
- **Deep links, OS application commands and plugins:** no additional documented
  compaction route was established. No Claude deep link was opened. No renderer
  code was injected and no GUI input was automated.

A watched native threshold would meet the goal of compacting before the next
model inference, but not necessarily Placet's stricter contract of completing
maintenance before it hands the work message to the provider. A button while
idle may need to schedule native compaction for the next ordinary turn rather
than immediately compact; the UI must disclose that difference. Project scope
also affects every session sharing that settings file, not just one Placet node.

## Sources

- [Claude environment variables](https://code.claude.com/docs/en/env-vars).
- [Claude settings reload](https://code.claude.com/docs/en/settings).
- [Claude Desktop shared configuration](https://code.claude.com/docs/en/desktop).
- [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).
