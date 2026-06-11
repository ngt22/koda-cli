# koda-cli

[![CI](https://github.com/ngt22/koda-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/ngt22/koda-cli/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)](https://github.com/ngt22/koda-cli/blob/main/pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](https://github.com/ngt22/koda-cli/blob/main/LICENSE)

A **text store** for the terminal. Save any text — commands, paths, templates, notes — to SQLite and recall it instantly by index, shortcut, or fuzzy search. Saved entries can be executed as shell commands, making koda a **terminal launcher**. Sync via a private Git repository to share the same store across machines, giving you a **cross-machine clipboard** that works from any terminal. Built with Python, Typer, and Rich.

## Contents

- [Why koda?](#why-koda)
- [Features](#features)
- [In action](#in-action)
- [Quick reference](#quick-reference)
- [Installation](#installation)
- [Update](#update)
- [Command reference](#command-reference)
- [Options](#options)
- [Recommended aliases](#recommended-aliases)
- [Configuration](#configuration)
- [Environment variables](#environment-variables)
- [Turso (remote database)](#turso-remote-database)
- [Git sync](#git-sync-multi-machine-sharing-via-github)
- [Example uses](#example-uses)
- [Security model](#security-model)
- [Development](#development)
- [License](#license)

## Why koda?

- **One store for text *and* commands.** Snippet managers store commands; note tools store text. koda does both, then lets you `exec` any entry as a shell command — with `${KEY}` / `$1` variable substitution at call time.
- **Recall is instant and scriptable.** Reach any entry by numeric index, a memorable shortcut, or fuzzy `pick` (fzf). `raw` emits body-only text for pipes and `$(...)`, and `--json` feeds `jq`.
- **Your store follows you.** A private Git repo syncs the same entries to every machine — a cross-machine clipboard for the terminal.

**Who it's for:** terminal-native developers, SREs, and DevOps folks who retype the same commands, paths, and templates across hosts.

koda sits between a snippet manager and a note tool: it keeps arbitrary text the way a note tool does, but every entry stays runnable the way a snippet manager's are — one store, recalled by index or name and executed in place.

**What koda gives you, at a glance:**

| Capability | koda |
|---|:---|
| Store arbitrary text | ✓ |
| Run any entry as a command | ✓ (`exec` / `pick`) |
| Fuzzy interactive pick | ✓ (fzf) |
| Variable substitution | ✓ (`${KEY}` / `$1`) |
| Shortcut & index recall | ✓ |
| Cross-machine sync | ✓ (your own Git repo) |
| Storage | SQLite (or Turso) |
| Shell-friendly output | ✓ (`raw`, `--json`) |

## Features

**Core**

- **Save and recall**: `add`, `list`, `show`, `edit`, `pick`, `remove` — all with one-letter aliases. Save any text and retrieve it instantly by index, shortcut, or fuzzy search.
- **Flexible input**: Arguments, heredocs, pipes, or `$EDITOR`.
- **Shortcuts**: Assign a memorable string alias to any entry and use it in place of a numeric index.
- **Tags**: Classify, filter, and batch-edit entries with multiple tags.
- **Shell-friendly output**: `raw` prints body-only text for pipes, `eval`, and scripts.

**Convenient features**

- **Launcher**: Run any saved command with `exec` or `pick`, with variable substitution at call time.
- **Command substitution**: Embed stored values directly in any command — `curl http://$(koda r web-ip):8080/healthz`, `tail -f $(koda r log-path)`.
- **Variable substitution**: Expand `${KEY}` and `$1 $2 ...` placeholders at recall time with `-V`.

**Other**

- **Cross-machine sync**: Push and pull via a private Git repository — the same store is available from every terminal, on every machine.
- **Display index**: Stable `uid` (SHA1 short hash) plus user-controlled `idx`. Reorder with `move`/`swap`; close gaps with `compact`.
- **XDG-friendly**: Data under `~/.local/share/koda/`, config under `~/.config/koda/`.
- **Configurable defaults**: Persist preferences in `~/.config/koda/config.toml`.

## In action

![koda demo: save commands, list them, then run one by shortcut with a variable](assets/demo.gif)

**Save a verbose command and run it by name:**

```bash
# Save the verbose command once
koda a "git log --oneline --graph --decorate --all" -t git -s glog

# From now on, just:
koda x 1        # run by index
koda x glog     # or by shortcut
koda p -x       # or pick interactively with fzf and execute
```

→ [More examples](#example-uses)

## Quick reference

### Subcommands

Grouped the same way as `koda --help`.

**Core**

| Command | Alias | Description |
|---|---|---|
| [`add`](#add) | `a` | Save a new entry |
| [`remove`](#remove) | `d` | Delete entries |
| [`copy`](#copy) | `c` | Duplicate an entry |
| [`edit`](#edit) | `e` | Open entry in `$EDITOR` |
| [`list`](#list) | `l` | List and filter entries |
| [`show`](#show) | `s` | Display entry with full metadata |
| [`raw`](#raw--body-only-output) | `r` | Print entry body to stdout |
| [`tag`](#tag) | `t` | Batch-add or remove tags |
| [`exec`](#execute-exec--run-a-saved-command) | `x` | Run a saved entry as a shell command |
| [`pick`](#pick--interactive-launcher-fzf) | `p` | Interactive selector (requires fzf) |

**Git sync**

| Command | Alias | Description |
|---|---|---|
| [`push`](#push-and-pull) | — | Export DB to Git sync repo and push |
| [`pull`](#push-and-pull) | — | Pull Git sync repo and merge into local DB |

**Data**

| Command | Alias | Description |
|---|---|---|
| `export` | — | Write all entries as JSON Lines to stdout or a file |
| `import` | — | Merge a JSONL file into the local database |
| `diff` | — | Show a uid-level diff against the remote payload |
| `backup` | — | Write a single-file SQLite snapshot (`VACUUM INTO`) |

**Index**

| Command | Alias | Description |
|---|---|---|
| [`move`](#reorder-entries-move-swap-shift-compact) | `m` | Move entry to a display index |
| [`shift`](#reorder-entries-move-swap-shift-compact) | `h` | Shift entries up or down by N |
| [`swap`](#reorder-entries-move-swap-shift-compact) | `w` | Swap display positions of two entries |
| [`compact`](#reorder-entries-move-swap-shift-compact) | `k` | Reassign indices to 0..n-1 |

**Config**

| Command | Alias | Description |
|---|---|---|
| [`config`](#configuration-config) | `g` | Read/write configuration |

Single-letter aliases are reserved and cannot be used as entry shortcuts.

### Options

| Option | Description |
|---|---|
| `-s` / `--shortcut` | Assign a memorable alias to an entry |
| `-t` / `--tag` | Assign tags (on `add`) or filter by tag |
| `--title` | Assign a human-readable display label (on `add`; editable via `edit`) |
| `-V` / `--var` | Variable substitution at recall time |

## Installation

**Requirements:** Python 3.10+ on Linux or macOS. `fzf` is optional and only needed for the interactive `pick` command.

```bash
# uv (recommended)
uv tool install "koda-cli @ git+https://github.com/ngt22/koda-cli.git"

# pipx
pipx install "git+https://github.com/ngt22/koda-cli.git"
```

Turso remote backend (optional):

```bash
uv tool install "koda-cli[turso] @ git+https://github.com/ngt22/koda-cli.git"
# or
pipx install "git+https://github.com/ngt22/koda-cli.git" --pip-args ".[turso]"
```

## Update

```bash
# uv
uv tool upgrade koda-cli

# pipx
pipx upgrade koda-cli
```

## Command reference

Most commands take an **entry reference** as their first argument — either a **numeric index** (e.g. `5`, shown by `koda l`) or a **shortcut** (a string alias you assign with `-s` at save time, e.g. `koda a "..." -s glog`). In the examples below, names like `glog` and `web-srv` are shortcuts.

### Add

Save a new entry from arguments, heredoc, stdin, or `$EDITOR`.

```bash
koda a "docker compose up --build" -t docker -s dc
koda a "npm run dev" -t work
koda a "psql -h localhost -U admin" --title "Local Postgres"
koda a              # opens $EDITOR
```

Use `--title` to attach a human-readable display label (single line). The title is shown by `koda show` and is also matched by `-q/--query` searches.

Heredoc for multi-line content:

```bash
koda a -t infra <<'EOF'
FROM python:3.12-slim
RUN pip install uv
WORKDIR /app
EOF
```

Pipe any command output directly into `koda a`:

```bash
history | grep ffmpeg | tail -1 | koda a -t ffmpeg
kubectl get pods -o wide    | koda a -t k8s
```

Content source precedence: **text arguments > piped stdin > `$EDITOR`**. When
arguments are given, piped stdin is ignored (with a warning on stderr). This
keeps `koda a "text"` working in non-interactive shells (cron, IDE tasks,
sandboxes) where stdin is not a TTY.

---

### Raw — body-only output

Print the entry body to stdout (no Rich formatting). Use for pipes, `eval`, and command substitution.

```bash
koda r web-srv        # by shortcut
koda r 5              # by index
koda r                # latest entry
echo 5 | koda r       # ref from stdin
```

**Command substitution — embed a stored value inside any command:**

```bash
# Capture the public IP of a freshly launched instance (changes every time)
aws ec2 describe-instances --filters "Name=tag:Name,Values=web" \
  | jq -r '.Reservations[0].Instances[0].PublicIpAddress' | koda a -t aws -s web-ip

# Embed in follow-up commands — no re-running the query, no copy-paste
ssh -i ~/.ssh/prod.pem ec2-user@$(koda r web-ip)
curl http://$(koda r web-ip):8080/healthz
ansible web -i "$(koda r web-ip)," -m ping

# Same with shell aliases: kr = koda raw, kd r = koda raw with kd prefix
ssh -i ~/.ssh/prod.pem ec2-user@$(kr web-ip)
curl http://$(kd r web-ip):8080/healthz
```

**Workflow example — capture a transient value once, reuse in requests:**

> **Security note**: Do not store passwords, API keys, or tokens.
> All entries are saved in plaintext SQLite, and Git sync will expose them
> in plaintext in the sync repository. Use this pattern for non-sensitive
> transient values only (container IPs, port numbers, generated paths, etc.).

```bash
# Step 1: capture an ephemeral value
docker inspect web \
  | jq -r '.[0].NetworkSettings.IPAddress' \
  | koda a -t docker -s web-ip

# Step 2: reuse it in subsequent commands — no copy-paste
curl http://$(koda r web-ip):3000/healthz

# Update when the value changes
koda e web-ip   # opens $EDITOR
```

`raw` strips shell-style inline comments (`#` at line start or after whitespace). Use `show` to see the original stored text.

```bash
koda a 'echo hello  # this is a comment'
koda r 5    # → echo hello
koda s 5    # → echo hello  # this is a comment
```

---

### List

```bash
koda l                          # all entries ordered by display index
koda l -q "docker"              # substring search on body or title
koda l -t linux                 # filter by tag substring
koda l -T archive               # exclude entries tagged "archive"
koda l -S                       # only entries that have a shortcut
koda l -n 50 -p 2              # 50 entries per page, page 2
koda l -s created_at --desc     # sort by creation date descending
koda l --columns idx,uid,sc,tags,content,created_at   # all columns
koda l --columns idx,content    # minimal view
koda l -d body                  # show body preview in content column
koda l -d title                 # show title (falls back to body preview if unset)
koda l -d full                  # show entire body, ignoring rows/truncate
koda l -d both                  # bold title line + body preview below
```

Default columns: `IDX`, `SC`, `Tags`, `Content`. Available columns: `idx`, `uid`, `sc`, `tags`, `title`, `content`, `created_at` (`idx` is required).
Sort columns: `id`, `idx`, `uid`, `tags`, `content`, `created_at`, `modified_at`, `shortcut`.

**Display modes (`--display` / `-d`):** Control what the Content column cell shows.

| Mode | Content cell |
|---|---|
| `title` | Entry title; falls back to body preview when title is unset (default) |
| `body` | Body preview, respecting `--rows` and `--truncate` (original behaviour) |
| `full` | Full body — all lines, no truncation |
| `both` | Bold title line followed by body preview; without a title, just the body preview |

---

### Execute (`exec`) — run a saved command

Run a saved entry as a shell command, with optional variable substitution.

```bash
# Without koda — retype or search history every time
kubectl logs -f deployment/api-gateway --tail=200 -n production --timestamps=true

# Save once
koda a "kubectl logs -f deployment/\$1 --tail=200 -n production --timestamps=true" -t k8s -s klogs
```

```bash
koda x klogs -V api-gateway    # run by shortcut with substitution
koda x klogs -V worker         # different deployment, same flags
koda x 12                      # run by index
koda x klogs -V worker -n      # --dry-run: print the resolved command, don't run it
```

**Append arguments at call time — `koda x <ref> [args…]`:**

Anything after the ref is passed to the command. If the body doesn't use it, it's appended at the end — like extra words after a shell alias — so one snippet covers both the bare and the parameterized case, with no `$1`/`${}` needed:

```bash
koda a "docker compose up -d" -s dcu

koda x dcu                 # → docker compose up -d
koda x dcu llama-server    # → docker compose up -d llama-server
koda x dcu web db          # → docker compose up -d web db
```

If the body *does* reference positional parameters, the args fill them instead, and `${1:-default}` supplies a fallback when none are passed:

```bash
koda a 'kubectl logs $1 --tail=100' -s klog
koda x klog mypod          # → kubectl logs mypod --tail=100

koda a 'echo Hello ${1:-world}' -s greet
koda x greet               # → Hello world
koda x greet Alice         # → Hello Alice
```

The args become the shell's real positional parameters (`$1`, `$2`, `"$@"`), so a value with spaces stays one word. Put `--` before anything that looks like an option (`koda x dcu -- --build`); koda's own flags (`-V`, `-f`, `-n`) are otherwise consumed by koda. This is distinct from `-V`, which does textual `$1`/`${KEY}` substitution *before* the shell runs — use `-V` when you need to drop a value into the middle of the body.

**Preview before running — `--dry-run` / `-n`:**

`koda x <ref> -n` prints the exact command that *would* run (variables already substituted) without executing it. It also skips the remote-confirmation prompt and shell validation, which makes it useful for checking an unreviewed `source=remote` entry before trusting it.

```bash
koda x deploy -V env=prod -n   # → sh -c 'kubectl apply -f prod/ && ...'
```

> The body is printed verbatim, including any terminal escape sequences it might contain. To inspect a *fully untrusted* entry, redirect the output to a file (`koda x <ref> -n > preview.txt`) rather than rendering it directly in your terminal.

**Deferred command substitution — `\$()` expands at exec time, not at save time:**

When you escape `$` with a backslash in `koda a "..."`, the shell stores the literal text `$(...)` unchanged. `koda x` then passes it to the shell, which evaluates it at that moment. This is useful for values that change on every run.

```bash
# Save once — \$() is stored literally
koda a "tar czf ~/backups/src-\$(date +%Y%m%d-%H%M).tar.gz ./src" -t backup -s backup

# Each invocation stamps the current date and time
koda x backup   # runs: tar czf ~/backups/src-20260505-1430.tar.gz ./src
koda x backup   # runs: tar czf ~/backups/src-20260506-0910.tar.gz ./src
```

**Multi-line scripts — the whole body runs in one shell:**

A saved entry is not limited to a single line. `koda x` passes the entire body to the shell as one program, so variables, loops, functions, and heredocs all work across lines. Save a script with `koda a` (open `$EDITOR` with no argument, or pipe it in) and run it later by shortcut.

```bash
# Pipe a multi-line script into a new entry
printf 'a=1\nb=2\necho "sum=$((a + b))"\n' | koda a -s sum
koda x sum            # → sum=3
```

```bash
# A for-loop body kept as one runnable entry
koda a -s ping3       # opens $EDITOR; paste:
#   for host in web db cache; do
#     echo "pinging $host"; ping -c1 "$host" >/dev/null && echo "  ok"
#   done
koda x ping3
```

```bash
# Function definitions and heredocs survive too
printf 'greet() {\n  echo "hi $1"\n}\ngreet "$1"\n' | koda a -s greet
koda x greet -V world   # → hi world
```

> **Security**: only store trusted commands. `exec` runs the body through the configured shell (`sh` by default).

---

### Edit

Open an entry in `$EDITOR`. The footer contains editable metadata (title, tags, shortcut).

```bash
koda e web-srv        # by shortcut
koda e 5              # by index
```

The metadata footer template:

```
---
# Metadata
title: My Deploy Script
tags: docker,work
shortcut: dc
created_at: 2026-01-01 00:00:00
---
```

Edit the `title:` value to change it; leave the value blank to clear the title. If the entire footer is deleted, the existing title is preserved along with the other metadata.

---

### Pick — interactive launcher (fzf)

Interactively select an entry with `fzf`, then run an action. Requires [`fzf`](https://github.com/junegunn/fzf) and an interactive TTY.

```bash
koda p -x                      # pick from all entries, execute immediately
koda p -x -q docker -t dev     # pre-filter by query and tag, then pick
```

**Compound patterns — use pick as a selector:**

```bash
koda x "$(koda p -p)"          # pick IDX, pass to exec
kd x "$(kd p -p)"              # kd prefix
kx "$(kp -p)"                  # two-letter alias

eval $(koda p -p | xargs koda r)   # pick IDX, eval the body
```

Other action flags: `-e` edit, `-r` raw, `-s` show.
`-p` (print IDX only) cannot be combined with action flags.

---

### Show

Display a single entry with full metadata.

```bash
koda s web-srv        # by shortcut
koda s 5              # by index
echo 5 | koda s       # ref from stdin
```

---

### Remove

Delete one or more entries.

```bash
koda d web-srv              # by shortcut
koda d 5                    # by index
koda d 1 3 5-8              # multiple entries and ranges
koda d -t archive           # delete all tagged "archive"
koda d -q "tmp"             # delete entries matching body substring
koda d --all -f             # delete everything (--all always requires -f)
```

---

### Copy

Duplicate an entry. The body and tags are copied; the shortcut is not.

```bash
koda c web-srv        # by shortcut
koda c 5              # by index
```

---

### Tag

```bash
koda t 1 3 5 -t work          # add tag to individual entries
koda t 2-6 -t archive         # add tag to a range
koda t 1 3-5 7 -T old         # remove tag from mixed selection
koda t 1 -t new -T old        # add one tag and remove another in one command
```

Re-tagging with an already-present tag is idempotent (no-op).

---

### Reorder entries (`move`, `swap`, `shift`, `compact`)

Each entry has a display index (`IDX`) you can freely rearrange — useful for keeping frequently used snippets at low numbers.

```bash
koda w 3 0        # swap display positions of entries 3 and 0
koda m 7 1        # move entry 7 to empty position 1
koda h 1          # shift entries at index 1+ up by 1 (makes room at 1)
koda h 1 -n 3     # shift up by 3 positions
koda h 5 -n -1    # shift entries from index 5 downward by 1
koda k            # reassign all indices to 0..n-1, fill gaps
```

`move` requires the destination index to be unoccupied. Use `shift` to make room first, or `swap` to exchange two occupied positions.

---

### Configuration (`config`)

```bash
koda g                           # show all settings with source
koda g get defaults.cmd          # print a single value
koda g set defaults.cmd list     # write to config file
koda g unset list.per_page       # remove key (reverts to built-in default)
koda g reset -f                  # delete config file without prompt
koda g edit                      # open config in $EDITOR
koda g path                      # print config file path
```

---

## Options

The following are flags that work across multiple commands, not standalone subcommands.

---

### Shortcuts (`-s` / `--shortcut`)

Assign a memorable string alias to any entry and use it instead of a numeric index.

```bash
# Save with a shortcut (-s is short for --shortcut)
koda a "kubectl rollout restart deploy/api" -t k8s -s restart

# Use the shortcut anywhere an index is accepted
koda r restart
koda x restart
koda s restart
koda d restart

# Default command — no subcommand needed (when defaults.cmd = raw)
koda restart          # → koda raw restart

# List all entries that have shortcuts
koda l -S
koda l -S --sort-by shortcut
```

To change or remove a shortcut, open the entry with `edit` — the `shortcut:` field appears in the metadata footer.

---

### Tags (`-t` / `--tag`)

Assign one or more tags to an entry at save time; use them to filter across commands.

```bash
koda a "docker compose up" -t docker,dev     # assign multiple tags at add time
koda l -t docker                             # filter list by tag substring
koda l -T archive                            # exclude entries tagged "archive"
koda d -t tmp                                # delete all entries tagged "tmp"
koda p -x -t dev                             # pick + exec, pre-filtered by tag
```

Use `tag` (subcommand) to add or remove tags on existing entries in bulk — see [Tag](#tag).

---

### Variable substitution (`-V` / `--var`)

Embed placeholders in a saved entry; fill them in at recall time with `-V`.

| Style | Placeholder | How to pass |
|---|---|---|
| Named | `${host}` | `-V KEY=VALUE` |
| Positional | `$1`, `$2`, ... | `-V value` or `-V val1,val2` (comma-separated, left-to-right) |

```bash
# Save a template with a positional placeholder
koda a "gcloud storage cp \$1 gs://my-company-analytics-prod/uploads/" -t gcloud -s upload

# Run with different values — no need to retype the bucket path
koda x upload -V ./report.csv
koda x upload -V ./summary.csv

# Named substitution — swap one variable by name
koda a "aws s3 sync ./dist s3://acme-frontend-\${env}-us-east-1/app/" -t aws -s deploy
koda x deploy -V env=prod
koda x deploy -V env=staging

# Multiple positional values
koda a "rsync -avz \$1 \$2" -t rsync
koda r 8 -V /src/path -V user@host:/dest
koda r 8 -V '/src/path,user@host:/dest'    # same result, comma-separated

# Mix named and positional
koda r 9 -V 'admin,5432' -V host=db.example.com -V name="new york"
# → connect admin@db.example.com:5432 as new york
```

Positional values are comma-separated within a single `-V` flag. Use `"..."` inside the flag to include spaces or commas in a value: `-V '"hello world","foo,bar"'`.

---

## Recommended aliases

Two patterns are available. Choose one based on how much you want to shorten your workflow.

---

### kd prefix — minimal (`alias kd='koda'` only)

Register only `kd` as an alias for `koda`, then use koda's built-in single-letter aliases for subcommands (`kd a`, `kd x`, `kd p -x`, etc.). No risk of conflicting with other tools.

```bash
# Add to ~/.zshrc or ~/.bashrc
alias kd='koda'
```

Usage with kd prefix:

```bash
kd a "npm run dev" -t work            # add
kd l -q docker                        # list
kd s web-srv                # show
kd e web-srv                # edit
kd r web-srv                # raw
kd x web-srv -V localhost   # exec
kd p -x -q docker           # pick + exec
kd d web-srv                # remove
kd t 1 3-5 -t archive       # tag
kd g set defaults.cmd list  # config set
```

---

### Two-letter alias — full set

Register a two-letter alias for every subcommand. Shorter to type, but check for conflicts before adding.

> **Note**: This pattern assigns `kd` to `koda remove`. If you already have `alias kd='koda'` (kd prefix), remove that line first.

```bash
# Add to ~/.zshrc or ~/.bashrc
alias ka='koda add'
alias kl='koda list'
alias ks='koda show'
alias ke='koda edit'
alias kr='koda raw'
alias kx='koda exec'
alias kp='koda pick'
alias kd='koda remove'   # ← replaces kd='koda' if you had kd prefix
alias kc='koda copy'
alias km='koda move'
alias kw='koda swap'
alias kh='koda shift'
alias kk='koda compact'
alias kt='koda tag'
alias kg='koda config'
```

Run `alias` in your shell to check for conflicts before adding these.

---

## Configuration

Koda reads `~/.config/koda/config.toml` (XDG: `$XDG_CONFIG_HOME/koda/config.toml`). All settings are optional — unset values fall back to built-in defaults.

```toml
[defaults]
cmd = "raw"       # "raw", "list", "show", or "add" — what bare `koda` does

[list]
per_page = 20     # entries per page
rows = 1          # content preview lines (0 = all)
truncate = 80     # max chars per line (0 = no truncation)
sort_by = "idx"   # default sort column
desc = false      # sort direction
columns = ["idx", "sc", "tags", "content"]   # idx is required; available: idx, uid, sc, tags, title, content, created_at
display = "title" # content column mode: title | body | full | both

[db]
path = "~/.local/share/koda/koda.db"
backend = "local"   # "local" or "turso"

[turso]
url = "libsql://YOUR_DB.turso.io"    # Turso database URL
# token = "..."                       # ⚠ Omit here — use KODA_TURSO_TOKEN env var
#                                     #   (config file may be committed to version control)

[git]
sync_path = "~/koda-sync"           # local clone of the sync repository
payload_file = "koda-sync.jsonl"    # JSONL file inside sync_path
sync_format = "jsonl"               # wire format (jsonl only)

[exec]
shell = "sh"      # shell used by exec — restricted to: sh, bash, zsh, fish
```

Priority order: **CLI flags > environment variables > config file > built-in defaults**

> **Security**: `exec.shell` is restricted to an allowlist (`sh`, `bash`, `zsh`, `fish`) that must resolve to an installed executable. This prevents a tampered config from redirecting `koda x` to an arbitrary binary. The config file (`config.toml`) and database are created with `0600` permissions and their parent directories with `0700`, so a plaintext Turso token is not world-readable.

## Environment variables

| Variable | Purpose |
|---|---|
| `KODA_DB_PATH` | Override database file path |
| `KODA_DEFAULT_CMD` | Override `defaults.cmd` for this session |
| `KODA_CONFIG_PATH` | Override config file path |
| `KODA_TURSO_URL`        | Turso database URL (overrides `turso.url` in config) |
| `KODA_TURSO_TOKEN`      | Turso auth token (overrides `turso.token` in config) |
| `KODA_GIT_SYNC_PATH`    | Path to the local Git sync clone (overrides `git.sync_path`) |
| `KODA_GIT_PAYLOAD_FILE` | JSONL file name inside the sync clone (overrides `git.payload_file`) |
| `KODA_GIT_SYNC_FORMAT`  | Sync wire format — `jsonl` (overrides `git.sync_format`) |
| `KODA_FZF_OPTS`         | Extra flags passed to `fzf` by `pick` (e.g. `--height 40% --layout reverse`) |
| `EDITOR`                | Editor for `add`, `edit`, and `config edit` (default: `vim`) |

## Turso (remote database)

Koda supports [Turso](https://turso.tech) as a remote backend, letting you share entries across machines.

### Setup

1. Install the optional dependency:

```bash
pip install libsql-experimental
# or with uv:
uv tool install ".[turso]"
```

2. Create a Turso database and get your URL and token from the [Turso dashboard](https://app.turso.tech) or CLI:

```bash
turso db create koda
turso db show koda --url
turso db tokens create koda
```

3. Configure Koda — use environment variables to keep the token out of the config file:

```bash
export KODA_TURSO_URL="libsql://YOUR_DB.turso.io"
export KODA_TURSO_TOKEN="YOUR_AUTH_TOKEN"
koda config set db.backend turso
```

You can also write the URL to the config file, but **omit the token** and supply it via the env var:

```bash
koda config set db.backend turso
koda config set turso.url "libsql://YOUR_DB.turso.io"
# Do NOT run: koda config set turso.token "..."
# The config file may be committed to version control — use KODA_TURSO_TOKEN instead.
```

### Switching between backends

```bash
koda config set db.backend turso   # use Turso
koda config set db.backend local   # use local SQLite (default)
```

The local SQLite database (`db.path`) and the Turso database are independent — switching backends does not migrate data.

## Git sync (multi-machine sharing via GitHub)

Koda supports syncing entries across machines using a Git repository (e.g. a private GitHub repo) as a transport. On push, Koda exports the local database as a JSON Lines file (`koda-sync.jsonl`) into a local clone, commits it, and pushes to the remote. On pull, it fetches the latest commit and merges entries into the local database by `uid` and `modified_at` — newer wins, no entry is deleted.

This works independently of the Turso backend. You can use Git sync with the default local SQLite database.

> **Security note**: `koda-sync.jsonl` contains **all entries in plaintext**.
> Any passwords, tokens, or secrets stored in Koda will be committed to the
> sync repository in plaintext. Use a **private** repository and avoid storing
> sensitive values in Koda when Git sync is enabled.

### Setup

**1. Create a sync repository on GitHub (private recommended):**

```bash
# Create the repo on GitHub, then clone it locally
git clone git@github.com:YOUR_USERNAME/koda-sync.git ~/koda-sync
```

**2. Point Koda at the clone:**

```bash
koda config set git.sync_path ~/koda-sync
```

The `koda-sync` directory is the local clone root. Koda creates `koda-sync.jsonl` inside it automatically on first push.

**3. Confirm the configuration:**

```bash
koda config get git.sync_path      # → ~/koda-sync
koda config get git.payload_file   # → koda-sync.jsonl  (default)
koda config get git.sync_format    # → jsonl            (default)
```

### Push and pull

```bash
koda push   # export DB → koda-sync.jsonl, commit, push to remote
koda pull   # git pull the clone, merge koda-sync.jsonl into local DB
```

`push` does a `git pull --rebase` before writing the payload so the branch stays linear. `pull` merges by `uid` — entries that already exist locally are updated only if the incoming `modified_at` is newer.

### Remote trust boundary

The sync remote is **outside your trust boundary**. Anyone who can write to it (a compromised account, a shared repo, a malicious collaborator) can change the body of any synced entry — including one bound to a shortcut you `exec`. To contain this:

- **Entries arriving via `pull` are marked `source=remote`.** `koda exec`/`koda x` **prompts for confirmation before running a `source=remote` entry**, so a silently rewritten `deploy` snippet cannot execute unattended. The recommended way to trust an entry is to **review it with `koda edit <ref>`**, which clears the flag back to `local` permanently. `koda x -f/--force` runs it once without prompting — prefer `edit` over habitually reaching for `-f`, since an `-f` baked into an alias or script silently disables the check.
- **Preview before you merge.** `koda pull --dry-run` shows the exact insert/update diff *without* touching the local database, so you can inspect incoming changes first.
- **The `source` flag never crosses the wire.** It is local-only state and is not written to (or read from) `koda-sync.jsonl`, so a remote cannot label its own entries `local` to dodge the prompt.

```bash
koda pull --dry-run     # show what would change, write nothing
koda pull               # merge; new/updated entries become source=remote
koda x deploy           # prompts because 'deploy' came from the remote
koda edit deploy        # review it → trusted (source=local), no more prompts
koda x deploy -f        # run once without prompting (does NOT clear the flag)
```

> **Disabling the prompt** — if you fully trust your sync remote, set
> `exec.confirm_remote = false` (or `koda config set exec.confirm_remote false`)
> to turn the confirmation off. **This is a security downgrade**: a compromised
> remote could then run arbitrary code via `koda x` with no confirmation. Leave
> it `true` (the default) unless you control the remote end to end.

### Setting up a second machine

```bash
# On the new machine: clone the sync repo
git clone git@github.com:YOUR_USERNAME/koda-sync.git ~/koda-sync

# Point Koda at it
koda config set git.sync_path ~/koda-sync

# Import the shared entries
koda pull
```

### Specifying a different remote

Koda resolves the remote automatically: it picks `origin` if available, otherwise the first listed remote. To use a different remote, set it as `origin` in the clone:

```bash
git -C ~/koda-sync remote set-url origin git@github.com:YOUR_USERNAME/koda-sync.git
```

Or add a named remote and set it as the tracking upstream for your branch:

```bash
git -C ~/koda-sync remote add work git@github.com:YOUR_ORG/koda-sync.git
git -C ~/koda-sync branch --set-upstream-to=work/main main
```

When an upstream is set for the current branch, Koda uses it directly (`git pull --rebase` / `git push`). When no upstream is configured, Koda calls `git push -u <remote> <branch>` to set one automatically on the first push.

### Configuration reference

| Key | Default | Description |
|---|---|---|
| `git.sync_path` | *(empty)* | Path to the local clone of the sync repository |
| `git.payload_file` | `koda-sync.jsonl` | JSONL file path, relative to `git.sync_path` |
| `git.sync_format` | `jsonl` | Wire format — only `jsonl` is supported |

Environment variable overrides:

| Variable | Purpose |
|---|---|
| `KODA_GIT_SYNC_PATH` | Override `git.sync_path` |
| `KODA_GIT_PAYLOAD_FILE` | Override `git.payload_file` |
| `KODA_GIT_SYNC_FORMAT` | Override `git.sync_format` |

## Example uses

A few highlights below. For the full collection (22 examples across Git, Docker/Kubernetes, Cloud, system ops, development, cross-machine sync, and local LLMs), see **[EXAMPLES.md](EXAMPLES.md)**.

**Shorten a verbose git command**

```bash
koda a "git log --oneline --graph --decorate --all" -t git -s glog
koda x glog
```

**Capture a container's IP and reuse it immediately**

```bash
docker inspect app | jq -r '.[0].NetworkSettings.IPAddress' | koda a -t docker
curl http://$(koda r):3000/healthz
```

**Restart any Kubernetes deployment**

```bash
koda a "kubectl rollout restart deployment/\${svc} -n production" -t k8s -s k8s-restart
koda x k8s-restart -V svc=api-gateway
```

**SSH into ephemeral instances with a flag-heavy command**

```bash
koda a "ssh -i ~/.ssh/prod.pem -o StrictHostKeyChecking=no ec2-user@\$1" -t ssh -s ec2
koda x ec2 -V 10.0.1.42
```

**Create a dated backup on every run**

Escape `\$(date)` so it expands at exec time, not at save time.

```bash
koda a "tar czf ~/backups/src-\$(date +%Y%m%d-%H%M).tar.gz ./src" -t backup -s backup
koda x backup   # creates src-20260505-1430.tar.gz each time
```

→ **[See all examples in EXAMPLES.md](EXAMPLES.md)**

---

## Security model

koda runs shell commands and syncs data across machines, so it is worth being
explicit about what it does and does not defend against.

**Threat model.** koda assumes the local user account and the local filesystem
are trusted, and that **anything reaching the database from outside — a Git
sync remote, the `KODA_*` environment variables, a hand-edited
`config.toml` — is not.** The hardening below targets that boundary.

| Area | Risk | Mitigation |
|---|---|---|
| `exec` shell | A tampered `exec.shell` could redirect `koda x` to an arbitrary binary | `exec.shell` is restricted to an allowlist (`sh`, `bash`, `zsh`, `fish`) that must resolve to an absolute executable |
| Git sync poisoning | A writable remote could rewrite the body of an `exec` entry | `pull`-merged entries are marked `source=remote` and **`koda x` prompts before running them** (review with `koda edit` to trust; `exec.confirm_remote=false` opts out, at the cost of the check); `pull --dry-run` previews changes; the `source` flag is local-only and never synced. See [Remote trust boundary](#remote-trust-boundary) |
| uid collision | The old 7-char (28-bit) `uid` was collidable (~16k entries) / preimage-attackable, enabling sync poisoning | `uid` is 16 hex chars (64-bit); legacy short uids still resolve via prefix match |
| `git.payload_file` | A relative path like `.git/hooks/post-merge` could overwrite a git hook that then executes | The validator and `push` reject any path with `..` or a `.git` component, or an absolute path |
| `db.path` | `KODA_DB_PATH=~/.ssh/authorized_keys` could create files at an attacker-chosen location | A local `db.path` is restricted to `~/.local/share/koda` / `$XDG_DATA_HOME/koda`; `KODA_DB_PATH_OVERRIDE=1` is the explicit escape hatch for CI/tests |
| File permissions | World-readable secrets on disk | `config.toml` and the database are written `0600`, their parent dirs `0700` |

**Not in scope.** Entries are stored **in plaintext** — see the repeated note
above: do not keep passwords, tokens, or other secrets in koda, and use a
**private** repository for Git sync. koda also trusts the commands you choose
to store and run; only `exec` entries you trust (or have reviewed).

## Development

```bash
uv sync                       # editable install of the project + dev tools
uv run pytest                 # run the test suite
uv run ruff check src tests   # lint
uv run ruff format src tests  # auto-format
```

Lint and format are configured under `[tool.ruff]` in `pyproject.toml`
(line length 100; rule sets `E`, `F`, `I`, `UP`).

Optionally install the [pre-commit](https://pre-commit.com) hooks so lint,
format, and whitespace checks run automatically before each commit:

```bash
uvx pre-commit install        # set up the git hook (one time)
uvx pre-commit run --all-files  # run all hooks on demand
```

---

## License

MIT (c) 2026 ngt22
