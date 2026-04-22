# Koda

A **terminal launcher and snippet store**. Save commands, config templates, and notes to SQLite; retrieve and execute them instantly — by index, shortcut, or fuzzy search. Built with Python, Typer, and Rich.

## Features

- **Launcher**: Run any saved command with `exec` or `pick`, with variable substitution at call time.
- **Fast save and recall**: `add`, `list`, `show`, `edit`, `pick`, `remove` — all with one-letter aliases.
- **Flexible input**: Arguments, heredocs, pipes, or `$EDITOR`.
- **Shortcuts**: Assign a memorable string alias to any entry and use it in place of a numeric index.
- **Variable substitution**: Expand `${KEY}` and `$1 $2 ...` placeholders at recall time with `-V`.
- **Command substitution**: Embed stored values directly in any command — `ssh $(kr bastion)`, `tail -f $(kr log-path)`.
- **Shell-friendly output**: `raw` prints body-only text for pipes, `eval`, and scripts.
- **Tags**: Classify, filter, and batch-edit entries with multiple tags.
- **Display index**: Stable `uid` (SHA1 short hash) plus user-controlled `idx`. Reorder with `move`/`swap`; close gaps with `compact`.
- **XDG-friendly**: Data under `~/.local/share/koda/`, config under `~/.config/koda/`.
- **Configurable defaults**: Persist preferences in `~/.config/koda/config.toml`.

## Installation

```bash
git clone https://github.com/ngt22/koda.git
cd koda
uv tool install .
```

## Update

```bash
cd koda
git pull
uv tool install . --force
```

## Command reference

### Built-in single-letter aliases

Each subcommand has a built-in single-letter alias:

```
a add      c copy     d remove   e edit
g config   h shift    k compact  l list
m move     p pick     r raw      s show
t tag      w swap     x exec
```

Single-letter aliases are reserved and cannot be used as entry shortcuts.

Each section below shows four equivalent forms:

| Form | Example |
|---|---|
| long | `koda exec web-srv -V localhost` |
| built-in alias | `koda x web-srv -V localhost` |
| Pattern A (`alias kd='koda'`) | `kd x web-srv -V localhost` |
| Pattern B (two-letter aliases) | `kx web-srv -V localhost` |

