# RS-09 lab: a session credential through SessionStart (Claude Code CLI)

Date: October 23, 2026, during the autonomous night session. Claude Code
2.1.284, headless (`claude -p`), model Sonnet, in a throw-away folder with a
project-local `SessionStart` hook (no change to the user's configuration). The
hook generated `plst_LAB` + `secrets.token_urlsafe(16)` on every start, put it
in `additionalContext` and logged the source and the SHA-256. The values were
lab values, not secrets.

## Steps and results

| Step | Hook `source` | Result |
| --- | --- | --- |
| New session, ask "reply with the credential or NONE" | `startup` | the model answered with exactly that credential |
| `claude -p --resume <id> "/compact"` | `resume`, then `compact` | a new credential at each start, including after compaction |
| Resume again, ask for "the newest credential" | `resume` | the model answered with the newest one (issued by this resume), not the earlier ones still in the history |

Where the plaintext ends up in the conversation file
(`~/.claude/projects/<folder>/<session>.jsonl`): attachments
`hook_additional_context` and `hook_success` for every issued credential,
`api-request-blob` records, and the assistant message that echoed it.

## What this settles for the plan

- **Delivery and renewal (Claude Code CLI): confirmed.** SessionStart runs on
  `startup`, `resume` and `compact`; each start can issue a new generation,
  and the model picks the newest one when asked.
- **Plaintext in the provider's transcript: confirmed**, as the plan already
  states ("it cannot erase the provider's own transcripts"); add
  `api-request-blob` to the records the transcript reader must redact or skip
  (`bridge/transcripts.py`), together with the two attachments.
- **A resume is a new generation.** Every reopening of a conversation renews
  the credential; the grace period for the previous generation must cover a
  resume during a running request.

## Not covered yet (still required by the lab gate)

- Claude Desktop: the same hook in a desktop session, and whether the app's
  own context handling keeps the newest value (needs a session opened by the
  user; Placet must not open `claude://` links for this).
- Codex 0.145 (`additionalContext`, `PostCompact`) and where its rollout
  records it.
- Concurrent sessions of one provider, lost spool events, `/clear`,
  continuation (RF-29d) and revocation: these are backend tests, written with
  step 2 of the plan.
