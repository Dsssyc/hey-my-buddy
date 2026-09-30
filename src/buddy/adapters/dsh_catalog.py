"""Read model metadata from the installed DSH provider without a model call."""
from __future__ import annotations

import json
import os
import shutil
import subprocess

from ..errors import BoardError
from ..runtime import resource_path


def discover_models() -> dict:
    from .dsh import node_binary
    from ..harness_runtime import selected
    from ..harness_discovery import native_environment
    script = resource_path("dsh.catalog")
    node = node_binary()
    if node is None or not script.is_file():
        raise BoardError("CATALOG_UNAVAILABLE", "DSH catalog helper or Node.js is unavailable")
    try:
        record = selected('dsh') or {}
        environment = native_environment(os.environ, command=record.get('command', []))
        if record.get('executable'):
            environment['DSH_BIN'] = record['executable']
        if os.environ.get('DSH_HOME'):
            environment['DSH_HOME'] = os.environ['DSH_HOME']
        completed = subprocess.run(
            [node, str(script)], capture_output=True, text=True, timeout=60, check=False, env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BoardError("CATALOG_UNAVAILABLE", f"DSH catalog discovery failed: {type(error).__name__}") from error
    if completed.returncode != 0:
        # Never expose raw provider diagnostics across the public boundary.
        raise BoardError("CATALOG_UNAVAILABLE", f"DSH catalog helper exited with code {completed.returncode}")
    try:
        return json.loads(completed.stdout)
    except ValueError as error:
        raise BoardError("CATALOG_INVALID", "DSH catalog helper returned invalid JSON") from error
