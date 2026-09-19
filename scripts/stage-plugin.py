#!/usr/bin/env python3
"""Stage only distributable files; keep uv environments and probe logs out of plugins."""
import argparse
import json
import time
from pathlib import Path
import shutil
import tempfile

# CLI-only plugin: an MCP companion is not part of this design. These paths are the
# removed MCP server, its launcher facades and the native App notification client.
FORBIDDEN_MCP_PATHS = [
    'mcp.json',
    'deepseek-delegate/python/buddy/mcp_server.py',
    'deepseek-delegate/python/buddy/native.py',
    'deepseek-delegate/python/buddy/receiver.py',
    'deepseek-delegate/service/mcp.mjs',
    'deepseek-delegate/service/native-client.mjs',
]


def mcp_companion_conflicts(source):
    """Return the removed-MCP paths present in a tree that is about to be staged."""
    return [rel for rel in FORBIDDEN_MCP_PATHS if (source / rel).exists()]


def assert_no_mcp_companion(source):
    conflicts = mcp_companion_conflicts(source)
    if conflicts:
        raise SystemExit('This plugin is CLI-only: remove ' + ', '.join(conflicts) + ' before staging')


def stage(source, destination):
    assert_no_mcp_companion(source)
    identity = json.loads((source / '.codex-plugin/plugin.json').read_text())
    portable = json.loads((source / 'plugin.json').read_text())
    portable.update({key: identity[key] for key in ['name', 'version', 'description', 'author', 'license']})
    (source / 'plugin.json').write_text(json.dumps(portable, indent=2) + '\n')
    dest = destination.expanduser().absolute()
    if dest.name != 'hey-my-buddy' or dest == source:
        raise SystemExit('Destination must be a separate hey-my-buddy plugin directory')
    dest.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix='.buddy-stage-', dir=dest.parent))
    try:
        for rel in ['.codex-plugin', 'plugin.json', 'skills', 'LICENSE', 'README.md', 'README.zh-CN.md',
                    'deepseek-delegate/LICENSE', 'deepseek-delegate/pyproject.toml', 'deepseek-delegate/uv.lock',
                    'deepseek-delegate/python/buddy', 'deepseek-delegate/scripts', 'deepseek-delegate/service',
                    'deepseek-delegate/plugins', 'deepseek-delegate/references']:
            src = source / rel
            target = staged / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, target, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            else:
                shutil.copy2(src, target)
        if dest.is_symlink():
            if dest.resolve() != source:
                raise SystemExit('Refusing to replace an unrelated source link')
            dest.unlink()
        elif dest.exists():
            backup = dest.with_name(dest.name + '.previous-' + str(time.time_ns()))
            dest.rename(backup)
        staged.rename(dest)
        print(dest)
    finally:
        if staged.exists():
            shutil.rmtree(staged)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    stage(Path(__file__).resolve().parents[1], args.destination)


if __name__ == '__main__':
    main()
