# Contributing to koda-cli

Thanks for your interest in improving koda! This guide covers everything you
need to send a pull request.

## Development setup

koda is a [uv](https://docs.astral.sh/uv/) + hatchling project targeting Python
3.10–3.12.

```bash
# Clone and create an editable environment in .venv
git clone https://github.com/ngt22/koda-cli.git
cd koda-cli
uv sync                       # installs runtime + dev dependencies

# Run the CLI from the project environment
uv run koda --help

# Install globally the way a user would (optional)
uv tool install ".[turso]"    # the turso extra is optional
```

## Running checks

There is no Makefile; run the tools directly. CI runs exactly these on Python
3.10/3.11/3.12, so green locally means green in CI.

```bash
uv run pytest                 # tests (with coverage; fails under 60%)
uv run ruff check src tests   # lint
uv run ruff format src tests  # auto-format (use --check to verify only)
uv run mypy                   # static type check (config in pyproject.toml)
```

Optionally install the pre-commit hooks so lint/format run on every commit:

```bash
uvx pre-commit install
```

## Branch naming

Use a `type/scope` slug, optionally with the issue number:

- `feat/<scope>` — new functionality (e.g. `feat/json-output`)
- `fix/<issue>` — bug fixes (e.g. `fix/53-empty-content`)
- `refactor/<scope>` — internal restructuring
- `docs/<scope>` / `chore/<scope>` / `ci/<scope>` — everything else

## Commit & PR conventions

- Follow [Conventional Commits](https://www.conventionalcommits.org/):
  `type(scope): summary`, e.g. `feat(cli): add --json output for list`.
  Common types: `feat`, `fix`, `refactor`, `docs`, `chore`, `ci`, `test`.
- **Write commit messages and PR titles/bodies in English.** Existing history is
  mixed Japanese/English; new work must be English.
- Reference the issue a PR closes with `Closes #<n>` in the body.

## Pull request checklist

Before opening a PR, make sure:

- [ ] `uv run ruff check src tests` passes
- [ ] `uv run ruff format --check src tests` passes
- [ ] `uv run mypy` passes
- [ ] `uv run pytest` passes
- [ ] New behavior has tests
- [ ] `CHANGELOG.md` has an entry under `## [Unreleased]` (see below)
- [ ] User-facing changes are reflected in `README.md`

## Changelog

We keep a [Keep a Changelog](https://keepachangelog.com/)-style `CHANGELOG.md`.
Add a bullet under the `## [Unreleased]` section describing your change, grouped
by `Added` / `Changed` / `Fixed` / `Removed`.

## JSON output schema

`list`, `show`, and `config` accept `--json` for scripting.

- `koda list --json` → a JSON **array** of entry objects (all matches, paging
  ignored). `koda show --json` → a single entry **object**. Each entry object is:

  ```json
  {
    "id": 1, "uid": "abc1234", "idx": 0,
    "content": "…", "tags": ["work", "home"],
    "shortcut": null, "created_at": "…", "modified_at": "…"
  }
  ```

  Note `tags` is a list (split on the stored comma separator).

- `koda config --json` → a hierarchical object `{section: {key: value}}`.
  `turso.token` is always emitted as `"****"`; use `koda config get turso.token`
  for the raw value.

## Language policy

- **Issues** may be written in Japanese or English — whichever is clearer.
- **Commits, PR titles, and PR bodies must be in English.**

## A note on AI-agent files

`AGENTS.md`, `CLAUDE.md`, and `opencode.json` are **personal, local-only** files
used by individual maintainers' AI coding agents. They are intentionally listed
in `.gitignore` and are **not** part of the repository — you do not need them to
contribute. The canonical, contributor-facing rules live in this file. If you
use an AI agent, point it here.
