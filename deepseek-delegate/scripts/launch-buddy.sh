#!/bin/sh
set -eu
BUDDY_PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PATH="${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:${PATH:-/usr/bin:/bin}"
export PATH
if [ -n "${PLUGIN_DATA:-}" ] && [ -z "${UV_PROJECT_ENVIRONMENT:-}" ]; then
  BUDDY_ENV_KEY=$(printf '%s' "$BUDDY_PROJECT_DIR" | cksum | cut -d ' ' -f 1)
  UV_PROJECT_ENVIRONMENT="$PLUGIN_DATA/venv-$BUDDY_ENV_KEY"
  export UV_PROJECT_ENVIRONMENT
fi
exec "${UV_BIN:-uv}" run --frozen --python 3.12 --project "$BUDDY_PROJECT_DIR" buddy-mcp
