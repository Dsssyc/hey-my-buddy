"""The single pending board-schema upgrade, invoked by installation after backup."""
from __future__ import annotations

from contextlib import closing, contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3

from .db import (ATTEMPT_PROCESS_IDENTITIES_DDL, ATTEMPT_RECEIVED_RECEIPTS_DDL, DB_FILE, DECISION_REQUESTS_DDL,
                 EVALUATION_DECISIONS_DDL, EVENTS_ROUTER_CLAIMED_INDEX_DDL,
                 MODEL_SERVICE_CONCURRENCY_DDL, ROUTER_DISPATCHES_DDL, ROUTER_OUTCOMES_DDL,
                 ROUTER_REQUESTS_DDL, SCHEMA_VERSION, UPGRADE_BASE_SCHEMA, WORKFLOW_CHILDREN_DDL,
                 WORKFLOW_RUNS_DDL, WORKFLOW_TURNS_DDL, canonical_json, sha256_text)
from ... import private_dirs
from ...errors import BoardError

# Task and attempt identity already live in the event columns.
ACTIVITY_EVENT_PAYLOAD = '{}'
EVENT_COLUMNS = ('seq', 'task_id', 'attempt_id', 'revision', 'kind', 'payload_json',
                 'actor_role', 'actor_id', 'created_at')

# Only the common columns cross the pending upgrade's retention comparison.
RETAINED_COLUMN_PROJECTIONS = {
    # The schema-29 identity CHECK adds the boot/Job platform exclusion; every
    # stored POSIX value crosses the rebuild unchanged.
    'attempt_process_identities': ('attempt_id', 'boot_id', 'root_pid', 'root_started_at',
                                   'process_group_id', 'job_name'),
    'evaluation_decisions': ('decision_id', 'status', 'buddy_id', 'reason_code',
                             'reason_params_json', 'note', 'created_at'),
    'router_outcomes': ('receipt_id', 'request_id', 'router_index', 'buddy_id', 'phase',
                        'task_id', 'attempt_id', 'outcome', 'code', 'event_seq'),
    'workflow_runs': (
        'run_id', 'macro_task_id', 'title', 'activity_seq', 'activity_at', 'host_id',
        'owner_generation', 'control_verifier', 'request_fingerprint', 'buddy_json',
        'buddy_revision', 'validated_buddy_revision', 'current_routing_id', 'submission_verifier',
        'execution_workspace_json', 'workspace_manifest_json', 'state', 'active_request_id',
        'current_turn_id', 'current_attempt_id', 'continuation_count', 'final_artifact_id',
        'final_attempt_id', 'revision', 'created_at', 'updated_at', 'buddy_locked', 'delegation_authority',
    ),
    'workflow_children': (
        'child_task_id', 'parent_run_id', 'request_id', 'decision_command_id', 'role', 'state',
        'workspace_manifest_json', 'created_at', 'updated_at',
    ),
    'workflow_turns': (
        'turn_id', 'run_id', 'attempt_id', 'generation', 'turn_index', 'resume_mode',
        'previous_session_id', 'input_json', 'access', 'access_note', 'expected_seconds',
        'state', 'created_at', 'updated_at',
    ),
}

_ROUTER_FACT_FIELDS = ('routerBuddyIds', 'routerIdentities', 'routingMode', 'routingBudget',
                      'routerRetryIntervalSeconds', 'budget')
_ROUTER_IDENTITY_FIELDS = ('harness', 'provider', 'model', 'effort')
_ROUTER_COLUMNS = {
    'decision_requests': ('decision_id', 'request_id', 'input_fingerprint', 'task_id',
                          'attempt_id', 'generation', 'selected_json', 'output_json',
                          'requested_json', 'updated_at'),
    'router_requests': ('decision_id', 'round', 'snapshot_json', 'frozen_at'),
    'router_dispatches': ('task_id', 'decision_id', 'round', 'router_index', 'buddy_id',
                          *_ROUTER_IDENTITY_FIELDS, 'created_at'),
}

