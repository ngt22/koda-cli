# Koda

A **memo and snippet CLI** for the terminal. Store frequently used commands, config fragments, and notes in SQLite, then pull them back instantly with `list`, `show`, and search. Built with Python, Typer, and Rich.

## Features

- **Fast save and recall**: Manage entries with `add`, `ls`, `show`, `edit`, and `rm`.
- **Flexible input**: Arguments, heredocs, pipes, or `$EDITOR`.
- **Tags**: Classify, filter, and batch-edit with multiple tags.
- **Shortcuts**: Assign a memorable string alias to any entry and use it in place of a numeric index.
- **Shell-friendly**: `raw` prints body-only text for `eval`, aliases, and scripts.
- **Variable substitution**: Expand `${KEY}` and `$1 $2 ...` placeholders at recall time with `--var` / `-V`.
- **Display index**: Each entry has a stable `uid` (sha1 short hash) and a user-controlled `idx`. Reorder freely with `mv`/`sw`, and close gaps with `compact`.
- **XDG-friendly**: Default data under `~/.local/share/koda/`, config under `~/.config/koda/`.
- **Configurable defaults**: Persist preferences like default command or list page size in `~/.config/koda/config.toml`.

## Installation

```bash
git clone https://github.com/ngt22/koda.git
cd koda
uv tool install .
```

## Update

To update to the latest version:

```bash
cd koda
git pull
uv tool install . --force
```

## Recommended alias

Add this to `.zshrc` or `.bashrc` to type less:

```bash
alias kd='koda'
```

## Example use cases

### 1. Save shell one-liners as runnable snippets

Stop memorizing long `docker`, `kubectl`, or `ffmpeg` invocations.

```bash
koda add "docker compose -f docker-compose.dev.yml up --build" -t docker,dev
koda ls
# Suppose the new entry is at display index 12 — execute directly:
koda ex 12
```

Put **only the command you intend to run** in the body. The editor template keeps metadata in a separate `---` block. Because `ex` runs shell code, **store only trusted text**.

### 2. Config templates and Dockerfile fragments

Tag infra snippets and paste the full body with `show` when needed.

```bash
koda add -t docker,template <<'EOF'
FROM python:3.12-slim
RUN pip install uv
WORKDIR /app
EOF
koda show   # show the entry just added (latest)
```

### 3. Reuse API `curl` commands or SQL

Save authenticated `curl` one-liners or common `SELECT` statements; search with `ls -q`.

```bash
koda add -t api,curl 'curl -sS -H "Authorization: Bearer $TOKEN" https://api.example.com/v1/status'
koda ls -q "curl"
```

### 4. Cheat sheets and plain notes (not only commands)

Git recipes, install commands, meeting notes — anything that fits in plain text.

```bash
koda add "git reset --soft HEAD~1   # undo last commit, keep changes" -t git
koda ls -t git
```

### 5. Execute stored commands and pipe into scripts

`raw` writes plain stdout (no Rich), so `$(koda raw <IDX>)` feeds the body directly into another command as an argument. Use `koda ex` to run the body as a shell command. **Keep the body simple** when you automate: extra newlines or prose can break word splitting.

```bash
# Store a path
koda add "/var/log/nginx/access.log" -t log

# Use the stored path as a command argument
tail -f $(koda raw)

# Store and reuse a hostname
koda add "user@prod.example.com" -t ssh
ssh $(koda raw)

# Execute the latest entry as a shell command
koda ex

# Execute entry at index 5 (works for single-line and multi-line)
koda ex 5

# Capture body into a variable
CONTENT=$(koda raw)
echo "$CONTENT"
```

## Command reference

### Add

```bash
koda add "One-line memo" -t tag1,tag2
koda add -t snippet <<EOF
multi-line
snippet
EOF
koda add   # opens $EDITOR

# Assign a shortcut alias at save time:
koda add "docker compose up --build" -t docker --shortcut dc
```

Any command's output can be piped directly into `koda add`:

```bash
# Save a command you just ran from history
history | grep "ffmpeg" | tail -1 | koda add -t ffmpeg

# Save the output of a command as a note
kubectl get pods -o wide | koda add -t k8s

# Save a generated value (e.g. a token or key) for later use
openssl rand -hex 32 | koda add -t secret

# Save a file path found by a search
find /etc -name "*.conf" | fzf | koda add -t path

# Save multi-line output (e.g. a config snippet)
cat ~/.ssh/config | koda add -t ssh
```

