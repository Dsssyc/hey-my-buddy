"""Model identities and the correspondence history, undone by deleting its latest change."""
from __future__ import annotations

import uuid

from ...errors import BoardError
from ...protocol import schemas

SERVICE_FIELDS = ('harness', 'provider', 'model')
CURRENT_CORRESPONDENCES = """SELECT m.rowid AS correspondence_order,m.* FROM model_correspondences m WHERE m.rowid=(
    SELECT MAX(h.rowid) FROM model_correspondences h
    WHERE h.harness=m.harness AND h.provider=m.provider AND h.model=m.model)"""


def current_model(connection, harness, provider, model):
    return connection.execute(
        'SELECT model_id FROM model_correspondences WHERE harness=? AND provider=? AND model=?'
        ' ORDER BY rowid DESC LIMIT 1', (harness, provider, model)).fetchone()[0]


def append_correspondence(connection, service, model_id, now, source, evidence_url=None, evidence_date=None):
    connection.execute(
        'INSERT INTO model_correspondences(correspondence_id,harness,provider,model,model_id,created_at,source,evidence_url,evidence_date)'
        ' VALUES(?,?,?,?,?,?,?,?,?)',
        (str(uuid.uuid4()), *service, model_id, now, source, evidence_url, evidence_date))


def create_model(connection, display_name):
    model_id = str(uuid.uuid4())
    connection.execute('INSERT INTO models(model_id,display_name) VALUES(?,?)', (model_id, display_name))
    return model_id


def ensure_model_service(connection, harness, provider, model, now):
    """Catalog admission unifies only exact names, following the most recently set matching service."""
    service = (harness, provider, model)
    existing = connection.execute(
        'SELECT model_id FROM model_correspondences WHERE harness=? AND provider=? AND model=?'
        ' ORDER BY rowid DESC LIMIT 1', service).fetchone()
    if existing is not None:
        return existing[0]
    same_name = connection.execute(
        'SELECT model_id FROM (' + CURRENT_CORRESPONDENCES + ') WHERE model=? ORDER BY correspondence_order DESC LIMIT 1',
        (model,)).fetchone()
    model_id = same_name[0] if same_name is not None else create_model(connection, model)
    append_correspondence(connection, service, model_id, now, 'same-name')
    return model_id


def model_view(connection, model_id):
    """Names come from live evidence: the services currently corresponding to the
    model and those its recorded executions used, so a change undone after the
    fact leaves the names its samples still rest on."""
    row = connection.execute('SELECT display_name FROM models WHERE model_id=?', (model_id,)).fetchone()
    names = [{field: value[field] for field in SERVICE_FIELDS} for value in connection.execute(
        'SELECT harness,provider,model FROM (' + CURRENT_CORRESPONDENCES + ') WHERE model_id=?'
        ' UNION SELECT model_harness,model_provider,model_model FROM attempts WHERE model_id=?'
        ' ORDER BY harness,provider,model', (model_id, model_id))]
    return {'modelId': model_id, 'displayName': row[0], 'names': names}


def catalog_view(connection, buddy_ids):
    marks = ','.join('?' for _ in buddy_ids) or 'NULL'
    model_ids = [row[0] for row in connection.execute(
        'SELECT DISTINCT m.model_id FROM (' + CURRENT_CORRESPONDENCES + ') m JOIN buddies b'
        ' ON b.harness=m.harness AND b.provider=m.provider AND b.model=m.model'
        f' WHERE b.buddy_id IN ({marks}) ORDER BY m.model_id', buddy_ids)]
    return [model_view(connection, model_id) for model_id in model_ids]


def correspondence_view(connection, buddy_ids):
    marks = ','.join('?' for _ in buddy_ids) or 'NULL'
    return [{**{field: row[field] for field in SERVICE_FIELDS},
             'correspondenceId': row['correspondence_id'], 'modelId': row['model_id'],
             'createdAt': row['created_at'], 'source': row['source'],
             'evidenceUrl': row['evidence_url'], 'evidenceDate': row['evidence_date'],
             'canUndo': bool(row['can_undo'])}
            for row in connection.execute(
                'SELECT m.*,EXISTS(SELECT 1 FROM model_correspondences h WHERE h.harness=m.harness'
                ' AND h.provider=m.provider AND h.model=m.model AND h.rowid<m.rowid) AS can_undo'
                ' FROM model_correspondences m WHERE m.rowid=(SELECT MAX(h.rowid) FROM model_correspondences h'
                ' WHERE h.harness=m.harness AND h.provider=m.provider AND h.model=m.model)'
                ' AND EXISTS(SELECT 1 FROM buddies b WHERE b.harness=m.harness AND b.provider=m.provider'
                f' AND b.model=m.model AND b.buddy_id IN ({marks})) ORDER BY m.harness,m.provider,m.model', buddy_ids)]