# These values were emitted by the shared schema-28 InquiryBridge writer for
# every harness and delivery mode, never observed from a native process.
_DELIVERY_CONSTANTS = {'extendsDeadline': False, 'requestedDelivery': None,
                       'startsNewTurn': False, 'supported': True}


def _validate_delivery_constants(encoded: str, *, message_id: str) -> None:
    delivery = json.loads(encoded)
    for key in delivery.keys() & _DELIVERY_CONSTANTS.keys():
        expected = _DELIVERY_CONSTANTS[key]
        if type(delivery[key]) is not type(expected) or delivery[key] != expected:
            raise BoardError('UPGRADE_INVALID_BOARD', 'Inquiry delivery differs from its constructed constant',
                             messageId=message_id, field=key)


def _validate_scope_paths(connection, encoded: str, *, record_id: str | int) -> None:
    facts = json.loads(encoded)
    paths = set()
    for conflict_id in facts['conflictIds']:
        conflict = connection.execute('SELECT evidence_json FROM workflow_workspace_conflicts WHERE conflict_id=?',
                                      (conflict_id,)).fetchone()
        if conflict is None:
            raise BoardError('UPGRADE_INVALID_BOARD', 'Scope reason names missing conflict evidence',
                             recordId=record_id, field='conflictIds', conflictId=conflict_id)
        paths.update(json.loads(conflict[0])['blockingPaths'])
    # The old writer sorted and truncated the union once, including when its
    # reason was copied to an owning ancestor's request.
    if facts.get('paths') != sorted(paths)[:32]:
        raise BoardError('UPGRADE_INVALID_BOARD', 'Scope paths differ from their conflict evidence',
                         recordId=record_id, field='paths')


def validate_inquiry_and_scope_stock(connection) -> None:
    """Reject inconsistent old copies in Python, before SQLite hashes any rows."""
    for message_id, encoded in connection.execute('SELECT message_id,delivery_json FROM messages'
                                                   ' WHERE delivery_json IS NOT NULL'):
        _validate_delivery_constants(encoded, message_id=message_id)
    for request_id, encoded in connection.execute("SELECT request_id,reason_params_json FROM workflow_requests"
                                                  " WHERE reason_code='workspace-scope-violation'"):
        _validate_scope_paths(connection, encoded, record_id=request_id)
    for seq, encoded in connection.execute("SELECT seq,payload_json FROM events WHERE kind='workflow.scope_conflict'"):
        _validate_scope_paths(connection, encoded, record_id=seq)


def _delivery_without_constants(encoded: str | None) -> str | None:
    if encoded is None:
        return None
    delivery = json.loads(encoded)
    retired = delivery.keys() & _DELIVERY_CONSTANTS.keys()
    return canonical_json({key: value for key, value in delivery.items() if key not in retired}) if retired else encoded


def _scope_without_paths(encoded: str) -> str:
    facts = json.loads(encoded)
    del facts['paths']
    return canonical_json(facts)


def _convert_inquiry_and_scope_stock(connection) -> None:
    for message_id, encoded in connection.execute('SELECT message_id,delivery_json FROM messages'
                                                   ' WHERE delivery_json IS NOT NULL'):
        retained = _delivery_without_constants(encoded)
        if retained != encoded:
            connection.execute('UPDATE messages SET delivery_json=? WHERE message_id=?', (retained, message_id))
    for request_id, encoded in connection.execute("SELECT request_id,reason_params_json FROM workflow_requests"
                                                  " WHERE reason_code='workspace-scope-violation'"):
        retained = _scope_without_paths(encoded)
        connection.execute('UPDATE workflow_requests SET reason_params_json=? WHERE request_id=?', (retained, request_id))
    for seq, encoded in connection.execute("SELECT seq,payload_json FROM events WHERE kind='workflow.scope_conflict'"):
        retained = _scope_without_paths(encoded)
        connection.execute('UPDATE events SET payload_json=? WHERE seq=?', (retained, seq))


