# Public repository review: English and localization

Date: 2026-10-18. Scope: local working tree, documentation organization, developer-facing language and product localization. This is a review and migration proposal, not an implemented migration or a security certification.

Baseline: local HEAD `f747911`, plus pre-existing uncommitted delivery-mode and response-approval changes. The remote GitHub revision and production deployment have not been inspected. Findings about local changes do not establish what is already published or installed.

## Recommendation

Keep code identifiers, comments, docstrings, logs, MCP tool descriptions, commit messages and canonical documentation in English. Move product-owned human-facing messages into English and Italian catalogs. Select the UI language by locale with an explicit, persistent override. Do not translate user prompts, agent responses, session titles, filenames, code blocks or stored conversations.

The module-level organization is already useful: `backend`, `bridge`, `hooks`, `mcp_server`, `frontend`, `tests` and `deploy` have distinct responsibilities. Preserve these boundaries. Do not combine localization with a wholesale backend or frontend rewrite.

## Findings

| Priority | Finding and evidence | Consequence | Proposed correction |
| --- | --- | --- | --- |
| P1 | No localization infrastructure in `frontend/package.json` or `frontend/src`. UI text is embedded in components and helper maps, including `RequestCard.jsx`, `Composer.jsx`, `History.jsx`, `Timeline.jsx`, `aspects.js` and `sessionInfo.js`. | An English interface cannot be selected; adding translations in only a few components would produce a mixed-language product. | Introduce catalogs and one locale policy, then migrate all surfaces including accessibility labels, confirmations, empty states, errors and the newly added return-approval UI. |
| P1 | `backend/app/main.py:105` and its transcript/attachment error maps contain Italian messages. `frontend/src/App.jsx:202`, `:244`, `:276` and `useTranscript.js:11` display backend `detail` directly. | A translated frontend would still show Italian errors; arbitrary validation payloads cannot safely be assumed to be strings. | Add stable error codes and structured parameters, a shared client error renderer, and a localized fallback. Preserve HTTP status and migrate existing `detail` consumers compatibly. |
| P1 | Public onboarding links from `README.md:85`, `:100`, `:114` lead to Italian architecture, installation, requirements and lab documentation. | The English README does not provide an end-to-end English onboarding path. | Translate and reorganize the full canonical documentation, preserving requirements, operational caveats and links. |
| P1 | `backend/app/envelopes.py` and the user-message assembly in `backend/app/main.py` generate Italian message wrappers; `mcp_server/server.py:69` onward exposes Italian tool descriptions. | Developer-facing and model-facing contracts remain language-dependent even if the UI is translated. | Translate MCP contracts to English. Centralize product-generated conversation templates separately from user content and decide their locale explicitly. Preserve tool names, request IDs and behavioral requirements. |
| P1 | `README.md:104-112` describes an alpha with an Italian interface and a broad wake capability; the local tree also contains newer approval and timing changes not yet committed. | A public reader can confuse released features, local development and future plans. | Document provider prerequisites and wake limitations; distinguish supported public interfaces from private adapters; publish only verified behavior for a clearly identified revision. Update language/status claims when the migration actually ships. |
| P2 | `sessionInfo.js:51`, `:90` and `chatEntries.js:64` force `it-IT`; `History.jsx` and `Timeline.jsx` use implicit browser formatting. `Composer.jsx:19` uses `toFixed(1)` for sizes. | Dates, relative time, numbers and language can disagree. | Use the active locale through centralized `Intl` formatters. Preserve timezone semantics and UTC storage; changing language must not silently change timestamps or business logic. |
| P2 | `backend/app/notifier.py` embeds Italian notification titles, bodies and action labels. Notifications run outside an interactive page request. | Browser locale negotiation alone cannot localize notifications consistently. | Persist the supervisor's preference in backend configuration or profile storage; notification builders receive the resolved locale explicitly. Keep public-server privacy behavior unchanged. |
| P2 | `hooks/install.py:154` onward contains Italian CLI output; some backend log messages are Italian. `CLAUDE.md` and agent registry prose are Italian. | Contributors see mixed developer-facing language and machine diagnostics. | Use English for CLI help/output, logs, tool docstrings and repository instructions. Preserve the registry frontmatter/schema and its installed configuration semantics. |
| P2 | Root contains both public entry points and lengthy Italian design/history documents. Architecture subsections 3.9 and 3.10 precede 3.8. | Navigation and cross-references are harder to maintain. | Keep a small root and an English docs index; translate, move and repair links together, preserving RF identifiers and old-path redirects where appropriate. |
| P2 | Frontend tests mostly assert Italian copy. No tracked `.github` workflows were found in this working tree. | Copy migration can break unrelated tests, and new hardcoded text or missing translations can return unnoticed. | Add explicit locale setup, catalog validation, behavior tests in both languages and automated checks for links and product literals. Do not replace all behavioral assertions with test IDs. |

