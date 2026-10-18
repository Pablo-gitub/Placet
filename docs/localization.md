# Localization

Placet is migrating its product interface to English and Italian. This document describes the implemented foundation and the first migrated flow, not a claim that the whole product has been translated.

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

The initial prompt-approval card, session picker, most composer/chat controls, attachment/PDF UI and other backend errors are not yet migrated. Notifications and provider message wrappers still use their existing language. The new browser preference intentionally does not pretend to configure these server-side surfaces.

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

The first migrated endpoint is `POST /api/requests/{id}/return-decision`, with codes `return.empty` and `return.unavailable`. HTTP status and the legacy `detail` field remain available to existing clients. Authentication and authorization are unchanged.

The frontend maps known machine codes to catalog keys. Unknown codes, missing/non-JSON bodies, authorization failures and validation lists receive safe localized fallbacks; raw exception strings and arbitrary validation objects are not rendered. Add new codes and parameters explicitly instead of matching source-language sentences. Other API endpoints retain their existing contracts until their flows are migrated.

## Verification

Run frontend tests, lint and production build from `frontend/`, and backend tests from the repository root. `I18n.test.jsx` checks preference resolution/storage failure, fallback, catalog and placeholder parity, plural forms, dynamic helpers, preservation of edits, actual approval/withholding API calls and translated error feedback. Existing product tests explicitly select Italian until their own flows are migrated.

The first slice was also exercised in Chromium with mocked backend data at 390px and 1280px: language switching, draft preservation, reply forwarding, history and mobile horizontal overflow. This does not replace a real iPhone PWA check or a production delivery test.

Continue with one user workflow at a time. Complete catalog coverage before claiming an entirely English interface or changing the public readiness status.