See [Recommended aliases](#recommended-aliases) for setup instructions.

---

### Add

Save a new entry from arguments, heredoc, stdin, or `$EDITOR`.

```bash
# Without koda — paste into a file or memorize
echo "docker compose -f docker-compose.dev.yml up --build" >> snippets.txt

# koda long form
koda add "docker compose -f docker-compose.dev.yml up --build" -t docker --shortcut dc
koda add -t infra <<'EOF'
FROM python:3.12-slim
RUN pip install uv
WORKDIR /app
EOF
koda add           # opens $EDITOR

# built-in alias
koda a "Quick note" -t quick -s dc

# Pattern A
kd a "Quick note" -t quick -s dc

# Pattern B
ka "Quick note" -t quick -s dc
```

Pipe any command output directly into `koda add`:

```bash
history | grep ffmpeg | tail -1 | koda add -t ffmpeg
kubectl get pods -o wide    | koda add -t k8s
openssl rand -hex 32        | koda add -t secret
```

---

### List

```bash
# koda long form
koda list                          # all entries ordered by display index
koda list -q "docker"              # substring search on body
koda list -t linux                 # filter by tag substring
koda list -T archive               # exclude entries tagged "archive"
koda list -n 50 -p 2              # 50 entries per page, page 2
koda list -s created_at --desc     # sort by creation date descending
koda list --shortcuts              # only entries that have a shortcut

# built-in alias
koda l -q docker -t dev

# Pattern A
kd l -q docker -t dev

# Pattern B
kl -q docker -t dev
```

Each row shows `IDX`, `UID`, `SC` (shortcut), tags, content preview, and creation time.
Sort columns: `id`, `idx`, `uid`, `tags`, `content`, `created_at`, `modified_at`, `shortcut`.

---

### Show

Display a single entry with full metadata.

```bash
# koda long form
koda show 5
koda show web-srv      # by shortcut
echo 5 | koda show     # ref from stdin

# built-in alias
koda s web-srv

# Pattern A
kd s web-srv

# Pattern B
ks web-srv
```

---

### Edit

Open an entry in `$EDITOR`. The footer contains editable metadata (tags, shortcut).

```bash
# koda long form
koda edit 5
koda edit web-srv      # by shortcut

# built-in alias
koda e web-srv

# Pattern A
kd e web-srv

# Pattern B
ke web-srv
```

---

### Remove

Delete one or more entries.

```bash
# koda long form
koda remove 5
koda remove web-srv          # by shortcut
koda remove 1 3 5-8          # multiple entries and ranges
koda remove -t archive        # delete all tagged "archive"
koda remove -q "tmp"          # delete entries matching body substring
koda remove --all -f          # delete everything (--all always requires -f)

# built-in alias
koda d web-srv

# Pattern A
kd d web-srv

# Pattern B
kd web-srv                   # kd = koda remove in Pattern B
```

---

### Copy

Duplicate an entry. The body and tags are copied; the shortcut is not.

```bash
# koda long form
koda copy 5
koda copy web-srv

# built-in alias
koda c web-srv

# Pattern A
kd c web-srv

# Pattern B
kc web-srv
```

---

### Execute (`exec`) — run a saved command

Run a saved entry as a shell command, with optional variable substitution.

```bash
# Without koda — retype or search history every time
ssh -i ~/.ssh/key.pem ec2-user@192.168.1.100

# Store once, run by index or shortcut
koda add "ssh -i ~/.ssh/key.pem ec2-user@\$1" -t ssh --shortcut web-srv

# koda long form
koda exec 12                      # by index
koda exec web-srv                 # by shortcut
koda exec 12 -V host=localhost    # named substitution
koda exec web-srv -V localhost    # positional substitution

# built-in alias
koda x web-srv -V localhost

# Pattern A
kd x web-srv -V localhost

# Pattern B
kx web-srv -V localhost
```

> **Security**: only store trusted commands. `exec` runs the body through the configured shell (`sh` by default).

---

### Raw

Print the entry body to stdout only (no Rich formatting). Use for pipes, `eval`, and command substitution.

```bash
# koda long form
koda raw 5
koda raw web-srv        # by shortcut
koda raw                # latest entry
echo 5 | koda raw       # ref from stdin

# built-in alias
koda r web-srv

# Pattern A
kd r web-srv

# Pattern B
kr web-srv
```

**Command substitution** — embed a stored value directly inside any command you type:

```bash
# Without koda — retype long strings inline every time
ssh -i ~/.ssh/key.pem ec2-user@bastion.prod.example.com
tail -f /var/log/nginx/access.log
curl -H "Authorization: Bearer eyJhbGciOiJSUzI1Ni..." https://api.example.com/v1/status

# Store once, reference by shortcut
koda add "bastion.prod.example.com"       -t ssh    --shortcut bastion
koda add "/var/log/nginx/access.log"      -t log    --shortcut nginx-log
koda add "eyJhbGciOiJSUzI1Ni..."          -t secret --shortcut api-token

# Embed in any command with $()
ssh -i ~/.ssh/key.pem ec2-user@$(koda raw bastion)
tail -f $(koda raw nginx-log)
curl -H "Authorization: Bearer $(koda raw api-token)" https://api.example.com/v1/status

# Pattern A
ssh -i ~/.ssh/key.pem ec2-user@$(kd r bastion)
tail -f $(kd r nginx-log)

# Pattern B — shortest form
ssh -i ~/.ssh/key.pem ec2-user@$(kr bastion)
tail -f $(kr nginx-log)
curl -H "Authorization: Bearer $(kr api-token)" https://api.example.com/v1/status
```

`raw` strips shell-style inline comments (`#` at line start or after whitespace). Use `show` to see the original stored text.

```bash
koda add 'echo hello  # this is a comment'
koda raw 5    # → echo hello
koda show 5   # → echo hello  # this is a comment
```

---

### Pick — interactive launcher (fzf)

Interactively select an entry with `fzf`, then run an action. Requires [`fzf`](https://github.com/junegunn/fzf) and an interactive TTY.

**Main use case — pick and execute immediately:**

```bash
# koda long form
koda pick --exec                     # pick from all entries, execute immediately
koda pick --exec -q docker -t dev    # pre-filter by query and tag, then pick

# built-in alias
koda p -x

# Pattern A
kd p -x
kd p -x -q docker -t dev

# Pattern B
kp -x
kp -x -q docker -t dev
```

**Compound patterns — use pick as a selector:**

```bash
# Eval the body of the selected entry (requires defaults.cmd = raw)
eval $(koda pick -p | xargs koda raw)
eval $(kd p -p | xargs kd r)    # Pattern A
eval $(kp -p | xargs kr)        # Pattern B

# Pass the selected IDX to exec
koda exec "$(koda pick -p)"
kd x "$(kd p -p)"               # Pattern A
kx "$(kp -p)"                   # Pattern B
```

Other action flags: `-e` edit, `-r` raw, `-s` show.
`-p` (print IDX only) cannot be combined with action flags.

---

### Shortcuts

Assign a memorable string alias to any entry and use it instead of a numeric index.

```bash
# Save with a shortcut
koda add "kubectl rollout restart deploy/api" -t k8s --shortcut restart

# Use the shortcut anywhere an index is accepted
koda raw restart
koda exec restart
koda show restart
koda remove restart

# Default command — no subcommand needed (when defaults.cmd = raw)
koda restart          # → koda raw restart

# List all entries that have shortcuts
koda list --shortcuts
koda list -S --sort-by shortcut
```

To change or remove a shortcut, open the entry with `edit` — the `shortcut:` field appears in the metadata footer.

---

### Variable substitution (`-V` / `--var`)

Embed placeholders in a saved entry; fill them in at recall time with `-V`.

| Style | Placeholder | How to pass |
|---|---|---|
| Named | `${host}` | `-V KEY=VALUE` |
| Positional | `$1`, `$2`, ... | `-V value` (left-to-right) |

```bash
# Save a template with a positional placeholder
koda add "gcloud storage cp \$1 gs://my-company-analytics-prod/uploads/" -t gcloud -s upload

# Run it with different values — no need to retype the bucket path
koda exec upload -V ./report.csv
koda exec upload -V ./summary.csv
kx upload -V ./report.csv          # Pattern B

# Named substitution — swap one variable by name
koda add "aws s3 sync ./dist s3://acme-frontend-\${env}-us-east-1/app/" -t aws -s deploy
koda exec deploy -V env=prod
koda exec deploy -V env=staging
kx deploy -V env=prod              # Pattern B

# Positional with multiple values
koda add "rsync -avz \$1 \$2" -t rsync
koda raw 8 -V /src/path -V user@host:/dest
koda raw 8 -V "/src/path user@host:/dest"    # same result

# Mix named and positional
koda raw 9 -V "admin 5432" -V host=db.example.com -V name="new york"
# → connect admin@db.example.com:5432 as new york
```

Values with spaces must be quoted so the shell passes them as a single token.

---

### Reorder entries (`move`, `swap`, `shift`, `compact`)

Each entry has a display index (`IDX`) you can freely rearrange — useful for keeping frequently used snippets at low numbers.

```bash
# koda long form
koda swap 3 0           # exchange display positions of entries 3 and 0
koda move 7 1           # move entry 7 to empty position 1
koda shift 1            # shift entries at index 1+ up by 1 (makes room at 1)
koda shift 1 -n 3       # shift up by 3 positions
koda shift 5 -n -1      # shift entries from index 5 downward by 1
koda compact            # reassign all indices to 0..n-1, fill gaps

# built-in alias
koda w 3 0
koda m 7 1
koda h 1
koda k

# Pattern A
kd w 3 0
kd m 7 1
kd h 1
kd k

# Pattern B
kw 3 0
km 7 1
kh 1
kk
```

`move` requires the destination index to be unoccupied. Use `shift` to make room first, or `swap` to exchange two occupied positions.

---

### Batch tag (`tag`)

```bash
# koda long form
koda tag 1 3 5 -t work          # add tag to individual entries
koda tag 2-6 -t archive         # add tag to a range
koda tag 1 3-5 7 -T old         # remove tag from mixed selection
koda tag 1 -t new -T old        # add one tag and remove another in one command

# built-in alias
koda t 1 3-5 -t archive

# Pattern A
kd t 1 3-5 -t archive

# Pattern B
kt 1 3-5 -t archive
```

Re-tagging with an already-present tag is idempotent (no-op).

---

### Configuration (`config`)

```bash
# koda long form
koda config                           # show all settings with source
koda config get defaults.cmd          # print a single value
koda config set defaults.cmd list     # write to config file
koda config unset list.per_page       # remove key (reverts to built-in default)
koda config reset                     # delete config file (prompts for confirmation)
koda config reset -f                  # delete without prompt
koda config edit                      # open config in $EDITOR
koda config path                      # print config file path

# built-in alias
koda g set defaults.cmd list

# Pattern A
kd g set defaults.cmd list

# Pattern B
kg set defaults.cmd list
```

---

## Recommended aliases

Two patterns are available. Choose one based on how much you want to shorten your workflow.

---

### Pattern A — minimal (`kd='koda'` only)

Register only `kd` as an alias for `koda`, then use koda's built-in single-letter aliases for subcommands. No risk of conflicting with other tools.

```bash
# Add to ~/.zshrc or ~/.bashrc
alias kd='koda'
```

Examples with Pattern A:

```bash
kd a "memo" -t tag -s sc    # add
kd l -q docker              # list
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

### Pattern B — full two-letter aliases

Register a two-letter alias for every subcommand. Shorter to type, but check for conflicts before adding.

> **Note**: Pattern B assigns `kd` to `koda remove`. If you previously used `alias kd='koda'` (Pattern A), remove that line first.

```bash
# Add to ~/.zshrc or ~/.bashrc
alias ka='koda add'
alias kl='koda list'
alias ks='koda show'
alias ke='koda edit'
alias kr='koda raw'
alias kx='koda exec'
alias kp='koda pick'
alias kd='koda remove'   # ← replaces kd='koda' if you had Pattern A
alias kc='koda copy'
alias km='koda move'
alias kw='koda swap'
alias kh='koda shift'
alias kk='koda compact'
alias kt='koda tag'
alias kg='koda config'
```

**Potential conflicts — check before adding:**

| Alias | Possible conflict |
|---|---|
| `ks` | Kakoune session manager on some setups |
| `kt` | Kotlin toolchain (`ktlint`, etc.) in some environments |

Run `alias` in your shell to see what is already defined before adding these.

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

[db]
path = "~/.local/share/koda/koda.db"

[exec]
shell = "sh"      # shell used by exec
```

Priority order: **CLI flags > environment variables > config file > built-in defaults**

## Environment variables

| Variable | Purpose |
|---|---|
| `KODA_DB_PATH` | Override database file path |
| `KODA_DEFAULT_CMD` | Override `defaults.cmd` for this session |
| `KODA_CONFIG_PATH` | Override config file path |
| `EDITOR` | Editor for `add`, `edit`, and `config edit` (default: `vim`) |

## License

MIT (c) 2026 ngt22
