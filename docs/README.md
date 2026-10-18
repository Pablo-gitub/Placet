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

## Experiments (historical records)

- [Provider integration laboratory](experiments/provider-integration.md), October 15–16, 2026.
- [Session integration](experiments/2026-10-18-session-integration.md), October 18, 2026.

These describe observations at a point in time. Where they differ from the requirements or the architecture, the latter describe the current design.

## Plans

- [Reserved commands](plans/reserved-commands.md): running approved privileged actions, such as a deploy, from the phone. Not implemented.

## Reviews

- [Public repository readiness](reviews/2026-10-18-public-repository-readiness.md), October 18, 2026.

- [Localization closure review](reviews/2026-10-18-localization-closure.md), October 18, 2026: combined review, verification and retained limitations.