### List and search

```bash
koda ls                        # entries ordered by display index (IDX)
koda ls -n 50                  # 50 entries per page
koda ls -p 2                   # show page 2
koda ls -n 25 -p 3             # page 3 at 25 entries per page
koda ls --rows 1               # 1-line content preview (default)
koda ls --rows 10              # 10-line content preview
koda ls --rows 0               # show all lines
koda ls --truncate 80          # truncate lines at 80 characters
koda ls --truncate 0           # disable line truncation
koda ls -s created_at --desc   # sort by created_at descending
koda ls -s shortcut --asc      # sort alphabetically by shortcut
koda ls -q "docker"            # substring search on body
koda ls -t "linux"             # filter by tag substring
koda ls -T "archive"           # exclude entries tagged "archive"
koda ls --shortcuts            # show only entries that have a shortcut (-S)
```

Each row shows `IDX` (display index), `UID` (7-char sha1), `SC` (shortcut), tags, content preview, and creation time.
`ls` always prints summary stats below the table: total entries, total pages, and max IDX.
Sort columns are: `id`, `idx`, `uid`, `tags`, `content`, `created_at`, `modified_at`, `shortcut`. Use `--desc` / `--asc` to choose direction.
Use `--rows 0` to display full content lines in the list.
Use `--truncate` to control max characters per content line (`0` disables truncation).
Use `--exclude-tag` / `-T` to hide entries that match a tag substring.

### Show, edit, remove

```bash
koda show 1
koda show deploy         # look up by shortcut
echo 1 | koda show       # read one ref from stdin
koda edit 1
koda edit deploy         # edit entry by shortcut (shortcut editable in footer)
koda cp 1                # duplicate to a new entry (shortcut is not copied)
koda rm 1                # delete with confirmation
koda rm deploy           # delete by shortcut
koda rm 1 3 5-8          # delete multiple entries or ranges
koda rm -t archive       # delete all entries tagged "archive"
koda rm -q "tmp"         # delete entries matching body substring
koda rm --all -f         # delete everything (--all always requires -f)
```

### Shortcuts

Assign a memorable alias to any entry and use it instead of a numeric index:

```bash
# Save with a shortcut:
koda add "kubectl rollout restart deploy/api" -t k8s --shortcut restart

# Use the shortcut anywhere an index is accepted:
koda raw restart       # print body
koda ex restart        # execute
koda show restart      # show with metadata
koda rm restart        # delete

# Or use the default command directly (no subcommand needed):
koda restart           # same as `koda raw restart` when defaults.cmd = raw

# List all entries that have shortcuts:
koda ls --shortcuts
koda ls -S --sort-by shortcut
```

To change or remove a shortcut, use `edit` — the `shortcut:` field appears in the metadata footer.

### Body-only stdout (scripts and shells)

```bash
koda raw          # latest entry body only
koda raw 5        # entry at display index 5, body only
koda raw deploy   # entry with shortcut "deploy", body only
echo 5 | koda raw # read ref(s) from stdin
koda 5            # numeric args route to the default command
koda deploy       # shortcut args also route to the default command
```

`show`/`ex` accept one stdin ref when no argument is given. `raw` accepts one or more whitespace-separated refs from stdin when no argument is given.

`raw` treats inline comments like shell scripts: an unquoted `#` at the start of a line or after whitespace hides everything to the right on that line. `show` always displays the original stored text.

```bash
koda add 'echo hello # comment'
koda raw 10      # -> echo hello
koda show 10     # -> echo hello # comment
```

To keep `#` as a literal character in `raw`, escape or quote it:

```bash
echo \#literal
echo '#literal'
echo "value#suffix"   # no whitespace before #
```

### Reorder entries (`mv`, `sw`, `shift`, `compact`)

Each entry has a display index (`IDX`) you can freely rearrange — handy for keeping frequently used snippets at low numbers (0–9).

```bash
koda sw 3 0        # swap display positions of entries 3 and 0
koda mv 7 1        # move entry 7 to empty position 1 (position must be unoccupied)
koda shift 1       # shift all entries at index 1 and above up by 1 (makes room at 1)
koda shift 1 -n 3  # shift up by 3 positions
koda shift 5 -n -1 # shift entries from index 5 downward by 1
koda compact       # reassign indices to 0..n-1 and fill gaps
```

