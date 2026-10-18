"""The one pending, offline upgrade from the 0.42.0 board to schema 25."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import json
import re
import sqlite3

from .db import (COMMAND_DELETION_COLUMN, CORE_SCHEMA, HOST_CONCLUSION_INDEX, HOST_CONCLUSION_TABLE,
                 MACRO_TASK_DELETIONS_DDL, PROVIDER_COOLING_DDL, WORKFLOW_REQUESTS_DDL, WORKFLOW_TURNS_DDL,
                 EVALUATION_TABLES, WORKFLOW_TABLES, canonical_json)
from ...errors import BoardError
from ...protocol.reasons import REGISTERED_REASON_PARAMETERS, reason as make_reason


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
    'UNSUPPORTED_HARNESS',
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


# Fields read_health added to the schema-24 record before freezing an observation.
_HEALTH_VIEW_FIELDS = frozenset({
    'billingByProvider', 'quota', 'quotaRouting', 'harness', 'available', 'status',
    'account', 'revision', 'manualPath', 'checkedAt', 'expiresAt', 'scanAfter',
    'systemSandbox', 'readOnlyStructured',
})


def _health_record(value: dict, *, owning_harness: str | None = None, status: str | None = None,
                   refresh_errors: set[tuple[str, str]] | None = None, snapshot: bool = False) -> dict:
    """Change known health-owned reason sites, leaving native diagnostics intact."""
    harness = value.get('harness') or owning_harness
    if 'reason' not in value and 'reasonCode' not in value:
        record = {key: item for key, item in value.items() if key not in _HEALTH_VIEW_FIELDS} if snapshot else value
        bare_claim = not record or record == {'lastProbe': None}
        unknown_probe_after_claim = (set(record) == {'lastProbe'} and
                                     isinstance(record['lastProbe'], dict) and
                                     set(record['lastProbe']) == {'status', 'reasonCode', 'observedAt', 'candidates'} and
                                     record['lastProbe']['status'] == 'unknown')
        ready_without_root_reason = status == 'ready' and value.get('status') == 'ready'
        if status == 'unknown' and (bare_claim or unknown_probe_after_claim):
            # Schema 24's bare scan claim started with {}; an unknown probe
            # could then add just lastProbe. read_health supplied this root
            # cause on reads but never wrote it to either row shape.
            value['reason'] = make_reason('harness.not-checked', operation='probe')
        elif ready_without_root_reason:
            # Codex account recovery removes the root reasonCode while the
            # last probe may still describe an older unknown result.
            value['reason'] = None
        elif status is not None:
            raise BoardError('UPGRADE_INVALID_BOARD', 'Stored health reason is missing')
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
            elif item is value and code in _DISCOVERY_ERRORS:
                if (harness, code) not in (refresh_errors or set()):
                    raise BoardError('UPGRADE_INVALID_BOARD', 'Stored catalog refresh failure lacks writer evidence')
                item['reason'] = make_reason('catalog.discovery-failed', harness=harness, errorCode=code)
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
    if value == 'native-metadata-wait-expired':
        return make_reason('native-metadata-wait-expired'), None
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
    service_copy = value.get('source') in ('current-native-observations', 'harness-health')
    native_harnesses = {name for name, source in _NATIVE_SOURCES.items() if source in source_parts}
    for provider in value.get('providers', []):
        for model in provider.get('models', []):
            old = model.pop('unavailableReason', None)
            if old is None or model.get('available', True):
                model['unavailableReason'] = None
                model['unavailableContent'] = None
            elif value.get('source') == 'current-native-observations' and old == 'HARNESS_UNHEALTHY':
                model['unavailableReason'] = _harness_reason(old)
                model['unavailableContent'] = None
            else:
                if old == 'native route is unavailable':
                    raise BoardError('UPGRADE_INVALID_BOARD', 'Native unavailable text has ambiguous ownership')
                model['unavailableReason'] = make_reason('catalog.native-unavailable')
                model['unavailableContent'] = old
    for discovery in value.get('discoveries', []):
        old = discovery.get('reason')
        discovery['reason'], discovery['content'] = _catalog_reason(
            old, discovery['harness'], program=service_copy or discovery['harness'] in program_harnesses,
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
            raise BoardError('UPGRADE_INVALID_BOARD', 'Stored catalog warning has unknown code')
        warnings.append(warning)
    value['warnings'] = warnings
    return value


def _upgrade_catalog_and_health(connection: sqlite3.Connection) -> None:
    refresh_errors = set()
    for raw, in connection.execute(
            "SELECT payload_json FROM events WHERE kind='harness.invalidated'").fetchall():
        payload = json.loads(raw)
        harness, code = payload.get('harness'), payload.get('reasonCode')
        if code in _DISCOVERY_ERRORS:
            refresh_errors.add((harness, code))
    for row in connection.execute('SELECT harness,status,record_json FROM harness_health').fetchall():
        value = _health_record(json.loads(row[2]), owning_harness=row[0], status=row[1],
                               refresh_errors=refresh_errors)
        connection.execute('UPDATE harness_health SET record_json=? WHERE harness=?',
                           (canonical_json(value), row[0]))
    copied_reasons = {}
    for row in connection.execute('SELECT observation_id,harnesses_json,response_json FROM catalog_observations').fetchall():
        harnesses = [_health_record(item, status=item['status'], refresh_errors=refresh_errors, snapshot=True)
                     for item in json.loads(row[1])]
        response = json.loads(row[2]) if row[2] is not None else None
        if response is not None:
            old_discoveries = {item['harness']: item.get('reason')
                               for item in response['catalog'].get('discoveries', [])}
            old_model_reasons = {(provider['harness'], provider['provider'], model['id']): model.get('unavailableReason')
                                 for provider in response['catalog']['providers'] for model in provider['models']}
            _catalog_payload(response['catalog'])
            for item in response['catalog'].get('discoveries', []):
                copied_reasons[(row[0], item['harness'])] = (old_discoveries[item['harness']], item['reason'])
            model_reasons = {(provider['harness'], provider['provider'], model['id']): model
                             for provider in response['catalog']['providers'] for model in provider['models']}
            for buddy in response.get('buddies', []):
                old = buddy.pop('unavailableReason', None)
                key = (buddy['harness'], buddy['provider'], buddy['model'])
                model = model_reasons.get(key)
                if model is None or old != old_model_reasons[key]:
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
        # Known board values remain identifiable after their discovery is retired.
        if old is None:
            code, params, content = None, None, None
        elif old in _CATALOG_FACTS:
            reason = make_reason(_CATALOG_FACTS[old])
            code, params, content = reason['code'], canonical_json(reason['params']), None
        elif old in _PROBE_CODES:
            reason = _harness_reason(old)
            code, params, content = reason['code'], canonical_json(reason['params']), None
        elif old == 'ACCOUNT_BINDING_CHANGED':
            reason = make_reason('catalog.account-binding-changed')
            code, params, content = reason['code'], canonical_json(reason['params']), None
        elif (discovery_id, row[2], row[3], row[4], old) in native_reasons:
            reason = make_reason('catalog.native-unavailable')
            code, params, content = reason['code'], canonical_json(reason['params']), old
        elif old in _DISCOVERY_ERRORS:
            if (row[2], old) not in refresh_errors:
                raise BoardError('UPGRADE_INVALID_BOARD', 'Buddy catalog refresh failure lacks writer evidence')
            reason = make_reason('catalog.discovery-failed', harness=row[2], errorCode=old)
            code, params, content = reason['code'], canonical_json(reason['params']), None
        elif discovery_id is not None and discovery_id not in discovery_ids:
            raise BoardError('UPGRADE_INVALID_BOARD', 'Buddy unavailable reason lacks catalog provenance')
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
    for seq, raw in connection.execute(
            "SELECT seq,payload_json FROM events WHERE kind IN "
            "('harness.checked','harness.probe_unknown','harness.account_read_failed',"
            "'harness.account_read_unknown','harness.invalidated')").fetchall():
        payload = json.loads(raw)
        _health_record(payload, refresh_errors=refresh_errors)
        connection.execute('UPDATE events SET payload_json=? WHERE seq=?', (canonical_json(payload), seq))


# The old helper-report summary was one of these service codes, never content.
_HELPER_REPORT_CODES = {
    'helpers-settled': 'helpers-settled',
    'ANCESTOR_TERMINAL': 'ANCESTOR_TERMINAL',
    'CONFLICT': 'CONFLICT',
    'SHUTDOWN_UNCONFIRMED': 'SHUTDOWN_UNCONFIRMED',
    'REVISION_CONFLICT': 'REVISION_CONFLICT',
    'UPGRADE_IN_PROGRESS': 'UPGRADE_IN_PROGRESS',
    'ILLEGAL_TRANSITION': 'ILLEGAL_TRANSITION',
}
_SYSTEM_REQUEST_TEXT_KEYS = frozenset((
    'summary', 'attempted', 'neededWork', 'expectedArtifacts', 'acceptance', 'suggestedBuddyId',
))


def _recorded_takeover(connection: sqlite3.Connection, run_id: str, at: str,
                       owner_generation: int) -> bool:
    return connection.execute(
        "WITH RECURSIVE owners(run_id) AS (SELECT ? UNION "
        "SELECT c.parent_run_id FROM workflow_children c JOIN owners o ON c.child_task_id=o.run_id) "
        "SELECT 1 FROM events e JOIN owners o ON e.task_id=o.run_id "
        "WHERE e.kind='workflow.takeover' AND e.created_at>=?"
        " AND (e.task_id<>? OR json_extract(e.payload_json,'$.previousOwnerGeneration')=?) LIMIT 1",
        (run_id, at, run_id, owner_generation)).fetchone() is not None


def _upgrade_workflow_requests(connection: sqlite3.Connection) -> None:
    cursor = connection.execute('SELECT * FROM workflow_requests ORDER BY rowid')
    columns = [column[0] for column in cursor.description]
    rows = {row[0]: dict(zip(columns, row)) for row in cursor.fetchall()}
    converted: dict[str, tuple[dict | None, dict]] = {}
    visiting: set[str] = set()

    def convert(record_id: str) -> tuple[dict | None, dict]:
        if record_id in converted:
            return converted[record_id]
        if record_id in visiting:
            raise BoardError('UPGRADE_INVALID_BOARD', 'Workflow request proxy chain contains a cycle')
        visiting.add(record_id)
        row = rows[record_id]
        payload = json.loads(row['payload_json'])
        structured = None
        system = False
        proxy = payload.get('proxy')
        if proxy is not None:
            source_id = proxy.get('requestId') if isinstance(proxy, dict) else None
            source = rows.get(source_id)
            if source is None or source['run_id'] != proxy.get('runId') or row['child_task_id'] != source['run_id']:
                raise BoardError('UPGRADE_INVALID_BOARD', 'Workflow request proxy source cannot be attributed')
            structured, source_payload = convert(source_id)
            if source.get('decision_id') is not None:
                row['decision_id'] = source['decision_id']
            if structured is not None or row.get('decision_id') is not None:
                # Keep the original lineage facts, replacing only the system body.
                payload = {**source_payload, 'origin': payload['origin'], 'proxy': proxy}
                system = True
        elif payload.get('source') == 'routing':
            decision_id = payload.get('decisionId')
            if decision_id is not None and (not isinstance(decision_id, str) or not decision_id or
                    connection.execute('SELECT 1 FROM evaluation_decisions WHERE decision_id=?',
                                       (decision_id,)).fetchone() is None):
                raise BoardError('UPGRADE_INVALID_BOARD', 'Routing request has no decision owner')
            takeover = connection.execute(
                "SELECT owner_generation FROM workflow_routes WHERE run_id=? AND state='fenced'"
                " AND reason='Host takeover fenced model selection' AND updated_at=? LIMIT 1",
                (row['run_id'], row['created_at'])).fetchone()
            if takeover is not None and _recorded_takeover(
                    connection, row['run_id'], row['created_at'], takeover['owner_generation']):
                structured = make_reason('routing-taken-over')
                row['decision_id'] = decision_id
            elif decision_id is None:
                match = (re.match(r'^([A-Z][A-Z0-9_]*): .+', row['summary'])
                         if isinstance(row['summary'], str) else None)
                structured = (make_reason('dispatch.buddy-rejected', errorCode=match[1]) if match else
                              make_reason('history.reason-unavailable', recordId=record_id))
            else:
                route = connection.execute('SELECT run_id,state,reason FROM workflow_routes WHERE decision_id=?',
                                           (decision_id,)).fetchone()
                if route is None or route['run_id'] != row['run_id']:
                    raise BoardError('UPGRADE_INVALID_BOARD', 'Routing request lacks route provenance')
                decision = connection.execute('SELECT status FROM evaluation_decisions WHERE decision_id=?',
                                              (decision_id,)).fetchone()
                row['decision_id'] = decision_id
                if decision['status'] == 'completed':
                    if route['state'] != 'needs-host':
                        raise BoardError('UPGRADE_INVALID_BOARD', 'Completed routing request lacks validation boundary')
                    diagnostic = route['reason'].split(':', 1)[0] if isinstance(route['reason'], str) else ''
                    if isinstance(route['reason'], str) and re.match(r'^[A-Z][A-Z0-9_]*: .+', route['reason']):
                        structured = make_reason('dispatch.buddy-rejected', errorCode=diagnostic)
                    elif route['reason'] == 'No legal coding buddy was selected':
                        structured = make_reason('routing-unusable-selection')
                    else:
                        raise BoardError('UPGRADE_INVALID_BOARD', 'Completed routing request has unknown validation outcome')
                elif decision['status'] in ('queued', 'running') and route['state'] != 'fenced':
                    raise BoardError('UPGRADE_INVALID_BOARD', 'Routing request has unknown failure ownership')
            payload = {'source': 'routing'}
            system = True
        elif payload.get('reason') == 'workspace-scope-violation' and 'conflictIds' in payload:
            system = True
            ids, paths = payload.get('conflictIds'), payload.get('paths')
            if (isinstance(ids, list) and ids and all(isinstance(item, str) and item for item in ids)
                    and isinstance(paths, list) and all(isinstance(item, str) for item in paths)):
                structured = make_reason('workspace-scope-violation', conflictIds=ids, paths=paths)
            for key in ('reason', 'conflictIds', 'paths'):
                payload.pop(key, None)
        elif (isinstance(payload.get('continuationId'), str)
              and record_id == 'prep-' + payload['continuationId'] and 'preparationError' in payload):
            system = True
            if payload['continuationId']:
                structured = make_reason('workspace.continuation-prepare-failed', continuationId=payload['continuationId'])
            payload.pop('continuationId')
        elif payload.get('source') == 'lead-lifecycle':
            system = True
            turn = connection.execute('SELECT disposition FROM workflow_turns WHERE turn_id=? AND run_id=?',
                                      (row['turn_id'], row['run_id'])).fetchone()
            facts, total = payload.get('unfinishedOwnedMicroTasks'), payload.get('unfinishedOwnedMicroTaskTotal')
            if (turn is not None and turn[0] in ('completed', 'waiting') and isinstance(facts, list)
                    and isinstance(total, int) and not isinstance(total, bool) and total >= len(facts)
                    and all(isinstance(item, dict) and set(item) == {'runId', 'state'}
                            and isinstance(item['runId'], str) and isinstance(item['state'], str) for item in facts)):
                structured = make_reason('lead.result-refused', disposition=turn[0], unfinished=facts, total=total)
            for key in ('source', 'unfinishedOwnedMicroTasks', 'unfinishedOwnedMicroTaskTotal'):
                payload.pop(key, None)
        elif row['kind'] == 'helper-report':
            if row['summary'] not in _HELPER_REPORT_CODES:
                raise BoardError('UPGRADE_INVALID_BOARD', 'Workflow helper report code cannot be attributed')
            system = True
            structured = make_reason(_HELPER_REPORT_CODES[row['summary']])
        if system and (structured is not None or row.get('decision_id') is None):
            if structured is None:
                structured = make_reason('history.reason-unavailable', recordId=record_id)
            payload = {key: value for key, value in payload.items() if key not in _SYSTEM_REQUEST_TEXT_KEYS}
        converted[record_id] = structured, payload
        visiting.remove(record_id)
        return converted[record_id]

    connection.execute(WORKFLOW_REQUESTS_DDL.replace('workflow_requests (', 'workflow_requests_upgrade ('))
    new_columns = columns[:columns.index('payload_json')] + ['reason_code', 'reason_params_json',
                                                               'decision_id'] + columns[columns.index('payload_json'):]
    for record_id, row in rows.items():
        structured, payload = convert(record_id)
        row['reason_code'] = structured['code'] if structured else None
        row['reason_params_json'] = canonical_json(structured['params']) if structured else None
        row.setdefault('decision_id', None)
        if structured is not None or row['decision_id'] is not None:
            row['summary'] = None
            row['payload_json'] = canonical_json(payload)
        connection.execute('INSERT INTO workflow_requests_upgrade (' + ','.join(new_columns) + ') VALUES ('
                           + ','.join('?' for _ in new_columns) + ')', [row[key] for key in new_columns])
    connection.execute('DROP TABLE workflow_requests')
    connection.execute('ALTER TABLE workflow_requests_upgrade RENAME TO workflow_requests')
    connection.execute('CREATE INDEX workflow_requests_run_idx ON workflow_requests(run_id, state)')
    for seq, raw in connection.execute("SELECT seq,payload_json FROM events WHERE kind='workflow.lead_result_refused'").fetchall():
        payload = json.loads(raw)
        payload.pop('reason', None)
        connection.execute('UPDATE events SET payload_json=? WHERE seq=?', (canonical_json(payload), seq))


_NOTE_EVENTS = frozenset({
    'workflow.request_declined', 'workflow.review_rejected', 'workflow.buddy_overridden',
    'workflow.cancelled', 'workflow.helper_cancelled', 'workflow.lead_task_cancelled',
    'task.cancelled', 'task.cancel_requested', 'task.retried',
})


def _note(value: str | None) -> str | None:
    return value or None


def _manual_cancel_continuation(connection: sqlite3.Connection, row: sqlite3.Row,
                                payload: dict) -> str:
    candidates = connection.execute(
        "SELECT c.continuation_id FROM workflow_continuations c JOIN events e"
        " ON e.task_id=c.run_id AND e.kind='workflow.continued'"
        " AND json_extract(e.payload_json,'$.continuationId')=c.continuation_id"
        " WHERE c.run_id=? AND c.kind='manual' AND c.helper_policy='cancel'"
        " AND c.created_at<=? AND e.created_at>=? AND e.seq>?",
        (payload['cancelledByRunId'], row['created_at'], row['created_at'], row['seq']),
    ).fetchall()
    if len(candidates) != 1:
        raise BoardError('UPGRADE_INVALID_BOARD', 'Manual helper cancellation lacks one continuation')
    return candidates[0][0]


def _converted_event_note(connection: sqlite3.Connection, row: sqlite3.Row,
                          payload: dict) -> tuple[bool, str | None]:
    """Return whether an old reason belongs to the authored-note contract."""
    kind, value, role = row['kind'], payload['reason'], row['actor_role']
    if role == 'service':
        if isinstance(value, str) and value.startswith('service stop: '):
            body = value[len('service stop: '):]
            return True, None if body in ('service stop requested', 'signal') else _note(body)
        return False, None
    if kind == 'task.retried':
        return True, None if value == 'explicit retry' else _note(value)
    if role not in ('host', 'console', 'lead'):
        return False, None
    if kind in ('task.cancelled', 'task.cancel_requested'):
        return True, None if value == 'operator request' else _note(value)
    if kind in ('workflow.cancelled', 'workflow.helper_cancelled', 'workflow.lead_task_cancelled'):
        if value == 'Owned micro task stopped because its Lead is being concluded':
            payload['leadRunId'] = (row['task_id'] if kind == 'workflow.cancelled'
                                    else payload['cancelledByRunId'])
            return True, None
        if value == 'manual continuation cancelled live helpers' and kind == 'workflow.helper_cancelled':
            payload['continuationId'] = _manual_cancel_continuation(connection, row, payload)
            return True, None
        defaults = ('Host cancelled', 'User stopped macro task') if role == 'console' else ('Host cancelled',)
        return True, None if value in defaults else _note(value)
    return True, _note(value)


def _upgrade_notes(connection: sqlite3.Connection) -> None:
    """Change mutable Host and user annotations without rewriting frozen evidence."""
    definition = next(part for part in EVALUATION_TABLES
                      if 'CREATE TABLE IF NOT EXISTS evaluation_preferences (' in part)
    connection.execute(definition.split('CREATE INDEX')[0].replace(
        'evaluation_preferences (', 'evaluation_preferences_upgrade ('))
    connection.execute("INSERT INTO evaluation_preferences_upgrade(buddy_id,mode,note)"
                       " SELECT buddy_id,mode,NULLIF(reason,'') FROM evaluation_preferences")
    connection.execute('DROP TABLE evaluation_preferences')
    connection.execute('ALTER TABLE evaluation_preferences_upgrade RENAME TO evaluation_preferences')
    connection.execute('CREATE INDEX evaluation_preferences_mode_idx ON evaluation_preferences(mode, buddy_id)')
    for table, old, new in (
        ('workflow_turns', 'access_reason', 'access_note'),
        ('workflow_continuations', 'reason', 'note'),
        ('workflow_scope_versions', 'reason', 'note'),
        ('workflow_workspace_conflicts', 'reason', 'note'),
        ('workflow_integrations', 'reason', 'note'),
    ):
        connection.execute(f'ALTER TABLE {table} RENAME COLUMN {old} TO {new}')
        connection.execute(f"UPDATE {table} SET {new}=NULL WHERE {new}='' ")

    connection.execute(
        "UPDATE workflow_continuations SET note=NULL WHERE kind='lead-wakeup'"
        " AND note='owned-micro-task-boundary'")
    for run_id, version, raw, note in connection.execute(
        'SELECT run_id,scope_version,stopped_evidence_json,note FROM workflow_scope_versions'
    ).fetchall():
        if version != 1:
            continue
        kind = json.loads(raw)['kind']
        if (kind == 'initial' and note == 'original submission'
                or kind == 'helper' and note == 'helper admission'):
            connection.execute('UPDATE workflow_scope_versions SET note=NULL WHERE run_id=? AND scope_version=1',
                               (run_id,))

    for run_id, raw in connection.execute('SELECT run_id,execution_workspace_json FROM workflow_runs').fetchall():
        workspace = json.loads(raw)
        if 'accessReason' in workspace:
            workspace['accessNote'] = _note(workspace.pop('accessReason'))
            connection.execute('UPDATE workflow_runs SET execution_workspace_json=? WHERE run_id=?',
                               (canonical_json(workspace), run_id))
    for integration_id, raw in connection.execute(
        'SELECT integration_id,verification_json FROM workflow_integrations'
    ).fetchall():
        verification = json.loads(raw)
        if 'reason' in verification:
            verification['note'] = _note(verification.pop('reason'))
            connection.execute('UPDATE workflow_integrations SET verification_json=? WHERE integration_id=?',
                               (canonical_json(verification), integration_id))
    for request_id, raw in connection.execute(
        'SELECT request_id,decision_json FROM workflow_requests WHERE decision_json IS NOT NULL'
    ).fetchall():
        decision = json.loads(raw)
        if 'reason' in decision:
            decision['note'] = _note(decision.pop('reason'))
            connection.execute('UPDATE workflow_requests SET decision_json=? WHERE request_id=?',
                               (canonical_json(decision), request_id))
    for row in connection.execute('SELECT seq,task_id,kind,payload_json,actor_role,created_at FROM events').fetchall():
        seq, kind, raw = row['seq'], row['kind'], row['payload_json']
        if kind not in _NOTE_EVENTS and kind != 'workflow.access_changed':
            continue
        payload = json.loads(raw)
        if kind in {'task.cancelled', 'task.cancel_requested'} and 'owningRunId' in payload:
            continue
        changed = False
        if kind in _NOTE_EVENTS and 'reason' in payload:
            convert, value = _converted_event_note(connection, row, payload)
            if convert:
                payload.pop('reason')
                payload['note'] = value
                changed = True
        if kind == 'workflow.access_changed' and 'accessReason' in payload:
            payload['accessNote'] = _note(payload.pop('accessReason'))
            changed = True
        if changed:
            connection.execute('UPDATE events SET payload_json=? WHERE seq=?', (canonical_json(payload), seq))
    for task_id, artifact_id, opinion in connection.execute(
        'SELECT t.task_id,r.final_artifact_id,t.review_rejection_note FROM tasks t'
        ' JOIN workflow_runs r ON r.run_id=t.task_id'
        ' WHERE t.review_rejected_at IS NOT NULL AND t.review_rejection_note IS NOT NULL'
    ).fetchall():
        event = connection.execute(
            "SELECT payload_json FROM events WHERE task_id=? AND kind='workflow.review_rejected'"
            ' ORDER BY seq DESC LIMIT 1', (task_id,),
        ).fetchone()
        payload = json.loads(event[0]) if event is not None else None
        if (payload is not None and payload.get('artifactId') == artifact_id
                and payload.get('opinion') == opinion
                and 'note' in payload and payload['note'] is None):
            connection.execute('UPDATE tasks SET review_rejection_note=NULL WHERE task_id=?', (task_id,))


def _auto_conclusion_lead(connection: sqlite3.Connection, row: dict, lead_run_id: str) -> bool:
    """Match the old generated sentence to a concluded owning Lead."""
    if row['note'] != f'Concluded because Lead micro task {lead_run_id} received a Host conclusion':
        return False
    parent = connection.execute('SELECT * FROM workflow_host_conclusions WHERE run_id=?',
                                (lead_run_id,)).fetchone()
    if parent is None:
        return False
    return (row['created_at'] == parent['created_at'] and row['actor_role'] == parent['actor_role']
            and row['actor_id'] == parent['actor_id'])


def _conclusion_ancestors(connection: sqlite3.Connection, run_id: str) -> list[str]:
    return [row[0] for row in connection.execute(
        'WITH RECURSIVE edges(owned_run_id,lead_run_id) AS ('
        ' SELECT owned_run_id,lead_run_id FROM workflow_lead_tasks'
        ' UNION SELECT child_task_id,parent_run_id FROM workflow_children),'
        ' ancestors(run_id) AS ('
        ' SELECT lead_run_id FROM edges WHERE owned_run_id=?'
        ' UNION SELECT e.lead_run_id FROM edges e JOIN ancestors a ON e.owned_run_id=a.run_id)'
        ' SELECT a.run_id FROM ancestors a JOIN workflow_runs r ON r.run_id=a.run_id'
        ' WHERE r.delegation_authority=1', (run_id,))]


def _upgrade_host_conclusions(connection: sqlite3.Connection) -> None:
    rows = [dict(row) for row in connection.execute('SELECT * FROM workflow_host_conclusions')]
    connection.execute(HOST_CONCLUSION_TABLE.replace('workflow_host_conclusions (',
                                                 'workflow_host_conclusions_upgrade ('))
    for row in rows:
        candidates = [lead_id for lead_id in _conclusion_ancestors(connection, row['run_id'])
                      if _auto_conclusion_lead(connection, row, lead_id)]
        if len(candidates) > 1:
            raise BoardError('UPGRADE_INVALID_BOARD', 'Generated conclusion has ambiguous Lead ownership')
        if candidates:
            lead_run_id = candidates[0]
            row['note'] = None
            row['reason_code'] = 'lead.owned-concluded'
            row['reason_params_json'] = canonical_json({'leadRunId': lead_run_id})
            for seq, raw in connection.execute(
                "SELECT seq,payload_json FROM events WHERE kind='workflow.host_concluded' AND task_id=?",
                (row['run_id'],),
            ).fetchall():
                payload = json.loads(raw)
                if payload.get('conclusionId') == row['conclusion_id']:
                    payload['reason'] = make_reason('lead.owned-concluded', leadRunId=lead_run_id)
                    connection.execute('UPDATE events SET payload_json=? WHERE seq=?',
                                       (canonical_json(payload), seq))
        else:
            row['reason_code'] = row['reason_params_json'] = None
            row['note'] = _note(row['note'])
        columns = list(row)
        connection.execute('INSERT INTO workflow_host_conclusions_upgrade (' + ','.join(columns) +
                           ') VALUES (' + ','.join('?' for _ in columns) + ')', [row[key] for key in columns])
    connection.execute('DROP TABLE workflow_host_conclusions')
    connection.execute('ALTER TABLE workflow_host_conclusions_upgrade RENAME TO workflow_host_conclusions')
    connection.execute(HOST_CONCLUSION_INDEX)


_ROUTER_UNAVAILABLE_OLD = (
    ('之后没有更多 Router', 'router-list-exhausted'),
    ('项当前都不可用', 'router-all-unavailable'),
    ('冻结时尚未发布', 'router-not-published-at-freeze'),
    ('已发布目录中不可用', 'router-catalog-unavailable'),
    ('已禁用', 'router-disabled'),
    ('harness ', 'router-harness-unavailable'),
)
_RELEASE_REASON_CODES = {
    'service stopped before execution launch': 'service-stopped-before-launch',
    'execution launch failed before process creation': 'execution-launch-failed',
    'No execution process was created': 'execution-process-not-created',
}


def _old_router_unavailable(value: str, record_id: str) -> dict:
    code = next((new for phrase, new in _ROUTER_UNAVAILABLE_OLD if phrase in value), None)
    return {'code': code, 'params': {}} if code else make_reason('history.reason-unavailable', recordId=record_id)


def _upgrade_preference_notes(entries: list[dict]) -> list[dict]:
    """The schema-24 preference writer stores the user's text verbatim."""
    converted = []
    for entry in entries:
        item = dict(entry)
        item['note'] = item.pop('reason') or None
        converted.append(item)
    return converted


