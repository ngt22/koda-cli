# Koda

A **memo and snippet CLI** for the terminal. Store frequently used commands, config fragments, and notes in SQLite, then pull them back instantly with `list`, `show`, and search. Built with Python, Typer, and Rich.

## Features

- **Fast save and recall**: Manage entries with `add`, `list`, `show`, `edit`, and `rm`.
- **Flexible input**: Arguments, heredocs, pipes, or `$EDITOR`.
- **Tags**: Classify and filter with multiple tags (`list -t`).
- **Shell-friendly**: `raw` prints body-only text for `eval`, aliases, and scripts.
- **XDG-friendly**: Default data under `~/.local/share/koda/`.
- **`KODA_DB_PATH`**: Override the database file location.

## Installation

```bash
git clone https://github.com/ngt22/koda.git
cd koda
uv tool install .
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
koda list
# Suppose the new entry is ID 12 — print body only and execute:
eval "$(koda raw 12)"
```

Put **only the command you intend to run** in the body. The editor template keeps metadata in a separate `---` block. Because `eval` executes shell code, **store only trusted text**.

### 2. Config templates and Dockerfile fragments

Tag infra snippets and paste the full body with `show` when needed.

```bash
koda add -t docker,template <<'EOF'
FROM python:3.12-slim
RUN pip install uv
WORKDIR /app
EOF
koda show 1
```

### 3. Reuse API `curl` commands or SQL

Save authenticated `curl` one-liners or common `SELECT` statements; search with `list -q`.

```bash
koda add -t api,curl 'curl -sS -H "Authorization: Bearer $TOKEN" https://api.example.com/v1/status'
koda list -q "curl"
```

### 4. Cheat sheets and plain notes (not only commands)

Git recipes, install commands, meeting notes — anything that fits in plain text.

```bash
koda add "git reset --soft HEAD~1   # undo last commit, keep changes" -t git
koda list -t git
```

### 5. Pipe body into scripts or other commands

`raw` writes plain stdout (no Rich), so `$(koda raw <ID>)` can feed variables or arguments. **Keep the body simple** when you automate: extra newlines or prose can break word splitting or `eval`.

```bash
# Latest entry body only (bare `koda` / `koda raw` with no ID = most recent)
CONTENT="$(koda raw)"
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
echo "stdin works too" | koda add
koda add   # opens $EDITOR
```

### List and search

```bash
koda list              # last 20 entries
koda list -q "docker"
koda list -t "linux"
```

### Show, edit, remove

```bash
koda show 1
koda edit 1
koda copy 1            # duplicate to a new ID
koda rm 1
```

### Body-only stdout (scripts and shells)

```bash
koda raw        # latest entry body only
koda raw 5      # entry 5 body only
koda 5          # numeric-only args are treated like `raw`
```

## Environment variables

| Variable       | Purpose |
|----------------|---------|
| `KODA_DB_PATH` | Path to the SQLite database file |
| `EDITOR`       | Editor for `add` and `edit` (default: `vim`) |

## License

MIT (c) 2026 ngt22
