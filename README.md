# Koda

A **terminal launcher and snippet store**. Save commands, config templates, and notes to SQLite; retrieve and execute them instantly — by index, shortcut, or fuzzy search. Built with Python, Typer, and Rich.

## Features

- **Launcher**: Run any saved command with `exec` or `pick`, with variable substitution at call time.
- **Command substitution**: Embed stored values directly in any command — `ssh $(kr bastion)`, `tail -f $(kr log-path)`.
- **Fast save and recall**: `add`, `list`, `show`, `edit`, `pick`, `remove` — all with one-letter aliases.
- **Flexible input**: Arguments, heredocs, pipes, or `$EDITOR`.
- **Shortcuts**: Assign a memorable string alias to any entry and use it in place of a numeric index.
- **Variable substitution**: Expand `${KEY}` and `$1 $2 ...` placeholders at recall time with `-V`.
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

Each section below shows these equivalent forms:

| Form | Example |
|---|---|
| long | `koda exec web-srv -V localhost` |
| built-in alias | `koda x web-srv -V localhost` |
| kd prefix<br>(`alias kd='koda'`) | `kd x web-srv -V localhost` |
| two-letter alias<br>(e.g. `alias kx='koda exec'`) | `kx web-srv -V localhost` |

See [Recommended aliases](#recommended-aliases) for setup instructions.

---

### Add

Save a new entry from arguments, heredoc, stdin, or `$EDITOR`.

```bash
koda a "docker compose up --build" -t docker -s dc
koda a "quick note" -t work
koda a              # opens $EDITOR
```

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
openssl rand -hex 32        | koda a -t secret
```

Full form and aliases:

```bash
koda add "memo" -t tag --shortcut sc   # long form
kd a "memo" -t tag -s sc              # kd prefix
ka "memo" -t tag -s sc                # two-letter alias
```

---

### Raw — body-only output

Print the entry body to stdout (no Rich formatting). Use for pipes, `eval`, and command substitution.

```bash
koda r web-srv        # by shortcut
koda r 5              # by index
koda r                # latest entry
echo 5 | koda r       # ref from stdin
```

Full form and aliases:

```bash
koda raw web-srv   # long form
kd r web-srv       # kd prefix
kr web-srv         # two-letter alias
```

**Command substitution — embed a stored value inside any command:**

```bash
# Without koda — retype long strings inline every time
ssh -i ~/.ssh/key.pem ec2-user@bastion.prod.example.com
tail -f /var/log/nginx/access.log

# Save once
koda a "bastion.prod.example.com"  -t ssh -s bastion
koda a "/var/log/nginx/access.log" -t log -s nginx-log

# Embed with $() — using two-letter alias
ssh -i ~/.ssh/key.pem ec2-user@$(kr bastion)
tail -f $(kr nginx-log)

# kd prefix
ssh -i ~/.ssh/key.pem ec2-user@$(kd r bastion)
tail -f $(kd r nginx-log)
```

**Workflow example — save a token via pipe, reuse in requests:**

```bash
# Step 1: obtain a token and save it
curl -s -X POST https://auth.example.com/token \
  -d '{"client_id":"myapp","client_secret":"s3cr3t"}' \
  | jq -r '.access_token' \
  | koda a -t api,token -s api-token

# Step 2: embed in every subsequent request — no copy-paste
curl -H "Authorization: Bearer $(kr api-token)" \
  https://api.example.com/v1/users

# kd prefix
curl -H "Authorization: Bearer $(kd r api-token)" \
  https://api.example.com/v1/users

# Refresh when expired
koda e api-token   # opens $EDITOR to paste the new value
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
koda l -q "docker"              # substring search on body
koda l -t linux                 # filter by tag substring
koda l -T archive               # exclude entries tagged "archive"
koda l -S                       # only entries that have a shortcut
koda l -n 50 -p 2              # 50 entries per page, page 2
koda l -s created_at --desc     # sort by creation date descending
```

Full form and aliases:

```bash
koda list -q docker -t dev   # long form
kd l -q docker -t dev        # kd prefix
kl -q docker -t dev          # two-letter alias
```

Each row shows `IDX`, `UID`, `SC` (shortcut), tags, content preview, and creation time.
Sort columns: `id`, `idx`, `uid`, `tags`, `content`, `created_at`, `modified_at`, `shortcut`.

---

### Shortcuts

Shortcuts and variable substitution are not subcommands — they are options (`-s`, `-V`) that work across multiple commands.

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

### Variable substitution (`-V` / `--var`)

Embed placeholders in a saved entry; fill them in at recall time with `-V`.

| Style | Placeholder | How to pass |
|---|---|---|
| Named | `${host}` | `-V KEY=VALUE` |
| Positional | `$1`, `$2`, ... | `-V value` (left-to-right) |

```bash
# Save a template with a positional placeholder
koda a "gcloud storage cp \$1 gs://my-company-analytics-prod/uploads/" -t gcloud -s upload

# Run with different values — no need to retype the bucket path
koda x upload -V ./report.csv
koda x upload -V ./summary.csv
kx upload -V ./report.csv          # two-letter alias

# Named substitution — swap one variable by name
koda a "aws s3 sync ./dist s3://acme-frontend-\${env}-us-east-1/app/" -t aws -s deploy
koda x deploy -V env=prod
koda x deploy -V env=staging
kx deploy -V env=prod              # two-letter alias

# Multiple positional values
koda a "rsync -avz \$1 \$2" -t rsync
koda r 8 -V /src/path -V user@host:/dest
koda r 8 -V "/src/path user@host:/dest"    # same result

# Mix named and positional
koda r 9 -V "admin 5432" -V host=db.example.com -V name="new york"
# → connect admin@db.example.com:5432 as new york
```

Values with spaces must be quoted so the shell passes them as a single token.

---

### Execute (`exec`) — run a saved command

Run a saved entry as a shell command, with optional variable substitution.

```bash
# Without koda — retype or search history every time
ssh -i ~/.ssh/key.pem ec2-user@192.168.1.100

# Save once
koda a "ssh -i ~/.ssh/key.pem ec2-user@\$1" -t ssh -s web-srv
```

```bash
koda x web-srv              # run by shortcut
koda x web-srv -V prod      # with variable substitution
koda x 12                   # run by index
```

Full form and aliases:

```bash
koda exec web-srv -V localhost   # long form
kd x web-srv -V localhost        # kd prefix
kx web-srv -V localhost          # two-letter alias
```

**Workflow example — query a local LLM with a one-liner:**

```bash
# Save once via heredoc
koda a -t llm -s ask <<'EOF'
curl -sS http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "$1"}], "stream": false}' \
  | jq -r '.choices[0].message.content'