def _upgrade_router_snapshot(value, *, record_id: str):
    """Convert board-authored, unsent routing snapshots only."""
    if not isinstance(value, dict):
        raise BoardError('UPGRADE_INVALID_BOARD', 'Old Router snapshot has unknown ownership')
    def issue(item):
        if not isinstance(item, dict):
            raise BoardError('UPGRADE_INVALID_BOARD', 'Old Router issue has unknown ownership')
        result = dict(item)
        old = result.pop('reason', None)
        if result.get('code') == 'router-unavailable':
            if not isinstance(old, str):
                raise BoardError('UPGRADE_INVALID_BOARD', 'Old Router inspection has unknown ownership')
            result['code'] = _old_router_unavailable(old, record_id)['code']
            if result['code'] == 'history.reason-unavailable':
                result['recordId'] = record_id
        return result

    result = dict(value)
    if 'preferences' in result:
        result['preferences'] = _upgrade_preference_notes(result['preferences'])
    if 'routingBasis' in result:
        basis = dict(result['routingBasis'])
        basis.pop('quotaRetryBuddyIds', None)
        if 'excludedBuddies' in basis:
            basis['excludedBuddies'] = _upgrade_preference_notes(basis['excludedBuddies'])
        result['routingBasis'] = basis
    if 'baseInput' in result:
        result['baseInput'] = _upgrade_router_snapshot(result['baseInput'], record_id=record_id)
    result.pop('routingBoundary', None)
    for key in ('inspections', 'selectionRetryInspections'):
        if key in result:
            if not isinstance(result[key], list):
                raise BoardError('UPGRADE_INVALID_BOARD', 'Old Router inspections have unknown ownership')
            result[key] = [issue(item) for item in result[key]]
    if 'routerProblem' in result:
        result['routerProblem'] = issue(result['routerProblem'])
    return result


