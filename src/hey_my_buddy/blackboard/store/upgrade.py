"""The one pending, offline upgrade from the 0.42.0 board to schema 25."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import json
import sqlite3

from .db import (COMMAND_DELETION_COLUMN, HOST_CONCLUSION_INDEX, HOST_CONCLUSION_TABLE,
                 MACRO_TASK_DELETIONS_DDL, WORKFLOW_REQUESTS_DDL,
                 EVALUATION_TABLES, canonical_json)
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
            if structured is not None:
                # Keep the original lineage facts, replacing only the system body.
                payload = {**source_payload, 'origin': payload['origin'], 'proxy': proxy}
                system = True
        elif payload.get('source') == 'routing':
            pass
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
        if system:
            if structured is None:
                structured = make_reason('history.reason-unavailable', recordId=record_id)
            payload = {key: value for key, value in payload.items() if key not in _SYSTEM_REQUEST_TEXT_KEYS}
        converted[record_id] = structured, payload
        visiting.remove(record_id)
        return converted[record_id]

    connection.execute(WORKFLOW_REQUESTS_DDL.replace('workflow_requests (', 'workflow_requests_upgrade ('))
    new_columns = columns[:columns.index('payload_json')] + ['reason_code', 'reason_params_json'] + columns[columns.index('payload_json'):]
    for record_id, row in rows.items():
        structured, payload = convert(record_id)
        row['reason_code'] = structured['code'] if structured else None
        row['reason_params_json'] = canonical_json(structured['params']) if structured else None
        if structured is not None:
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
        "SELECT continuation_id FROM workflow_continuations WHERE run_id=? AND kind='manual'"
        " AND helper_policy='cancel' AND created_at=?",
        (payload['cancelledByRunId'], row['created_at']),
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
        if value == 'Owned micro task stopped because its Lead is being concluded' and role == 'lead':
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
    for task_id, artifact_id in connection.execute(
        'SELECT t.task_id,r.final_artifact_id FROM tasks t JOIN workflow_runs r ON r.run_id=t.task_id'
        ' WHERE t.review_rejected_at IS NOT NULL AND t.review_rejection_note IS NOT NULL'
    ).fetchall():
        event = connection.execute(
            "SELECT payload_json FROM events WHERE task_id=? AND kind='workflow.review_rejected'"
            ' ORDER BY seq DESC LIMIT 1', (task_id,),
        ).fetchone()
        payload = json.loads(event[0]) if event is not None else None
        if (payload is not None and payload.get('artifactId') == artifact_id
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
            _upgrade_catalog_and_health(connection)
            _upgrade_workflow_requests(connection)
            _upgrade_notes(connection)
            _upgrade_host_conclusions(connection)
            connection.execute("UPDATE meta SET value='25' WHERE key='schema_version'")
            if connection.execute('PRAGMA foreign_key_check').fetchall():
                raise BoardError('UPGRADE_INVALID_BOARD', 'The upgraded board has foreign key violations')
            connection.commit()
            connection.execute('PRAGMA foreign_keys=ON')
        except BaseException:
            connection.rollback()
            raise
