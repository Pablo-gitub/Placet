# Localization

Placet is migrating its product interface to English and Italian. This document describes the implemented foundation and the migrated approval flows, not a claim that the whole product has been translated.

## Language policy

Write identifiers, comments, logs, developer documentation and tool contracts in English. Put product-owned human-facing text into catalogs. Never translate conversation content, user input, agent responses, titles, filenames, code or protocol identifiers as part of interface localization.

The frontend uses `i18next` and `react-i18next`, with catalogs bundled locally; no translation service or account is involved. English is the fallback language. Both catalogs live in `frontend/src/i18n/locales/`.

Language resolution is:

1. Explicit saved selection (`en` or `it`).
2. First supported browser language, including regional variants.
3. English fallback.

The header offers Automatic, English and Italiano. Automatic removes the saved override and follows the browser. Restricted local storage does not prevent changing language within the current session. This interface preference intentionally remains local to that browser/PWA installation. It is independent of the shared background-language setting below.

Changing language updates mounted translated components, the HTML language and document title. Date and number helpers use `Intl` and preserve a compatible browser region when available. Locale selection does not change timezone policy, UTC storage, drafts or request state.

## Implemented scope

The first complete migrated workflow is human approval of an agent's completed reply:

- Reply card, editable text, explicit/implicit explanation, forward and withhold actions.
- Confirmation feedback and localized API errors.
- Decision history, reply-forwarding status and audit timeline.
- Shared navigation, language selector, connection indicator and home headings.
- Session state/name helpers and shared date/time/number formatting infrastructure.

Initial request approval is now migrated too: confirmation questions, single/multiple choices and Other answers, prompt editing/preview, request origin and priority, target session selection, Queue/Now timing and optional automatic reply return. User-provided options, prompts and notes remain unchanged across language switches.

The session picker and composer are now migrated: provider tabs, closed-session visibility and unpinning, mention search and reference counts, draft/reattachment notices, suspended/unreachable session hints, Queue/Now controls, upload validation, transfer/read errors and localized file sizes. Selected mentions and filenames remain message data after insertion; switching language does not rewrite their tokens or IDs. Local file errors and API feedback banners retain translation descriptors so an already-visible error also changes language.

The conversation reader, action/tool summaries, outgoing and queue-hold states, delivery confirmations and image/PDF viewers are now migrated too. Code-copy controls and PDF page counts use the active locale. Transcript errors retain translation descriptors, including a visible retry for older-page failures that preserves loaded messages. Changing locale keeps action groups expanded and does not reload the current PDF or duplicate its canvases.

Bridge-owned media captions now carry `label_key` (`shared_file` or `generated_image`) with an English fallback `text` for older clients. The UI translates only these recognized metadata labels; arbitrary captions and original transcript content remain unchanged. Older bridges without `label_key` continue to display their original caption text.

Notifications and generated conversation envelopes use explicit backend English/Italian catalogs and the shared background-language preference described below. MCP tool descriptions, CLI output and bridge diagnostics are in English. Canonical documentation still needs its English cleanup. The browser interface preference remains independent of these server-side surfaces.

## Background language: shared installation setting

The header's separate language-settings icon opens **Notification and message language**. Choose English or Italiano and save. This applies to future product-generated notifications and messages for the whole installation, including all allowed human supervisors; it does not change any browser's interface language, translate user/agent content, or rewrite existing conversations. Changing the interface select does not write this shared setting.

`GET` and `PUT /api/settings/language` are available only through the authenticated human listener. Both use `{ "language": "en" | "it" }` and `Cache-Control: no-store`. Agents and the bridge cannot read/write this settings endpoint; the bridge receives only the effective language as part of its authenticated work response. Unknown values, including `auto`, are rejected. A background process has no browser locale to resolve Automatic from.

The setting is persisted in SQLite's `app_settings` table and survives backend/bridge restarts and ordinary deploys. Before any value is saved, `PLACET_DEFAULT_LANGUAGE=en` is the default; operators can set it to `it` in the backend environment. An unsupported default falls back to English. A saved value takes precedence over the environment. This does not introduce a system-locale dependency or modify installed environment files automatically.

Server catalogs live in `backend/app/i18n/locales/{en,it}.json`. Rendering receives an explicit locale; no mutable process-global current language is used. Notification jobs capture the language when scheduled; delivery wrappers use the current dispatch language, and message context uses a consistent creation-time language. Local and HTTP bridge modes propagate the setting to app, hook, steer and tmux delivery. Privacy controls for public ntfy servers remain unchanged: original prompt/context content is not included there.