def _oversize_reason(connection: sqlite3.Connection, decision_id: str, request: sqlite3.Row | None) -> dict:
    documents = []
    if request is not None and request['input_json']:
        documents.append(json.loads(request['input_json']))
    if request is not None:
        requested = json.loads(request['requested_json'])
        reference = requested.get('taskReference') if isinstance(requested, dict) else None
        if isinstance(reference, dict) and isinstance(reference.get('bytes'), int) and reference['bytes'] > 8192:
            return make_reason('router-input-too-large', bytes=reference['bytes'], limit=8192)
    snapshot = connection.execute('SELECT snapshot_json FROM router_requests WHERE decision_id=?',
                                  (decision_id,)).fetchone()
    if snapshot is not None:
        frozen = json.loads(snapshot[0])
        if isinstance(frozen, dict) and isinstance(frozen.get('baseInput'), dict):
            documents.append(frozen['baseInput'])
    for document in documents:
        if not isinstance(document, dict):
            continue
        for collection, limit in (('buddies', 200), ('preferences', 256), ('annotations', 200)):
            entries = document.get(collection)
            if isinstance(entries, list) and len(entries) > limit:
                return make_reason('router-input-too-large', collection=collection,
                                   count=len(entries), limit=limit)
        size = len(canonical_json(document).encode('utf-8'))
        if size > 128 * 1024:
            return make_reason('router-input-too-large', bytes=size, limit=128 * 1024)
    return make_reason('history.reason-unavailable', recordId=decision_id)


