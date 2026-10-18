# Changing Codex model and effort through `codex queue`

October 18, 2026. Codex app CLI 0.159.2 (`/usr/lib/chatgpt/resources/codex`), Linux. Related requirement: RF-62.

## Question

`codex queue` accepts `-m/--model` and `-c key=value` like the other Codex
commands. Do they change the model or reasoning effort of the queued turn when
the desktop app runs it?

## Procedure

1. A throwaway thread was created with `codex exec -c model_reasoning_effort="low"`.
   Its rollout recorded `turn_context` model `gpt-6.1-sol`, effort `low`.
2. `codex queue --thread <id> -c model_reasoning_effort="high" --message …`, then
   the native link `codex://threads/<id>`, as the bridge does.
3. `codex queue --thread <id> -m gpt-6.1-sol-mini -c model_reasoning_effort="high" --message …`,
   then the link again.

## Result

Both messages were accepted and answered, but every turn's `turn_context`
still said `gpt-6.1-sol` / `low`. The overrides are parsed by the CLI process
and not carried with the queued message: the app runs the turn with the
thread's own settings. A model name that may not exist was accepted without
error, which confirms that the flags are not forwarded.

## Consequence

`codex queue` cannot change model or effort. The app-server protocol exposes
per-turn model and effort, but the app-server belongs to the desktop app and
interposing on it was the removed proxy experiment (branch
`experiment/desktop-proxy`). RF-62 for Codex stays open; RF-61 (showing
model and effort) is unaffected.