def expected_inquiry_and_scope_fingerprints(connection) -> dict[str, str]:
    """Hash retained values after Python has validated the stock's copies."""
    fingerprints = {}
    for table in ('messages', 'workflow_requests'):
        columns = [item[0] for item in connection.execute(f'SELECT * FROM {table} LIMIT 0').description]
        positions = {column: index for index, column in enumerate(columns)}

        def encode(*values):
            row = list(values)
            if table == 'messages':
                index = positions['delivery_json']
                row[index] = _delivery_without_constants(row[index])
            elif row[positions['reason_code']] == 'workspace-scope-violation':
                index = positions['reason_params_json']
                row[index] = _scope_without_paths(row[index])
            return json.dumps(row, ensure_ascii=False, separators=(',', ':'))

        connection.create_function('hmb_upgrade_fact_row', len(columns), encode)
        try:
            cursor = connection.execute('SELECT hmb_upgrade_fact_row(' + ','.join(columns)
                                        + f') AS encoded FROM {table} ORDER BY encoded COLLATE BINARY')
            digest, separator = hashlib.sha256(), b''
            for row in cursor:
                digest.update(separator)
                digest.update(row[0].encode())
                separator = b'\n'
            fingerprints[table] = digest.hexdigest()
        finally:
            connection.create_function('hmb_upgrade_fact_row', len(columns), None)
    return fingerprints


def _request_without_router_facts(request: dict, snapshots: list[dict], *, decision_id: str) -> dict:
    """Check the old writer's duplicated facts before retiring their request copy."""
    current = snapshots[-1]
    facts = current['facts']
    if request.get('selectionRetry') and request['routingBasis']['candidateCount'] <= 1:
        # This old branch ended before resolving another Router, so its six
        # request facts still name round one; routerCalled describes the new round.
        facts = {**facts, **snapshots[0]['facts']}
    copied = request.keys() & facts.keys()
    for key in copied:
        if request[key] != facts[key]:
            raise BoardError('UPGRADE_INVALID_BOARD', 'Request Router fact differs from its round snapshot',
                             decisionId=decision_id, field=key)
    # The old writer also stored this now-unread timeout alias beside budget.
    if 'routerBudgetSeconds' in request:
        if request['routerBudgetSeconds'] != facts['budget']['timeoutSeconds']:
            raise BoardError('UPGRADE_INVALID_BOARD', 'Request Router timeout differs from its round snapshot',
                             decisionId=decision_id, field='routerBudgetSeconds')
        copied.add('routerBudgetSeconds')
    if 'selectionRetryInspections' in request and request['selectionRetryInspections'] != current['inspections']:
        raise BoardError('UPGRADE_INVALID_BOARD', 'Request Router inspections differ from its round snapshot',
                         decisionId=decision_id, field='selectionRetryInspections')
    return {key: value for key, value in request.items()
            if key not in copied and key != 'selectionRetryInspections'}


def _historical_router_document(encoded: str) -> dict:
    """Apply the pre-0.46 excluded-buddy conversion already applied to snapshots."""
    document = json.loads(encoded)
    for entry in document['routingBasis']['excludedBuddies']:
        if 'reason' in entry:
            entry['note'] = entry.pop('reason') or None
    return document