def _decision_reason(connection: sqlite3.Connection, row: sqlite3.Row,
                     request: sqlite3.Row | None) -> tuple[dict | None, str | None]:
    old, error, status = row['reason'], row['error'], row['status']
    if status in ('queued', 'running'):
        return None, None
    route = connection.execute('SELECT state,reason,run_id,owner_generation,updated_at FROM workflow_routes WHERE decision_id=?',
                               (row['decision_id'],)).fetchone()
    if route is not None and route['state'] == 'fenced' and status == 'cancelled':
        run = connection.execute('SELECT state FROM workflow_runs WHERE run_id=?', (route['run_id'],)).fetchone()
        if (route['reason'] == 'Host takeover fenced model selection' and
                _recorded_takeover(connection, route['run_id'], route['updated_at'], route['owner_generation'])):
            return make_reason('routing-taken-over'), None
        if route['reason'] == 'Owning Micro task cancelled' and run is not None and run['state'] == 'cancelled':
            return make_reason('micro-task-cancelled'), None
        return make_reason('history.reason-unavailable', recordId=row['decision_id']), None
    if status == 'cancelled' and old.startswith('the decision run was cancelled') and request is not None:
        cancelled = connection.execute(
            "SELECT 1 FROM events WHERE task_id=? AND kind IN ('task.cancelled','task.cancel_requested') LIMIT 1",
            (request['task_id'],)).fetchone() if request['task_id'] is not None else None
        if cancelled is not None:
            return make_reason('decision-cancelled'), None
    if status == 'cancelled' and old == 'The routing request was cancelled; no recommendation is published':
        return make_reason('decision-cancelled'), None
    if (error == 'router-unavailable' and request is not None and
            request['task_id'] is not None and request['attempt_id'] is not None):
        for (raw,) in connection.execute(
                "SELECT payload_json FROM events WHERE kind='attempt.released' AND task_id=? AND attempt_id=?",
                (request['task_id'], request['attempt_id'])):
            release = json.loads(raw)
            code = _RELEASE_REASON_CODES.get(release.get('reason')) if isinstance(release, dict) else None
            if code is not None and status in ('failed', 'needs-host'):
                return {'code': code, 'params': {}}, None
    if (status == 'failed' and request is not None and request['attempt_id'] is not None and
            old == 'the execution ended before result delivery; no delivered answer can become a current recommendation'
            and connection.execute('SELECT 1 FROM attempt_interruptions WHERE attempt_id=?',
                                   (request['attempt_id'],)).fetchone() is not None):
        return make_reason('interrupted-before-delivery'), None
    output = json.loads(request['output_json']) if request is not None and request['output_json'] else None
    if output is not None and not isinstance(output, dict):
        raise BoardError('UPGRADE_INVALID_BOARD', 'Decision output has unknown ownership')
    program = output.get('programSelection') if output else None
    if (status == 'completed' and error is None and isinstance(program, dict) and
            program.get('code') == 'single-candidate' and program.get('buddyId') == row['buddy_id']):
        return make_reason('single-candidate'), None
    adopted = output.get('decision') if output and output.get('status') == 'ok' and output.get('operation') == 'select' and 'code' not in output else None
    if (isinstance(adopted, dict) and isinstance(adopted.get('reason'), str) and
            isinstance(adopted.get('evidence'), list) and error is None):
        buddy = adopted.get('buddyId')
        if (status == 'completed' and buddy is not None and buddy == row['buddy_id']) or (
                status == 'needs-host' and buddy is None and error is None):
            return (make_reason('router-abstained') if buddy is None else None), adopted['reason']
        if adopted['reason'] == old and error is None:
            raise BoardError('UPGRADE_INVALID_BOARD', 'Decision reason has ambiguous Router ownership')
    if status == 'completed':
        raise BoardError('UPGRADE_INVALID_BOARD', 'Completed decision lacks adopted output provenance')
    if (status == 'needs-host' and error is None and request is not None and request['attempt_id'] is not None
            and output is None):
        if old.startswith('the model output could not be adopted ('):
            match = re.match(r'^the model output could not be adopted \(([A-Za-z][A-Za-z0-9._-]*): ', old)
            if match:
                return (_oversize_reason(connection, row['decision_id'], request) if match[1] == 'router-input-too-large'
                        else make_reason('decision-publication-failed', errorCode=match[1])), None
            return make_reason('history.reason-unavailable', recordId=row['decision_id']), None
    if old == 'the decision result could not be processed; no recommendation is published' and error:
        return make_reason('decision-publication-failed'), None
    if error:
        code = error if re.fullmatch(r'[A-Za-z][A-Za-z0-9._-]*', error) else error.split(':', 1)[0]
        if re.fullmatch(r'[A-Za-z][A-Za-z0-9._-]*', code) and code in REGISTERED_REASON_PARAMETERS:
            if code == 'router-out-of-bounds':
                bounds = {
                    'removed from the candidate table': 'removed', 'is disabled': 'disabled',
                    'is unavailable': 'unavailable', 'excluded by the user': 'excluded',
                    'missing a required task capability': 'capability-missing',
                    'outside current pins, task constraints, or quota limits': 'constraints-or-quota',
                    'selected quota retry was consumed': 'constraints-or-quota',
                }
                bound = next((value for phrase, value in bounds.items() if phrase in old), None)
                return ({'code': 'router-out-of-bounds', 'params': {'bound': bound}} if bound else
                        make_reason('history.reason-unavailable', recordId=row['decision_id'])), None
            if code == 'router-input-too-large':
                return _oversize_reason(connection, row['decision_id'], request), None
            if REGISTERED_REASON_PARAMETERS[code]:
                return make_reason('history.reason-unavailable', recordId=row['decision_id']), None
            return {'code': code, 'params': {}}, None
    preflight_owner = (status == 'needs-host' and request is not None and
                       request['task_id'] is None and output is None)
    if preflight_owner and 'no enabled, available, capability-matching buddy' in old:
        return make_reason('no-legal-candidate'), None
    if preflight_owner and old.startswith('Router 不可用：尚未设置'):
        return make_reason('router-not-configured'), None
    if preflight_owner and old.startswith('The complete Micro task contains '):
        return _oversize_reason(connection, row['decision_id'], request), None
    if (status == 'needs-host' and request is not None and request['attempt_id'] is None and
            isinstance(output, dict) and output.get('status') == 'error' and
            output.get('code') == 'router-unavailable' and error):
        return _old_router_unavailable(old, row['decision_id']), None
    if preflight_owner and old.startswith('Router 不可用：'):
        return _old_router_unavailable(old, row['decision_id']), None
    if old in ('No frozen Router request snapshot is recorded for this dispatch',
               'No frozen Router dispatch is recorded'):
        return make_reason('router-dispatch-unverified'), None
    if old and status in ('failed', 'cancelled') and request is not None:
        return make_reason('history.reason-unavailable', recordId=row['decision_id']), None
    if (old and status == 'needs-host' and (preflight_owner or
            (request is not None and request['attempt_id'] is not None and
             isinstance(output, dict) and output.get('status') != 'ok'))):
        return make_reason('history.reason-unavailable', recordId=row['decision_id']), None
    if not old and status in ('needs-host', 'failed', 'cancelled'):
        return None, None
    raise BoardError('UPGRADE_INVALID_BOARD', 'Decision reason has unknown ownership')


