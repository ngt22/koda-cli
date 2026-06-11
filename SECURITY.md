# Security Policy

## Supported versions

koda-cli is a small, single-maintainer project. Security fixes are applied to
the latest released version and `main` only. There is no long-term support for
older releases — please upgrade to the latest version before reporting an issue.

## Reporting a vulnerability

**Please do not open a public issue for security vulnerabilities.**

Report privately through GitHub's
[private vulnerability reporting](https://github.com/ngt22/koda-cli/security/advisories/new)
("Report a vulnerability" under the repository's **Security** tab). This keeps
the details confidential until a fix is available.

When reporting, please include:

- A description of the vulnerability and its impact.
- Steps to reproduce (a minimal command sequence or config is ideal).
- The koda-cli version (`koda --version`), OS, and Python version.

You can expect an initial acknowledgement within a few days. Once confirmed, a
fix and an advisory will be prepared; credit is given to reporters who want it.

## Scope and threat model

koda stores arbitrary text entries and can execute them as shell commands
(`koda x`), so a few areas are security-relevant by design:

- **Command execution** — `koda x` runs an entry's body through a shell. Entries
  brought in by `koda pull` are marked `source=remote` and require confirmation
  before they execute; reviewing an entry with `koda edit` clears that flag.
  `exec.shell` is restricted to an allowlist of known shells. Trailing CLI args
  after the ref are passed as the shell's positional parameters (`$1`, `"$@"`),
  individually quoted, so a caller's arguments cannot break out of the resolved
  command. `koda x --dry-run`
  previews the resolved command without running it (and so skips the confirmation
  prompt — nothing executes), but it prints the body verbatim and does not strip
  terminal escape sequences; redirect its output to a file when inspecting a
  fully untrusted entry.
- **Git sync** — `push`/`pull` exchange entries as JSON Lines. The merge is
  last-writer-wins on `modified_at`, and future-dated timestamps are rejected so
  a malicious peer cannot force-overwrite local entries. The `source` trust
  column is local-only and never travels in the payload.
- **Config** — `exec.shell`, `git.sync_path`, and similar keys are validated to
  resist redirection to arbitrary binaries or paths via a tampered config file.
- **Local files** — the database and config live under the user's home directory
  with restrictive permissions (`0600` for the DB, `0700` for its directory).

Reports that fall within this threat model are especially welcome. General bugs
without a security impact should go to the normal
[issue tracker](https://github.com/ngt22/koda-cli/issues).