`mv` requires the destination index to be unoccupied. Use `shift` first to make room, or `sw` to exchange two occupied positions.

Long-form aliases: `move` → `mv`, `swap` → `sw`.

### Batch tag (`tag`)

```bash
koda tag 1 3 5 -t work          # add tag to individual entries
koda tag 2-6 -t archive         # add tag to a range
koda tag 1 3-5 7 -T old         # remove tag from mixed selection
koda tag 1 -t new -T old        # add one tag and remove another in one command
```

Re-tagging with an already-present tag is a no-op (idempotent).

### Variable substitution (`--var` / `-V`)

Embed placeholders in a memo at save time, then fill them in at recall time.

Two placeholder styles:

| Style | Placeholder | How to pass |
|---|---|---|
| Named | `${host}` | `KEY=VALUE` |
| Positional | `$1`, `$2`, ... | bare values, left-to-right |

**Positional substitution** — use bare values; they map to `$1`, `$2`, ... in order:

Cloud bucket names are long and hard to retype. Store the command once, pass only the part that changes:

```bash
# Without Koda — retype the full bucket path every time:
gcloud storage cp ./report.csv gs://my-company-analytics-prod-us-central1/uploads/

# Store once with a positional placeholder for the local file:
koda add "gcloud storage cp \$1 gs://my-company-analytics-prod-us-central1/uploads/" -t gcloud,storage

# From now on:
koda ex -V ./report.csv
koda ex -V ./summary.csv
```

Same pattern works for AWS S3:

```bash
koda add "aws s3 sync \$1 s3://acme-frontend-assets-prod-us-east-1/app/" -t aws,s3
koda ex -V ./dist
koda ex -V ./build
```

Pass two positional values with repeated `-V` flags or space-separated in one:

```bash
koda add "rsync -avz \$1 \$2" -t rsync
koda raw 8 -V /src/path -V /user@host:/dest
koda raw 8 -V "/src/path /user@host:/dest"   # same result
```

**Named substitution** — use `KEY=VALUE` when you want to swap a specific part by name:

```bash
# Deploy to different environments by changing one variable:
koda add "aws s3 sync ./dist s3://acme-frontend-\${env}-us-east-1/app/" -t aws,s3,deploy

koda ex -V env=prod
koda ex -V env=staging
```

**Mix named and positional** — order of `-V` flags determines positional index:

```bash
# Template: connect $1@${host}:$2 as ${name}
koda raw 9 -V "admin 5432" -V host=db.example.com -V name="new york"
# → connect admin@db.example.com:5432 as new york
```

Values containing spaces must be quoted so the shell passes them as one token:

```bash
-V name="new york"      # value is: new york
-V "admin 5432"         # two positional values: admin → $1, 5432 → $2
```

**Execute with substitution** (`ex` command):

```bash
koda ex 7 -V env=prod
koda ex 7 -V ./report.csv
```

## Configuration

Koda reads `~/.config/koda/config.toml` (XDG: `$XDG_CONFIG_HOME/koda/config.toml`).
All settings are optional — unset values fall back to the built-in defaults.

```toml
[defaults]
cmd = "raw"       # "raw", "list", "show", or "add" — what bare `kd` does

[list]
per_page = 20     # entries per page
rows = 1          # content preview lines (0 = all)
truncate = 80     # max chars per line (0 = no truncation)
sort_by = "idx"   # default sort column
desc = false      # sort direction

[db]
path = "~/.local/share/koda/koda.db"

[exec]
shell = "sh"      # shell used by `ex`
```

### `config` subcommand

```bash
koda config                       # show all settings with source (default/file/env)
koda config get defaults.cmd      # print a single value
koda config set defaults.cmd list # write to config file
koda config unset list.per_page   # remove key (reverts to default)
koda config reset                 # delete config file (prompts for confirmation)
koda config reset -f              # delete without prompt
koda config edit                  # open config file in $EDITOR
koda config path                  # print config file path
```

Priority order: **CLI flags > environment variables > config file > built-in defaults**

## Environment variables

| Variable           | Purpose |
|--------------------|---------|
| `KODA_DB_PATH`     | Override database file path |
| `KODA_DEFAULT_CMD` | Override `defaults.cmd` for this session |
| `KODA_CONFIG_PATH` | Override config file path |
| `EDITOR`           | Editor for `add`, `edit`, and `config edit` (default: `vim`) |

## License

MIT (c) 2026 ngt22
