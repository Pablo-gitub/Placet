# Contributing to Placet

Issues and pull requests are welcome, especially adapters for other agents and checks on new versions of the supported apps.

## Two rules that shape the project

- Nothing reaches an agent from another agent without passing through Placet's policy.
- Nothing on the phone reveals a file the user did not choose to share.

Changes that touch approvals, delivery, attachments or the bridge's file access should say how they keep these rules.

## Setup

Requirements: Linux, Python 3.11+, Node.js 22.13+.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
npm --prefix frontend ci
```

For a full local run, see [docs/installation.md](docs/installation.md). `run_all.py` starts backend and bridge in one process; it is meant for development only.

## Tests and checks

```bash
python3 scripts/check_docs.py          # local documentation links/anchors
npm --prefix frontend test             # PWA and locale catalogs
npm --prefix frontend run lint         # PWA lint
npm --prefix frontend run build        # assets also used by API integration tests
.venv/bin/python -m pytest -q           # backend, bridge, hooks
```

GitHub Actions runs the documentation check, both test suites, lint and build on pushes and pull requests. It does not deploy.

Please add tests with behavior changes. For provider integrations, also describe what you verified with the real app and on which version: automated tests cannot prove that an undocumented provider interface still behaves the same.

## Language

- Code, comments, logs, commit messages and developer documentation are in English.
- Product-owned text shown to people goes into the translation catalogs (`frontend/src/i18n/locales/`), in English and Italian. Never translate conversation content, user input, agent replies, file names, code or protocol identifiers.
- Details and rules for translators: [docs/localization.md](docs/localization.md).

## Documentation

The map is [docs/README.md](docs/README.md). Keep the requirement identifiers (`RF-*`, `RFC-*`) stable, and keep historical observations in `docs/experiments/` separate from the current design in `docs/requirements.md` and `docs/architecture.md`.

`AGENTS.md` is not an instruction file: it is the agent registry read by the backend (YAML front matter). Edit it only in its documented format.

## Commits

Small, focused commits with a conventional prefix (`feat`, `fix`, `docs`, `chore`, `refactor`, `test`) and a scope, for example `fix(bridge): …`.