def _referencing_columns(connection):
    """Every column the schema itself binds to models(model_id)."""
    tables = [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    return [(table, row[3]) for table in tables
            for row in connection.execute(f'PRAGMA foreign_key_list("{table}")') if row[2] == 'models']


def delete_unreferenced(connection, model_ids):
    """Remove models whose last reference this save removed.

    Foreign keys on models(model_id) are all ON DELETE RESTRICT, so a missed
    reference aborts the save instead of letting a used model disappear.
    """
    references = _referencing_columns(connection)
    for model_id in model_ids:
        if all(connection.execute(f'SELECT 1 FROM "{table}" WHERE {column}=? LIMIT 1',
                                  (model_id,)).fetchone() is None
               for table, column in references):
            connection.execute('DELETE FROM models WHERE model_id=?', (model_id,))


def apply_user_changes(connection, values, now):
    """Apply one save's correspondence entries; report the count and the models
    that lost a reference, for the caller's end-of-save deletion check."""
    if not isinstance(values, list):
        raise BoardError('INVALID_ARGUMENT', 'modelCorrespondenceChanges must be a list')
    lost_references = set()
    seen = set()
    for value in values:
        if not isinstance(value, dict):
            raise BoardError('INVALID_ARGUMENT', 'modelCorrespondenceChanges entries must be objects')
        action = value.get('action')
        fields = {'map': {'modelId'}, 'undo': {'correspondenceId'}, 'generation': {'displayName'}}
        if not isinstance(action, str) or action not in fields:
            raise BoardError('INVALID_ARGUMENT', 'action must be map, undo or generation')
        schemas.reject_unknown(value, {*SERVICE_FIELDS, 'action', *fields[action]}, 'modelCorrespondenceChanges')
        service = tuple(schemas.required_string(value, field, max_length=256) for field in SERVICE_FIELDS)
        if service in seen:
            raise BoardError('INVALID_ARGUMENT', 'modelCorrespondenceChanges repeats a model service')
        seen.add(service)
        if connection.execute('SELECT 1 FROM buddies WHERE harness=? AND provider=? AND model=? LIMIT 1', service).fetchone() is None:
            raise BoardError('NOT_FOUND', 'Unknown model service')
        if action == 'map':
            model_id = schemas.required_string(value, 'modelId', max_length=128, pattern=schemas.IDENTIFIER_PATTERN)
            if connection.execute('SELECT 1 FROM models WHERE model_id=?', (model_id,)).fetchone() is None:
                raise BoardError('NOT_FOUND', 'Unknown modelId')
            append_correspondence(connection, service, model_id, now, 'user')
        elif action == 'undo':
            correspondence_id = schemas.required_string(value, 'correspondenceId', max_length=128,
                                                        pattern=schemas.IDENTIFIER_PATTERN)
            latest = connection.execute(
                'SELECT rowid,correspondence_id,model_id FROM model_correspondences WHERE harness=? AND provider=? AND model=?'
                ' ORDER BY rowid DESC LIMIT 1', service).fetchone()
            if latest is None or latest['correspondence_id'] != correspondence_id:
                raise BoardError('CONFLICT', 'This correspondence is not the model service\'s latest change')
            if connection.execute(
                    'SELECT 1 FROM model_correspondences WHERE harness=? AND provider=? AND model=?'
                    ' AND rowid<? LIMIT 1', (*service, latest['rowid'])).fetchone() is None:
                raise BoardError('CONFLICT', 'This model service has no previous correspondence')
            connection.execute('DELETE FROM model_correspondences WHERE rowid=?', (latest['rowid'],))
            lost_references.add(latest['model_id'])
        else:
            display_name = schemas.required_string(value, 'displayName', max_length=256)
            model_id = create_model(connection, display_name)
            append_correspondence(connection, service, model_id, now, 'user')
    return len(values), lost_references
