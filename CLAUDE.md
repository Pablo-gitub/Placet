# Instructions for Claude

## Language

**Always answer the maintainer in Italian.** This applies to every message addressed to the user: answers, progress updates, summaries, tables and questions. Do not switch to English even after long technical sessions or when quoting English documentation, logs or output (quote them, but comment on them in Italian).

Everything in the repository stays in English: code, code comments, file names, commit messages and documentation (see [CONTRIBUTING.md](CONTRIBUTING.md)).

## Project

- Documentation map: [docs/README.md](docs/README.md) · Requirements: [docs/requirements.md](docs/requirements.md) · Architecture: [docs/architecture.md](docs/architecture.md) · Experiments: [docs/experiments/provider-integration.md](docs/experiments/provider-integration.md).
- `AGENTS.md` is not an instruction file: it is the agent registry read by the Placet backend (YAML front matter). Do not add free text to it, or the backend can no longer read it.
