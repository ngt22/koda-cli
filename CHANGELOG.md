# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `SECURITY.md` (vulnerability reporting policy) and `CODE_OF_CONDUCT.md`
  (Contributor Covenant) for the project.
- CI now runs `mypy` static type checking, and `pytest` enforces a 60% coverage
  floor (`--cov-fail-under`).
- `--json` output for `list`, `show`, and `config` for scripting (jq-friendly).
- `koda list <idx|shortcut>` is now shorthand for `koda show <idx|shortcut>`
  instead of erroring on an unexpected argument.
- `--dry-run` on `compact`, `shift`, `move`, and `tag` previews changes
  without modifying the database.
- `--quiet` on `add`, `copy`, `tag`, `move`, and `swap` suppresses the
  success message; `add --print-uid` / `add --print-idx` print the new
  entry's uid / idx to stdout for pipelines.
- `koda export [--out PATH]` writes all entries as JSON Lines to stdout or a
  file, and `koda import <file>` merges a JSONL file into the local database.
- `koda diff [--file PATH]` shows a uid-level diff (local-only / remote-only /
  changed) between the local database and the remote payload.
- `koda backup --out PATH` writes a consistent single-file SQLite snapshot
  (`VACUUM INTO`).
- `koda pick --multi/-m` for fzf multi-select: prints selected IDXs (pipe to
  `remove`/`tag`) or applies `--raw`/`--show` per selection. Extra fzf flags
  via the `KODA_FZF_OPTS` environment variable.
- `koda config show` as an explicit subcommand, equivalent to bare
  `koda config`.

### Changed
- `-q` is now exclusively the short flag for `--query` (substring search) on
  `list`, `remove`, and `pick`. The `--quiet` flag on `add`/`copy`/`tag`/`move`/
  `swap` no longer has a `-q` short alias — spell out `--quiet`. This removes the
  ambiguity of `-q` meaning different things on different commands.
- `koda --help` now groups commands into labeled sections (Core, Git sync, Data,
  Index, Config) instead of one flat list, making the `push`/`pull` sync
  commands and the data/index commands easier to discover. The README subcommand
  quick reference is grouped to match.

### Fixed
- `swap` no longer fails with a UNIQUE-constraint error when one of the entries
  sits at display index `-1` (the temporary index is now derived from the table
  rather than a hardcoded `-1` sentinel).

## [1.2.0] - 2026-06-06

### Added
- Git sync over JSON Lines: `push` / `pull` commands plus `git.*` configuration,
  merging by `uid` + `modified_at`.
- `list.columns` configuration to toggle which columns `koda list` shows.
- Developer infrastructure: ruff (lint + format), a GitHub Actions CI matrix on
  Python 3.10/3.11/3.12, pre-commit hooks, and a `PRAGMA user_version` migration
  framework.
- `CONTRIBUTING.md`, GitHub issue/PR templates, and an MIT `LICENSE` file.

### Changed
- Split the `main.py` monolith into a `koda/commands/` package plus a shared
  `koda/runtime.py`; `main.py` is now thin wiring.
- Config and database are now resolved lazily, so importing the CLI has no side
  effects.
- Consolidated `get_memos` / `get_memos_all` into a single method and
  straightened `MemoMerger.merge`'s insert path.

### Fixed
- `raw` output is now newline-terminated for POSIX tool interop.
- `add` exits non-zero on empty-content abort and prioritizes text arguments
  over piped stdin.
- Error output now goes to stderr instead of stdout.
- `exec` runs multi-line bodies in a single shell.
- `tag` reports accurate add/remove counts in its completion message.

### Security
- Restricted `exec.shell` to an allowlisted, absolute-path executable.
- Hardened on-disk permissions for the config file and database.

## [1.1.1] - 2026-04-24

### Fixed
- Convert query parameters to tuples for Turso libsql compatibility.

## [1.1.0] - 2026-04-24

### Added
- Turso remote database backend.
- Neovim integration.

### Fixed
- Use CSV (comma + quotes) as the positional-variable delimiter and stop
  escaping positional values with `re.escape()`.

## [1.0.2] - 2026-04-22

### Added
- Interactive `pick` command backed by fzf.

## [1.0.1] - 2026-04-22

### Fixed
- Expand `~` in the database path from config.
- Use the correct package name for version lookup.

## [1.0.0] - 2026-04-22

### Added
- Initial public release: SQLite-backed memo store with `add`, `list`, `show`,
  `raw`, `edit`, `remove`, `copy`, `tag`, `exec`, index management
  (`move` / `swap` / `shift` / `compact`), shortcuts, tags, variable
  substitution, and a `config` subcommand.

[Unreleased]: https://github.com/ngt22/koda-cli/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/ngt22/koda-cli/compare/v1.1.1...v1.2.0
[1.1.1]: https://github.com/ngt22/koda-cli/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/ngt22/koda-cli/compare/v1.0.2...v1.1.0
[1.0.2]: https://github.com/ngt22/koda-cli/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/ngt22/koda-cli/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/ngt22/koda-cli/releases/tag/v1.0.0