P1 means a blocker for the stated English/localized public experience, not a claim that the deployed application is unsafe. P2 means consistency and maintainability work needed to keep that experience reliable.

## Locale contract

- Supported UI languages: `en` and `it`. The canonical source catalog and fallback are English.
- Resolution order: saved user preference, supported browser language, English fallback. Normalize regional variants such as `it-IT` and `en-GB` without discarding relevant formatting preferences. An explicit `Auto` option follows browser preferences.
- Changing language updates the whole mounted UI, date/number formatters and `document.documentElement.lang`, without losing drafts, selections or scroll context.
- Blocked/unavailable local storage must not prevent startup or language selection for the current session. Unsupported or malformed values fall back safely.
- Keep settings unobtrusive; no language-selection onboarding dialog. Both languages must be selectable from a small settings control.
- Backend notifications and product-generated agent-message wrappers cannot depend on the bridge's HTTP locale. Persist the supervisor preference on the backend; resolve server-side jobs from that setting, with a documented fallback. Switching UI language must not rewrite existing conversation entries.
- Developer-facing tool descriptions, logs and CLI help stay English; conversation content stays in the language in which it was written.
- Locale does not affect state keys, role IDs, routes, API field names, action identifiers, database values, filenames or RF requirement IDs.

## Catalog design

Recommended frontend implementation: `i18next` with `react-i18next`, bundled catalogs and standard `Intl` formatters. No remote translation service or account is needed. This uses an existing React integration rather than introducing a custom subscription, fallback and pluralization framework. Confirm exact dependency versions and bundle impact during implementation.

Suggested layout:

```text
frontend/src/i18n/
  index.js
  formatters.js
  locales/
    en/{common,sessions,requests,chat,errors}.json
    it/{common,sessions,requests,chat,errors}.json
backend/app/i18n/
  ... notification and product-message catalogs, with explicit locale input
```

Use semantic English keys such as `requests.return.approve` and `sessions.status.suspended`, not Italian source sentences. Store complete translatable messages with named interpolation parameters, not concatenated fragments. Use plural forms for message, page, attachment and session counts. Render interpolated values as text; no HTML translation strings or translation-driven executable behavior.

Do not use the same translation key for distinct domain concepts: request rejection, delivery cancellation, turn interruption and return withholding have different meanings. Likewise, `COMPLETED` does not mean the response was forwarded.

For API errors, define a stable machine contract, for example:

```json
{
  "code": "session.closed",
  "params": {},
  "detail": "The session is closed."
}
```

The client translates known codes. It must safely handle unknown codes, network failures, non-JSON replies and FastAPI validation lists. Keep a diagnostic fallback for older MCP/CLI clients, but avoid displaying arbitrary raw internal exceptions to the human. Map existing bridge error codes rather than throwing them away. Never infer a code by matching an Italian sentence.

## Documentation organization

Proposed canonical destinations; these moves have not been performed:

| Current document | English destination |
| --- | --- |
| `README.md` | Keep as the public entry point; concise features, actual limitations and installation links. |
| `ARCHITETTURA.md` | `docs/architecture.md` |
| `REQUISITI.md` | `docs/requirements.md` |
| `LABORATORIO.md` | `docs/experiments/provider-integration.md` |
| `docs/INSTALL.it.md` | `docs/installation.md` |
| `docs/CLAUDE_WAKE.md` | `docs/providers/claude-wake.md` |
| `docs/INTEGRAZIONE_SESSIONI_2026-10-18.md` | `docs/experiments/2026-10-18-session-integration.md` |
| `docs/COMANDI_RISERVATI.md` | `docs/plans/reserved-commands.md` |

