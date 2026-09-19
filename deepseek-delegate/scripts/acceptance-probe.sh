#!/bin/sh
# Run the verified, resumable real-dsh acceptance against explicitly private paths.
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec "${UV_BIN:-uv}" run --no-project --python 3.12 \
  "$PROJECT_DIR/scripts/acceptance_probe.py" \
  --launcher "$PROJECT_DIR/scripts/launch-buddy.sh" "$@"