The parser accepts both historical Italian and new English user envelopes. Request-ID prefixes and MCP tool names remain stable. Attachment-only drafts recognize the Italian and English attachment suffix/default-prompt forms. Already inserted mention tokens, original text, files and approved reply content remain data, not translation keys. MCP contracts explicitly distinguish task completion from human authorization to forward the reply.

The settings panel reports loading/save failures without exposing raw diagnostics and retains an unsaved selection for retry. Its caption explains the shared scope. It requires a deployed backend with the new endpoint; older backends report a load error. Installing source changes does not hot-reload descriptions in already running MCP clients; restart those clients when convenient to load the English descriptions.

## Adding translated text

Use semantic English keys grouped by feature, such as `return.approve` or `sessions.suspended`. Add the same key to both catalogs. Use full messages with named interpolation parameters rather than translated fragments. Keep placeholders equivalent across languages.

Use `useTranslation()` inside React components. Helper functions may call the shared i18next instance at invocation time; do not translate constants once at module load. A component rendering helper output must subscribe to language changes, directly or through its translated parent.

Use plural suffixes for count-dependent grammar, for example `home.pendingQuestions_one` and `_other`, and pass a numeric `count`. Existing IDs such as `__altro__`, API fields, statuses and hash routes remain unchanged for compatibility.

Use the functions in `i18n/formatters.js` for dates, numbers and relative time. New code must not force `it-IT`. Treat interpolation values as text: never enable arbitrary HTML from translations or server errors.

## API errors

`backend/app/errors.py` defines the additive product-error contract:

```json
{
  "code": "return.empty",
  "params": {},
  "detail": "The reply cannot be empty."
}
```

Migrated endpoints are `POST /api/requests/{id}/return-decision` (`return.empty`, `return.unavailable`) and `POST /api/requests/{id}/decision`:

- `decision.not_pending`, with the numeric request `id` parameter (including concurrent state changes).
- `decision.prompt_edit_not_allowed`, `decision.recipient_not_allowed`, `decision.no_options`.
- `decision.duplicate_options`, `decision.unknown_options`, `decision.rejected_options`.
- `decision.single_choice`, `decision.multi_choice`, `decision.self_target`, `decision.now_target`.
- `session.unavailable`, `session.excluded` from the shared target validator, also used when creating a request.

Validation returns codes directly; it does not infer codes by matching diagnostic sentences. Client interpolation only accepts documented, typed parameters; server fields cannot override the active translation language or fallback text. HTTP status and the legacy `detail` field remain available to existing clients. Authentication and authorization are unchanged.

The frontend maps known machine codes to catalog keys. Unknown codes, missing/non-JSON bodies, authorization failures and validation lists receive safe localized fallbacks; raw exception strings and arbitrary validation objects are not rendered. Add new codes and parameters explicitly instead of matching source-language sentences. Phone-message and pin routes also provide `session.not_found`, `session.closed`, `session.unknown_agent`, `session.excluded`, `mention.ambiguous`, `mention.missing`, `mention.unavailable`, `mention.self` and `message.unavailable` where applicable. Attachment sends use `upload.invalid`, `upload.too_large`, `upload.unsupported`, `upload.forbidden`, `upload.excluded`, `upload.incomplete`, `upload.failed`, `bridge.offline` and `bridge.timeout`. The JSON body-size middleware returns the same `upload.too_large` shape. Reader/viewer routes now also return `transcript.not_found`, `transcript.unsupported`, `transcript.invalid`, `transcript.excluded`, `transcript.failed`, and the corresponding `attachment.*` codes (`not_found`, `unsupported`, `invalid`, `excluded`, `forbidden`, `too_large`, `failed`). Bridge offline/timeouts use the shared `bridge.offline` and `bridge.timeout` codes for reads as well as uploads. Unknown bridge diagnostics are replaced with an English diagnostic and a safe localized fallback; raw bridge messages are not product UI text.

The three delivery-control conflicts use `delivery.retry_unavailable`, `delivery.cancel_unavailable` and `delivery.release_unavailable`. Authorization, explicit confirmations, uncertain-delivery handling and queue-release semantics are unchanged.

These changes preserve HTTP status, attachment limits, mention validation, pin semantics and the rule that failed transfers do not enqueue a message. Other API endpoints retain their existing contracts until their flows are migrated.