def _upgrade_decisions(connection: sqlite3.Connection) -> None:
    definition = next(part for part in EVALUATION_TABLES
                      if 'CREATE TABLE IF NOT EXISTS evaluation_decisions (' in part)
    connection.execute(definition.replace('evaluation_decisions (', 'evaluation_decisions_upgrade ('))
    requests = {row['decision_id']: row for row in connection.execute('SELECT * FROM decision_requests')}
    for row in connection.execute('SELECT * FROM evaluation_decisions'):
        reason, note = _decision_reason(connection, row, requests.get(row['decision_id']))
        connection.execute(
            'INSERT INTO evaluation_decisions_upgrade(decision_id,status,task,buddy_id,reason_code,'
            'reason_params_json,note,created_at) VALUES(?,?,?,?,?,?,?,?)',
            (row['decision_id'], row['status'], row['task'], row['buddy_id'],
             reason['code'] if reason else None,
             canonical_json(reason['params']) if reason else None, note, row['created_at']))
    connection.execute('DROP TABLE evaluation_decisions')
    connection.execute('ALTER TABLE evaluation_decisions_upgrade RENAME TO evaluation_decisions')
    connection.execute('CREATE INDEX evaluation_decisions_created_idx ON evaluation_decisions(created_at, decision_id)')