@contextmanager
def _router_upgrade_stock(connection):
    """Project the old writer's initial and single-retry packets into round rows.

    Original snapshots keep their rowids. The old writer omitted snapshots when
    zero or one candidates needed no Router; their task-only snapshots are made
    from the retained request text, checked against the task column being retired.
    The old task request key distinguishes
    retry dispatches; their actor-free document and retry inspections recover the
    second snapshot. Temporary tables let SQLite sort fingerprints without
    retaining all Router inputs in Python.
    """
    connection.execute('CREATE TEMP TABLE hmb_upgrade_router_requests AS'
                       ' SELECT rowid AS original_rowid,decision_id,1 AS round,snapshot_json,frozen_at'
                       ' FROM router_requests')
    connection.execute("CREATE TEMP TABLE hmb_upgrade_router_dispatches AS"
                       " SELECT d.rowid AS original_rowid,d.task_id,d.decision_id,"
                       " CASE WHEN t.request_id='decision:'||r.request_id||':1:'||d.router_index"
                       " THEN 2 ELSE 1 END AS round,d.router_index,d.buddy_id,"
                       " d.harness,d.provider,d.model,d.effort,d.created_at"
                       " FROM router_dispatches d JOIN tasks t ON t.task_id=d.task_id"
                       " JOIN decision_requests r ON r.decision_id=d.decision_id")
    try:
        next_rowid = connection.execute('SELECT COALESCE(MAX(rowid),0)+1 FROM router_requests').fetchone()[0]
        for decision_id, encoded, created_at in connection.execute(
            'SELECT r.decision_id,r.requested_json,d.created_at FROM decision_requests r'
            ' JOIN evaluation_decisions d ON d.decision_id=r.decision_id'
            ' LEFT JOIN router_requests s ON s.decision_id=r.decision_id'
            ' WHERE s.decision_id IS NULL ORDER BY r.rowid'):
            request = json.loads(encoded)
            count = request['routingBasis']['candidateCount']
            if request.get('selectionRetry') or count > 1 or connection.execute(
                    'SELECT 1 FROM router_dispatches WHERE decision_id=? LIMIT 1', (decision_id,)).fetchone():
                raise BoardError('UPGRADE_INVALID_BOARD', 'Decision has no Router snapshot', decisionId=decision_id)
            facts = {'routerBuddyId': None, 'routingMode': None, 'budget': None, 'routerCalled': False}
            snapshot = {'baseInput': {'task': request['task']}, 'facts': facts, 'inspections': []}
            connection.execute('INSERT INTO hmb_upgrade_router_requests VALUES(?,?,?,?,?)',
                               (next_rowid, decision_id, 1, canonical_json(snapshot), created_at))
            next_rowid += 1
        for row in connection.execute('SELECT decision_id,requested_json,input_json'
                                      ' FROM decision_requests ORDER BY rowid'):
            decision_id, encoded_request, encoded_input = row
            request = json.loads(encoded_request)
            if not request.get('selectionRetry'):
                continue
            if request['routingBasis']['candidateCount'] <= 1:
                snapshot = {'baseInput': {'task': request['task']},
                            'facts': {'routerBuddyId': None, 'routingMode': None, 'budget': None, 'routerCalled': False},
                            'inspections': []}
            else:
                dispatch = connection.execute(
                    'SELECT d.document_json FROM router_dispatches d JOIN hmb_upgrade_router_dispatches s'
                    ' ON s.task_id=d.task_id WHERE s.decision_id=? AND s.round=2 ORDER BY d.rowid LIMIT 1',
                    (decision_id,)).fetchone()
                if dispatch is None and encoded_input is None:
                    raise BoardError('UPGRADE_INVALID_BOARD', 'Selection retry has no retained Router packet',
                                     decisionId=decision_id)
                base_input = _historical_router_document(dispatch[0] if dispatch is not None else encoded_input)
                for field in ('buddy', 'routerBuddyId', 'routerBuddy', 'routerIndex'):
                    base_input.pop(field, None)
                snapshot = {'baseInput': base_input,
                            'facts': {field: base_input[field] for field in _ROUTER_FACT_FIELDS},
                            'inspections': request['selectionRetryInspections']}
            frozen_at = connection.execute(
                "SELECT created_at FROM events WHERE kind='decision.selection_rerouted'"
                " AND json_extract(payload_json,'$.decisionId')=?", (decision_id,)).fetchone()[0]
            connection.execute('INSERT INTO hmb_upgrade_router_requests VALUES(?,?,?,?,?)',
                               (next_rowid, decision_id, 2, canonical_json(snapshot), frozen_at))
            next_rowid += 1
        columns = ','.join(_ROUTER_COLUMNS['decision_requests'])
        connection.execute('CREATE TEMP TABLE hmb_upgrade_decision_requests AS'
                           f' SELECT rowid AS original_rowid,{columns} FROM decision_requests')
        try:
            for decision_id, encoded in connection.execute('SELECT decision_id,requested_json FROM decision_requests'):
                snapshots = [json.loads(row[0]) for row in connection.execute(
                    'SELECT snapshot_json FROM hmb_upgrade_router_requests WHERE decision_id=? ORDER BY round',
                    (decision_id,))]
                request = _request_without_router_facts(json.loads(encoded), snapshots, decision_id=decision_id)
                connection.execute('UPDATE hmb_upgrade_decision_requests SET requested_json=? WHERE decision_id=?',
                                   (canonical_json(request), decision_id))
            yield
        finally:
            connection.execute('DROP TABLE hmb_upgrade_decision_requests')
    finally:
        connection.execute('DROP TABLE hmb_upgrade_router_dispatches')
        connection.execute('DROP TABLE hmb_upgrade_router_requests')


