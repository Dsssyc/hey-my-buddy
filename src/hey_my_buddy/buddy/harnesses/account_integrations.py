"""Native account paths admitted by the separately approved macOS checks.

No native process or credential-store query runs during import or capability
projection. Fixture injection remains separate from this production evidence.
"""
from __future__ import annotations
import json
from pathlib import Path
import sys

from ...errors import BoardError

VERIFIED_VERSIONS = {'codex': ('0.159.0',), 'claude': ('2.1.284',)}


def _environment(state, account, environment, purpose):
    from ...private_dirs import account_root, linked_component
    adapter = account['adapter']
    home = account_root(state, adapter)
    if linked_component(home) is not None:
        raise BoardError('PRIVATE_PATH_UNSAFE', 'The Worker account path contains a link')
    result = dict(environment)
    result['BUDDY_STATE_DIR'] = str(state)
    result['BUDDY_ACCOUNT_SELECTION'] = json.dumps(account, separators=(',', ':'))
    result['CODEX_HOME' if adapter == 'codex' else 'CLAUDE_CONFIG_DIR'] = str(home)
    return result


def providers():
    if sys.platform != 'darwin':
        return {}
    from ...blackboard.catalog.accounts import WorkerAccountProvider
    return {adapter: WorkerAccountProvider(environment=_environment, versions=versions,
        capabilities={'workerAccount': True, 'oauth': adapter == 'codex', 'apiKey': True,
                      'logout': True, 'remove': True}) for adapter, versions in VERIFIED_VERSIONS.items()}


def inject_claude_key(result, source):
    """Called only while constructing the actual native Claude child environment."""
    selected = source.get('BUDDY_ACCOUNT_SELECTION')
    if not selected:
        return result
    try:
        account = json.loads(selected)
        if (account.get('adapter') != 'claude' or account.get('source') != 'worker'
                or type(account.get('credentialRevision')) is not int):
            raise ValueError('invalid selection')
        state = Path(source['BUDDY_STATE_DIR'])
        from ...private_dirs import account_root, linked_component
        home = account_root(state, 'claude')
        if source.get('CLAUDE_CONFIG_DIR') != str(home) or linked_component(home) is not None:
            raise ValueError('wrong private home')
        from ...blackboard.catalog.account_keystore import identity, open_store
        secret = open_store().get(*identity(state, 'claude'))
        if secret is None:
            raise BoardError('ACCOUNT_LOGIN_REQUIRED', 'The Worker Claude account has no system credential')
        return {**result, 'ANTHROPIC_API_KEY': secret}
    except BoardError:
        raise
    except Exception:
        raise BoardError('ACCOUNT_BINDING_UNAVAILABLE', 'The private Claude account binding is unavailable') from None