def _upgrade_routing_copies(connection: sqlite3.Connection) -> None:
    definition = next(part for part in WORKFLOW_TABLES
                      if 'CREATE TABLE IF NOT EXISTS workflow_routes (' in part)
    connection.execute(definition.replace('workflow_routes (', 'workflow_routes_upgrade ('))
    connection.execute('INSERT INTO workflow_routes_upgrade(decision_id,run_id,owner_generation,state,'
                       'created_at,updated_at) SELECT decision_id,run_id,owner_generation,state,'
                       'created_at,updated_at FROM workflow_routes')
    connection.execute('DROP TABLE workflow_routes')
    connection.execute('ALTER TABLE workflow_routes_upgrade RENAME TO workflow_routes')
    connection.execute('CREATE INDEX workflow_routes_run_idx ON workflow_routes(run_id)')
    for table, column, identity in (('decision_requests', 'requested_json', 'decision_id'),
                                    ('router_requests', 'snapshot_json', 'decision_id')):
        for record_id, raw in connection.execute(f'SELECT {identity},{column} FROM {table} WHERE {column} IS NOT NULL'):
            converted = _upgrade_router_snapshot(json.loads(raw), record_id=record_id)
            if converted != json.loads(raw):
                connection.execute(f'UPDATE {table} SET {column}=? WHERE {identity}=?',
                                   (canonical_json(converted), record_id))
    for record_id, raw in connection.execute(
        'SELECT decision_id,output_json FROM decision_requests WHERE output_json IS NOT NULL'
    ):
        output = json.loads(raw)
        if 'programSelection' in output and 'preferences' in output['programSelection']:
            output['programSelection']['preferences'] = _upgrade_preference_notes(
                output['programSelection']['preferences'])
            connection.execute('UPDATE decision_requests SET output_json=? WHERE decision_id=?',
                               (canonical_json(output), record_id))
    for seq, kind, task_id, attempt_id, raw, actor_role, actor_id in connection.execute(
        'SELECT seq,kind,task_id,attempt_id,payload_json,actor_role,actor_id FROM events'
    ):
        if kind.startswith('decision.') or kind == 'workflow.routing_needs_host':
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise BoardError('UPGRADE_INVALID_BOARD', 'Routing event has unknown ownership')
            for key in ('reason', 'error', 'errorCode', 'routingBoundary'):
                payload.pop(key, None)
            connection.execute('UPDATE events SET payload_json=? WHERE seq=?',
                               (canonical_json(payload), seq))
        elif kind in ('task.cancelled', 'task.cancel_requested') and task_id is not None:
            task = connection.execute('SELECT spec_json FROM tasks WHERE task_id=?', (task_id,)).fetchone()
            spec = json.loads(task[0]) if task is not None else {}
            decision = spec.get('decision') if isinstance(spec, dict) else None
            if isinstance(decision, dict) and isinstance(decision.get('decisionId'), str):
                payload = json.loads(raw)
                owning_run_id = payload.get('owningRunId')
                owned_route = (connection.execute(
                    'SELECT 1 FROM workflow_routes WHERE decision_id=? AND run_id=?',
                    (decision['decisionId'], owning_run_id)).fetchone() if owning_run_id is not None else None)
                if (owned_route is not None or payload.get('phase') == 'queued' and
                        actor_role == 'service' and actor_id == 'service'):
                    payload.pop('reason', None)
                    payload['decisionId'] = decision['decisionId']
                    connection.execute('UPDATE events SET payload_json=? WHERE seq=?',
                                       (canonical_json(payload), seq))
                elif 'reason' in payload:
                    authored, note = _converted_event_note(
                        connection, {'kind': kind, 'actor_role': actor_role}, payload)
                    if not authored:
                        raise BoardError('UPGRADE_INVALID_BOARD', 'Decision task cancellation has unknown ownership')
                    payload.pop('reason')
                    payload['note'] = note
                    connection.execute('UPDATE events SET payload_json=? WHERE seq=?',
                                       (canonical_json(payload), seq))
        elif kind == 'attempt.released':
            payload = json.loads(raw)
            if not isinstance(payload, dict) or attempt_id is None:
                raise BoardError('UPGRADE_INVALID_BOARD', 'Execution release lacks its attempt identity')
            old_reason = payload.get('reason')
            code = _RELEASE_REASON_CODES.get(old_reason)
            if code is None:
                payload['reason'] = make_reason('history.reason-unavailable', recordId=attempt_id)
            else:
                payload['reason'] = {'code': code, 'params': {}}
            connection.execute('UPDATE events SET payload_json=? WHERE seq=?',
                               (canonical_json(payload), seq))
            connection.execute('UPDATE attempts SET error=NULL WHERE attempt_id=? AND error=?',
                               (attempt_id, old_reason))
        elif kind == 'workflow.attempt_released':
            payload = json.loads(raw)
            payload.pop('reason', None)
            connection.execute('UPDATE events SET payload_json=? WHERE seq=?',
                               (canonical_json(payload), seq))
        elif kind == 'attempt.uncertain':
            payload = json.loads(raw)
            payload.pop('reason', None)
            connection.execute('UPDATE events SET payload_json=? WHERE seq=?',
                               (canonical_json(payload), seq))