Add `docs/README.md` as the map of installation, architecture, requirements, provider behavior, historical experiments and future plans. Add an English `CONTRIBUTING.md` covering setup, tests, language policy and translation rules. Translate `CLAUDE.md` and registry prose while preserving behavior and any intentional user-response preference.

Translate documents faithfully; do not replace detailed requirements or experiments with a short summary. Keep stable RF identifiers and distinguish historical observations from current guarantees. Preserve old-path compatibility with minimal English redirect documents during the transition, then update internal links and section anchors. Do not let translation create two independently maintained canonical versions.

Examples should use portable paths and fictional session IDs. Verify screenshots for personal project names and conversation contents before republishing them; no screenshot redaction has been performed in this review.

## Migration sequence

1. **Stabilize the baseline.** Separate and review existing uncommitted feature work. Identify the published revision. Do not combine it silently with the language migration.
2. **Establish conventions and an inventory.** English contribution rules, complete product-string inventory, error-code inventory and the shared locale contract. Separate content from protocol and diagnostics.
3. **Build and test localization infrastructure.** Locale resolver, persistent preference, reactive switching, catalogs, pluralization, formatting helpers and safe error rendering. Start with one vertical slice, such as session states and one API error.
4. **Migrate all frontend surfaces.** Home, session picker, request/return approval, history, timeline, chat/composer, attachments and PDF viewer; include accessibility labels, confirmation dialogs and HTML metadata. Preserve the existing mobile layout and draft behavior.
5. **Migrate server-generated text.** API codes, notification catalogs, product-message wrappers, MCP tool descriptions and English logs/CLI messages. Maintain rollout compatibility and keep privacy, approval and queue semantics unchanged.
6. **Translate and reorganize documentation.** Update links, filenames, examples, status claims and contributor instructions. Keep experimental/provider-version caveats visible.
7. **Add regression gates and publish coherently.** Verify catalog parity, placeholders and plurals; check documentation links; run existing backend/frontend checks and the new English/Italian scenarios. Review a final diff before deploying or pushing changes.

Each step should produce a focused, reviewable commit. Avoid simultaneous provider-adapter refactors, state-machine changes, deployment hardening or the reserved-commands feature during this migration.

## Acceptance checks

- Both languages cover every product-owned frontend message, including the response-return approval flow.
- Catalog key and interpolation parity checks pass; no accidental source keys appear to the user.
- Unsupported locale, missing key, corrupted preference and unavailable storage have safe behavior.
- Locale switching updates status maps and helper output, not only components using hooks. Nothing is translated once at module load and left stale.
- Dates, relative times and sizes are consistent; test timezones and plural cases explicitly.
- Unknown/legacy API errors and validation lists do not break React rendering.
- Notifications follow the persisted human preference even when the bridge runs with another environment locale.
- Prompts, replies, filenames, code, mentions and routing IDs survive unchanged.
- Human approvals, same-provider collaboration and one-request-per-session behavior retain their tested semantics.
- Test the full PWA on a narrow viewport in both languages, long English labels, attachment controls and PDF close controls. Automated tests alone do not establish iPhone visual correctness.
- English installation and contributor instructions work without relying on the author's paths or personal account.
- Backend tests, frontend tests, lint, production build and documentation-link checks pass.

## Public repository hygiene: limited observations

The tracked-path inventory did not show `.env`, SQLite databases, private key files or contents under the local `.aws`, `.codex` and `.agents` directories. The working directory does contain an ignored local database. This is not a scan of file contents, Git history, screenshots, branches or GitHub settings, and does not establish that no secret was ever published.

Before a public release, perform a separate history/content secret scan and inspect screenshots. Review `.gitignore` for environment files, SQLite sidecars, local tool configuration and logs. Keep tokens in environment references and use sanitized examples; do not rewrite history or rotate credentials without a concrete finding and an appropriate plan.

No application code, deployment configuration, provider configuration, dependencies or existing documents were changed for this review. Only this review document was added. Tests were not rerun because no executable code changed.

## References

- [react-i18next documentation](https://react.i18next.com/): React integration and reactive translated rendering.
- [MDN: Intl](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Intl): locale-aware formatting.
