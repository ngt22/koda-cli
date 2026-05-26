# koda-cli

A **text store** for the terminal. Save any text — commands, paths, templates, notes — to SQLite and recall it instantly by index, shortcut, or fuzzy search. Saved entries can be executed as shell commands, making koda a **terminal launcher**. Sync via a private Git repository to share the same store across machines, giving you a **cross-machine clipboard** that works from any terminal. Built with Python, Typer, and Rich.

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

| Command | Alias | Description |
|---|---|---|
| [`add`](#add) | `a` | Save a new entry |
| [`raw`](#raw--body-only-output) | `r` | Print entry body to stdout |
| [`list`](#list) | `l` | List and filter entries |
| [`exec`](#execute-exec--run-a-saved-command) | `x` | Run a saved entry as a shell command |
| [`edit`](#edit) | `e` | Open entry in `$EDITOR` |
| [`pick`](#pick--interactive-launcher-fzf) | `p` | Interactive selector (requires fzf) |
| [`show`](#show) | `s` | Display entry with full metadata |
| [`remove`](#remove) | `d` | Delete entries |
| [`copy`](#copy) | `c` | Duplicate an entry |
| [`tag`](#tag) | `t` | Batch-add or remove tags |
| [`move`](#reorder-entries-move-swap-shift-compact) | `m` | Move entry to a display index |
| [`swap`](#reorder-entries-move-swap-shift-compact) | `w` | Swap display positions of two entries |
| [`shift`](#reorder-entries-move-swap-shift-compact) | `h` | Shift entries up or down by N |
| [`compact`](#reorder-entries-move-swap-shift-compact) | `k` | Reassign indices to 0..n-1 |
| [`config`](#configuration-config) | `g` | Read/write configuration |
| [`push`](#push-and-pull) | — | Export DB to Git sync repo and push |
| [`pull`](#push-and-pull) | — | Pull Git sync repo and merge into local DB |

Single-letter aliases are reserved and cannot be used as entry shortcuts.

### Options

| Option | Description |
|---|---|
| `-s` / `--shortcut` | Assign a memorable alias to an entry |
| `-t` / `--tag` | Assign tags (on `add`) or filter by tag |
| `-V` / `--var` | Variable substitution at recall time |

## Installation

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
koda l -q "docker"              # substring search on body
koda l -t linux                 # filter by tag substring
koda l -T archive               # exclude entries tagged "archive"
koda l -S                       # only entries that have a shortcut
koda l -n 50 -p 2              # 50 entries per page, page 2
koda l -s created_at --desc     # sort by creation date descending
koda l --columns idx,uid,sc,tags,content,created_at   # all columns
koda l --columns idx,content    # minimal view
```

Default columns: `IDX`, `SC`, `Tags`, `Content`. Available columns: `idx`, `uid`, `sc`, `tags`, `content`, `created_at` (`idx` is required).
Sort columns: `id`, `idx`, `uid`, `tags`, `content`, `created_at`, `modified_at`, `shortcut`.

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
```

**Deferred command substitution — `\$()` expands at exec time, not at save time:**

When you escape `$` with a backslash in `koda a "..."`, the shell stores the literal text `$(...)` unchanged. `koda x` then passes it to the shell, which evaluates it at that moment. This is useful for values that change on every run.

```bash
# Save once — \$() is stored literally
koda a "tar czf ~/backups/src-\$(date +%Y%m%d-%H%M).tar.gz ./src" -t backup -s backup

# Each invocation stamps the current date and time
koda x backup   # runs: tar czf ~/backups/src-20260505-1430.tar.gz ./src
koda x backup   # runs: tar czf ~/backups/src-20260506-0910.tar.gz ./src
```

> **Security**: only store trusted commands. `exec` runs the body through the configured shell (`sh` by default).

---

### Edit

Open an entry in `$EDITOR`. The footer contains editable metadata (tags, shortcut).

```bash
koda e web-srv        # by shortcut
koda e 5              # by index
```

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
columns = ["idx", "sc", "tags", "content"]   # idx is required; available: idx, uid, sc, tags, content, created_at

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
shell = "sh"      # shell used by exec
```

Priority order: **CLI flags > environment variables > config file > built-in defaults**

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

A collection of concrete examples across different domains.

---

### Git

**1. Shorten a verbose git log command**

Save a long git log command and run it with a short name.

```bash
koda a "git log --oneline --graph --decorate --all" -t git -s glog
koda x glog
```

**2. Tag the current commit and push it**

Save a one-liner that tags HEAD and pushes the tag in one step.

```bash
koda a "git tag \$1 \$(git rev-parse --short HEAD) && git push origin \$1" -t git -s tag-push
koda x tag-push -V v1.2.3
```

---

### Docker / Kubernetes

**3. Capture a container's IP and reuse it immediately**

Pipe `docker inspect` output into koda, then embed the saved value with `$(koda r)`.

```bash
docker inspect app | jq -r '.[0].NetworkSettings.IPAddress' | koda a -t docker
curl http://$(koda r):3000/healthz
```

**4. Restart any Kubernetes deployment**

Save a rollout restart command with a named placeholder for the service.

```bash
koda a "kubectl rollout restart deployment/\${svc} -n production" -t k8s -s k8s-restart
koda x k8s-restart -V svc=api-gateway
```

**5. Port-forward to any service**

Save a kubectl port-forward template; supply service and port at call time.

```bash
koda a "kubectl port-forward svc/\${svc} \${port}:80 -n production" -t k8s -s pf
koda x pf -V svc=api,port=8080
```

**6. Tail error logs from any deployment**

Save a kubectl log pipeline and run it against any service.

```bash
koda a "kubectl logs deploy/\${svc} --tail=200 | grep ERROR" -t k8s -s k8s-errors
koda x k8s-errors -V svc=api
```

---

### Cloud

**7. Sync a build artifact to any S3 bucket**

Save an `aws s3 sync` command and swap the bucket name at call time.

```bash
koda a "aws s3 sync ./dist s3://\${bucket}/app/ --delete --profile prod" -t aws -s s3-sync
koda x s3-sync -V bucket=my-staging-frontend
```

**8. Start an SSM session on any EC2 instance**

Save the SSM command with the instance ID as a positional placeholder.

```bash
koda a "aws ssm start-session --target \$1 --region ap-northeast-1" -t aws -s ssm
koda x ssm -V i-0abc1234567def890
```

**9. Save a generated instance ID and reuse it**

Pipe the ID from `aws ec2 run-instances` into koda, then pass it to follow-up commands.

```bash
aws ec2 run-instances ... | jq -r '.Instances[0].InstanceId' | koda a -t aws -s new-instance
koda x ssm -V $(koda r new-instance)
```

---

### System ops

**10. Capture a container's dynamic port and reuse it immediately**

A container started with `-P` gets a random host port. Capture it once and embed it across follow-up commands.

```bash
# Start a container and save the randomly assigned host port
docker run -d -P --name web nginx
docker port web 80/tcp | cut -d: -f2 | koda a -t docker -s web-port

# Hit the container from any command — no repeated docker port query
curl http://localhost:$(koda r web-port)/
open http://localhost:$(koda r web-port)/admin
```

**11. SSH into ephemeral instances with a flag-heavy command**

Ephemeral instances (spot workers, CI runners) don't belong in `.ssh/config`. Save the full command with all flags and substitute the IP at call time.

```bash
koda a "ssh -i ~/.ssh/prod.pem -o StrictHostKeyChecking=no ec2-user@\$1" -t ssh -s ec2
koda x ec2 -V 10.0.1.42
koda x ec2 -V 10.0.1.55
```

**12. Sync a local directory to a remote server**

Save an rsync command with a named host placeholder.

```bash
koda a "rsync -avz --progress ./dist deploy@\${host}:/var/www/html/" -t deploy -s rsync-deploy
koda x rsync-deploy -V host=prod.example.com
```

**13. Create a dated backup on every run**

Escape `\$(date)` so it expands at exec time, not at save time.

```bash
koda a "tar czf ~/backups/src-\$(date +%Y%m%d-%H%M).tar.gz ./src" -t backup -s backup
koda x backup   # creates src-20260505-1430.tar.gz each time
```

**14. Pick a saved host with fzf and substitute it into a command**

Register IP addresses with a `host` tag. Use `koda p -r -t host` to pick one interactively and pass it as a variable into any command template. Or save the full command per host and use `koda p -x` to pick and run in one step.

```bash
# Register IP addresses once
koda a "10.0.1.10" -t host -s web-1
koda a "10.0.1.11" -t host -s web-2
koda a "10.0.1.20" -t host -s db-1
koda a "10.0.1.30" -t host -s bastion
```

**Pattern A — template + pick**: save a command once with `$1`, pick the IP at run time.

```bash
# Save a long command template once
koda a "ssh -i ~/.ssh/prod.pem ec2-user@\$1 'sudo journalctl -u app -n 100 -f'" -t ssh -s taillog

# Open fzf filtered to the host tag, pick a host, run the command
koda x taillog -V $(koda p -r -t host)
```

`koda p -r -t host` opens fzf showing only `host` entries; selecting one prints the IP, which `-V` passes as `$1`.

**Pattern B — one entry per host, pick and exec**: save the full command for each host, then use `koda p -x` to pick and execute in one step.

```bash
# Save the full command for each host
koda a "ssh -i ~/.ssh/prod.pem ec2-user@10.0.1.10 'sudo journalctl -u app -n 100 -f'" -t ssh -s web-1-log
koda a "ssh -i ~/.ssh/prod.pem ec2-user@10.0.1.11 'sudo journalctl -u app -n 100 -f'" -t ssh -s web-2-log
koda a "ssh -i ~/.ssh/prod.pem ec2-user@10.0.1.30 'sudo journalctl -u app -n 100 -f'" -t ssh -s bastion-log

# Pick from the ssh entries and execute immediately
koda p -x -t ssh
```

`koda p -x -t ssh` opens fzf pre-filtered to the `ssh` tag; pressing Enter executes the selected entry directly.

---

### Development

**15. Open a dashboard for any environment**

Save dashboard URLs for each environment under the same tag, then pick one with fzf.

```bash
koda a "https://grafana.internal/d/prod/main"    -t url,prod    -s grafana-prod
koda a "https://grafana.internal/d/staging/main" -t url,staging -s grafana-staging
koda a "https://grafana.internal/d/dev/main"     -t url,dev     -s grafana-dev

# Open directly by shortcut
xdg-open $(koda r grafana-prod)

# Or pick from all url entries interactively
xdg-open $(koda p -r -t url)
```

**16. Connect to a database in any environment**

Save connection strings for each environment and pick one with fzf.

```bash
koda a "psql postgres://admin@db.prod.internal:5432/myapp"    -t db,prod    -s db-prod
koda a "psql postgres://admin@db.staging.internal:5432/myapp" -t db,staging -s db-staging
koda a "psql postgres://admin@db.dev.internal:5432/myapp"     -t db,dev     -s db-dev

# Connect to a specific environment
koda x db-prod

# Or pick interactively
koda p -x -t db
```

**17. Convert video with a saved ffmpeg preset**

Save an ffmpeg encode command with source and output as positional placeholders.

```bash
koda a "ffmpeg -i \$1 -vcodec libx264 -crf 23 \$2" -t media -s h264
koda x h264 -V input.mov,output.mp4
```

**18. Query a local LLM from the terminal**

Save a curl-based request template via heredoc; supply the prompt at call time.

```bash
koda a -t llm -s gen <<'EOF'
curl -sS http://localhost:11434/api/generate \
  -d '{"model":"llama3","prompt":"$1","stream":false}' | jq -r .response
EOF
koda x gen -V "Explain HTTP/2 server push"
```

**19. Append a saved snippet to a project file**

Store a reusable multi-line fragment and stream it directly into a file with `koda r`.

```bash
koda a -t infra -s pybase <<'EOF'
FROM python:3.12-slim
RUN pip install uv
WORKDIR /app
EOF
koda r pybase >> Dockerfile
```

---

### Cross-machine

**20. Share your public SSH key across machines**

Save the key on machine A, push it, then pull and retrieve it on any other machine.

```bash
# Machine A
koda a "$(cat ~/.ssh/id_ed25519.pub)" -t ssh -s pubkey
koda push

# Machine B
koda pull
koda r pubkey   # paste into authorized_keys or GitHub
```

**21. Keep reusable commands in sync across machines**

Build a library of snippets on one machine and make them available everywhere via Git sync.

```bash
# Machine A — build the library
koda a "kubectl rollout restart deployment/\${svc} -n production" -t k8s -s k8s-restart
koda a "aws ssm start-session --target \$1 --region ap-northeast-1" -t aws -s ssm
koda push

# Machine B — pull and run immediately
koda pull
koda x k8s-restart -V svc=worker
```

---

### Local LLM

**22. Query a llama.cpp server from the terminal**

llama.cpp exposes an OpenAI-compatible API at `http://localhost:8080`. Save the curl invocation once and supply the prompt at call time.

```bash
koda a -t llm -s llm <<'EOF'
curl -sS http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d "{\"model\": \"llama3\", \"messages\": [{\"role\": \"user\", \"content\": \"$1\"}], \"stream\": false}" \
  | jq -r '.choices[0].message.content'
EOF

koda x llm -V "What is the time complexity of quicksort?"
koda x llm -V "Summarize the last git commit"
```

---

## License

MIT (c) 2026 ngt22
