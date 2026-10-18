# English/localization migration: closure review

Date: 2026-10-18. This records source review and pre-deployment validation, not a security certification or confirmation that a particular installation has been updated.

## Scope and decisions

Reviewed the accumulated changes from the fetched `origin/main` baseline through `101faad`, including the localization commits, English documentation moves, session wakeup/reliability changes, collaboration return approval and Claude's subsequent usage/model/context work. The review focused on state/authorization invariants, locale propagation, content preservation and mobile controls. Corrections are in `3a74a36`; documentation and regression gates accompany this report.

- Canonical developer documentation, registry descriptions, CLI output and diagnostics use English. Legacy document paths are English redirects to `docs/`; requirements keep their original RF/RFC/RS identifiers. Laboratory observations and future plans remain distinct from implemented behavior.
- Product controls use English/Italian catalogs with English fallback. The device-local UI language and shared backend notification/envelope language are intentionally independent.
- User prompts, responses, titles, filenames, code and provider identifiers remain data in their original language. Existing Italian protocol patterns and audit records are retained for compatibility.
- Initial collaboration approval and return approval remain separate. Queue/Now timing is human-selected; uncertain delivery is not silently retried; time does not release an in-flight session queue.
- Registry translation preserves the parsed YAML configuration, including same-provider routes and human approval rules. The installed registry is not overwritten by ordinary deploys.

## Findings corrected before publication

1. **Withheld reply draft in audit:** an agent participating in a request could read `edited_response` from a return-decision audit event even when the human rejected forwarding. Non-human event reads now omit that field; the human keeps the complete audit. Tests check both sender and recipient access. Authorized reply delivery remains on the established reply path.
2. **Timezone-less usage reset:** accepted naive timestamps caused comparisons with UTC-aware timestamps to fail. Usage-report validation now requires timezone-aware reset times and rejects invalid input without mutating provider state.
3. **Optional metadata breaking chat:** malformed provider context values could raise while reading the conversation. Optional metadata failures now return no metadata, preserving transcript access. A session change also clears the previous model/context while loading or failing to load the next conversation.
4. **Documentation gaps:** corrected a translated section anchor, stale retry/return semantics and root-relative development commands. Replaced the frontend template README, translated registry prose, and aligned Node requirements with PDF.js (22.13+), not only Vite.

## Verification

- Python: **450 tests passed**, including backend, bridge, hooks, catalogs, approvals and new failure cases.
- Frontend: **136 tests passed**; English/Italian catalog key and placeholder parity are included.
- Frontend lint and production build passed. The existing bundle-size advisory above 500 kB remains; no build failure.
- Local Markdown links and heading anchors are checked by `python3 scripts/check_docs.py`; it uses no network. All original requirement-table IDs were compared with the pre-migration requirements and retained.
- Chromium at **360 and 390 px**, both languages: Home usage rows, session model/context, locale switching through the real selector, retained draft, visible composer, no horizontal overflow or page errors.
- Additional mocked-API browser flows passed: closed-session unpin, mentions, PDF upload, Queue/Now selection, localized upload failure/retry, prompt approval with retained edits/recipient/timing/return policy, image viewer, real two-page PDF rendering, visible close controls, conversation pagination/errors and independent shared-language settings. Viewer/approval/settings flows also checked at 1280 px.
- Screenshots were inspected. Data and requests in browser checks were simulated; no production agent tasks or push notifications were generated for those checks.

GitHub Actions runs documentation links, Python tests, frontend tests/catalog validation, lint and build with read-only repository permissions. CI is separate from privileged deployment; it does not receive deployment credentials or run the installer. The first remote run still needs confirmation after push.

## Limits retained

- This is Chromium viewport validation, not a physical iPhone/PWA test, soft-keyboard test or accessibility certification. Final phone checks should include PDF close, composer visibility and language persistence after reopening.
- Crossing the 900 px layout boundary can recreate an approval form and lose its local unsent choices; language switching within a layout preserves them. This existing issue remains a separate layout fix.
- Provider wake/usage integrations include private interfaces and app-version assumptions. Localization tests do not establish live behavior on every provider version or after long inactivity.
- The privileged installer still executes reviewed repository/dependency code as root; the [reserved-commands plan](../plans/reserved-commands.md) remains unimplemented. Agents do not receive a sudo password as part of this migration.
- No complete Git-history/secret/screenshot privacy audit was performed. Local environment/tool files and SQLite sidecars are now ignored to reduce accidental future additions; this does not remove anything already published.

## Installation follow-up

After a reviewed push, an administrator runs `sudo deploy/install.sh` from the repository root to install the build and restart services. Verify backend/bridge health and the served asset versions afterward. The source review and passing tests do not themselves confirm deployment. Existing MCP clients may need restarting to refresh English tool descriptions; no provider conversation needs to be resumed headlessly.