def _validate_router_stock(connection) -> None:
    missing = connection.execute(
        'SELECT d.decision_id FROM evaluation_decisions d LEFT JOIN hmb_upgrade_router_requests r'
        ' ON r.decision_id=d.decision_id WHERE r.decision_id IS NULL LIMIT 1').fetchone()
    if missing is not None:
        raise BoardError('UPGRADE_INVALID_BOARD', 'Decision has no Router snapshot', decisionId=missing[0])
    # The only redundant task copy is checked before evaluation_decisions is rebuilt.
    for decision_id, task, snapshot in connection.execute(
        'SELECT d.decision_id,d.task,s.snapshot_json FROM evaluation_decisions d'
        ' JOIN hmb_upgrade_router_requests s ON s.decision_id=d.decision_id'
        ' WHERE s.round=(SELECT MAX(current.round) FROM hmb_upgrade_router_requests current'
        ' WHERE current.decision_id=d.decision_id)'):
        if task != json.loads(snapshot)['baseInput']['task']:
            raise BoardError('UPGRADE_INVALID_BOARD', 'Decision task differs from its Router snapshot',
                             decisionId=decision_id)
    for row in connection.execute(
        'SELECT d.task_id,d.document_json,s.snapshot_json,d.buddy_id,d.harness,d.provider,d.model,d.effort,'
        ' d.router_index FROM router_dispatches d JOIN hmb_upgrade_router_dispatches projected'
        ' ON projected.task_id=d.task_id JOIN hmb_upgrade_router_requests s'
        ' ON s.decision_id=projected.decision_id AND s.round=projected.round'):
        task_id, encoded, snapshot, buddy_id, *identity, router_index = row
        buddy = dict(zip(_ROUTER_IDENTITY_FIELDS, identity))
        expected = {**json.loads(snapshot)['baseInput'], 'buddy': buddy,
                    'routerBuddyId': buddy_id, 'routerBuddy': buddy, 'routerIndex': router_index}
        if _historical_router_document(encoded) != expected:
            raise BoardError('UPGRADE_INVALID_BOARD', 'Router dispatch differs from its round snapshot',
                             taskId=task_id)
    # input_json held either a retry's actor-free packet or its current dispatch.
    for decision_id, encoded, snapshot, document in connection.execute(
        'SELECT r.decision_id,r.input_json,s.snapshot_json,d.document_json FROM decision_requests r'
        ' JOIN hmb_upgrade_router_requests s ON s.decision_id=r.decision_id'
        ' LEFT JOIN router_dispatches d ON d.task_id=r.task_id'
        ' WHERE r.input_json IS NOT NULL AND s.round=(SELECT MAX(current.round)'
        ' FROM hmb_upgrade_router_requests current WHERE current.decision_id=r.decision_id)'):
        actual = _historical_router_document(encoded)
        if actual != json.loads(snapshot)['baseInput'] and (
                document is None or actual != _historical_router_document(document)):
            raise BoardError('UPGRADE_INVALID_BOARD', 'Decision input differs from its retained Router packet',
                             decisionId=decision_id)
    if connection.execute("SELECT 1 FROM router_outcomes WHERE plane!='routing' LIMIT 1").fetchone():
        raise BoardError('UPGRADE_INVALID_BOARD', 'Router outcome is outside the routing plane')


