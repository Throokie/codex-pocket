#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
fi
# Data is intentionally outside the shipped source when POCKET_DATA is set.
exec .venv/bin/python -m server --data "${POCKET_DATA:-.local}" "$@"
