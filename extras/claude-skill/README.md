# koda-cli — Claude Code skill for koda

Let an AI agent (Claude Code, or any tool that loads `SKILL.md` skills) save,
search, recall, and run prompts/snippets through the [koda](https://github.com/ngt22/koda-cli)
CLI. The agent uses the same local store you use from the terminal, so prompts
and results live alongside your everyday snippets.

## Requirements

- [koda](https://github.com/ngt22/koda-cli) installed and available in `$PATH`
  (the agent shells out to `koda`).
- A skill-aware client. In Claude Code, skills are loaded from
  `~/.claude/skills/` (user-wide) or `.claude/skills/` (per project).

## Installation

The `extras/` directory is **not** shipped in the published package — install
the skill from a clone of this repository. First make sure `koda` itself is
installed and on `$PATH` (e.g. `uv tool install .`), since the skill shells out
to it.

Run the commands below **from the repository root**.

```bash
git clone https://github.com/ngt22/koda-cli.git
cd koda
```

**Option A — copy** (a fixed snapshot):

```bash
mkdir -p ~/.claude/skills/koda-cli
cp extras/claude-skill/SKILL.md ~/.claude/skills/koda-cli/SKILL.md
```

**Option B — symlink** (tracks this clone, so `git pull` updates the skill):

```bash
mkdir -p ~/.claude/skills/koda-cli
ln -s "$(pwd)/extras/claude-skill/SKILL.md" ~/.claude/skills/koda-cli/SKILL.md
```

Symlink the `SKILL.md` file, not the `extras/claude-skill` directory: linking
the directory into an existing `~/.claude/skills/koda-cli/` would nest it as
`koda-cli/claude-skill/SKILL.md`, where Claude Code won't find it.

For a single project instead of user-wide, use that project's
`.claude/skills/koda-cli/` directory as the destination in either option.

Claude Code discovers the skill on its next run — try asking the agent to
"save this prompt to koda" to confirm it loaded.

## What the agent can do

| Intent | koda command the skill runs |
|---|---|
| Save a prompt / command / result | `koda add "<content>" -t <tag> --remote --print-idx --quiet` |
| Browse or search saved entries | `koda list --json [-q <substr>] [-t <tag>]` |
| Recall a body, filling variables | `koda raw <ref> -V KEY=value` |
| Run a saved command | hands you `koda x <ref>` to run yourself |

## Safety model

Entries the agent saves are marked `source=remote` (unreviewed) via `--remote`.
koda refuses to `exec` a `remote` entry without confirmation (and refuses
outright in a non-interactive shell), so an injected or mistaken command cannot
run silently. You review and trust an entry with `koda edit <ref>`, which clears
the flag. The skill never executes saved command bodies itself — it only reads
text and hands runnable commands back to you.
