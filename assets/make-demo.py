#!/usr/bin/env python3
"""Build a deterministic asciinema cast for the README demo.

Each real `koda` command is executed under a PTY (so Rich keeps its colors),
its output captured, and an asciinema v2 cast is assembled with synthetic but
natural-looking typing/pause timing. This makes the demo fully reproducible in
any environment, including headless CI where live recording loses keystroke
timing.

Run via assets/record-demo.sh (it then renders the GIF with agg).
"""

from __future__ import annotations

import json
import os
import pty
import select
import sys
from pathlib import Path

COLS, ROWS = 92, 22
PROMPT = "\x1b[1;36m$\x1b[0m "
TYPE_DELAY = 0.05  # per character
PRE_RUN_PAUSE = 0.35  # after typing, before Enter
POST_RUN_PAUSE = 1.6  # after output, before next command
END_PAUSE = 1.8

# The demo story: save two entries, list them, run one by shortcut with a
# variable, then inspect it. Commands are the real CLI; output is captured live.
COMMANDS = [
    "koda add 'git log --oneline --graph --decorate -8' -s glog -t git",
    "koda add 'echo deploying to $1...' -s deploy -t ops",
    "koda list",
    "koda x deploy -V prod",
    "koda show glog",
]


def run_pty(cmd: str, env: dict[str, str]) -> str:
    """Run `cmd` under a PTY and return its combined output as text."""
    chunks: list[bytes] = []
    pid, fd = pty.fork()
    if pid == 0:  # child
        os.environ.update(env)
        os.environ["COLUMNS"] = str(COLS)
        os.environ["LINES"] = str(ROWS)
        os.execvp("bash", ["bash", "-c", cmd])
    while True:
        try:
            r, _, _ = select.select([fd], [], [], 5)
        except OSError:
            break
        if not r:
            break
        try:
            data = os.read(fd, 4096)
        except OSError:
            break
        if not data:
            break
        chunks.append(data)
    os.waitpid(pid, 0)
    os.close(fd)
    return b"".join(chunks).decode("utf-8", "replace")


def main() -> int:
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("assets/demo.cast")

    env = dict(os.environ)
    db = Path.home() / ".local" / "share" / "koda" / "demo.db"
    env["KODA_DB_PATH"] = str(db)
    db.unlink(missing_ok=True)

    events: list[list] = []
    t = 0.0

    def emit(data: str) -> None:
        events.append([round(t, 3), "o", data])

    for cmd in COMMANDS:
        emit(PROMPT)
        for ch in cmd:
            t += TYPE_DELAY
            emit(ch)
        t += PRE_RUN_PAUSE
        emit("\r\n")
        # Normalize bare LF to CRLF so the terminal renderer aligns columns.
        output = run_pty(cmd, env).replace("\r\n", "\n").replace("\n", "\r\n")
        emit(output)
        t += POST_RUN_PAUSE
    emit(PROMPT)
    t += END_PAUSE
    emit("")

    header = {"version": 2, "width": COLS, "height": ROWS, "title": "koda-cli"}
    with out_path.open("w") as f:
        f.write(json.dumps(header) + "\n")
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    print(f"Wrote {out_path} ({len(events)} events, {events[-1][0]:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