## Verification

Run frontend tests, lint and production build from `frontend/`, and backend tests from the repository root. `I18n.test.jsx` checks preference resolution/storage failure, fallback, catalog and placeholder parity, plural forms, dynamic helpers, preservation of edits, actual approval/withholding API calls and translated error feedback. `RequestLocalization.test.jsx` additionally covers initial approvals in both languages, single/multiple choices, unchanged Other text, confirmation rejection, target/timing selection, retained edits and a localized conflict. Backend tests cover every semantic decision error code and verify that rejected input does not mutate a pending request. `ComposerLocalization.test.jsx` covers selection of closed sessions, provider selection during a locale change, attachment/mention payloads in both languages, retained drafts/files/timing on failure, reactive validation errors and localized API banners. Backend message/upload tests assert structured codes and no request creation after failed validation or transport. `ReaderLocalization.test.jsx` and `ViewerLocalization.test.jsx` cover dynamic action labels, preserved content/expansion state, copy fidelity, error recovery and pagination, localized delivery confirmations, image open/close/error states, PDF page counts and failures, and absence of PDF reload/canvas duplication on locale change. Backend reader tests check sanitized, stable error codes; media tests check label metadata without changing attachment identity. `BackgroundLanguage.test.jsx` covers save/load errors, preference independence, localized status and bilingual attachment-draft reconstruction. `test_background_language.py` covers human-only access, forged-header rejection, persistence, English fallback, server catalog/placeholder parity, notification privacy/content fidelity, browser-independent language dispatch, both bridge stores and bilingual hook parsing. Legacy backend scenarios explicitly choose Italian through a test-only default; production fallback remains English. Existing frontend scenarios also select their test locale explicitly.

The first slice was also exercised in Chromium with mocked backend data at 390px and 1280px: language switching, draft preservation, reply forwarding, history and mobile horizontal overflow. The initial prompt-approval flow was also exercised with edited prompts, Queue/Now and automatic return policy on mobile, plus a separate desktop rendering check. This does not replace a real iPhone PWA check or a production delivery test.

The picker/composer slice was exercised in Chromium at 390px with mocked backend data: unpin a closed session, mention another session, attach a PDF, choose Now, receive an upload error, switch locale without losing draft/files/timing, then retry successfully. User data and the final request payload were checked, with no mobile horizontal overflow.

The reader/viewer slice was exercised in Chromium at 390px and 1280px with mocked API data but real PDF rendering: initial read error/retry, unchanged conversation/code content, expanded action groups, image open/close, a two-page PDF and language switching while open, visible mobile close button, and delivery retry confirmation/conflict feedback. No mobile horizontal overflow or page errors were observed. This does not establish behavior on a physical iPhone.

The shared-language panel was exercised in Chromium at 390px and 1280px with mocked API data: persistence across close/reopen, separation from the interface preference, failed-save retention, translated errors, and absence of mobile horizontal overflow. Notification tests use mocked transport; no live push notification or provider message was sent for this migration.

Known pre-existing limitation: crossing the 900px mobile/desktop layout boundary while an approval is being edited recreates the form, discarding its unsent local choices. Locale switching within a layout preserves them. Responsive draft retention requires a separate fix; the viewport checks do not claim that behavior is supported.

Continue with one user workflow at a time. Complete catalog coverage before claiming an entirely English interface or changing the public readiness status.

## Developer diagnostics

Hook installation (including dry-run, idempotent reinstall and removal), tmux calibration and idle-check reasons use English diagnostic text. Upload validation and private-cache errors also use English diagnostics; their stable error codes continue to select localized PWA messages. The generated fallback filename is `attachment`; filenames supplied by users remain unchanged.

These changes do not alter provider hook configuration, approval requirements, idle detection, transfer limits or filesystem checks. The historical audit reason `progetto escluso` remains unchanged for compatibility, as do bilingual transcript parser patterns and user-authored content. Diagnostic output is intended for developers and is not a localized product interface.

## Migration closure

The [closure review](reviews/2026-10-18-localization-closure.md) records the combined diff review, remaining limits and bilingual narrow-viewport checks. `python3 scripts/check_docs.py` validates local documentation links/anchors. GitHub Actions runs that check, Python/backend catalog tests, frontend/catalog tests, lint and build. English registry prose preserves the parsed routing configuration. Provider model IDs/effort values remain original data; their surrounding controls and numbers use the active UI locale.