EOF

# Ask anything — no copy-paste, no editing
koda x ask -V "How high is Mt. Fuji?"
kx ask -V "Summarize the last git commit"   # two-letter alias
```

> **Security**: only store trusted commands. `exec` runs the body through the configured shell (`sh` by default).

---

### Edit

Open an entry in `$EDITOR`. The footer contains editable metadata (tags, shortcut).

```bash
koda e web-srv        # by shortcut
koda e 5              # by index
```

Full form and aliases:

```bash
koda edit web-srv   # long form
kd e web-srv        # kd prefix
ke web-srv          # two-letter alias
```

---

### Pick — interactive launcher (fzf)

Interactively select an entry with `fzf`, then run an action. Requires [`fzf`](https://github.com/junegunn/fzf) and an interactive TTY.

```bash
koda p -x                      # pick from all entries, execute immediately
koda p -x -q docker -t dev     # pre-filter by query and tag, then pick
```

Full form and aliases:

```bash
koda pick --exec -q docker -t dev   # long form
kd p -x -q docker -t dev            # kd prefix
kp -x -q docker -t dev              # two-letter alias
```

**Compound patterns — use pick as a selector:**

```bash
koda x "$(koda p -p)"          # pick IDX, pass to exec
kd x "$(kd p -p)"              # kd prefix
kx "$(kp -p)"                  # two-letter alias

eval $(kp -p | xargs kr)       # pick IDX, eval the body
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

Full form and aliases:

```bash
koda show web-srv   # long form
kd s web-srv        # kd prefix
ks web-srv          # two-letter alias
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

Full form and aliases:

```bash
koda remove web-srv   # long form
kd d web-srv          # kd prefix
kd web-srv            # two-letter alias (kd = koda remove)
```

---

### Copy

Duplicate an entry. The body and tags are copied; the shortcut is not.

```bash
koda c web-srv        # by shortcut
koda c 5              # by index
```

Full form and aliases:

```bash
koda copy web-srv   # long form
kd c web-srv        # kd prefix
kc web-srv          # two-letter alias
```

---

### Tag

```bash
koda t 1 3 5 -t work          # add tag to individual entries
koda t 2-6 -t archive         # add tag to a range
koda t 1 3-5 7 -T old         # remove tag from mixed selection
koda t 1 -t new -T old        # add one tag and remove another in one command
```

Full form and aliases:

```bash
koda tag 1 3-5 -t archive   # long form
kd t 1 3-5 -t archive       # kd prefix
kt 1 3-5 -t archive         # two-letter alias
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

Full form and aliases:

```bash
koda swap 3 0    kd w 3 0    kw 3 0
koda move 7 1    kd m 7 1    km 7 1
koda shift 1     kd h 1      kh 1
koda compact     kd k        kk
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

Full form and aliases:

```bash
koda config set defaults.cmd list   # long form
kd g set defaults.cmd list          # kd prefix
kg set defaults.cmd list            # two-letter alias
```

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
