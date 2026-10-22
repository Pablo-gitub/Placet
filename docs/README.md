# Placet documentation

Start with the [project README](../README.md) for what Placet does and a quick start.

## Using Placet

- [Installation](installation.md): prerequisites, environment variables, production install, notifications, hooks and MCP setup, available tools.
- [Localization](localization.md): interface languages, background language, rules for translators.

## Design

- [Requirements](requirements.md): what Placet must do, with stable `RF-*` / `RFC-*` identifiers and their status.
- [Architecture](architecture.md): components, delivery and wakeup per provider, request lifecycle, data model, security.

## Provider behavior

- [Codex wakeup](providers/codex-wake.md): suspended Codex chats, `codex queue` and the native `codex://threads/` link.
- [Claude Desktop wakeup](providers/claude-wake.md): suspended Claude Desktop conversations and the `claude://code/continue` link.

Some integrations rely on undocumented internals of the provider apps. These pages record what was verified and on which versions; they are not guarantees for future versions.

## Laboratory procedures (not yet executed)

- [Fixed bridge worker probe](experiments/2026-10-21-bridge-worker-probe.md): operator-only system-worker experiment against a disposable target.

- [Root-to-user bridge helper probe](experiments/2026-10-21-bridge-drop-probe.md): a human-only drop/import experiment targeting a disposable unit.

- [Bridge sandbox/chroot probe](experiments/2026-10-20-bridge-sandbox-probe.md): a human-only disposable user-service experiment; no production restart.

## Experiments (historical records)

- [Provider integration laboratory](experiments/provider-integration.md), October 15–16, 2026.
- [Session integration](experiments/2026-10-18-session-integration.md), October 18, 2026.
- [Codex effort through `codex queue`](experiments/2026-10-18-codex-effort.md), October 18, 2026: overrides are not applied.

These describe observations at a point in time. Where they differ from the requirements or the architecture, the latter describe the current design.

## Plans

- [Portability and local mode](plans/portability-and-local-mode.md): study for macOS/Windows support and a local mode without Tailscale.
- [Verified session binding](plans/session-binding.md): proposal for RS-09, proving which session an agent acts for (prerequisite of RFC-17).
- [Reserved commands](plans/reserved-commands.md): running approved privileged actions, such as a deploy, from the phone. Foundations, passkey enrollment and administrative transport implemented; privileged dispatch not yet.
  - [Administrative network setup](plans/admin-network-setup.md) and [administrative web setup](plans/admin-web-setup.md): the separate Tailscale transport and passkey enrollment page.

## Reviews

- [Public repository readiness](reviews/2026-10-18-public-repository-readiness.md), October 18, 2026.

- [Localization closure review](reviews/2026-10-18-localization-closure.md), October 18, 2026: combined review, verification and retained limitations.