def _validate_turn_stock(connection) -> None:
    """Check receipt copies and the turn's accepted pins before retiring them."""
    from ..tasks.workflow import WorkflowCoordinator

    cursor = connection.execute(
        'SELECT t.*,a.result_json FROM workflow_turns t'
        ' LEFT JOIN attempts a ON a.attempt_id=t.attempt_id ORDER BY t.rowid')
    columns = [column[0] for column in cursor.description]
    for values in cursor:
        row = dict(zip(columns, values))
        payload = json.loads(row['result_json']) if row['result_json'] is not None else None
        result = WorkflowCoordinator.result_payload(payload)
        turn = result.get('turn')
        if row['state'] == 'returned':
            if not isinstance(turn, dict):
                raise BoardError('UPGRADE_INVALID_BOARD', 'Returned turn has no execution receipt outcome',
                                 turnId=row['turn_id'])
            expected = {
                'disposition': turn['outcome']['disposition'],
                'outcome_json': canonical_json(turn['outcome']),
                'outcome_truncated_json': (canonical_json(turn['outcomeTruncated'])
                                           if 'outcomeTruncated' in turn else None),
                'provenance_json': canonical_json(turn.get('provenance')),
                'session_id': turn.get('sessionId'),
                'prompt_sha256': turn.get('promptSha256'),
                'input_sha256': turn['inputSha256'],
                'turn_result_path': result.get('turnResultPath'),
            }
            if turn['inputSha256'] != sha256_text(row['input_json']):
                raise BoardError('UPGRADE_INVALID_BOARD', 'Turn receipt input digest differs from its frozen input',
                                 turnId=row['turn_id'], field='input_sha256')
        else:
            expected = dict.fromkeys((
                'disposition', 'outcome_json', 'outcome_truncated_json', 'provenance_json',
                'session_id', 'prompt_sha256', 'input_sha256', 'turn_result_path'))
        expected['sealed_artifacts_json'] = canonical_json(WorkflowCoordinator._sealed_artifacts(
            connection, run_id=row['run_id'], turn_id=row['turn_id']))
        for column, value in expected.items():
            if row[column] != value:
                raise BoardError('UPGRADE_INVALID_BOARD', 'Turn copy differs from its authoritative projection',
                                 turnId=row['turn_id'], field=column)


def _rebuild_table(connection, name: str, ddl: str, *, projected: bool = False,
                   expressions: dict[str, str] | None = None) -> None:
    previous = name + '_upgrade'
    indexes = connection.execute("SELECT name,sql FROM sqlite_master WHERE type='index'"
                                 " AND tbl_name=? AND sql IS NOT NULL", (name,)).fetchall()
    connection.execute(f'ALTER TABLE {name} RENAME TO {previous}')
    for index_name, _ in indexes:
        connection.execute(f'DROP INDEX {index_name}')
    connection.execute(ddl)
    names = [row[1] for row in connection.execute(f'PRAGMA table_info({name})')]
    columns = ','.join(names)
    values = ','.join((expressions or {}).get(column, column) for column in names)
    source, rowid = ('hmb_upgrade_' + name, 'original_rowid') if projected else (previous, 'rowid')
    connection.execute(f'INSERT INTO {name}(rowid,{columns}) SELECT {rowid},{values} FROM {source} source')
    connection.execute(f'DROP TABLE {previous}')
    for _, index_sql in indexes:
        connection.execute(index_sql)


def expected_router_fingerprints(connection) -> dict[str, str]:
    """Hash the projected round rows with the backup's sorted JSON row format."""
    fingerprints = {}
    with _router_upgrade_stock(connection):
        connection.create_function('hmb_upgrade_router_row', -1,
                                   lambda *values: json.dumps(values, ensure_ascii=False, separators=(',', ':')))
        try:
            for name, columns in _ROUTER_COLUMNS.items():
                projected = 'hmb_upgrade_' + name
                cursor = connection.execute('SELECT hmb_upgrade_router_row(' + ','.join(columns)
                                            + f') AS encoded FROM {projected} ORDER BY encoded COLLATE BINARY')
                digest, separator = hashlib.sha256(), b''
                for row in cursor:
                    digest.update(separator)
                    digest.update(row[0].encode())
                    separator = b'\n'
                fingerprints[name] = digest.hexdigest()
        finally:
            connection.create_function('hmb_upgrade_router_row', -1, None)
    return fingerprints

