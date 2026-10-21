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

The administrative package (`admin_executor/`) is tested in its own environment, without the application's dependencies or the shared `conftest.py`:

```bash
python3 -m venv /tmp/admin-venv
/tmp/admin-venv/bin/pip install -r admin_executor/requirements.txt pytest pytest-asyncio httpx
/tmp/admin-venv/bin/python -m pytest -q --noconftest $(ls tests/test_admin_*.py | grep -v test_admin_commands.py)
```

Before installing on the PC, also set `PLACET_TEST_SYSTEMD=1`: it checks the generated units against this host's binaries (CI skips it).

GitHub Actions (`.github/workflows/checks.yml`) runs five jobs on every pull request and on `main`: documentation links, PWA (tests, lint, build), backend (with the built PWA), the administrative package in its separate environment, and the backend command API against a real broker (`tests/test_admin_commands.py`, which needs both environments' dependencies: test-only, they are never installed together). A test module skipped for a missing dependency fails its job. Actions are pinned to commit SHAs; Dependabot proposes weekly updates as pull requests. CI never deploys: a release is installed only through a reserved command confirmed with a passkey (see [reserved commands](docs/plans/reserved-commands.md)).

Please add tests with behavior changes. For provider integrations, also describe what you verified with the real app and on which version: automated tests cannot prove that an undocumented provider interface still behaves the same.

## Parallel work

Agents working in parallel use a separate git worktree and branch each, created inside the repository so they stay within the agent's allowed folder:

```bash
git fetch && git worktree add .worktrees/<task> -b <branch> origin/main
```

`.worktrees/` is ignored by git. Never switch branches in the main checkout another agent may be using; remove the worktree (`git worktree remove .worktrees/<task>`) once its PR is merged.

## Language

- Code, comments, logs, commit messages and developer documentation are in English.
- Product-owned text shown to people goes into the translation catalogs (`frontend/src/i18n/locales/`), in English and Italian. Never translate conversation content, user input, agent replies, file names, code or protocol identifiers.
- Details and rules for translators: [docs/localization.md](docs/localization.md).

## Documentation

The map is [docs/README.md](docs/README.md). Keep the requirement identifiers (`RF-*`, `RFC-*`) stable, and keep historical observations in `docs/experiments/` separate from the current design in `docs/requirements.md` and `docs/architecture.md`.

`AGENTS.md` is not an instruction file: it is the agent registry read by the backend (YAML front matter). Edit it only in its documented format.

## Commits

Small, focused commits with a conventional prefix (`feat`, `fix`, `docs`, `chore`, `refactor`, `test`) and a scope, for example `fix(bridge): …`.

Work on a branch and open a pull request against `main`; it is merged when the checks pass. Keep each pull request to one topic.
