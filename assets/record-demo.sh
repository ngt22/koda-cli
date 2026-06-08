#!/usr/bin/env bash
# Regenerate the README demo GIF.
#
# Requirements: agg and `koda` on PATH (e.g. an active project venv or a global
# install). Produces assets/demo.cast and assets/demo.gif.
#
#   ./assets/record-demo.sh
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
cast="$here/demo.cast"
gif="$here/demo.gif"

python3 "$here/make-demo.py" "$cast"

agg --theme dracula --font-size 26 --speed 1.0 --idle-time-limit 2 \
  "$cast" "$gif"

echo "Wrote $gif"