# This is the schema-28 reader's inference, consumed once rather than on reads.
SOURCE_HOST_SQL = """
COALESCE(
    (SELECT CASE event.kind
                WHEN 'task.submitted' THEN json_extract(event.payload_json, '$.microTask.sourceHostId')
                ELSE json_extract(event.payload_json, '$.sourceHostId')
            END
       FROM events event
      WHERE event.task_id = source.run_id AND (
          (event.kind = 'task.submitted'
           AND json_type(event.payload_json, '$.microTask.sourceHostId') = 'text'
           AND length(json_extract(event.payload_json, '$.microTask.sourceHostId')) > 0)
          OR (event.kind = 'workflow.helper_admitted'
              AND json_type(event.payload_json, '$.sourceHostId') = 'text'
              AND length(json_extract(event.payload_json, '$.sourceHostId')) > 0)
      ) ORDER BY event.seq LIMIT 1),
    (SELECT json_extract(event.payload_json, '$.previousHostId') FROM events event
      WHERE event.task_id = source.run_id AND event.kind = 'workflow.takeover'
        AND json_type(event.payload_json, '$.previousOwnerGeneration') = 'integer'
        AND json_extract(event.payload_json, '$.previousOwnerGeneration') = 1
        AND json_type(event.payload_json, '$.previousHostId') = 'text'
        AND length(json_extract(event.payload_json, '$.previousHostId')) > 0
      ORDER BY event.seq LIMIT 1),
    CASE WHEN source.owner_generation = 1 THEN source.host_id END
)
"""


# These retired entries belong to the schema-28 stock, not to future installs.
LEGACY_LAYOUT = (
    ('harnesses/codex/goals', True),
    ('harnesses/claude/goals', True),
    ('harnesses/zcode/goals', True),
    ('harnesses/dsh/goals', True),
    ('decisions', True),
    ('legacy-worker-cleanup.lock', False),
    ('worker.log', False),
)


def cleanup_legacy_layout(state: Path) -> dict:
    """Remove retired private entries after the pending conversion is verified."""
    state = private_dirs._absolute(state)
    with closing(sqlite3.connect((state / DB_FILE).as_uri() + '?mode=ro', uri=True)) as connection:
        deliveries = [((private_dirs.attempt_root(state, adapter, task_id, attempt_id)
                        / 'result-delivery.json').relative_to(state).as_posix(), False)
                      for task_id, attempt_id, adapter in connection.execute(
                          "SELECT task_id,attempt_id,CASE WHEN adapter='decision' THEN model_harness ELSE adapter END"
                          ' FROM attempts ORDER BY rowid')]
    removed, failures = [], []
    for relative, directory in (*LEGACY_LAYOUT, *deliveries):
        path = state / relative
        try:
            # Check parents from state outward before the private remover can
            # reach a linked harness directory or the target of any link.
            parts = Path(relative).parts
            for depth in range(len(parts) + 1):
                parent = state.joinpath(*parts[:depth])
                if private_dirs.linked(parent):
                    raise BoardError('PRIVATE_PATH_UNSAFE', 'Legacy cleanup path contains a link or reparse point',
                                     path=str(parent))
            if directory:
                deleted = private_dirs.remove_tree(path)
            else:
                deleted = path.exists()
                path.unlink(missing_ok=True)
            if deleted:
                removed.append(relative)
        except (BoardError, OSError) as error:
            failures.append({'path': relative,
                             'code': error.code if isinstance(error, BoardError) else type(error).__name__,
                             'message': str(error)})
    return {'complete': not failures, 'removed': removed, 'failures': failures}


