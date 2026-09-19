#!/bin/sh
# Portable CLI launcher for the uv-managed Buddy service.
#
# This is the only entrypoint the plugin exposes: it resolves the project from its
# own location (never from the caller's cwd), makes uv reachable under a minimal
# PATH, and forwards every argument verbatim to the `buddy` CLI:
#
#   sh scripts/launch-buddy.sh health
#   sh scripts/launch-buddy.sh run '{"requestId":"...","task":"...","cwd":"/abs/path"}'
#
# There is no MCP server to launch; `scripts/buddy.mjs` is the equivalent Node
# launcher and targets exactly the same CLI.
set -eu
BUDDY_PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PATH="${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:${PATH:-/usr/bin:/bin}"
export PATH
if [ -n "${PLUGIN_DATA:-}" ] && [ -z "${UV_PROJECT_ENVIRONMENT:-}" ]; then
  BUDDY_ENV_KEY=$(printf '%s' "$BUDDY_PROJECT_DIR" | cksum | cut -d ' ' -f 1)
  UV_PROJECT_ENVIRONMENT="$PLUGIN_DATA/venv-$BUDDY_ENV_KEY"
  export UV_PROJECT_ENVIRONMENT
fi
exec "${UV_BIN:-uv}" run --frozen --python 3.12 --project "$BUDDY_PROJECT_DIR" buddy "$@"