# Each schema-24 sentence is owned by one event writer.
_INQUIRY_PROGRAM_REASONS = {
    'the attempt ended before this question could be delivered': ('inquiry-queued-at-attempt-end', ('service', 'attempt-finished')),
    'this adapter observes native activity but has no correlated inquiry capability': ('inquiry-observe-only', ('bridge', 'live-bridge')),
    'the live bridge reported answered without usable answer text': ('inquiry-answer-empty', ('bridge', 'live-bridge')),
    'the bridge recorded an answered journal entry without usable answer text': ('inquiry-answer-empty', ('bridge', 'bridge-journal')),
    'the micro task root turn ended before this inquiry was answered': ('inquiry-root-turn-ended', ('bridge', 'bridge-journal')),
    'the micro task root turn ended before reading or answering this inquiry; its queued follow-up was stopped': ('inquiry-root-turn-ended', ('bridge', 'bridge-journal')),
    "Host questions reach this root only when the root itself calls hey_my_buddy_checkpoint inside the one admitted native turn (cooperative-checkpoint delivery); they are never injected mid-turn, never start a new turn, and an answer counts only through hey_my_buddy_answer_inquiry verified against this root turn's own tool evidence": ('inquiry-root-turn-ended', ('bridge', 'bridge-journal')),
    "Host questions are queued by the bridge and delivered only at the root's next hey_my_buddy_checkpoint or finish refusal inside the same admitted native turn; they are never injected through session/send, a v4 command, a stop, a restart or a new turn, and an answer counts only through hey_my_buddy_answer_inquiry verified against the root turn's own tool evidence": ('inquiry-root-turn-ended', ('bridge', 'bridge-journal')),
}