def expected_events_fingerprint(connection, *, event_head: int) -> str:
    """Hash validated event rows in the same bounded sorted format as the backup."""
    def encode(*values):
        row = list(values)
        if row[4] == 'attempt.activity':
            row[5] = ACTIVITY_EVENT_PAYLOAD
        elif row[4] == 'workflow.scope_conflict':
            row[5] = _scope_without_paths(row[5])
        return json.dumps(row, ensure_ascii=False, separators=(',', ':'))

    connection.create_function('hmb_upgrade_event_row', len(EVENT_COLUMNS), encode)
    try:
        cursor = connection.execute('SELECT hmb_upgrade_event_row('
                                    + ','.join(EVENT_COLUMNS)
                                    + ') AS encoded FROM events WHERE seq<=? ORDER BY encoded COLLATE BINARY',
                                    (event_head,))
        digest = hashlib.sha256()
        separator = b''
        for row in cursor:
            digest.update(separator)
            digest.update(row[0].encode())
            separator = b'\n'
        return digest.hexdigest()
    finally:
        connection.create_function('hmb_upgrade_event_row', len(EVENT_COLUMNS), None)


def migrate_schema(state: Path) -> None:
    """Consume the single pending schema-28 upgrade after a verified backup."""
    with closing(sqlite3.connect(state / DB_FILE)) as connection:
        # SQLite's parent-table rebuild requires foreign keys disabled before
        # BEGIN; the complete foreign_key_check below gates the atomic commit.
        connection.execute('PRAGMA foreign_keys=OFF')
        connection.execute('PRAGMA legacy_alter_table=ON')
        connection.execute('BEGIN IMMEDIATE')
        try:
            version = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if version is None or version[0] not in (str(UPGRADE_BASE_SCHEMA), str(SCHEMA_VERSION)):
                raise BoardError('UNSUPPORTED_SCHEMA', 'Board schema cannot be upgraded')
            if version[0] == str(UPGRADE_BASE_SCHEMA):
                _validate_turn_stock(connection)
                validate_inquiry_and_scope_stock(connection)
                _convert_inquiry_and_scope_stock(connection)
                with _router_upgrade_stock(connection):
                    _validate_router_stock(connection)
                    _rebuild_table(connection, 'router_requests', ROUTER_REQUESTS_DDL, projected=True)
                    _rebuild_table(connection, 'router_dispatches', ROUTER_DISPATCHES_DDL, projected=True)
                    _rebuild_table(connection, 'evaluation_decisions', EVALUATION_DECISIONS_DDL)
                    _rebuild_table(connection, 'decision_requests', DECISION_REQUESTS_DDL, projected=True)
                    _rebuild_table(connection, 'router_outcomes', ROUTER_OUTCOMES_DDL)
                    _rebuild_table(connection, 'model_service_concurrency', MODEL_SERVICE_CONCURRENCY_DDL)
                _rebuild_table(connection, 'workflow_runs', WORKFLOW_RUNS_DDL,
                               expressions={'source_host_id': SOURCE_HOST_SQL})
                _rebuild_table(connection, 'workflow_children', WORKFLOW_CHILDREN_DDL)
                _rebuild_table(connection, 'workflow_turns', WORKFLOW_TURNS_DDL)
                _rebuild_table(connection, 'attempt_process_identities', ATTEMPT_PROCESS_IDENTITIES_DDL)
                connection.execute(ATTEMPT_RECEIVED_RECEIPTS_DDL)
                connection.execute("UPDATE events SET payload_json=? WHERE kind='attempt.activity'",
                                   (ACTIVITY_EVENT_PAYLOAD,))
                connection.execute('DROP INDEX events_router_claimed_idx')
                connection.execute(EVENTS_ROUTER_CLAIMED_INDEX_DDL)
                connection.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
            if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or connection.execute('PRAGMA foreign_key_check').fetchall():
                raise BoardError('UPGRADE_INVALID_BOARD', 'Migrated board integrity or foreign key checks failed')
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
