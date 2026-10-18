# Placet PWA

React/Vite frontend for human approvals, conversations, session selection, attachments and usage. Production builds are served by the FastAPI backend.

Run from the repository root (Node.js 22.13+ is required by the frontend dependencies):

```bash
npm --prefix frontend ci
npm --prefix frontend test
npm --prefix frontend run lint
npm --prefix frontend run build
```

For interactive development, run the backend first, then:

```bash
PLACET_DEV_LOGIN=you@example.com npm --prefix frontend run dev
```

The login must be in the backend's `PLACET_ALLOWED_USERS`. Vite listens on loopback and proxies `/api` to the human Unix socket; set `PLACET_HUMAN_SOCKET` if needed. This development identity proxy must not be exposed to an untrusted network. See [installation](../docs/installation.md) for production isolation.

Product text belongs in the English/Italian [catalogs](src/i18n/locales/). English is the fallback. Translate controls, not prompts, replies, filenames, model IDs or code. The device-local interface setting and shared background-message language are independent; see [localization](../docs/localization.md).

Tests cover both languages, safe API errors and retention of drafts during language changes. Browser checks do not replace physical iPhone/PWA verification. Crossing the 900px layout breakpoint can still recreate approval forms; see the documented limitation.
