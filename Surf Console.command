#!/bin/bash
cd -- "$(dirname -- "$0")" || exit 1
SURF_PY="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
if [ ! -x "$SURF_PY" ]; then SURF_PY=python3; fi
exec "$SURF_PY" console.py "$@"
