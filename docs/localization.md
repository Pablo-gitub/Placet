# Localization

Placet is migrating its product interface to English and Italian. This document describes the implemented foundation and the migrated approval flows, not a claim that the whole product has been translated.

## Language policy

Write identifiers, comments, logs, developer documentation and tool contracts in English. Put product-owned human-facing text into catalogs. Never translate conversation content, user input, agent responses, titles, filenames, code or protocol identifiers as part of interface localization.

The frontend uses `i18next` and `react-i18next`, with catalogs bundled locally; no translation service or account is involved. English is the fallback language. Both catalogs live in `frontend/src/i18n/locales/`.

Language resolution is:

1. Explicit saved selection (`en` or `it`).
2. First supported browser language, including regional variants.
3. English fallback.

The header offers Automatic, English and Italiano. Automatic removes the saved override and follows the browser. Restricted local storage does not prevent changing language within the current session. The setting is currently local to that browser/PWA installation; it is not yet synchronized to the backend or other devices.

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

The conversation reader, tool/action labels, delivery-management controls and attachment/PDF viewers are not yet migrated. Notifications and provider message wrappers still use their existing language. The new browser preference intentionally does not pretend to configure these server-side surfaces.

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

The frontend maps known machine codes to catalog keys. Unknown codes, missing/non-JSON bodies, authorization failures and validation lists receive safe localized fallbacks; raw exception strings and arbitrary validation objects are not rendered. Add new codes and parameters explicitly instead of matching source-language sentences. Phone-message and pin routes also provide `session.not_found`, `session.closed`, `session.unknown_agent`, `session.excluded`, `mention.ambiguous`, `mention.missing`, `mention.unavailable`, `mention.self` and `message.unavailable` where applicable. Attachment sends use `upload.invalid`, `upload.too_large`, `upload.unsupported`, `upload.forbidden`, `upload.excluded`, `upload.incomplete`, `upload.failed`, `bridge.offline` and `bridge.timeout`. The JSON body-size middleware returns the same `upload.too_large` shape. Shared bridge reads keep their prior contracts until the reader/viewer slice is migrated; raw bridge diagnostics are not used as upload UI text.

These changes preserve HTTP status, attachment limits, mention validation, pin semantics and the rule that failed transfers do not enqueue a message. Other API endpoints retain their existing contracts until their flows are migrated.

## Verification

Run frontend tests, lint and production build from `frontend/`, and backend tests from the repository root. `I18n.test.jsx` checks preference resolution/storage failure, fallback, catalog and placeholder parity, plural forms, dynamic helpers, preservation of edits, actual approval/withholding API calls and translated error feedback. `RequestLocalization.test.jsx` additionally covers initial approvals in both languages, single/multiple choices, unchanged Other text, confirmation rejection, target/timing selection, retained edits and a localized conflict. Backend tests cover every semantic decision error code and verify that rejected input does not mutate a pending request. `ComposerLocalization.test.jsx` covers selection of closed sessions, provider selection during a locale change, attachment/mention payloads in both languages, retained drafts/files/timing on failure, reactive validation errors and localized API banners. Backend message/upload tests assert structured codes and no request creation after failed validation or transport. Existing product tests explicitly select Italian until their own flows are migrated.

The first slice was also exercised in Chromium with mocked backend data at 390px and 1280px: language switching, draft preservation, reply forwarding, history and mobile horizontal overflow. The initial prompt-approval flow was also exercised with edited prompts, Queue/Now and automatic return policy on mobile, plus a separate desktop rendering check. This does not replace a real iPhone PWA check or a production delivery test.

The picker/composer slice was exercised in Chromium at 390px with mocked backend data: unpin a closed session, mention another session, attach a PDF, choose Now, receive an upload error, switch locale without losing draft/files/timing, then retry successfully. User data and the final request payload were checked, with no mobile horizontal overflow.

Known pre-existing limitation: crossing the 900px mobile/desktop layout boundary while an approval is being edited recreates the form, discarding its unsent local choices. Locale switching within a layout preserves them. Responsive draft retention requires a separate fix; the viewport checks do not claim that behavior is supported.

Continue with one user workflow at a time. Complete catalog coverage before claiming an entirely English interface or changing the public readiness status.
