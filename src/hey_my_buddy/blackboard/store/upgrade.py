"""The one pending, offline upgrade from the 0.42.0 board to schema 25."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import json
import sqlite3

from .db import COMMAND_DELETION_COLUMN, MACRO_TASK_DELETIONS_DDL, EVALUATION_TABLES, canonical_json
from ...errors import BoardError
from ...protocol.reasons import reason as make_reason


UPGRADE_BASE_SCHEMA = 24

_CATALOG_FACTS = {
    'not present in the latest confirmed native discovery': 'catalog.model-absent',
    'not present in the latest complete native discovery': 'catalog.effort-retired',
}
_NATIVE_DISCOVERY_CODES = {
    'config-options-shape-unreadable': 'config-options-shape-unreadable',
    'config-shape-unreadable': 'config-shape-unreadable',
    'model-list-shape-unreadable': 'model-list-shape-unreadable',
    'model-cursor-shape-unreadable': 'model-cursor-shape-unreadable',
}
_NATIVE_SOURCES = {
    'dsh': 'dsh-acp-session-config',
    'codex': 'codex-native-app-server',
    'zcode': 'zcode-native-app-server',
    'claude': 'claude-code-initialize',
}
_DISCOVERY_ERRORS = frozenset((
    'BUDDY_UNAVAILABLE', 'CATALOG_INVALID', 'CATALOG_LIMIT', 'CATALOG_UNAVAILABLE',
    'CONFLICT', 'HARNESS_DISCOVERY_STOP_UNCONFIRMED', 'HARNESS_RECORD_INVALID',
    'HARNESS_UNAVAILABLE', 'INVALID_ARGUMENT', 'LIVE_ENDPOINT_START_FAILED',
    'LIVE_UNAVAILABLE', 'NOT_FOUND', 'PRIVATE_PATH_UNSAFE', 'ROLE_RUN_UNREGISTERED',
    'UNAUTHORIZED', 'UNSUPPORTED', 'UNSUPPORTED_ADAPTER', 'UNSUPPORTED_CAPABILITY',
    'UNSUPPORTED_HARNESS', 'UPGRADE_IN_PROGRESS',
))
_PROBE_CODES = {
    'HARNESS_NOT_CHECKED': 'harness.not-checked',
    'HARNESS_START_FAILED': 'harness.launch-failed',
    'HARNESS_HANDSHAKE_FAILED': 'harness.probe-failed',
    'HARNESS_UNHEALTHY': 'harness.probe-failed',
    'HARNESS_ACCOUNT_READ_FAILED': 'harness.account-read-failed',
    'HARNESS_ACCOUNT_STOP_UNCONFIRMED': 'harness.shutdown-unverified',
    'not-found': 'harness.not-found',
    'node-missing': 'harness.node-missing',
    'login-required': 'harness.login-required',
    'interpreter-missing': 'harness.interpreter-missing',
    'timeout': 'harness.timeout',
    'output-limit': 'harness.output-limit',
    'auth-unverified': 'harness.auth-unverified',
    'version-unknown': 'harness.version-unknown',
    'scan-timeout': 'harness.scan-timeout',
    'shutdown-unverified': 'harness.shutdown-unverified',
    'pipe-failed': 'harness.pipe-failed',
    'probe-failed': 'harness.probe-failed',
    'launch-failed': 'harness.launch-failed',
}
_READONLY_CODES = {
    'readonly-not-implemented': 'harness.readonly-not-implemented',
    'readonly-platform-unsupported': 'harness.readonly-platform-unsupported',
    'readonly-tools-unrestricted': 'harness.readonly-tools-unrestricted',
    'readonly-resource-missing': 'harness.readonly-resource-missing',
}
_WARNING_CODES = {
    'HARNESS_RATE_LIMIT_REPORTED': 'HARNESS_RATE_LIMIT_REPORTED',
    'HARNESS_QUOTA_LIMIT_REPORTED': 'HARNESS_QUOTA_LIMIT_REPORTED',
    'HARNESS_QUOTA_NEAR_LIMIT': 'HARNESS_QUOTA_NEAR_LIMIT',
}
_ACCOUNT_CODES = {'ACCOUNT_LOGIN_REQUIRED': 'account.login-required',
                  'ACCOUNT_NOT_CHECKED': 'account.not-checked'}
_FIXED_CATALOG_WARNINGS = {
    'A model without a resolved identity was omitted': 'catalog.model-identity-omitted',
    'A model with an invalid effort directory was omitted': 'catalog.effort-directory-omitted',
    'A model with no usable identity or reasoning effort was omitted': 'catalog.model-identity-or-effort-omitted',
    'Models exposing no configurable native reasoning efforts were omitted': 'catalog.effort-directory-omitted',
    'Claude initialize protocol observations are incomplete': 'catalog.initialize-incomplete',
}
_FIXED_WARNING_OWNERS = {
    'A model without a resolved identity was omitted': 'claude',
    'A model with an invalid effort directory was omitted': 'claude',
    'A model with no usable identity or reasoning effort was omitted': 'codex',
    'Models exposing no configurable native reasoning efforts were omitted': 'zcode',
    'Claude initialize protocol observations are incomplete': 'claude',
}
_REDUNDANT_CATALOG_WARNINGS = {
    'The no-prompt ACP surface exposes the declared model and effort selectors only; '
    'per-model effort availability and context windows stay unknown until a real session.': 'dsh',
    'The account status is not applicable to this reading: the discovery launches the '
    "installed DSH with the user's inherited HOME and DSH_HOME, so DSH loads its own "
    'configuration and login without this module reading or copying credential content, and '
    'the no-prompt session carries no separate account read.': 'dsh',
    'The native app server returned no models with configurable reasoning efforts; this complete empty observation retires missing ZCode buddies': 'zcode',
    'The account status is not applicable to this reading: the no-prompt native model snapshot carries no separate account check': 'zcode',
    'The discovery session could not confirm the Codex account; whether to trust this reading is the board\'s judgment': 'codex',
    'The native config/read configuration is unreadable; no provider or models can be attributed': 'codex',
}


def _harness_reason(code: str | None, *, harness: str | None = None) -> dict | None:
    if code is None:
        return None
    if code == 'readonly-worker-carrier-unimplemented':
        return make_reason('harness.readonly-worker-carrier-unimplemented', harness=harness)
    if code in _READONLY_CODES:
        return make_reason(_READONLY_CODES[code])
    operation = 'account-read' if code in ('HARNESS_ACCOUNT_READ_FAILED', 'HARNESS_ACCOUNT_STOP_UNCONFIRMED') else 'probe'
    if code not in _PROBE_CODES:
        raise BoardError('UPGRADE_INVALID_BOARD', 'Unknown stored harness reason code')
    return make_reason(_PROBE_CODES[code], operation=operation)


def _account_reason(code: str | None, *, harness: str) -> dict | None:
    if code is None:
        return None
    if code not in _ACCOUNT_CODES:
        return make_reason('history.reason-unavailable', recordId=harness)
    return make_reason(_ACCOUNT_CODES[code])


def _health_record(value: dict, *, owning_harness: str | None = None) -> dict:
    """Change known health-owned reason sites, leaving native diagnostics intact."""
    harness = value.get('harness') or owning_harness
    account_failure = value.get('accountReadFailure')
    last_probe = value.get('lastProbe')
    for item in (value, *(value.get('candidates') or []), last_probe,
                 *((last_probe.get('candidates') or []) if isinstance(last_probe, dict) else []),
                 account_failure, value.get('readOnlyStructured')):
        if not isinstance(item, dict):
            continue
        if 'reasonCode' in item:
            code = item.pop('reasonCode')
            account_context = (item is account_failure or
                               (item is value and isinstance(account_failure, dict)
                                and account_failure.get('reasonCode') == code))
            if account_context and code not in ('HARNESS_ACCOUNT_STOP_UNCONFIRMED', None):
                if code != 'HARNESS_ACCOUNT_READ_FAILED':
                    account_failure['diagnosticCode'] = code
                item['reason'] = make_reason('harness.account-read-failed', operation='account-read')
            elif code is not None and code not in _PROBE_CODES and code not in _READONLY_CODES \
                    and code != 'readonly-worker-carrier-unimplemented':
                if harness is None:
                    raise BoardError('UPGRADE_INVALID_BOARD', 'Stored health reason has unknown owner')
                item['reason'] = make_reason('history.reason-unavailable', recordId=harness)
            else:
                item['reason'] = _harness_reason(code, harness=harness)
        elif isinstance(item.get('reason'), str):
            # These records are written by the board or a registered harness;
            # an older unrecognized phrase has a known owner but no safe code.
            if harness is None:
                raise BoardError('UPGRADE_INVALID_BOARD', 'Stored health reason has unknown owner')
            item['reason'] = make_reason('history.reason-unavailable', recordId=harness)
        item.pop('remedy', None)
    account = value.get('account')
    if isinstance(account, dict):
        if 'reasonCode' in account:
            if harness is None:
                raise BoardError('UPGRADE_INVALID_BOARD', 'Stored account reason has unknown owner')
            account['reason'] = _account_reason(account.pop('reasonCode'), harness=harness)
        elif isinstance(account.get('reason'), str):
            account['reason'] = make_reason('history.reason-unavailable', recordId=harness)
        account.pop('guidance', None)
    return value


def _catalog_reason(value: str | None, harness: str, *, program: bool,
                    native_program: bool = False) -> tuple[dict | None, str | None]:
    if value is None:
        return None, None
    if value == 'ACCOUNT_STATUS_UNKNOWN' and program:
        return make_reason('catalog.account-status-unknown', harness=harness), None
    if native_program and value in _NATIVE_DISCOVERY_CODES:
        return make_reason(_NATIVE_DISCOVERY_CODES[value]), None
    if program and value in _PROBE_CODES:
        return _harness_reason(value), None
    if program and value in _DISCOVERY_ERRORS:
        return make_reason('catalog.discovery-failed', harness=harness, errorCode=value), None
    if program:
        return make_reason('history.reason-unavailable', recordId=harness), None
    return make_reason('catalog.native-unknown', harness=harness), value


def _catalog_payload(value: dict) -> dict:
    old_discoveries = {item['harness']: item.get('reason') for item in value.get('discoveries', [])}
    provider_harnesses = {item['harness'] for item in value.get('providers', [])}
    old_warnings = value.get('warnings', [])
    program_harnesses = {harness for harness, old in old_discoveries.items()
                         if harness not in provider_harnesses and old is not None
                         and f'{harness}: {old}' in old_warnings}
    source_parts = {part.strip() for part in value.get('source', '').split(';')}
    native_harnesses = {name for name, source in _NATIVE_SOURCES.items() if source in source_parts}
    for provider in value.get('providers', []):
        for model in provider.get('models', []):
            old = model.pop('unavailableReason', None)
            if old is None or model.get('available', True):
                model['unavailableReason'] = None
                model['unavailableContent'] = None
            else:
                if old == 'native route is unavailable':
                    raise BoardError('UPGRADE_INVALID_BOARD', 'Native unavailable text has ambiguous ownership')
                model['unavailableReason'] = make_reason('catalog.native-unavailable')
                model['unavailableContent'] = old
    for discovery in value.get('discoveries', []):
        old = discovery.get('reason')
        discovery['reason'], discovery['content'] = _catalog_reason(
            old, discovery['harness'], program=discovery['harness'] in program_harnesses,
            native_program=_NATIVE_SOURCES.get(discovery['harness']) in source_parts)
        if (discovery['harness'] == 'zcode' and 'zcode' in native_harnesses
                and discovery['status'] == 'unknown' and old is None
                and 'The native model catalog shape was not fully recognized; this reading cannot retire missing buddies'
                in old_warnings):
            discovery['reason'] = make_reason('catalog.model-shape-unreadable')
    warnings = []
    for warning in value.get('warnings', []):
        if isinstance(warning, str):
            if value.get('source', '').startswith(('file:', 'fixture:')):
                warnings.append({'reason': make_reason('catalog.native-warning'), 'content': warning})
                continue
            harness, separator, detail = warning.partition(': ')
            if separator and harness in program_harnesses and detail == old_discoveries[harness]:
                reason, content = _catalog_reason(detail, harness, program=True)
                warnings.append({'harness': harness, 'reason': reason,
                                 **({'content': content} if content is not None else {})})
            elif warning in _REDUNDANT_CATALOG_WARNINGS and _REDUNDANT_CATALOG_WARNINGS[warning] in native_harnesses:
                continue
            elif warning in _FIXED_CATALOG_WARNINGS and _FIXED_WARNING_OWNERS[warning] in native_harnesses:
                warnings.append({'reason': make_reason(_FIXED_CATALOG_WARNINGS[warning])})
            elif 'claude' in native_harnesses and warning.startswith('Claude model list was unread (shape: ') and warning.endswith(')'):
                warnings.append({'reason': make_reason('catalog.model-list-unreadable',
                                                       shape=warning[len('Claude model list was unread (shape: '):-1])})
            elif 'claude' in native_harnesses and warning.startswith('Claude unread initialization: '):
                warnings.append({'reason': make_reason('catalog.initialize-incomplete'),
                                 'diagnostic': warning[len('Claude unread initialization: '):]})
            elif 'dsh' in native_harnesses and warning.startswith('ACP initialize response shape unrecognized; capability check unknown: '):
                warnings.append({'reason': make_reason('catalog.acp-initialize-unreadable'),
                                 'diagnostic': warning[len('ACP initialize response shape unrecognized; capability check unknown: '):]})
            elif 'dsh' in native_harnesses and warning.startswith("The native session's configuration options had an unrecognized shape; "):
                continue
            elif ('codex' in native_harnesses and warning.startswith('The native model reading is incomplete: ')
                  and warning[len('The native model reading is incomplete: '):] == old_discoveries.get('codex')):
                continue
            elif 'zcode' in native_harnesses and warning == 'The native model catalog shape was not fully recognized; this reading cannot retire missing buddies':
                warnings.append({'reason': make_reason('catalog.model-shape-unreadable')})
            elif 'zcode' in native_harnesses and warning.startswith('Native replies included unrecognized shapes: '):
                warnings.append({'reason': make_reason('catalog.model-shape-unreadable'),
                                 'diagnostic': warning[len('Native replies included unrecognized shapes: '):]})
            else:
                raise BoardError('UPGRADE_INVALID_BOARD', 'Stored catalog warning has unknown ownership')
            continue
        if not isinstance(warning, dict):
            raise BoardError('UPGRADE_INVALID_BOARD', 'Stored catalog warning has invalid form')
        old = warning.pop('message', None)
        code = warning.pop('code', None)
        if code is None:
            if old is None:
                raise BoardError('UPGRADE_INVALID_BOARD', 'Stored catalog warning has unknown ownership')
            warning['reason'] = make_reason('catalog.native-warning')
            warning['content'] = old
        else:
            if code not in _WARNING_CODES:
                raise BoardError('UPGRADE_INVALID_BOARD', 'Stored catalog warning has unknown code')
            warning['reason'] = make_reason(_WARNING_CODES[code])
        warnings.append(warning)
    value['warnings'] = warnings
    return value


def _upgrade_catalog_and_health(connection: sqlite3.Connection) -> None:
    for row in connection.execute('SELECT harness,record_json FROM harness_health').fetchall():
        value = _health_record(json.loads(row[1]), owning_harness=row[0])
        connection.execute('UPDATE harness_health SET record_json=? WHERE harness=?',
                           (canonical_json(value), row[0]))
    copied_reasons = {}
    for row in connection.execute('SELECT observation_id,harnesses_json,response_json FROM catalog_observations').fetchall():
        harnesses = [_health_record(item) for item in json.loads(row[1])]
        response = json.loads(row[2]) if row[2] is not None else None
        if response is not None:
            old_discoveries = {item['harness']: item.get('reason')
                               for item in response['catalog'].get('discoveries', [])}
            _catalog_payload(response['catalog'])
            for item in response['catalog'].get('discoveries', []):
                copied_reasons[(row[0], item['harness'])] = (old_discoveries[item['harness']], item['reason'])
            model_reasons = {(provider['harness'], provider['provider'], model['id']): model
                             for provider in response['catalog']['providers'] for model in provider['models']}
            for buddy in response.get('buddies', []):
                old = buddy.pop('unavailableReason', None)
                model = model_reasons.get((buddy['harness'], buddy['provider'], buddy['model']))
                if model is None or old != model['unavailableContent']:
                    raise BoardError('UPGRADE_INVALID_BOARD', 'Catalog buddy reason lacks native model ownership')
                buddy['unavailableReason'] = model['unavailableReason']
                buddy['unavailableContent'] = model['unavailableContent']
            response.pop('note', None)
        connection.execute('UPDATE catalog_observations SET harnesses_json=?,response_json=? WHERE observation_id=?',
                           (canonical_json(harnesses), canonical_json(response) if response is not None else None, row[0]))
    for row in connection.execute('SELECT discovery_id,payload_json FROM evaluation_catalog').fetchall():
        connection.execute('UPDATE evaluation_catalog SET payload_json=? WHERE discovery_id=?',
                           (canonical_json(_catalog_payload(json.loads(row[1]))), row[0]))
    for row in connection.execute('SELECT harness,observation_id,reason FROM catalog_current WHERE reason IS NOT NULL').fetchall():
        if row[2] == 'ACCOUNT_STATUS_UNKNOWN':
            reason = make_reason('catalog.account-status-unknown', harness=row[0])
        else:
            # Its reason is a copy of this observation's discovery reason.
            copied = copied_reasons.get((row[1], row[0]))
            if copied is None or copied[0] != row[2]:
                raise BoardError('UPGRADE_INVALID_BOARD', 'Catalog current reason has unknown ownership')
            reason = copied[1]
        connection.execute('UPDATE catalog_current SET reason=? WHERE harness=?',
                           (canonical_json(reason), row[0]))
    native_reasons: set[tuple[str, str, str, str, str]] = set()
    discovery_ids = set()
    for discovery_id, payload_json in connection.execute('SELECT discovery_id,payload_json FROM evaluation_catalog'):
        discovery_ids.add(discovery_id)
        payload = json.loads(payload_json)
        for provider in payload.get('providers', []):
            for model in provider.get('models', []):
                if (model.get('unavailableReason') or {}).get('code') == 'catalog.native-unavailable':
                    native_reasons.add((discovery_id, provider['harness'], provider['provider'],
                                        model['id'], model['unavailableContent']))
    # A copied table permits the CHECK constraints to apply to upgraded stock.
    ddl = EVALUATION_TABLES[3].split(';', 1)[0].replace('CREATE TABLE IF NOT EXISTS buddies',
                                                       'CREATE TABLE buddies_upgrade', 1)
    connection.execute(ddl)
    rows = connection.execute('SELECT * FROM buddies').fetchall()
    for row in rows:
        old = row[-1]
        discovery_id = row[11][len('catalog:'):] if row[11].startswith('catalog:') else None
        if old is not None and discovery_id is not None and discovery_id not in discovery_ids:
            raise BoardError('UPGRADE_INVALID_BOARD', 'Buddy unavailable reason lacks catalog provenance')
        if old is None:
            code, params, content = None, None, None
        elif (discovery_id, row[2], row[3], row[4], old) in native_reasons:
            reason = make_reason('catalog.native-unavailable')
            code, params, content = reason['code'], canonical_json(reason['params']), old
        elif old in _CATALOG_FACTS and row[11].startswith('catalog:'):
            reason = make_reason(_CATALOG_FACTS[old])
            code, params, content = reason['code'], canonical_json(reason['params']), None
        elif old in _PROBE_CODES and row[11].startswith('catalog:'):
            reason = _harness_reason(old)
            code, params, content = reason['code'], canonical_json(reason['params']), None
        elif old == 'ACCOUNT_BINDING_CHANGED' and row[11].startswith('catalog:'):
            reason = make_reason('catalog.account-binding-changed')
            code, params, content = reason['code'], canonical_json(reason['params']), None
        elif row[11].startswith('catalog:'):
            code, params, content = 'history.reason-unavailable', canonical_json({'recordId': row[0]}), None
        else:
            raise BoardError('UPGRADE_INVALID_BOARD', 'Buddy unavailable reason has unknown ownership')
        if row[6] and code is not None:
            raise BoardError('UPGRADE_INVALID_BOARD', 'Available buddy has an unavailable reason')
        connection.execute('INSERT INTO buddies_upgrade VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (*row[:-1], code, params, content))
    connection.execute('DROP TABLE buddies')
    connection.execute('ALTER TABLE buddies_upgrade RENAME TO buddies')
    connection.execute('CREATE INDEX buddies_current_idx ON buddies(available, buddy_id)')
    connection.execute('CREATE INDEX buddies_harness_idx ON buddies(harness, buddy_id)')


def upgrade_schema_24_to_25(state: Path) -> None:
    """Convert a stopped schema-24 board after its exact rollback backup exists."""
    with closing(sqlite3.connect(Path(state) / 'board.sqlite3')) as connection:
        # Rebuild buddies inside one transaction; its dependent preferences are
        # checked against the final table before commit.
        connection.execute('PRAGMA foreign_keys=OFF')
        connection.execute('BEGIN IMMEDIATE')
        try:
            row = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row is None or row[0] != str(UPGRADE_BASE_SCHEMA):
                raise BoardError('UNSUPPORTED_SCHEMA', 'The pending upgrade accepts schema 24 and writes schema 25')
            connection.execute(MACRO_TASK_DELETIONS_DDL)
            connection.execute('ALTER TABLE commands ADD COLUMN ' + COMMAND_DELETION_COLUMN)
            _upgrade_catalog_and_health(connection)
            connection.execute("UPDATE meta SET value='25' WHERE key='schema_version'")
            if connection.execute('PRAGMA foreign_key_check').fetchall():
                raise BoardError('UPGRADE_INVALID_BOARD', 'The upgraded board has foreign key violations')
            connection.commit()
            connection.execute('PRAGMA foreign_keys=ON')
        except BaseException:
            connection.rollback()
            raise