def _message_reason(connection: sqlite3.Connection, row: sqlite3.Row) -> dict | None:
    old = row['reason']
    if old is None and row['state'] != 'unavailable':
        return None
    events = connection.execute(
        "SELECT kind,payload_json,actor_role,actor_id FROM events WHERE task_id=? "
        "AND kind IN ('message.posted','message.updated')", (row['task_id'],))
    sources = set()
    posted_unavailable = False
    live_closed_without_reason = False
    for event in events:
        payload = json.loads(event['payload_json'])
        if payload.get('inquiryId') != row['inquiry_id']:
            continue
        if event['kind'] == 'message.updated':
            sources.add((event['actor_role'], event['actor_id']))
            if (event['actor_role'], event['actor_id']) == ('bridge', 'live-bridge') \
                    and payload.get('state') == 'unavailable':
                live_closed_without_reason = True
        elif payload.get('state') == 'unavailable':
            posted_unavailable = True
    if old is None:
        if live_closed_without_reason:
            return make_reason('inquiry-root-turn-ended')
        raise BoardError('UPGRADE_INVALID_BOARD', 'Unavailable message has no recognized writer')
    reason = None
    if posted_unavailable and old == 'no live agent to receive this question':
        reason = make_reason('inquiry-no-live-agent')
    elif old in _INQUIRY_PROGRAM_REASONS:
        code, writer = _INQUIRY_PROGRAM_REASONS[old]
        if writer in sources:
            reason = {'code': code, 'params': {}}
    elif ('bridge', 'live-bridge') in sources:
        match = re.fullmatch(r'the bridge cannot deliver this question \((agent-gone|agent-not-running|journal-unavailable)\)', old)
        if match:
            reason = make_reason('bridge-refused', errorCode=match[1])
    if reason is None:
        raise BoardError('UPGRADE_INVALID_BOARD', 'Message reason has no recognized writer')
    return None if row['state'] == 'answered' else reason


def _upgrade_messages(connection: sqlite3.Connection) -> None:
    ddl = CORE_SCHEMA.split('CREATE TABLE IF NOT EXISTS messages (', 1)[1].split(';', 1)[0]
    columns = ('message_id', 'task_id', 'attempt_id', 'inquiry_id', 'direction', 'author',
               'recipient', 'correlation_id', 'body', 'body_bytes', 'payload_hash', 'state',
               'reason_code', 'reason_params_json', 'delivery_json', 'answer_json',
               'attempts_count', 'created_at', 'updated_at', 'revision')
    converted = []
    for row in connection.execute('SELECT * FROM messages').fetchall():
        reason = _message_reason(connection, row)
        values = {key: row[key] for key in columns if key not in ('reason_code', 'reason_params_json')}
        if row['state'] == 'queued' and row['attempt_id'] is not None:
            attempt = connection.execute('SELECT execution_state FROM attempts WHERE attempt_id=?',
                                         (row['attempt_id'],)).fetchone()
            if attempt['execution_state'] == 'finished':
                values['state'] = 'unavailable'
                reason = make_reason('inquiry-queued-at-attempt-end')
        values.update(reason_code=reason['code'] if reason else None,
                      reason_params_json=canonical_json(reason['params']) if reason else None)
        converted.append(tuple(values[key] for key in columns))
    connection.execute('DROP TABLE messages')
    connection.execute('CREATE TABLE IF NOT EXISTS messages (' + ddl)
    connection.executemany('INSERT INTO messages (' + ','.join(columns) + ') VALUES ('
                           + ','.join('?' for _ in columns) + ')', converted)
    connection.execute('CREATE INDEX messages_task_idx ON messages(task_id, created_at)')
    connection.execute('CREATE INDEX messages_state_idx ON messages(state)')


def _upgrade_inquiry_waiting(connection: sqlite3.Connection) -> None:
    for row in connection.execute("SELECT seq,payload_json FROM events WHERE kind='attempt.waiting'").fetchall():
        payload = json.loads(row['payload_json'])
        old = payload.get('reason')
        if old is None:
            continue
        elif old == 'router-input-unknown':
            payload['reason'] = make_reason('router-input-unknown')
        else:
            raise BoardError('UPGRADE_INVALID_BOARD', 'Unknown stored attempt waiting reason')
        connection.execute('UPDATE events SET payload_json=? WHERE seq=?',
                           (canonical_json(payload), row['seq']))

    def convert(activity: dict) -> bool:
        old = activity.get('waitingReason')
        if old is None:
            return False
        match = (re.fullmatch(r'native (permission\.requested|userInput\.requested) requires Host authority', old)
                 if isinstance(old, str) else None)
        if not match:
            raise BoardError('UPGRADE_INVALID_BOARD', 'Unknown stored native waiting reason')
        activity['waitingReason'] = make_reason('native-authority-required',
                                                kind='permission' if match[1] == 'permission.requested' else 'user-input')
        return True

    for row in connection.execute('SELECT attempt_id,activity_json FROM attempt_activity').fetchall():
        activity = json.loads(row['activity_json'])
        if convert(activity):
            connection.execute('UPDATE attempt_activity SET activity_json=? WHERE attempt_id=?',
                               (canonical_json(activity), row['attempt_id']))
    for row in connection.execute("SELECT seq,payload_json FROM events WHERE kind='attempt.activity'").fetchall():
        payload = json.loads(row['payload_json'])
        if convert(payload['activity']):
            connection.execute('UPDATE events SET payload_json=? WHERE seq=?',
                               (canonical_json(payload), row['seq']))


def _rebuild_workflow_turns(connection: sqlite3.Connection) -> None:
    # The old table already has access_note after _upgrade_notes. Build the new
    # CHECK without renaming the referenced table, so lead_turn_id keeps its FK.
    connection.execute(WORKFLOW_TURNS_DDL.replace('workflow_turns (', 'workflow_turns_upgrade ('))
    connection.execute('INSERT INTO workflow_turns_upgrade SELECT * FROM workflow_turns')
    connection.execute('DROP TABLE workflow_turns')
    connection.execute('ALTER TABLE workflow_turns_upgrade RENAME TO workflow_turns')
    connection.execute('CREATE INDEX workflow_turns_run_idx ON workflow_turns(run_id, turn_index)')
    connection.execute('CREATE INDEX workflow_turns_attempt_idx ON workflow_turns(attempt_id)')


def upgrade_schema_24_to_25(state: Path) -> None:
    """Convert a stopped schema-24 board after its exact rollback backup exists."""
    with closing(sqlite3.connect(Path(state) / 'board.sqlite3')) as connection:
        connection.row_factory = sqlite3.Row
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
            connection.execute("DELETE FROM commands WHERE kind='quota.redetect'")
            connection.execute('DROP TABLE quota_current')
            connection.execute('DROP TABLE harness_quota_observations')
            connection.execute(PROVIDER_COOLING_DDL)
            _upgrade_catalog_and_health(connection)
            _upgrade_decisions(connection)
            connection.execute('DROP INDEX events_router_outcome_idx')
            _upgrade_workflow_requests(connection)
            _upgrade_routing_copies(connection)
            _upgrade_notes(connection)
            _rebuild_workflow_turns(connection)
            _upgrade_host_conclusions(connection)
            _upgrade_messages(connection)
            _upgrade_inquiry_waiting(connection)
            connection.execute("UPDATE meta SET value='25' WHERE key='schema_version'")
            if connection.execute('PRAGMA foreign_key_check').fetchall():
                raise BoardError('UPGRADE_INVALID_BOARD', 'The upgraded board has foreign key violations')
            connection.commit()
            connection.execute('PRAGMA foreign_keys=ON')
        except BaseException:
            connection.rollback()
            raise
